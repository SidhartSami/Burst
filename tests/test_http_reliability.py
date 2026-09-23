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
)

# Test payload: 256 KB deterministic data
TEST_DATA = bytes([(i * 31 + 7) % 256 for i in range(256 * 1024)])
TEST_DATA_HASH = hashlib.sha256(TEST_DATA).hexdigest()


class MockHttpHandler(http.server.BaseHTTPRequestHandler):
    """Configurable HTTP handler simulating various server behaviors."""
    etag = "v1-valid-etag"
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
    request_counts = {}

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(TEST_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", f'"{self.etag}"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        endpoint = self.path.split("?")[0]
        self.request_counts[endpoint] = self.request_counts.get(endpoint, 0) + 1
        count = self.request_counts[endpoint]

        # Fail first N requests simulation
        if endpoint == "/fail_first" and count <= self.fail_first_n_requests:
            self.send_response(503)
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

        range_header = self.headers.get("Range")

        # Fallback / No-range endpoint
        if endpoint in self.no_range_endpoints or not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", f'"{self.etag}"')
            self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
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
                self.send_header("ETag", f'"{self.etag}"')
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

        # Wrong Content-Range simulation
        if endpoint in self.wrong_range_endpoints and range_header != "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes 0-10/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            self.send_header("ETag", f'"{self.etag}"')
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
            self.send_header("ETag", f'"{self.etag}"')
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1] + b"Z" * 500)
            return

        # Stall after initial bytes simulation
        if endpoint in self.stall_after_bytes_endpoints and count == 1:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            self.send_header("ETag", f'"{self.etag}"')
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
            self.send_header("ETag", f'"{self.etag}"')
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
            self.send_header("ETag", f'"{self.etag}"')
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
        self.send_header("ETag", f'"{self.etag}"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")

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
        MockHttpHandler.request_counts.clear()
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


if __name__ == "__main__":
    unittest.main()
