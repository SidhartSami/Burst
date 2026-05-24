"""
Burst — ffmpeg auto-downloader.

Downloads a static Win64 ffmpeg binary to %APPDATA%/Burst/ffmpeg/
on first use and caches it for all future sessions.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import urllib.request
import zipfile
from typing import Any, Callable, Optional

# Storage location — lives in AppData so it survives Burst reinstalls
FFMPEG_DIR = pathlib.Path(os.getenv("APPDATA", "C:/Users/Default/AppData/Roaming")) / "Burst" / "ffmpeg"
FFMPEG_BIN = FFMPEG_DIR / "ffmpeg.exe"

# BtbN GPL static build — no runtime DLLs, single .exe inside /bin/
FFMPEG_URL = (
    "https://github.com/BtbN/ffmpeg-builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)


def get_ffmpeg_path() -> Optional[str]:
    """Return path to ffmpeg.exe if already downloaded, otherwise None."""
    if FFMPEG_BIN.exists():
        return str(FFMPEG_BIN)
    return None


def _extract_ffmpeg_sync(zip_path: pathlib.Path) -> None:
    """Extract only ffmpeg.exe from the BtbN zip into FFMPEG_DIR."""
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            # BtbN layout: ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
            if name.endswith("ffmpeg.exe") and "/bin/" in name:
                data = z.read(name)
                FFMPEG_BIN.parent.mkdir(parents=True, exist_ok=True)
                FFMPEG_BIN.write_bytes(data)
                return
    raise RuntimeError("ffmpeg.exe not found inside the downloaded zip")


async def ensure_ffmpeg(
    broadcast_fn: Optional[Callable] = None,
) -> Optional[str]:
    """
    Ensure ffmpeg.exe is available, downloading it if necessary.

    Sends ffmpeg_progress events via broadcast_fn while downloading:
        {"type": "ffmpeg_progress", "data": {"status": "downloading", "percent": 0-100}}
        {"type": "ffmpeg_progress", "data": {"status": "done"}}
        {"type": "ffmpeg_progress", "data": {"status": "error", "error": "..."}}

    Returns the absolute path to ffmpeg.exe, or None on failure.
    """
    if FFMPEG_BIN.exists():
        return str(FFMPEG_BIN)

    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = FFMPEG_DIR / "ffmpeg.zip"

    loop = asyncio.get_event_loop()

    async def _broadcast(status: str, **kwargs: Any) -> None:
        if broadcast_fn:
            try:
                await broadcast_fn("ffmpeg_progress", {"status": status, **kwargs})
            except Exception:
                pass

    await _broadcast("downloading", percent=0)

    # --- Download in a thread so we don't block the event loop ---
    def _download() -> None:
        last_pct = [-1]

        def reporthook(count: int, block_size: int, total_size: int) -> None:
            if total_size <= 0:
                return
            pct = min(int(count * block_size * 100 / total_size), 99)
            if pct != last_pct[0]:
                last_pct[0] = pct
                asyncio.run_coroutine_threadsafe(
                    _broadcast("downloading", percent=pct), loop
                )

        urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook)

    try:
        await asyncio.to_thread(_download)
        await _broadcast("downloading", percent=99)

        # Extract ffmpeg.exe from the zip
        await asyncio.to_thread(_extract_ffmpeg_sync, zip_path)

    except Exception as exc:
        print(f"[ffmpeg] download/extract failed: {exc}", flush=True)
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
        await _broadcast("error", error=str(exc))
        return None
    finally:
        # Always clean up the zip to save ~70 MB
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass

    if FFMPEG_BIN.exists():
        await _broadcast("done", percent=100)
        return str(FFMPEG_BIN)

    await _broadcast("error", error="ffmpeg.exe not found after extraction")
    return None
