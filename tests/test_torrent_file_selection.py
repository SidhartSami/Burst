"""
Tests for Milestone 5: Native Torrent File Selection & Priorities in Burst.
"""
from __future__ import annotations

import asyncio
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
)


def create_test_torrent_file(temp_dir: str) -> str:
    """Helper creating a deterministic 3-file torrent for testing."""
    fs = lt.file_storage()
    fs.set_name("multi_file_torrent")
    fs.add_file("multi_file_torrent/file_a.txt", 32768)
    fs.add_file("multi_file_torrent/file_b.txt", 64512)
    fs.add_file("multi_file_torrent/file_c.txt", 32768)

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
    # Test 2: Initial file priorities applied via atp.file_priorities
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

        # Allow initial session creation
        await asyncio.sleep(0.1)

        # 1. Total size must only count wanted files (file_b = 64512 bytes)
        self.assertEqual(job.total_size, 64512)
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

        # 4. to_dict() serialization must expose files and priorities
        d = job.to_dict()
        self.assertIn("file_priorities", d)
        self.assertEqual(d["file_priorities"], {0: 0, 1: 4, 2: 0})
        self.assertIn("files", d)
        self.assertEqual(len(d["files"]), 3)

        job._running = False

    # -----------------------------------------------------------------------
    # Test 3: Dynamic file priority updates on active handles
    # -----------------------------------------------------------------------
    async def test_dynamic_file_priority_modification(self):
        # Start with all files enabled by default
        job = await start_torrent_download(
            magnet_uri=self.torrent_file,
            output_path=self.out_dir,
            interface_ips=["127.0.0.1"],
        )
        await asyncio.sleep(0.1)
        self.assertEqual(job.total_size, 32768 + 64512 + 32768)

        # Dynamically change priorities: enable file_a and file_b, disable file_c
        res = await job.set_file_priorities({0: 4, 1: 4, 2: 0})
        self.assertEqual(res["status"], "success")
        self.assertEqual(job.total_size, 32768 + 64512)

        # Let libtorrent handle thread process prioritization
        await asyncio.sleep(0.1)

        handle = job.handles[0][1]
        self.assertEqual(list(handle.get_file_priorities()), [4, 4, 0])

        # Dynamically deselect all files
        await job.set_file_priorities({0: 0, 1: 0, 2: 0})
        self.assertEqual(job.total_size, 0)
        await asyncio.sleep(0.1)
        self.assertEqual(list(handle.get_file_priorities()), [0, 0, 0])

        # Dynamically re-enable file_c only
        await job.set_file_priorities({2: 7})
        self.assertEqual(job.total_size, 32768)
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
    # Test 5: REST API endpoints (/torrent/inspect, /files, /files/priorities)
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

        # Wait for job to start
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

        job._running = False


if __name__ == "__main__":
    unittest.main()
