# macOS only — do not import on Windows or Linux
"""Burst — macOS window management and desktop integration using pywebview."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Optional


class MacWindowAPI:
    """
    Cross-platform window API exposed to the frontend via pywebview's js_api.
    Uses only built-in pywebview native methods (show, hide, minimize, maximize)
    without direct AppKit or platform-specific calls.
    """
    def __init__(self, window: Optional[Any] = None):
        self._window = window

    def set_window(self, window: Any) -> None:
        self._window = window

    def close(self) -> None:
        """Hide window to system tray / dock instead of destroying."""
        if self._window:
            self._window.hide()

    def minimize(self) -> None:
        """Minimize window using pywebview native method."""
        if self._window:
            self._window.minimize()

    def maximize(self) -> None:
        """Toggle fullscreen or maximize using pywebview native method."""
        if self._window:
            if getattr(self._window, "fullscreen", False):
                self._window.toggle_fullscreen()
            else:
                try:
                    self._window.maximize()
                except Exception:
                    self._window.toggle_fullscreen()

    def show(self) -> None:
        """Bring window to front using pywebview native method."""
        if self._window:
            self._window.show()

    def hide(self) -> None:
        """Hide window using pywebview native method."""
        if self._window:
            self._window.hide()

    def restore(self) -> None:
        """Restore window using pywebview native method."""
        if self._window:
            try:
                self._window.restore()
            except Exception:
                self._window.show()


def detect_mac_theme() -> str:
    """
    Detect system dark/light theme on macOS via `defaults read -g AppleInterfaceStyle`.
    Returns 'dark' if dark mode is active, or 'light' (command fails/exits non-zero when light).
    """
    try:
        result = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        if result.returncode == 0 and "dark" in result.stdout.lower():
            return "dark"
        return "light"
    except Exception:
        pass
    # Fallback to dark theme (Burst default design)
    return "dark"


def play_sound_mac(wav_path: str) -> None:
    """
    Play notification/completion sound on macOS using built-in afplay command.
    Zero third-party dependencies required.
    """
    try:
        if os.path.exists(wav_path):
            subprocess.Popen(
                ["afplay", wav_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        print(f"[AUDIO-MAC] Playback skipped: {e}", flush=True)
