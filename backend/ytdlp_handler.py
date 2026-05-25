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
            has_audio = f.get("acodec", "none") != "none"
            result.append({
                "id": f.get("format_id", lbl),
                "label": lbl,
                "ext": f.get("ext", "mp4"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
                "has_audio": has_audio,
                "stream_url": f.get("url"),
                "audio_stream_url": audio_best.get("url") if (not has_audio and audio_best) else None,
                "audio_ext": audio_best.get("ext", "m4a") if (not has_audio and audio_best) else None,
                "progressive_id": height_safe[lbl].get("format_id") if lbl in height_safe else None,
                "progressive_url": height_safe[lbl].get("url") if lbl in height_safe else None,
            })

    # Any non-standard heights
    for lbl, f in sorted(
        height_best.items(),
        key=lambda x: -(int(x[0].rstrip("p")) if x[0].rstrip("p").isdigit() else 0),
    ):
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            has_audio = f.get("acodec", "none") != "none"
            result.append({
                "id": f.get("format_id", lbl),
                "label": lbl,
                "ext": f.get("ext", "mp4"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
                "has_audio": has_audio,
                "stream_url": f.get("url"),
                "audio_stream_url": audio_best.get("url") if (not has_audio and audio_best) else None,
                "audio_ext": audio_best.get("ext", "m4a") if (not has_audio and audio_best) else None,
                "progressive_id": height_safe[lbl].get("format_id") if lbl in height_safe else None,
                "progressive_url": height_safe[lbl].get("url") if lbl in height_safe else None,
            })

    if audio_best:
        result.append({
            "id": audio_best.get("format_id", "bestaudio"),
            "label": "Audio only",
            "ext": audio_best.get("ext", "m4a"),
            "filesize": audio_best.get("filesize") or audio_best.get("filesize_approx"),
            "tbr": audio_best.get("tbr"),
            "has_audio": True,
            "stream_url": audio_best.get("url"),
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

        # Bonding fields
        self.sub_jobs: List[str] = []
        self.temp_files: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        iface_dict: Dict[str, Any] = {}
        
        # Merge sub-jobs interface info if active
        if self.sub_jobs:
            try:
                from main import manager
                for sub_id in self.sub_jobs:
                    sub_job = manager.get_job(sub_id)
                    if sub_job:
                        sub_dict = sub_job.to_dict()
                        for ip, info in sub_dict.get("interfaces", {}).items():
                            if ip not in iface_dict:
                                iface_dict[ip] = dict(info)
                            else:
                                iface_dict[ip]["speed_mb_s"] = iface_dict[ip].get("speed_mb_s", 0.0) + info.get("speed_mb_s", 0.0)
                                iface_dict[ip]["downloaded"] = iface_dict[ip].get("downloaded", 0) + info.get("downloaded", 0)
            except Exception as e:
                print(f"[yt-dlp job] Error merging sub-job interfaces: {e}")
                
        if not iface_dict:
            # Fallback format for single connection
            total_speed = self.speed_bytes / (1024 * 1024) if self.speed_bytes else 0.0
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


def _sanitize_filename(name: str) -> str:
    # Remove chars that are illegal in Windows filenames
    for c in '<>:"/\\|?*':
        name = name.replace(c, '')
    return name.strip()


async def monitor_bonded_download(
    job: YtDlpJob,
    video_job_id: str,
    audio_job_id: Optional[str],
    final_output_path: pathlib.Path,
    video_temp_path: pathlib.Path,
    audio_temp_path: Optional[pathlib.Path],
    ffmpeg_path: str,
    broadcast_fn: Callable,
):
    from main import manager
    
    # Store sub-job IDs on the parent job
    job.sub_jobs = [video_job_id]
    if audio_job_id:
        job.sub_jobs.append(audio_job_id)
        
    job.temp_files = [str(video_temp_path)]
    if audio_temp_path:
        job.temp_files.append(str(audio_temp_path))
        
    job.status = "downloading"
    job.started_at = time.time()
    
    # Send initial progress
    await broadcast_fn("job_progress", job.to_dict())
    
    try:
        while True:
            # 1. Check parent job cancellation
            if job.is_cancelled:
                # Cancel all sub-jobs
                for sub_id in job.sub_jobs:
                    try:
                        await manager.cancel_job(sub_id)
                    except Exception as e:
                        print(f"[yt-dlp bonded] Failed to cancel sub-job {sub_id}: {e}")
                
                # Cleanup temp files
                for tf in job.temp_files:
                    try:
                        pathlib.Path(tf).unlink(missing_ok=True)
                    except:
                        pass
                
                job.status = "failed"
                job.error = "Cancelled by user"
                job.finished_at = time.time()
                await broadcast_fn("job_error", {"job_id": job.job_id, "error": "Cancelled by user"})
                return
                
            # Get sub-jobs
            vjob = manager.get_job(video_job_id)
            ajob = manager.get_job(audio_job_id) if audio_job_id else None
            
            # If any sub-job is None, we can't monitor properly
            if not vjob or (audio_job_id and not ajob):
                await asyncio.sleep(0.5)
                continue
                
            # Check for failures
            if vjob.status == "failed" or (ajob and ajob.status == "failed"):
                err_msg = vjob.error or (ajob.error if ajob else "") or "Sub-job download failed"
                
                # Cancel the other sub-job if still running
                for sub_id in job.sub_jobs:
                    try:
                        await manager.cancel_job(sub_id)
                    except:
                        pass
                        
                # Cleanup temp files
                for tf in job.temp_files:
                    try:
                        pathlib.Path(tf).unlink(missing_ok=True)
                    except:
                        pass
                    
                job.status = "failed"
                job.error = err_msg
                job.finished_at = time.time()
                await broadcast_fn("job_error", {"job_id": job.job_id, "error": err_msg})
                return
                
            # Calculate percentages and speeds from sub-jobs
            vjob_pct = (vjob.total_downloaded / vjob.expected_size * 100.0) if vjob.expected_size else 0.0
            ajob_pct = (ajob.total_downloaded / ajob.expected_size * 100.0) if (ajob and ajob.expected_size) else 0.0

            v_speed = sum(i.get("speed_mb_s", 0.0) for i in vjob.to_dict().get("interfaces", {}).values()) * 1024 * 1024
            a_speed = sum(i.get("speed_mb_s", 0.0) for i in ajob.to_dict().get("interfaces", {}).values()) * 1024 * 1024 if ajob else 0

            # Aggregate download statistics
            if ajob:
                # DASH format with separate video and audio streams
                job.total_downloaded = vjob.total_downloaded + ajob.total_downloaded
                job.expected_size = vjob.expected_size + ajob.expected_size
                
                # percent = average of stream percentages
                job.percent = min(99.9, (vjob_pct + ajob_pct) / 2.0)
                
                # Sum the active speed across interfaces
                job.speed_bytes = v_speed + a_speed
                
                # Combined ETA
                eta_v = (vjob.expected_size - vjob.total_downloaded) / v_speed if v_speed else 0
                eta_a = (ajob.expected_size - ajob.total_downloaded) / a_speed if a_speed else 0
                job.eta = max(eta_v, eta_a) if (eta_v or eta_a) else None
            else:
                # Combined format - single stream
                job.total_downloaded = vjob.total_downloaded
                job.expected_size = vjob.expected_size
                job.percent = vjob_pct
                job.speed_bytes = v_speed
                eta_val = (vjob.expected_size - vjob.total_downloaded) / v_speed if v_speed else 0
                job.eta = eta_val if eta_val else None
                
            # Update filename
            job.filename = final_output_path.name
            
            # Broadcast progress
            await broadcast_fn("job_progress", job.to_dict())
            
            # Check for completion
            v_done = vjob.status == "completed"
            a_done = ajob.status == "completed" if ajob else True
            
            if v_done and a_done:
                # Transition to merging!
                job.status = "merging"
                job.percent = 99.9
                await broadcast_fn("job_progress", job.to_dict())
                
                # Perform the merge or copy in a background thread
                try:
                    if ajob:
                        # DASH format - needs ffmpeg merge!
                        import subprocess
                        ffmpeg_cmd = [
                            ffmpeg_path,
                            "-i", str(video_temp_path),
                            "-i", str(audio_temp_path),
                            "-c", "copy",
                            str(final_output_path),
                            "-y"
                        ]
                        print(f"[yt-dlp bonded] Merging streams: {ffmpeg_cmd}")
                        await asyncio.to_thread(subprocess.run, ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        # Single combined format - just rename/move!
                        print(f"[yt-dlp bonded] Renaming combined stream: {video_temp_path} -> {final_output_path}")
                        
                        def do_rename():
                            # Remove output if somehow exists (should be handled by path collision)
                            if final_output_path.exists():
                                final_output_path.unlink()
                            video_temp_path.rename(final_output_path)
                            
                        await asyncio.to_thread(do_rename)
                        
                    # Clean up the completed sub-jobs from manager so they don't clutter regular history
                    vjob.filename = "BURST_INTERNAL_CHECK"
                    vjob.url = "BURST_INTERNAL_CHECK"
                    if ajob:
                        ajob.filename = "BURST_INTERNAL_CHECK"
                        ajob.url = "BURST_INTERNAL_CHECK"
                        
                    # Cleanup temp files
                    for tf in job.temp_files:
                        try:
                            pathlib.Path(tf).unlink(missing_ok=True)
                        except:
                            pass
                            
                    # Successful completion!
                    job.status = "completed"
                    job.percent = 100.0
                    job.finished_at = time.time()
                    await broadcast_fn("job_complete", job.to_dict())
                    return
                    
                except Exception as merge_err:
                    print(f"[yt-dlp bonded] Merge/Rename error: {merge_err}")
                    # Cleanup temp files
                    for tf in job.temp_files:
                        try:
                            pathlib.Path(tf).unlink(missing_ok=True)
                        except:
                            pass
                        
                    job.status = "failed"
                    job.error = f"Merge failed: {merge_err}"
                    job.finished_at = time.time()
                    await broadcast_fn("job_error", {"job_id": job.job_id, "error": job.error})
                    return
            
            await asyncio.sleep(0.5)
            
    except Exception as loop_err:
        print(f"[yt-dlp bonded] coordinator loop error: {loop_err}")
        job.status = "failed"
        job.error = str(loop_err)
        job.finished_at = time.time()
        await broadcast_fn("job_error", {"job_id": job.job_id, "error": job.error})


async def start_ytdlp_download(
    url: str,
    format_id: str,
    output_path: str,
    label: str,
    broadcast_fn: Callable,
    interface_ips: List[str] = [],
    streamable: bool = False,
) -> YtDlpJob:
    """
    Ensure ffmpeg is available, then kick off a yt-dlp download.
    It attempts to extract direct CDN stream URLs and run them via Burst's chunked multi-interface engine.
    If it fails, it falls back to native single-connection yt-dlp.
    """
    if not YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp is not installed")

    # Ensure ffmpeg
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

    # output_path is ALWAYS a directory for yt-dlp downloads
    output_dir = pathlib.Path(output_path)
    if output_dir.suffix and not output_dir.is_dir():
        output_dir = output_dir.parent
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        job.status = "failed"
        job.error = f"Cannot create output folder: {e}"
        await broadcast_fn("job_error", {"job_id": job_id, "error": job.error})
        return job

    # 1. Fetch direct CDN URLs
    info = None
    try:
        info = await fetch_info(url)
    except Exception as e:
        print(f"[yt-dlp bonded] Failed to fetch info: {e}")

    use_custom_bonding = False
    video_stream_url = None
    audio_stream_url = None
    video_ext = "mp4"
    audio_ext = "m4a"
    title = "video"

    if info and info.get("supported"):
        title = info.get("title", "video")
        chosen_fmt = None
        for f in info.get("formats", []):
            if str(f.get("id")) == str(format_id):
                chosen_fmt = f
                break

        if chosen_fmt and chosen_fmt.get("stream_url"):
            video_stream_url = chosen_fmt["stream_url"]
            video_ext = chosen_fmt.get("ext", "mp4")

            if not chosen_fmt.get("has_audio"):
                audio_fmt = None
                for f in info.get("formats", []):
                    if f.get("label") == "Audio only" and f.get("stream_url"):
                        audio_fmt = f
                        break
                if audio_fmt:
                    audio_stream_url = audio_fmt["stream_url"]
                    audio_ext = audio_fmt.get("ext", "m4a")
                    use_custom_bonding = True
            else:
                use_custom_bonding = True

    if streamable:
        use_custom_bonding = False

    if use_custom_bonding:
        print(f"[yt-dlp bonded] Starting bonded download for '{title}' (format {format_id})")
        
        # Build interface list
        from main import _interfaces_by_ip
        all_ifaces = _interfaces_by_ip()
        selected = [all_ifaces[ip] for ip in interface_ips if ip in all_ifaces]
        if not selected:
            from interfaces import get_active_interfaces_dict
            selected = get_active_interfaces_dict()

        sanitized_title = _sanitize_filename(title)
        
        is_audio_only = label == "Audio only"
        final_ext = "mp3" if is_audio_only else video_ext
        final_filename = f"{sanitized_title}.{final_ext}"
        
        final_file_path = output_dir / final_filename
        counter = 1
        while final_file_path.exists():
            final_file_path = output_dir / f"{sanitized_title}({counter}).{final_ext}"
            counter += 1

        # Spawn sub-jobs
        from main import manager
        
        video_temp_path = output_dir / f".tmp_{job_id}_video.{video_ext}"
        video_sub_job = await manager.create_job(video_stream_url, str(video_temp_path), selected)
        
        audio_sub_job = None
        audio_temp_path = None
        if audio_stream_url:
            audio_temp_path = output_dir / f".tmp_{job_id}_audio.{audio_ext}"
            audio_sub_job = await manager.create_job(audio_stream_url, str(audio_temp_path), selected)

        # Start background task to monitor sub-jobs
        asyncio.create_task(
            monitor_bonded_download(
                job=job,
                video_job_id=video_sub_job.job_id,
                audio_job_id=audio_sub_job.job_id if audio_sub_job else None,
                final_output_path=final_file_path,
                video_temp_path=video_temp_path,
                audio_temp_path=audio_temp_path,
                ffmpeg_path=ffmpeg_path,
                broadcast_fn=broadcast_fn
            )
        )
        return job

    # SILENT FALLBACK to classic yt-dlp native downloader
    print(f"[yt-dlp bonded] WARNING: Cannot extract direct CDN URL. Falling back to native single-connection yt-dlp downloader.")
    
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
