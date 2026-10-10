# macOS only — do not import on Windows or Linux
"""Burst — macOS clipboard monitor using native pbpaste."""
from __future__ import annotations

import asyncio
import subprocess
import time
from typing import Callable, Optional

_last_clipboard_text: Optional[str] = None
_monitor_enabled: bool = False


def get_clipboard_text() -> Optional[str]:
    """Read clipboard text on macOS using built-in pbpaste utility."""
    try:
        output = subprocess.check_output(["pbpaste"], stderr=subprocess.DEVNULL)
        return output.decode("utf-8", errors="replace")
    except Exception:
        return None


def start_clipboard_monitor(
    on_url_detected: Callable[[str], None],
    stop_event: Optional[asyncio.Event] = None,
    poll_interval: float = 1.0,
) -> None:
    """
    Background worker that polls pbpaste every 1 second.
    If the clipboard content starts with magnet: or http and has changed
    since the last check, fires on_url_detected(url).
    """
    global _last_clipboard_text, _monitor_enabled
    _monitor_enabled = True
    _last_clipboard_text = get_clipboard_text()

    while stop_event is None or not stop_event.is_set():
        try:
            current = get_clipboard_text()
            if current and current != _last_clipboard_text:
                _last_clipboard_text = current
                trimmed = current.strip()
                if trimmed.startswith(("magnet:", "http://", "https://")):
                    print(f"[clipboard-mac] URL detected: {trimmed[:80]}", flush=True)
                    on_url_detected(trimmed)
        except Exception:
            pass

        if stop_event is not None:
            for _ in range(int(poll_interval * 10)):
                if stop_event.is_set():
                    break
                time.sleep(0.1)
        else:
            time.sleep(poll_interval)

    _monitor_enabled = False
