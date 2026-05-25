"""Burst — FastAPI application with settings and download management."""
from __future__ import annotations

import asyncio
import time
import sys
import os
import ctypes

# ---------------------------------------------------------------------------
# Console Management for Windowed Mode
# ---------------------------------------------------------------------------
def hide_console():
    """Hides the console window if running as a frozen executable."""
    if os.name == "nt" and getattr(sys, "frozen", False):
        # SW_HIDE = 0
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

# Redirect stdout/stderr to devnull for --noconsole mode
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import os

from downloader import DownloadManager, analyze_url
from interfaces import get_active_interfaces_dict
from torrent import start_torrent_download, active_torrents
from speedtest import benchmark_interfaces
import config
from history_manager import load_history, save_to_history
from setup_utils import apply_firewall_rules
from contextlib import asynccontextmanager


class DownloadRequest(BaseModel):
    url: str = Field(min_length=5)
    output_path: str = Field(min_length=1)
    interface_ips: List[str]
    bandwidth_limits: Optional[Dict[str, int]] = None


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=5)
    interface_ip: str | None = None


class AddInterfacesRequest(BaseModel):
    interface_ips: List[str]

class AddInterfaceRequest(BaseModel):
    interface_ip: str

class RemoveInterfaceRequest(BaseModel):
    interface_ip: str

class TorrentStartRequest(BaseModel):
    magnet_uri: str
    output_path: str
    interface_ips: List[str]
    bandwidth_limits: Optional[Dict[str, int]] = None


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]


class BatchScanRequest(BaseModel):
    url: str = Field(min_length=5)
    output_path: str = Field(min_length=1)


class BatchDownloadRequest(BaseModel):
    urls: List[str]
    output_path: str


class ChecksumRequest(BaseModel):
    file_path: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    algorithm: Optional[str] = None


class FileDeleteRequest(BaseModel):
    file_path: str = Field(min_length=1)


class ScheduleRequest(BaseModel):
    url: str = Field(min_length=5)
    output_path: str = Field(min_length=1)
    scheduled_time: str = Field(min_length=1)  # ISO 8601 local datetime string
    repeat: str = "once"  # "once" | "daily" | "weekly"


async def history_saver_loop():
    saved_ids = set()
    try:
        saved_ids = {item.get("job_id") for item in load_history()}
    except Exception as e:
        print(f"[HISTORY_LOOP] Init error: {e}")
    
    while True:
        try:
            current_jobs = list(manager.jobs.values()) + list(active_torrents.values())
            for job in current_jobs:
                jid = job.job_id
                if jid not in saved_ids and job.status in ("completed", "failed"):
                    if getattr(job, "url", None) and "BURST_INTERNAL_CHECK" in job.url:
                        continue
                    save_to_history(job.to_dict())
                    saved_ids.add(jid)
        except Exception as e:
            print(f"[HISTORY_LOOP] error: {e}")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run firewall and native messaging host setup in a separate thread so it doesn't block startup
    from threading import Thread
    from setup_utils import apply_firewall_rules, setup_native_host
    def startup_setup():
        apply_firewall_rules()
        setup_native_host()

    Thread(target=startup_setup, daemon=True).start()

    # Load state
    await load_state()

    # Start clipboard monitor (enabled by setting)
    clipboard_stop = asyncio.Event()
    app.state.clipboard_stop = clipboard_stop
    loop = asyncio.get_event_loop()
    _start_clipboard_monitor_if_enabled(app, clipboard_stop, loop)

    # Start scheduler (non-fatal — app boots even if scheduler fails)
    try:
        import scheduler as sched_module
        sched_module.init_scheduler(
            broadcast_fn=broadcast_event,
            manager=manager,
            active_torrents_dict=active_torrents,
            interfaces_fn=_interfaces_by_ip,
        )
    except Exception as e:
        print(f"[SCHEDULER] Failed to start scheduler: {e}")

    # Start polling loops
    task = asyncio.create_task(interface_polling_loop())
    state_task = asyncio.create_task(save_state_loop())
    history_task = asyncio.create_task(history_saver_loop())
    yield
    # Shutdown: Clean up if needed
    if not clipboard_stop.is_set():
        clipboard_stop.set()
    try:
        import scheduler as sched_module
        sched_module.shutdown_scheduler()
    except Exception:
        pass
    task.cancel()
    state_task.cancel()
    history_task.cancel()
    try:
        await task
        await state_task
        await history_task
    except asyncio.CancelledError:
        pass


def _start_clipboard_monitor_if_enabled(app, stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop):
    """Start clipboard monitor if the setting is enabled. Safe to call on non-Windows."""
    settings = config.load_settings()
    if not settings.get("CLIPBOARD_MONITOR_ENABLED", False):
        return
    _launch_clipboard_thread(stop_event, loop)


def _launch_clipboard_thread(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop):
    """Actually launch the clipboard monitor in a daemon thread."""
    import threading
    def bridge():
        from clipboard_monitor import start_monitor
        def on_url_detected(url: str):
            print(f"[clipboard] URL detected: {url}", flush=True)
            asyncio.run_coroutine_threadsafe(
                broadcast_event("clipboard_url", {"url": url}),
                loop
            )
        start_monitor(on_url_detected, stop_event, poll_interval=2.0)
    t = threading.Thread(target=bridge, daemon=True)
    t.start()

app = FastAPI(title="Burst API", version="1.2.1", lifespan=lifespan)
manager = DownloadManager()
active_sockets: Dict[str, Set[WebSocket]] = {}
event_bus: Set[WebSocket] = set()

# Global reference for window and quitting flag
window_ref = None
is_quitting = False
uvicorn_server = None

async def broadcast_event(event_type: str, data: Any):
    """Notify all global event listeners."""
    message = {"type": event_type, "data": data}
    disconnected = set()
    for ws in event_bus:
        try:
            await ws.send_json(message)
        except:
            disconnected.add(ws)
    for ws in disconnected:
        event_bus.discard(ws)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _interfaces_by_ip() -> Dict[str, Dict[str, Any]]:
    return {str(item["ip_address"]): item for item in get_active_interfaces_dict()}


# ---------------------------------------------------------------------------
# Interface & speed endpoints
# ---------------------------------------------------------------------------
@app.get("/interfaces")
async def interfaces(benchmark: bool = True) -> Dict[str, Any]:
    base = get_active_interfaces_dict()
    if benchmark:
        speed_data = await benchmark_interfaces(base)
        by_ip = {str(item["ip_address"]): item for item in speed_data}
        for interface in base:
            matched = by_ip.get(str(interface["ip_address"]))
            interface["speed_mb_s"] = matched.get("speed_mb_s", 0.0) if matched else 0.0
            interface["speedtest_error"] = matched.get("error") if matched else None
    else:
        for interface in base:
            interface["speed_mb_s"] = 0.0
            interface["speedtest_error"] = None
    return {"interfaces": base, "count": len(base)}


@app.post("/speedtest")
async def speedtest() -> Dict[str, Any]:
    interfaces_data = get_active_interfaces_dict()
    results = await benchmark_interfaces(interfaces_data)
    return {"results": results}


# ---------------------------------------------------------------------------
# URL type detection
# ---------------------------------------------------------------------------
@app.get("/url-type")
async def get_url_type(url: str) -> Dict[str, Any]:
    """Lightweight check — is this URL a file or an HTML page? Does a HEAD request, fallback to GET."""
    try:
        import aiohttp
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        async with aiohttp.ClientSession() as session:
            ct = ""
            status = 0
            try:
                async with session.head(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    status = resp.status
            except Exception:
                pass
            
            # Fall back to GET if HEAD fails, returns non-2xx/non-3xx, or returns no content type
            if status < 200 or status >= 400 or not ct:
                try:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as resp:
                        ct = resp.headers.get("Content-Type", "")
                        status = resp.status
                except Exception as e:
                    return {"type": "file", "error": str(e)}

            if "text/html" in ct or "application/xhtml" in ct:
                # Try to extract page title quickly
                title = None
                try:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as get_resp:
                        if get_resp.status == 200:
                            text = await get_resp.text()
                            import re
                            m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
                            if m:
                                title = m.group(1).strip()
                except Exception:
                    pass
                return {"type": "html_page", "title": title}
            return {"type": "file"}
    except Exception as e:
        return {"type": "file", "error": str(e)}


# ---------------------------------------------------------------------------
# Batch scan endpoint
# ---------------------------------------------------------------------------
@app.post("/batch-scan")
async def batch_scan(payload: BatchScanRequest) -> Dict[str, Any]:
    """Scan a webpage for downloadable links. Returns a list of {url, filename}."""
    from batch_downloader import scan_page

    try:
        result = await asyncio.wait_for(
            scan_page(payload.url),
            timeout=30.0
        )
        return result
    except asyncio.TimeoutError:
        return {"urls": [], "total": 0, "error": "Scan timed out after 30 seconds"}


@app.post("/batch-download")
async def batch_download(payload: BatchDownloadRequest) -> Dict[str, Any]:
    """Start downloads for multiple URLs at once. Returns list of job_ids."""
    from batch_downloader import guess_filename
    all_ifaces = _interfaces_by_ip()
    default_ips = list(all_ifaces.keys())
    job_ids = []
    errors = []

    for url in payload.urls:
        try:
            # Determine filename for this URL
            filename = guess_filename(url)
            output = payload.output_path
            # If output_path is a directory, append filename
            if not output.endswith("/") and not output.endswith("\\"):
                if "." not in output.split("\\")[-1]:
                    output = output + "/" + filename
            else:
                output = output + filename

            selected = [all_ifaces[ip] for ip in default_ips if ip in all_ifaces]
            job = await manager.create_job(url.strip(), output, selected, None)
            await broadcast_event("new_job", {"job_id": job.job_id})
            save_state()
            job_ids.append(job.job_id)
        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    return {"job_ids": job_ids, "errors": errors if errors else None}


# ---------------------------------------------------------------------------
# yt-dlp info + download endpoints
# ---------------------------------------------------------------------------

class YtDlpDownloadRequest(BaseModel):
    url: str = Field(min_length=5)
    format_id: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    label: str = ""
    interface_ips: List[str] = []
    streamable: bool = False


@app.get("/yt-dlp/info")
async def ytdlp_info(url: str) -> Dict[str, Any]:
    """Fetch video metadata from yt-dlp. Returns formats list if supported."""
    from ytdlp_handler import fetch_info
    return await fetch_info(url)


@app.post("/yt-dlp/download")
async def ytdlp_download(payload: YtDlpDownloadRequest) -> Dict[str, Any]:
    """Start a yt-dlp download. Returns job_id; progress is broadcast via WS."""
    from ytdlp_handler import start_ytdlp_download, YTDLP_AVAILABLE
    if not YTDLP_AVAILABLE:
        raise HTTPException(status_code=503, detail="yt-dlp is not installed on this server")
    job = await start_ytdlp_download(
        url=payload.url,
        format_id=payload.format_id,
        output_path=payload.output_path,
        label=payload.label,
        broadcast_fn=broadcast_event,
        interface_ips=payload.interface_ips,
        streamable=payload.streamable,
    )
    await broadcast_event("new_job", {"job_id": job.job_id})
    return {"job_id": job.job_id, "status": job.status, "type": "ytdlp"}


@app.post("/yt-dlp/cancel/{job_id}")
async def ytdlp_cancel(job_id: str) -> Dict[str, Any]:
    from ytdlp_handler import get_ytdlp_job
    job = get_ytdlp_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="yt-dlp job not found")
    job.is_cancelled = True
    job.status = "failed"
    job.error = "Cancelled by user"
    await broadcast_event("job_error", {"job_id": job_id, "error": "Cancelled by user"})
    return {"cancelled": True}


@app.post("/analyze")
async def analyze(payload: AnalyzeRequest) -> Dict[str, Any]:
    try:
        result = await analyze_url(payload.url, payload.interface_ip)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/download")
async def start_download(payload: DownloadRequest) -> Dict[str, Any]:
    # Show the hidden window on new actual download
    if "BURST_INTERNAL_CHECK" not in payload.url:
        global window_ref
        if window_ref:
            try:
                window_ref.show()
            except Exception as e:
                print(f"Error showing window: {e}")

    # Auto-detect download type
    is_torrent = payload.url.startswith("magnet:")
    
    if is_torrent:
        try:
            job = await start_torrent_download(payload.url, payload.output_path, payload.interface_ips, payload.bandwidth_limits)
            # Only broadcast if it's not an internal check (though pings usually aren't magnets)
            if "BURST_INTERNAL_CHECK" not in payload.url:
                await broadcast_event("new_job", {"job_id": job.job_id})
                save_state()
            return {"job_id": job.job_id, "status": job.status, "type": "torrent"}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Regular multi-interface downloader
    all_ifaces = _interfaces_by_ip()
    selected = [all_ifaces[ip] for ip in payload.interface_ips if ip in all_ifaces]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid interfaces selected")
    try:
        job = await manager.create_job(payload.url, payload.output_path, selected, payload.bandwidth_limits)
        
        # Don't notify UI about internal health checks
        if "BURST_INTERNAL_CHECK" not in payload.url:
            await broadcast_event("new_job", {"job_id": job.job_id})
            save_state()
            
        return {"job_id": job.job_id, "status": job.status, "type": "download"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/download/{job_id}")
async def get_download_status(job_id: str) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.post("/download/{job_id}/cancel")
async def cancel_download(job_id: str) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        tjob = active_torrents.get(job_id)
        if not tjob:
            from ytdlp_handler import get_ytdlp_job
            ytjob = get_ytdlp_job(job_id)
            if ytjob:
                ytjob.is_cancelled = True
                ytjob.status = "failed"
                ytjob.error = "Cancelled by user"
                for sub_id in ytjob.sub_jobs:
                    try:
                        await manager.cancel_job(sub_id)
                    except:
                        pass
                save_state()
                return {"status": "cancelled"}
            raise HTTPException(status_code=404, detail="Job not found")
        tjob.is_cancelled = True
        save_state()
        return {"status": "cancelled"}
    job.is_cancelled = True
    save_state()
    return {"status": "cancelled"}


@app.post("/download/{job_id}/interfaces")
async def add_interfaces_to_job(job_id: str, payload: AddInterfacesRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    all_ifaces = _interfaces_by_ip()
    selected = [all_ifaces[ip] for ip in payload.interface_ips if ip in all_ifaces]
    results = []
    for iface in selected:
        try:
            result = await manager.add_interface(job_id, iface)
            print(f"[ADD_IFACE] {iface['ip_address']} -> {result}")
            results.append(result)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": len(selected), "results": results}


@app.get("/active-jobs")
async def get_active_jobs() -> Dict[str, Any]:
    """Returns a list of all active (not completed/failed) job IDs, excluding system pings."""
    from ytdlp_handler import get_all_ytdlp_jobs
    regular_ids = [
        jid for jid, job in manager.jobs.items() 
        if job.status not in ("completed", "failed")
        and "BURST_INTERNAL_CHECK" not in job.url
    ]
    torrent_ids = [jid for jid, job in active_torrents.items() if job.status not in ("completed", "failed")]
    ytdlp_ids = [jid for jid, job in get_all_ytdlp_jobs().items() if job.status not in ("completed", "failed")]
    return {"job_ids": regular_ids + torrent_ids + ytdlp_ids}


@app.get("/history")
async def get_history():
    # 1. Start with the persistent history from disk
    all_history = load_history()
    
    # 2. Get current session jobs (regular and torrents)
    current_jobs = [j.to_dict() for j in manager.jobs.values()]
    current_jobs += [j.to_dict() for j in active_torrents.values()]
    
    # Filter: Only completed or failed jobs, skip system checks
    finished_current = [
        j for j in current_jobs 
        if j["status"] in ("completed", "failed") 
        and j.get("filename") != "BURST_INTERNAL_CHECK"
        and "BURST_INTERNAL_CHECK" not in (j.get("url") or "")
    ]
    
    # 3. Merge them (disk history + current finished jobs)
    # Using job_id to avoid duplicates
    existing_ids = {item.get("job_id") for item in all_history}
    for j in finished_current:
        if j.get("job_id") not in existing_ids:
            all_history.append(j)
            save_to_history(j) # Persist it immediately if it's new
            
    # Sort by finished_at desc
    all_history.sort(key=lambda x: x.get("finished_at", 0) or 0, reverse=True)
    return {"history": all_history}


@app.post("/history/clear")
async def clear_history_api():
    from history_manager import clear_history
    clear_history()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------
@app.get("/settings")
async def get_settings() -> Dict[str, Any]:
    settings = config.load_settings()
    import platform
    if platform.system() == "Windows":
        try:
            import win32clipboard
        except ImportError:
            settings["CLIPBOARD_MONITOR_REASON"] = "pywin32_not_installed"
    return {"settings": settings}


@app.post("/settings")
async def update_settings(payload: SettingsUpdate) -> Dict[str, Any]:
    # Check if START_ON_BOOT state changes
    old_settings = config.load_settings()
    old_boot = old_settings.get("START_ON_BOOT", True)
    new_boot = payload.settings.get("START_ON_BOOT", True)
    
    updated = config.save_settings(payload.settings)
    
    if old_boot != new_boot:
        from setup_utils import apply_autostart_rules
        apply_autostart_rules(new_boot)
        
    return {"settings": updated}


@app.post("/settings/reset")
async def reset_settings() -> Dict[str, Any]:
    settings = config.reset_settings()
    from setup_utils import apply_autostart_rules
    apply_autostart_rules(settings.get("START_ON_BOOT", True))
    return {"settings": settings}


@app.post("/settings/clipboard-monitor")
async def set_clipboard_monitor(enabled: bool) -> Dict[str, Any]:
    """Enable or disable the clipboard monitor. ctypes-based, no pywin32 required."""
    import platform
    if platform.system() != "Windows":
        return {"supported": False}

    reason = None
    supported = True
    try:
        import win32clipboard
    except ImportError:
        reason = "pywin32_not_installed"
        supported = False

    # Persist the setting
    updated = config.save_settings({"CLIPBOARD_MONITOR_ENABLED": enabled})
    # Start or stop the monitor
    app_state = app.state
    stop_event: asyncio.Event = getattr(app_state, "clipboard_stop", None)
    if not stop_event:
        stop_event = asyncio.Event()
        app_state.clipboard_stop = stop_event
    if enabled:
        loop = asyncio.get_event_loop()
        _launch_clipboard_thread(stop_event, loop)
    else:
        if not stop_event.is_set():
            stop_event.set()

    if reason:
        updated["CLIPBOARD_MONITOR_REASON"] = reason

    return {"supported": supported, "enabled": enabled, "settings": updated, "reason": reason}


def _calculate_hash(file_path: str, expected: str, algorithm: Optional[str] = None) -> dict:
    import os
    import hashlib
    if not os.path.exists(file_path):
        return {"match": False, "actual": "", "algorithm": "", "error": "File not found"}

    expected_clean = expected.strip().lower()
    algo = algorithm
    if not algo:
        length = len(expected_clean)
        if length == 32:
            algo = "md5"
        elif length == 40:
            algo = "sha1"
        elif length == 64:
            algo = "sha256"
        else:
            return {
                "match": False,
                "actual": "",
                "algorithm": "",
                "error": "Unsupported expected hash length. Must be MD5 (32 chars), SHA1 (40 chars), or SHA256 (64 chars)."
            }

    algo_lower = algo.lower()
    if algo_lower not in ("md5", "sha1", "sha256"):
        return {"match": False, "actual": "", "algorithm": algo, "error": f"Unsupported algorithm: {algo}. Supported: md5, sha1, sha256"}

    try:
        if algo_lower == "md5":
            hasher = hashlib.md5()
        elif algo_lower == "sha1":
            hasher = hashlib.sha1()
        else:
            hasher = hashlib.sha256()

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)

        actual_hash = hasher.hexdigest().lower()
        return {
            "match": actual_hash == expected_clean,
            "actual": actual_hash,
            "algorithm": algo_lower,
            "error": None
        }
    except Exception as e:
        return {"match": False, "actual": "", "algorithm": algo_lower, "error": str(e)}


@app.post("/verify-checksum")
async def verify_checksum(req: ChecksumRequest):
    return await asyncio.to_thread(_calculate_hash, req.file_path, req.expected, req.algorithm)


@app.delete("/file")
async def delete_file(req: FileDeleteRequest):
    import os
    try:
        if os.path.exists(req.file_path):
            os.remove(req.file_path)
            return {"success": True, "message": "File deleted successfully"}
        else:
            return {"success": False, "error": "File not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}



# ---------------------------------------------------------------------------
# Schedule endpoints
# ---------------------------------------------------------------------------

@app.post("/schedule")
async def create_schedule(req: ScheduleRequest) -> Dict[str, Any]:
    """Create a new scheduled download."""
    import scheduler as sched_module
    from datetime import datetime, timezone

    # Parse and validate the time is in the future
    try:
        fire_dt = datetime.fromisoformat(req.scheduled_time)
        if fire_dt.tzinfo is None:
            fire_dt = fire_dt.astimezone()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_time format. Use ISO 8601.")

    now = datetime.now(timezone.utc).astimezone()
    if fire_dt <= now:
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future.")

    if req.repeat not in ("once", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="repeat must be 'once', 'daily', or 'weekly'.")

    entry = sched_module.add_schedule(
        url=req.url,
        output_path=req.output_path,
        scheduled_time=req.scheduled_time,
        repeat=req.repeat,
    )
    return entry


@app.get("/schedules")
async def list_schedules() -> Dict[str, Any]:
    """Return all pending scheduled downloads."""
    import scheduler as sched_module
    return {"schedules": sched_module.get_all_schedules()}


@app.get("/schedules/missed")
async def list_missed_schedules() -> Dict[str, Any]:
    """Return schedules that were missed while the app was closed."""
    import scheduler as sched_module
    return {"missed": sched_module.get_missed_schedules()}


@app.delete("/schedules/missed")
async def clear_missed_schedules() -> Dict[str, Any]:
    """Dismiss the missed schedules list."""
    import scheduler as sched_module
    sched_module.dismiss_missed()
    return {"status": "cleared"}


@app.delete("/schedules/{schedule_id}")
async def cancel_schedule(schedule_id: str) -> Dict[str, Any]:
    """Cancel a scheduled download."""
    import scheduler as sched_module
    ok = sched_module.remove_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "cancelled", "schedule_id": schedule_id}


@app.get("/select-path")
async def select_path():
    """Opens a native directory selection dialog and returns the selected path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory()
        root.destroy()
        return {"path": path if path else None}
    except Exception as e:
        return {"path": None, "error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket for real-time progress & Interface Hotplug Polling
# ---------------------------------------------------------------------------
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    event_bus.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.discard(websocket)

@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str) -> None:
    if job_id == "interfaces":
        return await websocket_interfaces(websocket)
        
    await websocket.accept()
    active_sockets.setdefault(job_id, set()).add(websocket)
    try:
        while True:
            from ytdlp_handler import get_ytdlp_job
            job = manager.get_job(job_id)
            if not job:
                tjob = active_torrents.get(job_id)
                ytjob = get_ytdlp_job(job_id)
                if tjob:
                    await websocket.send_json(tjob.to_dict())
                    if tjob.status in {"completed", "failed"}:
                        break
                elif ytjob:
                    await websocket.send_json(ytjob.to_dict())
                    if ytjob.status in {"completed", "failed"}:
                        break
                else:
                    await websocket.send_json({"error": "Job not found"})
                    break
            else:
                await websocket.send_json(job.to_dict())
                if job.status in {"completed", "failed"}:
                    break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    finally:
        active_sockets.get(job_id, set()).discard(websocket)

interfaces_ws_sockets: Set[WebSocket] = set()

async def websocket_interfaces(websocket: WebSocket) -> None:
    await websocket.accept()
    interfaces_ws_sockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        interfaces_ws_sockets.discard(websocket)

async def interface_polling_loop():
    last_interfaces = {}
    while True:
        try:
            current_interfaces = get_active_interfaces_dict()
            current_dict = {str(i["ip_address"]): i for i in current_interfaces}
            
            if last_interfaces:
                added = []
                removed = []
                for ip, i in current_dict.items():
                    if ip not in last_interfaces:
                        added.append(i)
                for ip, i in last_interfaces.items():
                    if ip not in current_dict:
                        removed.append(i)
                        
                for i in added:
                    ip = i["ip_address"]
                    msg = {"event": "interface_added", "interface": {"name": i["name"], "ip": ip, "type": i.get("interface_type", ""), "speed": 0}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
                for i in removed:
                    ip = i["ip_address"]
                    msg = {"event": "interface_removed", "interface": {"name": i["name"], "ip": ip}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
            
            last_interfaces = current_dict
        except Exception:
            pass
        await asyncio.sleep(3)

import json

async def load_state():
    try:
        if os.path.exists("burst_active_jobs.json"):
            with open("burst_active_jobs.json", "r") as f:
                state = json.load(f)
            
            # Use active interfaces to map them back
            all_ifaces = get_active_interfaces_dict()
            
            for t_job in state.get("torrents", []):
                if t_job.get("status") in ("cancelled", "failed"):
                    continue
                try:
                    await start_torrent_download(
                        magnet_uri=t_job["magnet_uri"],
                        output_path=t_job["output_path"],
                        interface_ips=t_job["interface_ips"],
                        bandwidth_limits=t_job.get("bandwidth_limits", {}),
                        job_id=t_job["job_id"],
                        resume_data=t_job
                    )
                except Exception as e:
                    print(f"Error resuming torrent job: {e}")

            for d_job in state.get("downloads", []):
                if d_job.get("status") in ("cancelled", "failed"):
                    continue
                try:
                    interfaces = []
                    for ip in d_job.get("interfaces", {}):
                        # Match with active interfaces if possible
                        matched = next((i for i in all_ifaces if i["ip_address"] == ip), None)
                        if matched:
                            interfaces.append(matched)
                        else:
                            interfaces.append({"name": "Unknown", "ip_address": ip})

                    if not interfaces:
                        if all_ifaces:
                            interfaces.append(all_ifaces[0])

                    await manager.resume_job_from_state(d_job, interfaces)
                except Exception as e:
                    print(f"Error resuming download job: {e}")
    except Exception as e:
        print(f"Failed to load state: {e}")

def save_state():
    try:
        downloads = [
            j.to_dict() for j in manager.jobs.values()
            if j.status not in ("completed", "failed", "cancelled")
            and not getattr(j, "is_cancelled", False)
            and "BURST_INTERNAL_CHECK" not in j.url
        ]
        torrents = [
            j.to_dict() for j in active_torrents.values()
            if j.status not in ("completed", "failed", "cancelled")
            and not getattr(j, "is_cancelled", False)
        ]
        
        with open("burst_active_jobs.json", "w") as f:
            json.dump({"downloads": downloads, "torrents": torrents}, f)
    except Exception as e:
        print(f"State save error: {e}")

async def save_state_loop():
    while True:
        save_state()
        await asyncio.sleep(5)

# startup/shutdown handled by lifespan decorator

@app.post("/download/{job_id}/add_interface")
async def add_single_interface_to_job(job_id: str, payload: AddInterfaceRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        # Check if it's a torrent job
        tjob = active_torrents.get(job_id)
        if not tjob:
            from ytdlp_handler import get_ytdlp_job
            ytjob = get_ytdlp_job(job_id)
            if ytjob:
                all_ifaces = _interfaces_by_ip()
                iface = all_ifaces.get(payload.interface_ip)
                if not iface:
                    raise HTTPException(status_code=404, detail="Interface not found")
                results = []
                for sub_id in ytjob.sub_jobs:
                    try:
                        res = await manager.add_interface(sub_id, iface)
                        results.append(res)
                    except Exception as e:
                        print(f"[main] Failed to add interface to yt sub-job {sub_id}: {e}")
                return {"status": "success", "added": payload.interface_ip, "detail": results}
            raise HTTPException(status_code=404, detail="Job not found")
        all_ifaces = get_active_interfaces_dict()
        iface = next((i for i in all_ifaces if i["ip_address"] == payload.interface_ip), None)
        if not iface:
            raise HTTPException(status_code=404, detail="Interface not found")
        return await tjob.add_interface(payload.interface_ip, iface["name"])
    all_ifaces = _interfaces_by_ip()
    iface = all_ifaces.get(payload.interface_ip)
    if not iface:
        raise HTTPException(status_code=404, detail="Interface not found")
    try:
        result = await manager.add_interface(job_id, iface)
        print(f"[ADD_IFACE] Single add {payload.interface_ip} -> {result}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": payload.interface_ip, "detail": result}

@app.post("/download/{job_id}/remove_interface")
async def remove_interface_from_job(job_id: str, payload: RemoveInterfaceRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        # Check if it's a torrent job
        tjob = active_torrents.get(job_id)
        if not tjob:
            from ytdlp_handler import get_ytdlp_job
            ytjob = get_ytdlp_job(job_id)
            if ytjob:
                results = []
                for sub_id in ytjob.sub_jobs:
                    try:
                        res = await manager.remove_interface(sub_id, payload.interface_ip)
                        results.append(res)
                    except Exception as e:
                        print(f"[main] Failed to remove interface from yt sub-job {sub_id}: {e}")
                return {"status": "success", "removed": payload.interface_ip, "detail": results}
            raise HTTPException(status_code=404, detail="Job not found")
        return await tjob.remove_interface(payload.interface_ip)
        
    try:
        result = await manager.remove_interface(job_id, payload.interface_ip)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/download/{job_id}/interface/{ip}/exclude")
async def exclude_interface(job_id: str, ip: str) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        result = await manager.remove_interface(job_id, ip)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/download/{job_id}/pause")
async def pause_download(job_id: str) -> Dict[str, Any]:
    # Check regular manager first
    job = manager.get_job(job_id)
    if job:
        try:
            res = await manager.pause_job(job_id)
            save_state()
            return res
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
            
    # Check torrent manager
    tjob = active_torrents.get(job_id)
    if tjob:
        res = await tjob.pause()
        save_state()
        return res
        
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/download/{job_id}/resume")
async def resume_download(job_id: str) -> Dict[str, Any]:
    # Check regular manager first
    job = manager.get_job(job_id)
    if job:
        try:
            res = await manager.resume_job(job_id)
            save_state()
            return res
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
            
    # Check torrent manager
    tjob = active_torrents.get(job_id)
    if tjob:
        res = await tjob.resume()
        save_state()
        return res
        
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/download/{job_id}/cancel")
async def cancel_download(job_id: str) -> Dict[str, Any]:
    # Check regular manager first
    job = manager.get_job(job_id)
    if job:
        res = await manager.cancel_job(job_id)
        save_state()
        return res
    
    # Check torrent manager
    tjob = active_torrents.get(job_id)
    if tjob:
        res = await tjob.cancel()
        save_state()
        return res
    
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/download/{job_id}/boost")
async def toggle_boost_download(job_id: str) -> Dict[str, Any]:
    active_ifaces = get_active_interfaces_dict()
    # Check regular manager first
    job = manager.get_job(job_id)
    if job:
        try:
            res = await manager.toggle_boost(job_id, active_ifaces)
            save_state()
            return res
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
            
    # Check torrent manager
    tjob = active_torrents.get(job_id)
    if tjob:
        res = await tjob.toggle_boost(active_ifaces)
        save_state()
        return res
        
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/torrent/start")
async def start_torrent_api(req: TorrentStartRequest) -> Dict[str, str]:
    # Show the hidden window on new torrent download
    global window_ref
    if window_ref:
        try:
            window_ref.show()
        except Exception as e:
            print(f"Error showing window: {e}")

    if not req.interface_ips:
        raise HTTPException(status_code=400, detail="No interfaces selected")
    try:
        job = await start_torrent_download(req.magnet_uri, req.output_path, req.interface_ips, req.bandwidth_limits)
        await broadcast_event("new_job", {"job_id": job.job_id})
        save_state()
        return {"job_id": job.job_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/torrent/{job_id}")
async def get_torrent_status(job_id: str) -> Dict[str, Any]:
    job = active_torrents.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()

@app.post("/show")
async def show_window_endpoint():
    global window_ref
    if window_ref:
        try:
            window_ref.show()
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
    return {"status": "no_window"}

@app.post("/shutdown")
async def shutdown_endpoint():
    global uvicorn_server
    if uvicorn_server:
        uvicorn_server.should_exit = True
    return {"status": "shutting_down"}

# ---------------------------------------------------------------------------
# Serve Production UI
# ---------------------------------------------------------------------------
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # In dev, we are in backend/main.py, so we go up one level to the root
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    return os.path.join(base_path, relative_path)

frontend_dist = resource_path("frontend/dist")

# Mount assets folder for JS/CSS if it exists
assets_dir = os.path.join(frontend_dist, "assets")
if os.path.exists(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

@app.get("/", include_in_schema=False)
async def serve_index():
    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(content=f"<h3>Burst UI Error</h3><p>index.html not found at: {index_path}</p>", status_code=404)

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    # Check if the requested path is a file in the dist root (like favicon.ico)
    file_path = os.path.join(frontend_dist, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    
    # SPA Fallback: serve index.html for unknown routes
    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    return HTMLResponse(content="<h3>Burst UI Not Found</h3>", status_code=404)

def setup_tray(window):
    try:
        import pystray
        from PIL import Image
        import threading
        import sys
        import os

        def load_tray_icon():
            try:
                # Try standard assets/logo.png path (dev)
                logo_path = resource_path("assets/logo.png")
                if os.path.exists(logo_path):
                    return Image.open(logo_path)
                # Try root logo.png path (PyInstaller bundle root)
                root_logo_path = resource_path("logo.png")
                if os.path.exists(root_logo_path):
                    return Image.open(root_logo_path)
            except:
                pass
            # Fallback block
            return Image.new('RGB', (64, 64), color=(0, 102, 204))

        def on_open(icon, item):
            window.show()

        def on_exit(icon, item):
            global is_quitting
            is_quitting = True
            try:
                import requests as req
                req.post("http://127.0.0.1:59284/shutdown", timeout=2)
            except Exception:
                pass
            icon.stop()
            try:
                window.destroy()
            except:
                pass
            sys.exit(0)

        image = load_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem('Open Burst', on_open, default=True),
            pystray.MenuItem('Quit', on_exit)
        )
        icon = pystray.Icon("Burst", image, "Burst Download Manager", menu)
        
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        print("System tray icon started successfully.")
    except Exception as e:
        print(f"Failed to initialize system tray: {e}")

if __name__ == "__main__":
    # 1. Handle CLI commands (NO elevation needed)
    # Check if 'pip' is anywhere in the arguments to avoid logic leaks
    if any(arg == "pip" for arg in sys.argv):
        try:
            from cli import run_pip_cli
            run_pip_cli()
        finally:
            # Absolute exit to prevent any fall-through to elevation code
            os._exit(0)

    # If running with python directly, we might not want elevation immediately
    # or we might be in an environment where we can't elevate easily.
    # For dev purposes, let's allow bypassing elevation if requested or if in dev.
    from setup_utils import is_admin, request_elevation
    if not is_admin() and getattr(sys, 'frozen', False):
        request_elevation()

    # Hide the console window immediately for GUI mode (only when frozen)
    if getattr(sys, 'frozen', False):
        hide_console()

    import uvicorn
    import webview
    from threading import Thread
    import sys
    import urllib.request

    # Parse headless argument
    headless = False
    if "--headless" in sys.argv:
        headless = True
        sys.argv.remove("--headless")

    # 0. Single instance check using socket (more reliable than HTTP timeout)
    import socket
    is_running = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("127.0.0.1", 59284))
        is_running = True
    except:
        pass

    startup_url = None
    if is_running:
        if len(sys.argv) > 1:
            try:
                import os
                sys.path.insert(0, os.path.dirname(__file__))
                from native_host import handle
                handle({"url": sys.argv[1]})
            except Exception as e:
                print("Error sending to running instance:", e)
        else:
            # Wake up/show the existing UI
            try:
                import urllib.request
                req = urllib.request.Request("http://127.0.0.1:59284/show", method="POST")
                with urllib.request.urlopen(req) as response:
                    pass
            except Exception as e:
                print("Error waking up running instance UI:", e)
        sys.exit(0)
    else:
        if len(sys.argv) > 1:
            startup_url = sys.argv[1]

    # 1. Define the server thread
    def start_server():
        global uvicorn_server
        # Full silent config for Uvicorn
        full_silence_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(message)s", "use_colors": False},
                "access": {"()": "uvicorn.logging.AccessFormatter", "fmt": "%(message)s", "use_colors": False},
            },
            "handlers": {"null": {"class": "logging.NullHandler"}},
            "loggers": {
                "uvicorn": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.error": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.access": {"handlers": ["null"], "level": "CRITICAL"},
            },
        }
        # Passing 'app' object directly is safer for PyInstaller
        config_obj = uvicorn.Config(app, host="127.0.0.1", port=59284, reload=False, log_config=full_silence_config, use_colors=False)
        uvicorn_server = uvicorn.Server(config_obj)
        uvicorn_server.run()

    # 2. Start server in background
    t = Thread(target=start_server)
    t.daemon = True
    t.start()

    # 3. Create the API for Window Controls
    class WindowAPI:
        def __init__(self):
            self._window = None
        def set_window(self, window):
            self._window = window

        def close(self):
            if self._window: self._window.hide()
        def minimize(self):
            if self._window: self._window.minimize()
        def maximize(self):
            if self._window:
                if self._window.fullscreen:
                    self._window.toggle_fullscreen()
                else:
                    self._window.maximize()

    api = WindowAPI()

    # 4. Define the background loader (handles backend status polling and startup URL)
    def load_logic(window):
        import time
        import urllib.request
        server_ready = False
        for _ in range(40):  # Wait up to 4 seconds
            try:
                urllib.request.urlopen("http://127.0.0.1:59284/interfaces?benchmark=false", timeout=0.1)
                server_ready = True
                break
            except Exception:
                time.sleep(0.1)
        
        if not server_ready:
            window.load_html("<h3>Burst could not start</h3><p>The local API did not become ready. Please restart Burst.</p>")
            return
            
        time.sleep(0.3)  # Grace period to let background state loads complete smoothly
        window.load_url("http://127.0.0.1:59284")

        if startup_url:
            try:
                import os
                import sys
                sys.path.insert(0, os.path.dirname(__file__))
                from native_host import handle
                handle({"url": startup_url})
            except Exception as e:
                print(f"Failed to process startup URL: {e}")

    # 5. Create and Start directly with beautiful HTML loading page to prevent delays
    loading_html = """<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            margin: 0;
            padding: 0;
            background: #0f0f11;
            color: #f3f4f6;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            overflow: hidden;
            -webkit-user-select: none;
            user-select: none;
        }
        .container {
            text-align: center;
        }
        .logo {
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 1rem;
            letter-spacing: -0.05em;
        }
        .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid rgba(249, 115, 22, 0.1);
            border-radius: 50%;
            border-top-color: #f97316;
            animation: spin 1s ease-in-out infinite;
            margin: 0 auto 1.5rem auto;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .status {
            font-size: 0.875rem;
            color: #9ca3af;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">BURST</div>
        <div class="spinner"></div>
        <div class="status">Connecting to engine...</div>
    </div>
</body>
</html>"""

    window = webview.create_window(
        "Burst", 
        html=loading_html,
        width=950, 
        height=680, 
        frameless=True, 
        easy_drag=True,
        resizable=False,
        hidden=headless,
        js_api=api
    )
    api.set_window(window)
    
    # Keep reference to the window globally
    window_ref = window

    # Intercept window close (closing) event to hide instead of destroy
    def on_closing():
        global is_quitting
        if is_quitting:
            return True
        if window:
            window.hide()
        return False
        
    window.events.closing += on_closing

    # Start the system tray icon
    setup_tray(window)
    
    # Start the engine - this runs the load_logic in the background
    webview.start(load_logic, window)
