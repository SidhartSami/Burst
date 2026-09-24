"""
Burst — Download Engine.

Implements weighted bandwidth scoring, slow-interface auto-drop,
orphaned chunk reassignment, latency-aware chunk sizing,
and cross-interface retry routing.
"""
from __future__ import annotations

import asyncio
import errno
import os
import random
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import requests
import urllib3
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from urllib3.poolmanager import PoolManager

import config
from merger import cleanup_chunks, merge_chunks

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class StalledDownloadError(Exception):
    """Raised when an active download connection receives no bytes within the stall timeout."""
    pass


class OriginResourceModifiedError(ValueError):
    """Raised when the remote resource has been modified mid-flight (ETag/Last-Modified mismatch or 200 OK to Range)."""
    pass


class InsufficientDiskSpaceError(OSError):
    """Raised when destination filesystem lacks required disk space for download and merge."""
    def __init__(self, message: str, required_bytes: int = 0, available_bytes: int = 0):
        super().__init__(errno.ENOSPC, message)
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort flush of directory entry to disk to ensure rename durability across power loss (POSIX)."""
    if hasattr(os, "O_DIRECTORY"):
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


def check_disk_space_preflight(
    output_path: Path,
    expected_size: int,
    chunk_files: Optional[Dict[int, Path]] = None,
    supports_ranges: bool = True
) -> None:
    """Preflight check on destination volume before starting or resuming downloads."""
    if expected_size <= 0:
        return
    out_dir = output_path.parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(out_dir)
        free_bytes = usage.free
    except OSError:
        return  # virtual fs or path without disk_usage support, proceed

    # Resume-aware accounting: sum size of existing .part chunks
    existing_bytes = 0
    if supports_ranges and chunk_files:
        for p in chunk_files.values():
            if p.exists():
                try:
                    existing_bytes += p.stat().st_size
                except OSError:
                    pass
    elif output_path.exists():
        try:
            existing_bytes = output_path.stat().st_size
        except OSError:
            pass

    remaining_download = max(0, expected_size - existing_bytes)
    # Merge requires space for final file alongside existing chunks
    required_bytes = remaining_download + (expected_size if supports_ranges else 0)

    if free_bytes < required_bytes:
        req_mb = required_bytes / (1024 * 1024)
        avail_mb = free_bytes / (1024 * 1024)
        raise InsufficientDiskSpaceError(
            f"Insufficient disk space on {out_dir}: required {req_mb:.1f} MB (including merge buffer), available {avail_mb:.1f} MB",
            required_bytes=required_bytes,
            available_bytes=free_bytes,
        )


def redact_url(url: Optional[str]) -> str:
    """Redact query strings and sensitive credentials from URLs in log outputs."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[1]
        query = "[REDACTED]" if parsed.query else ""
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except Exception:
        return url


def normalize_etag(etag: Optional[str]) -> Optional[str]:
    """Normalize ETag string, preserving weak indicator (W/) if present."""
    if not etag:
        return None
    val = etag.strip()
    if not val:
        return None
    if val.startswith(("W/", "w/")):
        inner = val[2:].strip().strip('"')
        return f'W/"{inner}"'
    return val.strip('"')


def is_strong_etag(etag: Optional[str]) -> bool:
    """Return True if etag is present and is a strong entity tag per RFC 9110 Section 8.8.3.

    Weak entity tags begin with W/ or w/ and MUST NOT be used in If-Range.
    """
    if not etag:
        return False
    val = etag.strip()
    return bool(val) and not (val.startswith("W/") or val.startswith("w/"))


def calculate_backoff(attempt: int) -> float:
    """Calculate exponential backoff with jitter."""
    base = float(config.get("RETRY_BACKOFF_BASE") or 1.0)
    max_delay = float(config.get("RETRY_BACKOFF_MAX") or 10.0)
    jitter = float(config.get("RETRY_JITTER_MAX") or 0.5)
    delay = min(base * (2 ** max(0, attempt - 1)), max_delay)
    delay += random.uniform(0, jitter)
    return delay


def is_non_retryable_error(exc: Exception) -> bool:
    """Identify permanent non-retryable errors that should fail immediately."""
    if isinstance(exc, (FileNotFoundError, PermissionError, OriginResourceModifiedError, InsufficientDiskSpaceError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        if exc.response.status_code in (400, 401, 403, 404, 405, 410, 416):
            return True
    if isinstance(exc, ValueError):
        msg = str(exc).lower()
        if (
            "range download rejected" in msg
            or "expected 206" in msg
            or "content-range mismatch" in msg
            or "too many bytes" in msg
            or "remote resource modified mid-flight" in msg
            or "protocol downgrade" in msg
            or "insufficient disk space" in msg
        ):
            return True
    return False


class ChunkStatus:
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"  # ponytail: ceiling: in-memory queue pops chunk directly into DOWNLOADING without intermediate ASSIGNED phase; upgrade: explicit ASSIGNED state if worker reservation is split from download start
    DOWNLOADING = "DOWNLOADING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class Chunk:
    chunk_id: int
    start: int
    end: int
    status: str = ChunkStatus.PENDING
    attempts: int = 0
    assigned_interface: Optional[str] = None
    created_at: float = field(default_factory=time.time)  # ponytail: ceiling: in-memory field only; upgrade: serialize to burst_active_jobs.json if chunk history needs persistence across restarts
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    last_error: Optional[str] = None
    _racing_claimed: bool = field(default=False, repr=False)
    _racing_cancel: Any = field(default=None, repr=False)

    @property
    def expected_bytes(self) -> int:
        return max(0, self.end - self.start + 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "start": self.start,
            "end": self.end,
            "status": self.status,
            "attempts": self.attempts,
            "assigned_interface": self.assigned_interface,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "last_error": self.last_error,
        }

    def __iter__(self):
        # Backward-compatibility with tuple unpacking: chunk_idx, start, end = item
        yield self.chunk_id
        yield self.start
        yield self.end


@dataclass
class URLAnalysis:
    url: str
    final_url: str
    content_length: int
    supports_ranges: bool
    content_type: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    range_error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "content_length": self.content_length,
            "supports_ranges": self.supports_ranges,
            "content_type": self.content_type,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "range_error_reason": self.range_error_reason,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


@dataclass
class RetryEvent:
    """A single retry/reassignment event for the activity log."""
    timestamp: float
    chunk_index: int
    from_interface: str
    to_interface: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "chunk_index": self.chunk_index,
            "from_interface": self.from_interface,
            "to_interface": self.to_interface,
            "reason": self.reason,
        }


@dataclass
class InterfaceProgress:
    name: str
    ip_address: str
    chunk_start: int
    chunk_end: int
    downloaded: int = 0
    status: str = "pending"          # pending | downloading | completed | paused_slow | disconnected | excluded | cancelled
    current_chunk_idx: Optional[int] = None
    _bytes_at_start_of_chunk: int = 0
    speed_mb_s: float = 0.0
    error: Optional[str] = None
    weight: float = 0.0              # 0.0–1.0 share of total bandwidth
    weight_percent: int = 0          # 0–100 for UI display
    latency_ms: float = 0.0
    chunks_completed: int = 0
    consecutive_failures: int = 0
    ewma_speed_mb_s: float = 0.0
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    retry_count: int = 0
    stall_count: int = 0
    last_success_time: Optional[float] = None
    active_workers: int = 0
    _speed_samples: Any = field(default_factory=list, repr=False)
    _slow_since: Optional[float] = field(default=None, repr=False)
    _last_progress_time: float = field(default_factory=time.time, repr=False)
    _cooldown_until: float = field(default=0.0, repr=False)
    _recent_outcomes: List[str] = field(default_factory=list, repr=False)

    def record_outcome(self, outcome: str) -> None:
        """Record request outcome in rolling window (last 20 requests) for decaying rate-based health."""
        self._recent_outcomes.append(outcome)
        if len(self._recent_outcomes) > 20:
            self._recent_outcomes.pop(0)

    @property
    def health(self) -> str:
        max_failures = config.get("MAX_CONSECUTIVE_FAILURES") or 3
        now = time.time()
        if (self.status == "excluded" or self.consecutive_failures >= max_failures) and (self._cooldown_until == 0.0 or now < self._cooldown_until):
            return "excluded"
        if (
            self.consecutive_failures > 0
            or self._cooldown_until > now
            or self.status in ("paused_slow", "disconnected")
        ):
            return "degraded"
        # Rolling window rate evaluation (last 10 requests, min 4):
        window = self._recent_outcomes[-10:] if self._recent_outcomes else []
        if len(window) >= 4:
            fails = sum(1 for o in window if o == "failure")
            stalls = sum(1 for o in window if o == "stall")
            if (fails / len(window) >= 0.5) or (stalls / len(window) >= 0.25):
                return "degraded"
        elif self.request_count >= 4:
            # Fallback for legacy / pre-populated counters
            failure_rate = self.failure_count / self.request_count
            stall_rate = self.stall_count / self.request_count
            if failure_rate >= 0.5 or stall_rate >= 0.25:
                return "degraded"
        return "healthy"


@dataclass
class DownloadJob:
    job_id: str
    url: str
    output_path: str
    expected_size: int = 0
    supports_ranges: bool = False
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    progress: Dict[str, InterfaceProgress] = field(default_factory=dict)
    total_downloaded: int = 0
    error: Optional[str] = None
    is_cancelled: bool = False
    retry_events: List[RetryEvent] = field(default_factory=list)
    bandwidth_limits: Dict[str, float] = field(default_factory=dict)
    boosted: bool = False
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    final_url: Optional[str] = None
    range_error_reason: Optional[str] = None
    resume_confidence: str = "high"
    chunks: Dict[int, Chunk] = field(default_factory=dict)
    _queue: Any = field(default=None, repr=False)
    _chunk_files: Any = field(default=None, repr=False)
    _workers: Any = field(default_factory=dict, repr=False)   # ip -> Task (or list of Tasks when boosted)
    _chunk_failures: Any = field(default_factory=dict, repr=False)
    _total_chunks: int = field(default=0, repr=False)
    _ranges: List[Tuple[int, int, int]] = field(default_factory=list, repr=False)
    _completion_event: Any = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        iface_dict = {}
        for k, v in self.progress.items():
            active_cnt = sum(
                1 for w_key, t in self._workers.items()
                if (w_key == k or w_key.startswith(f"{k}_")) and not t.done()
            )
            d = {
                "name": v.name, "ip_address": v.ip_address,
                "chunk_start": v.chunk_start, "chunk_end": v.chunk_end,
                "bytes": v.downloaded,
                "downloaded": v.downloaded, "status": v.status,
                "health": v.health,
                "speed_mb_s": v.speed_mb_s,
                "ewma_speed_mb_s": round(v.ewma_speed_mb_s, 2),
                "request_count": v.request_count,
                "success_count": v.success_count,
                "failure_count": v.failure_count,
                "retry_count": v.retry_count,
                "stall_count": v.stall_count,
                "last_success_time": v.last_success_time,
                "active_workers": active_cnt,
                "error": v.error,
                "weight": v.weight, "weight_percent": v.weight_percent,
                "latency_ms": v.latency_ms, "chunks_completed": v.chunks_completed,
                "consecutive_failures": v.consecutive_failures,
            }
            iface_dict[k] = d
        return {
            "job_id": self.job_id, "url": self.url,
            "output_path": self.output_path,
            "expected_size": self.expected_size,
            "supports_ranges": self.supports_ranges,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_downloaded": self.total_downloaded,
            "error": self.error, "is_cancelled": self.is_cancelled,
            "interfaces": iface_dict,
            "retry_events": [e.to_dict() for e in self.retry_events[-20:]],
            "_ranges": self._ranges,
            "bandwidth_limits": self.bandwidth_limits,
            "boosted": self.boosted,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "final_url": self.final_url,
            "range_error_reason": self.range_error_reason,
            "resume_confidence": self.resume_confidence,
            "is_resumable": self.is_resumable,
            "chunks": {k: c.to_dict() for k, c in self.chunks.items()},
        }

    @property
    def is_resumable(self) -> bool:
        """Structured resumable state: True if job can be resumed from existing partial state."""
        return (
            self.supports_ranges
            and self.status in ("paused", "waiting", "waiting_reconnect", "failed")
            and not self.is_cancelled
            and (self.status != "failed" or "resumable" in (self.error or "").lower())
        )


def _build_connector(local_ip: str) -> aiohttp.TCPConnector:
    family = socket.AF_INET6 if ":" in local_ip else socket.AF_INET
    return aiohttp.TCPConnector(family=family, local_addr=(local_ip, 0))


async def analyze_url(url: str, preferred_ip: Optional[str] = None) -> URLAnalysis:
    connector = _build_connector(preferred_ip) if preferred_ip else None
    timeout = aiohttp.ClientTimeout(total=config.get("REQUEST_TIMEOUT_SECONDS"))
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=BROWSER_HEADERS) as session:
        # Step 1: Probe Range capability directly with Range: bytes=0-0
        try:
            headers = dict(BROWSER_HEADERS)
            headers["Range"] = "bytes=0-0"
            headers["Accept-Encoding"] = "identity"
            async with session.get(url, allow_redirects=True, headers=headers, ssl=False) as resp:
                final_url = str(resp.url)
                if url.lower().startswith("https://") and final_url.lower().startswith("http://"):
                    raise ValueError(f"Insecure redirect rejected: protocol downgrade from HTTPS to HTTP ({redact_url(final_url)})")

                content_type = resp.headers.get("Content-Type", "application/octet-stream")
                etag = normalize_etag(resp.headers.get("ETag"))
                last_modified = resp.headers.get("Last-Modified") or None

                if resp.status == 206:
                    content_range = resp.headers.get("Content-Range", "").strip()
                    m = CONTENT_RANGE_RE.match(content_range)
                    if m:
                        r_start, r_end, total_str = int(m.group(1)), int(m.group(2)), m.group(3)
                        total_size = int(total_str) if total_str != "*" else int(resp.headers.get("Content-Length", 0))
                        if total_size > 0 and r_start == 0 and r_end == 0:
                            return URLAnalysis(
                                url=url,
                                final_url=final_url,
                                content_length=total_size,
                                supports_ranges=True,
                                content_type=content_type,
                                etag=etag,
                                last_modified=last_modified,
                            )
                    # 206 but missing/malformed Content-Range
                    total_size = int(resp.headers.get("Content-Length", "0"))
                    return URLAnalysis(
                        url=url,
                        final_url=final_url,
                        content_length=total_size,
                        supports_ranges=False,
                        content_type=content_type,
                        etag=etag,
                        last_modified=last_modified,
                        range_error_reason="Malformed or missing Content-Range in HTTP 206 response",
                    )
                elif resp.status == 200:
                    # Server ignored Range header and returned full content
                    total_size = int(resp.headers.get("Content-Length", "0"))
                    return URLAnalysis(
                        url=url,
                        final_url=final_url,
                        content_length=total_size,
                        supports_ranges=False,
                        content_type=content_type,
                        etag=etag,
                        last_modified=last_modified,
                        range_error_reason="Server returned HTTP 200 OK to Range request (ranges not supported)",
                    )
        except ValueError:
            raise
        except Exception:
            pass

        # Step 2: Fallback to HEAD request
        try:
            async with session.head(url, allow_redirects=True, ssl=False) as resp:
                final_url = str(resp.url)
                if resp.status < 400:
                    total_size = int(resp.headers.get("Content-Length", "0"))
                    etag = normalize_etag(resp.headers.get("ETag"))
                    last_modified = resp.headers.get("Last-Modified") or None
                    content_type = resp.headers.get("Content-Type", "application/octet-stream")
                    return URLAnalysis(
                        url=url,
                        final_url=final_url,
                        content_length=total_size,
                        supports_ranges=False,
                        content_type=content_type,
                        etag=etag,
                        last_modified=last_modified,
                        range_error_reason="Range probe failed; falling back to single-stream",
                    )
        except Exception:
            pass

        # Step 3: Final fallback basic GET (stream headers/metadata)
        async with session.get(url, allow_redirects=True, ssl=False) as resp:
            resp.raise_for_status()
            total_size = int(resp.headers.get("Content-Length", "0"))
            return URLAnalysis(
                url=url,
                final_url=str(resp.url),
                content_length=total_size,
                supports_ranges=False,
                content_type=resp.headers.get("Content-Type", "application/octet-stream"),
                etag=normalize_etag(resp.headers.get("ETag")),
                last_modified=resp.headers.get("Last-Modified") or None,
                range_error_reason="Fallback to basic GET; ranges disabled",
            )


def plan_adaptive_chunks(
    expected_size: int,
    latencies: Optional[Dict[str, float]] = None,
    base_chunk_size: Optional[int] = None,
    min_chunk_size: Optional[int] = None,
    max_chunk_size: Optional[int] = None,
    num_interfaces: int = 1,
    enable_warmup_tail: Optional[bool] = None,
) -> List[Tuple[int, int, int]]:
    """
    Adaptive chunk planner (Milestone 3):
    - Small payloads (<= 4 * base_cs): partitioned uniformly by base_cs.
    - Large payloads:
      1. Warm-up phase: Initial chunks are small (min_cs) for fast RTT/speed probing.
      2. Steady-state phase: Chunks expand based on interface latencies/bandwidth up to max_cs.
      3. Tail-end ramp-down: Final chunks taper back to min_cs to eliminate straggler workers.
    """
    if expected_size <= 0:
        return []

    base_cs = base_chunk_size or config.get("BASE_CHUNK_SIZE") or (2 * 1024 * 1024)
    min_cs = min_chunk_size or config.get("MIN_CHUNK_SIZE") or (256 * 1024)
    max_cs = max_chunk_size or config.get("MAX_CHUNK_SIZE") or (8 * 1024 * 1024)

    # For files smaller than or equal to min_cs, return a single chunk
    if expected_size <= min_cs:
        return [(0, 0, expected_size - 1)]

    # For smaller files (<= 4 chunks), use uniform base_cs chunks
    if expected_size <= base_cs * 4:
        ranges = []
        cursor = 0
        idx = 0
        while cursor < expected_size:
            end = min(cursor + base_cs - 1, expected_size - 1)
            ranges.append((idx, cursor, end))
            cursor = end + 1
            idx += 1
        return ranges

    # Steady-state sizing based on interface latency normalization
    steady_cs = base_cs
    if latencies:
        min_lat = max(min(latencies.values()), 1.0)
        normalized = [min_lat / max(lat, 1.0) for lat in latencies.values()]
        sizes = [max(min_cs, min(max_cs, int(base_cs * n))) for n in normalized]
        steady_cs = max(min_cs, min(max_cs, sum(sizes) // len(sizes)))

    # For single-interface downloads, warm-up probing and tail tapering provide no straggler
    # mitigation and introduce unnecessary chunk allocation, HTTP roundtrips, and fsync overhead.
    # Therefore, single-interface downloads use uniform steady-state chunks.
    # ponytail: ceiling: single-interface uses uniform chunks; upgrade: dynamic mid-flight chunk sizing on single interface if bandwidth is volatile
    if num_interfaces <= 1:
        ranges = []
        cursor = 0
        idx = 0
        while cursor < expected_size:
            end = min(cursor + steady_cs - 1, expected_size - 1)
            ranges.append((idx, cursor, end))
            cursor = end + 1
            idx += 1
        return ranges

    # Warm-up and tail tapering: gated by parameter or config flag.
    # Default OFF: uniform steady-state chunks showed equal or better performance on both
    # loopback and synthetic 20/2 MB/s asymmetric benchmarks (5-run medians).
    # Enable when a real asymmetric benchmark demonstrates a measurable win.
    # ponytail: ceiling: uniform is correct default; upgrade: enable ENABLE_ADAPTIVE_WARMUP_TAIL
    # when benchmarks on real cellular/Wi-Fi asymmetry show improvement.
    if enable_warmup_tail is None:
        enable_warmup_tail = bool(config.get("ENABLE_ADAPTIVE_WARMUP_TAIL", False))

    if not enable_warmup_tail:
        ranges = []
        cursor = 0
        idx = 0
        while cursor < expected_size:
            end = min(cursor + steady_cs - 1, expected_size - 1)
            ranges.append((idx, cursor, end))
            cursor = end + 1
            idx += 1
        return ranges

    # Warmup covers first 2 chunks per interface (min 2, max 6)
    warmup_chunks = max(2, min(2 * max(1, num_interfaces), 6))
    warmup_size = min_cs

    # Tail zone threshold: last 10% of total bytes or 3 * steady_cs
    tail_threshold = max(steady_cs * 3, int(expected_size * 0.10))

    ranges = []
    cursor = 0
    idx = 0

    while cursor < expected_size:
        remaining = expected_size - cursor

        if remaining < min_cs and ranges:
            # Absorb small leftover fragment into the last chunk
            prev_idx, prev_start, _ = ranges[-1]
            ranges[-1] = (prev_idx, prev_start, expected_size - 1)
            break

        # Tail-end ramp-down: small chunks to eliminate stragglers
        # ponytail: ceiling: tail-end small chunks mitigate stragglers; upgrade: in-flight chunk splitting if extreme stragglers occur
        if remaining <= tail_threshold and idx >= warmup_chunks:
            chunk_size = min_cs
        # Warmup phase: small chunks for immediate speed feedback
        elif idx < warmup_chunks:
            chunk_size = warmup_size
        # Steady-state phase
        else:
            chunk_size = steady_cs

        chunk_size = min(chunk_size, remaining)
        end = min(cursor + chunk_size - 1, expected_size - 1)
        ranges.append((idx, cursor, end))
        cursor = end + 1
        idx += 1

    return ranges


class DownloadManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, DownloadJob] = {}
        self._job_tasks: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._thread_locks: Dict[str, threading.Lock] = {}

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        return self.jobs.get(job_id)

    async def create_job(self, url: str, output_path: str, interfaces: List[Dict[str, str]], bandwidth_limits: Dict[str, float] = None) -> DownloadJob:
        if not interfaces:
            raise ValueError("At least one interface is required")
            
        # Path collision handling
        final_path = output_path
        active_paths = [j.output_path for j in self.jobs.values() if j.status not in ("completed", "failed")]
        if final_path in active_paths:
            p = Path(final_path)
            ext = p.suffix
            base = str(p.with_suffix(""))
            counter = 1
            while f"{base}({counter}){ext}" in active_paths:
                counter += 1
            final_path = f"{base}({counter}){ext}"

        job_id = str(uuid.uuid4())
        # Ensure directory exists
        try:
            Path(final_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[MANAGER] Could not create directory: {e}")

        job = DownloadJob(job_id=job_id, url=url, output_path=final_path, bandwidth_limits=bandwidth_limits or {})
        self.jobs[job_id] = job
        self._locks[job_id] = asyncio.Lock()
        self._thread_locks[job_id] = threading.Lock()
        self._job_tasks[job_id] = asyncio.create_task(self._run_job(job, interfaces))
        return job

    async def resume_job_from_state(self, data: dict, interfaces: List[Dict[str, str]]) -> DownloadJob:
        if not interfaces:
            raise ValueError("At least one interface is required")
            
        job_id = data["job_id"]
        job = DownloadJob(
            job_id=job_id,
            url=data["url"],
            output_path=data["output_path"],
            bandwidth_limits=data.get("bandwidth_limits", {}),
            expected_size=data.get("expected_size", 0),
            supports_ranges=data.get("supports_ranges", False),
            total_downloaded=data.get("total_downloaded", 0),
            boosted=data.get("boosted", False),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            final_url=data.get("final_url"),
            range_error_reason=data.get("range_error_reason"),
            resume_confidence=data.get("resume_confidence", "high"),
        )
        job._ranges = data.get("_ranges", [])
        for r in job._ranges:
            c_idx, c_start, c_end = r
            c_data = (data.get("chunks") or {}).get(str(c_idx), {})
            job.chunks[c_idx] = Chunk(
                chunk_id=c_idx,
                start=c_start,
                end=c_end,
                status=c_data.get("status", ChunkStatus.PENDING),
                attempts=c_data.get("attempts", 0),
            )
        
        # Restore interfaces progress if present to avoid UI flashing 0
        for ip, iface_data in data.get("interfaces", {}).items():
            job.progress[ip] = InterfaceProgress(
                name=iface_data["name"],
                ip_address=iface_data["ip_address"],
                chunk_start=iface_data["chunk_start"],
                chunk_end=iface_data["chunk_end"],
                downloaded=iface_data.get("downloaded", 0),
                status=iface_data.get("status", "pending"),
            )
        
        self.jobs[job_id] = job
        self._locks[job_id] = asyncio.Lock()
        self._thread_locks[job_id] = threading.Lock()
        
        # Use the wrapper
        self._job_tasks[job_id] = asyncio.create_task(self._resume_job_wrapper(job, interfaces))
            
        return job

    async def _resume_job_wrapper(self, job: DownloadJob, interfaces: List[Dict[str, str]]) -> None:
        job.started_at = time.time()
        try:
            # Safe Resume Validation: verify remote server validators before combining partial data
            iface_ip = interfaces[0]["ip_address"] if interfaces else None
            current_info = None
            try:
                current_info = await analyze_url(job.url, iface_ip)
            except Exception as e:
                print(f"[RESUME] Warning: Could not re-probe server validators: {e}")

            if current_info:
                mismatch = False
                reason = ""
                if job.expected_size > 0 and current_info.content_length > 0 and current_info.content_length != job.expected_size:
                    mismatch = True
                    reason = f"File size changed from {job.expected_size} to {current_info.content_length}"
                elif job.etag and current_info.etag and job.etag != current_info.etag:
                    mismatch = True
                    reason = f"ETag changed from {job.etag} to {current_info.etag}"
                elif job.last_modified and current_info.last_modified and job.last_modified != current_info.last_modified:
                    mismatch = True
                    reason = f"Last-Modified changed from {job.last_modified} to {current_info.last_modified}"

                if mismatch:
                    print(f"[RESUME] Remote resource modified ({reason}). Restarting download safely to prevent corruption.")
                    job.resume_confidence = "none"
                    temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
                    if temp_dir.exists():
                        for f in temp_dir.glob("chunk_*.*"):
                            try:
                                f.unlink(missing_ok=True)
                            except Exception:
                                pass
                    job.total_downloaded = 0
                    job.expected_size = current_info.content_length
                    job.supports_ranges = current_info.supports_ranges
                    job.etag = current_info.etag
                    job.last_modified = current_info.last_modified
                    job.final_url = current_info.final_url
                    job.range_error_reason = current_info.range_error_reason
                    job._ranges = []
                    job.chunks = {}
                    await self._run_job(job, interfaces)
                    return
                else:
                    if current_info.etag and job.etag and current_info.etag == job.etag:
                        job.resume_confidence = "high"
                    elif current_info.last_modified and job.last_modified and current_info.last_modified == job.last_modified:
                        job.resume_confidence = "medium"
                    elif job.expected_size > 0 and current_info.content_length == job.expected_size:
                        # ponytail: ceiling: 'low' confidence resumes without prompting user; upgrade: prompt or warn user in UI before resuming with 'low' confidence
                        job.resume_confidence = "low"
                    else:
                        job.resume_confidence = "none"

                    if current_info.etag:
                        job.etag = current_info.etag
                    if current_info.last_modified:
                        job.last_modified = current_info.last_modified
                    if current_info.final_url:
                        job.final_url = current_info.final_url

            # Milestone 4: Preflight disk space check on resume
            check_disk_space_preflight(
                Path(job.output_path),
                job.expected_size,
                chunk_files=job._chunk_files,
                supports_ranges=job.supports_ranges,
            )

            if job.expected_size > 0 and job.supports_ranges and job._ranges:
                job.status = "downloading"
                await self._parallel_download(job, interfaces)
            elif job.expected_size > 0 and not job.supports_ranges:
                job.status = "downloading"
                await self._single_download(job, interfaces[0])
            else:
                # Fallback to full _run_job
                await self._run_job(job, interfaces)
                return
                
            if job.status == "failed" or job.is_cancelled:
                if job.status != "failed":
                    job.status = "failed"
                    job.error = "Cancelled by user"
            else:
                job.status = "completed"
            job.finished_at = time.time()
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()

    # ------------------------------------------------------------------
    # Latency measurement
    # ------------------------------------------------------------------
    async def _measure_latency(self, url: str, interface_ip: str) -> float:
        """Measure round-trip latency (ms) to the download server via HEAD request."""
        try:
            connector = _build_connector(interface_ip)
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=BROWSER_HEADERS) as session:
                start = time.perf_counter()
                async with session.head(url, allow_redirects=True) as _:
                    pass
                return (time.perf_counter() - start) * 1000
        except Exception:
            return 500.0  # Default high latency on failure

    # ------------------------------------------------------------------
    # Weight calculation
    # ------------------------------------------------------------------
    def _rebalance_weights(self, job: DownloadJob) -> None:
        """Recalculate bandwidth weights based on current rolling speeds."""
        active = {ip: prog for ip, prog in job.progress.items()
                  if prog.status in ("downloading", "pending")}
        total_speed = sum(p.speed_mb_s for p in active.values())
        if total_speed <= 0:
            # Equal distribution when no speed data yet
            count = len(active) or 1
            for prog in active.values():
                prog.weight = 1.0 / count
                prog.weight_percent = round(100 / count)
            return
        for prog in active.values():
            prog.weight = prog.speed_mb_s / total_speed
            prog.weight_percent = round((prog.speed_mb_s / total_speed) * 100)

    # ------------------------------------------------------------------
    # Job orchestration
    # ------------------------------------------------------------------
    async def _run_job(self, job: DownloadJob, interfaces: List[Dict[str, str]]) -> None:
        job.started_at = time.time()
        job.status = "analyzing"

        try:
            analysis = None
            last_analysis_err = None
            for iface in interfaces:
                try:
                    analysis = await analyze_url(job.url, iface["ip_address"])
                    break
                except Exception as e:
                    last_analysis_err = e
            if not analysis:
                try:
                    analysis = await analyze_url(job.url, None)
                except Exception as e:
                    raise last_analysis_err or e

            job.expected_size = int(analysis.content_length)
            job.supports_ranges = bool(analysis.supports_ranges)
            job.etag = analysis.etag
            job.last_modified = analysis.last_modified
            job.final_url = analysis.final_url
            job.range_error_reason = analysis.range_error_reason
            
            if not job.supports_ranges and analysis.range_error_reason:
                print(f"[HTTP] Multi-range disabled for job {job.job_id}: {analysis.range_error_reason}")
            
            if job.expected_size <= 0:
                # Size is unknown (e.g. dynamic page / chunked encoding). Fall back to single connection.
                job.supports_ranges = False

            # Milestone 4: Preflight disk space check per volume
            check_disk_space_preflight(
                Path(job.output_path),
                job.expected_size,
                chunk_files=job._chunk_files,
                supports_ranges=job.supports_ranges,
            )

            if not job.supports_ranges:
                job.status = "downloading"
                active_iface = interfaces[0]
                await self._single_download(job, active_iface)
            else:
                job.status = "downloading"
                await self._parallel_download(job, interfaces)

            if job.status == "failed" or job.is_cancelled:
                if job.status != "failed":
                    job.status = "failed"
                    job.error = "Cancelled by user"
            else:
                job.status = "completed"
            job.finished_at = time.time()
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()

    async def _single_download(self, job: DownloadJob, interface: Dict[str, str]) -> None:
        out_path = Path(job.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp_out = out_path.with_suffix(out_path.suffix + f".tmp_{job.job_id}")
        progress = InterfaceProgress(
            name=interface["name"],
            ip_address=interface["ip_address"],
            chunk_start=0,
            chunk_end=max(job.expected_size - 1, 0),
            status="downloading",
            weight=1.0,
            weight_percent=100,
        )
        job.progress[interface["ip_address"]] = progress
        started = time.perf_counter()
        
        worker_id = uuid.uuid4()
        if not hasattr(job, "_active_threads"):
            job._active_threads = set()
        job._active_threads.add(worker_id)
        
        def _do_single_download_and_commit():
            self._download_with_requests(
                job, interface["ip_address"],
                job.url, temp_out, "wb", None, progress, started,
                worker_id, 0, job.expected_size if job.expected_size > 0 else None
            )
            if not job.is_cancelled and temp_out.exists():
                if job.expected_size > 0 and temp_out.stat().st_size != job.expected_size:
                    temp_out.unlink(missing_ok=True)
                    raise ValueError(f"Single download size mismatch: expected {job.expected_size}, got {temp_out.stat().st_size}")
                os.replace(temp_out, out_path)

        try:
            temp_out.unlink(missing_ok=True)
            await asyncio.to_thread(_do_single_download_and_commit)
        finally:
            if hasattr(job, "_active_threads"):
                job._active_threads.discard(worker_id)
            if temp_out.exists() and (job.is_cancelled or job.status == "failed"):
                try:
                    temp_out.unlink(missing_ok=True)
                except Exception:
                    pass
                
        if job.is_cancelled:
            progress.status = "cancelled"
        else:
            progress.status = "completed"

    # ------------------------------------------------------------------
    # Worker — grabs chunks from the shared queue with EWMA/health adaptive sizing
    # ------------------------------------------------------------------
    def _calculate_worker_target_chunk_size(self, prog: InterfaceProgress) -> int:
        """
        Calculates optimal worker chunk size based on interface EWMA throughput and health.

        Parameters & Sizing Model:
        - EWMA smoothing factor: alpha = 0.3 (30% new chunk speed sample, 70% historical EWMA).
        - Target throughput duration: 2.0 seconds of data per chunk.
        - Clamping bounds: [MIN_CHUNK_SIZE (256 KB), MAX_CHUNK_SIZE (8 MB)].
        - Default fallback: BASE_CHUNK_SIZE (2 MB) when EWMA is 0 or uninitialized.
        - Health constraint: Any 'degraded' or consecutive_failures > 0 interface is strictly capped at MIN_CHUNK_SIZE (256 KB).
        - Slicing hysteresis: Workers only slice chunks from the queue when (chunk_bytes - target_cs) >= MIN_CHUNK_SIZE (256 KB)
          to prevent tiny residual fragment chunks.
        """
        min_cs = config.get("MIN_CHUNK_SIZE") or (256 * 1024)
        max_cs = config.get("MAX_CHUNK_SIZE") or (8 * 1024 * 1024)
        base_cs = config.get("BASE_CHUNK_SIZE") or (2 * 1024 * 1024)

        # Degraded or failing interfaces are strictly restricted to min_chunk_size
        if prog.health == "degraded" or prog.consecutive_failures > 0:
            return min_cs

        if prog.ewma_speed_mb_s > 0:
            # Target ~2.0 seconds of throughput based on EWMA
            target_bytes = int(prog.ewma_speed_mb_s * 1024 * 1024 * 2.0)
            return max(min_cs, min(max_cs, target_bytes))

        return base_cs

    async def _worker(self, job: DownloadJob, iface: Dict[str, str],
                      queue: asyncio.Queue, chunk_files: Dict[int, Path]) -> None:
        ip = iface["ip_address"]
        prog = job.progress[ip]
        min_speed = config.get("MIN_INTERFACE_SPEED_THRESHOLD")
        grace = config.get("SLOW_INTERFACE_GRACE_PERIOD")
        max_failures = config.get("MAX_CONSECUTIVE_FAILURES")
        cooldown_secs = config.get("RETRY_SAME_INTERFACE_COOLDOWN")

        paused_since = None

        while not job.is_cancelled:
            # --- Check if this interface is excluded ---
            if prog.consecutive_failures >= max_failures:
                prog.status = "excluded"
                prog.speed_mb_s = 0.0
                cooldown = float(config.get("EXCLUDED_INTERFACE_COOLDOWN") or 60.0)
                prog._cooldown_until = time.time() + cooldown
                break

            # --- Check slow-speed gating ---
            if prog.status == "paused_slow":
                if paused_since is None:
                    paused_since = time.time()
                if time.time() - paused_since > 5.0:
                    break
                await asyncio.sleep(1)
                continue

            paused_since = None

            # --- Check cooldown (from cross-interface retry) ---
            if time.time() < prog._cooldown_until:
                break

            # --- Grab next chunk ---
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            chunk = item if isinstance(item, Chunk) else job.chunks.get(item[0])
            if chunk is None:
                chunk = Chunk(chunk_id=item[0], start=item[1], end=item[2])
                job.chunks[chunk.chunk_id] = chunk

            # Skip if already completed
            if chunk.status == ChunkStatus.COMPLETE:
                queue.task_done()
                continue

            chunk.status = ChunkStatus.ASSIGNED
            chunk.assigned_interface = ip

            chunk_idx = chunk.chunk_id
            start, end = chunk.start, chunk.end
            output_file = chunk_files[chunk_idx]

            # --- Adaptive Chunk Sizing & Slicing based on EWMA and Health ---
            target_cs = self._calculate_worker_target_chunk_size(prog)
            chunk_bytes = end - start + 1
            min_cs = config.get("MIN_CHUNK_SIZE") or (256 * 1024)

            # If chunk is larger than target_cs, slice off target_cs and return the remainder to the queue
            if chunk_bytes > target_cs and (chunk_bytes - target_cs) >= min_cs:
                old_end = end
                end = min(start + target_cs - 1, old_end - min_cs)
                if job.expected_size > 0:
                    end = min(end, job.expected_size - 1)
                chunk.end = end
                new_idx = max(job.chunks.keys()) + 1
                remainder_end = min(old_end, job.expected_size - 1) if job.expected_size > 0 else old_end
                remainder = Chunk(chunk_id=new_idx, start=end + 1, end=remainder_end)
                job.chunks[new_idx] = remainder
                temp_dir = output_file.parent
                new_part = temp_dir / f"chunk_{new_idx:05d}.part"
                chunk_files[new_idx] = new_part
                job._chunk_files[new_idx] = new_part
                for i_r, r in enumerate(job._ranges):
                    if r[0] == chunk_idx:
                        job._ranges[i_r] = (chunk_idx, start, end)
                        break
                job._ranges.append((new_idx, remainder.start, remainder.end))
                job._total_chunks = len(job._ranges)
                queue.put_nowait(remainder)

            chunk.status = ChunkStatus.DOWNLOADING
            chunk.assigned_interface = ip
            chunk.started_at = time.time()
            chunk.attempts += 1

            prog.chunk_start = start
            prog.chunk_end = end
            prog.current_chunk_idx = chunk_idx
            prog._bytes_at_start_of_chunk = prog.downloaded
            prog.status = "downloading"

            worker_id = uuid.uuid4()
            if not hasattr(job, "_active_threads"):
                job._active_threads = set()
            job._active_threads.add(worker_id)
            prog.request_count += 1

            try:
                won = await self._download_range(job, iface, (start, end), output_file, worker_id, chunk=chunk)
                if not won:
                    # Tail racer lost or was cancelled by winning competitor:
                    # Clean exit with zero penalty to health, stall, or EWMA.
                    queue.task_done()
                    continue

                prog.error = None
                prog.consecutive_failures = 0
                prog.chunks_completed += 1
                prog.success_count += 1
                prog.record_outcome("success")
                prog.last_success_time = time.time()
                prog._last_progress_time = time.time()

                # Update EWMA throughput
                chunk_dur = max(time.time() - chunk.started_at, 0.001)
                chunk_speed = ((end - start + 1) / (1024 * 1024)) / chunk_dur
                if prog.ewma_speed_mb_s <= 0.0:
                    prog.ewma_speed_mb_s = chunk_speed
                else:
                    prog.ewma_speed_mb_s = 0.3 * chunk_speed + 0.7 * prog.ewma_speed_mb_s

                chunk.status = ChunkStatus.COMPLETE
                chunk.completed_at = time.time()
                chunk.last_error = None
                queue.task_done()
                if getattr(job, "_completion_event", None):
                    job._completion_event.set()
            except asyncio.CancelledError:
                if not job.is_cancelled:
                    if chunk.status != ChunkStatus.COMPLETE:
                        chunk.status = ChunkStatus.PENDING
                        chunk.assigned_interface = None
                    queue.put_nowait(chunk)
                raise
            except Exception as e:
                is_stall = isinstance(e, (StalledDownloadError, requests.exceptions.ReadTimeout))
                chunk.last_error = str(e)
                if chunk.status != ChunkStatus.COMPLETE:
                    chunk.status = ChunkStatus.PENDING
                    chunk.assigned_interface = None

                prog.consecutive_failures += 1
                prog.failure_count += 1
                prog.record_outcome("stall" if is_stall else "failure")
                if is_stall:
                    prog.stall_count += 1
                job._chunk_failures[chunk_idx] = job._chunk_failures.get(chunk_idx, 0) + 1

                if isinstance(e, OriginResourceModifiedError):
                    job.status = "failed"
                    job.resume_confidence = "none"
                    job.error = str(e)
                    chunk.status = ChunkStatus.FAILED
                    job.is_cancelled = True
                    # Defined Mutation Abort Outcome:
                    # 1. Abort chunk stream immediately without writing to .part
                    # 2. Unlink any active .tmp files (handled in finally of _download_range)
                    # 3. Purge existing .part files in .burst_{job_id}
                    temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
                    if temp_dir.exists():
                        for f in temp_dir.glob("chunk_*"):
                            try:
                                f.unlink(missing_ok=True)
                            except Exception:
                                pass
                    # 4. Clean up destination file if present
                    try:
                        Path(job.output_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    # 5. Drain the queue so other workers immediately exit
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except Exception:
                            break
                    break

                if is_non_retryable_error(e) or job._chunk_failures[chunk_idx] > config.get("RETRY_ATTEMPTS") * 2:
                    job.status = "failed"
                    job.error = f"Chunk {chunk_idx} failed permanently: {e}"
                    chunk.status = ChunkStatus.FAILED
                    job.is_cancelled = True
                    break

                prog.retry_count += 1
                backoff_delay = calculate_backoff(chunk.attempts)
                best_alt = self._find_best_alternate(job, ip)
                reason = "Stalled watchdog timeout" if is_stall else str(e)[:100]

                if best_alt:
                    job.retry_events.append(RetryEvent(
                        timestamp=time.time(), chunk_index=chunk_idx,
                        from_interface=ip, to_interface=best_alt,
                        reason=reason,
                    ))
                    queue.put_nowait(chunk)
                    prog._cooldown_until = time.time() + cooldown_secs
                    prog.error = str(e)
                    prog.status = "paused_slow"
                    prog.speed_mb_s = 0.0
                else:
                    queue.put_nowait(chunk)
                    prog.error = str(e)
                    prog.speed_mb_s = 0.0
                    await asyncio.sleep(backoff_delay)
            finally:
                if hasattr(job, "_active_threads"):
                    job._active_threads.discard(worker_id)

            # --- Slow-speed detection ---
            if prog.speed_mb_s > 0 and prog.speed_mb_s < min_speed:
                if prog._slow_since is None:
                    prog._slow_since = time.time()
                elif time.time() - prog._slow_since > grace:
                    prog.status = "paused_slow"
                    prog.speed_mb_s = 0.0
            else:
                prog._slow_since = None

        if prog.status == "downloading" and not job.is_cancelled:
            all_chunks_done = all(c.status == ChunkStatus.COMPLETE for c in job.chunks.values())
            prog.status = "completed" if all_chunks_done else "idle"
            prog.speed_mb_s = 0.0

        if getattr(job, "_completion_event", None):
            job._completion_event.set()

    async def remove_interface(self, job_id: str, ip: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        
        prog = job.progress.get(ip)
        if not prog or prog.status in ("excluded", "cancelled"):
            return {"status": "already_removed"}

        if job.status not in ("downloading", "waiting_reconnect", "paused"):
             raise ValueError(f"Job is not in a state to remove interfaces (status={job.status})")            
        prog.status = "excluded"
        prog.speed_mb_s = 0.0
        prog.current_chunk_idx = None
        
        # 2. Cancel the worker(s) if active
        for key in list(job._workers.keys()):
            if key == ip or key.startswith(f"{ip}_"):
                task = job._workers[key]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                del job._workers[key]
            
        print(f"[REMOVE_IFACE] Interface {ip} removed from job {job_id}")
        return {"removed": True}

    def _find_best_alternate(self, job: DownloadJob, exclude_ip: str) -> Optional[str]:
        """Find the healthiest alternative interface for retry routing."""
        best_ip = None
        best_speed = -1.0
        for ip, prog in job.progress.items():
            if ip == exclude_ip:
                continue
            if prog.status in ("excluded", "disconnected", "cancelled"):
                continue
            if prog.speed_mb_s > best_speed:
                best_speed = prog.speed_mb_s
                best_ip = ip
        return best_ip

    # ------------------------------------------------------------------
    # Add interface mid-download (hot-swap)
    # ------------------------------------------------------------------
    async def add_interface(self, job_id: str, interface: Dict[str, str]) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if job._queue is None:
            raise ValueError(f"Job uses single-stream mode — cannot add interfaces (no queue)")
        if job.status not in ("downloading", "waiting_reconnect", "paused"):
            raise ValueError(f"Job is not active (status={job.status})")
        ip = interface["ip_address"]

        if ip in job.progress:
            # Interface already known — restart its worker if it died
            prog = job.progress[ip]
            existing_tasks = [t for k, t in job._workers.items() if (k == ip or k.startswith(f"{ip}_")) and not t.done()]
            if existing_tasks:
                return {"reused": True}  # Already actively working
            # Reset state and respawn worker
            prog.status = "pending"
            prog.consecutive_failures = 0
            prog._cooldown_until = 0.0
            prog._slow_since = None
            prog.error = None
        else:
            job.progress[ip] = InterfaceProgress(
                name=interface["name"], ip_address=ip,
                chunk_start=0, chunk_end=job.expected_size, status="pending",
            )

        # If the job was waiting for reconnect, resume it
        if job.status == "waiting_reconnect":
            job.status = "downloading"
            job.error = None

        # If queue is empty, steal work from the busiest interface by
        # cancelling its worker, splitting its remaining range, and re-queuing
        if job._queue.empty():
            print(f"[ADD_IFACE] Queue empty, looking for work to steal for {ip}...")
            busiest_ip = None
            busiest_remaining = -1
            for other_ip, other_prog in job.progress.items():
                if other_ip == ip:
                    continue
                if other_prog.status == "downloading" and other_prog.current_chunk_idx is not None:
                    remaining = (other_prog.chunk_end - other_prog.chunk_start) - other_prog.downloaded
                    if remaining > busiest_remaining:
                        busiest_remaining = remaining
                        busiest_ip = other_ip
            
            if busiest_ip:
                other_prog = job.progress[busiest_ip]
                chunk_idx = other_prog.current_chunk_idx
                print(f"[ADD_IFACE] Stealing from {busiest_ip} (chunk {chunk_idx}, remaining: {busiest_remaining} bytes)")
                
                # Cancel all busiest workers
                old_tasks = [t for k, t in job._workers.items() if (k == busiest_ip or k.startswith(f"{busiest_ip}_")) and not t.done()]
                for old_task in old_tasks:
                    old_task.cancel()
                    try:
                        await old_task
                    except (asyncio.CancelledError, Exception):
                        pass
                for key in list(job._workers.keys()):
                    if key == busiest_ip or key.startswith(f"{busiest_ip}_"):
                        del job._workers[key]
                
                # Find the chunk range to split
                range_idx = -1
                for i, r in enumerate(job._ranges):
                    if r[0] == chunk_idx:
                        range_idx = i
                        break
                
                if range_idx != -1:
                    _, r_start, r_end = job._ranges[range_idx]
                    # We use the full range since we can't easily resume partial chunks in current architecture
                    mid = r_start + (r_end - r_start) // 2
                    
                    new_idx = job._total_chunks
                    job._total_chunks += 1
                    
                    range_a = (chunk_idx, r_start, mid)
                    range_b = (new_idx, mid + 1, r_end)
                    
                    print(f"[ADD_IFACE] Splitting chunk {chunk_idx} [{r_start}-{r_end}] -> [{r_start}-{mid}] and [{mid+1}-{r_end}] (new index {new_idx})")
                    
                    # Update ranges and files
                    job._ranges[range_idx] = range_a
                    job._ranges.insert(range_idx + 1, range_b)
                    
                    temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
                    chunk_a = Chunk(chunk_id=chunk_idx, start=r_start, end=mid, status=ChunkStatus.PENDING)
                    chunk_b = Chunk(chunk_id=new_idx, start=mid + 1, end=r_end, status=ChunkStatus.PENDING)
                    job.chunks[chunk_idx] = chunk_a
                    job.chunks[new_idx] = chunk_b
                    
                    # Put both back in queue
                    job._queue.put_nowait(chunk_a)
                    job._queue.put_nowait(chunk_b)
                    
                    # Reset both interface progresses to pending so workers restart
                    other_prog.status = "pending"
                    
                    # Correct progress accounting
                    downloaded_this_chunk = other_prog.downloaded - other_prog._bytes_at_start_of_chunk
                    with self._thread_locks[job.job_id]:
                        job.total_downloaded -= downloaded_this_chunk
                    other_prog.downloaded = other_prog._bytes_at_start_of_chunk
                    
                    other_prog.current_chunk_idx = None
                    
                    # New interface is already pending from earlier logic
                    print(f"[ADD_IFACE] Work redistributed. Queue size: {job._queue.qsize()}")
        
        if job.status == "paused":
            print(f"[ADD_IFACE] Job is paused, just marking {ip} as pending")
            return {"added": True, "paused": True}

        print(f"[ADD_IFACE] Spawning/Restarting download worker(s) for {ip}")
        self._rebalance_weights(job)
        num_workers = 3 if getattr(job, "boosted", False) else 1
        for idx in range(num_workers):
            task_key = f"{ip}_{idx}" if num_workers > 1 else ip
            task = asyncio.create_task(self._worker(job, interface, job._queue, job._chunk_files))
            job._workers[task_key] = task
        return {"spawned": True, "queue_size": job._queue.qsize()}

    async def remove_interface(self, job_id: str, ip: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if ip not in job.progress:
            return {"status": "not_in_job"}

        prog = job.progress[ip]
        if prog.status == "excluded":
            return {"status": "already_excluded"}

        # Cancel the worker(s)
        for key in list(job._workers.keys()):
            if key == ip or key.startswith(f"{ip}_"):
                task = job._workers[key]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                del job._workers[key]
        
        # If it was middle of a chunk, return chunk to queue
        if prog.current_chunk_idx is not None and job._queue:
            c = job.chunks.get(prog.current_chunk_idx)
            if c and c.status != ChunkStatus.COMPLETE:
                print(f"[REMOVE_IFACE] Returning chunk {prog.current_chunk_idx} to queue")
                c.status = ChunkStatus.PENDING
                c.assigned_interface = None
                job._queue.put_nowait(c)
            downloaded_this_chunk = prog.downloaded - prog._bytes_at_start_of_chunk
            if downloaded_this_chunk > 0:
                with self._thread_locks[job.job_id]:
                    job.total_downloaded -= downloaded_this_chunk
                prog.downloaded = prog._bytes_at_start_of_chunk
            prog.current_chunk_idx = None

        prog.status = "excluded"
        prog.current_chunk_idx = None
        prog.speed_mb_s = 0
        for key in list(job._workers.keys()):
            if key == ip or key.startswith(f"{ip}_"):
                del job._workers[key]

        self._rebalance_weights(job)
        return {"status": "success", "removed": ip, "queue_size": job._queue.qsize() if job._queue else 0}

    async def pause_job(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status != "downloading":
            return {"status": "already_paused_or_inactive", "current": job.status}

        # Cancel all active workers
        for ip, task in list(job._workers.items()):
            if not task.done():
                task.cancel()
            
            # Re-queue active chunks
            prog = job.progress.get(ip)
            if prog and prog.current_chunk_idx is not None and job._queue:
                c = job.chunks.get(prog.current_chunk_idx)
                if c and c.status != ChunkStatus.COMPLETE:
                    print(f"[PAUSE] Returning chunk {prog.current_chunk_idx} to queue")
                    c.status = ChunkStatus.PENDING
                    c.assigned_interface = None
                    job._queue.put_nowait(c)
                downloaded_this_chunk = prog.downloaded - prog._bytes_at_start_of_chunk
                if downloaded_this_chunk > 0:
                    with self._thread_locks[job.job_id]:
                        job.total_downloaded -= downloaded_this_chunk
                    prog.downloaded = prog._bytes_at_start_of_chunk
                prog.current_chunk_idx = None
                prog.speed_mb_s = 0
            if prog:
                prog.status = "pending"
        
        job._workers.clear()
        job.status = "paused"
        if getattr(job, "_completion_event", None):
            job._completion_event.set()
        return {"status": "paused"}

    async def resume_job(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status not in ("paused", "waiting_reconnect", "failed"):
            return {"status": "not_paused", "current": job.status}
        if job.status == "failed" and "resumable" not in (job.error or "").lower():
            return {"status": "cannot_resume", "reason": "Job failed non-resumably"}

        job.status = "downloading"
        job.error = None
        
        # Respawn workers for all non-excluded interfaces (clear exclusion cooldown on explicit resume)
        spawned = 0
        for ip, prog in job.progress.items():
            if prog.status != "cancelled":
                prog.status = "pending"
                prog.consecutive_failures = 0
                prog._cooldown_until = 0.0
                prog.error = None
                iface_dict = {"ip_address": ip, "name": prog.name}
                num_workers = 3 if getattr(job, "boosted", False) else 1
                for idx in range(num_workers):
                    task_key = f"{ip}_{idx}" if num_workers > 1 else ip
                    task = asyncio.create_task(self._worker(job, iface_dict, job._queue, job._chunk_files))
                    job._workers[task_key] = task
                    spawned += 1
        
        return {"status": "resumed", "workers_spawned": spawned}

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        
        job.is_cancelled = True
        job.status = "failed"
        job.error = "User cancelled"
        
        # Stop all workers
        for ip, task in list(job._workers.items()):
            if not task.done():
                task.cancel()
        
        job.finished_at = time.time()
        if getattr(job, "_completion_event", None):
            job._completion_event.set()
        return {"status": "cancelled", "job_id": job_id}

    async def toggle_boost(self, job_id: str, active_interfaces: List[Dict[str, str]] = None) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        
        job.boosted = not getattr(job, 'boosted', False)
        
        if job.status == "downloading" and job._queue:
            if job.boosted:
                # 1. Add all active interfaces if they are not already in the job
                if active_interfaces:
                    for iface in active_interfaces:
                        ip = iface["ip_address"]
                        if ip not in job.progress or job.progress[ip].status in ("excluded", "cancelled"):
                            try:
                                await self.add_interface(job_id, iface)
                            except Exception as e:
                                print(f"[BOOST] Failed to add interface {ip}: {e}")
                
                # 2. Spawn extra workers for all active/pending interfaces
                for ip, prog in job.progress.items():
                    if prog.status not in ("excluded", "cancelled", "completed"):
                        iface_dict = {"ip_address": ip, "name": prog.name}
                        # We want 3 workers total per interface
                        for idx in range(1, 3):
                            task_key = f"{ip}_{idx}"
                            if task_key not in job._workers or job._workers[task_key].done():
                                task = asyncio.create_task(self._worker(job, iface_dict, job._queue, job._chunk_files))
                                job._workers[task_key] = task
            else:
                # Scale down: cancel extra workers (idx >= 1)
                for key in list(job._workers.keys()):
                    if "_" in key:
                        task = job._workers[key]
                        if not task.done():
                            task.cancel()
                        del job._workers[key]
                        
        return {"status": "success", "boosted": job.boosted}

    # ------------------------------------------------------------------
    # Parallel download with adaptive latency-aware chunking (Milestone 3)
    # ------------------------------------------------------------------
    async def _parallel_download(self, job: DownloadJob, interfaces: List[Dict[str, str]]) -> None:
        # Measure latency per interface
        latencies = {}
        for iface in interfaces:
            lat = await self._measure_latency(job.url, iface["ip_address"])
            latencies[iface["ip_address"]] = lat

        temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        job._completion_event = asyncio.Event()

        # Cleanup leftover uncommitted .tmp files from prior interrupted sessions
        for tmp_f in temp_dir.glob("chunk_*.tmp*"):
            try:
                tmp_f.unlink(missing_ok=True)
            except Exception:
                pass
        
        if job._ranges:
            ranges = job._ranges
        else:
            ranges = plan_adaptive_chunks(
                expected_size=job.expected_size,
                latencies=latencies,
                num_interfaces=len(interfaces),
            )
            job._ranges = ranges
            
        # Authoritative Chunk dictionary
        for r in ranges:
            idx, r_start, r_end = r
            if idx not in job.chunks:
                job.chunks[idx] = Chunk(chunk_id=idx, start=r_start, end=r_end)
            else:
                job.chunks[idx].start = r_start
                job.chunks[idx].end = r_end

        job._total_chunks = len(ranges)
        chunk_files: Dict[int, Path] = {
            r[0]: temp_dir / f"chunk_{r[0]:05d}.part" for r in ranges
        }
        job._chunk_files = chunk_files

        job._queue = asyncio.Queue()
        job.total_downloaded = 0
        
        # Only queue chunks that are not completely finished
        for r in ranges:
            chunk_idx, r_start, r_end = r
            chunk = job.chunks[chunk_idx]
            part_file = chunk_files[chunk_idx]
            expected = r_end - r_start + 1
            if part_file.exists():
                if part_file.stat().st_size == expected:
                    chunk.status = ChunkStatus.COMPLETE
                    chunk.completed_at = chunk.completed_at or time.time()
                    job.total_downloaded += expected
                    continue
                else:
                    # Incomplete/corrupted legacy .part file — remove cleanly
                    try:
                        part_file.unlink(missing_ok=True)
                    except Exception:
                        pass
            chunk.status = ChunkStatus.PENDING
            job._queue.put_nowait(chunk)

        # Initialize progress and spawn workers
        for iface in interfaces:
            ip = iface["ip_address"]
            lat = latencies.get(ip, 500.0)
            job.progress[ip] = InterfaceProgress(
                name=iface["name"], ip_address=ip,
                chunk_start=0, chunk_end=job.expected_size,
                status="pending", latency_ms=round(lat, 1),
            )
            num_workers = 3 if getattr(job, "boosted", False) else 1
            for idx in range(num_workers):
                task_key = f"{ip}_{idx}" if num_workers > 1 else ip
                task = asyncio.create_task(self._worker(job, iface, job._queue, chunk_files))
                job._workers[task_key] = task

        self._rebalance_weights(job)

        # Monitor loop: rebalance weights, detect disconnects, check completion
        rebalance_interval = config.get("WEIGHT_REBALANCE_INTERVAL_SECONDS")
        disconnect_timeout = config.get("DISCONNECT_DETECTION_TIMEOUT")
        last_rebalance = time.time()

        while True:
            if job.is_cancelled:
                break

            now = time.time()

            # Periodic weight rebalancing
            if now - last_rebalance >= rebalance_interval:
                self._rebalance_weights(job)
                last_rebalance = now

            # Disconnect detection
            for ip, prog in job.progress.items():
                if prog.status == "downloading":
                    if now - prog._last_progress_time > disconnect_timeout and prog.downloaded > 0:
                        prog.status = "disconnected"
                        prog.speed_mb_s = 0.0

            # Proactively restart dead workers when work remains in queue
            max_failures = config.get("MAX_CONSECUTIVE_FAILURES") or 3
            if not job._queue.empty():
                for ip, prog in job.progress.items():
                    active_tasks = [t for k, t in job._workers.items() if (k == ip or k.startswith(f"{ip}_")) and not t.done()]
                    if not active_tasks and prog.status != "cancelled":
                        if prog.status == "excluded" or prog.consecutive_failures >= max_failures:
                            if now >= prog._cooldown_until and prog._cooldown_until > 0:
                                # Exclusion cooldown expired: restore to degraded state for probe attempt
                                prog.status = "pending"
                                prog.consecutive_failures = max_failures - 1
                                prog.error = None
                            else:
                                if prog.status != "excluded":
                                    prog.status = "excluded"
                                    prog._cooldown_until = now + float(config.get("EXCLUDED_INTERFACE_COOLDOWN") or 60.0)
                                continue
                        if now < prog._cooldown_until:
                            continue

                        prog.status = "pending"
                        prog._slow_since = None
                        iface_dict = {"ip_address": ip, "name": prog.name}
                        num_workers = 3 if getattr(job, "boosted", False) else 1
                        for idx in range(num_workers):
                            task_key = f"{ip}_{idx}" if num_workers > 1 else ip
                            new_task = asyncio.create_task(
                                self._worker(job, iface_dict, job._queue, chunk_files)
                            )
                            job._workers[task_key] = new_task

            # Tail racing: launch duplicate racer on idle healthy interface for straggler chunks
            if config.get("ENABLE_TAIL_RACING", False) and job._queue.empty():
                incomplete_chunks = [c for c in job.chunks.values() if c.status in (ChunkStatus.DOWNLOADING, ChunkStatus.ASSIGNED)]
                if 0 < len(incomplete_chunks) <= int(config.get("TAIL_RACING_REMAINING_CHUNKS") or 2):
                    idle_healthy_ifaces = [
                        ip for ip, prog in job.progress.items()
                        if prog.health == "healthy" and prog.status in ("idle", "pending")
                    ]
                    for target_chunk in incomplete_chunks:
                        if not idle_healthy_ifaces:
                            break
                        if target_chunk.started_at and (now - target_chunk.started_at) > 2.0:
                            if not getattr(target_chunk, "_racing_cancel", None):
                                target_chunk._racing_cancel = threading.Event()
                            job._queue.put_nowait(target_chunk)
                            break

            # Check completion
            all_done = all(w.done() for w in job._workers.values())

            if job._queue.empty() and all_done:
                incomplete = [
                    c for c in job.chunks.values()
                    if c.status != ChunkStatus.COMPLETE or not chunk_files[c.chunk_id].exists() or chunk_files[c.chunk_id].stat().st_size != c.expected_bytes
                ]
                if not incomplete:
                    break
                else:
                    for c in incomplete:
                        c.status = ChunkStatus.PENDING
                        job._queue.put_nowait(c)

            # Bounded reconnect wait when all workers dead and work remains
            if not job._queue.empty() and all_done:
                all_inactive = all(
                    p.status in ("excluded", "disconnected", "cancelled", "paused_slow") or p.consecutive_failures >= max_failures
                    for p in job.progress.values()
                )
                if getattr(job, "_reconnect_wait_start", None) is None:
                    job._reconnect_wait_start = now

                reconnect_timeout = float(config.get("SINGLE_INTERFACE_RECONNECT_TIMEOUT") or 180.0)
                elapsed_wait = now - job._reconnect_wait_start

                if elapsed_wait < reconnect_timeout:
                    job.status = "waiting"
                    remaining_secs = int(reconnect_timeout - elapsed_wait)
                    job.error = f"All interfaces unavailable — waiting to reconnect ({remaining_secs}s remaining, resumable)"
                    await asyncio.sleep(0.2)
                    continue
                else:
                    job.status = "failed"
                    last_chunk_err = next((c.last_error for c in job.chunks.values() if c.last_error), None)
                    err_detail = f": {last_chunk_err}" if last_chunk_err else ""
                    job.error = f"All interfaces failed and reconnect timeout expired (failed, resumable){err_detail}"
                    raise Exception(job.error)

            # Event-based completion wakeup: unblock immediately when a chunk completes or worker finishes
            if getattr(job, "_completion_event", None):
                try:
                    await asyncio.wait_for(job._completion_event.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
                finally:
                    job._completion_event.clear()
            else:
                await asyncio.sleep(0.05)

        if not job.is_cancelled and job.status != "failed":
            # Important Safety Rule 16: Verify all chunks complete + exact byte count before merge
            for c in job.chunks.values():
                part_f = job._chunk_files[c.chunk_id]
                if not part_f.exists() or part_f.stat().st_size != c.expected_bytes:
                    raise ValueError(f"Chunk {c.chunk_id} missing or incomplete before merge")
                c.status = ChunkStatus.COMPLETE

            sorted_ranges = sorted(job._ranges, key=lambda r: r[1])
            sorted_files = [job._chunk_files[r[0]] for r in sorted_ranges]
            expected_chunk_sizes = [r[2] - r[1] + 1 for r in sorted_ranges]
            await merge_chunks(sorted_files, Path(job.output_path), job.expected_size, expected_chunk_sizes)
            out_file = Path(job.output_path)
            if not out_file.exists() or out_file.stat().st_size != job.expected_size:
                raise ValueError(f"Final file size verification failed: expected {job.expected_size}, got {out_file.stat().st_size if out_file.exists() else 0}")

        await cleanup_chunks(list(chunk_files.values()))
        # Clean up any leftover temporary files
        for tmp_f in temp_dir.glob("chunk_*.*"):
            try:
                tmp_f.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    async def _download_range(self, job: DownloadJob, interface: Dict[str, str],
                              byte_range: Tuple[int, int], output_file: Path, worker_id: uuid.UUID,
                              chunk: Optional[Chunk] = None) -> bool:
        start, end = byte_range
        if start > end:
            return True

        expected_bytes = end - start + 1
        part_file = output_file
        tmp_file = part_file.with_suffix(f".tmp_{worker_id.hex[:8]}")

        # Check if already complete
        if part_file.exists() and part_file.stat().st_size == expected_bytes:
            if chunk:
                chunk.status = ChunkStatus.COMPLETE
                chunk.completed_at = time.time()
            return True

        cancel_event = getattr(chunk, "_racing_cancel", None) if chunk else None

        tmp_file.unlink(missing_ok=True)

        progress = job.progress[interface["ip_address"]]
        progress.status = "downloading"
        progress.error = None
        started = time.perf_counter()
        headers = {
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
        }
        if is_strong_etag(job.etag):
            etag_val = job.etag
            headers["If-Range"] = f'"{etag_val}"' if not etag_val.startswith('"') else etag_val
        elif job.last_modified:
            headers["If-Range"] = job.last_modified

        def _do_chunk_download():
            self._download_with_requests(
                job, interface["ip_address"],
                job.final_url or job.url, tmp_file, "wb", headers, progress, started,
                worker_id, 0, expected_bytes, cancel_event=cancel_event
            )

            if cancel_event and cancel_event.is_set():
                tmp_file.unlink(missing_ok=True)
                return

            # Atomic Chunk Completion in worker thread:
            if not tmp_file.exists() or tmp_file.stat().st_size != expected_bytes:
                actual = tmp_file.stat().st_size if tmp_file.exists() else 0
                tmp_file.unlink(missing_ok=True)
                raise ValueError(f"Chunk byte count mismatch: expected {expected_bytes} bytes, got {actual} bytes")

        if not hasattr(job, "_active_threads"):
            job._active_threads = set()
        job._active_threads.add(worker_id)

        try:
            # File I/O and network streaming occur in worker thread pool
            await asyncio.to_thread(_do_chunk_download)

            if cancel_event and cancel_event.is_set():
                tmp_file.unlink(missing_ok=True)
                return False

            # Event-loop atomic claim before rename:
            # Guarantees exactly-once completion even if multiple racers finish simultaneously
            if chunk:
                if getattr(chunk, "_racing_claimed", False):
                    # Competitor already claimed this chunk; discard losing result
                    tmp_file.unlink(missing_ok=True)
                    return False
                chunk._racing_claimed = True
                if getattr(chunk, "_racing_cancel", None):
                    chunk._racing_cancel.set()

            def _do_commit():
                os.replace(tmp_file, part_file)
                _fsync_dir(part_file.parent)

            await asyncio.to_thread(_do_commit)

            if chunk:
                chunk.status = ChunkStatus.COMPLETE
                chunk.completed_at = time.time()
                chunk.last_error = None
            return True
        finally:
            if hasattr(job, "_active_threads"):
                job._active_threads.discard(worker_id)
            if tmp_file.exists():
                try:
                    tmp_file.unlink(missing_ok=True)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # HTTP session bound to a specific source IP
    # ------------------------------------------------------------------
    @staticmethod
    def _make_bound_session(source_ip: str) -> requests.Session:
        session = requests.Session()

        class BoundAdapter(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
                pool_kwargs["source_address"] = (source_ip, 0)
                super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

        session.mount("http://", BoundAdapter())
        session.mount("https://", BoundAdapter())
        return session

    @staticmethod
    def _http_fallback_url(url: str) -> Optional[str]:
        if url.lower().startswith("https://"):
            return "http://" + url[len("https://"):]
        return None

    # ------------------------------------------------------------------
    # Core byte-level download with sliding-window speed measurement
    # ------------------------------------------------------------------
    def _download_with_requests(self, job: DownloadJob, interface_ip: str,
                                url: str, output_file: Path, mode: str,
                                headers: Optional[Dict[str, str]],
                                progress: InterfaceProgress, started: float,
                                worker_id: uuid.UUID, downloaded_so_far: int = 0,
                                expected_bytes: Optional[int] = None,
                                cancel_event: Optional[threading.Event] = None) -> None:
        last_error: Optional[Exception] = None
        retry_attempts = int(config.get("RETRY_ATTEMPTS") or 3)
        io_size = int(config.get("CHUNK_IO_SIZE") or 64 * 1024)
        sample_interval = float(config.get("SPEED_SAMPLE_INTERVAL") or 0.1)
        window = float(config.get("SPEED_WINDOW_SECONDS") or 2.0)
        stall_timeout = float(config.get("STALL_TIMEOUT_SECONDS") or 10.0)
        request_timeout = float(config.get("REQUEST_TIMEOUT_SECONDS") or 30.0)
        limit = job.bandwidth_limits.get(interface_ip)
        if limit:
            print(f"[THROTTLE] interface {interface_ip} max_speed={limit} bytes/s")

        for attempt in range(1, retry_attempts + 1):
            chunk_downloaded = 0
            session = self._make_bound_session(interface_ip)
            try:
                request_urls = [url]
                if job.url != url:
                    request_urls.append(job.url)
                fallback_url = self._http_fallback_url(url)
                if fallback_url and fallback_url not in request_urls:
                    request_urls.append(fallback_url)

                response = None
                for candidate_url in request_urls:
                    try:
                        merged_headers = dict(BROWSER_HEADERS)
                        if headers:
                            merged_headers.update(headers)

                        # Strip Authorization on cross-host redirects/requests
                        if "Authorization" in merged_headers:
                            orig_host = urllib.parse.urlsplit(job.url).netloc
                            target_host = urllib.parse.urlsplit(candidate_url).netloc
                            if orig_host and target_host and orig_host.lower() != target_host.lower():
                                merged_headers.pop("Authorization", None)
                                merged_headers.pop("Proxy-Authorization", None)

                        # Timeout is (connect_timeout, read_timeout)
                        response = session.get(
                            candidate_url, headers=merged_headers,
                            stream=True, timeout=(request_timeout, stall_timeout),
                            allow_redirects=True, verify=False,
                        )

                        # Refuse protocol downgrades (HTTPS -> HTTP) across redirect history
                        for hist in response.history:
                            if hist.url.lower().startswith("https://") and response.url.lower().startswith("http://"):
                                raise ValueError(f"Insecure redirect rejected: protocol downgrade from HTTPS to HTTP ({redact_url(response.url)})")

                        # Re-resolution fallback on 403 / 410 (e.g. expired presigned CDN tokens)
                        if response.status_code in (403, 410) and candidate_url != job.url:
                            print(f"[RE-RESOLVE] Received {response.status_code} on {redact_url(candidate_url)}, falling back to authoritative URL {redact_url(job.url)}")
                            continue

                        break
                    except requests.RequestException as exc:
                        last_error = exc
                        response = None

                if response is None:
                    raise last_error or requests.ConnectionError("No response from server")

                with response:
                    response.raise_for_status()
                    if headers and "Range" in headers:
                        if response.status_code == 200:
                            raise OriginResourceModifiedError(
                                "Server responded with HTTP 200 to Range request, expected 206 Partial Content (origin resource modified or ranges unsupported)"
                            )
                        elif response.status_code != 206:
                            raise ValueError(
                                f"Server responded with HTTP {response.status_code} to Range request, expected 206 Partial Content"
                            )
                        cr = response.headers.get("Content-Range", "").strip()
                        if cr:
                            m = CONTENT_RANGE_RE.match(cr)
                            if not m:
                                raise ValueError(f"Malformed Content-Range header: {cr}")
                            r_start, r_end = int(m.group(1)), int(m.group(2))
                            req_range = headers.get("Range", "")
                            rm = re.match(r"^bytes=(\d+)-(\d+)$", req_range)
                            if rm:
                                exp_start, exp_end = int(rm.group(1)), int(rm.group(2))
                                if r_start != exp_start or r_end != exp_end:
                                    raise ValueError(
                                        f"Content-Range mismatch: requested {exp_start}-{exp_end}, received {r_start}-{r_end}"
                                    )

                        resp_etag = normalize_etag(response.headers.get("ETag"))
                        if job.etag and resp_etag and resp_etag != job.etag:
                            raise OriginResourceModifiedError(
                                f"Remote resource modified mid-flight: ETag changed from {job.etag} to {resp_etag}"
                            )
                        resp_last_modified = response.headers.get("Last-Modified") or None
                        if job.last_modified and resp_last_modified and resp_last_modified != job.last_modified:
                            raise OriginResourceModifiedError(
                                f"Remote resource modified mid-flight: Last-Modified changed from {job.last_modified} to {resp_last_modified}"
                            )

                    # Token-bucket throttle state
                    throttle_window_start = time.monotonic()
                    throttle_window_bytes = 0

                    last_progress_time = time.time()

                    with output_file.open(mode) as handle:
                        for data in response.iter_content(chunk_size=io_size):
                            if cancel_event and cancel_event.is_set():
                                handle.close()
                                output_file.unlink(missing_ok=True)
                                return
                            if job.is_cancelled:
                                raise ValueError("Job cancelled")
                            if job.status == "paused":
                                raise ValueError("Job paused")
                            if worker_id not in getattr(job, "_active_threads", set()):
                                raise ValueError("Worker thread cancelled/orphaned")

                            now = time.time()
                            if now - last_progress_time > stall_timeout:
                                raise StalledDownloadError(
                                    f"Worker on {interface_ip} stalled: no bytes received for {now - last_progress_time:.1f}s"
                                )

                            if not data:
                                continue

                            size = len(data)
                            if expected_bytes is not None and chunk_downloaded + size > expected_bytes:
                                raise ValueError(
                                    f"Server sent too many bytes: expected at most {expected_bytes}, received {chunk_downloaded + size}"
                                )

                            last_progress_time = now
                            handle.write(data)
                            chunk_downloaded += size
                            progress.downloaded += size
                            thread_lock = self._thread_locks.get(job.job_id)
                            if thread_lock:
                                with thread_lock:
                                    job.total_downloaded += size
                            else:
                                job.total_downloaded += size

                            # Sliding-window speed measurement
                            current_time = time.perf_counter()
                            if not progress._speed_samples or current_time - progress._speed_samples[-1][0] >= sample_interval:
                                progress._speed_samples.append((current_time, progress.downloaded))
                                while progress._speed_samples and current_time - progress._speed_samples[0][0] > window:
                                    progress._speed_samples.pop(0)
                                if len(progress._speed_samples) > 1:
                                    oldest_time, oldest_bytes = progress._speed_samples[0]
                                    time_diff = current_time - oldest_time
                                    if time_diff > 0:
                                        progress.speed_mb_s = ((progress.downloaded - oldest_bytes) / (1024 * 1024)) / time_diff

                            progress._last_progress_time = time.time()

                            # Token-bucket bandwidth limiting
                            if limit and limit > 0:
                                throttle_window_bytes += size
                                elapsed = time.monotonic() - throttle_window_start
                                expected_time = throttle_window_bytes / limit
                                if expected_time > elapsed:
                                    time.sleep(expected_time - elapsed)
                                if throttle_window_bytes >= limit:
                                    throttle_window_start = time.monotonic()
                                    throttle_window_bytes = 0

                        handle.flush()
                        # ponytail: per-chunk fsync ensures durability on crash; upgrade: batched periodic fsync or asynchronous io_uring if disk I/O becomes bottleneck on spinning HDDs
                        if config.get("ENABLE_CHUNK_FSYNC", True):
                            try:
                                os.fsync(handle.fileno())
                            except Exception:
                                pass

                    # Byte count validation
                    if expected_bytes is not None and chunk_downloaded != expected_bytes:
                        raise ValueError(
                            f"Byte count mismatch: expected {expected_bytes} bytes, received {chunk_downloaded} bytes"
                        )

                return
            except (requests.RequestException, ValueError, Exception) as exc:
                last_error = exc
                total_to_subtract = chunk_downloaded + downloaded_so_far
                if total_to_subtract > 0:
                    progress.downloaded -= total_to_subtract
                    thread_lock = self._thread_locks.get(job.job_id)
                    if thread_lock:
                        with thread_lock:
                            job.total_downloaded -= total_to_subtract
                    else:
                        job.total_downloaded -= total_to_subtract
                progress._speed_samples.clear()

                if is_non_retryable_error(exc) or job.is_cancelled or job.status == "paused":
                    raise

                if attempt < retry_attempts:
                    delay = calculate_backoff(attempt)
                    time.sleep(delay)
                else:
                    raise
            finally:
                session.close()
