"""
Tests for Burst Scheduler, Multi-Interface Routing, Health Recovery, and Bounded Wait (Milestone 3/4).

Verifies:
- Test D: Retry with exponential backoff
- Test E: Per-worker stall watchdog
- Test I: Interface failure routing and requeue
- Test J: Concurrent multi-worker chunk completion & integrity
- Test K: Mid-download interface failure & rollback
- Test N: Stall after initial bytes watchdog trigger & recovery
- Test P: Interface health state exposure (healthy, degraded, excluded)
- Test Q: Slow-but-progressing transfer is not stalled
- Test R: Degraded to healthy recovery and temporary exclusion expiration
- Test V: All interfaces excluded terminates cleanly with informative error
- Test W: Deterministic exponential backoff progression with mocked jitter
- Test X: Stall watchdog clock injection
- Test Z: Adaptive chunk planning (warmup, steady, tail, conservation)
- Test ZA: Work-stealing queue with real worker and asymmetric speeds
- Test ZB: Per-interface stats (bytes, EWMA, request/success/failure/retry/stall, workers) in API payload
- Test ZC: Scheduler caps failing/degraded interfaces at MIN_CHUNK_SIZE
- Test ZD: Interface health recovery tested against real EWMA and failure data
- Test ZE: Non-uniform chunk plan persistence, resume, and exact boundaries
- Test ZF: Mid-download chunk slice, kill, resume with exact boundaries, exactly-once commits, SHA256
- Test ZG: Single interface bounded wait and failed, resumable transition
- Test ZK: Worker cancellation between ASSIGNED and DOWNLOADING returns chunk to PENDING
- Test ZL: Waiting state and countdown persist sanely across restart
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _json
import os
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add backend and tests to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import config
from downloader import (
    Chunk,
    ChunkStatus,
    DownloadJob,
    InterfaceProgress,
    StalledDownloadError,
    calculate_backoff,
    plan_adaptive_chunks,
)
from test_fixtures import (
    BaseHttpTest,
    MockHttpHandler,
    TEST_DATA,
    TEST_DATA_HASH,
)


class TestScheduler(BaseHttpTest):
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
    # Test V — All interfaces excluded terminates cleanly with informative error
    # -----------------------------------------------------------------------
    async def test_v_all_interfaces_excluded(self):
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
                self.assertEqual(d10, 10.0)

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
    # Test Z — Milestone 3 Adaptive chunk planning (warmup, steady, tail, conservation)
    # -----------------------------------------------------------------------
    def test_z_adaptive_chunk_planning(self):
        size = 64 * 1024 * 1024  # 64 MB
        base_cs = 2 * 1024 * 1024
        min_cs = 256 * 1024
        max_cs = 8 * 1024 * 1024

        # Uniform mode
        uniform_ranges = plan_adaptive_chunks(
            expected_size=size,
            latencies={"192.168.1.1": 20.0, "10.0.0.1": 80.0},
            base_chunk_size=base_cs,
            min_chunk_size=min_cs,
            max_chunk_size=max_cs,
            num_interfaces=2,
        )
        self.assertGreater(len(uniform_ranges), 0)
        self.assertEqual(uniform_ranges[0][1], 0)
        self.assertEqual(uniform_ranges[-1][2], size - 1)
        total_u = sum(r[2] - r[1] + 1 for r in uniform_ranges)
        self.assertEqual(total_u, size, "Uniform mode: total bytes must equal expected_size")
        for i, (_, s, e) in enumerate(uniform_ranges[:-1]):
            self.assertLessEqual(e - s + 1, base_cs)

        # Warm-up/tail mode
        ranges = plan_adaptive_chunks(
            expected_size=size,
            latencies={"192.168.1.1": 20.0, "10.0.0.1": 80.0},
            base_chunk_size=base_cs,
            min_chunk_size=min_cs,
            max_chunk_size=max_cs,
            num_interfaces=2,
            enable_warmup_tail=True,
        )

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

        warmup_size = ranges[0][2] - ranges[0][1] + 1
        self.assertEqual(warmup_size, min_cs, "Warm-up chunk must start at min_chunk_size")

        mid_chunk = ranges[len(ranges) // 2]
        mid_size = mid_chunk[2] - mid_chunk[1] + 1
        self.assertGreater(mid_size, warmup_size, "Steady-state chunk must be larger than warmup chunk")

        tail_chunk = ranges[-1]
        tail_size = tail_chunk[2] - tail_chunk[1] + 1
        self.assertEqual(tail_size, min_cs, "Tail chunk must taper to min_chunk_size to eliminate stragglers")

        single = plan_adaptive_chunks(100 * 1024, base_chunk_size=base_cs, min_chunk_size=min_cs)
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0], (0, 0, 100 * 1024 - 1))

    # -----------------------------------------------------------------------
    # Test ZA — Milestone 3 Work-stealing queue with real worker and asymmetric speeds
    # -----------------------------------------------------------------------
    async def test_za_work_stealing_differential_throughput(self):
        dest = self.out_dir / "test_za_real.bin"
        temp_dir = dest.parent / ".burst_test_za"
        temp_dir.mkdir(parents=True, exist_ok=True)

        url = f"{self.base_url}/za_worksteal.bin"
        chunk_size = 16 * 1024
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

        iface_fast = {"name": "FastLoopback", "ip_address": "127.0.0.1"}
        iface_slow = {"name": "SlowLoopback", "ip_address": "127.0.0.2"}

        prog_fast = InterfaceProgress(name="FastLoopback", ip_address="127.0.0.1", chunk_start=0, chunk_end=total_size)
        prog_slow = InterfaceProgress(name="SlowLoopback", ip_address="127.0.0.2", chunk_start=0, chunk_end=total_size)
        job.progress["127.0.0.1"] = prog_fast
        job.progress["127.0.0.2"] = prog_slow

        task_fast = asyncio.create_task(self.manager._worker(job, iface_fast, queue, chunk_files))
        task_slow = asyncio.create_task(self.manager._worker(job, iface_slow, queue, chunk_files))

        await asyncio.gather(task_fast, task_slow)

        self.assertEqual(len([c for c in job.chunks.values() if c.status == ChunkStatus.COMPLETE]), num_chunks)
        for i in range(num_chunks):
            self.assertTrue(chunk_files[i].exists())
            self.assertEqual(chunk_files[i].stat().st_size, chunk_size)

        self.assertEqual(prog_fast.chunks_completed + prog_slow.chunks_completed, num_chunks)
        self.assertGreaterEqual(prog_fast.chunks_completed, prog_slow.chunks_completed * 2,
            f"Fast worker ({prog_fast.chunks_completed}) should steal significantly more chunks than slow worker ({prog_slow.chunks_completed})")
        self.assertGreater(prog_fast.downloaded, prog_slow.downloaded)

    # -----------------------------------------------------------------------
    # Test ZB — Per-interface stats in API payload
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
        self.assertIn("bytes", stats)
        self.assertIn("ewma_speed_mb_s", stats)
        self.assertIn("request_count", stats)
        self.assertIn("success_count", stats)
        self.assertIn("failure_count", stats)
        self.assertIn("retry_count", stats)
        self.assertIn("stall_count", stats)
        self.assertIn("last_success_time", stats)
        self.assertIn("active_workers", stats)

        self.assertEqual(stats["bytes"], len(TEST_DATA))
        self.assertGreater(stats["request_count"], 0)
        self.assertEqual(stats["success_count"], stats["request_count"])
        self.assertEqual(stats["failure_count"], 0)
        self.assertEqual(stats["retry_count"], 0)
        self.assertEqual(stats["stall_count"], 0)
        self.assertGreater(stats["ewma_speed_mb_s"], 0.0)
        self.assertIsNotNone(stats["last_success_time"])
        self.assertGreater(stats["last_success_time"], 0.0)
        self.assertEqual(stats["active_workers"], 0)

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

            prog_healthy = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=1.5, consecutive_failures=0
            )
            self.assertEqual(prog_healthy.health, "healthy")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_healthy), 3145728)

            prog_high = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=10.0, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_high), max_cs)

            prog_low = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=0.05, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_low), min_cs)

            prog_zero = InterfaceProgress(
                name="eth0", ip_address="192.168.1.10",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=0.0, consecutive_failures=0
            )
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_zero), base_cs)

            prog_degraded = InterfaceProgress(
                name="wlan0", ip_address="192.168.1.20",
                chunk_start=0, chunk_end=1000,
                ewma_speed_mb_s=10.0, consecutive_failures=1
            )
            self.assertEqual(prog_degraded.health, "degraded")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_degraded), min_cs)

            prog_fail_rate = InterfaceProgress(
                name="wlan0", ip_address="192.168.1.20",
                chunk_start=0, chunk_end=1000,
                request_count=4, failure_count=2, consecutive_failures=0
            )
            self.assertEqual(prog_fail_rate.health, "degraded")
            self.assertEqual(self.manager._calculate_worker_target_chunk_size(prog_fail_rate), min_cs)

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

        prog.consecutive_failures = 1
        prog.failure_count = 1
        self.assertEqual(prog.health, "degraded")
        capped_size = self.manager._calculate_worker_target_chunk_size(prog)
        min_cs = config.get("MIN_CHUNK_SIZE") or (256 * 1024)
        self.assertEqual(capped_size, min_cs)

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        p = job.progress["127.0.0.1"]
        self.assertEqual(p.health, "healthy")
        self.assertEqual(p.consecutive_failures, 0)
        self.assertGreater(p.success_count, 0)
        self.assertGreater(p.ewma_speed_mb_s, 0.0)
        self.assertIsNotNone(p.last_success_time)

        recovered_size = self.manager._calculate_worker_target_chunk_size(p)
        self.assertGreaterEqual(recovered_size, min_cs)

    # -----------------------------------------------------------------------
    # Test ZE — Non-uniform chunk plan persistence, resume, and exact boundaries
    # -----------------------------------------------------------------------
    async def test_ze_non_uniform_chunk_plan_persistence_and_resume_exact_boundaries(self):
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

        temp_dir = dest.parent / f".burst_{state['job_id']}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "chunk_00000.part").write_bytes(TEST_DATA[:65536])

        job = await self.manager.resume_job_from_state(state, self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test ZF — Mid-download chunk slicing, kill, resume with exact boundaries
    # -----------------------------------------------------------------------
    async def test_zf_mid_download_chunk_slice_kill_resume_exact_boundaries(self):
        url = f"{self.base_url}/slice_test.bin"
        dest = self.out_dir / "test_zf_sliced.bin"
        temp_dir = dest.parent / ".burst_test_zf"
        temp_dir.mkdir(parents=True, exist_ok=True)

        total_size = len(TEST_DATA)
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

            for dst_path, count in replace_counts.items():
                self.assertEqual(count, 1, f"os.replace called {count} times for {dst_path} (expected 1)")

            persisted_state = job.to_dict()

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

            active_jobs_file = self.out_dir / "burst_active_jobs.json"
            active_jobs_file.write_text(_json.dumps({"downloads": [persisted_state]}), encoding="utf-8")
            self.assertTrue(active_jobs_file.exists())
            loaded = _json.loads(active_jobs_file.read_text(encoding="utf-8"))
            loaded_ranges = loaded["downloads"][0]["_ranges"]
            self.assertEqual(len(loaded_ranges), len(ranges), "Range count must persist in burst_active_jobs.json")
            for orig, loaded_r in zip(ranges, loaded_ranges):
                self.assertEqual(list(orig), list(loaded_r), "Sliced boundary must persist to burst_active_jobs.json")

            resumed_job = await self.manager.resume_job_from_state(persisted_state, self.iface)
            resumed_task = self.manager._job_tasks[resumed_job.job_id]
            await resumed_task

            self.assertEqual(resumed_job.status, "completed")
            self.assertTrue(dest.exists())
            self.assertEqual(dest.stat().st_size, total_size)
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # Test ZG — Single interface bounded wait and failed, resumable transition
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
            self.assertTrue(job.is_resumable)
            self.assertTrue(job.to_dict()["is_resumable"])

            res = await self.manager.resume_job(job.job_id)
            self.assertEqual(res["status"], "resumed")
            await self.manager.cancel_job(job.job_id)

    # -----------------------------------------------------------------------
    # Test ZK — Worker cancellation between ASSIGNED and DOWNLOADING returns to PENDING
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

        self.assertEqual(chunk.status, ChunkStatus.PENDING)
        self.assertIsNone(chunk.assigned_interface)
        self.assertFalse(queue.empty())
        requeued_chunk = queue.get_nowait()
        self.assertEqual(requeued_chunk.chunk_id, 0)

    # -----------------------------------------------------------------------
    # Test ZL — Waiting state and countdown persist sanely across restart
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

        active_jobs_file = self.out_dir / "burst_active_jobs_zl.json"
        active_jobs_file.write_text(_json.dumps({"downloads": [persisted]}), encoding="utf-8")
        loaded = _json.loads(active_jobs_file.read_text(encoding="utf-8"))
        loaded_job_data = loaded["downloads"][0]
        self.assertEqual(loaded_job_data["status"], "waiting")

        resumed = await self.manager.resume_job_from_state(loaded_job_data, self.iface)
        self.assertIsNotNone(resumed)
        self.assertTrue(resumed.is_resumable)
        self.assertIsNone(getattr(resumed, "_reconnect_wait_start", None))
        await self.manager.cancel_job(resumed.job_id)

    # -----------------------------------------------------------------------
    # Test ZM — Add interface succeeds during waiting and waiting_reconnect state
    # -----------------------------------------------------------------------
    async def test_zm_add_interface_during_waiting_state(self):
        for test_status in ("waiting", "waiting_reconnect"):
            job = DownloadJob(
                job_id=f"test_zm_{test_status}",
                url=f"{self.base_url}/test_zm.bin",
                output_path=str(self.out_dir / f"zm_{test_status}.bin"),
                expected_size=1024,
                supports_ranges=True,
                status=test_status,
                error="All interfaces unavailable — waiting to reconnect (120s remaining, resumable)",
            )
            job._queue = asyncio.Queue()
            job._chunk_files = {}
            job._reconnect_wait_start = time.time() - 30.0
            self.manager.jobs[job.job_id] = job

            res = await self.manager.add_interface(job.job_id, {"ip_address": "127.0.0.1", "name": "Loopback"})
            self.assertTrue(res.get("spawned") or res.get("reused"))
            self.assertEqual(job.status, "downloading")
            self.assertIsNone(job._reconnect_wait_start)
            self.assertIsNone(job.error)
            await self.manager.cancel_job(job.job_id)

    # -----------------------------------------------------------------------
    # Test ZN — Resume succeeds during waiting and waiting_reconnect state
    # -----------------------------------------------------------------------
    async def test_zn_resume_job_during_waiting_state(self):
        for test_status in ("waiting", "waiting_reconnect"):
            job = DownloadJob(
                job_id=f"test_zn_{test_status}",
                url=f"{self.base_url}/test_zn.bin",
                output_path=str(self.out_dir / f"zn_{test_status}.bin"),
                expected_size=1024,
                supports_ranges=True,
                status=test_status,
                error="All interfaces unavailable — waiting to reconnect (120s remaining, resumable)",
            )
            job._queue = asyncio.Queue()
            job._chunk_files = {}
            job._reconnect_wait_start = time.time() - 30.0
            job.progress["127.0.0.1"] = InterfaceProgress(name="L1", ip_address="127.0.0.1", chunk_start=0, chunk_end=1023)
            self.manager.jobs[job.job_id] = job

            res = await self.manager.resume_job(job.job_id)
            self.assertEqual(res.get("status"), "resumed")
            self.assertEqual(job.status, "downloading")
            self.assertIsNone(job._reconnect_wait_start)
            self.assertIsNone(job.error)
            await self.manager.cancel_job(job.job_id)

    # -----------------------------------------------------------------------
    # Test ZO — Dynamic interface migration on network change (e.g. Wi-Fi switch)
    # -----------------------------------------------------------------------
    async def test_zo_handle_interface_change_migration(self):
        job = DownloadJob(
            job_id="test_zo_migration",
            url=f"{self.base_url}/test_zo.bin",
            output_path=str(self.out_dir / "zo_mig.bin"),
            expected_size=1024,
            supports_ranges=True,
            status="downloading",
        )
        job._queue = asyncio.Queue()
        job._chunk_files = {}
        old_ip = "192.168.1.100"
        new_ip = "192.168.0.50"
        job.progress[old_ip] = InterfaceProgress(name="Wi-Fi", ip_address=old_ip, chunk_start=0, chunk_end=1023)
        self.manager.jobs[job.job_id] = job

        # Simulate Wi-Fi network switch
        removed = [{"name": "Wi-Fi", "ip_address": old_ip}]
        added = [{"name": "Wi-Fi", "ip_address": new_ip}]
        await self.manager.handle_interface_change(added, removed)

        # Old IP should be purged from job.progress
        self.assertNotIn(old_ip, job.progress)
        # New IP should be actively added
        self.assertIn(new_ip, job.progress)
        self.assertEqual(job.progress[new_ip].name, "Wi-Fi")
        await self.manager.cancel_job(job.job_id)

    # -----------------------------------------------------------------------
    # Test ZP — Zero/Infinite reconnect timeout does not report remaining countdown
    # -----------------------------------------------------------------------
    async def test_zp_infinite_reconnect_timeout(self):
        job = DownloadJob(
            job_id="test_zp_infinite",
            url=f"{self.base_url}/test_zp.bin",
            output_path=str(self.out_dir / "zp_inf.bin"),
            expected_size=1024,
            supports_ranges=True,
            status="waiting_reconnect",
            error="All interfaces unavailable — waiting to reconnect (resumable)",
        )
        job._reconnect_wait_start = time.time() - 3600.0  # 1 hour ago
        job._reconnect_wait_max = 0.0  # 0 = infinite

        d = job.to_dict()
        self.assertEqual(d["status"], "waiting_reconnect")
        self.assertEqual(d["waiting_remaining_s"], 0.0)


if __name__ == "__main__":
    unittest.main()


