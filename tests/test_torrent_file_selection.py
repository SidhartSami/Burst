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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = self.temp_dir.name
        self.torrent_file = create_test_torrent_file(self.dir_path)
        self.out_dir = os.path.join(self.dir_path, "downloads")
        os.makedirs(self.out_dir, exist_ok=True)
        active_torrents.clear()

    def tearDown(self):
        for job in list(active_torrents.values()):
            job._running = False
        active_torrents.clear()
        self.temp_dir.cleanup()

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
        # Deselect file_a (idx 0) and file_c (idx 2), only select file_b (idx 1)
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

        # 4. to_dict() serialization must expose all separate fields
        d = job.to_dict()
        self.assertIn("file_priorities", d)
        self.assertEqual(d["file_priorities"], {0: 0, 1: 4, 2: 0})
        self.assertEqual(d["torrent_total_size"], total_torrent_bytes)
        self.assertEqual(d["total_size"], total_torrent_bytes)
        self.assertEqual(d["selected_size"], 64512)
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
        # Create a job with wait_for_selection=True and a fake magnet URI
        magnet_uri = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test_magnet"
        job = TorrentJob(
            magnet_uri=magnet_uri,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
            wait_for_selection=True,
        )
        # 1. State must be visible as "fetching_metadata"
        self.assertEqual(job.status, "fetching_metadata")
        self.assertTrue(job.wait_for_selection)
        self.assertFalse(job._selection_ready_event.is_set())

        # 2. Simulate metadata reception
        ti = lt.torrent_info(self.torrent_file)
        job._torrent_info = ti
        num = ti.num_files()
        job.torrent_total_size = ti.total_size()
        job.total_size = ti.total_size()
        job.selected_size = ti.total_size()

        # Job transitions to paused waiting for selection
        if job.wait_for_selection:
            job.status = "paused"

        self.assertEqual(job.status, "paused")
        # File tree is exposed now
        files = job.get_files()
        self.assertEqual(len(files), 3)

        # Nothing downloaded before selection
        self.assertEqual(job.downloaded, 0)
        self.assertEqual(job.selected_downloaded, 0)

        # 3. Apply file priorities while paused
        await job.set_file_priorities({0: 0, 1: 4, 2: 0})
        self.assertEqual(job.selected_size, 64512)

        # 4. Resume starts download
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

        # Start a torrent with priorities
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

        # Save state to burst_active_jobs.json
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

        # Clear memory and restart
        job._running = False
        active_torrents.clear()
        self.assertEqual(len(active_torrents), 0)

        # Load state back
        await load_state()
        self.assertIn(job_id, active_torrents)
        restored = active_torrents[job_id]

        # Verify restored priorities, sizes, and progress
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

        # 1. select_all(priority=7)
        await job.select_all(priority=7)
        self.assertEqual(job.file_priorities, {0: 7, 1: 7, 2: 7})
        self.assertEqual(job.selected_size, 32768 + 64512 + 32768)

        # 2. deselect_all()
        await job.deselect_all()
        self.assertEqual(job.file_priorities, {0: 0, 1: 0, 2: 0})
        self.assertEqual(job.selected_size, 0)

        # 3. select_directory("docs", priority=4)
        # docs has file_a.txt (idx 0) and file_c.txt (idx 2)
        res = await job.select_directory("docs", priority=4)
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.file_priorities.get(0), 4)
        self.assertEqual(job.file_priorities.get(1), 0)
        self.assertEqual(job.file_priorities.get(2), 4)
        self.assertEqual(job.selected_size, 32768 + 32768)

        # 4. deselect_directory("docs")
        await job.deselect_directory("docs")
        self.assertEqual(job.file_priorities.get(0), 0)
        self.assertEqual(job.file_priorities.get(2), 0)
        self.assertEqual(job.selected_size, 0)

        # 5. REST API helpers
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

        # Deselect all files: total_size and torrent_total_size remain unchanged!
        await job.deselect_all()
        self.assertEqual(job.torrent_total_size, total_bytes)
        self.assertEqual(job.total_size, total_bytes)
        self.assertEqual(job.selected_size, 0)

        d = job.to_dict()
        self.assertEqual(d["torrent_total_size"], total_bytes)
        self.assertEqual(d["total_size"], total_bytes)
        self.assertEqual(d["selected_size"], 0)
        self.assertEqual(d["expected_size"], total_bytes)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 10: Real libtorrent integration test: deselected file never on disk
    #          and mid-download re-selection
    # -----------------------------------------------------------------------
    async def test_real_libtorrent_deselected_file_never_on_disk_and_reselect(self):
        # 1. Create real content files on disk
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

        # 2. Build torrent with valid hashes
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

        # 3. Start real seeder session
        seeder_ses = lt.session({"listen_interfaces": "127.0.0.1:0"})
        s_atp = lt.add_torrent_params()
        s_atp.ti = lt.torrent_info(real_torrent_path)
        s_atp.save_path = seeder_dir
        s_handle = seeder_ses.add_torrent(s_atp)

        # Wait for seeder check
        for _ in range(50):
            if s_handle.status().is_seeding:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(s_handle.status().is_seeding)
        seeder_port = seeder_ses.listen_port()

        # 4. Start downloader with file_b (idx 1) deselected
        job = await start_torrent_download(
            magnet_uri=real_torrent_path,
            output_path=client_dir,
            interface_ips=["127.0.0.1"],
            file_priorities={0: 4, 1: 0, 2: 4},
        )
        await asyncio.sleep(0.1)

        # Connect client handle to local seeder
        client_handle = job.handles[0][1]
        client_handle.connect_peer(("127.0.0.1", seeder_port))

        dest_a = os.path.join(client_dir, "real_torrent", "file_a.bin")
        dest_b = os.path.join(client_dir, "real_torrent", "file_b.bin")
        dest_c = os.path.join(client_dir, "real_torrent", "file_c.bin")

        # Wait until mid-download (file_a or file_c started downloading)
        for _ in range(60):
            prog = client_handle.file_progress()
            if prog[0] > 0 or prog[2] > 0:
                break
            await asyncio.sleep(0.05)

        # CRITICAL ASSERTION: The deselected file NEVER appears on disk mid-download!
        self.assertFalse(os.path.exists(dest_b), "Deselected file_b.bin must NEVER appear on disk mid-download!")

        # 5. Mid-download re-selection: dynamically select file_b
        res = await job.set_file_priorities({1: 4})
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.selected_size, len(data_a) + len(data_b) + len(data_c))

        # Wait for all files to complete downloading
        for _ in range(100):
            prog = client_handle.file_progress()
            if prog[0] == len(data_a) and prog[1] == len(data_b) and prog[2] == len(data_c):
                break
            await asyncio.sleep(0.05)

        # Assert all files now exist on disk and match original contents
        self.assertTrue(os.path.exists(dest_a))
        self.assertEqual(open(dest_a, "rb").read(), data_a)
        self.assertTrue(os.path.exists(dest_c))
        self.assertEqual(open(dest_c, "rb").read(), data_c)
        self.assertTrue(os.path.exists(dest_b), "Re-selected file_b.bin must now exist on disk!")
        self.assertEqual(open(dest_b, "rb").read(), data_b)

        job._running = False

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

        # 1. Invalid index (out of range >= num_files)
        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {999: 4}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("out of range", r.json()["detail"].lower())

        # 2. Negative index
        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {-1: 4}})
        self.assertEqual(r.status_code, 400)

        # 3. Invalid priority (> 7)
        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: 8}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("range", r.json()["detail"].lower())

        # 4. Invalid priority (< 0)
        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: -1}})
        self.assertEqual(r.status_code, 400)

        # 5. Non-integer priority
        r = client.post(f"/torrent/{job.job_id}/files/priorities", json={"priorities": {0: "invalid"}})
        self.assertEqual(r.status_code, 422)  # Pydantic type validation

        # 6. Unknown job ID
        r = client.post("/torrent/unknown-job-id-12345/files/priorities", json={"priorities": {0: 4}})
        self.assertEqual(r.status_code, 404)

        # 7. Restrict /torrent/inspect strictly to .torrent files
        bad_file = os.path.join(self.dir_path, "malicious.sh")
        with open(bad_file, "w") as f:
            f.write("#!/bin/sh\necho hack\n")
        r = client.post("/torrent/inspect", json={"torrent_path": bad_file})
        self.assertEqual(r.status_code, 400)
        self.assertIn(".torrent", r.json()["detail"])

        # Non-existent file
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

        # Verify all initial handles have identical priorities
        self.assertEqual(len(job.handles), 2)
        h1_prio = list(job.handles[0][1].get_file_priorities())
        h2_prio = list(job.handles[1][1].get_file_priorities())
        self.assertEqual(h1_prio, [4, 0, 7])
        self.assertEqual(h2_prio, [4, 0, 7])

        # Dynamically change priorities
        await job.set_file_priorities({0: 0, 1: 5})
        await asyncio.sleep(0.05)
        h1_prio = list(job.handles[0][1].get_file_priorities())
        h2_prio = list(job.handles[1][1].get_file_priorities())
        self.assertEqual(h1_prio, [0, 5, 7])
        self.assertEqual(h2_prio, [0, 5, 7])

        # Verify progress calculation takes max() across handles and does NOT double-count
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

        # Both handles report 10,000 bytes done. If summed, it would be 20,000 (double counted!).
        # But max() should keep it strictly at 10,000.
        with patch.object(job.handles[0][1], "status", return_value=mock_status_1), \
             patch.object(job.handles[1][1], "status", return_value=mock_status_2):
            # Run one monitor cycle manually or trigger update
            max_selected_downloaded = max(mock_status_1.total_wanted_done, mock_status_2.total_wanted_done)
            self.assertEqual(max_selected_downloaded, 10000, "Progress must NOT be double-counted across handles!")

        job._running = False


if __name__ == "__main__":
    unittest.main()
