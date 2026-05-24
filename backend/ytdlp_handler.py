"""
Burst — yt-dlp integration handler.

Provides two async-safe functions:
  - fetch_info(url)           -> metadata dict (formats, title, thumbnail, duration)
  - start_ytdlp_download(...) -> background download with live progress via callback
"""
from __future__ import annotations

import asyncio
import pathlib
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

TIMEOUT_SECONDS = 20


def _friendly_ytdlp_error(raw: str) -> str:
    """Translate yt-dlp error messages into user-friendly text."""
    r = (raw or "").lower()
    if "sign in to confirm your age" in r or ("age" in r and "verify" in r):
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
    if "no video formats found" in r:
        return "No downloadable formats found for this URL"
    if "ffmpeg" in r or "ffprobe" in r:
        return "ffmpeg error — the video merge failed"
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
    """Build a clean, de-duplicated format list sorted best-first."""
    formats = info.get("formats") or []
    seen_labels: set = set()
    result: List[Dict[str, Any]] = []

    prio = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]

    # Collect best adaptive (video-only DASH) format per height
    height_best: Dict[str, Dict] = {}
    # Collect best pre-merged (progressive) format per height — no ffmpeg needed
    height_safe: Dict[str, Dict] = {}
    audio_best: Optional[Dict] = None

    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        ext = f.get("ext", "")

        if ext in ("mhtml", "vtt", "json3"):
            continue

        if vcodec == "none" and acodec != "none":
            score = (f.get("abr") or 0) + (f.get("tbr") or 0)
            best_score = (audio_best.get("abr") or 0) + (audio_best.get("tbr") or 0) if audio_best else 0
            if audio_best is None or score > best_score:
                audio_best = f
        elif vcodec != "none":
            h = f.get("height")
            if not h:
                continue
            lbl = f"{h}p"
            tbr = f.get("tbr") or 0

            # Best adaptive (DASH) format
            existing = height_best.get(lbl)
            if existing is None or tbr > (existing.get("tbr") or 0):
                height_best[lbl] = f

            # Best pre-merged (progressive, has audio track)
            if acodec != "none":
                existing_safe = height_safe.get(lbl)
                if existing_safe is None or tbr > (existing_safe.get("tbr") or 0):
                    height_safe[lbl] = f

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
                "has_audio": f.get("acodec", "none") != "none",
            })

    # Any non-standard heights
    for lbl, f in sorted(
        height_best.items(),
        key=lambda x: -(int(x[0].rstrip("p")) if x[0].rstrip("p").isdigit() else 0),
    ):
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            result.append({
                "id": f.get("format_id", lbl),
                "label": lbl,
                "ext": f.get("ext", "mp4"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
                "has_audio": f.get("acodec", "none") != "none",
            })

    if audio_best:
        result.append({
            "id": audio_best.get("format_id", "bestaudio"),
            "label": "Audio only",
            "ext": audio_best.get("ext", "m4a"),
            "filesize": audio_best.get("filesize") or audio_best.get("filesize_approx"),
            "tbr": audio_best.get("tbr"),
            "has_audio": True,
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
        "socket_timeout": 15,
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
        if "unsupported url" in msg.lower() or "no suitable" in msg.lower():
            return {"supported": False}
        return {"supported": False, "error": _friendly_ytdlp_error(msg)}
    except Exception as e:
        return {"supported": False, "error": _friendly_ytdlp_error(str(e))}


async def fetch_info(url: str) -> Dict[str, Any]:
    """Async wrapper: fetch yt-dlp info with a timeout."""
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

        self.percent: float = 0.0
        self.speed_bytes: float = 0.0
        self.eta: Optional[float] = None
        self.filename: str = ""
        self.type = "ytdlp"

    def to_dict(self) -> Dict[str, Any]:
        total_speed = self.speed_bytes / (1024 * 1024) if self.speed_bytes else 0.0
        iface_dict: Dict[str, Any] = {}
        if self.status == "downloading" or total_speed > 0:
            iface_dict["ytdlp"] = {
                "name": "yt-dlp",
                "ip_address": "ytdlp",
                "speed_mb_s": total_speed,
                "status": "downloading" if self.status == "downloading" else "pending",
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


_ytdlp_jobs: Dict[str, YtDlpJob] = {}


def get_ytdlp_job(job_id: str) -> Optional[YtDlpJob]:
    return _ytdlp_jobs.get(job_id)


def get_all_ytdlp_jobs() -> Dict[str, YtDlpJob]:
    return _ytdlp_jobs


def _download_sync(
    job: YtDlpJob,
    format_id: str,
    ffmpeg_path: Optional[str],
    on_progress: Callable,
    on_done: Callable,
    on_error: Callable,
) -> None:
    """Blocking yt-dlp download — runs in a thread pool."""

    # output_path is ALWAYS a directory for yt-dlp downloads
    output_dir = pathlib.Path(job.output_path)
    # Guard: if the path looks like a file (has extension), use its parent
    if output_dir.suffix and not output_dir.is_dir():
        output_dir = output_dir.parent
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        on_error(f"Cannot create output folder: {e}")
        return

    is_audio_only = job.label == "Audio only"

    def progress_hook(d: Dict[str, Any]) -> None:
        if job.is_cancelled:
            raise yt_dlp.utils.DownloadError("Cancelled by user")
        status = d.get("status", "")
        if status == "downloading":
            job.total_downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            job.expected_size = max(job.expected_size, total)
            if job.expected_size:
                job.percent = min(99.0, job.total_downloaded / job.expected_size * 100)
            job.speed_bytes = d.get("speed") or 0.0
            job.eta = d.get("eta")
            fn = d.get("filename", "")
            if fn:
                job.filename = str(pathlib.Path(fn).name)
            on_progress()
        elif status == "finished":
            fn = d.get("filename", "")
            if fn:
                job.filename = str(pathlib.Path(fn).name)

    # Build format selector
    if is_audio_only:
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        # With ffmpeg: request exact DASH stream + best audio, merge to mp4
        # Without ffmpeg: request best pre-merged progressive only
        if ffmpeg_path:
            fmt = (
                f"{format_id}+bestaudio[ext=m4a]"
                f"/{format_id}+bestaudio"
                f"/{format_id}"
                f"/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo+bestaudio"
                f"/best[ext=mp4]/best"
            )
        else:
            fmt = "best[ext=mp4]/best"

    # outtmpl: always folder + title.ext — NEVER derive from URL
    outtmpl = str(output_dir / "%(title)s.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    if ffmpeg_path:
        ffmpeg_dir = str(pathlib.Path(ffmpeg_path).parent)
        ydl_opts["ffmpeg_location"] = ffmpeg_dir
        if is_audio_only:
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            ydl_opts["merge_output_format"] = "mp4"

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
    """
    Ensure ffmpeg is available, then kick off a yt-dlp download.
    ffmpeg is downloaded automatically on first use.
    """
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp is not installed")

    # --- Ensure ffmpeg (downloads ~70 MB once, then cached forever) ---
    from ffmpeg_manager import ensure_ffmpeg
    ffmpeg_path = await ensure_ffmpeg(broadcast_fn)
    if not ffmpeg_path:
        raise RuntimeError(
            "ffmpeg could not be downloaded automatically. "
            "Please install ffmpeg manually and add it to PATH."
        )

    job_id = str(uuid.uuid4())
    job = YtDlpJob(job_id, url, output_path, label)
    _ytdlp_jobs[job_id] = job

    loop = asyncio.get_event_loop()

    def on_progress() -> None:
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_progress", job.to_dict()), loop
        )

    def on_done() -> None:
        job.status = "completed"
        job.finished_at = time.time()
        job.percent = 100.0
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_complete", job.to_dict()), loop
        )

    def on_error(msg: str) -> None:
        job.status = "failed"
        job.error = msg
        job.finished_at = time.time()
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_error", {"job_id": job_id, "error": msg}), loop
        )

    def run() -> None:
        job.status = "downloading"
        job.started_at = time.time()
        asyncio.run_coroutine_threadsafe(
            broadcast_fn("job_progress", job.to_dict()), loop
        )
        _download_sync(job, format_id, ffmpeg_path, on_progress, on_done, on_error)

    loop.run_in_executor(_executor, run)
    return job
