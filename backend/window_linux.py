# Linux only — do not import on Windows
"""Burst — Linux window management and desktop integration using pywebview."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Optional


class LinuxWindowAPI:
    """
    Cross-platform window API exposed to the frontend via pywebview's js_api.
    Uses only built-in pywebview native methods (show, hide, minimize, maximize)
    without direct GTK or platform-specific calls.
    """
    def __init__(self, window: Optional[Any] = None):
        self._window = window

    def set_window(self, window: Any) -> None:
        self._window = window

    def close(self) -> None:
        """Hide window to system tray instead of destroying."""
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


def detect_linux_theme() -> str:
    """
    Detect system dark/light theme on Linux via gsettings (GNOME/GTK).
    Returns 'dark' or 'light'.
    """
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        output = result.stdout.lower()
        if "prefer-dark" in output:
            return "dark"
        if "prefer-light" in output:
            return "light"
    except Exception:
        pass
    # Fallback to dark theme (Burst default design)
    return "dark"


def play_sound_linux(wav_path: str) -> None:
    """
    Play notification/completion sound on Linux using pygame.mixer.
    Falls back gracefully if sound hardware or pygame is unavailable.
    """
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        if os.path.exists(wav_path):
            sound = pygame.mixer.Sound(wav_path)
            sound.play()
    except Exception as e:
        print(f"[AUDIO-LINUX] Playback skipped: {e}", flush=True)
