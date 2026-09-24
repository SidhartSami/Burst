"""
Tests for Advanced HTTP Features (Milestone 4):
- Disk space preflight & mid-download ENOSPC
- Redirect loops, hop caps, status semantics (301/302/303/307/308)
- Cross-host header stripping & resume confidence lowering
- Protocol downgrade refusal & URL/exception redaction
- Speculative tail racing unit & integration tests (zj1 to zj5)
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import requests
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
    InsufficientDiskSpaceError,
    check_disk_space_preflight,
    is_non_retryable_error,
    redact_url,
    sanitize_exception_text,
)
from test_fixtures import (
    BaseHttpTest,
    MockHttpHandler,
    TEST_DATA,
    TEST_DATA_HASH,
)


class TestAdvancedHttp(BaseHttpTest):
    # -----------------------------------------------------------------------
    # Test ZH — Milestone 4: Preflight disk space check per volume
    # -----------------------------------------------------------------------
    def test_zh_disk_space_preflight(self):
        dest = self.out_dir / "test_zh_preflight.bin"
        chunk_files = {
            0: self.out_dir / "chunk_00000.part",
            1: self.out_dir / "chunk_00001.part",
        }
        chunk_files[0].write_bytes(b"A" * (10 * 1024 * 1024))
        expected_size = 20 * 1024 * 1024

        with patch("shutil.disk_usage", return_value=type("Usage", (), {"total": 500*1024*1024, "used": 400*1024*1024, "free": 100*1024*1024})()):
            check_disk_space_preflight(dest, expected_size, chunk_files=chunk_files, supports_ranges=True)

        with patch("shutil.disk_usage", return_value=type("Usage", (), {"total": 500*1024*1024, "used": 485*1024*1024, "free": 15*1024*1024})()):
            with self.assertRaises(InsufficientDiskSpaceError) as ctx:
                check_disk_space_preflight(dest, expected_size, chunk_files=chunk_files, supports_ranges=True)
            self.assertEqual(ctx.exception.errno, errno.ENOSPC)
            self.assertTrue(is_non_retryable_error(ctx.exception))
            self.assertIn("Insufficient disk space", str(ctx.exception))

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
                with self.assertRaises(InsufficientDiskSpaceError) as ctx:
                    check_disk_space_preflight(dest_vol1, expected_size=20*1024*1024, supports_ranges=False)
                self.assertEqual(ctx.exception.errno, errno.ENOSPC)
                self.assertIn("Insufficient disk space", str(ctx.exception))

                check_disk_space_preflight(dest_vol2, expected_size=20*1024*1024, supports_ranges=False)

    # -----------------------------------------------------------------------
    # Test ZI1 — Milestone 4: Redirect loop and hop cap enforcement against real server
    # -----------------------------------------------------------------------
    async def test_zi1_redirect_loop_and_hop_cap(self):
        url = f"{self.base_url}/redirect_loop"
        dest = self.out_dir / "zi1.bin"

        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "failed")
        self.assertFalse(dest.exists())

    # -----------------------------------------------------------------------
    # Test ZI2 — Milestone 4: 301/302/303/307/308 redirect semantics executed through engine
    # -----------------------------------------------------------------------
    async def test_zi2_redirect_http_status_semantics(self):
        for status_code in (301, 302, 303, 307, 308):
            url = f"{self.base_url}/redirect_status_{status_code}"
            dest = self.out_dir / f"zi2_{status_code}.bin"

            job = await self.manager.create_job(url, str(dest), self.iface)
            task = self.manager._job_tasks[job.job_id]
            await task

            self.assertEqual(job.status, "completed", f"Download should succeed following HTTP {status_code} redirect")
            self.assertTrue(dest.exists())
            self.assertEqual(dest.stat().st_size, len(TEST_DATA))
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

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
            self.manager._download_with_requests(
                job, "127.0.0.1", "https://cdn.thirdparty.com/download.zip",
                self.out_dir / "zi3.tmp", "wb", in_headers, prog, time.perf_counter(), uuid.uuid4()
            )

            self.assertNotIn("Authorization", captured_headers)
            self.assertNotIn("Proxy-Authorization", captured_headers)
            self.assertNotIn("Cookie", captured_headers)
            self.assertNotIn("If-Range", captured_headers)

    # -----------------------------------------------------------------------
    # Test ZI3B — Milestone 4: Cross-host download lowers resume_confidence
    # -----------------------------------------------------------------------
    async def test_zi3b_cross_host_lowers_resume_confidence(self):
        dest = self.out_dir / "test_zi3b.bin"
        job = DownloadJob(
            job_id="zi3b",
            url="https://origin.example.com/file.bin",
            final_url="https://cdn.edgecloud.net/file.bin",
            output_path=str(dest),
            expected_size=len(TEST_DATA),
            supports_ranges=True,
            resume_confidence="high",
        )

        with patch("downloader.analyze_url") as mock_analyze:
            mock_analyze.return_value = MagicMock(
                content_length=len(TEST_DATA),
                supports_ranges=True,
                etag='"cdn-etag"',
                last_modified="Wed, 23 Sep 2026 12:00:00 GMT",
                final_url="https://cdn.edgecloud.net/file.bin",
                range_error_reason=None,
            )
            # Run resume wrapper
            await self.manager._resume_job_wrapper(job, self.iface)

            # Cross-host redirect (origin.example.com != cdn.edgecloud.net) must lower resume_confidence to "low"
            self.assertEqual(job.resume_confidence, "low")

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

        raw_exc_text = (
            "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='cdn.example.com', port=443): "
            "Max retries exceeded with url: /download.zip?token=SUPERSECRET123&expire=99999 (Caused by ConnectTimeoutError)"
        )
        cleaned_exc = sanitize_exception_text(raw_exc_text)
        self.assertNotIn("SUPERSECRET123", cleaned_exc)
        self.assertIn("[REDACTED]", cleaned_exc)

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
            def fake_get(target_url, **kwargs):
                calls.append(target_url)
                resp = MagicMock()
                resp.url = target_url
                resp.history = []
                if target_url == candidate_cdn:
                    resp.status_code = 403
                else:
                    resp.status_code = 200
                    resp.iter_content.return_value = [b"Z" * 100]
                    resp.raise_for_status = MagicMock()
                return resp

            sess_inst.get = fake_get
            mock_sess.return_value = sess_inst

            self.manager._download_with_requests(
                job, "127.0.0.1", candidate_cdn,
                self.out_dir / "zi6.tmp", "wb", None, prog, time.perf_counter(), uuid.uuid4()
            )

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

        replace_calls = []
        real_replace = os.replace
        def mock_replace(src, dst):
            replace_calls.append((src, dst))
            return real_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            w1 = uuid.uuid4()
            w1_tmp = part_file.with_suffix(f".tmp_{w1.hex[:8]}")
            w1_tmp.write_bytes(b"A" * 1024)

            won1 = await self.manager._download_range(
                job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w1, chunk=chunk
            )
            self.assertTrue(won1)
            self.assertEqual(len(replace_calls), 1)

            w2 = uuid.uuid4()
            w2_tmp = part_file.with_suffix(f".tmp_{w2.hex[:8]}")
            w2_tmp.write_bytes(b"B" * 1024)

            won2 = await self.manager._download_range(
                job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w2, chunk=chunk
            )
            self.assertFalse(won2)
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

        def chunk_gen():
            yield b"A" * 256
            cancel_evt.set()
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

        res1, res2 = await asyncio.gather(
            self.manager._download_range(job, {"ip_address": "127.0.0.1", "name": "L1"}, (0, 1023), part_file, w1, chunk=chunk),
            self.manager._download_range(job, {"ip_address": "127.0.0.2", "name": "L2"}, (0, 1023), part_file, w2, chunk=chunk),
        )

        self.assertEqual(res1 + res2, 1, "Exactly one racer must win the claim (True + False = 1)")
        self.assertTrue(chunk._racing_claimed)
        self.assertTrue(part_file.exists())
        self.assertEqual(part_file.stat().st_size, 1024)

    # -----------------------------------------------------------------------
    # Test ZJ5 — Milestone 4/5: Real race through _parallel_download with SHA256 verification
    # -----------------------------------------------------------------------
    async def test_zj5_tail_racing_real_race_sha256(self):
        url = f"{self.base_url}/real_racing_test.bin"
        dest = self.out_dir / "test_zj5_real_race.bin"

        two_ifaces = [
            {"name": "FastLoopback", "ip_address": "127.0.0.1"},
            {"name": "SlowLoopback", "ip_address": "127.0.0.2"},
        ]

        # Enable tail racing and configure low straggler threshold so racing triggers on straggler
        with patch.dict(config._DEFAULTS, {
            "ENABLE_TAIL_RACING": True,
            "BASE_CHUNK_SIZE": 64 * 1024,
            "MIN_CHUNK_SIZE": 64 * 1024,
        }):
            job = await self.manager.create_job(url, str(dest), two_ifaces)
            task = self.manager._job_tasks[job.job_id]
            await task

            self.assertEqual(job.status, "completed")
            self.assertTrue(dest.exists())
            self.assertEqual(dest.stat().st_size, len(TEST_DATA))
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)


if __name__ == "__main__":
    unittest.main()
