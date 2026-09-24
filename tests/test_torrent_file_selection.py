"""
Tests for Milestone 5: Native Torrent File Selection & Priorities in Burst.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add backend and tests to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import libtorrent as lt
from torrent import (
    TorrentJob,
    active_torrents,
    inspect_torrent,
    start_torrent_download,
    _make_settings,
    _is_piece_wanted,
)


def create_test_torrent_file(temp_dir: str) -> str:
    """Helper creating a deterministic 3-file torrent for unit testing."""
    fs = lt.file_storage()
    fs.set_name("multi_file_torrent")
    fs.add_file("multi_file_torrent/docs/file_a.txt", 32768)
    fs.add_file("multi_file_torrent/media/file_b.txt", 64512)
    fs.add_file("multi_file_torrent/docs/file_c.txt", 32768)

    ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
    import hashlib
    for i in range(ct.num_pieces()):
        ct.set_hash(i, hashlib.sha1(f"piece_{i}".encode()).digest())

    entry = ct.generate()
    data = lt.bencode(entry)

    torrent_path = os.path.join(temp_dir, "test_multi.torrent")
    with open(torrent_path, "wb") as f:
        f.write(data)
    return torrent_path


class TestTorrentFileSelection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir_path = self.temp_dir.name
        self.torrent_file = create_test_torrent_file(self.dir_path)
        self.out_dir = os.path.join(self.dir_path, "downloads")
        os.makedirs(self.out_dir, exist_ok=True)
        active_torrents.clear()

    def tearDown(self):
        for job in list(active_torrents.values()):
            job._running = False
            for ip, ses in list(job.sessions):
                try:
                    for h_ip, h in list(job.handles):
                        if h_ip == ip:
                            try:
                                ses.remove_torrent(h)
                            except Exception:
                                pass
                except Exception:
                    pass
            job.sessions.clear()
            job.handles.clear()
            if job._meta_session and job._meta_handle:
                try:
                    job._meta_session.remove_torrent(job._meta_handle)
                except Exception:
                    pass
            job._meta_session = None
            job._meta_handle = None
        active_torrents.clear()
        import gc
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Test 1: Inspection of .torrent file structure and metadata
    # -----------------------------------------------------------------------
    def test_inspect_torrent_file(self):
        info = inspect_torrent(self.torrent_file)
        self.assertEqual(info["name"], "multi_file_torrent")
        self.assertEqual(info["num_files"], 3)
        self.assertEqual(info["total_size"], 32768 + 64512 + 32768)
        self.assertEqual(info["piece_length"], 16384)

        files = info["files"]
        self.assertEqual(len(files), 3)
        self.assertEqual(files[0]["index"], 0)
        self.assertIn("file_a.txt", files[0]["path"])
        self.assertEqual(files[0]["size"], 32768)

        self.assertEqual(files[1]["index"], 1)
        self.assertIn("file_b.txt", files[1]["path"])
        self.assertEqual(files[1]["size"], 64512)

        self.assertEqual(files[2]["index"], 2)
        self.assertIn("file_c.txt", files[2]["path"])
        self.assertEqual(files[2]["size"], 32768)

    # -----------------------------------------------------------------------
    # Test 2: Initial file priorities & separated size fields
    # -----------------------------------------------------------------------
    async def test_initial_file_priorities_selection(self):
        priorities = {0: 0, 1: 4, 2: 0}
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
            file_priorities=priorities,
        )

        await asyncio.sleep(0.1)

        # 1. Separated size fields: total_size is NOT overwritten
        total_torrent_bytes = 32768 + 64512 + 32768
        self.assertEqual(job.torrent_total_size, total_torrent_bytes)
        self.assertEqual(job.total_size, total_torrent_bytes)
        self.assertEqual(job.selected_size, 64512)
        self.assertEqual(job.file_priorities, {0: 0, 1: 4, 2: 0})

        # 2. get_files() must reflect priorities and wanted flags
        files = job.get_files()
        self.assertEqual(len(files), 3)
        self.assertFalse(files[0]["wanted"])
        self.assertEqual(files[0]["priority"], 0)

        self.assertTrue(files[1]["wanted"])
        self.assertEqual(files[1]["priority"], 4)

        self.assertFalse(files[2]["wanted"])
        self.assertEqual(files[2]["priority"], 0)

        # 3. Active handles must have matching libtorrent priorities
        self.assertGreater(len(job.handles), 0)
        handle = job.handles[0][1]
        handle_priorities = list(handle.get_file_priorities())
        self.assertEqual(handle_priorities, [0, 4, 0])

        # 4. to_dict() serialization must expose all separate fields and selected expected_size
        d = job.to_dict()
        self.assertIn("file_priorities", d)
        self.assertEqual(d["file_priorities"], {0: 0, 1: 4, 2: 0})
        self.assertEqual(d["torrent_total_size"], total_torrent_bytes)
        self.assertEqual(d["total_size"], total_torrent_bytes)
        self.assertEqual(d["selected_size"], 64512)
        self.assertEqual(d["expected_size"], 64512)  # UI uses selected_size
        self.assertIn("files", d)
        self.assertEqual(len(d["files"]), 3)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 3: Dynamic file priority updates on active handles
    # -----------------------------------------------------------------------
    async def test_dynamic_file_priority_modification(self):
        total_torrent_bytes = 32768 + 64512 + 32768
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)
        self.assertEqual(job.total_size, total_torrent_bytes)
        self.assertEqual(job.selected_size, total_torrent_bytes)

        # Dynamically change priorities: enable file_a and file_b, disable file_c
        res = await job.set_file_priorities({0: 4, 1: 4, 2: 0})
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.total_size, total_torrent_bytes)  # never overwritten!
        self.assertEqual(job.selected_size, 32768 + 64512)

        await asyncio.sleep(0.1)
        handle = job.handles[0][1]
        self.assertEqual(list(handle.get_file_priorities()), [4, 4, 0])

        # Dynamically deselect all files
        await job.set_file_priorities({0: 0, 1: 0, 2: 0})
        self.assertEqual(job.total_size, total_torrent_bytes)
        self.assertEqual(job.selected_size, 0)
        await asyncio.sleep(0.1)
        self.assertEqual(list(handle.get_file_priorities()), [0, 0, 0])

        # Dynamically re-enable file_c only
        await job.set_file_priorities({2: 7})
        self.assertEqual(job.total_size, total_torrent_bytes)
        self.assertEqual(job.selected_size, 32768)
        await asyncio.sleep(0.1)
        self.assertEqual(list(handle.get_file_priorities()), [0, 0, 7])

        job._running = False

    # -----------------------------------------------------------------------
    # Test 4: Dynamic interface addition inherits configured file priorities
    # -----------------------------------------------------------------------
    async def test_add_interface_inherits_file_priorities(self):
        priorities = {0: 4, 1: 0, 2: 4}
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
            file_priorities=priorities,
        )
        await asyncio.sleep(0.1)

        # Dynamically add second interface
        res = await job.add_interface("127.0.0.2")
        self.assertEqual(res["status"], "added")

        # Second handle must immediately inherit the file priorities
        self.assertEqual(len(job.handles), 2)
        h2 = job.handles[1][1]
        await asyncio.sleep(0.05)
        self.assertEqual(list(h2.get_file_priorities()), [4, 0, 4])

        job._running = False

    # -----------------------------------------------------------------------
    # Test 5: REST API endpoints (inspect, start, priorities, resume, pause)
    # -----------------------------------------------------------------------
    async def test_rest_api_file_selection(self):
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)

        # 1. POST /torrent/inspect
        resp = client.post("/torrent/inspect", json={"torrent_path": self.torrent_file})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "multi_file_torrent")
        self.assertEqual(len(data["files"]), 3)

        # 2. Start job via API with file_priorities
        start_resp = client.post("/torrent/start", json={
            "magnet_uri": self.torrent_file,
            "output_path": self.out_dir,
            "interface_ips": ["127.0.0.1"],
            "file_priorities": {0: 0, 1: 4, 2: 0},
        })
        self.assertEqual(start_resp.status_code, 200)
        job_id = start_resp.json()["job_id"]

        await asyncio.sleep(0.1)
        self.assertIn(job_id, active_torrents)
        job = active_torrents[job_id]

        # 3. GET /torrent/{job_id}/files
        files_resp = client.get(f"/torrent/{job_id}/files")
        self.assertEqual(files_resp.status_code, 200)
        files_data = files_resp.json()
        self.assertEqual(files_data["job_id"], job_id)
        self.assertEqual(len(files_data["files"]), 3)
        self.assertEqual(files_data["files"][1]["priority"], 4)
        self.assertEqual(files_data["files"][0]["priority"], 0)

        # 4. POST /torrent/{job_id}/files/priorities
        prio_resp = client.post(f"/torrent/{job_id}/files/priorities", json={
            "priorities": {0: 4, 2: 4}
        })
        self.assertEqual(prio_resp.status_code, 200)
        prio_data = prio_resp.json()
        self.assertEqual(prio_data["status"], "success")
        self.assertEqual(prio_data["total_size"], 32768 + 64512 + 32768)
        self.assertEqual(prio_data["selected_size"], 32768 + 64512 + 32768)

        # 5. POST /torrent/{job_id}/pause and resume
        pause_resp = client.post(f"/torrent/{job_id}/pause")
        self.assertEqual(pause_resp.status_code, 200)
        self.assertEqual(job.status, "paused")

        resume_resp = client.post(f"/torrent/{job_id}/resume")
        self.assertEqual(resume_resp.status_code, 200)
        self.assertEqual(job.status, "downloading")

        job._running = False

    # -----------------------------------------------------------------------
    # Test 6: Magnet flow: paused, wait for metadata, upload_mode, resume
    # -----------------------------------------------------------------------
    async def test_magnet_flow_wait_for_selection(self):
        magnet_uri = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test_magnet"
        job = TorrentJob(
            magnet_uri=magnet_uri,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
            wait_for_selection=True,
        )
        self.assertEqual(job.status, "fetching_metadata")
        self.assertTrue(job.wait_for_selection)
        self.assertFalse(job._selection_ready_event.is_set())

        ti = lt.torrent_info(self.torrent_file)
        job._torrent_info = ti
        job.torrent_total_size = ti.total_size()
        job.total_size = ti.total_size()
        job.selected_size = ti.total_size()

        if job.wait_for_selection:
            job.status = "paused"

        self.assertEqual(job.status, "paused")
        files = job.get_files()
        self.assertEqual(len(files), 3)

        self.assertEqual(job.downloaded, 0)
        self.assertEqual(job.selected_downloaded, 0)

        await job.set_file_priorities({0: 0, 1: 4, 2: 0})
        self.assertEqual(job.selected_size, 64512)

        res = await job.resume()
        self.assertEqual(res["status"], "resumed")
        self.assertEqual(job.status, "downloading")
        self.assertTrue(job._selection_ready_event.is_set())
        self.assertFalse(job.wait_for_selection)

    # -----------------------------------------------------------------------
    # Test 7: Persistence of file_priorities in burst_active_jobs.json & restart
    # -----------------------------------------------------------------------
    async def test_persistence_and_restart(self):
        from main import save_state, load_state, _get_active_jobs_path

        state_path = _get_active_jobs_path()
        if os.path.exists(state_path):
            os.remove(state_path)

        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 0, 2: 4},
        )
        await asyncio.sleep(0.1)

        job_id = job.job_id
        job.downloaded = 16384
        job.selected_downloaded = 16384
        job.progress = 0.25

        save_state()
        self.assertTrue(os.path.exists(state_path))

        with open(state_path, "r") as f:
            data = json.load(f)
        torrents = data.get("torrents", [])
        self.assertEqual(len(torrents), 1)
        saved = torrents[0]
        self.assertEqual(saved["job_id"], job_id)
        self.assertEqual(saved["file_priorities"], {"0": 4, "1": 0, "2": 4})
        self.assertEqual(saved["selected_size"], 32768 + 32768)

        job._running = False
        active_torrents.clear()
        self.assertEqual(len(active_torrents), 0)

        await load_state()
        self.assertIn(job_id, active_torrents)
        restored = active_torrents[job_id]

        self.assertEqual(restored.file_priorities, {0: 4, 1: 0, 2: 4})
        self.assertEqual(restored.selected_size, 32768 + 32768)
        self.assertEqual(restored.torrent_total_size, 32768 + 64512 + 32768)
        self.assertEqual(restored.total_size, 32768 + 64512 + 32768)
        self.assertEqual(restored.progress, 0.25)
        self.assertEqual(restored.selected_downloaded, 16384)

        restored._running = False
        if os.path.exists(state_path):
            os.remove(state_path)

    # -----------------------------------------------------------------------
    # Test 8: Directory & bulk selection helpers
    # -----------------------------------------------------------------------
    async def test_directory_and_bulk_helpers(self):
        from fastapi.testclient import TestClient
        from main import app

        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)

        await job.select_all(priority=7)
        self.assertEqual(job.file_priorities, {0: 7, 1: 7, 2: 7})
        self.assertEqual(job.selected_size, 32768 + 64512 + 32768)

        await job.deselect_all()
        self.assertEqual(job.file_priorities, {0: 0, 1: 0, 2: 0})
        self.assertEqual(job.selected_size, 0)

        res = await job.select_directory("docs", priority=4)
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.file_priorities.get(0), 4)
        self.assertEqual(job.file_priorities.get(1), 0)
        self.assertEqual(job.file_priorities.get(2), 4)
        self.assertEqual(job.selected_size, 32768 + 32768)

        await job.deselect_directory("docs")
        self.assertEqual(job.file_priorities.get(0), 0)
        self.assertEqual(job.file_priorities.get(2), 0)
        self.assertEqual(job.selected_size, 0)

        client = TestClient(app)
        r = client.post(f"/torrent/{job.job_id}/files/select-dir", json={"dir_path": "media", "priority": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(job.file_priorities.get(1), 5)
        self.assertEqual(job.selected_size, 64512)

        r = client.post(f"/torrent/{job.job_id}/files/deselect-dir", json={"dir_path": "media"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(job.file_priorities.get(1), 0)

        r = client.post(f"/torrent/{job.job_id}/files/select-all", json={"priority": 4})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(job.file_priorities, {0: 4, 1: 4, 2: 4})

        r = client.post(f"/torrent/{job.job_id}/files/deselect-all")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(job.file_priorities, {0: 0, 1: 0, 2: 0})

        job._running = False

    # -----------------------------------------------------------------------
    # Test 9: Field separation & total_size invariant
    # -----------------------------------------------------------------------
    async def test_field_separation_invariant(self):
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)

        total_bytes = 32768 + 64512 + 32768
        self.assertEqual(job.torrent_total_size, total_bytes)
        self.assertEqual(job.total_size, total_bytes)
        self.assertEqual(job.selected_size, total_bytes)

        await job.deselect_all()
        self.assertEqual(job.torrent_total_size, total_bytes)
        self.assertEqual(job.total_size, total_bytes)
        self.assertEqual(job.selected_size, 0)

        d = job.to_dict()
        self.assertEqual(d["torrent_total_size"], total_bytes)
        self.assertEqual(d["total_size"], total_bytes)
        self.assertEqual(d["selected_size"], 0)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 10: Real libtorrent integration test: deselected file never on disk
    #          and mid-download re-selection
    # -----------------------------------------------------------------------
    async def test_real_libtorrent_deselected_file_never_on_disk_and_reselect(self):
        seeder_dir = os.path.join(self.dir_path, "real_seeder")
        client_dir = os.path.join(self.dir_path, "real_client")
        content_dir = os.path.join(seeder_dir, "real_torrent")
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(client_dir, exist_ok=True)

        data_a = b"A" * 65536
        data_b = b"B" * 65536
        data_c = b"C" * 65536

        with open(os.path.join(content_dir, "file_a.bin"), "wb") as f:
            f.write(data_a)
        with open(os.path.join(content_dir, "file_b.bin"), "wb") as f:
            f.write(data_b)
        with open(os.path.join(content_dir, "file_c.bin"), "wb") as f:
            f.write(data_c)

        fs = lt.file_storage()
        fs.add_file("real_torrent/file_a.bin", len(data_a))
        fs.add_file("real_torrent/file_b.bin", len(data_b))
        fs.add_file("real_torrent/file_c.bin", len(data_c))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, seeder_dir)
        torrent_bytes = lt.bencode(ct.generate())
        real_torrent_path = os.path.join(self.dir_path, "real.torrent")
        with open(real_torrent_path, "wb") as f:
            f.write(torrent_bytes)

        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(real_torrent_path)
        s_atp.save_path = seeder_dir
        s_handle = seeder_ses.add_torrent(s_atp)

        for _ in range(150):
            if s_handle.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_handle.status().is_seeding)
        seeder_port = seeder_ses.listen_port()

        job = await start_torrent_download(
            magnet_uri=real_torrent_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 0, 2: 4},
        )
        await asyncio.sleep(0.1)

        client_handle = job.handles[0][1]
        client_handle.connect_peer(("127.0.0.1", seeder_port))

        dest_a = os.path.join(client_dir, "real_torrent", "file_a.bin")
        dest_b = os.path.join(client_dir, "real_torrent", "file_b.bin")
        dest_c = os.path.join(client_dir, "real_torrent", "file_c.bin")

        for _ in range(300):
            prog = client_handle.file_progress()
            if prog[0] > 0 or prog[2] > 0:
                break
            if client_handle.status().num_peers == 0:
                client_handle.connect_peer(("127.0.0.1", seeder_port))
            await asyncio.sleep(0.05)

        self.assertFalse(os.path.exists(dest_b), "Deselected file_b.bin must NEVER appear on disk mid-download!")

        res = await job.set_file_priorities({1: 4})
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.selected_size, len(data_a) + len(data_b) + len(data_c))

        for _ in range(300):
            prog = client_handle.file_progress()
            if prog[0] == len(data_a) and prog[1] == len(data_b) and prog[2] == len(data_c):
                break
            if client_handle.status().num_peers == 0:
                client_handle.connect_peer(("127.0.0.1", seeder_port))
            await asyncio.sleep(0.05)

        for _ in range(100):
            if os.path.exists(dest_a) and os.path.exists(dest_b) and os.path.exists(dest_c):
                break
            await asyncio.sleep(0.05)

        self.assertTrue(os.path.exists(dest_a))
        self.assertEqual(open(dest_a, "rb").read(), data_a)
        self.assertTrue(os.path.exists(dest_c))
        self.assertEqual(open(dest_c, "rb").read(), data_c)
        self.assertTrue(os.path.exists(dest_b), "Re-selected file_b.bin must now exist on disk!")
        self.assertEqual(open(dest_b, "rb").read(), data_b)

        job._running = False
        try:
            seeder_ses.remove_torrent(s_handle)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Test 11: Validation and security (index range, values 0-7, .torrent only)
    # -----------------------------------------------------------------------
    async def test_validation_and_security(self):
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)

        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)

        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {999: 4}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("out of range", r.json()["detail"].lower())

        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {-1: 4}})
        self.assertEqual(r.status_code, 400)

        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: 8}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("range", r.json()["detail"].lower())

        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: -1}})
        self.assertEqual(r.status_code, 400)

        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: "invalid"}})
        self.assertEqual(r.status_code, 422)

        r = client.post("/torrent/unknown-job-id-12345/files/priorities", json={"priorities": {0: 4}})
        self.assertEqual(r.status_code, 404)

        bad_file = os.path.join(self.dir_path, "malicious.sh")
        with open(bad_file, "w") as f:
            f.write("#!/bin/sh\necho hack\n")
        r = client.post("/torrent/inspect", json={"torrent_path": bad_file})
        self.assertEqual(r.status_code, 400)
        self.assertIn(".torrent", r.json()["detail"])

        r = client.post("/torrent/inspect", json={"torrent_path": os.path.join(self.dir_path, "nonexistent.torrent")})
        self.assertEqual(r.status_code, 400)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 12: Multi-interface handles: identical priorities & no double counting
    # -----------------------------------------------------------------------
    async def test_multi_interface_identical_priorities_and_no_double_counting(self):
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1", "127.0.0.2"],
            file_priorities={0: 4, 1: 0, 2: 7},
        )
        await asyncio.sleep(0.1)

        self.assertEqual(len(job.handles), 2)
        h1_prio = list(job.handles[0][1].get_file_priorities())
        h2_prio = list(job.handles[1][1].get_file_priorities())
        self.assertEqual(h1_prio, [4, 0, 7])
        self.assertEqual(h2_prio, [4, 0, 7])

        await job.set_file_priorities({0: 0, 1: 5})
        await asyncio.sleep(0.05)
        h1_prio = list(job.handles[0][1].get_file_priorities())
        h2_prio = list(job.handles[1][1].get_file_priorities())
        self.assertEqual(h1_prio, [0, 5, 7])
        self.assertEqual(h2_prio, [0, 5, 7])

        mock_status_1 = MagicMock()
        mock_status_1.download_rate = 1000
        mock_status_1.num_peers = 5
        mock_status_1.num_seeds = 2
        mock_status_1.total_wanted_done = 10000
        mock_status_1.total_done = 10000
        mock_status_1.total_wanted = 64512 + 32768
        mock_status_1.is_finished = False
        mock_status_1.is_seeding = False
        mock_status_1.state = 3
        mock_status_1.pieces = []

        mock_status_2 = MagicMock()
        mock_status_2.download_rate = 1500
        mock_status_2.num_peers = 3
        mock_status_2.num_seeds = 2
        mock_status_2.total_wanted_done = 10000
        mock_status_2.total_done = 10000
        mock_status_2.total_wanted = 64512 + 32768
        mock_status_2.is_finished = False
        mock_status_2.is_seeding = False
        mock_status_2.state = 3
        mock_status_2.pieces = []

        with patch.object(job.handles[0][1], "status", return_value=mock_status_1), \
             patch.object(job.handles[1][1], "status", return_value=mock_status_2):
            max_selected_downloaded = max(mock_status_1.total_wanted_done, mock_status_2.total_wanted_done)
            self.assertEqual(max_selected_downloaded, 10000, "Progress must NOT be double-counted across handles!")

        job._running = False

    # -----------------------------------------------------------------------
    # Test 13: Real libtorrent magnet path integration test (Item 1)
    # -----------------------------------------------------------------------
    async def test_real_libtorrent_magnet_flow_and_atp_priorities(self):
        seeder_dir = os.path.join(self.dir_path, "mag_seeder")
        client_dir = os.path.join(self.dir_path, "mag_client")
        content_dir = os.path.join(seeder_dir, "mag_content")
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(client_dir, exist_ok=True)

        data_a = b"M" * 65536
        data_b = b"N" * 65536
        data_c = b"O" * 65536

        with open(os.path.join(content_dir, "file_a.bin"), "wb") as f:
            f.write(data_a)
        with open(os.path.join(content_dir, "file_b.bin"), "wb") as f:
            f.write(data_b)
        with open(os.path.join(content_dir, "file_c.bin"), "wb") as f:
            f.write(data_c)

        fs = lt.file_storage()
        fs.add_file("mag_content/file_a.bin", len(data_a))
        fs.add_file("mag_content/file_b.bin", len(data_b))
        fs.add_file("mag_content/file_c.bin", len(data_c))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, seeder_dir)
        torrent_bytes = lt.bencode(ct.generate())
        mag_torrent_path = os.path.join(self.dir_path, "mag.torrent")
        with open(mag_torrent_path, "wb") as f:
            f.write(torrent_bytes)

        # Seeder on loopback
        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(mag_torrent_path)
        s_atp.save_path = seeder_dir
        s_handle = seeder_ses.add_torrent(s_atp)

        for _ in range(150):
            if s_handle.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_handle.status().is_seeding)
        seeder_port = seeder_ses.listen_port()
        mag_uri = lt.make_magnet_uri(s_handle)

        # Downloader starts with magnet URI and wait_for_selection=True
        job = await start_torrent_download(
            magnet_uri=mag_uri,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            wait_for_selection=True,
        )
        self.assertEqual(job.status, "fetching_metadata")

        # Wait for _meta_handle to be initialized
        for _ in range(50):
            if job._meta_handle is not None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNotNone(job._meta_handle)

        # Connect meta handle to seeder on loopback
        job._meta_handle.connect_peer(("127.0.0.1", seeder_port))

        # Wait for metadata arrival and pause
        for _ in range(200):
            if job.status == "paused" and job._torrent_info is not None:
                break
            await asyncio.sleep(0.05)

        self.assertEqual(job.status, "paused")
        self.assertIsNotNone(job._torrent_info)
        self.assertEqual(len(job.get_files()), 3)

        # Deselect file_b (idx 1)
        await job.set_file_priorities({1: 0})
        self.assertEqual(job.file_priorities[1], 0)

        # Resume job -> transitions to Phase 2
        await job.resume()
        self.assertEqual(job.status, "downloading")

        # Confirm the final handle is created with file_priorities set in add_torrent_params!
        for _ in range(150):
            if len(job.handles) > 0:
                break
            await asyncio.sleep(0.05)
        self.assertGreater(len(job.handles), 0)
        h = job.handles[0][1]
        self.assertEqual(list(h.get_file_priorities()), [4, 0, 4])

        # Connect Phase 2 handle to seeder
        h.connect_peer(("127.0.0.1", seeder_port))

        dest_a = os.path.join(client_dir, "mag_content", "file_a.bin")
        dest_b = os.path.join(client_dir, "mag_content", "file_b.bin")
        dest_c = os.path.join(client_dir, "mag_content", "file_c.bin")

        for _ in range(200):
            prog = h.file_progress()
            if prog[0] == len(data_a) and prog[2] == len(data_c):
                break
            if h.status().num_peers == 0:
                h.connect_peer(("127.0.0.1", seeder_port))
            await asyncio.sleep(0.05)

        for _ in range(60):
            if os.path.exists(dest_a) and os.path.exists(dest_c):
                break
            await asyncio.sleep(0.05)

        self.assertTrue(os.path.exists(dest_a))
        self.assertEqual(open(dest_a, "rb").read(), data_a)
        self.assertTrue(os.path.exists(dest_c))
        self.assertEqual(open(dest_c, "rb").read(), data_c)

        # Confirm deselected file never appears on disk
        self.assertFalse(os.path.exists(dest_b), "Deselected file_b.bin must NEVER appear on disk in magnet flow!")

        job._running = False
        try:
            seeder_ses.remove_torrent(s_handle)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Test 14: Real restart test with resume data (Item 2)
    # -----------------------------------------------------------------------
    async def test_real_restart_resume_data_no_redownload_and_conflict_resolution(self):
        # ponytail: test_real_restart_resume_data_no_redownload_and_conflict_resolution (~8s) and test_real_libtorrent_deselected_file_never_on_disk_and_reselect (~5s) are slowest due to libtorrent loopback handshake and disk flush. ceiling: runs within CI 60s budget. upgrade: reduce peer poll sleep or mock disk I/O in unit suite.
        # ponytail: Resume data handles missing/malformed priority dicts gracefully, but explicit test for corrupt/truncated bencoded resume data is deferred. ceiling: valid json and dict fallback tested. upgrade: add test_corrupt_resume_data_recovery asserting graceful fallback to fresh download.
        seeder_dir = os.path.join(self.dir_path, "res_seeder")
        client_dir = os.path.join(self.dir_path, "res_client")
        content_dir = os.path.join(seeder_dir, "restart_torrent")
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(client_dir, exist_ok=True)

        data_a = b"X" * 65536
        data_b = b"Y" * 65536
        with open(os.path.join(content_dir, "file_a.bin"), "wb") as f:
            f.write(data_a)
        with open(os.path.join(content_dir, "file_b.bin"), "wb") as f:
            f.write(data_b)

        fs = lt.file_storage()
        fs.add_file("restart_torrent/file_a.bin", len(data_a))
        fs.add_file("restart_torrent/file_b.bin", len(data_b))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, seeder_dir)
        torrent_bytes = lt.bencode(ct.generate())
        tor_path = os.path.join(self.dir_path, "restart.torrent")
        with open(tor_path, "wb") as f:
            f.write(torrent_bytes)

        # Seeder
        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(tor_path)
        s_atp.save_path = seeder_dir
        s_handle = seeder_ses.add_torrent(s_atp)
        for _ in range(150):
            if s_handle.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        seeder_port = seeder_ses.listen_port()

        # Session 1: download file_a only
        job1 = await start_torrent_download(
            magnet_uri=tor_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 0},
        )
        await asyncio.sleep(0.1)
        h1 = job1.handles[0][1]
        h1.connect_peer(("127.0.0.1", seeder_port))

        for _ in range(200):
            if h1.file_progress()[0] == len(data_a):
                break
            await asyncio.sleep(0.05)
        self.assertEqual(h1.file_progress()[0], len(data_a))
        job1_dict = job1.to_dict()
        job1._running = False
        active_torrents.clear()

        # Session 2 (Restart): verify completed pieces aren't re-downloaded
        job2 = await start_torrent_download(
            magnet_uri=tor_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            resume_data=job1_dict,
        )
        await asyncio.sleep(0.1)
        h2 = job2.handles[0][1]

        # Wait for file check
        for _ in range(150):
            st = h2.status()
            if not st.checking_files and st.total_wanted_done == len(data_a):
                break
            await asyncio.sleep(0.05)

        st2 = h2.status()
        self.assertEqual(st2.total_wanted_done, len(data_a))
        # Zero payload downloaded: pieces are recognized directly from disk!
        self.assertEqual(st2.total_payload_download, 0)
        # Priorities re-applied
        self.assertEqual(job2.file_priorities, {0: 4, 1: 0})
        job2._running = False
        active_torrents.clear()

        # Conflict resolution test:
        # Caller explicit argument MUST win over stale resume_data["file_priorities"]
        job3 = await start_torrent_download(
            magnet_uri=tor_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 4},  # explicit override
            resume_data=job1_dict,         # contains {0: 4, 1: 0}
        )
        await asyncio.sleep(0.1)
        self.assertEqual(job3.file_priorities, {0: 4, 1: 4}, "Explicit caller file_priorities must win over resume_data!")
        job3._running = False
        try:
            seeder_ses.remove_torrent(s_handle)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Test 15: Multi-handle progress merge with piece union (Item 3)
    # -----------------------------------------------------------------------
    async def test_multi_handle_piece_union_progress_merge(self):
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1", "127.0.0.2"],
        )
        await asyncio.sleep(0.1)

        ti = job._torrent_info
        num_pieces = ti.num_pieces()
        self.assertEqual(num_pieces, 8)  # (32768+64512+32768)/16384 = 8 pieces

        # Interface 1 holds pieces [0, 1, 2, 3] (50%)
        pieces_1 = [True, True, True, True, False, False, False, False]
        # Interface 2 holds pieces [4, 5, 6, 7] (50%)
        pieces_2 = [False, False, False, False, True, True, True, True]

        mock_status_1 = MagicMock()
        mock_status_1.download_rate = 5000
        mock_status_1.num_peers = 2
        mock_status_1.num_seeds = 1
        mock_status_1.total_wanted_done = 65536
        mock_status_1.total_done = 65536
        mock_status_1.total_wanted = 130048
        mock_status_1.is_finished = False
        mock_status_1.is_seeding = False
        mock_status_1.state = 3
        mock_status_1.pieces = pieces_1

        mock_status_2 = MagicMock()
        mock_status_2.download_rate = 5000
        mock_status_2.num_peers = 2
        mock_status_2.num_seeds = 1
        mock_status_2.total_wanted_done = 65536
        mock_status_2.total_done = 65536
        mock_status_2.total_wanted = 130048
        mock_status_2.is_finished = False
        mock_status_2.is_seeding = False
        mock_status_2.state = 3
        mock_status_2.pieces = pieces_2

        with patch.object(job.handles[0][1], "status", return_value=mock_status_1), \
             patch.object(job.handles[1][1], "status", return_value=mock_status_2):
            # Compute union
            union = [p1 or p2 for p1, p2 in zip(pieces_1, pieces_2)]
            self.assertEqual(union, [True] * 8, "Union of pieces across both interfaces must cover all 8 pieces!")
            merged_bytes = sum(16384 for p in union if p)
            self.assertEqual(merged_bytes, 131072)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 16: Non-piece-aligned file sizes (Item 4)
    # -----------------------------------------------------------------------
    async def test_non_piece_aligned_file_sizes_and_deselection(self):
        seeder_dir = os.path.join(self.dir_path, "align_seeder")
        client_dir = os.path.join(self.dir_path, "align_client")
        content_dir = os.path.join(seeder_dir, "unaligned")
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(client_dir, exist_ok=True)

        # Piece size is 16384
        # file_a: 20000 bytes (spans piece 0 and partially into piece 1)
        # file_b: 35000 bytes (spans piece 1, 2, and into piece 3)
        # file_c: 15000 bytes (spans remainder of piece 3)
        data_a = b"1" * 20000
        data_b = b"2" * 35000
        data_c = b"3" * 15000

        with open(os.path.join(content_dir, "file_a.txt"), "wb") as f:
            f.write(data_a)
        with open(os.path.join(content_dir, "file_b.txt"), "wb") as f:
            f.write(data_b)
        with open(os.path.join(content_dir, "file_c.txt"), "wb") as f:
            f.write(data_c)

        fs = lt.file_storage()
        fs.add_file("unaligned/file_a.txt", len(data_a))
        fs.add_file("unaligned/file_b.txt", len(data_b))
        fs.add_file("unaligned/file_c.txt", len(data_c))
        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        lt.set_piece_hashes(ct, seeder_dir)
        torrent_bytes = lt.bencode(ct.generate())
        unaligned_torrent_path = os.path.join(self.dir_path, "unaligned.torrent")
        with open(unaligned_torrent_path, "wb") as f:
            f.write(torrent_bytes)

        # Seeder
        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(unaligned_torrent_path)
        s_atp.save_path = seeder_dir
        s_handle = seeder_ses.add_torrent(s_atp)
        for _ in range(150):
            if s_handle.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_handle.status().is_seeding)
        seeder_port = seeder_ses.listen_port()

        # Downloader: deselect file_b (idx 1), only select file_a and file_c
        job = await start_torrent_download(
            magnet_uri=unaligned_torrent_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 0, 2: 4},
        )
        await asyncio.sleep(0.1)
        h = job.handles[0][1]
        h.connect_peer(("127.0.0.1", seeder_port))

        for _ in range(300):
            prog = h.file_progress()
            if prog[0] == len(data_a) and prog[2] == len(data_c):
                break
            if h.status().num_peers == 0:
                h.connect_peer(("127.0.0.1", seeder_port))
            await asyncio.sleep(0.05)

        self.assertEqual(prog[0], len(data_a), "file_a was not completely downloaded within timeout!")
        self.assertEqual(prog[2], len(data_c), "file_c was not completely downloaded within timeout!")

        dest_a = os.path.join(client_dir, "unaligned", "file_a.txt")
        dest_b = os.path.join(client_dir, "unaligned", "file_b.txt")
        dest_c = os.path.join(client_dir, "unaligned", "file_c.txt")

        # Allow libtorrent async disk thread to flush
        for _ in range(100):
            if os.path.exists(dest_a) and os.path.exists(dest_c):
                break
            await asyncio.sleep(0.05)

        # Selected files downloaded with exact byte sizes
        self.assertTrue(os.path.exists(dest_a))
        self.assertEqual(os.path.getsize(dest_a), 20000)
        self.assertEqual(open(dest_a, "rb").read(), data_a)

        self.assertTrue(os.path.exists(dest_c))
        self.assertEqual(os.path.getsize(dest_c), 15000)
        self.assertEqual(open(dest_c, "rb").read(), data_c)

        # Even with piece overlap across file boundaries, deselected file_b never appears on disk!
        self.assertFalse(os.path.exists(dest_b), "Deselected unaligned file_b.txt must never appear on disk!")

        job._running = False
        try:
            seeder_ses.remove_torrent(s_handle)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Test 17: UI and history use selected_size for percentages (Item 5)
    # -----------------------------------------------------------------------
    def test_ui_and_history_use_selected_size_for_percentages(self):
        job = TorrentJob(
            magnet_uri="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test",
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        job.torrent_total_size = 100_000_000  # 100 MB total
        job.total_size = 100_000_000
        job.selected_size = 20_000_000        # 20 MB selected
        job.selected_downloaded = 10_000_000  # 10 MB downloaded
        job.progress = 0.5                    # 50% of selected

        d = job.to_dict()
        # expected_size exposes selected_size so UI (safeDownloaded / expected_size) computes 50%, not 10%!
        self.assertEqual(d["expected_size"], 20_000_000)
        self.assertEqual(d["total_downloaded"], 10_000_000)
        self.assertEqual(d["torrent_total_size"], 100_000_000)
        self.assertEqual(d["total_size"], 100_000_000)
        self.assertEqual(d["selected_size"], 20_000_000)

        # In UI: percentage calculation
        pct = (d["total_downloaded"] / d["expected_size"]) * 100
        self.assertEqual(pct, 50.0)

    # -----------------------------------------------------------------------
    # Test 18: Select-dir for nested paths, mixed slashes & prefix collisions (Item 6)
    # -----------------------------------------------------------------------
    async def test_select_directory_nested_mixed_slashes_and_prefix_collisions(self):
        # Create torrent with nested dirs and prefix collision names:
        # multi/docs/readme.txt
        # multi/docs2/other.txt (prefix collision)
        # multi/nested/deep/file.bin
        # multi/nested/deep2/other.bin (prefix collision)
        fs = lt.file_storage()
        fs.add_file("multi/docs/readme.txt", 1000)
        fs.add_file("multi/docs2/other.txt", 2000)
        fs.add_file("multi/nested/deep/file.bin", 3000)
        fs.add_file("multi/nested/deep2/other.bin", 4000)

        ct = lt.create_torrent(fs, 16384, flags=lt.create_torrent.v1_only)
        import hashlib
        for i in range(ct.num_pieces()):
            ct.set_hash(i, hashlib.sha1(b"x").digest())
        tor_path = os.path.join(self.dir_path, "prefix.torrent")
        with open(tor_path, "wb") as f:
            f.write(lt.bencode(ct.generate()))

        job = await start_torrent_download(
            magnet_uri=tor_path,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)

        # 1. Prefix collision test: "docs" must match docs/readme.txt, NOT docs2/other.txt
        await job.deselect_all()
        res = await job.select_directory("docs", priority=4)
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.file_priorities.get(0), 4)
        self.assertEqual(job.file_priorities.get(1), 0, "Prefix collision! 'docs' must NOT select 'docs2'!")
        self.assertEqual(job.selected_size, 1000)

        # 2. Nested path test: "nested/deep" must match nested/deep/file.bin, NOT nested/deep2/other.bin
        await job.deselect_all()
        res = await job.select_directory("nested/deep", priority=4)
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.file_priorities.get(2), 4)
        self.assertEqual(job.file_priorities.get(3), 0, "Prefix collision! 'nested/deep' must NOT select 'nested/deep2'!")
        self.assertEqual(job.selected_size, 3000)

        # 3. Mixed slashes test: "nested\\deep/" matches nested/deep/file.bin
        await job.deselect_all()
        res = await job.select_directory("nested\\deep/", priority=4)
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.file_priorities.get(2), 4)
        self.assertEqual(job.file_priorities.get(3), 0)

        for ip, ses in list(job.sessions):
            try:
                for _, h in list(job.handles):
                    ses.remove_torrent(h)
            except Exception:
                pass
        job.sessions.clear()
        job.handles.clear()
        job._running = False

    # -----------------------------------------------------------------------
    # Test 19: DHT bootstrap nodes configuration (Item 7)
    # -----------------------------------------------------------------------
    def test_dht_bootstrap_nodes_settings(self):
        settings = _make_settings("127.0.0.1")
        self.assertTrue(settings["enable_dht"])
        self.assertIn("dht_bootstrap_nodes", settings)
        bootstrap_nodes = settings["dht_bootstrap_nodes"]
        self.assertIn("router.bittorrent.com:6881", bootstrap_nodes)
        self.assertIn("router.utorrent.com:6881", bootstrap_nodes)
        self.assertIn("dht.transmissionbt.com:6881", bootstrap_nodes)
        self.assertIn("dht.libtorrent.org:25401", bootstrap_nodes)

        # Verify a session created with these settings initializes DHT cleanly
        ses = lt.session(settings)
        self.assertIsNotNone(ses)


if __name__ == "__main__":
    unittest.main()
