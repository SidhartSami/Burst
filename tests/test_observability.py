"""
Tests for Milestone 6: Real-data Observability in Burst.
Scope:
- Per-interface speed and bytes (HTTP and Torrent)
- Interface health (failure/stall rates, consecutive failures, cooldown)
- Chunk-by-interface map built exclusively from actual commit events
- Worker state tracking (idle, downloading, retrying, completed)
- Retries and stalls counters
- Torrent observability: torrent-size / selected-size / downloaded-selected / bytes-per-interface
- HTTP state: resume_confidence, is_resumable, is_waiting, and waiting countdown
- Bounded payload size for large downloads via bucket aggregation
- Verification of ponytail debt ledger markers
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from downloader import (
    Chunk,
    ChunkStatus,
    DownloadJob,
    InterfaceProgress,
    RetryEvent,
    URLAnalysis,
)
from torrent import TorrentJob, active_torrents


class TestObservability(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir_path = Path(self.temp_dir.name)
        active_torrents.clear()

    def tearDown(self):
        active_torrents.clear()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 1. Per-interface speed and bytes (HTTP)
    # -----------------------------------------------------------------------
    def test_http_per_interface_speed_and_bytes(self):
        job = DownloadJob(
            job_id="job_obs_1",
            url="http://example.com/test.iso",
            output_path=str(self.dir_path / "test.iso"),
            expected_size=10_000_000,
        )
        p1 = InterfaceProgress(
            name="Wi-Fi",
            ip_address="192.168.1.50",
            chunk_start=0,
            chunk_end=5_000_000,
            downloaded=3_500_000,
            speed_mb_s=12.5,
        )
        p2 = InterfaceProgress(
            name="Ethernet",
            ip_address="192.168.1.60",
            chunk_start=5_000_001,
            chunk_end=10_000_000,
            downloaded=4_200_000,
            speed_mb_s=18.2,
        )
        job.progress["192.168.1.50"] = p1
        job.progress["192.168.1.60"] = p2

        d = job.to_dict()
        self.assertIn("interfaces", d)
        ifaces = d["interfaces"]
        self.assertEqual(ifaces["192.168.1.50"]["bytes"], 3_500_000)
        self.assertEqual(ifaces["192.168.1.50"]["downloaded"], 3_500_000)
        self.assertEqual(ifaces["192.168.1.50"]["speed_mb_s"], 12.5)

        self.assertEqual(ifaces["192.168.1.60"]["bytes"], 4_200_000)
        self.assertEqual(ifaces["192.168.1.60"]["downloaded"], 4_200_000)
        self.assertEqual(ifaces["192.168.1.60"]["speed_mb_s"], 18.2)

    # -----------------------------------------------------------------------
    # 2. Torrent observability fields: sizes, downloaded, and per-interface
    # -----------------------------------------------------------------------
    def test_torrent_observability_fields(self):
        tjob = TorrentJob(
            magnet_uri="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=ObsTorrent",
            output_path=str(self.dir_path),
            interface_ips=["192.168.1.50", "192.168.1.60"],
        )
        tjob.torrent_total_size = 50_000_000
        tjob.total_size = 50_000_000
        tjob.selected_size = 30_000_000
        tjob.selected_downloaded = 15_000_000
        tjob.downloaded = 15_000_000
        tjob.speeds = {"192.168.1.50": 2_097_152, "192.168.1.60": 3_145_728}
        tjob.bytes_per_interface = {"192.168.1.50": 6_000_000, "192.168.1.60": 9_000_000}
        tjob.peers_per_interface = {"192.168.1.50": 8, "192.168.1.60": 12}

        d = tjob.to_dict()
        # Verify sizes and downloaded-selected
        self.assertEqual(d["torrent_total_size"], 50_000_000)
        self.assertEqual(d["total_size"], 50_000_000)
        self.assertEqual(d["selected_size"], 30_000_000)
        self.assertEqual(d["selected_downloaded"], 15_000_000)
        self.assertEqual(d["downloaded_selected"], 15_000_000)
        self.assertEqual(d["expected_size"], 30_000_000)

        # Verify per-interface metrics
        self.assertEqual(d["bytes_per_interface"]["192.168.1.50"], 6_000_000)
        self.assertEqual(d["bytes_per_interface"]["192.168.1.60"], 9_000_000)

        # Verify unified interfaces dict
        self.assertIn("interfaces", d)
        self.assertEqual(d["interfaces"]["192.168.1.50"]["bytes"], 6_000_000)
        self.assertEqual(d["interfaces"]["192.168.1.50"]["speed_mb_s"], 2.0)
        self.assertEqual(d["interfaces"]["192.168.1.50"]["peers"], 8)

        self.assertEqual(d["interfaces"]["192.168.1.60"]["bytes"], 9_000_000)
        self.assertEqual(d["interfaces"]["192.168.1.60"]["speed_mb_s"], 3.0)
        self.assertEqual(d["interfaces"]["192.168.1.60"]["peers"], 12)

    # -----------------------------------------------------------------------
    # 3. Interface health metrics (failure/stall rates, consecutive failures, cooldown)
    # -----------------------------------------------------------------------
    def test_interface_health_metrics(self):
        job = DownloadJob(
            job_id="job_obs_health",
            url="http://example.com/test.bin",
            output_path=str(self.dir_path / "test.bin"),
        )
        p = InterfaceProgress(
            name="LTE",
            ip_address="10.0.0.2",
            chunk_start=0,
            chunk_end=1_000_000,
            request_count=20,
            success_count=14,
            failure_count=4,
            stall_count=2,
            consecutive_failures=1,
            _cooldown_until=time.time() + 45.0,
        )
        job.progress["10.0.0.2"] = p

        d = job.to_dict()
        iface = d["interfaces"]["10.0.0.2"]
        self.assertEqual(iface["health"], "degraded")
        self.assertEqual(iface["request_count"], 20)
        self.assertEqual(iface["failure_count"], 4)
        self.assertEqual(iface["stall_count"], 2)
        self.assertEqual(iface["failure_rate"], 0.2)
        self.assertEqual(iface["stall_rate"], 0.1)
        self.assertEqual(iface["consecutive_failures"], 1)
        self.assertGreater(iface["cooldown_remaining_s"], 40.0)

    # -----------------------------------------------------------------------
    # 4. Chunk-by-interface map built from actual commit events
    # -----------------------------------------------------------------------
    def test_chunk_map_built_from_actual_commit_events(self):
        job = DownloadJob(
            job_id="job_obs_map",
            url="http://example.com/test.bin",
            output_path=str(self.dir_path / "test.bin"),
            expected_size=6_000_000,
        )
        # 3 chunks
        c0 = Chunk(chunk_id=0, start=0, end=1_999_999, assigned_interface="192.168.1.10")
        c1 = Chunk(chunk_id=1, start=2_000_000, end=3_999_999, assigned_interface="192.168.1.10")  # initially assigned to .10
        c2 = Chunk(chunk_id=2, start=4_000_000, end=5_999_999, assigned_interface="192.168.1.20")

        job.chunks = {0: c0, 1: c1, 2: c2}
        job._total_chunks = 3

        # Chunk 0 committed by .10
        c0.status = ChunkStatus.COMPLETE
        c0.committed_interface = "192.168.1.10"
        job.committed_chunk_map[0] = "192.168.1.10"

        # Chunk 1 was stolen / won in tail race by .20 and committed by .20!
        c1.status = ChunkStatus.COMPLETE
        c1.committed_interface = "192.168.1.20"
        job.committed_chunk_map[1] = "192.168.1.20"

        # Chunk 2 still in flight (DOWNLOADING on .20, not committed yet)
        c2.status = ChunkStatus.DOWNLOADING

        chunk_map = job.get_chunk_map(threshold=1000)
        self.assertFalse(chunk_map["is_aggregated"])
        self.assertEqual(chunk_map["total_chunks"], 3)
        self.assertEqual(chunk_map["committed_count"], 2)

        # Confirm map reflects the COMMITTED interface, not initial assignment
        self.assertEqual(chunk_map["chunks"][0]["committed_interface"], "192.168.1.10")
        self.assertEqual(chunk_map["chunks"][1]["committed_interface"], "192.168.1.20")
        self.assertIsNone(chunk_map["chunks"][2]["committed_interface"])

    # -----------------------------------------------------------------------
    # 5. Bounded payload size via bucket aggregation for large downloads
    # -----------------------------------------------------------------------
    def test_chunk_map_aggregated_buckets_large_download_bounded_payload(self):
        job = DownloadJob(
            job_id="job_obs_large",
            url="http://example.com/big_10gb.iso",
            output_path=str(self.dir_path / "big_10gb.iso"),
            expected_size=10 * 1024 * 1024 * 1024,
        )
        # Simulate 5,000 chunks (2 MB each)
        NUM_CHUNKS = 5000
        for i in range(NUM_CHUNKS):
            chk = Chunk(chunk_id=i, start=i * 2097152, end=(i + 1) * 2097152 - 1)
            # Commit first 2500 chunks across two interfaces
            if i < 2500:
                comm_ip = "192.168.1.10" if i % 2 == 0 else "192.168.1.20"
                chk.status = ChunkStatus.COMPLETE
                chk.committed_interface = comm_ip
                job.committed_chunk_map[i] = comm_ip
            job.chunks[i] = chk
        job._total_chunks = NUM_CHUNKS

        chunk_map = job.get_chunk_map(max_buckets=100, threshold=1000)
        self.assertTrue(chunk_map["is_aggregated"])
        self.assertEqual(chunk_map["num_buckets"], 100)
        self.assertEqual(chunk_map["bucket_size"], 50)
        self.assertEqual(chunk_map["committed_count"], 2500)
        self.assertEqual(len(chunk_map["buckets"]), 100)

        # Bucket 0 (chunks 0..49) is 100% completed
        b0 = chunk_map["buckets"][0]
        self.assertEqual(b0["bucket_index"], 0)
        self.assertEqual(b0["start_chunk"], 0)
        self.assertEqual(b0["end_chunk"], 49)
        self.assertEqual(b0["total_chunks"], 50)
        self.assertEqual(b0["committed_count"], 50)
        self.assertEqual(b0["percent"], 100.0)
        self.assertEqual(b0["interfaces"]["192.168.1.10"], 25)
        self.assertEqual(b0["interfaces"]["192.168.1.20"], 25)

        # Bucket 99 (chunks 4950..4999) has 0 committed chunks
        b99 = chunk_map["buckets"][99]
        self.assertEqual(b99["committed_count"], 0)
        self.assertEqual(b99["percent"], 0.0)
        self.assertIsNone(b99["dominant_interface"])

        # Crucial requirement: payload size bounding!
        # With 5,000 chunks, unaggregated chunks dictionary is omitted from to_dict()
        # and the entire serialized JSON string must stay under 25 KB!
        payload = job.to_dict()
        self.assertEqual(payload["chunks"], {})
        json_bytes = len(json.dumps(payload).encode("utf-8"))
        self.assertLess(json_bytes, 25_000, f"Payload size {json_bytes} bytes exceeds 25 KB bound!")

    # -----------------------------------------------------------------------
    # 6. Worker state tracking
    # -----------------------------------------------------------------------
    def test_worker_state_tracking(self):
        job = DownloadJob(
            job_id="job_obs_worker",
            url="http://example.com/test.bin",
            output_path=str(self.dir_path / "test.bin"),
        )
        job.worker_states["192.168.1.10"] = {
            "worker_id": "192.168.1.10",
            "interface_ip": "192.168.1.10",
            "status": "downloading",
            "current_chunk_idx": 4,
            "current_chunk_bytes": 2_097_152,
            "started_at": time.time() - 1.2,
            "elapsed_s": 1.2,
        }
        job.worker_states["192.168.1.20"] = {
            "worker_id": "192.168.1.20",
            "interface_ip": "192.168.1.20",
            "status": "idle",
            "current_chunk_idx": None,
            "current_chunk_bytes": 0,
            "started_at": None,
            "elapsed_s": 0.0,
        }

        d = job.to_dict()
        self.assertIn("workers", d)
        self.assertEqual(len(d["workers"]), 2)
        w0 = next(w for w in d["workers"] if w["worker_id"] == "192.168.1.10")
        self.assertEqual(w0["status"], "downloading")
        self.assertEqual(w0["current_chunk_idx"], 4)

        w1 = next(w for w in d["workers"] if w["worker_id"] == "192.168.1.20")
        self.assertEqual(w1["status"], "idle")
        self.assertIsNone(w1["current_chunk_idx"])

    # -----------------------------------------------------------------------
    # 7. Retries and stalls observability
    # -----------------------------------------------------------------------
    def test_retries_and_stalls_observability(self):
        job = DownloadJob(
            job_id="job_obs_retries",
            url="http://example.com/test.bin",
            output_path=str(self.dir_path / "test.bin"),
        )
        p1 = InterfaceProgress(
            name="Wi-Fi",
            ip_address="192.168.1.10",
            chunk_start=0,
            chunk_end=1000,
            retry_count=3,
            stall_count=1,
        )
        p2 = InterfaceProgress(
            name="Cellular",
            ip_address="192.168.1.20",
            chunk_start=1001,
            chunk_end=2000,
            retry_count=2,
            stall_count=2,
        )
        job.progress["192.168.1.10"] = p1
        job.progress["192.168.1.20"] = p2

        d = job.to_dict()
        self.assertEqual(d["total_retries"], 5)
        self.assertEqual(d["total_stalls"], 3)
        self.assertEqual(d["interfaces"]["192.168.1.10"]["retry_count"], 3)
        self.assertEqual(d["interfaces"]["192.168.1.10"]["stall_count"], 1)
        self.assertEqual(d["interfaces"]["192.168.1.20"]["retry_count"], 2)
        self.assertEqual(d["interfaces"]["192.168.1.20"]["stall_count"], 2)

    # -----------------------------------------------------------------------
    # 8. HTTP resume confidence and waiting/resumable state
    # -----------------------------------------------------------------------
    def test_http_resume_confidence_and_waiting_resumable_state(self):
        job = DownloadJob(
            job_id="job_obs_waiting",
            url="http://example.com/file.bin",
            output_path=str(self.dir_path / "file.bin"),
            expected_size=50_000_000,
            supports_ranges=True,
            status="waiting_reconnect",
            resume_confidence="medium",
        )
        job._reconnect_wait_start = time.time() - 30.0
        job._reconnect_wait_max = 180.0

        d = job.to_dict()
        self.assertEqual(d["resume_confidence"], "medium")
        self.assertTrue(d["is_resumable"])
        self.assertTrue(d["is_waiting"])
        self.assertAlmostEqual(d["waiting_remaining_s"], 150.0, delta=2.0)

    # -----------------------------------------------------------------------
    # 9. Batch UI updates (no per-chunk pushes)
    # -----------------------------------------------------------------------
    def test_batch_ui_updates_and_no_per_chunk_pushes(self):
        # Verify websocket progress endpoint uses throttled 0.5s poll interval,
        # with zero per-chunk push overhead
        from main import websocket_progress
        import inspect

        source = inspect.getsource(websocket_progress)
        self.assertIn("await asyncio.sleep(0.5)", source)
        self.assertNotIn("on_chunk_downloaded", source)
        self.assertNotIn("for chunk in job.chunks:", source)

    # -----------------------------------------------------------------------
    # 10. Verify all 5 ponytail debt markers are properly recorded
    # -----------------------------------------------------------------------
    def test_ponytail_debt_markers_harvest(self):
        base_dir = Path(__file__).parent.parent
        files_to_check = [
            base_dir / "backend" / "torrent.py",
            base_dir / "tests" / "test_torrent_file_selection.py",
        ]
        all_markers = []
        for fpath in files_to_check:
            text = fpath.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if "# ponytail:" in line:
                    all_markers.append((fpath.name, line_no, line))

        # Check required debt topics
        stray_found = any("stray files" in m[2].lower() or "0-byte" in m[2].lower() for m in all_markers)
        dht_found = any("manual public-magnet" in m[2].lower() or "dht bootstrap" in m[2].lower() for m in all_markers)
        corrupt_found = any("corrupt" in m[2].lower() for m in all_markers)
        cache_found = any("cache" in m[2].lower() and "union" in m[2].lower() for m in all_markers)
        slowest_found = any("slowest" in m[2].lower() for m in all_markers)

        self.assertTrue(stray_found, "Missing debt marker: stray files after magnet metadata phase")
        self.assertTrue(dht_found, "Missing debt marker: manual public-magnet DHT check")
        self.assertTrue(corrupt_found, "Missing debt marker: corrupt-resume-data test")
        self.assertTrue(cache_found, "Missing debt marker: cache piece-union merge")
        self.assertTrue(slowest_found, "Missing debt marker: note slowest torrent tests")

        # Verify that all 5 markers have ceiling and upgrade triggers (no rotting debt)
        for name, line_no, line in all_markers:
            if any(term in line.lower() for term in ["stray", "dht", "corrupt", "union", "slowest"]):
                self.assertIn("ceiling:", line, f"{name}:{line_no} marker missing ceiling: tag")
                self.assertIn("upgrade:", line, f"{name}:{line_no} marker missing upgrade: tag")


if __name__ == "__main__":
    unittest.main()
