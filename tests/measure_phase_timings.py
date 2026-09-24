"""
Burst — Measure Exact Phase Timings on 64 MB payload (10 runs).
Measures real microsecond-accurate timings for all I/O phases:
1. URL analysis & probe (HEAD/Range bytes=0-0)
2. Thread pool dispatch / asyncio.to_thread overhead
3. Per-chunk os.fsync() (32 chunks)
4. Per-chunk os.replace() (32 chunks)
5. Per-chunk os.stat() calls during chunk validation
6. Merger os.fsync() on the merged file
7. Merger final atomic os.replace()
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
from downloader import DownloadManager, analyze_url

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()


class TimedBenchmarkHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args): pass
    def handle_error(self, req, addr): pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"bench-64mb-etag"')
        self.send_header("Last-Modified", "Wed, 23 Sep 2026 12:00:00 GMT")
        self.end_headers()

    def do_GET(self):
        rh = self.headers.get("Range")
        if not rh:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("ETag", '"bench-64mb-etag"')
            self.end_headers()
            self.wfile.write(BENCHMARK_DATA)
            return
        m = re.match(r"^bytes=(\d+)-(\d+)?$", rh)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(BENCHMARK_DATA) - 1
        end = min(end, len(BENCHMARK_DATA) - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(BENCHMARK_DATA)}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("ETag", '"bench-64mb-etag"')
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start:end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, req, addr): pass


async def profile_phases(url: str, dest: Path, ifaces: list) -> dict:
    metrics: dict = {
        "probe_time": 0.0,
        "chunk_fsync_times": [],
        "chunk_replace_times": [],
        "chunk_stat_times": [],
        "merger_fsync": 0.0,
        "merger_replace": 0.0,
        "thread_dispatch_overhead": 0.0,
        "total_elapsed": 0.0,
    }

    t0 = time.perf_counter()
    await analyze_url(url, ifaces[0]["ip_address"])
    metrics["probe_time"] = time.perf_counter() - t0

    orig_fsync = os.fsync
    orig_replace = os.replace
    orig_stat = os.stat

    def timed_fsync(fd):
        t = time.perf_counter()
        orig_fsync(fd)
        dur = time.perf_counter() - t
        # Merger fsyncs come in the merge step (after all 32 chunk replaces)
        if len(metrics["chunk_replace_times"]) >= 32:
            metrics["merger_fsync"] += dur
        else:
            metrics["chunk_fsync_times"].append(dur)

    def timed_replace(src, dst):
        t = time.perf_counter()
        orig_replace(src, dst)
        dur = time.perf_counter() - t
        src_s = str(src)
        if "merge_tmp" in src_s or ".merge_tmp" in src_s:
            metrics["merger_replace"] = dur
        else:
            metrics["chunk_replace_times"].append(dur)

    def timed_stat(path, *a, **kw):
        t = time.perf_counter()
        r = orig_stat(path, *a, **kw)
        metrics["chunk_stat_times"].append(time.perf_counter() - t)
        return r

    # Thread hop overhead baseline
    t_hop = time.perf_counter()
    for _ in range(32):
        await asyncio.to_thread(lambda: None)
    metrics["thread_dispatch_overhead"] = time.perf_counter() - t_hop

    mgr = DownloadManager()

    from unittest.mock import patch
    with patch("os.fsync", side_effect=timed_fsync), \
         patch("os.replace", side_effect=timed_replace), \
         patch("os.stat", side_effect=timed_stat):
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
    N = 10

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)

    print("=" * 82)
    print(f"BURST 64 MB PHASE TIMINGS — {N} RUNS, SINGLE-INTERFACE LOOPBACK")
    print("=" * 82)

    all_runs = []
    for i in range(N):
        dest = out_dir / f"profile_{i}.bin"
        m = await profile_phases(url, dest, ifaces)
        all_runs.append(m)
        dest.unlink(missing_ok=True)
        nf = len(m["chunk_fsync_times"])
        nr = len(m["chunk_replace_times"])
        ns = len(m["chunk_stat_times"])
        print(f"  Run {i+1:2d}: total={m['total_elapsed']:.3f}s "
              f"fsync={sum(m['chunk_fsync_times'])*1000:.1f}ms({nf}) "
              f"replace={sum(m['chunk_replace_times'])*1000:.1f}ms({nr}) "
              f"stat={sum(m['chunk_stat_times'])*1000:.1f}ms({ns}) "
              f"mrg_fsync={m['merger_fsync']*1000:.1f}ms "
              f"mrg_replace={m['merger_replace']*1000:.1f}ms", flush=True)

    def med_ms(key):
        return statistics.median(r[key] for r in all_runs) * 1000

    med_total = statistics.median(r["total_elapsed"] for r in all_runs)
    n_chunk = len(all_runs[0]["chunk_fsync_times"])

    med_fsync_total   = statistics.median(sum(r["chunk_fsync_times"])   for r in all_runs) * 1000
    med_replace_total = statistics.median(sum(r["chunk_replace_times"]) for r in all_runs) * 1000
    med_stat_total    = statistics.median(sum(r["chunk_stat_times"])     for r in all_runs) * 1000

    avg_fsync   = med_fsync_total   / max(n_chunk, 1)
    avg_replace = med_replace_total / max(n_chunk, 1)
    avg_stat    = med_stat_total    / max(len(all_runs[0]["chunk_stat_times"]), 1)

    print("\n" + "=" * 82)
    print(f"{'Phase':<50} | {'Median':>10}")
    print("-" * 82)
    print(f"{'1. Range Probe & Validator (HEAD+206)':<50} | {med_ms('probe_time'):>8.2f} ms")
    print(f"{'2. Thread Dispatch Overhead (32 to_thread hops)':<50} | {med_ms('thread_dispatch_overhead'):>8.2f} ms")
    print(f"{'3. Per-chunk os.fsync() total ({n_chunk} chunks)':<50} | {med_fsync_total:>8.2f} ms")
    print(f"{'   └─ per chunk average':<50} | {avg_fsync:>8.2f} ms")
    print(f"{'4. Per-chunk os.replace() total ({n_chunk} chunks)':<50} | {med_replace_total:>8.2f} ms")
    print(f"{'   └─ per chunk average':<50} | {avg_replace:>8.2f} ms")
    n_stat = len(all_runs[0]["chunk_stat_times"])
    print(f"{'5. os.stat() calls total ({n_stat} calls)':<50} | {med_stat_total:>8.2f} ms")
    print(f"{'   └─ per call average':<50} | {avg_stat:>8.4f} ms")
    print(f"{'6. Merger os.fsync()':<50} | {med_ms('merger_fsync'):>8.2f} ms")
    print(f"{'7. Merger os.replace()':<50} | {med_ms('merger_replace'):>8.2f} ms")
    print(f"{'8. Total wall-clock':<50} | {med_total*1000:>8.1f} ms ({med_total:.3f} s)")
    print("=" * 82)

    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
