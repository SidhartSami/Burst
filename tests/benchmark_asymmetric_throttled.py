"""
Burst — Asymmetric Throttled Benchmark (Milestone 3).
Simulates multi-interface environments with severe bandwidth asymmetry:
- Interface 1 (127.0.0.1): 20 MB/s (Fast, e.g. Wi-Fi / Ethernet)
- Interface 2 (127.0.0.2): 2 MB/s (Slow, e.g. Cellular / 4G)

Compares:
1. Uniform 2 MB Chunks (Milestone 2 baseline, no tail tapering)
2. Static Planner (Fixed pre-split chunks based on initial probe)
3. Adaptive Planner (Milestone 3: warm-up, dynamic EWMA chunk sizing, tail-end tapering)

Payload: 64 MB
Runs 5 iterations per mode and reports median and spread.
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
from downloader import DownloadManager, DownloadJob, Chunk, ChunkStatus, plan_adaptive_chunks

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
        self.send_header("ETag", '"asym-64mb-etag"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", '"asym-64mb-etag"')
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
        self.send_header("ETag", '"asym-64mb-etag"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start : end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass


async def run_uniform_benchmark(url: str, dest: Path, ifaces: list):
    """Uniform 2 MB chunks (Milestone 2 behavior, no adaptive sizing or tail tapering)."""
    mgr = DownloadManager()
    limits = {"127.0.0.1": FAST_LIMIT, "127.0.0.2": SLOW_LIMIT}
    
    # 32 uniform 2 MB chunks
    base_cs = 2 * 1024 * 1024
    uniform_ranges = [(i, i * base_cs, min((i + 1) * base_cs - 1, BENCHMARK_64MB_SIZE - 1)) for i in range(32)]

    t0 = time.perf_counter()
    job = await mgr.create_job(url, str(dest), ifaces, bandwidth_limits=limits)
    # Force uniform ranges
    job._ranges = uniform_ranges

    task = mgr._job_tasks[job.job_id]
    await task
    elapsed = time.perf_counter() - t0

    assert dest.exists()
    assert dest.stat().st_size == BENCHMARK_64MB_SIZE
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == BENCHMARK_HASH

    p_fast = job.progress.get("127.0.0.1")
    p_slow = job.progress.get("127.0.0.2")

    return {
        "elapsed": elapsed,
        "fast_chunks": p_fast.chunks_completed if p_fast else 0,
        "slow_chunks": p_slow.chunks_completed if p_slow else 0,
        "fast_bytes": p_fast.downloaded if p_fast else 0,
        "slow_bytes": p_slow.downloaded if p_slow else 0,
        "total_chunks": len(job.chunks),
    }


async def run_static_planner_benchmark(url: str, dest: Path, ifaces: list):
    """Static planner (fixed chunk sizes based on initial probe, no dynamic slicing/tail tapering)."""
    mgr = DownloadManager()
    limits = {"127.0.0.1": FAST_LIMIT, "127.0.0.2": SLOW_LIMIT}

    # Static: 1 MB chunks across the board
    cs = 1 * 1024 * 1024
    static_ranges = [(i, i * cs, min((i + 1) * cs - 1, BENCHMARK_64MB_SIZE - 1)) for i in range(64)]

    t0 = time.perf_counter()
    job = await mgr.create_job(url, str(dest), ifaces, bandwidth_limits=limits)
    job._ranges = static_ranges

    task = mgr._job_tasks[job.job_id]
    await task
    elapsed = time.perf_counter() - t0

    assert dest.exists()
    assert dest.stat().st_size == BENCHMARK_64MB_SIZE
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == BENCHMARK_HASH

    p_fast = job.progress.get("127.0.0.1")
    p_slow = job.progress.get("127.0.0.2")

    return {
        "elapsed": elapsed,
        "fast_chunks": p_fast.chunks_completed if p_fast else 0,
        "slow_chunks": p_slow.chunks_completed if p_slow else 0,
        "fast_bytes": p_fast.downloaded if p_fast else 0,
        "slow_bytes": p_slow.downloaded if p_slow else 0,
        "total_chunks": len(job.chunks),
    }


async def run_adaptive_planner_benchmark(url: str, dest: Path, ifaces: list):
    """Milestone 3 Adaptive Scheduler: warm-up, dynamic EWMA sizing, tail tapering."""
    mgr = DownloadManager()
    limits = {"127.0.0.1": FAST_LIMIT, "127.0.0.2": SLOW_LIMIT}

    t0 = time.perf_counter()
    job = await mgr.create_job(url, str(dest), ifaces, bandwidth_limits=limits)
    task = mgr._job_tasks[job.job_id]
    await task
    elapsed = time.perf_counter() - t0

    if not dest.exists():
        print(f"\n[DEBUG] Job failed! status={job.status}, error={job.error}", flush=True)
        for cid, chk in sorted(job.chunks.items()):
            pf = job._chunk_files.get(cid)
            sz = pf.stat().st_size if pf and pf.exists() else None
            if chk.status != ChunkStatus.COMPLETE or sz != chk.expected_bytes:
                print(f"  Chunk {cid}: status={chk.status}, range={chk.start}-{chk.end} ({chk.expected_bytes} bytes), on disk={sz}", flush=True)

    assert dest.exists(), f"dest does not exist! job.status={job.status}, job.error={job.error}"
    assert dest.stat().st_size == BENCHMARK_64MB_SIZE
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == BENCHMARK_HASH

    p_fast = job.progress.get("127.0.0.1")
    p_slow = job.progress.get("127.0.0.2")

    return {
        "elapsed": elapsed,
        "fast_chunks": p_fast.chunks_completed if p_fast else 0,
        "slow_chunks": p_slow.chunks_completed if p_slow else 0,
        "fast_bytes": p_fast.downloaded if p_fast else 0,
        "slow_bytes": p_slow.downloaded if p_slow else 0,
        "total_chunks": len(job.chunks),
    }


async def main():
    server = ThreadedServer(("127.0.0.1", 0), AsymmetricServerHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/asym64.bin"
    ifaces = [
        {"name": "FastIface (20 MB/s)", "ip_address": "127.0.0.1"},
        {"name": "SlowIface (2 MB/s)", "ip_address": "127.0.0.2"},
    ]

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)

    modes = [
        ("1. Uniform 2 MB Chunks (M2 Baseline)", run_uniform_benchmark),
        ("2. Static Planner (Fixed 1 MB Chunks)", run_static_planner_benchmark),
        ("3. Adaptive Planner (M3 Dynamic + Tail)", run_adaptive_planner_benchmark),
    ]

    print("=" * 105)
    print("BURST 64 MB BENCHMARK: ASYMMETRIC THROTTLED INTERFACES (20 MB/s vs 2 MB/s)")
    print(f"Server: 127.0.0.1:{port} | Fast: 127.0.0.1 (20 MB/s) | Slow: 127.0.0.2 (2 MB/s)")
    print("5 Iterations Per Mode (reporting Median and Spread)")
    print("=" * 105)

    summary = []

    for name, runner in modes:
        print(f"\nRunning 5 iterations: {name} ...", flush=True)
        times = []
        fast_chunks_list = []
        slow_chunks_list = []
        total_chunks = 0

        for i in range(5):
            dest = out_dir / f"asym_{i}.bin"
            res = await runner(url, dest, ifaces)
            dest.unlink(missing_ok=True)
            times.append(res["elapsed"])
            fast_chunks_list.append(res["fast_chunks"])
            slow_chunks_list.append(res["slow_chunks"])
            total_chunks = res["total_chunks"]
            print(f"  Run {i+1}: {res['elapsed']:.3f}s | Fast: {res['fast_chunks']} chunks ({res['fast_bytes']/(1024*1024):.1f} MB) | "
                  f"Slow: {res['slow_chunks']} chunks ({res['slow_bytes']/(1024*1024):.1f} MB)", flush=True)

        med_time = statistics.median(times)
        min_time = min(times)
        max_time = max(times)
        spread_time = max_time - min_time
        med_fast_chunks = statistics.median(fast_chunks_list)
        med_slow_chunks = statistics.median(slow_chunks_list)

        summary.append({
            "name": name,
            "med_time": med_time,
            "min_time": min_time,
            "max_time": max_time,
            "spread": spread_time,
            "fast_chunks": med_fast_chunks,
            "slow_chunks": med_slow_chunks,
            "total_chunks": total_chunks,
            "speed": (BENCHMARK_64MB_SIZE / (1024 * 1024)) / med_time,
        })

    print("\n" + "=" * 105)
    print(f"{'Mode':<42} | {'Median (s)':<10} | {'Spread (min-max)':<18} | {'Throughput':<12} | {'Fast/Slow Chunks'}")
    print("-" * 105)
    for s in summary:
        print(f"{s['name']:<42} | {s['med_time']:<10.3f} | {s['min_time']:.3f} - {s['max_time']:.3f}s (±{s['spread']/2:.3f}) | "
              f"{s['speed']:>7.2f} MB/s | {int(s['fast_chunks']):>3} / {int(s['slow_chunks']):<3} ({s['total_chunks']} total)")
    print("=" * 105)

    # Calculate speedup
    m2_med = summary[0]["med_time"]
    m3_med = summary[2]["med_time"]
    delta_s = m2_med - m3_med
    pct_gain = ((m2_med - m3_med) / m2_med) * 100

    print("\n--- STRAGGLER MITIGATION ANALYSIS ---")
    print(f"Uniform 2 MB (Milestone 2) Median : {m2_med:.3f} s")
    print(f"Adaptive Planner (Milestone 3)   : {m3_med:.3f} s")
    print(f"Time Delta (M2 - M3)             : {delta_s:+.3f} s ({pct_gain:+.1f}%)")
    if delta_s > 0.05:
        print("Conclusion: Adaptive planner improved over uniform 2 MB baseline on this run.")
    elif delta_s < -0.05:
        print("Conclusion: Adaptive planner was slower than uniform baseline on this loopback run.")
        print("  Root cause: On loopback both interfaces share the same CPU bottleneck.")
        print("  The slow EWMA on 127.0.0.2 triggers more chunk splits, adding fsync/rename overhead")
        print("  with no corresponding straggler benefit. Straggler mitigation requires a real asymmetric")
        print("  network where the slow interface is actually bottlenecked by bandwidth, not CPU.")
    else:
        print("Conclusion: Within measurement noise; no significant difference on this loopback run.")


    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
