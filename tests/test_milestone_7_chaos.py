"""
Milestone 7 Hardening & Chaos Test Suite.
Scope:
- Stray-file scan after magnet metadata phase
- Corrupt resume data handling (HTTP and Torrent)
- Piece-union merge caching across monitor ticks
- Live/mock DHT bootstrap configuration check
- Chaos tests: interface loss mid-download, server stall watchdog & recovery, crash/restart persistence with SHA256 verification
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import config
from downloader import (
    Chunk,
    ChunkStatus,
    DownloadJob,
    DownloadManager,
    InterfaceProgress,
    is_non_retryable_error,
)
from torrent import (
    TorrentJob,
    _clean_stray_metadata_files,
    _make_settings,
    DHT_STATE_FILE,
)
from test_fixtures import (
    BaseHttpTest,
    MockHttpHandler,
    TEST_DATA,
    TEST_DATA_HASH,
)


class TestMilestone7Hardening(BaseHttpTest):
    # -----------------------------------------------------------------------
    # 1. Stray-file scan after magnet metadata phase
    # -----------------------------------------------------------------------
    def test_stray_file_cleanup_removes_zero_byte_files(self):
        output_dir = self.out_dir / "torrent_meta_test"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create a 0-byte placeholder (stray file created during metadata probe)
        stray_file = output_dir / "stray_zero.dat"
        stray_file.write_bytes(b"")

        # Create a legitimate non-empty file (actual data)
        real_file = output_dir / "real_payload.iso"
        real_file.write_bytes(b"EXISTING_DATA_DO_NOT_DELETE")

        # Mock libtorrent torrent_info with files matching both
        mock_ti = MagicMock()
        mock_ti.num_files.return_value = 2
        mock_ti.files().file_path.side_effect = lambda idx: "stray_zero.dat" if idx == 0 else "real_payload.iso"

        _clean_stray_metadata_files(output_dir, mock_ti)

        # 0-byte stray file should be unlinked
        self.assertFalse(stray_file.exists(), "0-byte stray file was not cleaned up!")
        # Legitimate file with data must be preserved
        self.assertTrue(real_file.exists(), "Legitimate non-zero file was mistakenly deleted!")
        self.assertEqual(real_file.read_bytes(), b"EXISTING_DATA_DO_NOT_DELETE")

    # -----------------------------------------------------------------------
    # 2. Corrupt-resume-data tests (HTTP and Torrent)
    # -----------------------------------------------------------------------
    async def test_http_corrupt_partial_chunk_discarded_and_redownloaded(self):
        dest = self.out_dir / "corrupt_resume.bin"
        temp_dir = self.out_dir / ".burst_tmp_corrupt_resume"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create chunk 0 with corrupt / truncated size (10 bytes instead of expected chunk size)
        part0 = temp_dir / "chunk_00000.part"
        part0.write_bytes(b"CORRUPT")

        # Pre-create chunk 1 with valid size (64 KB)
        part1 = temp_dir / "chunk_00001.part"
        part1.write_bytes(TEST_DATA[64 * 1024 : 128 * 1024])

        url = f"{self.base_url}/data_corrupt_test"
        job = await self.manager.create_job(url, str(dest), self.iface)
        task = self.manager._job_tasks[job.job_id]
        await task

        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        actual_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        self.assertEqual(actual_hash, TEST_DATA_HASH)

    def test_torrent_corrupt_resume_data_handling(self):
        # Malformed resume_data dictionary with corrupt field types
        corrupt_resume = {
            "progress": "invalid_not_a_float",
            "total_size": "not_an_int",
            "file_priorities": "corrupt_string_not_dict",
            "status": 12345,
            "bytes_per_interface": "bad_mapping",
        }

        # TorrentJob should handle malformed resume data gracefully without crash
        job = TorrentJob(
            magnet_uri="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=CorruptTest",
            output_path=str(self.out_dir),
            interface_ips=["127.0.0.1"],
            resume_data=corrupt_resume,
        )

        d = job.to_dict()
        self.assertIsInstance(d["progress"], float)
        self.assertEqual(d["progress"], 0.0)
        self.assertIsInstance(d["file_priorities"], dict)
        self.assertEqual(d["file_priorities"], {})
        self.assertIn(d["status"], ("fetching_metadata", "paused"))

    # -----------------------------------------------------------------------
    # 3. Piece-union merge caching across monitor ticks
    # -----------------------------------------------------------------------
    async def test_piece_union_cache_avoids_redundant_work(self):
        # Use loopback IPs so _monitor_download recognizes them as valid test interfaces
        job = TorrentJob(
            magnet_uri="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=UnionCacheTest",
            output_path=str(self.out_dir),
            interface_ips=["127.0.0.1", "127.0.0.2"],
        )

        # Create mock torrent_info with 10 pieces of 1000 bytes each
        mock_ti = MagicMock()
        mock_ti.num_pieces.return_value = 10
        mock_ti.piece_length.return_value = 1000
        mock_ti.total_size.return_value = 10000
        mock_ti.num_files.return_value = 1
        mock_ti.files().file_size.return_value = 10000
        job._torrent_info = mock_ti
        job.file_priorities = {0: 4}

        # Mock 2 handles
        h1 = MagicMock()
        s1 = MagicMock()
        s1.download_rate = 1000
        s1.upload_rate = 100
        s1.total_upload = 500
        s1.num_peers = 3
        s1.num_seeds = 1
        s1.total_wanted_done = 3000
        s1.total_done = 3000
        s1.total_payload_download = 3000
        s1.pieces = [True, True, True, False, False, False, False, False, False, False]
        s1.progress = 0.3
        s1.is_finished = False
        s1.is_seeding = False
        s1.state = 3
        s1.total_wanted = 10000
        h1.status.return_value = s1

        h2 = MagicMock()
        s2 = MagicMock()
        s2.download_rate = 1000
        s2.upload_rate = 100
        s2.total_upload = 500
        s2.num_peers = 3
        s2.num_seeds = 1
        s2.total_wanted_done = 2000
        s2.total_done = 2000
        s2.total_payload_download = 2000
        # union has pieces 0, 1, 2, 3 = 4 pieces = 4,000 bytes
        s2.pieces = [False, False, True, True, False, False, False, False, False, False]
        s2.progress = 0.2
        s2.is_finished = False
        s2.is_seeding = False
        s2.state = 3
        s2.total_wanted = 10000
        h2.status.return_value = s2

        job.handles = [("127.0.0.1", h1), ("127.0.0.2", h2)]

        from torrent import _monitor_download
        job._running = True
        mon_task = asyncio.create_task(_monitor_download(job))
        await asyncio.sleep(1.2)
        job._running = False
        await mon_task

        self.assertEqual(job.selected_downloaded, 4000)
        self.assertEqual(job._cached_merged_wanted_bytes, 4000)
        self.assertTrue(hasattr(job, "_cached_union_pieces"))
        self.assertEqual(len(job._cached_union_pieces), 10)

    # -----------------------------------------------------------------------
    # 4. Live / DHT bootstrap configuration check
    # -----------------------------------------------------------------------
    def test_dht_bootstrap_configuration_and_routing(self):
        settings = _make_settings(ip=None)
        self.assertTrue(settings["enable_dht"])
        self.assertIn("router.bittorrent.com:6881", settings["dht_bootstrap_nodes"])
        self.assertIn("router.utorrent.com:6881", settings["dht_bootstrap_nodes"])
        self.assertIn("dht.transmissionbt.com:6881", settings["dht_bootstrap_nodes"])
        self.assertIn("dht.libtorrent.org:25401", settings["dht_bootstrap_nodes"])
        self.assertTrue(str(DHT_STATE_FILE).endswith("dht_state.dat"))

    # -----------------------------------------------------------------------
    # 5. Chaos Tests: Interface loss mid-download
    # -----------------------------------------------------------------------
    async def test_chaos_interface_loss_during_download(self):
        dest = self.out_dir / "chaos_loss.bin"
        multi_iface = [
            {"name": "lo1", "ip_address": "127.0.0.1"},
            {"name": "lo2", "ip_address": "127.0.0.1"},
        ]

        url = f"{self.base_url}/chaos_loss_data"
        job = await self.manager.create_job(url, str(dest), multi_iface)
        task = self.manager._job_tasks[job.job_id]

        # Give it a brief moment to start downloading
        await asyncio.sleep(0.02)
        # Drop one of the interfaces cleanly
        try:
            await self.manager.remove_interface(job.job_id, "127.0.0.1")
        except Exception:
            pass

        # Job completes on remaining interface
        await task
        self.assertEqual(job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)

    # -----------------------------------------------------------------------
    # 6. Chaos Tests: Server stall watchdog and recovery
    # -----------------------------------------------------------------------
    async def test_chaos_server_stall_watchdog_and_recovery(self):
        dest = self.out_dir / "chaos_stall.bin"
        MockHttpHandler.stall_after_bytes_endpoints.add("/chaos_stall_data")
        url = f"{self.base_url}/chaos_stall_data"

        config.save_settings({"STALL_TIMEOUT_SECONDS": 0.2, "RETRY_BACKOFF_BASE": 0.01, "RETRY_BACKOFF_MAX": 0.05})
        try:
            job = await self.manager.create_job(url, str(dest), self.iface)
            task = self.manager._job_tasks[job.job_id]

            await task

            self.assertEqual(job.status, "completed")
            self.assertTrue(dest.exists())
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)
            self.assertGreaterEqual(job.to_dict()["total_stalls"], 1)
        finally:
            config.reset_settings()
            MockHttpHandler.stall_after_bytes_endpoints.clear()

    # -----------------------------------------------------------------------
    # 7. Chaos Tests: Crash and restart persistence with SHA256 integrity
    # -----------------------------------------------------------------------
    async def test_chaos_crash_restart_persistence(self):
        dest = self.out_dir / "crash_restart.bin"
        temp_dir = self.out_dir / ".burst_tmp_crash_restart"
        temp_dir.mkdir(parents=True, exist_ok=True)

        chunk_size = 64 * 1024
        # Chunk 0 was written to disk before crash
        part0 = temp_dir / "chunk_00000.part"
        part0.write_bytes(TEST_DATA[:chunk_size])

        # Active state saved before crash
        saved_state = {
            "job_id": "crash_job_1",
            "url": f"{self.base_url}/crash_restart_data",
            "output_path": str(dest),
            "expected_size": len(TEST_DATA),
            "supports_ranges": True,
            "status": "downloading",
            "resume_confidence": "high",
            "total_downloaded": chunk_size,
            "file_priorities": {},
            "etag": "v1-valid-etag",
            "_ranges": [
                [0, 0, chunk_size - 1],
                [1, chunk_size, len(TEST_DATA) - 1],
            ],
        }

        # New manager restarts and resumes from saved state
        resumed_job = await self.manager.resume_job_from_state(saved_state, self.iface)
        task = self.manager._job_tasks[resumed_job.job_id]
        await task

        self.assertEqual(resumed_job.status, "completed")
        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(TEST_DATA))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), TEST_DATA_HASH)


if __name__ == "__main__":
    unittest.main()
