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
import os
APPDATA = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
SETTINGS_DIR = Path(APPDATA) / "Burst"
SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = SETTINGS_DIR / "burst_settings.json"

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
RETRY_BACKOFF_BASE: float = 1.0                   # Base delay in seconds for exponential backoff
RETRY_BACKOFF_MAX: float = 10.0                   # Maximum backoff delay in seconds
RETRY_JITTER_MAX: float = 0.5                     # Random jitter range (0 to N seconds)
STALL_TIMEOUT_SECONDS: float = 10.0               # Seconds without received bytes before considered stalled


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
EXCLUDED_INTERFACE_COOLDOWN: float = 60.0          # Seconds an interface remains excluded before re-probing
SINGLE_INTERFACE_RECONNECT_TIMEOUT: float = 180.0  # Bounded wait in seconds (3 min) for single interface recovery with visible 'waiting' state
ENABLE_CHUNK_FSYNC: bool = True                    # Per-chunk fsync before atomic commit (durability vs throughput)
# ponytail: warm-up and tail tapering add chunk-split overhead with no measured win on loopback;
# enable only when a benchmark on real asymmetric interfaces shows improvement over uniform.
ENABLE_ADAPTIVE_WARMUP_TAIL: bool = False          # Warm-up + tail tapering for multi-interface downloads
# ponytail: tail racing launches speculative duplicate chunk on idle interface; default off until benchmarked
ENABLE_TAIL_RACING: bool = False                   # Speculative tail racing for straggler chunks

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
    "RETRY_BACKOFF_BASE": RETRY_BACKOFF_BASE,
    "RETRY_BACKOFF_MAX": RETRY_BACKOFF_MAX,
    "RETRY_JITTER_MAX": RETRY_JITTER_MAX,
    "STALL_TIMEOUT_SECONDS": STALL_TIMEOUT_SECONDS,
    "WEIGHT_REBALANCE_INTERVAL_SECONDS": WEIGHT_REBALANCE_INTERVAL_SECONDS,
    "MIN_INTERFACE_SPEED_THRESHOLD": MIN_INTERFACE_SPEED_THRESHOLD,
    "SLOW_INTERFACE_GRACE_PERIOD": SLOW_INTERFACE_GRACE_PERIOD,
    "DISCONNECT_DETECTION_TIMEOUT": DISCONNECT_DETECTION_TIMEOUT,
    "RETRY_SAME_INTERFACE_COOLDOWN": RETRY_SAME_INTERFACE_COOLDOWN,
    "MAX_CONSECUTIVE_FAILURES": MAX_CONSECUTIVE_FAILURES,
    "EXCLUDED_INTERFACE_COOLDOWN": EXCLUDED_INTERFACE_COOLDOWN,
    "SINGLE_INTERFACE_RECONNECT_TIMEOUT": SINGLE_INTERFACE_RECONNECT_TIMEOUT,
    "ENABLE_CHUNK_FSYNC": ENABLE_CHUNK_FSYNC,
    "ENABLE_ADAPTIVE_WARMUP_TAIL": ENABLE_ADAPTIVE_WARMUP_TAIL,
    "ENABLE_TAIL_RACING": ENABLE_TAIL_RACING,
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


def get(key: str, default: Any = None) -> Any:
    """Get a single setting value (hot-reads from disk for runtime changes)."""
    val = load_settings().get(key, _DEFAULTS.get(key))
    return val if val is not None else default
