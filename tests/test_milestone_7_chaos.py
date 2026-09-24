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
import libtorrent as lt
from torrent import (
    TorrentJob,
    _clean_stray_metadata_files,
    _make_settings,
    _load_dht_state,
    start_torrent_download,
    active_torrents,
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

    # -----------------------------------------------------------------------
    # 8. Torrent Chaos: Truncated bencoded resume data & DHT state
    # -----------------------------------------------------------------------
    def test_torrent_truncated_bencoded_resume_and_dht_data(self):
        truncated_bencoded = b"d4:nodes32:some_truncated_binary_without_closing"
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_bytes", return_value=truncated_bencoded):
            mock_ses = MagicMock()
            # _load_dht_state must handle corrupt/truncated bencoded bytes gracefully
            _load_dht_state(mock_ses)
            # load_state must not be called with broken payload
            mock_ses.load_state.assert_not_called()

        job = TorrentJob(
            magnet_uri="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=TruncTest",
            output_path=str(self.out_dir),
            interface_ips=["127.0.0.1"],
            resume_data={"bencoded_fastresume": b"d5:filesl...truncated", "progress": "invalid"},
        )
        self.assertEqual(job.progress, 0.0)

    # -----------------------------------------------------------------------
    # 9. Torrent Chaos: Multi-interface download with mid-download interface drop
    # -----------------------------------------------------------------------
    async def test_torrent_chaos_interface_loss_mid_download(self):
        test_data = b"T" * 32768
        seeder_dir = self.out_dir / "t_chaos_seed"
        client_dir = self.out_dir / "t_chaos_client"
        content_dir = seeder_dir / "t_chaos"
        content_dir.mkdir(parents=True, exist_ok=True)
        client_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "payload.bin").write_bytes(test_data)

        fs = lt.file_storage()
        fs.add_file("t_chaos/payload.bin", len(test_data))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, str(seeder_dir))
        tor_bytes = lt.bencode(ct.generate())
        tor_file = self.out_dir / "t_chaos.torrent"
        tor_file.write_bytes(tor_bytes)

        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(str(tor_file))
        s_atp.save_path = str(seeder_dir)
        s_h = seeder_ses.add_torrent(s_atp)
        for _ in range(150):
            if s_h.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_h.status().is_seeding)
        seeder_port = seeder_ses.listen_port()

        job = await start_torrent_download(
            magnet_uri=str(tor_file),
            output_path=str(client_dir),
            interface_ips=["127.0.0.1", "127.0.0.2"],
        )
        await asyncio.sleep(0.1)
        for _, h in job.handles:
            h.connect_peer(("127.0.0.1", seeder_port))

        # Drop second interface session mid-download
        if len(job.sessions) > 1:
            dropped_ip, dropped_ses = job.sessions.pop(1)
            if len(job.handles) > 1:
                _, dropped_h = job.handles.pop(1)
                try:
                    dropped_ses.remove_torrent(dropped_h)
                except Exception:
                    pass

        # Remaining interface must complete the transfer
        main_handle = job.handles[0][1]
        for _ in range(300):
            if main_handle.status().is_seeding or (main_handle.file_progress() and main_handle.file_progress()[0] == len(test_data)):
                break
            await asyncio.sleep(0.05)

        dest = client_dir / "t_chaos" / "payload.bin"
        for _ in range(100):
            if dest.exists() and dest.stat().st_size == len(test_data):
                break
            await asyncio.sleep(0.05)

        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(test_data))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), hashlib.sha256(test_data).hexdigest())

        job._running = False
        try:
            seeder_ses.remove_torrent(s_h)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 10. Torrent Chaos: Mid-download interruption, state persistence & restart
    # -----------------------------------------------------------------------
    async def test_torrent_chaos_restart_mid_download(self):
        test_data = b"R" * 65536  # 4 pieces of 16384
        seeder_dir = self.out_dir / "t_restart_seed"
        client_dir = self.out_dir / "t_restart_client"
        content_dir = seeder_dir / "t_restart"
        content_dir.mkdir(parents=True, exist_ok=True)
        client_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "file.bin").write_bytes(test_data)

        fs = lt.file_storage()
        fs.add_file("t_restart/file.bin", len(test_data))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, str(seeder_dir))
        tor_file = self.out_dir / "t_restart.torrent"
        tor_file.write_bytes(lt.bencode(ct.generate()))

        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(str(tor_file))
        s_atp.save_path = str(seeder_dir)
        s_h = seeder_ses.add_torrent(s_atp)
        for _ in range(150):
            if s_h.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_h.status().is_seeding)
        seeder_port = seeder_ses.listen_port()

        job1 = await start_torrent_download(
            magnet_uri=str(tor_file),
            output_path=str(client_dir),
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)
        h1 = job1.handles[0][1]
        h1.connect_peer(("127.0.0.1", seeder_port))

        for _ in range(200):
            if h1.file_progress() and h1.file_progress()[0] >= 16384:
                break
            await asyncio.sleep(0.05)

        state1 = job1.to_dict()
        job1._running = False
        for _, s in list(job1.sessions):
            try:
                s.remove_torrent(h1)
            except Exception:
                pass
        job1.sessions.clear()
        job1.handles.clear()
        active_torrents.clear()

        # Session 2: Resume from saved state and finish downloading
        job2 = await start_torrent_download(
            magnet_uri=str(tor_file),
            output_path=str(client_dir),
            interface_ips=["127.0.0.1"],
            resume_data=state1,
        )
        await asyncio.sleep(0.1)
        h2 = job2.handles[0][1]
        h2.connect_peer(("127.0.0.1", seeder_port))

        for _ in range(300):
            if h2.file_progress() and h2.file_progress()[0] == len(test_data):
                break
            await asyncio.sleep(0.05)

        dest = client_dir / "t_restart" / "file.bin"
        for _ in range(100):
            if dest.exists() and dest.stat().st_size == len(test_data):
                break
            await asyncio.sleep(0.05)

        self.assertTrue(dest.exists())
        self.assertEqual(dest.stat().st_size, len(test_data))
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), hashlib.sha256(test_data).hexdigest())

        job2._running = False
        try:
            seeder_ses.remove_torrent(s_h)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 11. Loopback-only settings verification (Requirement 7)
    # -----------------------------------------------------------------------
    def test_non_loopback_settings_enable_all_network_features(self):
        real_settings = _make_settings("192.168.0.103")
        self.assertEqual(real_settings["outgoing_interfaces"], "192.168.0.103")
        self.assertTrue(real_settings["enable_lsd"])
        self.assertTrue(real_settings["enable_natpmp"])
        self.assertTrue(real_settings["enable_upnp"])
        self.assertTrue(real_settings["listen_interfaces"].startswith("192.168.0.103:"))

        loopback_settings = _make_settings("127.0.0.1")
        self.assertNotIn("outgoing_interfaces", loopback_settings)
        self.assertFalse(loopback_settings["enable_lsd"])
        self.assertFalse(loopback_settings["enable_natpmp"])
        self.assertFalse(loopback_settings["enable_upnp"])


if __name__ == "__main__":
    unittest.main()

