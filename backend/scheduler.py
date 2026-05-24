"""Burst Download Scheduler — APScheduler-backed schedule manager."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

SCHEDULES_FILE = Path("schedules.json")

# ---------------------------------------------------------------------------
# In-memory store + persistence
# ---------------------------------------------------------------------------

_schedules: Dict[str, Dict[str, Any]] = {}  # schedule_id -> schedule dict
_missed: List[Dict[str, Any]] = []          # schedules that were missed on startup
_scheduler: Optional[AsyncIOScheduler] = None

# These are set via init_scheduler so fire callbacks can reach them
_broadcast_fn: Optional[Callable] = None
_manager = None
_active_torrents: Optional[Dict] = None
_interfaces_fn: Optional[Callable] = None


def load_schedules() -> List[Dict[str, Any]]:
    try:
        if SCHEDULES_FILE.exists():
            with open(SCHEDULES_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"[SCHEDULER] Load error: {e}")
    return []


def _save_schedules():
    try:
        with open(SCHEDULES_FILE, "w") as f:
            json.dump(list(_schedules.values()), f, indent=2)
    except Exception as e:
        print(f"[SCHEDULER] Save error: {e}")


def get_all_schedules() -> List[Dict[str, Any]]:
    """Return all pending schedules sorted by scheduled_time ascending."""
    items = list(_schedules.values())
    items.sort(key=lambda x: x.get("scheduled_time", ""))
    return items


def get_missed_schedules() -> List[Dict[str, Any]]:
    return list(_missed)


def dismiss_missed():
    """Clear missed schedules list."""
    global _missed
    _missed = []


# ---------------------------------------------------------------------------
# Job firing
# ---------------------------------------------------------------------------

async def _fire_schedule(schedule_id: str):
    """Called by APScheduler when a scheduled job fires."""
    global _schedules

    entry = _schedules.get(schedule_id)
    if not entry:
        return

    url = entry["url"]
    output_path = entry["output_path"]
    repeat = entry.get("repeat", "once")
    filename = url.split("/")[-1].split("?")[0] or "download"

    print(f"[SCHEDULER] Firing schedule {schedule_id}: {url}")

    try:
        all_ifaces = _interfaces_fn()
        ips = list(all_ifaces.keys())
        selected = [all_ifaces[ip] for ip in ips if ip in all_ifaces]

        is_torrent = url.startswith("magnet:") or url.endswith(".torrent")

        if is_torrent:
            from torrent import start_torrent_download
            job = await start_torrent_download(url, output_path, ips)
            job_id = job.job_id
        else:
            if not selected:
                print(f"[SCHEDULER] No interfaces available for {schedule_id}")
                return
            job = await _manager.create_job(url, output_path, selected, None)
            job_id = job.job_id

        # Broadcast to frontend
        if _broadcast_fn:
            await _broadcast_fn("scheduled_start", {
                "job_id": job_id,
                "schedule_id": schedule_id,
                "filename": filename,
                "url": url,
            })

        print(f"[SCHEDULER] Started job {job_id} for schedule {schedule_id}")

    except Exception as e:
        print(f"[SCHEDULER] Fire error for {schedule_id}: {e}")

    # Remove 'once' schedules; APScheduler handles daily/weekly automatically
    if repeat == "once":
        _schedules.pop(schedule_id, None)
        _save_schedules()
        print(f"[SCHEDULER] Removed once-schedule {schedule_id}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_schedule(
    url: str,
    output_path: str,
    scheduled_time: str,      # ISO 8601, e.g. "2026-05-25T14:00:00"
    repeat: str = "once",     # "once" | "daily" | "weekly"
) -> Dict[str, Any]:
    """Create a new schedule entry and register it with APScheduler."""
    sid = str(uuid.uuid4())
    entry = {
        "schedule_id": sid,
        "url": url,
        "output_path": output_path,
        "scheduled_time": scheduled_time,
        "repeat": repeat,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _schedules[sid] = entry
    _save_schedules()
    _register_job(entry)
    return entry


def remove_schedule(schedule_id: str) -> bool:
    """Cancel and remove a scheduled job."""
    if schedule_id not in _schedules:
        return False
    try:
        _scheduler.remove_job(schedule_id)
    except Exception:
        pass
    _schedules.pop(schedule_id, None)
    _save_schedules()
    return True


def _register_job(entry: Dict[str, Any]):
    """Register a schedule entry with APScheduler."""
    if _scheduler is None:
        return

    sid = entry["schedule_id"]
    repeat = entry.get("repeat", "once")
    # Parse the ISO datetime (treat as local time)
    fire_dt = datetime.fromisoformat(entry["scheduled_time"])
    # Make it timezone-aware if it isn't
    if fire_dt.tzinfo is None:
        fire_dt = fire_dt.astimezone()

    try:
        if repeat == "once":
            _scheduler.add_job(
                _fire_schedule,
                trigger=DateTrigger(run_date=fire_dt),
                id=sid,
                args=[sid],
                replace_existing=True,
                misfire_grace_time=None,
            )
        elif repeat == "daily":
            _scheduler.add_job(
                _fire_schedule,
                trigger=IntervalTrigger(days=1, start_date=fire_dt),
                id=sid,
                args=[sid],
                replace_existing=True,
            )
        elif repeat == "weekly":
            _scheduler.add_job(
                _fire_schedule,
                trigger=IntervalTrigger(weeks=1, start_date=fire_dt),
                id=sid,
                args=[sid],
                replace_existing=True,
            )
    except Exception as e:
        print(f"[SCHEDULER] Failed to register job {sid}: {e}")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def init_scheduler(broadcast_fn, manager, active_torrents_dict, interfaces_fn):
    """Call once from lifespan startup. Loads persisted schedules, starts scheduler."""
    global _scheduler, _broadcast_fn, _manager, _active_torrents, _interfaces_fn

    _broadcast_fn = broadcast_fn
    _manager = manager
    _active_torrents = active_torrents_dict
    _interfaces_fn = interfaces_fn

    _scheduler = AsyncIOScheduler(timezone="local")
    _scheduler.start()
    print("[SCHEDULER] APScheduler started.")

    now = datetime.now(timezone.utc).astimezone()

    for entry in load_schedules():
        sid = entry["schedule_id"]
        _schedules[sid] = entry

        fire_dt = datetime.fromisoformat(entry["scheduled_time"])
        if fire_dt.tzinfo is None:
            fire_dt = fire_dt.astimezone()

        repeat = entry.get("repeat", "once")

        # For once-schedules that are in the past → missed
        if repeat == "once" and fire_dt < now:
            print(f"[SCHEDULER] Missed schedule: {sid} was {entry['scheduled_time']}")
            _missed.append(entry)
            _schedules.pop(sid, None)
        else:
            _register_job(entry)
            print(f"[SCHEDULER] Restored schedule {sid} ({repeat}) @ {entry['scheduled_time']}")

    # Re-save after removing missed once-jobs
    _save_schedules()


def shutdown_scheduler():
    """Gracefully shutdown APScheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[SCHEDULER] APScheduler shut down.")
