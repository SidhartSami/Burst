"""Burst — Clipboard monitor for auto-detecting download URLs."""
from __future__ import annotations

import asyncio
import ctypes
import re
import threading
import time
from typing import Callable, Optional

URL_RE = re.compile(r"(?:https?://|magnet:\?)[^\s\"'<>]+", re.IGNORECASE)

# Module-level state shared with main.py
_last_clipboard_text: Optional[str] = None
_monitor_enabled = False

HAS_WIN32 = False
try:
    import win32clipboard
    HAS_WIN32 = True
except ImportError:
    pass

print("[clipboard] using ctypes reader" if not HAS_WIN32 else "[clipboard] using win32clipboard", flush=True)


def get_clipboard_text() -> Optional[str]:
    """
    Read current Windows clipboard using ctypes (no external packages).
    Falls back to win32clipboard if available.
    Returns None on failure.
    """
    # Try ctypes first — works on any Windows Python without post-install steps
    text = _get_clipboard_text_ctypes()
    if text is not None:
        return text

    # Fall back to win32clipboard if available (optional enhancement)
    try:
        import win32clipboard
        return _get_clipboard_text_win32(win32clipboard)
    except ImportError:
        pass

    return None


def _get_clipboard_text_ctypes() -> Optional[str]:
    """Read clipboard via ctypes (Windows stdlib approach)."""
    from ctypes import wintypes
    
    # Configure signatures for 64-bit safety
    try:
        ctypes.windll.user32.OpenClipboard.argtypes = [wintypes.HWND]
        ctypes.windll.user32.OpenClipboard.restype = wintypes.BOOL
        
        ctypes.windll.user32.GetClipboardData.argtypes = [wintypes.UINT]
        ctypes.windll.user32.GetClipboardData.restype = wintypes.HANDLE
        
        ctypes.windll.kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        ctypes.windll.kernel32.GlobalLock.restype = ctypes.c_void_p
        
        ctypes.windll.kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        ctypes.windll.kernel32.GlobalUnlock.restype = wintypes.BOOL
        
        ctypes.windll.user32.CloseClipboard.argtypes = []
        ctypes.windll.user32.CloseClipboard.restype = wintypes.BOOL
    except Exception:
        # Fallback if wintypes/windll setup fails
        pass

    CF_UNICODETEXT = 13
    if not ctypes.windll.user32.OpenClipboard(0):
        return None
    try:
        handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = ctypes.windll.kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(handle)
    finally:
        ctypes.windll.user32.CloseClipboard()


def _get_clipboard_text_win32(win32clipboard) -> Optional[str]:
    """Read clipboard via win32clipboard (optional enhancement)."""
    try:
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return str(data) if data else None
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
            return data.decode("utf-8", errors="replace") if data else None
    except Exception:
        pass
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    return None


def extract_url(text: str) -> Optional[str]:
    """Extract first HTTP/HTTPS URL or magnet link from text, or None."""
    match = URL_RE.search(text)
    return match.group(0) if match else None


def start_monitor(
    on_url_detected: Callable[[str], None],
    stop_event: asyncio.Event,
    poll_interval: float = 2.0,
) -> None:
    """
    Background thread that polls the clipboard every `poll_interval` seconds.
    Calls `on_url_detected(url)` when a new URL is found.
    Runs until stop_event is set.
    """
    global _last_clipboard_text, _monitor_enabled
    _monitor_enabled = True
    _last_clipboard_text = get_clipboard_text()

    while not stop_event.is_set():
        try:
            current = get_clipboard_text()
            if current and current != _last_clipboard_text:
                _last_clipboard_text = current
                url = extract_url(current)
                if url:
                    print(f"[clipboard] clipboard changed: {current[:80]}", flush=True)
                    on_url_detected(url)
        except Exception:
            pass

        # Poll with short sleeps to respect stop_event quickly
        for _ in range(int(poll_interval * 10)):
            if stop_event.is_set():
                break
            time.sleep(0.1)

    _monitor_enabled = False