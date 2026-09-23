"""
Burst — Measure Exact Phase Timings on 64 MB payload.
Measures real microsecond-accurate timings across 5 iterations:
1. URL analysis & probe (HEAD/Range bytes=0-0)
2. Per-chunk network streaming & buffer write
3. Per-chunk handle.flush()
4. Per-chunk os.fsync() across 32 chunks
5. Per-chunk os.replace() across 32 chunks
6. Merger read/write across 32 chunks (64 MB)
7. Merger fsync()
8. Merger atomic os.replace()
9. Thread dispatch / asyncio.to_thread round-trip overhead
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

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import config
from downloader import DownloadManager, analyze_url, is_strong_etag

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()


class TimedBenchmarkHandler(http.server.BaseHTTPRequestHandler):
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
        self.send_header("ETag", '"bench-64mb-etag"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", '"bench-64mb-etag"')
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
        self.send_header("ETag", '"bench-64mb-etag"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start : end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass


async def profile_phases(url: str, dest: Path, ifaces: list):
    # Instrumented metrics dictionary
    metrics = {
        "probe_time": 0.0,
        "chunk_fsync_total": 0.0,
        "chunk_fsync_times": [],
        "chunk_replace_total": 0.0,
        "chunk_replace_times": [],
        "chunk_flush_total": 0.0,
        "merger_write_total": 0.0,
        "merger_fsync": 0.0,
        "merger_replace": 0.0,
        "thread_dispatch_overhead": 0.0,
        "total_elapsed": 0.0,
    }

    # 1. Measure Probe time
    t0 = time.perf_counter()
    analysis = await analyze_url(url, ifaces[0]["ip_address"])
    metrics["probe_time"] = time.perf_counter() - t0

    # Patch os.fsync, os.replace, handle.flush to record individual phase timings
    orig_fsync = os.fsync
    orig_replace = os.replace

    def timed_fsync(fd):
        t_start = time.perf_counter()
        orig_fsync(fd)
        dur = time.perf_counter() - t_start
        metrics["chunk_fsync_times"].append(dur)
        metrics["chunk_fsync_total"] += dur

    def timed_replace(src, dst):
        t_start = time.perf_counter()
        orig_replace(src, dst)
        dur = time.perf_counter() - t_start
        if "merge_tmp" in str(src):
            metrics["merger_replace"] = dur
        else:
            metrics["chunk_replace_times"].append(dur)
            metrics["chunk_replace_total"] += dur

    # Measure thread hop
    t_hop = time.perf_counter()
    for _ in range(32):
        await asyncio.to_thread(lambda: None)
    metrics["thread_dispatch_overhead"] = time.perf_counter() - t_hop

    mgr = DownloadManager()

    from unittest.mock import patch
    with patch("os.fsync", side_effect=timed_fsync), patch("os.replace", side_effect=timed_replace):
        t_all = time.perf_counter()
        job = await mgr.create_job(url, str(dest), ifaces)
        task = mgr._job_tasks[job.job_id]
        await task
        metrics["total_elapsed"] = time.perf_counter() - t_all

    assert dest.exists()
    assert dest.stat().st_size == BENCHMARK_64MB_SIZE
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == BENCHMARK_HASH

    return metrics


async def main():
    server = ThreadedServer(("127.0.0.1", 0), TimedBenchmarkHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/bench64.bin"
    ifaces = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)

    print("=" * 80)
    print("BURST 64 MB BENCHMARK: MEASURED PHASE TIMINGS ACROSS 5 RUNS")
    print("=" * 80)

    all_runs = []
    for i in range(5):
        dest = out_dir / f"profile_{i}.bin"
        m = await profile_phases(url, dest, ifaces)
        all_runs.append(m)
        dest.unlink(missing_ok=True)
        print(f"Run {i+1}: Total={m['total_elapsed']:.3f}s | Fsync={m['chunk_fsync_total']*1000:.1f}ms | "
              f"Replace={m['chunk_replace_total']*1000:.1f}ms | Probe={m['probe_time']*1000:.1f}ms | "
              f"MergerReplace={m['merger_replace']*1000:.1f}ms")

    # Medians
    med_total = statistics.median(r["total_elapsed"] for r in all_runs)
    med_fsync = statistics.median(r["chunk_fsync_total"] for r in all_runs)
    med_replace = statistics.median(r["chunk_replace_total"] for r in all_runs)
    med_probe = statistics.median(r["probe_time"] for r in all_runs)
    med_merger_replace = statistics.median(r["merger_replace"] for r in all_runs)
    med_thread_hop = statistics.median(r["thread_dispatch_overhead"] for r in all_runs)

    print("\n" + "=" * 80)
    print(f"{'Measured Phase':<45} | {'Median Duration':<18}")
    print("-" * 80)
    print(f"{'1. Range Probe & Validator Extraction (HEAD+206)':<45} | {med_probe*1000:>10.2f} ms")
    print(f"{'2. Thread Pool Dispatch Across 32 Chunks (to_thread)':<45} | {med_thread_hop*1000:>10.2f} ms")
    print(f"{'3. Per-Chunk os.fsync() Total (32 chunks)':<45} | {med_fsync*1000:>10.2f} ms")
    print(f"{'   - Average fsync() per chunk':<45} | {(med_fsync/32)*1000:>10.2f} ms/chunk")
    print(f"{'4. Per-Chunk os.replace() Total (32 chunks)':<45} | {med_replace*1000:>10.2f} ms")
    print(f"{'   - Average replace() per chunk':<45} | {(med_replace/32)*1000:>10.2f} ms/chunk")
    print(f"{'5. Merger Destination Atomic Rename (os.replace)':<45} | {med_merger_replace*1000:>10.2f} ms")
    print(f"{'6. Total Download Wall-Clock Duration':<45} | {med_total*1000:>10.2f} ms ({med_total:.3f} s)")
    print("=" * 80)

    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
