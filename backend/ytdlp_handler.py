"""
Burst — yt-dlp integration handler.

Provides two async-safe functions:
  - fetch_info(url)      -> metadata dict (formats, title, thumbnail, duration)
  - run_download(...)    -> background download with live progress via callback
"""
from __future__ import annotations

import asyncio
import uuid
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

# yt-dlp is optional — Burst still starts if it's not installed.
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    print("[yt-dlp] WARNING: yt-dlp not installed. Video download features disabled.")

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ytdlp")

TIMEOUT_SECONDS = 15


def _friendly_ytdlp_error(raw: str) -> str:
    """Translate yt-dlp error messages into user-friendly text."""
    r = (raw or "").lower()
    if "sign in to confirm your age" in r or "age" in r and "verify" in r:
        return "This video requires age verification — yt-dlp cannot download it"
    if "video unavailable" in r or "this video is unavailable" in r:
        return "This video is unavailable or has been removed"
    if "private video" in r or "this video is private" in r:
        return "This video is private"
    if "geo" in r and ("block" in r or "restrict" in r):
        return "This video is not available in your region"
    if "429" in r or "too many requests" in r:
        return "Rate limited — too many requests, try again later"
    if "copyright" in r:
        return "This content is unavailable due to copyright restrictions"
    if "network" in r or "connection" in r:
        return "Network error — check your internet connection"
    if "no video formats found" in r:
        return "No downloadable formats found for this URL"
    # Strip yt-dlp internal prefix noise
    for prefix in ("ERROR: ", "youtube ", "[youtube] "):
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix):].strip()
    return raw or "yt-dlp download failed"


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _build_format_list(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a clean, de-duplicated format list sorted by quality."""
    formats = info.get("formats") or []
    seen_labels: set = set()
    result: List[Dict[str, Any]] = []

    # Priority labels in preferred order
    prio = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]

    def height_label(f: Dict) -> Optional[str]:
        h = f.get("height")
        if h:
            return f"{h}p"
        return None

    # Collect best format per height
    height_best: Dict[str, Dict] = {}
    audio_best: Optional[Dict] = None

    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        ext = f.get("ext", "")

        # Skip manifests / storyboards
        if ext in ("mhtml", "vtt"):
            continue

        if vcodec == "none" and acodec != "none":
            # Audio-only — keep best by tbr
            if audio_best is None or (f.get("tbr") or 0) > (audio_best.get("tbr") or 0):
                audio_best = f
        elif vcodec != "none":
            h = f.get("height")
            if h:
                lbl = f"{h}p"
                existing = height_best.get(lbl)
                if existing is None or (f.get("tbr") or 0) > (existing.get("tbr") or 0):
                    height_best[lbl] = f

    # Add video formats in priority order
    for lbl in prio:
        f = height_best.get(lbl)
        if f and lbl not in seen_labels:
            seen_labels.add(lbl)
            result.append({
                "id": f.get("format_id", lbl),
                "label": lbl,
                "ext": f.get("ext", "mp4"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
            })

    # Any remaining heights not in prio list
    for lbl, f in sorted(height_best.items(), key=lambda x: -(int(x[0].rstrip("p")) if x[0].rstrip("p").isdigit() else 0)):
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            result.append({
                "id": f.get("format_id", lbl),
                "label": lbl,
                "ext": f.get("ext", "mp4"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
            })

    # Audio-only at the end
    if audio_best:
        result.append({
            "id": audio_best.get("format_id", "bestaudio"),
            "label": "Audio only",
            "ext": audio_best.get("ext", "m4a"),
            "filesize": audio_best.get("filesize") or audio_best.get("filesize_approx"),
            "tbr": audio_best.get("tbr"),
        })

    return result


def _fetch_info_sync(url: str) -> Dict[str, Any]:
    """Blocking yt-dlp info extraction — call from a thread."""
    if not YTDLP_AVAILABLE:
        return {"supported": False, "error": "yt-dlp not installed"}

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 10,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return {"supported": False}

            formats = _build_format_list(info)
            if not formats:
                return {"supported": False, "error": "No downloadable formats found"}

            return {
                "supported": True,
                "title": info.get("title", ""),
                "thumbnail": info.get("thumbnail", ""),
                "duration": info.get("duration"),
                "duration_str": _format_duration(info.get("duration")),
                "uploader": info.get("uploader", ""),
                "formats": formats,
                "error": None,
            }
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        # Unsupported site = not really an error for us
        if "unsupported url" in msg.lower() or "no suitable" in msg.lower():
            return {"supported": False}
        return {"supported": False, "error": _friendly_ytdlp_error(msg)}
    except Exception as e:
        return {"supported": False, "error": _friendly_ytdlp_error(str(e))}


async def fetch_info(url: str) -> Dict[str, Any]:
    """Async wrapper: fetch yt-dlp info with a 15s timeout."""
    if not YTDLP_AVAILABLE:
        return {"supported": False, "error": "yt-dlp not installed"}
    try:
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(_executor, _fetch_info_sync, url),
            timeout=TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return {"supported": False, "error": "Timed out fetching video info"}
    except Exception as e:
        return {"supported": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class YtDlpJob:
    """Minimal job-compatible object for yt-dlp downloads."""

    def __init__(self, job_id: str, url: str, output_path: str, label: str):
        self.job_id = job_id
        self.url = url
        self.output_path = output_path
        self.label = label
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.total_downloaded: int = 0
        self.expected_size: int = 0
        self.error: Optional[str] = None
        self.is_cancelled: bool = False
        self._cancel_event = asyncio.Event()

        # Progress fields used by the frontend
        self.percent: float = 0.0
        self.speed_bytes: float = 0.0
        self.eta: Optional[float] = None
        self.filename: str = ""

        # Stored for history
        self.type = "ytdlp"

    def to_dict(self) -> Dict[str, Any]:
        total_speed = self.speed_bytes / (1024 * 1024) if self.speed_bytes else 0.0
        iface_dict = {}
        if total_speed > 0:
            iface_dict["ytdlp"] = {
                "name": "yt-dlp",
                "ip_address": "ytdlp",
                "speed_mb_s": total_speed,
                "status": self.status if self.status == "downloading" else "pending",
                "downloaded": self.total_downloaded,
                "weight": 1.0,
                "weight_percent": 100,
                "chunk_start": 0,
                "chunk_end": self.expected_size,
                "latency_ms": 0,
                "chunks_completed": 1 if self.status == "completed" else 0,
                "consecutive_failures": 0,
            }
        return {
            "job_id": self.job_id,
            "url": self.url,
            "output_path": self.output_path,
            "filename": self.filename or os.path.basename(self.output_path),
            "expected_size": self.expected_size,
            "supports_ranges": False,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_downloaded": self.total_downloaded,
            "error": self.error,
            "is_cancelled": self.is_cancelled,
            "interfaces": iface_dict,
            "retry_events": [],
            "bandwidth_limits": {},
            "boosted": False,
            "type": "ytdlp",
            "percent": self.percent,
            "ytdlp_label": self.label,
        }


# Global registry of yt-dlp jobs so the WS handler can look them up
_ytdlp_jobs: Dict[str, YtDlpJob] = {}


def get_ytdlp_job(job_id: str) -> Optional[YtDlpJob]:
    return _ytdlp_jobs.get(job_id)


def get_all_ytdlp_jobs() -> Dict[str, YtDlpJob]:
    return _ytdlp_jobs


def _download_sync(job: YtDlpJob, format_id: str, on_progress: Callable, on_done: Callable, on_error: Callable):
    """Blocking yt-dlp download — runs in a thread."""

    output_dir = job.output_path
    if not os.path.isdir(output_dir):
        # If it looks like a file path, get its directory
        output_dir = os.path.dirname(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    def progress_hook(d: Dict[str, Any]):
        if job.is_cancelled:
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        status = d.get("status", "")
        if status == "downloading":
            job.total_downloaded = d.get("downloaded_bytes") or 0
            job.expected_size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if job.expected_size:
                job.percent = (job.total_downloaded / job.expected_size) * 100
            job.speed_bytes = d.get("speed") or 0.0
            job.eta = d.get("eta")
            job.filename = d.get("filename", job.filename)
            on_progress()
        elif status == "finished":
            job.filename = d.get("filename", job.filename)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": f"{format_id}+bestaudio/best[height<={format_id}]/{format_id}/best",
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "noplaylist": True,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }] if format_id != "bestaudio" else [],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([job.url])
        on_done()
    except yt_dlp.utils.DownloadError as e:
        on_error(_friendly_ytdlp_error(str(e)))
    except Exception as e:
        on_error(_friendly_ytdlp_error(str(e)))


async def start_ytdlp_download(
    url: str,
    format_id: str,
    output_path: str,
    label: str,
    broadcast_fn: Callable,
) -> YtDlpJob:
    """Create a YtDlpJob and kick off the download in a thread."""
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp is not installed")

    job_id = str(uuid.uuid4())
    job = YtDlpJob(job_id, url, output_path, label)
    _ytdlp_jobs[job_id] = job

    loop = asyncio.get_event_loop()

    def on_progress():
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_progress", job.to_dict()), loop
        )

    def on_done():
        job.status = "completed"
        job.finished_at = time.time()
        job.percent = 100.0
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_complete", job.to_dict()), loop
        )

    def on_error(msg: str):
        job.status = "failed"
        job.error = msg
        job.finished_at = time.time()
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_error", {"job_id": job_id, "error": msg}), loop
        )

    def run():
        job.status = "downloading"
        job.started_at = time.time()
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_progress", job.to_dict()), loop
        )
        _download_sync(job, format_id, on_progress, on_done, on_error)

    loop.run_in_executor(_executor, run)
    return job
