"""
Tests for Burst HTTP Engine Core Functionality (Milestone 2/3).

Verifies:
- Test A: Range support (HTTP 206)
- Test B: No range support (HTTP 200 fallback)
- Test C: Wrong byte count rejection & retry
- Test F: Safe resume without redownloading complete chunks (request-count verified)
- Test G: Changed ETag detection and safe restart (request-count verified)
- Test H: Atomic chunk write (no partial .part files)
- Test L: Too-many-bytes rejection & terminal failure
- Test M: Wrong-Content-Range rejection & terminal failure
- Test O: Mid-download HTTP 200 rejection & terminal failure
- Test S: If-Range header and mid-flight origin mutation detection
- Test T: Weak ETag handling in If-Range (RFC 9110 §13.1.2)
- Test U: Mutation abort outcome (purge stale chunks, fail terminal)
- Test Y: Production config constants and defaults assertion
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import time
import unittest
from pathlib import Path

# Add backend and tests to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import config
from downloader import (
    Chunk,
    DownloadJob,
    analyze_url,
)
from test_fixtures import (
    BaseHttpTest,
    MockHttpHandler,
    TEST_DATA,
    TEST_DATA_HASH,
)


class TestHttpCore(BaseHttpTest):
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


if __name__ == "__main__":
    unittest.main()
