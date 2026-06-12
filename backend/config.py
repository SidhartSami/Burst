"""
Burst — Central configuration.

All tunable thresholds live here. No magic numbers elsewhere.
These values serve as defaults and can be overridden at runtime
via the settings API (persisted to burst_settings.json).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
import sys
if getattr(sys, 'frozen', False):
    import os
    APPDATA = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
    SETTINGS_DIR = Path(APPDATA) / "Burst"
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE = SETTINGS_DIR / "burst_settings.json"
else:
    SETTINGS_FILE = Path(__file__).parent / "burst_settings.json"

# ---------------------------------------------------------------------------
# Chunk sizing
# ---------------------------------------------------------------------------
BASE_CHUNK_SIZE: int = 2 * 1024 * 1024          # 2 MB — default chunk for average latency
MIN_CHUNK_SIZE: int = 256 * 1024                 # 256 KB — floor for high-latency interfaces
MAX_CHUNK_SIZE: int = 8 * 1024 * 1024            # 8 MB — ceiling for ultra-low-latency links
CHUNK_IO_SIZE: int = 64 * 1024                   # 64 KB — read buffer per iter_content call

# ---------------------------------------------------------------------------
# Networking / timeouts
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS: int = 60
RETRY_ATTEMPTS: int = 3
RETRY_DELAY_SECONDS: int = 2

# ---------------------------------------------------------------------------
# Bandwidth management
# ---------------------------------------------------------------------------
WEIGHT_REBALANCE_INTERVAL_SECONDS: float = 5.0   # Re-score interface weights every N seconds
MIN_INTERFACE_SPEED_THRESHOLD: float = 0.05       # 50 KB/s expressed in MB/s
SLOW_INTERFACE_GRACE_PERIOD: float = 10.0         # Seconds below threshold before pausing
DISCONNECT_DETECTION_TIMEOUT: float = 3.0         # Seconds of zero progress before "disconnected"

# ---------------------------------------------------------------------------
# Retry routing
# ---------------------------------------------------------------------------
RETRY_SAME_INTERFACE_COOLDOWN: float = 15.0       # Seconds before failed iface is eligible again
MAX_CONSECUTIVE_FAILURES: int = 3                  # Consecutive chunk failures → exclude interface

# ---------------------------------------------------------------------------
# Sliding-window speed measurement
# ---------------------------------------------------------------------------
SPEED_SAMPLE_INTERVAL: float = 0.1                # Minimum seconds between speed samples
SPEED_WINDOW_SECONDS: float = 2.0                 # Rolling window width

# ---------------------------------------------------------------------------
# Speedtest
# ---------------------------------------------------------------------------
SPEEDTEST_URL: str = "https://speed.cloudflare.com/__down?bytes=1000000"
SPEEDTEST_TIMEOUT: int = 20

# ---------------------------------------------------------------------------
# Protocol handler (flag-gated, Feature 3)
# ---------------------------------------------------------------------------
HTTP_HANDLER_SIZE_THRESHOLD: int = 50 * 1024 * 1024   # 50 MB

# ---------------------------------------------------------------------------
# App version
# ---------------------------------------------------------------------------
APP_VERSION = "1.1.2"


# ---------------------------------------------------------------------------
# Runtime settings helpers
# ---------------------------------------------------------------------------
_DEFAULTS: Dict[str, Any] = {
    "BASE_CHUNK_SIZE": BASE_CHUNK_SIZE,
    "MIN_CHUNK_SIZE": MIN_CHUNK_SIZE,
    "MAX_CHUNK_SIZE": MAX_CHUNK_SIZE,
    "CHUNK_IO_SIZE": CHUNK_IO_SIZE,
    "REQUEST_TIMEOUT_SECONDS": REQUEST_TIMEOUT_SECONDS,
    "RETRY_ATTEMPTS": RETRY_ATTEMPTS,
    "RETRY_DELAY_SECONDS": RETRY_DELAY_SECONDS,
    "WEIGHT_REBALANCE_INTERVAL_SECONDS": WEIGHT_REBALANCE_INTERVAL_SECONDS,
    "MIN_INTERFACE_SPEED_THRESHOLD": MIN_INTERFACE_SPEED_THRESHOLD,
    "SLOW_INTERFACE_GRACE_PERIOD": SLOW_INTERFACE_GRACE_PERIOD,
    "DISCONNECT_DETECTION_TIMEOUT": DISCONNECT_DETECTION_TIMEOUT,
    "RETRY_SAME_INTERFACE_COOLDOWN": RETRY_SAME_INTERFACE_COOLDOWN,
    "MAX_CONSECUTIVE_FAILURES": MAX_CONSECUTIVE_FAILURES,
    "SPEED_SAMPLE_INTERVAL": SPEED_SAMPLE_INTERVAL,
    "SPEED_WINDOW_SECONDS": SPEED_WINDOW_SECONDS,
    "SPEEDTEST_URL": SPEEDTEST_URL,
    "SPEEDTEST_TIMEOUT": SPEEDTEST_TIMEOUT,
    "HTTP_HANDLER_SIZE_THRESHOLD": HTTP_HANDLER_SIZE_THRESHOLD,
    "DOWNLOAD_PATH": "C:/Burst-Downloads",
    "THEME_MODE": "system",
    "START_ON_BOOT": True,
    "ONBOARDING_COMPLETE": False,
    "CLIPBOARD_MONITOR_ENABLED": True,
}


def load_settings() -> Dict[str, Any]:
    """Load persisted settings, falling back to defaults for missing keys."""
    settings = dict(_DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r") as fh:
                overrides = json.load(fh)
            settings.update({k: v for k, v in overrides.items() if k in _DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Persist user-supplied overrides and return the merged settings."""
    current = load_settings()
    for key, value in overrides.items():
        if key in _DEFAULTS:
            current[key] = value
    try:
        with SETTINGS_FILE.open("w") as fh:
            json.dump(current, fh, indent=2)
    except OSError:
        pass
    return current

def reset_settings() -> Dict[str, Any]:
    """Delete overrides and restore defaults."""
    if SETTINGS_FILE.exists():
        try:
            SETTINGS_FILE.unlink()
        except OSError:
            pass
    return dict(_DEFAULTS)


def get(key: str) -> Any:
    """Get a single setting value (hot-reads from disk for runtime changes)."""
    return load_settings().get(key, _DEFAULTS.get(key))
