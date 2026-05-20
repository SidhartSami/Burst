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

import config
from merger import cleanup_chunks, merge_chunks

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}


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
    boosted: bool = False
    _queue: Any = field(default=None, repr=False)
    _chunk_files: Any = field(default=None, repr=False)
    _workers: Any = field(default_factory=dict, repr=False)   # ip -> Task (or list of Tasks when boosted)
    _chunk_failures: Any = field(default_factory=dict, repr=False)
    _total_chunks: int = field(default=0, repr=False)
    _ranges: List[Tuple[int, int, int]] = field(default_factory=list, repr=False)

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
            "_ranges": self._ranges,
            "bandwidth_limits": self.bandwidth_limits,
            "boosted": self.boosted,
        }


def _build_connector(local_ip: str) -> aiohttp.TCPConnector:
    family = socket.AF_INET6 if ":" in local_ip else socket.AF_INET
    return aiohttp.TCPConnector(family=family, local_addr=(local_ip, 0))


async def analyze_url(url: str, preferred_ip: Optional[str] = None) -> Dict[str, Any]:
    connector = _build_connector(preferred_ip) if preferred_ip else None
    timeout = aiohttp.ClientTimeout(total=config.get("REQUEST_TIMEOUT_SECONDS"))
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=BROWSER_HEADERS) as session:
        # Try Stage 1: HEAD (Fastest)
        try:
            async with session.head(url, allow_redirects=True) as resp:
                if resp.status < 400:
                    total_size = int(resp.headers.get("Content-Length", "0"))
                    supports_ranges = "bytes" in resp.headers.get("Accept-Ranges", "").lower()
                    content_type = resp.headers.get("Content-Type", "application/octet-stream")
                    return {
                        "url": str(resp.url),
                        "content_length": total_size,
                        "supports_ranges": supports_ranges,
                        "content_type": content_type,
                    }
        except:
            pass

        # Try Stage 2: GET with Range (To check resume support)
        try:
            headers = dict(BROWSER_HEADERS)
            headers["Range"] = "bytes=0-0"
            async with session.get(url, allow_redirects=True, headers=headers) as resp:
                if resp.status < 400:
                    content_range = resp.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total_size = int(content_range.split("/")[-1])
                    else:
                        total_size = int(resp.headers.get("Content-Length", "0"))
                    
                    supports_ranges = resp.status == 206 or "bytes" in resp.headers.get("Accept-Ranges", "").lower()
                    content_type = resp.headers.get("Content-Type", "application/octet-stream")
                    return {
                        "url": str(resp.url),
                        "content_length": total_size,
                        "supports_ranges": supports_ranges,
                        "content_type": content_type,
                    }
        except:
            pass

        # Try Stage 3: Super-Basic GET (Final Fallback)
        async with session.get(url, allow_redirects=True) as resp:
            resp.raise_for_status()
            total_size = int(resp.headers.get("Content-Length", "0"))
            supports_ranges = "bytes" in resp.headers.get("Accept-Ranges", "").lower()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            return {
                "url": str(resp.url),
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
        )
        job._ranges = data.get("_ranges", [])
        
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
            analysis = await analyze_url(job.url, interfaces[0]["ip_address"])
            job.expected_size = int(analysis["content_length"])
            job.supports_ranges = bool(analysis["supports_ranges"])
            if job.expected_size <= 0:
                raise ValueError("Target server did not provide a valid Content-Length")

            if not job.supports_ranges:
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
            job.url, out_path, "wb", None, progress, started,
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
            prog.chunk_start = start
            prog.chunk_end = end
            prog.current_chunk_idx = chunk_idx
            prog._bytes_at_start_of_chunk = prog.downloaded
            output_file = chunk_files[chunk_idx]
            prog.status = "downloading"

            worker_id = uuid.uuid4()
            if not hasattr(job, "_active_threads"):
                job._active_threads = set()
            job._active_threads.add(worker_id)

            try:
                await self._download_range(job, iface, (start, end), output_file, worker_id)
                prog.error = None
                prog.consecutive_failures = 0
                prog.chunks_completed += 1
                prog._last_progress_time = time.time()
                queue.task_done()
            except asyncio.CancelledError:
                if not job.is_cancelled:
                    # Put it back so another interface picks it up
                    queue.put_nowait(item)
                raise
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
            prog.status = "completed"

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
                    job._chunk_files[new_idx] = temp_dir / f"chunk_{new_idx:05d}.part"
                    
                    # Put both back in queue
                    job._queue.put_nowait(range_a)
                    job._queue.put_nowait(range_b)
                    
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
            for r in job._ranges:
                if r[0] == prog.current_chunk_idx:
                    print(f"[REMOVE_IFACE] Returning chunk {prog.current_chunk_idx} to queue")
                    job._queue.put_nowait(r)
                    downloaded_this_chunk = prog.downloaded - prog._bytes_at_start_of_chunk
                    with self._thread_locks[job.job_id]:
                        job.total_downloaded -= downloaded_this_chunk
                    prog.downloaded = prog._bytes_at_start_of_chunk
                    break

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
                for r in job._ranges:
                    if r[0] == prog.current_chunk_idx:
                        print(f"[PAUSE] Returning chunk {prog.current_chunk_idx} to queue")
                        job._queue.put_nowait(r)
                        downloaded_this_chunk = prog.downloaded - prog._bytes_at_start_of_chunk
                        with self._thread_locks[job.job_id]:
                            job.total_downloaded -= downloaded_this_chunk
                        prog.downloaded = prog._bytes_at_start_of_chunk
                        break
                prog.current_chunk_idx = None
                prog.speed_mb_s = 0
            if prog:
                prog.status = "pending"
        
        job._workers.clear()
        job.status = "paused"
        return {"status": "paused"}

    async def resume_job(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status not in ("paused", "waiting_reconnect"):
            return {"status": "not_paused", "current": job.status}

        job.status = "downloading"
        job.error = None
        
        # Respawn workers for all non-excluded interfaces
        spawned = 0
        for ip, prog in job.progress.items():
            if prog.status not in ("excluded", "cancelled", "completed"):
                prog.status = "pending"
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
    # Parallel download with latency-aware chunking
    # ------------------------------------------------------------------
    async def _parallel_download(self, job: DownloadJob, interfaces: List[Dict[str, str]]) -> None:
        # Measure latency per interface
        latencies = {}
        for iface in interfaces:
            lat = await self._measure_latency(job.url, iface["ip_address"])
            latencies[iface["ip_address"]] = lat

        temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        if job._ranges:
            # Resuming from a loaded job
            ranges = job._ranges
        else:
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
            job._ranges = ranges
            
        job._total_chunks = len(ranges)
        chunk_files: Dict[int, Path] = {
            r[0]: temp_dir / f"chunk_{r[0]:05d}.part" for r in ranges
        }

        job._queue = asyncio.Queue()
        job._chunk_files = chunk_files
        
        # Reset total_downloaded to 0 and recalculate to avoid double counting
        job.total_downloaded = 0
        
        # Only queue chunks that are not completely finished
        for r in ranges:
            chunk_idx, r_start, r_end = r
            part_file = chunk_files[chunk_idx]
            if part_file.exists() and part_file.stat().st_size >= (r_end - r_start + 1):
                # Already complete, don't queue
                job.total_downloaded += (r_end - r_start + 1)
                continue
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

            # Proactively restart dead workers in recoverable states
            if not job._queue.empty():
                for ip, prog in job.progress.items():
                    active_tasks = [t for k, t in job._workers.items() if (k == ip or k.startswith(f"{ip}_")) and not t.done()]
                    if not active_tasks and prog.status in ("paused_slow", "disconnected", "pending"):
                        prog.status = "pending"
                        prog.consecutive_failures = 0
                        prog._slow_since = None
                        prog._cooldown_until = 0.0
                        iface_dict = {"ip_address": ip, "name": prog.name}
                        num_workers = 3 if getattr(job, "boosted", False) else 1
                        for idx in range(num_workers):
                            task_key = f"{ip}_{idx}" if num_workers > 1 else ip
                            new_task = asyncio.create_task(
                                self._worker(job, iface_dict, job._queue, chunk_files)
                            )
                            job._workers[task_key] = new_task

            # Check completion
            all_done = all(w.done() for w in job._workers.values())

            if job._queue.empty() and all_done:
                break

            if not job._queue.empty() and all_done:
                any_recoverable = any(
                    p.status not in ("cancelled", "completed")
                    for p in job.progress.values()
                )
                if any_recoverable:
                    if job.status != "waiting_reconnect":
                        print(f"[MONITOR] No active workers but work remains. Waiting for connection...")
                    job.status = "waiting_reconnect"
                    job.error = "All connections paused or lost — waiting to resume"
                    await asyncio.sleep(2)
                    continue
                else:
                    raise Exception("All interfaces failed to download the remaining chunks.")

            await asyncio.sleep(0.5)

        if not job.is_cancelled:
            sorted_files = [job._chunk_files[r[0]] for r in job._ranges]
            await merge_chunks(sorted_files, Path(job.output_path), job.expected_size)
        await cleanup_chunks(list(chunk_files.values()))
        # Clean up temp directory
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    async def _download_range(self, job: DownloadJob, interface: Dict[str, str],
                              byte_range: Tuple[int, int], output_file: Path, worker_id: uuid.UUID) -> None:
        start, end = byte_range
        if start > end:
            return
            
        mode = "wb"
        downloaded_so_far = 0
        if output_file.exists():
            downloaded_so_far = output_file.stat().st_size
            if downloaded_so_far >= (end - start + 1):
                # Chunk already fully downloaded
                job.total_downloaded += (end - start + 1)
                progress = job.progress[interface["ip_address"]]
                progress.downloaded += (end - start + 1)
                return
            if downloaded_so_far > 0:
                mode = "ab"
                start += downloaded_so_far
                # Pre-fill progress for what's already downloaded
                progress = job.progress[interface["ip_address"]]
                progress.downloaded += downloaded_so_far
                with self._thread_locks[job.job_id]:
                    job.total_downloaded += downloaded_so_far
                
        progress = job.progress[interface["ip_address"]]
        progress.status = "downloading"
        progress.error = None
        started = time.perf_counter()
        headers = {"Range": f"bytes={start}-{end}"}
        await asyncio.to_thread(
            self._download_with_requests, job, interface["ip_address"],
            job.url, output_file, mode, headers, progress, started,
            worker_id, downloaded_so_far
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
                                url: str, output_file: Path, mode: str,
                                headers: Optional[Dict[str, str]],
                                progress: InterfaceProgress, started: float,
                                worker_id: uuid.UUID, downloaded_so_far: int = 0) -> None:
        last_error: Optional[Exception] = None
        retry_attempts = config.get("RETRY_ATTEMPTS")
        retry_delay = config.get("RETRY_DELAY_SECONDS")
        io_size = config.get("CHUNK_IO_SIZE")
        sample_interval = config.get("SPEED_SAMPLE_INTERVAL")
        window = config.get("SPEED_WINDOW_SECONDS")
        limit = job.bandwidth_limits.get(interface_ip)
        if limit:
            print(f"[THROTTLE] interface {interface_ip} max_speed={limit} bytes/s")
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
                        # Merge range headers with browser headers
                        merged_headers = dict(BROWSER_HEADERS)
                        if headers:
                            merged_headers.update(headers)
                            
                        response = session.get(
                            candidate_url, headers=merged_headers,
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

                    # Token-bucket throttle state
                    throttle_window_start = time.monotonic()
                    throttle_window_bytes = 0

                    with output_file.open(mode) as handle:
                        for data in response.iter_content(chunk_size=io_size):
                            if job.is_cancelled:
                                raise ValueError("Job cancelled")
                            if job.status == "paused":
                                raise ValueError("Job paused")
                            if worker_id not in getattr(job, "_active_threads", set()):
                                raise ValueError("Worker thread cancelled/orphaned")
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

                            # Token-bucket bandwidth limiting
                            if limit and limit > 0:
                                throttle_window_bytes += size
                                elapsed = time.monotonic() - throttle_window_start
                                expected_time = throttle_window_bytes / limit
                                if expected_time > elapsed:
                                    time.sleep(expected_time - elapsed)
                                # Reset window periodically to avoid float drift
                                if throttle_window_bytes >= limit:
                                    throttle_window_start = time.monotonic()
                                    throttle_window_bytes = 0
                return
            except (requests.RequestException, ValueError, Exception) as exc:
                last_error = exc
                total_to_subtract = chunk_downloaded + downloaded_so_far
                if total_to_subtract > 0:
                    progress.downloaded -= total_to_subtract
                    with self._thread_locks[job.job_id]:
                        job.total_downloaded -= total_to_subtract
                progress._speed_samples.clear()
                if attempt < retry_attempts and not job.is_cancelled and job.status != "paused":
                    time.sleep(retry_delay)
                else:
                    raise
            finally:
                session.close()
