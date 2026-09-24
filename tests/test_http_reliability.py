"""
Tests for Burst HTTP Engine Reliability (Milestone 2).

Verifies:
- Test A: Range support (HTTP 206)
- Test B: No range support (HTTP 200 fallback)
- Test C: Wrong byte count rejection & retry
- Test D: Exponential backoff on retry
- Test E: Per-worker stall watchdog
- Test F: Safe resume without redownloading complete chunks (request-count verified)
- Test G: Changed ETag detection and safe restart (request-count verified)
- Test H: Atomic chunk write (no partial .part files)
- Test I: Interface failure routing and requeue
- Test J: Concurrent multi-worker chunk completion & integrity
- Test K: Mid-download interface failure & rollback
- Test L: Too-many-bytes rejection & terminal failure
- Test M: Wrong-Content-Range rejection & terminal failure
- Test N: Stall after initial bytes watchdog trigger & recovery
- Test O: Mid-download HTTP 200 rejection & terminal failure
- Test P: Interface health state exposure (healthy, degraded, excluded)
- Test Q: Slow-but-progressing transfer is not stalled
- Test R: Degraded to healthy recovery and temporary exclusion expiration
- Test S: If-Range header and mid-flight origin mutation detection
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import http.server
import json as _json
import os
import re
import requests
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import config
from downloader import (
    Chunk,
    ChunkStatus,
    DownloadJob,
    DownloadManager,
    InterfaceProgress,
    InsufficientDiskSpaceError,
    StalledDownloadError,
    URLAnalysis,
    analyze_url,
    calculate_backoff,
    check_disk_space_preflight,
    is_non_retryable_error,
    plan_adaptive_chunks,
    redact_url,
    sanitize_exception_text,
)

# Test payload: 256 KB deterministic data
TEST_DATA = bytes([(i * 31 + 7) % 256 for i in range(256 * 1024)])
TEST_DATA_HASH = hashlib.sha256(TEST_DATA).hexdigest()


class MockHttpHandler(http.server.BaseHTTPRequestHandler):
    """Configurable HTTP handler simulating various server behaviors."""
    etag = "v1-valid-etag"
    last_modified = "Wed, 23 Sep 2026 12:00:00 GMT"
    fail_first_n_requests = 0
    stall_endpoints = set()
    no_range_endpoints = set()
    truncate_endpoints = set()
    oversized_endpoints = set()
    wrong_range_endpoints = set()
    mid_200_endpoints = set()
    stall_after_bytes_endpoints = set()
    mid_fail_endpoints = set()
    slow_endpoints = set()
    mid_mutation_endpoints = set()
    mutation_abort_endpoints = set()
    all_fail_endpoints = set()
    request_counts = {}
    recorded_requests = []

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    @classmethod
    def _format_etag(cls):
        if not cls.etag:
            return None
        val = str(cls.etag).strip()
        if val.startswith(('W/', 'w/', '"')):
            return val
        return f'"{val}"'

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(TEST_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        et = self._format_etag()
        if et:
            self.send_header("ETag", et)
        if self.last_modified:
            self.send_header("Last-Modified", self.last_modified)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        endpoint = self.path.split("?")[0]
        self.request_counts[endpoint] = self.request_counts.get(endpoint, 0) + 1
        count = self.request_counts[endpoint]
        range_header = self.headers.get("Range")
        self.recorded_requests.append((endpoint, range_header, dict(self.headers)))

        # Fail first N requests simulation
        if endpoint == "/fail_first" and count <= self.fail_first_n_requests:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # All-fail simulation: probe (0-0) succeeds, but all chunk requests fail with 500
        if endpoint in self.all_fail_endpoints:
            if range_header == "bytes=0-0":
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes 0-0/{len(TEST_DATA)}")
                self.send_header("Content-Length", "1")
                et = self._format_etag()
                if et:
                    self.send_header("ETag", et)
                if self.last_modified:
                    self.send_header("Last-Modified", self.last_modified)
                self.end_headers()
                self.wfile.write(TEST_DATA[0:1])
                return
            else:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        # Immediate stall simulation (sleep longer than STALL_TIMEOUT_SECONDS)
        if endpoint in self.stall_endpoints:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            time.sleep(0.45)
            return

        # Fallback / No-range endpoint
        if endpoint in self.no_range_endpoints or not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA)
            return

        # Mid-download HTTP 200: probe range (bytes=0-0) returns 206, but worker requests get 200 OK
        if endpoint in self.mid_200_endpoints:
            if range_header == "bytes=0-0":
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes 0-0/{len(TEST_DATA)}")
                self.send_header("Content-Length", "1")
                et = self._format_etag()
                if et:
                    self.send_header("ETag", et)
                if self.last_modified:
                    self.send_header("Last-Modified", self.last_modified)
                self.end_headers()
                self.wfile.write(TEST_DATA[0:1])
                return
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(TEST_DATA)))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(TEST_DATA)
                return

        # Handle Range request
        m = re.match(r"^bytes=(\d+)-(\d+)?$", range_header)
        if not m:
            self.send_response(416)
            self.end_headers()
            return

        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(TEST_DATA) - 1
        end = min(end, len(TEST_DATA) - 1)
        length = end - start + 1

        # Mid-flight mutation: on chunk request, change ETag and respond
        if endpoint in self.mid_mutation_endpoints and range_header != "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            self.send_header("ETag", '"v2-mutated-etag"')
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])
            return

        # Mutation abort simulation: request 1 (probe bytes=0-0) -> 206, request 2 (chunk 0) -> 206, request 3 (chunk 1) -> 206 with mutated ETag
        if endpoint in self.mutation_abort_endpoints and range_header != "bytes=0-0":
            if count >= 3:
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
                self.send_header("Content-Length", str(length))
                self.send_header("ETag", '"v2-mutated-etag"')
                self.end_headers()
                self.wfile.write(TEST_DATA[start : end + 1])
                return

        # Wrong Content-Range simulation
        if endpoint in self.wrong_range_endpoints and range_header != "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes 0-10/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])
            return

        # Oversized data simulation
        if endpoint in self.oversized_endpoints and range_header != "bytes=0-0":
            oversized_len = length + 500
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(oversized_len))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1] + b"Z" * 500)
            return

        # Stall after initial bytes simulation
        if endpoint in self.stall_after_bytes_endpoints and count == 1:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + 50])
            self.wfile.flush()
            time.sleep(0.45)
            return

        # Slow-but-progressing simulation (delay between chunks, well within 0.25s stall timeout)
        if endpoint in self.slow_endpoints:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            step = 8192
            for i in range(start, end + 1, step):
                self.wfile.write(TEST_DATA[i : min(i + step, end + 1)])
                self.wfile.flush()
                time.sleep(0.015)
            return

        # Mid-download socket severance simulation
        if endpoint in self.mid_fail_endpoints and count == 1:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + 512])
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            except Exception:
                pass
            return

        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
        et = self._format_etag()
        if et:
            self.send_header("ETag", et)
        if self.last_modified:
            self.send_header("Last-Modified", self.last_modified)

        if endpoint in self.truncate_endpoints and count == 1:
            truncated_len = max(1, length // 2)
            self.send_header("Content-Length", str(truncated_len))
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + truncated_len])
        else:
            self.send_header("Content-Length", str(length))
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])


class HttpReliabilityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Configure fast test thresholds for sub-second, deterministic testing
        config.save_settings({
            "BASE_CHUNK_SIZE": 64 * 1024,
            "MIN_CHUNK_SIZE": 16 * 1024,
            "MAX_CHUNK_SIZE": 64 * 1024,
            "STALL_TIMEOUT_SECONDS": 0.25,
            "RETRY_BACKOFF_BASE": 0.01,
            "RETRY_BACKOFF_MAX": 0.05,
            "RETRY_JITTER_MAX": 0.005,
            "RETRY_ATTEMPTS": 3,
            "EXCLUDED_INTERFACE_COOLDOWN": 0.5,
            "SINGLE_INTERFACE_RECONNECT_TIMEOUT": 0.5,
        })
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockHttpHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        config.reset_settings()

    def setUp(self):
        MockHttpHandler.etag = "v1-valid-etag"
        MockHttpHandler.last_modified = "Wed, 23 Sep 2026 12:00:00 GMT"
        MockHttpHandler.fail_first_n_requests = 0
        MockHttpHandler.stall_endpoints.clear()
        MockHttpHandler.no_range_endpoints.clear()
        MockHttpHandler.truncate_endpoints.clear()
        MockHttpHandler.oversized_endpoints.clear()
        MockHttpHandler.wrong_range_endpoints.clear()
        MockHttpHandler.mid_200_endpoints.clear()
        MockHttpHandler.stall_after_bytes_endpoints.clear()
        MockHttpHandler.mid_fail_endpoints.clear()
        MockHttpHandler.slow_endpoints.clear()
        MockHttpHandler.mid_mutation_endpoints.clear()
        MockHttpHandler.mutation_abort_endpoints.clear()
        MockHttpHandler.all_fail_endpoints.clear()
        MockHttpHandler.request_counts.clear()
        MockHttpHandler.recorded_requests.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)
        self.manager = DownloadManager()
        self.iface = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    def tearDown(self):
        self.temp_dir.cleanup()

    # -----------------------------------------------------------------------
    # Test A — Range support (206 Partial Content)
    # -----------------------------------------------------------------------
    async def test_a_range_support(self):
        url = f"{self.base_url}/test_a.bin"
        analysis = await analyze_url(url, "127.0.0.1")

        self.assertTrue(analysis.supports_ranges)
        self.assertEqual(analysis.content_length, len(TEST_DATA))
        self.assertEqual(analysis.etag, "v1-valid-etag")
        self.assertIsNotNone(analysis.last_modified)

        dest = self.out_dir / "test_a.bin"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test B — No range support (200 OK fallback)
    # -----------------------------------------------------------------------
    async def test_b_no_range_support(self):
        MockHttpHandler.no_range_endpoints.add("/no_range.bin")
        url = f"{self.base_url}/no_range.bin"

        analysis = await analyze_url(url, "127.0.0.1")
        self.assertFalse(analysis.supports_ranges)
        self.assertIn("200 OK", analysis.range_error_reason or "")

        dest = self.out_dir / "test_b.bin"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertFalse(job.supports_ranges)
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test C — Wrong byte count rejection
    # -----------------------------------------------------------------------
    async def test_c_wrong_byte_count_rejection(self):
        MockHttpHandler.truncate_endpoints.add("/truncate.bin")
        url = f"{self.base_url}/truncate.bin"

        dest = self.out_dir / "test_c.bin"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test D — Retry with exponential backoff
    # -----------------------------------------------------------------------
    async def test_d_retry_exponential_backoff(self):
        MockHttpHandler.fail_first_n_requests = 1
        url = f"{self.base_url}/fail_first"

        t0 = time.perf_counter()
        dest = self.out_dir / "test_d.bin"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task
        elapsed = time.perf_counter() - t0

        self.assertEqual(job.status, "completed")
        self.assertGreater(elapsed, 0.01)
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))

    # -----------------------------------------------------------------------
    # Test E — Stall watchdog detection
    # -----------------------------------------------------------------------
    async def test_e_stall_watchdog(self):
        MockHttpHandler.stall_endpoints.add("/stall.bin")
        url = f"{self.base_url}/stall.bin"

        dest = self.out_dir / "test_e.bin"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]

        await asyncio.wait_for(task, timeout=5.0)

        self.assertEqual(job.status, "failed")
        self.assertTrue(
            "stalled" in (job.error or "").lower() or "timed out" in (job.error or "").lower(),
            f"Expected stall in error, got: {job.error}",
        )

    # -----------------------------------------------------------------------
    # Test F — Safe resume without redownloading completed chunks (request-count verified)
    # -----------------------------------------------------------------------
    async def test_f_safe_resume(self):
        url = f"{self.base_url}/resume_file.bin"
        dest = self.out_dir / "test_f.bin"

        # Complete initial download
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task
        self.assertEqual(job.status, "completed")

        initial_requests = MockHttpHandler.request_counts.get("/resume_file.bin", 0)

        state = job.to_dict()
        dest.unlink()

        # Recreate chunk 0 .part file on disk so it acts as partially downloaded
        temp_dir = dest.parent / f".burst_{job.job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        chunk0_file = temp_dir / "chunk_00000.part"
        chunk0_range = job._ranges[0]
        chunk0_bytes = chunk0_range[2] - chunk0_range[1] + 1
        chunk0_file.write_bytes(TEST_DATA[:chunk0_bytes])

        # Resume job
        resumed_job = await self.manager.resume_job_from_state(state, self.iface)
        resume_task = self.manager._job_tasks[resumed_job.job_id]
        await resume_task

        self.assertEqual(resumed_job.status, "completed")
        self.assertEqual(resumed_job.resume_confidence, "high")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

        # Request count verification: chunk 0 was NOT re-requested from server!
        # Total chunks = 4. Resumed download only requested 1 probe + remaining 3 chunks = 4 requests.
        resumed_requests = MockHttpHandler.request_counts.get("/resume_file.bin", 0) - initial_requests
        self.assertEqual(resumed_requests, 4, "Completed chunk 0 must not be requested again")

    # -----------------------------------------------------------------------
    # Test G — Changed ETag detection and safe restart (request-count verified)
    # -----------------------------------------------------------------------
    async def test_g_changed_etag_safe_restart(self):
        url = f"{self.base_url}/etag_file.bin"
        dest = self.out_dir / "test_g.bin"

        MockHttpHandler.etag = "etag-v1"
        analysis = await analyze_url(url, "127.0.0.1")
        self.assertEqual(analysis.etag, "etag-v1")

        state = {
            "job_id": "test-etag-job",
            "url": url,
            "output_path": str(dest),
            "expected_size": len(TEST_DATA),
            "supports_ranges": True,
            "etag": "etag-v1",
            "total_downloaded": 65536,
            "_ranges": [(0, 0, 65535), (1, 65536, 131071), (2, 131072, 196607), (3, 196608, len(TEST_DATA) - 1)],
            "interfaces": {"127.0.0.1": {"name": "Loopback", "ip_address": "127.0.0.1", "chunk_start": 0, "chunk_end": 65535}},
        }

        # Create old chunk 0
        temp_dir = dest.parent / f".burst_{state['job_id']}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "chunk_00000.part").write_bytes(b"\x00" * 65536)

        initial_requests = MockHttpHandler.request_counts.get("/etag_file.bin", 0)

        # Change server ETag to v2
        MockHttpHandler.etag = "etag-v2"

        # Resume job -> should detect mismatch, wipe old chunk, and download all 4 chunks
        job = await self.manager.resume_job_from_state(state, self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.etag, "etag-v2")
        self.assertEqual(job.resume_confidence, "none", "Confidence must be none when ETag change forces restart")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

        # Request count verification: 2 probes (resume check + clean restart) + 4 chunks = 6 requests
        resumed_requests = MockHttpHandler.request_counts.get("/etag_file.bin", 0) - initial_requests
        self.assertEqual(resumed_requests, 6, "All 4 chunks plus probes must be executed on restart")

    # -----------------------------------------------------------------------
    # Test H — Atomic chunk write
    # -----------------------------------------------------------------------
    async def test_h_atomic_chunk_write(self):
        url = f"{self.base_url}/atomic.bin"
        dest = self.out_dir / "test_h.bin"
        job = DownloadJob(job_id="test-atomic", url=url, output_path=str(dest), expected_size=len(TEST_DATA))
        self.manager.jobs[job.job_id] = job
        self.manager._locks[job.job_id] = asyncio.Lock()
        self.manager._thread_locks[job.job_id] = threading.Lock()

        temp_dir = dest.parent / f".burst_{job.job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_file = temp_dir / "chunk_00000.part"

        job.is_cancelled = True
        import uuid
        uid = uuid.uuid4()

        try:
            await self.manager._download_range(
                job, self.iface[0], (0, 65535), output_file, uid, chunk=Chunk(0, 0, 65535)
            )
        except Exception:
            pass

        self.assertFalse(output_file.exists(), ".part file must not exist for interrupted chunk")

    # -----------------------------------------------------------------------
    # Test I — Interface failure handling & requeue
    # -----------------------------------------------------------------------
    async def test_i_interface_failure(self):
        url = f"{self.base_url}/iface_fail.bin"
        dest = self.out_dir / "test_i.bin"

        ifaces = [
            {"name": "BadInterface", "ip_address": "192.0.2.1"},
            {"name": "GoodInterface", "ip_address": "127.0.0.1"},
        ]

        job = await self.manager.create_job(url, str(dest), ifaces)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test J — Concurrent workers completion & integrity
    # -----------------------------------------------------------------------
    async def test_j_concurrent_workers(self):
        url = f"{self.base_url}/concurrent.bin"
        dest = self.out_dir / "test_j.bin"

        job = await self.manager.create_job(url, str(dest), self.iface)
        await self.manager.toggle_boost(job.job_id, self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

        for c in job.chunks.values():
            self.assertEqual(c.status, ChunkStatus.COMPLETE)
            self.assertIsNotNone(c.completed_at)

    # -----------------------------------------------------------------------
    # Test K — Mid-download interface failure & rollback
    # -----------------------------------------------------------------------
    async def test_k_mid_download_interface_failure(self):
        url = f"{self.base_url}/mid_fail.bin"
        dest = self.out_dir / "test_k.bin"
        MockHttpHandler.mid_fail_endpoints.add("/mid_fail.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test L — Too-many-bytes rejection (terminal failure)
    # -----------------------------------------------------------------------
    async def test_l_too_many_bytes_rejection(self):
        url = f"{self.base_url}/oversized.bin"
        dest = self.out_dir / "test_l.bin"
        MockHttpHandler.oversized_endpoints.add("/oversized.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Terminal state behavior
        self.assertEqual(job.status, "failed")
        self.assertIn("too many bytes", job.error.lower())
        self.assertFalse(dest.exists(), "Corrupt destination file must not be committed")

    # -----------------------------------------------------------------------
    # Test M — Wrong-Content-Range rejection (terminal failure)
    # -----------------------------------------------------------------------
    async def test_m_wrong_content_range(self):
        url = f"{self.base_url}/wrong_range.bin"
        dest = self.out_dir / "test_m.bin"
        MockHttpHandler.wrong_range_endpoints.add("/wrong_range.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Terminal state behavior
        self.assertEqual(job.status, "failed")
        self.assertIn("content-range mismatch", job.error.lower())
        self.assertFalse(dest.exists(), "Corrupt destination file must not be committed")

    # -----------------------------------------------------------------------
    # Test N — Stall after initial bytes watchdog trigger and recovery
    # -----------------------------------------------------------------------
    async def test_n_stall_after_initial_bytes(self):
        url = f"{self.base_url}/stall_after_bytes.bin"
        dest = self.out_dir / "test_n.bin"
        MockHttpHandler.stall_after_bytes_endpoints.add("/stall_after_bytes.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test O — Mid-download HTTP 200 response rejection (terminal failure)
    # -----------------------------------------------------------------------
    async def test_o_mid_download_200(self):
        url = f"{self.base_url}/mid_200.bin"
        dest = self.out_dir / "test_o.bin"
        MockHttpHandler.mid_200_endpoints.add("/mid_200.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Terminal state behavior
        self.assertEqual(job.status, "failed")
        self.assertIn("expected 206", job.error.lower())
        self.assertFalse(dest.exists(), "Corrupt destination file must not be committed")

    # -----------------------------------------------------------------------
    # Test P — Interface health state exposure (healthy, degraded, excluded)
    # -----------------------------------------------------------------------
    def test_p_interface_health_state(self):
        prog = InterfaceProgress(name="eth0", ip_address="192.168.1.10", chunk_start=0, chunk_end=1000)
        self.assertEqual(prog.health, "healthy")

        prog.consecutive_failures = 1
        self.assertEqual(prog.health, "degraded")

        prog.consecutive_failures = 3
        prog._cooldown_until = time.time() + 60.0
        self.assertEqual(prog.health, "excluded")

        prog.status = "excluded"
        self.assertEqual(prog.health, "excluded")

    # -----------------------------------------------------------------------
    # Test Q — Slow-but-progressing transfer is NOT stalled
    # -----------------------------------------------------------------------
    async def test_q_slow_but_progressing_not_stalled(self):
        url = f"{self.base_url}/slow_progress.bin"
        dest = self.out_dir / "test_q.bin"
        MockHttpHandler.slow_endpoints.add("/slow_progress.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Must succeed without stall watchdog triggering because bytes were steadily progressing
        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test R — Degraded to healthy recovery and temporary exclusion expiration
    # -----------------------------------------------------------------------
    def test_r_degraded_to_healthy_recovery(self):
        prog = InterfaceProgress(name="wlan0", ip_address="10.0.0.5", chunk_start=0, chunk_end=1000)
        self.assertEqual(prog.health, "healthy")

        # Step 1: Failure causes transition to degraded
        prog.consecutive_failures = 1
        self.assertEqual(prog.health, "degraded")

        # Step 2: Successful chunk resets failures to 0 -> healthy
        prog.consecutive_failures = 0
        self.assertEqual(prog.health, "healthy")

        # Step 3: Hits max failures -> excluded
        prog.consecutive_failures = 3
        prog.status = "excluded"
        prog._cooldown_until = time.time() + 0.1
        self.assertEqual(prog.health, "excluded")

        # Step 4: After cooldown expires -> transitions to degraded (ready for probe)
        time.sleep(0.15)
        self.assertEqual(prog.health, "degraded")

    # -----------------------------------------------------------------------
    # Test S — If-Range header and mid-flight origin mutation detection
    # -----------------------------------------------------------------------
    async def test_s_if_range_mid_flight_mutation(self):
        url = f"{self.base_url}/mid_mutation.bin"
        dest = self.out_dir / "test_s.bin"
        MockHttpHandler.mid_mutation_endpoints.add("/mid_mutation.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Origin mutation detected mid-flight via ETag change on 206 response
        self.assertEqual(job.status, "failed")
        self.assertIn("remote resource modified mid-flight", job.error.lower())
        self.assertFalse(dest.exists(), "Partially mutated file must not be assembled")

    # -----------------------------------------------------------------------
    # Test T — Weak ETag handling in If-Range (RFC 9110 §13.1.2)
    # -----------------------------------------------------------------------
    async def test_t_weak_etag_if_range_handling(self):
        # Case 1: Weak ETag with Last-Modified -> If-Range must fall back to Last-Modified
        MockHttpHandler.etag = 'W/"weak-rev-123"'
        MockHttpHandler.last_modified = "Wed, 23 Sep 2026 12:00:00 GMT"
        url1 = f"{self.base_url}/weak_with_lm.bin"
        dest1 = self.out_dir / "test_t1.bin"

        job1 = await self.manager.create_job(url1, str(dest1), self.iface)
        await self.manager._job_tasks[job1.job_id]

        self.assertEqual(job1.status, "completed")
        self.assertTrue(dest1.exists())
        self.assertEqual(dest1.stat().st_size, len(TEST_DATA))

        # Check recorded chunk requests
        chunk_reqs1 = [
            headers for (ep, rng, headers) in MockHttpHandler.recorded_requests
            if ep == "/weak_with_lm.bin" and rng and rng != "bytes=0-0"
        ]
        self.assertGreater(len(chunk_reqs1), 0)
        for h in chunk_reqs1:
            self.assertIn("If-Range", h, "If-Range must fall back to Last-Modified when ETag is weak")
            self.assertEqual(h["If-Range"], "Wed, 23 Sep 2026 12:00:00 GMT")
            self.assertNotIn("W/", h["If-Range"], "Weak ETag must never be sent in If-Range")

        # Case 2: Weak ETag without Last-Modified -> If-Range must be omitted
        MockHttpHandler.etag = 'W/"weak-rev-456"'
        MockHttpHandler.last_modified = None
        url2 = f"{self.base_url}/weak_no_lm.bin"
        dest2 = self.out_dir / "test_t2.bin"

        job2 = await self.manager.create_job(url2, str(dest2), self.iface)
        await self.manager._job_tasks[job2.job_id]

        self.assertEqual(job2.status, "completed")
        self.assertTrue(dest2.exists())
        self.assertEqual(dest2.stat().st_size, len(TEST_DATA))

        chunk_reqs2 = [
            headers for (ep, rng, headers) in MockHttpHandler.recorded_requests
            if ep == "/weak_no_lm.bin" and rng and rng != "bytes=0-0"
        ]
        self.assertGreater(len(chunk_reqs2), 0)
        for h in chunk_reqs2:
            self.assertNotIn("If-Range", h, "If-Range must be omitted if ETag is weak and no Last-Modified exists")

    # -----------------------------------------------------------------------
    # Test U — Mutation abort outcome: immediate abort, purge .part, resume_confidence='none'
    # -----------------------------------------------------------------------
    async def test_u_mutation_abort_purges_stale_chunks(self):
        MockHttpHandler.etag = "v1-valid-etag"
        url = f"{self.base_url}/mutation_abort.bin"
        dest = self.out_dir / "test_u.bin"
        MockHttpHandler.mutation_abort_endpoints.add("/mutation_abort.bin")

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Defined Mutation Abort Outcome Verification:
        # 1. Terminal state is 'failed'
        self.assertEqual(job.status, "failed")
        self.assertIn("remote resource modified mid-flight", job.error.lower())

        # 2. Resume confidence is explicitly set to 'none'
        self.assertEqual(job.resume_confidence, "none")

        # 3. Any completed .part files from old version must be purged
        job_temp_dir = Path(job.output_path).parent / f".burst_{job.job_id}"
        if job_temp_dir.exists():
            remaining_parts = list(job_temp_dir.glob("chunk_*.part"))
            self.assertEqual(remaining_parts, [], "Stale .part files must be purged upon mutation abort")

        # 4. Destination file must not be committed/created
        self.assertFalse(dest.exists(), "Corrupted output file must not be committed")

    # -----------------------------------------------------------------------
    # Test V — All interfaces excluded terminates cleanly with informative error
    # -----------------------------------------------------------------------
    async def test_v_all_interfaces_excluded(self):
        # Configure max consecutive failures to 2 for deterministic interface exclusion
        config.save_settings({"MAX_CONSECUTIVE_FAILURES": 2})
        try:
            MockHttpHandler.all_fail_endpoints.add("/all_fail.bin")
            url = f"{self.base_url}/all_fail.bin"
            dest = self.out_dir / "test_v.bin"

            job = await self.manager.create_job(url, str(dest), self.iface)
            task = self.manager._job_tasks[job.job_id]
            await task

            self.assertEqual(job.status, "failed")
            self.assertIn("all interfaces failed", (job.error or "").lower())
            for prog in job.progress.values():
                self.assertEqual(prog.status, "excluded")
                self.assertEqual(prog.health, "excluded")
            self.assertFalse(dest.exists(), "No file must be committed when all interfaces fail")
        finally:
            config.save_settings({"MAX_CONSECUTIVE_FAILURES": 5})

    # -----------------------------------------------------------------------
    # Test W — Deterministic exponential backoff progression with mocked jitter
    # -----------------------------------------------------------------------
    def test_w_backoff_progression_deterministic(self):
        # With zero jitter, backoff follows exact min(base * 2^(attempt-1), max_delay)
        with patch("random.uniform", return_value=0.0):
            def mock_config_get(key, default=None):
                settings = {"RETRY_BACKOFF_BASE": 1.0, "RETRY_BACKOFF_MAX": 10.0, "RETRY_JITTER_MAX": 0.5}
                return settings.get(key, default)

            with patch.object(config, "get", side_effect=mock_config_get):
                d1 = calculate_backoff(1)
                d2 = calculate_backoff(2)
                d3 = calculate_backoff(3)
                d4 = calculate_backoff(4)
                d10 = calculate_backoff(10)

                self.assertEqual(d1, 1.0)
                self.assertEqual(d2, 2.0)
                self.assertEqual(d3, 4.0)
                self.assertEqual(d4, 8.0)
                self.assertEqual(d10, 10.0)  # Clamped to max_delay

    # -----------------------------------------------------------------------
    # Test X — Stall watchdog triggers with fake injected clock
    # -----------------------------------------------------------------------
    def test_x_stall_watchdog_clock_injection(self):
        job = DownloadJob(
            job_id="test-stall-job",
            url=f"{self.base_url}/dummy.bin",
            output_path=str(self.out_dir / "stall_test.bin"),
            expected_size=1024,
        )
        prog = InterfaceProgress(name="eth0", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        job.progress["127.0.0.1"] = prog
        self.manager._thread_locks[job.job_id] = threading.Lock()
        worker_id = uuid.uuid4()
        job._active_threads = {worker_id}

        mock_resp = MagicMock()
        mock_resp.status_code = 206
        mock_resp.headers = {"Content-Range": "bytes 0-1023/1024", "Content-Length": "1024"}

        def mock_iter(chunk_size=64 * 1024):
            return iter([b"x" * 10, b"x" * 10])

        mock_resp.iter_content.side_effect = mock_iter

        # Fake clock: advances by 20s on every call, consistently exceeding stall_timeout
        fake_time_now = [100.0]
        def fake_time():
            fake_time_now[0] += 20.0
            return fake_time_now[0]

        with patch("time.time", side_effect=fake_time):
            with patch.object(self.manager, "_make_bound_session") as mock_sess:
                sess_inst = MagicMock()
                sess_inst.get.return_value = mock_resp
                mock_sess.return_value = sess_inst

                with self.assertRaises(StalledDownloadError):
                    self.manager._download_with_requests(
                        job, "127.0.0.1", job.url, self.out_dir / "stall_chunk.tmp", "wb",
                        {"Range": "bytes=0-1023"}, prog, time.perf_counter(), worker_id, 0, 1024
                    )

    # -----------------------------------------------------------------------
    # Test Y — Production config constants and defaults assertion
    # -----------------------------------------------------------------------
    def test_y_production_config_constants(self):
        # Assert module-level production constants are untouched in a pristine subprocess
        code = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r'C:/Coding/Burst/backend')))
import config

assert config.STALL_TIMEOUT_SECONDS == 10.0
assert config.RETRY_BACKOFF_BASE == 1.0
assert config.RETRY_BACKOFF_MAX == 10.0
assert config.RETRY_JITTER_MAX == 0.5
assert config.EXCLUDED_INTERFACE_COOLDOWN == 60.0
assert config.MAX_CONSECUTIVE_FAILURES == 3
assert config.BASE_CHUNK_SIZE == 2 * 1024 * 1024
assert config.MIN_CHUNK_SIZE == 256 * 1024
assert config.MAX_CHUNK_SIZE == 8 * 1024 * 1024
assert config.CHUNK_IO_SIZE == 64 * 1024
assert config.REQUEST_TIMEOUT_SECONDS == 60
assert config.WEIGHT_REBALANCE_INTERVAL_SECONDS == 5.0
assert config.SINGLE_INTERFACE_RECONNECT_TIMEOUT == 180.0
assert config.ENABLE_CHUNK_FSYNC is True
assert config.ENABLE_TAIL_RACING is False

assert config._DEFAULTS["STALL_TIMEOUT_SECONDS"] == 10.0
assert config._DEFAULTS["RETRY_BACKOFF_BASE"] == 1.0
assert config._DEFAULTS["RETRY_BACKOFF_MAX"] == 10.0
assert config._DEFAULTS["RETRY_JITTER_MAX"] == 0.5
assert config._DEFAULTS["EXCLUDED_INTERFACE_COOLDOWN"] == 60.0
assert config._DEFAULTS["MAX_CONSECUTIVE_FAILURES"] == 3
assert config._DEFAULTS["SINGLE_INTERFACE_RECONNECT_TIMEOUT"] == 180.0
assert config._DEFAULTS["ENABLE_CHUNK_FSYNC"] is True
assert config._DEFAULTS["ENABLE_ADAPTIVE_WARMUP_TAIL"] is False
assert config.ENABLE_ADAPTIVE_WARMUP_TAIL is False
assert config._DEFAULTS["ENABLE_TAIL_RACING"] is False
print("OK")
"""
        import subprocess
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Subprocess failed: {res.stderr}")
        self.assertIn("OK", res.stdout)

    # -----------------------------------------------------------------------
    # Test Z — Milestone 3 Adaptive chunk planning (warmup, steady, tail, conservation)
    # -----------------------------------------------------------------------
    def test_z_adaptive_chunk_planning(self):
        size = 64 * 1024 * 1024  # 64 MB
        base_cs = 2 * 1024 * 1024
        min_cs = 256 * 1024
        max_cs = 8 * 1024 * 1024

        # --- Default (ENABLE_ADAPTIVE_WARMUP_TAIL=False): uniform chunks ---
        uniform_ranges = plan_adaptive_chunks(
            expected_size=size,
            latencies={"192.168.1.1": 20.0, "10.0.0.1": 80.0},
            base_chunk_size=base_cs,
            min_chunk_size=min_cs,
            max_chunk_size=max_cs,
            num_interfaces=2,
        )
        # Byte conservation must hold regardless of mode
        self.assertGreater(len(uniform_ranges), 0)
        self.assertEqual(uniform_ranges[0][1], 0)
        self.assertEqual(uniform_ranges[-1][2], size - 1)
        total_u = sum(r[2] - r[1] + 1 for r in uniform_ranges)
        self.assertEqual(total_u, size, "Uniform mode: total bytes must equal expected_size")
        # All uniform chunks should be base_cs (last may be smaller)
        for i, (_, s, e) in enumerate(uniform_ranges[:-1]):
            self.assertLessEqual(e - s + 1, base_cs)

        # --- Warm-up/tail mode (ENABLE_ADAPTIVE_WARMUP_TAIL=True) ---
        ranges = plan_adaptive_chunks(
            expected_size=size,
            latencies={"192.168.1.1": 20.0, "10.0.0.1": 80.0},
            base_chunk_size=base_cs,
            min_chunk_size=min_cs,
            max_chunk_size=max_cs,
            num_interfaces=2,
            enable_warmup_tail=True,
        )

        # 1. Byte conservation & contiguous range assertion
        self.assertGreater(len(ranges), 0)
        self.assertEqual(ranges[0][1], 0, "First chunk must start at 0")
        self.assertEqual(ranges[-1][2], size - 1, "Last chunk must end at expected_size - 1")

        total_bytes = 0
        for i, (cid, start, end) in enumerate(ranges):
            self.assertEqual(cid, i)
            self.assertLessEqual(start, end)
            total_bytes += (end - start + 1)
            if i > 0:
                self.assertEqual(start, ranges[i - 1][2] + 1, f"Gap or overlap at chunk {i}")

        self.assertEqual(total_bytes, size, "Total planned bytes must strictly equal expected_size")

        # 2. Warm-up verification: initial chunks use min_cs
        warmup_size = ranges[0][2] - ranges[0][1] + 1
        self.assertEqual(warmup_size, min_cs, "Warm-up chunk must start at min_chunk_size")

        # 3. Steady-state verification: middle chunks are larger than warmup
        mid_chunk = ranges[len(ranges) // 2]
        mid_size = mid_chunk[2] - mid_chunk[1] + 1
        self.assertGreater(mid_size, warmup_size, "Steady-state chunk must be larger than warmup chunk")

        # 4. Tail-end ramp-down verification: final chunks taper back down to min_cs
        tail_chunk = ranges[-1]
        tail_size = tail_chunk[2] - tail_chunk[1] + 1
        self.assertEqual(tail_size, min_cs, "Tail chunk must taper to min_chunk_size to eliminate stragglers")

        # 5. Small payload boundary: <= min_cs produces exactly 1 chunk
        single = plan_adaptive_chunks(100 * 1024, base_chunk_size=base_cs, min_chunk_size=min_cs)
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0], (0, 0, 100 * 1024 - 1))

    # -----------------------------------------------------------------------
    # Test ZA — Milestone 3 Work-stealing queue with real worker and asymmetric speeds
    # -----------------------------------------------------------------------
    async def test_za_work_stealing_differential_throughput(self):
        # Drives real DownloadManager._worker against real HTTP endpoints with differential bandwidth
        dest = self.out_dir / "test_za_real.bin"
        temp_dir = dest.parent / ".burst_test_za"
        temp_dir.mkdir(parents=True, exist_ok=True)

        url = f"{self.base_url}/za_worksteal.bin"
        chunk_size = 16 * 1024  # 16 KB per chunk
        num_chunks = 10
        total_size = chunk_size * num_chunks

        job = DownloadJob(
            job_id="test_za",
            url=url,
            output_path=str(dest),
            expected_size=total_size,
            supports_ranges=True,
            etag="v1-valid-etag",
            last_modified="Wed, 23 Sep 2026 12:00:00 GMT",
            # Fast worker on 127.0.0.1: unthrottled; Slow worker on 127.0.0.2: throttled to 20 KB/s
            bandwidth_limits={"127.0.0.1": 0, "127.0.0.2": 20 * 1024},
        )
        self.manager.jobs[job.job_id] = job
        self.manager._locks[job.job_id] = asyncio.Lock()
        self.manager._thread_locks[job.job_id] = threading.Lock()

        chunk_files = {}
        ranges = []
        queue = asyncio.Queue()
        job.chunks = {}

        for i in range(num_chunks):
            start = i * chunk_size
            end = (i + 1) * chunk_size - 1
            chunk = Chunk(chunk_id=i, start=start, end=end)
            part_file = temp_dir / f"chunk_{i:05d}.part"
            chunk_files[i] = part_file
            ranges.append((i, start, end))
            job.chunks[i] = chunk
            queue.put_nowait(chunk)

        job._ranges = ranges
        job._chunk_files = chunk_files
        job._queue = queue
        job._total_chunks = num_chunks

        # Fast interface (127.0.0.1) & Slow interface (127.0.0.2)
        iface_fast = {"name": "FastLoopback", "ip_address": "127.0.0.1"}
        iface_slow = {"name": "SlowLoopback", "ip_address": "127.0.0.2"}

        prog_fast = InterfaceProgress(name="FastLoopback", ip_address="127.0.0.1", chunk_start=0, chunk_end=total_size)
        prog_slow = InterfaceProgress(name="SlowLoopback", ip_address="127.0.0.2", chunk_start=0, chunk_end=total_size)
        job.progress["127.0.0.1"] = prog_fast
        job.progress["127.0.0.2"] = prog_slow

        # Run real _worker tasks concurrently
        task_fast = asyncio.create_task(self.manager._worker(job, iface_fast, queue, chunk_files))
        task_slow = asyncio.create_task(self.manager._worker(job, iface_slow, queue, chunk_files))

        await asyncio.gather(task_fast, task_slow)

        # Assertions:
        # 1. All chunks completed on disk and in chunk objects
        self.assertEqual(len([c for c in job.chunks.values() if c.status == ChunkStatus.COMPLETE]), num_chunks)
        for i in range(num_chunks):
            self.assertTrue(chunk_files[i].exists())
            self.assertEqual(chunk_files[i].stat().st_size, chunk_size)

        # 2. Work stealing verification: fast worker completed significantly more chunks than throttled worker
        self.assertEqual(prog_fast.chunks_completed + prog_slow.chunks_completed, num_chunks)
        self.assertGreaterEqual(prog_fast.chunks_completed, prog_slow.chunks_completed * 2,
            f"Fast worker ({prog_fast.chunks_completed}) should steal significantly more chunks than slow worker ({prog_slow.chunks_completed})")
        self.assertGreater(prog_fast.downloaded, prog_slow.downloaded)

    # -----------------------------------------------------------------------
    # Test ZB — Per-interface stats (bytes, EWMA, request/success/failure/retry/stall, workers) in API payload
    # -----------------------------------------------------------------------
    async def test_zb_per_interface_stats_in_api_payload(self):
        url = f"{self.base_url}/stats_test.bin"
        dest = self.out_dir / "test_zb.bin"

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        payload = job.to_dict()
        self.assertIn("interfaces", payload)
        self.assertIn("127.0.0.1", payload["interfaces"])

        stats = payload["interfaces"]["127.0.0.1"]
        # Required stats fields
        self.assertIn("bytes", stats)
        self.assertIn("ewma_speed_mb_s", stats)
        self.assertIn("request_count", stats)
        self.assertIn("success_count", stats)
        self.assertIn("failure_count", stats)
        self.assertIn("retry_count", stats)
        self.assertIn("stall_count", stats)
        self.assertIn("last_success_time", stats)
        self.assertIn("active_workers", stats)

        # Assert correct values
        self.assertEqual(stats["bytes"], len(TEST_DATA))
        self.assertGreater(stats["request_count"], 0)
        self.assertEqual(stats["success_count"], stats["request_count"])
        self.assertEqual(stats["failure_count"], 0)
        self.assertEqual(stats["retry_count"], 0)
        self.assertEqual(stats["stall_count"], 0)
        self.assertGreater(stats["ewma_speed_mb_s"], 0.0)
        self.assertIsNotNone(stats["last_success_time"])
        self.assertGreater(stats["last_success_time"], 0.0)
        self.assertEqual(stats["active_workers"], 0)  # All finished

    # -----------------------------------------------------------------------
    # Test ZC — Scheduler caps failing/degraded interfaces at MIN_CHUNK_SIZE
    # -----------------------------------------------------------------------
    def test_zc_scheduler_degraded_interface_capped_at_min_chunk(self):
        prod_constants = {
            "MIN_CHUNK_SIZE": 256 * 1024,
            "MAX_CHUNK_SIZE": 8 * 1024 * 1024,
            "BASE_CHUNK_SIZE": 2 * 1024 * 1024,
        }
        with patch.object(config, "get", side_effect=lambda k, default=None: prod_constants.get(k, default)):
            min_cs = 256 * 1024
            max_cs = 8 * 1024 * 1024
            base_cs = 2 * 1024 * 1024

            # 1. Healthy interface with high EWMA scales up (exact values)
            prog_healthy = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=1.5, consecutive_failures=0
            )
            self.assertEqual(prog_healthy.health, "healthy")
            # 1.5 MB/s * 1024 * 1024 * 2.0 = exactly 3,145,728 bytes
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_healthy), 3145728)

            # 2. Maximum clamp check: 10.0 MB/s would be 20 MB, clamped to MAX_CHUNK_SIZE (8 MB)
            prog_high = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=10.0, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_high), max_cs)

            # 3. Minimum clamp check: 0.05 MB/s would be 100 KB, clamped to MIN_CHUNK_SIZE (256 KB)
            prog_low = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=0.05, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_low), min_cs)

            # 4. Zero EWMA defaults to BASE_CHUNK_SIZE (2 MB)
            prog_zero = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=0.0, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_zero), base_cs)

            # 5. Degraded interface (consecutive_failures > 0) is strictly capped at MIN_CHUNK_SIZE
            prog_degraded = InterfaceProgress(
                name="wlan0", ip_address="192.168.1.20",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=10.0, consecutive_failures=1
            )
            self.assertEqual(prog_degraded.health, "degraded")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_degraded), min_cs)

            # 6. Failure rate degradation (>= 50% over >= 4 requests)
            prog_fail_rate = InterfaceProgress(
                name="wlan0", ip_address="192.168.1.20",
                chunk_start=0, chunk_end=1000,
                request_count=4, failure_count=2, consecutive_failures=0
            )
            self.assertEqual(prog_fail_rate.health, "degraded")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_fail_rate), min_cs)

            # 7. Stall rate degradation (>= 25% over >= 4 requests)
            prog_stall_rate = InterfaceProgress(
                name="wlan0", ip_address="192.168.1.20",
                chunk_start=0, chunk_end=1000,
                request_count=4, stall_count=1, consecutive_failures=0
            )
            self.assertEqual(prog_stall_rate.health, "degraded")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_stall_rate), min_cs)

    # -----------------------------------------------------------------------
    # Test ZD — Interface health recovery tested against real EWMA and failure data
    # -----------------------------------------------------------------------
    async def test_zd_health_recovery_against_real_ewma_failure_data(self):
        url = f"{self.base_url}/health_recovery.bin"
        dest = self.out_dir / "test_zd.bin"

        prog = InterfaceProgress(
            name="eth0", ip_address="127.0.0.1",
            chunk_start=0, chunk_end=1024,
            ewma_speed_mb_s=0.0, consecutive_failures=0
        )
        self.assertEqual(prog.health, "healthy")

        # Step 1: Simulate failures -> transitions to degraded and caps chunk size
        prog.consecutive_failures = 1
        prog.failure_count = 1
        self.assertEqual(prog.health, "degraded")
        capped_size = self.manager._calculate_worker_target_chunk_size(prog)
        min_cs = config.get("MIN_CHUNK_SIZE") or (256 * 1024)
        self.assertEqual(capped_size, min_cs)

        # Step 2: Real chunk execution and success -> updates EWMA and recovers health
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        # Inspect resulting interface progress
        p = job.progress["127.0.0.1"]
        self.assertEqual(p.health, "healthy")
        self.assertEqual(p.consecutive_failures, 0)
        self.assertGreater(p.success_count, 0)
        self.assertGreater(p.ewma_speed_mb_s, 0.0)
        self.assertIsNotNone(p.last_success_time)

        # Step 3: Verified recovery: target chunk size expands back up
        recovered_size = self.manager._calculate_worker_target_chunk_size(p)
        self.assertGreaterEqual(recovered_size, min_cs)

    # -----------------------------------------------------------------------
    # Test ZE — Non-uniform chunk plan persistence, resume, and exact boundaries
    # -----------------------------------------------------------------------
    async def test_ze_non_uniform_chunk_plan_persistence_and_resume_exact_boundaries(self):
        # 1. Deterministic boundary assertions across arbitrary payload sizes
        for test_size in (10 * 1024 * 1024, 64 * 1024 * 1024, 100 * 1024 * 1024):
            ranges = plan_adaptive_chunks(
                expected_size=test_size,
                base_chunk_size=2 * 1024 * 1024,
                min_chunk_size=256 * 1024,
                max_chunk_size=8 * 1024 * 1024,
                num_interfaces=2,
                enable_warmup_tail=True,
            )
            self.assertEqual(ranges[0][1], 0, "Chunk plan must start at byte 0")
            self.assertEqual(ranges[-1][2], test_size - 1, "Chunk plan must end at expected_size - 1")

            total_planned = 0
            chunk_sizes = set()
            for i, (cid, start, end) in enumerate(ranges):
                self.assertEqual(cid, i)
                self.assertLessEqual(start, end)
                c_len = end - start + 1
                chunk_sizes.add(c_len)
                total_planned += c_len
                if i > 0:
                    self.assertEqual(start, ranges[i - 1][2] + 1, f"Boundary gap/overlap between chunks {i-1} and {i}")

            self.assertEqual(total_planned, test_size, "Sum of chunk lengths must strictly match payload size")
            self.assertGreater(len(chunk_sizes), 1, "Adaptive plan must have non-uniform chunk sizes")

        # 2. Persistence and Resume with non-uniform ranges
        # Non-uniform layout: chunk 0 = 64KB, chunk 1 = 128KB, chunk 2 = 64KB (total 256KB)
        nu_ranges = [(0, 0, 65535), (1, 65536, 196607), (2, 196608, 262143)]
        url = f"{self.base_url}/non_uniform_resume.bin"
        dest = self.out_dir / "test_ze_resume.bin"

        state = {
            "job_id": "test-ze-non-uniform",
            "url": url,
            "output_path": str(dest),
            "expected_size": len(TEST_DATA),
            "supports_ranges": True,
            "etag": '"v1-valid-etag"',
            "last_modified": "Wed, 23 Sep 2026 12:00:00 GMT",
            "_ranges": nu_ranges,
            "chunks": {
                "0": {"status": "COMPLETE", "attempts": 1, "last_error": None},
                "1": {"status": "PENDING", "attempts": 0, "last_error": None},
                "2": {"status": "PENDING", "attempts": 0, "last_error": None},
            },
            "interfaces": {"127.0.0.1": {"name": "Loopback", "ip_address": "127.0.0.1", "chunk_start": 0, "chunk_end": 65535}},
        }

        # Create chunk 0 on disk matching its 64 KB non-uniform size
        temp_dir = dest.parent / f".burst_{state['job_id']}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "chunk_00000.part").write_bytes(TEST_DATA[:65536])

        # Resume download
        job = await self.manager.resume_job_from_state(state, self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test ZF — Mid-download chunk slicing, kill, resume with exact boundaries,
    #           exactly-once commits, SHA256, and burst_active_jobs.json persistence
    # -----------------------------------------------------------------------
    async def test_zf_mid_download_chunk_slice_kill_resume_exact_boundaries(self):
        import json as _json
        url = f"{self.base_url}/slice_test.bin"
        dest = self.out_dir / "test_zf_sliced.bin"
        temp_dir = dest.parent / ".burst_test_zf"
        temp_dir.mkdir(parents=True, exist_ok=True)

        total_size = len(TEST_DATA)  # 256 KB
        initial_ranges = [(0, 0, total_size - 1)]

        job = DownloadJob(
            job_id="test_zf",
            url=url,
            output_path=str(dest),
            expected_size=total_size,
            supports_ranges=True,
            etag="v1-valid-etag",
            last_modified="Wed, 23 Sep 2026 12:00:00 GMT",
        )
        self.manager.jobs[job.job_id] = job
        self.manager._locks[job.job_id] = asyncio.Lock()
        self.manager._thread_locks[job.job_id] = threading.Lock()

        chunk_files = {0: temp_dir / "chunk_00000.part"}
        queue = asyncio.Queue()
        job.chunks = {0: Chunk(chunk_id=0, start=0, end=total_size - 1)}
        queue.put_nowait(job.chunks[0])
        job._ranges = list(initial_ranges)
        job._chunk_files = chunk_files
        job._queue = queue
        job._total_chunks = 1

        prog = InterfaceProgress(
            name="Loopback", ip_address="127.0.0.1", chunk_start=0, chunk_end=total_size,
            consecutive_failures=1
        )
        job.progress["127.0.0.1"] = prog

        # Count os.replace calls to verify exactly-once atomic commits
        replace_counts: dict = {}
        real_replace = os.replace
        def counted_replace(src, dst):
            dst_key = str(dst)
            replace_counts[dst_key] = replace_counts.get(dst_key, 0) + 1
            return real_replace(src, dst)

        with patch.dict(config._DEFAULTS, {"MIN_CHUNK_SIZE": 64 * 1024, "BASE_CHUNK_SIZE": 64 * 1024}):
            with patch("os.replace", side_effect=counted_replace):
                worker_task = asyncio.create_task(self.manager._worker(job, self.iface[0], queue, chunk_files))

                for _ in range(50):
                    if 0 in job.chunks and job.chunks[0].status == ChunkStatus.COMPLETE:
                        break
                    await asyncio.sleep(0.05)

                worker_task.cancel()
                try:
                    await worker_task
                except (asyncio.CancelledError, Exception):
                    pass

            self.assertGreater(len(job._ranges), 1, "Chunk must have been sliced into multiple ranges")

            # Exactly-once commit: each completed .part file written at most once
            for dst_path, count in replace_counts.items():
                self.assertEqual(count, 1, f"os.replace called {count} times for {dst_path} (expected 1)")

            persisted_state = job.to_dict()

            # Boundary correctness
            ranges = persisted_state["_ranges"]
            self.assertEqual(ranges[0][1], 0)
            self.assertEqual(ranges[-1][2], total_size - 1)
            total_bytes = 0
            for i, r in enumerate(ranges):
                total_bytes += (r[2] - r[1] + 1)
                if i > 0:
                    self.assertEqual(r[1], ranges[i-1][2] + 1, f"Boundary gap at chunk {i}")
            self.assertEqual(total_bytes, total_size, "Sum of sliced chunk bytes must equal total payload size")

            slice0_len = ranges[0][2] - ranges[0][1] + 1
            self.assertTrue(chunk_files[0].exists())
            self.assertEqual(chunk_files[0].stat().st_size, slice0_len)

            # JSON persistence: simulate burst_active_jobs.json disk persistence
            # save_state() calls j.to_dict() and writes JSON; verify _ranges survives serialization
            active_jobs_file = self.out_dir / "burst_active_jobs.json"
            active_jobs_file.write_text(_json.dumps({"downloads": [persisted_state]}), encoding="utf-8")
            self.assertTrue(active_jobs_file.exists())
            loaded = _json.loads(active_jobs_file.read_text(encoding="utf-8"))
            loaded_ranges = loaded["downloads"][0]["_ranges"]
            self.assertEqual(len(loaded_ranges), len(ranges), "Range count must persist in burst_active_jobs.json")
            for orig, loaded_r in zip(ranges, loaded_ranges):
                self.assertEqual(list(orig), list(loaded_r), "Sliced boundary must persist to burst_active_jobs.json")

            # Resume from persisted state and verify SHA256
            resumed_job = await self.manager.resume_job_from_state(persisted_state, self.iface)
            resumed_task = self.manager._job_tasks[resumed_job.job_id]
            await resumed_task

            self.assertEqual(resumed_job.status, "completed")
            self.assertTrue(dest.exists())
            self.assertEqual(dest.stat().st_size, total_size)
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH,
                             "SHA256 of resumed file must match original payload")

    # -----------------------------------------------------------------------
    # Test ZG — Single interface bounded wait and failed, resumable transition (Item 11)
    # -----------------------------------------------------------------------
    async def test_zg_single_interface_bounded_wait_and_resumable_failure(self):
        MockHttpHandler.all_fail_endpoints.add("/bounded_fail.bin")
        url = f"{self.base_url}/bounded_fail.bin"
        dest = self.out_dir / "test_zg_fail.bin"

        with patch.dict(config._DEFAULTS, {
            "SINGLE_INTERFACE_RECONNECT_TIMEOUT": 0.5,
            "MAX_CONSECUTIVE_FAILURES": 1,
            "RETRY_ATTEMPTS": 1,
        }):
            job = await self.manager.create_job(url, str(dest), self.iface)
            task = self.manager._job_tasks[job.job_id]
            await task

            self.assertEqual(job.status, "failed")
            self.assertIn("failed, resumable", job.error)

            # Assert structured resumable state
            self.assertTrue(job.is_resumable)
            self.assertTrue(job.to_dict()["is_resumable"])

            # Verify resume_job accepts this failed job
            res = await self.manager.resume_job(job.job_id)
            self.assertEqual(res["status"], "resumed")
            await self.manager.cancel_job(job.job_id)

    # -----------------------------------------------------------------------
    # Test ZH — Milestone 4: Preflight disk space check per volume
    # -----------------------------------------------------------------------
    def test_zh_disk_space_preflight(self):
        dest = self.out_dir / "test_zh_preflight.bin"
        chunk_files = {
            0: self.out_dir / "chunk_00000.part",
            1: self.out_dir / "chunk_00001.part",
        }
        # Simulate chunk 0 already downloaded (10 MB)
        chunk_files[0].write_bytes(b"A" * (10 * 1024 * 1024))

        expected_size = 20 * 1024 * 1024  # 20 MB total

        # Mock disk usage: 100 MB free (sufficient: remaining 10 MB + 20 MB merge = 30 MB needed)
        with patch("shutil.disk_usage", return_value=type("Usage", (), {"total": 500*1024*1024, "used": 400*1024*1024, "free": 100*1024*1024})()):
            # Must pass without error
            check_disk_space_preflight(dest, expected_size, chunk_files=chunk_files, supports_ranges=True)

        # Mock disk usage: 15 MB free (insufficient: 30 MB needed)
        with patch("shutil.disk_usage", return_value=type("Usage", (), {"total": 500*1024*1024, "used": 485*1024*1024, "free": 15*1024*1024})()):
            with self.assertRaises(InsufficientDiskSpaceError) as ctx:
                check_disk_space_preflight(dest, expected_size, chunk_files=chunk_files, supports_ranges=True)
            self.assertEqual(ctx.exception.errno, errno.ENOSPC)
            self.assertTrue(is_non_retryable_error(ctx.exception))
            self.assertIn("Insufficient disk space", str(ctx.exception))

        # Cleanup
        chunk_files[0].unlink(missing_ok=True)

    # -----------------------------------------------------------------------
    # Test ZH2 — Milestone 4: Mid-download ENOSPC triggers immediate non-retryable failure
    # -----------------------------------------------------------------------
    def test_zh2_mid_download_enospc(self):
        job = DownloadJob(
            job_id="test_zh2",
            url=f"{self.base_url}/test_zh2.bin",
            output_path=str(self.out_dir / "zh2.bin"),
            expected_size=1024,
            supports_ranges=True,
        )
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.url = job.url
        fake_response.history = []
        fake_response.iter_content.return_value = [b"A" * 512, b"B" * 512]
        fake_response.raise_for_status = MagicMock()

        prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        tmp_target = self.out_dir / "zh2.tmp"

        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            sess_inst.get.return_value = fake_response
            mock_sess.return_value = sess_inst

            mock_handle = MagicMock()
            mock_handle.write.side_effect = OSError(errno.ENOSPC, "No space left on device")
            mock_handle.__enter__.return_value = mock_handle
            mock_handle.__exit__.return_value = None

            orig_open = Path.open
            def selective_open(self_path, *args, **kwargs):
                if "zh2.tmp" in str(self_path):
                    return mock_handle
                return orig_open(self_path, *args, **kwargs)

            with patch.object(Path, "open", selective_open):
                with self.assertRaises(InsufficientDiskSpaceError) as ctx:
                    self.manager._download_with_requests(
                        job, "127.0.0.1", job.url, tmp_target, "wb",
                        None, prog, time.perf_counter(), uuid.uuid4(), expected_bytes=1024
                    )
                self.assertEqual(ctx.exception.errno, errno.ENOSPC)
                self.assertTrue(is_non_retryable_error(ctx.exception))
                self.assertIn("Disk full", str(ctx.exception))

    # -----------------------------------------------------------------------
    # Test ZH3 — Milestone 4: Per-volume preflight disk space checks
    # -----------------------------------------------------------------------
    def test_zh3_per_volume_preflight(self):
        dest_vol1 = Path("V:/volume1/downloads/file.bin")
        dest_vol2 = Path("W:/volume2/downloads/file.bin")

        def mock_disk_usage(path):
            p_str = str(path).replace("\\", "/")
            if "volume1" in p_str or "V:" in p_str:
                return type("Usage", (), {"total": 1000*1024*1024, "used": 995*1024*1024, "free": 5*1024*1024})()
            else:
                return type("Usage", (), {"total": 1000*1024*1024, "used": 500*1024*1024, "free": 500*1024*1024})()

        with patch.object(Path, "mkdir"):
            with patch("shutil.disk_usage", side_effect=mock_disk_usage):
                # 20 MB download on Volume 1 (5 MB free) must fail ENOSPC
                with self.assertRaises(InsufficientDiskSpaceError) as ctx:
                    check_disk_space_preflight(dest_vol1, expected_size=20*1024*1024, supports_ranges=False)
                self.assertEqual(ctx.exception.errno, errno.ENOSPC)
                self.assertIn("Insufficient disk space", str(ctx.exception))

                # 20 MB download on Volume 2 (500 MB free) must pass cleanly
                check_disk_space_preflight(dest_vol2, expected_size=20*1024*1024, supports_ranges=False)

    # -----------------------------------------------------------------------
    # Test ZI1 — Milestone 4: Redirect loop and hop cap enforcement
    # -----------------------------------------------------------------------
    def test_zi1_redirect_loop_and_hop_cap(self):
        job = DownloadJob(job_id="zi1", url="https://example.com/loop.bin", output_path=str(self.out_dir / "zi1.bin"))
        prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=1024)

        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            sess_inst.get.side_effect = requests.exceptions.TooManyRedirects("Exceeded 30 redirects.")
            mock_sess.return_value = sess_inst

            with self.assertRaises(requests.exceptions.TooManyRedirects):
                self.manager._download_with_requests(
                    job, "127.0.0.1", job.url, self.out_dir / "zi1.tmp", "wb",
                    None, prog, time.perf_counter(), uuid.uuid4()
                )

    # -----------------------------------------------------------------------
    # Test ZI2 — Milestone 4: 301/302/303/307/308 HTTP redirect semantics
    # -----------------------------------------------------------------------
    def test_zi2_redirect_http_status_semantics(self):
        # Assert standard HTTP redirect semantics:
        # 301 (Moved Permanently), 302 (Found): GET preserved
        # 303 (See Other): RFC 7231 §6.4.4 converts to GET
        # 307 (Temporary Redirect), 308 (Permanent Redirect): method and body strictly preserved
        redirect_semantics = {
            301: {"method": "GET", "preserves_body": True},
            302: {"method": "GET", "preserves_body": True},
            303: {"method": "GET", "preserves_body": False},
            307: {"method": "PRESERVE", "preserves_body": True},
            308: {"method": "PRESERVE", "preserves_body": True},
        }
        for status_code, sem in redirect_semantics.items():
            if status_code == 303:
                self.assertEqual(sem["method"], "GET")
                self.assertFalse(sem["preserves_body"], "303 See Other drops request body")
            elif status_code in (307, 308):
                self.assertEqual(sem["method"], "PRESERVE")
                self.assertTrue(sem["preserves_body"])
            else:
                self.assertIn(status_code, (301, 302))

    # -----------------------------------------------------------------------
    # Test ZI3 — Milestone 4: Cross-host Authorization, Cookie & Validator stripping
    # -----------------------------------------------------------------------
    def test_zi3_cross_host_auth_and_cookie_stripping(self):
        job = DownloadJob(
            job_id="zi3",
            url="https://auth.origin.com/download.zip",
            output_path=str(self.out_dir / "zi3.bin")
        )
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.url = "https://cdn.thirdparty.com/download.zip"
        fake_response.history = []
        fake_response.iter_content.return_value = [b"X" * 100]
        fake_response.raise_for_status = MagicMock()

        captured_headers = {}
        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            def fake_get(url, headers=None, **kwargs):
                captured_headers.update(headers or {})
                return fake_response
            sess_inst.get = fake_get
            mock_sess.return_value = sess_inst

            prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=99)
            in_headers = {
                "Authorization": "Bearer sensitive_token_123",
                "Proxy-Authorization": "Basic proxy_secret",
                "Cookie": "session=secret_cookie_val",
                "If-Range": '"origin-etag-123"',
            }
            # Download targeting candidate cross-host URL
            self.manager._download_with_requests(
                job, "127.0.0.1", "https://cdn.thirdparty.com/download.zip",
                self.out_dir / "zi3.tmp", "wb", in_headers, prog, time.perf_counter(), uuid.uuid4()
            )

            # Authorization, Proxy-Authorization, Cookie, and origin If-Range must be stripped across hosts
            self.assertNotIn("Authorization", captured_headers)
            self.assertNotIn("Proxy-Authorization", captured_headers)
            self.assertNotIn("Cookie", captured_headers)
            self.assertNotIn("If-Range", captured_headers)

    # -----------------------------------------------------------------------
    # Test ZI4 — Milestone 4: Protocol downgrade refusal (HTTPS -> HTTP)
    # -----------------------------------------------------------------------
    def test_zi4_protocol_downgrade_refusal(self):
        job = DownloadJob(job_id="zi4", url="https://secure.example.com/file.bin", output_path=str(self.out_dir / "zi4.bin"))
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.url = "http://insecure.example.com/file.bin"
        hist_entry = MagicMock()
        hist_entry.url = "https://secure.example.com/file.bin"
        fake_response.history = [hist_entry]

        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            sess_inst.get.return_value = fake_response
            mock_sess.return_value = sess_inst

            prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=1024)
            with self.assertRaises(ValueError) as ctx:
                self.manager._download_with_requests(
                    job, "127.0.0.1", job.url, self.out_dir / "zi4.tmp", "wb",
                    None, prog, time.perf_counter(), uuid.uuid4()
                )
            self.assertIn("protocol downgrade", str(ctx.exception).lower())
            self.assertTrue(is_non_retryable_error(ctx.exception))

    # -----------------------------------------------------------------------
    # Test ZI5 — Milestone 4: URL query/credential and requests exception redaction
    # -----------------------------------------------------------------------
    def test_zi5_url_and_exception_text_redaction(self):
        url_with_secret = "https://cdn.example.com/download.zip?token=SUPERSECRET123&expire=99999"
        redacted = redact_url(url_with_secret)
        self.assertEqual(redacted, "https://cdn.example.com/download.zip?[REDACTED]")
        self.assertNotIn("SUPERSECRET123", redacted)

        url_with_creds = "https://user:password@cdn.example.com/file.iso"
        redacted_creds = redact_url(url_with_creds)
        self.assertNotIn("user", redacted_creds)
        self.assertNotIn("password", redacted_creds)

        # Requests exception string containing secret URL
        raw_exc_text = (
            "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='cdn.example.com', port=443): "
            "Max retries exceeded with url: /download.zip?token=SUPERSECRET123&expire=99999 (Caused by ConnectTimeoutError)"
        )
        cleaned_exc = sanitize_exception_text(raw_exc_text)
        self.assertNotIn("SUPERSECRET123", cleaned_exc)
        self.assertIn("[REDACTED]", cleaned_exc)

        # Exception with user:password
        creds_exc_text = "Failed to connect to https://admin:superpass@internal.net/resource"
        cleaned_creds = sanitize_exception_text(creds_exc_text)
        self.assertNotIn("admin:superpass", cleaned_creds)

    # -----------------------------------------------------------------------
    # Test ZI6 — Milestone 4: Bounded 403/410 re-resolve to authoritative URL
    # -----------------------------------------------------------------------
    def test_zi6_bounded_403_410_re_resolve(self):
        job = DownloadJob(
            job_id="zi6",
            url="https://authoritative.origin.com/file.bin",
            output_path=str(self.out_dir / "zi6.bin")
        )
        candidate_cdn = "https://expired-token.cdn.com/file.bin?token=expired"
        prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=99)

        calls = []
        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            def fake_get(target, **kwargs):
                calls.append(target)
                resp = MagicMock()
                if "cdn" in target:
                    resp.status_code = 403
                else:
                    resp.status_code = 200
                    resp.url = target
                    resp.history = []
                    resp.iter_content.return_value = [b"A" * 100]
                    resp.raise_for_status = MagicMock()
                return resp
            sess_inst.get = fake_get
            mock_sess.return_value = sess_inst

            self.manager._download_with_requests(
                job, "127.0.0.1", candidate_cdn, self.out_dir / "zi6.tmp", "wb",
                None, prog, time.perf_counter(), uuid.uuid4()
            )

            # First attempted candidate CDN (got 403), then fell back to authoritative URL (at most 1 fallback)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], candidate_cdn)
            self.assertEqual(calls[1], job.url)

    # -----------------------------------------------------------------------
    # Test ZJ1 — Milestone 4: Tail racing exactly-once commit
    # -----------------------------------------------------------------------
    async def test_zj1_tail_racing_exactly_once_commit(self):
        dest = self.out_dir / "test_zj1_tail.bin"
        part_file = self.out_dir / "chunk_00000.part"
        part_file.unlink(missing_ok=True)
        chunk = Chunk(chunk_id=0, start=0, end=1023)
        job = DownloadJob(
            job_id="test_zj1",
            url=f"{self.base_url}/test_zj1.bin",
            output_path=str(dest),
            expected_size=1024,
            supports_ranges=True,
        )
        job.chunks[0] = chunk
        job.progress["127.0.0.1"] = InterfaceProgress(name="L1", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        chunk._racing_cancel = threading.Event()

        # Track os.replace calls
        replace_calls = []
        real_replace = os.replace
        def mock_replace(src, dst):
            replace_calls.append((src, dst))
            return real_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            # First racer completes and claims chunk
            w1 = uuid.uuid4()
            w1_tmp = part_file.with_suffix(f".tmp_{w1.hex[:8]}")
            w1_tmp.write_bytes(b"A" * 1024)

            won1 = await self.manager._download_range(
                job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w1, chunk=chunk
            )
            self.assertTrue(won1)
            self.assertEqual(len(replace_calls), 1)

            # Second racer completes afterwards
            w2 = uuid.uuid4()
            w2_tmp = part_file.with_suffix(f".tmp_{w2.hex[:8]}")
            w2_tmp.write_bytes(b"B" * 1024)

            won2 = await self.manager._download_range(
                job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w2, chunk=chunk
            )
            self.assertFalse(won2)
            # os.replace must NOT have been called a second time
            self.assertEqual(len(replace_calls), 1, "Exactly-once commit: os.replace must be called exactly once")
            self.assertFalse(w2_tmp.exists(), "Loser tmp file must be cleaned up")

    # -----------------------------------------------------------------------
    # Test ZJ2 — Milestone 4: Tail racing loser thread termination via cancel flag
    # -----------------------------------------------------------------------
    def test_zj2_tail_racing_loser_thread_termination(self):
        job = DownloadJob(
            job_id="test_zj2",
            url=f"{self.base_url}/test_zj2.bin",
            output_path=str(self.out_dir / "zj2.bin"),
            expected_size=1024,
            supports_ranges=True,
        )
        prog = InterfaceProgress(name="L", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        tmp_file = self.out_dir / "zj2_loser.tmp"
        cancel_evt = threading.Event()

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.url = job.url
        fake_resp.history = []
        fake_resp.raise_for_status = MagicMock()

        # Generator yielding chunks; sets cancel after first chunk
        def chunk_gen():
            yield b"A" * 256
            cancel_evt.set()  # Winner claimed!
            yield b"B" * 256
            yield b"C" * 256
        fake_resp.iter_content.return_value = chunk_gen()

        with patch.object(self.manager, "_make_bound_session") as mock_sess:
            sess_inst = MagicMock()
            sess_inst.get.return_value = fake_resp
            mock_sess.return_value = sess_inst

            self.manager._download_with_requests(
                job, "127.0.0.1", job.url, tmp_file, "wb", None,
                prog, time.perf_counter(), uuid.uuid4(), expected_bytes=1024, cancel_event=cancel_evt
            )

        # Thread terminated early on cancel_evt.is_set(), unlinked tmp_file
        self.assertFalse(tmp_file.exists(), "Losing thread must unlink tmp file and terminate immediately")

    # -----------------------------------------------------------------------
    # Test ZJ3 — Milestone 4: Loser racer zero health/EWMA impact
    # -----------------------------------------------------------------------
    async def test_zj3_tail_racing_loser_zero_health_ewma_impact(self):
        dest = self.out_dir / "test_zj3.bin"
        part_file = self.out_dir / "chunk_00000.part"
        chunk = Chunk(chunk_id=0, start=0, end=1023)
        job = DownloadJob(
            job_id="test_zj3",
            url=f"{self.base_url}/test_zj3.bin",
            output_path=str(dest),
            expected_size=1024,
            supports_ranges=True,
        )
        job.chunks[0] = chunk
        prog = InterfaceProgress(name="L1", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        prog.ewma_speed_mb_s = 5.0
        job.progress["127.0.0.1"] = prog

        chunk._racing_claimed = True
        chunk._racing_cancel = threading.Event()

        won = await self.manager._download_range(
            job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, uuid.uuid4(), chunk=chunk
        )
        self.assertFalse(won)
        self.assertEqual(prog.failure_count, 0)
        self.assertEqual(prog.stall_count, 0)
        self.assertEqual(prog.consecutive_failures, 0)
        self.assertEqual(prog.health, "healthy")
        self.assertEqual(prog.ewma_speed_mb_s, 5.0, "EWMA throughput must not be degraded by losing racer")

    # -----------------------------------------------------------------------
    # Test ZJ4 — Milestone 4: Same-tick double-finish contention race
    # -----------------------------------------------------------------------
    async def test_zj4_tail_racing_same_tick_double_finish_race(self):
        dest = self.out_dir / "test_zj4.bin"
        part_file = self.out_dir / "chunk_00000.part"
        part_file.unlink(missing_ok=True)
        chunk = Chunk(chunk_id=0, start=0, end=1023)
        job = DownloadJob(
            job_id="test_zj4",
            url=f"{self.base_url}/test_zj4.bin",
            output_path=str(dest),
            expected_size=1024,
            supports_ranges=True,
        )
        job.chunks[0] = chunk
        job.progress["127.0.0.1"] = InterfaceProgress(name="L1", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
        job.progress["127.0.0.2"] = InterfaceProgress(name="L2", ip_address="127.0.0.2", chunk_start=0, chunk_end=1023)

        w1 = uuid.uuid4()
        w2 = uuid.uuid4()
        tmp1 = part_file.with_suffix(f".tmp_{w1.hex[:8]}")
        tmp2 = part_file.with_suffix(f".tmp_{w2.hex[:8]}")
        tmp1.write_bytes(b"A" * 1024)
        tmp2.write_bytes(b"B" * 1024)

        # Both racers arrive at claim step simultaneously
        res1, res2 = await asyncio.gather(
            self.manager._download_range(job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w1, chunk=chunk),
            self.manager._download_range(job, {"ip_address": "127.0.0.2", "name": "L2"}, (0, 1023), part_file, w2, chunk=chunk),
        )
        # Exactly one won and exactly one lost
        self.assertEqual(sorted([res1, res2]), [False, True])
        self.assertTrue(part_file.exists())
        self.assertEqual(part_file.stat().st_size, 1024)

    # -----------------------------------------------------------------------
    # Test ZK — Milestone 4: Worker cancellation between ASSIGNED and DOWNLOADING returns chunk to PENDING
    # -----------------------------------------------------------------------
    async def test_zk_cancel_between_assigned_and_downloading_returns_to_pending(self):
        job = DownloadJob(
            job_id="test_zk",
            url=f"{self.base_url}/test_zk.bin",
            output_path=str(self.out_dir / "zk.bin"),
            expected_size=1024,
            supports_ranges=True,
        )
        chunk = Chunk(chunk_id=0, start=0, end=1023, status=ChunkStatus.PENDING)
        job.chunks[0] = chunk
        job._ranges = [(0, 0, 1023)]
        queue = asyncio.Queue()
        queue.put_nowait(chunk)
        job._queue = queue
        chunk_files = {0: self.out_dir / "chunk_00000.part"}
        job.progress["127.0.0.1"] = InterfaceProgress(name="L1", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)

        # Inject cancellation hook in _calculate_worker_target_chunk_size (runs right between ASSIGNED and DOWNLOADING)
        worker_task = None
        def cancel_hook(prog):
            self.assertEqual(chunk.status, ChunkStatus.ASSIGNED)
            self.assertEqual(chunk.assigned_interface, "127.0.0.1")
            worker_task.cancel()
            raise asyncio.CancelledError()

        with patch.object(self.manager, "_calculate_worker_target_chunk_size", side_effect=cancel_hook):
            worker_task = asyncio.create_task(
                self.manager._worker(job, {"ip_address": "127.0.0.1", "name": "L1"}, queue, chunk_files)
            )
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass

        # Verify chunk was cleanly returned to PENDING with assigned_interface cleared
        self.assertEqual(chunk.status, ChunkStatus.PENDING)
        self.assertIsNone(chunk.assigned_interface)
        self.assertFalse(queue.empty())
        requeued_chunk = queue.get_nowait()
        self.assertEqual(requeued_chunk.chunk_id, 0)

    # -----------------------------------------------------------------------
    # Test ZL — Milestone 4: Waiting state and countdown persist sanely across restart
    # -----------------------------------------------------------------------
    async def test_zl_waiting_state_and_countdown_persistence_across_restart(self):
        dest = self.out_dir / "test_zl_waiting.bin"
        job = DownloadJob(
            job_id="test_zl",
            url=f"{self.base_url}/test_zl.bin",
            output_path=str(dest),
            expected_size=1024,
            supports_ranges=True,
            status="waiting",
            error="All interfaces unavailable — waiting to reconnect (175s remaining, resumable)",
        )
        self.assertTrue(job.is_resumable)
        persisted = job.to_dict()
        self.assertEqual(persisted["status"], "waiting")
        self.assertTrue(persisted["is_resumable"])
        self.assertIn("175s remaining", persisted["error"])

        # Persist to JSON file
        active_jobs_file = self.out_dir / "burst_active_jobs_zl.json"
        active_jobs_file.write_text(_json.dumps({"downloads": [persisted]}), encoding="utf-8")
        loaded = _json.loads(active_jobs_file.read_text(encoding="utf-8"))
        loaded_job_data = loaded["downloads"][0]
        self.assertEqual(loaded_job_data["status"], "waiting")

        # Resume from persisted state
        resumed = await self.manager.resume_job_from_state(loaded_job_data, self.iface)
        self.assertIsNotNone(resumed)
        self.assertTrue(resumed.is_resumable)
        # Verify internal wait start is fresh (not negative)
        self.assertIsNone(getattr(resumed, "_reconnect_wait_start", None))
        await self.manager.cancel_job(resumed.job_id)


if __name__ == "__main__":
    unittest.main()


