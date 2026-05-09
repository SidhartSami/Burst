"""
Burst — Download Engine.

Implements weighted bandwidth scoring, slow-interface auto-drop,
orphaned chunk reassignment, latency-aware chunk sizing,
and cross-interface retry routing.
"""
from __future__ import annotations

import asyncio
import ssl
import socket
import threading
import time
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

from merger import cleanup_chunks, merge_chunks
import config


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
    speed_mb_s: float = 0.0
    error: Optional[str] = None
    weight: float = 0.0              # 0.0–1.0 share of total bandwidth
    weight_percent: int = 0          # 0–100 for UI display
    latency_ms: float = 0.0
    chunks_completed: int = 0
    consecutive_failures: int = 0
    _speed_samples: Any = field(default_factory=list, repr=False)
    _slow_since: Optional[float] = field(default=None, repr=False)
    _last_progress_time: float = field(default_factory=time.time, repr=False)
    _cooldown_until: float = field(default=0.0, repr=False)


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
    _queue: Any = field(default=None, repr=False)
    _chunk_files: Any = field(default=None, repr=False)
    _workers: Any = field(default_factory=dict, repr=False)   # ip -> Task
    _chunk_failures: Any = field(default_factory=dict, repr=False)
    _total_chunks: int = field(default=0, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        iface_dict = {}
        for k, v in self.progress.items():
            d = {
                "name": v.name, "ip_address": v.ip_address,
                "chunk_start": v.chunk_start, "chunk_end": v.chunk_end,
                "downloaded": v.downloaded, "status": v.status,
                "speed_mb_s": v.speed_mb_s, "error": v.error,
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
        }


def _build_connector(local_ip: str) -> aiohttp.TCPConnector:
    family = socket.AF_INET6 if ":" in local_ip else socket.AF_INET
    return aiohttp.TCPConnector(family=family, local_addr=(local_ip, 0))


async def analyze_url(url: str, preferred_ip: Optional[str] = None) -> Dict[str, Any]:
    connector = _build_connector(preferred_ip) if preferred_ip else None
    timeout = aiohttp.ClientTimeout(total=config.get("REQUEST_TIMEOUT_SECONDS"))
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.head(url, allow_redirects=True) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("Content-Length", "0"))
            supports_ranges = "bytes" in response.headers.get("Accept-Ranges", "").lower()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            return {
                "url": str(response.url),
                "content_length": total_size,
                "supports_ranges": supports_ranges,
                "content_type": content_type,
            }


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
        job_id = str(uuid.uuid4())
        job = DownloadJob(job_id=job_id, url=url, output_path=output_path, bandwidth_limits=bandwidth_limits or {})
        self.jobs[job_id] = job
        self._locks[job_id] = asyncio.Lock()
        self._thread_locks[job_id] = threading.Lock()
        self._job_tasks[job_id] = asyncio.create_task(self._run_job(job, interfaces))
        return job

    # ------------------------------------------------------------------
    # Latency measurement
    # ------------------------------------------------------------------
    async def _measure_latency(self, url: str, interface_ip: str) -> float:
        """Measure round-trip latency (ms) to the download server via HEAD request."""
        try:
            connector = _build_connector(interface_ip)
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
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
            analysis = await analyze_url(job.url, interfaces[0]["ip_address"])
            job.expected_size = int(analysis["content_length"])
            job.supports_ranges = bool(analysis["supports_ranges"])
            if job.expected_size <= 0:
                raise ValueError("Target server did not provide a valid Content-Length")

            if not job.supports_ranges or len(interfaces) == 1:
                job.status = "downloading"
                await self._single_download(job, interfaces[0])
            else:
                job.status = "downloading"
                await self._parallel_download(job, interfaces)

            if job.is_cancelled:
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
        await asyncio.to_thread(
            self._download_with_requests, job, interface["ip_address"],
            job.url, out_path, None, progress, started,
        )
        if job.is_cancelled:
            progress.status = "cancelled"
        else:
            progress.status = "completed"

    # ------------------------------------------------------------------
    # Worker — grabs chunks from the shared queue
    # ------------------------------------------------------------------
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
                break

            # --- Check slow-speed gating ---
            # If paused, exit the worker so the monitor loop can restart us later
            if prog.status == "paused_slow":
                if paused_since is None:
                    paused_since = time.time()
                if time.time() - paused_since > 5.0:
                    # Exit worker — monitor loop will restart if conditions improve
                    break
                await asyncio.sleep(1)
                continue

            paused_since = None

            # --- Check cooldown (from cross-interface retry) ---
            if time.time() < prog._cooldown_until:
                # Exit and let monitor restart after cooldown
                break

            # --- Grab next chunk ---
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            chunk_idx, start, end = item
            output_file = chunk_files[chunk_idx]
            prog.status = "downloading"

            try:
                await self._download_range(job, iface, (start, end), output_file)
                prog.error = None
                prog.consecutive_failures = 0
                prog.chunks_completed += 1
                prog._last_progress_time = time.time()
                queue.task_done()
            except Exception as e:
                prog.consecutive_failures += 1
                job._chunk_failures[chunk_idx] = job._chunk_failures.get(chunk_idx, 0) + 1

                if job._chunk_failures[chunk_idx] > config.get("RETRY_ATTEMPTS") * 2:
                    job.status = "failed"
                    job.error = f"Chunk {chunk_idx} failed permanently: {e}"
                    job.is_cancelled = True
                    break

                # Cross-interface retry: route to another interface
                best_alt = self._find_best_alternate(job, ip)
                if best_alt:
                    job.retry_events.append(RetryEvent(
                        timestamp=time.time(), chunk_index=chunk_idx,
                        from_interface=ip, to_interface=best_alt,
                        reason=str(e)[:100],
                    ))
                    queue.put_nowait(item)
                    prog._cooldown_until = time.time() + cooldown_secs
                    prog.error = str(e)
                    prog.status = "paused_slow"
                    prog.speed_mb_s = 0.0
                else:
                    # No alternative — retry on self after delay
                    queue.put_nowait(item)
                    prog.error = str(e)
                    prog.speed_mb_s = 0.0
                    await asyncio.sleep(config.get("RETRY_DELAY_SECONDS"))

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
            prog.status = "completed"

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
        if not job or job.status not in ("downloading", "waiting_reconnect") or job._queue is None:
            raise ValueError("Job is not in a state to accept new interfaces")
        ip = interface["ip_address"]

        if ip in job.progress:
            # Interface already known — restart its worker if it died
            prog = job.progress[ip]
            existing_task = job._workers.get(ip)
            if existing_task and not existing_task.done():
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
        # cancelling its worker so its current chunk gets re-queued on retry
        if job._queue.empty():
            busiest_ip = None
            busiest_remaining = -1
            for other_ip, prog in job.progress.items():
                if other_ip == ip:
                    continue
                if prog.status == "downloading":
                    remaining = (prog.chunk_end - prog.chunk_start) - prog.downloaded
                    if remaining > busiest_remaining:
                        busiest_remaining = remaining
                        busiest_ip = other_ip
            if busiest_ip:
                # Cancel the busiest worker — the monitor loop will restart it
                # and it will re-grab from queue alongside the new worker
                old_task = job._workers.get(busiest_ip)
                if old_task and not old_task.done():
                    old_task.cancel()
                    try:
                        await old_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    # Reset state so monitor restarts it
                    job.progress[busiest_ip].status = "pending"
                    print(f"[ADD_IFACE] Cancelled worker for {busiest_ip} to redistribute work to {ip}")

        print(f"[ADD_IFACE] Spawning new download worker for {ip} (queue size: {job._queue.qsize()})")
        self._rebalance_weights(job)
        task = asyncio.create_task(self._worker(job, interface, job._queue, job._chunk_files))
        job._workers[ip] = task
        return {"spawned": True, "queue_size": job._queue.qsize()}

    # ------------------------------------------------------------------
    # Parallel download with latency-aware chunking
    # ------------------------------------------------------------------
    async def _parallel_download(self, job: DownloadJob, interfaces: List[Dict[str, str]]) -> None:
        # Measure latency per interface
        latencies = {}
        for iface in interfaces:
            lat = await self._measure_latency(job.url, iface["ip_address"])
            latencies[iface["ip_address"]] = lat

        # Compute per-interface chunk size based on latency
        min_lat = max(min(latencies.values()), 1.0)
        base = config.get("BASE_CHUNK_SIZE")
        min_cs = config.get("MIN_CHUNK_SIZE")
        max_cs = config.get("MAX_CHUNK_SIZE")

        # Use the average chunk size for queue generation
        avg_chunk = base
        if latencies:
            normalized = [min_lat / max(lat, 1.0) for lat in latencies.values()]
            sizes = [max(min_cs, min(max_cs, int(base * n))) for n in normalized]
            avg_chunk = max(min_cs, sum(sizes) // len(sizes))

        # Build chunk ranges
        ranges = []
        cursor = 0
        idx = 0
        while cursor < job.expected_size:
            end = min(cursor + avg_chunk - 1, job.expected_size - 1)
            ranges.append((idx, cursor, end))
            cursor = end + 1
            idx += 1

        job._total_chunks = len(ranges)
        temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        chunk_files: Dict[int, Path] = {
            i: temp_dir / f"chunk_{i:05d}.part" for i in range(len(ranges))
        }

        job._queue = asyncio.Queue()
        job._chunk_files = chunk_files
        for r in ranges:
            job._queue.put_nowait(r)

        # Initialize progress and spawn workers
        for iface in interfaces:
            ip = iface["ip_address"]
            lat = latencies.get(ip, 500.0)
            job.progress[ip] = InterfaceProgress(
                name=iface["name"], ip_address=ip,
                chunk_start=0, chunk_end=job.expected_size,
                status="pending", latency_ms=round(lat, 1),
            )
            task = asyncio.create_task(self._worker(job, iface, job._queue, chunk_files))
            job._workers[ip] = task

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

            # Proactively restart dead workers in recoverable states
            if not job._queue.empty():
                for ip, prog in job.progress.items():
                    task = job._workers.get(ip)
                    if task and task.done() and prog.status in ("paused_slow", "disconnected", "pending"):
                        prog.status = "pending"
                        prog.consecutive_failures = 0
                        prog._slow_since = None
                        prog._cooldown_until = 0.0
                        iface_dict = {"ip_address": ip, "name": prog.name}
                        new_task = asyncio.create_task(
                            self._worker(job, iface_dict, job._queue, chunk_files)
                        )
                        job._workers[ip] = new_task

            # Check completion
            all_done = all(w.done() for w in job._workers.values())

            if job._queue.empty() and all_done:
                break

            if not job._queue.empty() and all_done:
                any_recoverable = any(
                    p.status not in ("excluded", "cancelled", "completed")
                    for p in job.progress.values()
                )
                if any_recoverable:
                    job.status = "waiting_reconnect"
                    job.error = "All connections lost — waiting to reconnect"
                    await asyncio.sleep(2)
                    continue
                else:
                    raise Exception("All interfaces failed to download the remaining chunks.")

            await asyncio.sleep(0.5)

        if not job.is_cancelled:
            sorted_files = [chunk_files[i] for i in range(len(ranges))]
            await merge_chunks(sorted_files, Path(job.output_path), job.expected_size)
        await cleanup_chunks(list(chunk_files.values()))
        # Clean up temp directory
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    async def _download_range(self, job: DownloadJob, interface: Dict[str, str],
                              byte_range: Tuple[int, int], output_file: Path) -> None:
        start, end = byte_range
        if start > end:
            return
        progress = job.progress[interface["ip_address"]]
        progress.status = "downloading"
        progress.error = None
        started = time.perf_counter()
        headers = {"Range": f"bytes={start}-{end}"}
        await asyncio.to_thread(
            self._download_with_requests, job, interface["ip_address"],
            job.url, output_file, headers, progress, started,
        )

    # ------------------------------------------------------------------
    # HTTP session bound to a specific source IP
    # ------------------------------------------------------------------
    @staticmethod
    def _make_bound_session(source_ip: str) -> requests.Session:
        session = requests.Session()
        insecure_ssl_context = ssl.create_default_context()
        insecure_ssl_context.check_hostname = False
        insecure_ssl_context.verify_mode = ssl.CERT_NONE

        class BoundAdapter(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
                pool_kwargs["ssl_context"] = insecure_ssl_context
                pool_kwargs["source_address"] = (source_ip, 0)
                self.poolmanager = PoolManager(
                    num_pools=connections, maxsize=maxsize,
                    block=block, **pool_kwargs,
                )

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
                                url: str, output_file: Path,
                                headers: Optional[Dict[str, str]],
                                progress: InterfaceProgress, started: float) -> None:
        last_error: Optional[Exception] = None
        retry_attempts = config.get("RETRY_ATTEMPTS")
        retry_delay = config.get("RETRY_DELAY_SECONDS")
        io_size = config.get("CHUNK_IO_SIZE")
        sample_interval = config.get("SPEED_SAMPLE_INTERVAL")
        window = config.get("SPEED_WINDOW_SECONDS")
        limit = job.bandwidth_limits.get(interface_ip)
        start_time = time.time()
        bytes_read = 0

        for attempt in range(1, retry_attempts + 1):
            chunk_downloaded = 0
            session = self._make_bound_session(interface_ip)
            try:
                request_urls = [url]
                fallback_url = self._http_fallback_url(url)
                if fallback_url:
                    request_urls.append(fallback_url)

                response = None
                for candidate_url in request_urls:
                    try:
                        response = session.get(
                            candidate_url, headers=headers or {},
                            stream=True, timeout=30,
                            allow_redirects=True, verify=False,
                        )
                        break
                    except requests.RequestException as exc:
                        last_error = exc
                        response = None

                if response is None:
                    raise last_error or requests.ConnectionError("No response from server")

                with response:
                    response.raise_for_status()
                    if headers and response.status_code not in (200, 206):
                        raise ValueError(f"Range download rejected ({response.status_code})")

                    with output_file.open("wb") as handle:
                        for data in response.iter_content(chunk_size=io_size):
                            if job.is_cancelled:
                                raise ValueError("Job cancelled")
                            if not data:
                                continue
                            handle.write(data)
                            size = len(data)
                            chunk_downloaded += size
                            progress.downloaded += size
                            with self._thread_locks[job.job_id]:
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
                return
            except (requests.RequestException, ValueError, Exception) as exc:
                last_error = exc
                if chunk_downloaded > 0:
                    progress.downloaded -= chunk_downloaded
                    with self._thread_locks[job.job_id]:
                        job.total_downloaded -= chunk_downloaded
                progress._speed_samples.clear()
                if attempt < retry_attempts and not job.is_cancelled:
                    time.sleep(retry_delay)
                else:
                    raise
            finally:
                session.close()
