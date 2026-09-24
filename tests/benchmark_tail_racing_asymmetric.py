"""
Burst — Tail Racing On vs Off Benchmark (Milestone 4).
10 Interleaved Runs on Asymmetric Throttled Server (20 MB/s vs 2 MB/s).
- 127.0.0.1: 20 MB/s (Fast)
- 127.0.0.2: 2 MB/s (Slow)

Compares:
- Tail Racing OFF (ENABLE_TAIL_RACING = False)
- Tail Racing ON  (ENABLE_TAIL_RACING = True)
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
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

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import config
from downloader import DownloadManager, Chunk, ChunkStatus

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()

FAST_LIMIT = 20 * 1024 * 1024  # 20 MB/s
SLOW_LIMIT = 2 * 1024 * 1024   # 2 MB/s


class AsymmetricServerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"asym-racing-64mb"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", '"asym-racing-64mb"')
            self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
            self.end_headers()
            self.wfile.write(BENCHMARK_DATA)
            return

        m = re.match(r"^bytes=(\d+)-(\d+)?$", range_header)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(BENCHMARK_DATA) - 1
        end = min(end, len(BENCHMARK_DATA) - 1)
        length = end - start + 1

        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(BENCHMARK_DATA)}")
        self.send_header("Content-Length", str(length))
        self.send_header("ETag", '"asym-racing-64mb"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start : end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass


async def run_single_download(url: str, dest: Path, ifaces: list, enable_tail_racing: bool):
    mgr = DownloadManager()
    limits = {"127.0.0.1": FAST_LIMIT, "127.0.0.2": SLOW_LIMIT}

    with patch.dict(config._DEFAULTS, {"ENABLE_TAIL_RACING": enable_tail_racing}):
        t0 = time.perf_counter()
        job = await mgr.create_job(url, str(dest), ifaces, bandwidth_limits=limits)
        task = mgr._job_tasks[job.job_id]
        await task
        elapsed = time.perf_counter() - t0

    assert dest.exists(), f"Destination file not found! error: {job.error}"
    assert dest.stat().st_size == BENCHMARK_64MB_SIZE, f"Size mismatch: {dest.stat().st_size}"
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == BENCHMARK_HASH, "Hash mismatch"

    p_fast = job.progress.get("127.0.0.1")
    p_slow = job.progress.get("127.0.0.2")

    return {
        "elapsed": elapsed,
        "fast_chunks": p_fast.chunks_completed if p_fast else 0,
        "slow_chunks": p_slow.chunks_completed if p_slow else 0,
        "fast_bytes": p_fast.downloaded if p_fast else 0,
        "slow_bytes": p_slow.downloaded if p_slow else 0,
    }


async def main():
    server = ThreadedServer(("127.0.0.1", 0), AsymmetricServerHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/asym64.bin"
    ifaces = [
        {"name": "Fast (20 MB/s)", "ip_address": "127.0.0.1"},
        {"name": "Slow (2 MB/s)", "ip_address": "127.0.0.2"},
    ]

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)

    num_runs = 10
    print("=" * 95)
    print("BURST TAIL RACING BENCHMARK: 10 INTERLEAVED RUNS (20 MB/s vs 2 MB/s)")
    print(f"Server: 127.0.0.1:{port} | Fast: 127.0.0.1 (20 MB/s) | Slow: 127.0.0.2 (2 MB/s)")
    print(f"Payload: 64 MB | Total Runs: {num_runs * 2} interleaved (OFF, ON, OFF, ON...)")
    print("=" * 95)

    off_times = []
    on_times = []

    for round_num in range(1, num_runs + 1):
        print(f"\n[Round {round_num}/{num_runs}]", flush=True)

        # 1. Run Tail Racing OFF
        dest_off = out_dir / f"racing_off_{round_num}.bin"
        res_off = await run_single_download(url, dest_off, ifaces, enable_tail_racing=False)
        dest_off.unlink(missing_ok=True)
        off_times.append(res_off["elapsed"])
        print(f"  Tail Racing OFF : {res_off['elapsed']:.3f} s | Fast: {res_off['fast_chunks']} chunks ({res_off['fast_bytes']/(1024*1024):.1f} MB) | Slow: {res_off['slow_chunks']} chunks ({res_off['slow_bytes']/(1024*1024):.1f} MB)", flush=True)

        # 2. Run Tail Racing ON
        dest_on = out_dir / f"racing_on_{round_num}.bin"
        res_on = await run_single_download(url, dest_on, ifaces, enable_tail_racing=True)
        dest_on.unlink(missing_ok=True)
        on_times.append(res_on["elapsed"])
        print(f"  Tail Racing ON  : {res_on['elapsed']:.3f} s | Fast: {res_on['fast_chunks']} chunks ({res_on['fast_bytes']/(1024*1024):.1f} MB) | Slow: {res_on['slow_chunks']} chunks ({res_on['slow_bytes']/(1024*1024):.1f} MB)", flush=True)

    server.shutdown()
    temp_dir.cleanup()

    print("\n" + "=" * 95)
    print("SUMMARY RESULTS (10 Interleaved Runs)")
    print("=" * 95)
    print(f"{'Configuration':<22} | {'Min (s)':<10} | {'Median (s)':<12} | {'Max (s)':<10} | {'Spread (max-min)':<18}")
    print("-" * 95)

    med_off = statistics.median(off_times)
    min_off = min(off_times)
    max_off = max(off_times)
    spread_off = max_off - min_off

    med_on = statistics.median(on_times)
    min_on = min(on_times)
    max_on = max(on_times)
    spread_on = max_on - min_on

    print(f"{'Tail Racing OFF':<22} | {min_off:<10.3f} | {med_off:<12.3f} | {max_off:<10.3f} | {spread_off:<18.3f}")
    print(f"{'Tail Racing ON':<22} | {min_on:<10.3f} | {med_on:<12.3f} | {max_on:<10.3f} | {spread_on:<18.3f}")
    print("=" * 95)

    diff = med_off - med_on
    pct = (diff / med_off) * 100
    print(f"Median Delta (OFF - ON): {diff:+.3f} s ({pct:+.2f}%)")
    if diff > 0.05:
        print("Result: Tail racing demonstrated a measurable performance improvement.")
    elif diff < -0.05:
        print("Result: Tail racing incurred minor overhead without net throughput improvement on loopback.")
    else:
        print("Result: Tail racing performance difference is within noise margin (no regression established).")


if __name__ == "__main__":
    asyncio.run(main())
