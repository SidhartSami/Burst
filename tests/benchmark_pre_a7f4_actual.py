"""
Burst — 64 MB Benchmark: Actual pre-a7f4a26 commit vs Current Engine & Fsync Profiling.
Runs 5 iterations per mode and reports median metrics.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import importlib.util
import os
import re
import socketserver
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

# Current backend
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import config
import downloader as current_downloader
from downloader import DownloadManager, DownloadJob, ChunkStatus, analyze_url

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024  # 64 MB
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()


class BenchmarkHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    def do_HEAD(self):
        endpoint = self.path.split("?")[0]
        self.send_response(200)
        self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        if endpoint == "/single64.bin":
            self.send_header("Accept-Ranges", "none")
        else:
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"bench-64mb-etag"')
        self.end_headers()

    def do_GET(self):
        endpoint = self.path.split("?")[0]
        range_header = self.headers.get("Range")

        if endpoint == "/single64.bin" or not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", '"bench-64mb-etag"')
            self.end_headers()
            self.wfile.write(BENCHMARK_DATA)
            return

        m = re.match(r"^bytes=(\d+)-(\d+)?$", range_header)
        if not m:
            self.send_response(416)
            self.end_headers()
            return

        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(BENCHMARK_DATA) - 1
        end = min(end, len(BENCHMARK_DATA) - 1)
        length = end - start + 1

        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(BENCHMARK_DATA)}")
        self.send_header("Content-Length", str(length))
        self.send_header("ETag", '"bench-64mb-etag"')
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start : end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass


def load_pre_a7f4_downloader():
    pre_path = Path("C:/Coding/Burst_pre_a7f4/backend")
    orig_path = list(sys.path)
    try:
        sys.path.insert(0, str(pre_path))
        spec = importlib.util.spec_from_file_location("pre_downloader", pre_path / "downloader.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pre_downloader"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path = orig_path


async def run_one_download(mgr, url, dest, ifaces, boosted=False, disable_fsync=False):
    t_start = time.perf_counter()
    
    fsync_patcher = None
    if disable_fsync:
        fsync_patcher = patch("os.fsync", lambda fd: None)
        fsync_patcher.start()

    try:
        job = await mgr.create_job(url, str(dest), ifaces)
        if boosted:
            await mgr.toggle_boost(job.job_id, ifaces)

        task = mgr._job_tasks[job.job_id]

        peak_speed = 0.0
        while not task.done():
            for p in job.progress.values():
                if p.speed_mb_s > peak_speed:
                    peak_speed = p.speed_mb_s
            await asyncio.sleep(0.01)

        await task
        elapsed = time.perf_counter() - t_start

        # Verification
        assert dest.exists(), f"Destination {dest} missing!"
        assert dest.stat().st_size == BENCHMARK_64MB_SIZE, f"Size mismatch: {dest.stat().st_size}"
        file_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert file_hash == BENCHMARK_HASH, "Hash mismatch!"

        avg_speed = (BENCHMARK_64MB_SIZE / (1024 * 1024)) / elapsed
        chunk_count = len(job.chunks) if hasattr(job, "chunks") and job.chunks else 1
        return elapsed, avg_speed, peak_speed, chunk_count
    finally:
        if fsync_patcher:
            fsync_patcher.stop()


async def main():
    server = ThreadedServer(("127.0.0.1", 0), BenchmarkHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base_chunked_url = f"http://127.0.0.1:{port}/chunked64.bin"
    base_single_url = f"http://127.0.0.1:{port}/single64.bin"
    ifaces = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    pre_mod = load_pre_a7f4_downloader()

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)

    modes = [
        ("Single-Stream (Direct sequential)", base_single_url, False, False, lambda: DownloadManager()),
        ("Actual Pre-a7f4a26 Commit (d57ec59)", base_chunked_url, False, False, lambda: pre_mod.DownloadManager()),
        ("Milestone 3 Current Standard (1 Worker)", base_chunked_url, False, False, lambda: DownloadManager()),
        ("Milestone 3 Current Boost (3 Workers)", base_chunked_url, True, False, lambda: DownloadManager()),
        ("Milestone 3 Current (fsync DISABLED)", base_chunked_url, False, True, lambda: DownloadManager()),
    ]

    print("=" * 110)
    print("BURST 64 MB BENCHMARK: ACTUAL PRE-A7F4 vs CURRENT ENGINE (MEDIAN OF 5 RUNS)")
    print(f"Payload Size: {BENCHMARK_64MB_SIZE / (1024*1024):.0f} MB | Server: 127.0.0.1:{port}")
    print("=" * 110)

    results_summary = []

    for label, url, boosted, no_fsync, mgr_factory in modes:
        print(f"\nRunning 5 iterations: {label} ...", flush=True)
        times = []
        avg_speeds = []
        peak_speeds = []
        chunks = 0

        for i in range(5):
            dest = out_dir / f"test_{int(time.time()*1000)}_{i}.bin"
            mgr = mgr_factory()
            elapsed, avg_s, peak_s, chunk_c = await run_one_download(mgr, url, dest, ifaces, boosted, no_fsync)
            times.append(elapsed)
            avg_speeds.append(avg_s)
            peak_speeds.append(peak_s)
            chunks = chunk_c
            print(f"  Run {i+1}: {elapsed:.3f}s ({avg_s:.2f} MB/s), Peak: {peak_s:.2f} MB/s", flush=True)
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass

        med_time = statistics.median(times)
        med_speed = statistics.median(avg_speeds)
        med_peak = statistics.median(peak_speeds)

        results_summary.append({
            "label": label,
            "med_time": med_time,
            "med_speed": med_speed,
            "med_peak": med_peak,
            "all_times": times,
            "chunks": chunks,
        })

    print("\n" + "=" * 110)
    print(f"{'Engine Mode':<44} | {'Median (s)':<11} | {'Sustained Rate':<16} | {'Peak Window':<14} | {'Chunks'}")
    print("-" * 110)
    for r in results_summary:
        print(f"{r['label']:<44} | {r['med_time']:<11.3f} | {r['med_speed']:>9.2f} MB/s | {r['med_peak']:>8.2f} MB/s | {r['chunks']:<7}")
    print("=" * 110)

    # Fsync analysis
    std_res = results_summary[2]
    nofsync_res = results_summary[4]
    fsync_diff = std_res["med_time"] - nofsync_res["med_time"]
    fsync_per_chunk = (fsync_diff / 32) * 1000  # ms
    fsync_pct = (fsync_diff / std_res["med_time"]) * 100

    print("\n--- FSYNC SHARE & OVERHEAD ANALYSIS ---")
    print(f"Standard (fsync enabled)  Median Time : {std_res['med_time']:.3f} s")
    print(f"No-Fsync (fsync disabled) Median Time : {nofsync_res['med_time']:.3f} s")
    print(f"Total Fsync Time Across 32 Chunks     : {fsync_diff * 1000:>6.2f} ms ({fsync_diff:.3f} s)")
    print(f"Average Fsync Cost Per Chunk          : {fsync_per_chunk:>6.2f} ms / chunk")
    print(f"Fsync Share of Total Download Time    : {fsync_pct:>6.2f} %")

    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
