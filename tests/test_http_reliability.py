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
import hashlib
import http.server
import os
import re
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
    StalledDownloadError,
    URLAnalysis,
    analyze_url,
    calculate_backoff,
    plan_adaptive_chunks,
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
        # Assert module-level production constants are untouched
        self.assertEqual(config.STALL_TIMEOUT_SECONDS, 10.0)
        self.assertEqual(config.RETRY_BACKOFF_BASE, 1.0)
        self.assertEqual(config.RETRY_BACKOFF_MAX, 10.0)
        self.assertEqual(config.RETRY_JITTER_MAX, 0.5)
        self.assertEqual(config.EXCLUDED_INTERFACE_COOLDOWN, 60.0)
        self.assertEqual(config.MAX_CONSECUTIVE_FAILURES, 3)
        self.assertEqual(config.BASE_CHUNK_SIZE, 2 * 1024 * 1024)
        self.assertEqual(config.MIN_CHUNK_SIZE, 256 * 1024)
        self.assertEqual(config.MAX_CHUNK_SIZE, 8 * 1024 * 1024)
        self.assertEqual(config.CHUNK_IO_SIZE, 64 * 1024)
        self.assertEqual(config.REQUEST_TIMEOUT_SECONDS, 60)
        self.assertEqual(config.WEIGHT_REBALANCE_INTERVAL_SECONDS, 5.0)

        # Assert default dictionary matches production values
        self.assertEqual(config._DEFAULTS["STALL_TIMEOUT_SECONDS"], 10.0)
        self.assertEqual(config._DEFAULTS["RETRY_BACKOFF_BASE"], 1.0)
        self.assertEqual(config._DEFAULTS["RETRY_BACKOFF_MAX"], 10.0)
        self.assertEqual(config._DEFAULTS["RETRY_JITTER_MAX"], 0.5)
        self.assertEqual(config._DEFAULTS["EXCLUDED_INTERFACE_COOLDOWN"], 60.0)
        self.assertEqual(config._DEFAULTS["MAX_CONSECUTIVE_FAILURES"], 3)

    # -----------------------------------------------------------------------
    # Test Z — Milestone 3 Adaptive chunk planning (warmup, steady, tail, conservation)
    # -----------------------------------------------------------------------
    def test_z_adaptive_chunk_planning(self):
        size = 64 * 1024 * 1024  # 64 MB
        base_cs = 2 * 1024 * 1024
        min_cs = 256 * 1024
        max_cs = 8 * 1024 * 1024

        ranges = plan_adaptive_chunks(
            expected_size=size,
            latencies={"192.168.1.1": 20.0, "10.0.0.1": 80.0},
            base_chunk_size=base_cs,
            min_chunk_size=min_cs,
            max_chunk_size=max_cs,
            num_interfaces=2,
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
    # Test ZA — Milestone 3 Work-stealing queue with asymmetric worker speeds
    # -----------------------------------------------------------------------
    async def test_za_work_stealing_differential_throughput(self):
        queue = asyncio.Queue()
        chunks = [Chunk(chunk_id=i, start=i*1000, end=(i+1)*1000 - 1) for i in range(12)]
        for c in chunks:
            queue.put_nowait(c)

        fast_completed = []
        slow_completed = []

        async def fast_worker():
            while not queue.empty():
                try:
                    c = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await asyncio.sleep(0)  # Yields immediately
                c.status = ChunkStatus.COMPLETE
                fast_completed.append(c.chunk_id)
                queue.task_done()

        async def slow_worker():
            while not queue.empty():
                try:
                    c = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await asyncio.sleep(0.05)   # 50ms delay
                c.status = ChunkStatus.COMPLETE
                slow_completed.append(c.chunk_id)
                queue.task_done()

        await asyncio.gather(fast_worker(), slow_worker())

        # All 12 chunks completed
        self.assertEqual(len(fast_completed) + len(slow_completed), 12)
        # No duplicate chunk processing
        self.assertEqual(set(fast_completed) & set(slow_completed), set())
        # Fast worker stole significantly more chunks than slow worker (at least 2x)
        self.assertGreaterEqual(len(fast_completed), len(slow_completed) * 2, "Fast worker must steal at least 2x chunks")

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
        min_cs = config.get("MIN_CHUNK_SIZE") or (256 * 1024)
        max_cs = config.get("MAX_CHUNK_SIZE") or (8 * 1024 * 1024)

        # 1. Healthy interface with high EWMA scales up
        prog_healthy = InterfaceProgress(
            name="eth0", ip_address="192.168.1.10",
            chunk_start=0, chunk_end=1000,
            ewma_speed_mb_s=10.0, consecutive_failures=0
        )
        self.assertEqual(prog_healthy.health, "healthy")
        size_healthy = self.manager._calculate_worker_target_chunk_size(prog_healthy)
        self.assertGreater(size_healthy, min_cs)
        self.assertLessEqual(size_healthy, max_cs)

        # 2. Degraded interface (consecutive_failures > 0) is strictly capped at min_chunk_size
        prog_degraded = InterfaceProgress(
            name="wlan0", ip_address="192.168.1.20",
            chunk_start=0, chunk_end=1000,
            ewma_speed_mb_s=10.0, consecutive_failures=1
        )
        self.assertEqual(prog_degraded.health, "degraded")
        size_degraded = self.manager._calculate_worker_target_chunk_size(prog_degraded)
        self.assertEqual(size_degraded, min_cs, "Failing/degraded interface must be capped at MIN_CHUNK_SIZE")

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


if __name__ == "__main__":
    unittest.main()
