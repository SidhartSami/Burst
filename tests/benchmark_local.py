"""
Burst — Local HTTP Download Engine Benchmark (Milestone 2).

Measures:
- Single-worker vs Multi-worker (Boost Mode) download performance
- Total time, throughput (MB/s), chunk count, retries, stalls, integrity.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import config
from downloader import DownloadManager, ChunkStatus

BENCHMARK_SIZE = 16 * 1024 * 1024  # 16 MB payload
BENCHMARK_DATA = bytes([(i * 37 + 13) % 256 for i in range(BENCHMARK_SIZE)])
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()


class BenchmarkHttpHandler(http.server.BaseHTTPRequestHandler):
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
        self.send_header("ETag", '"bench-etag-16mb"')
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", '"bench-etag-16mb"')
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
        self.send_header("ETag", '"bench-etag-16mb"')
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start : end + 1])


async def run_benchmark():
    # 1. Start Benchmark Server
    server = http.server.HTTPServer(("127.0.0.1", 0), BenchmarkHttpHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{port}/bench.bin"

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)
    ifaces = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    print("=" * 65)
    print(f"BURST HTTP ENGINE BENCHMARK (File size: {BENCHMARK_SIZE / (1024*1024):.1f} MB)")
    print("=" * 65)

    results = []

    for mode in ["Standard (1 Worker)", "Boost Mode (3 Workers)"]:
        dest = out_dir / f"bench_{'boost' if 'Boost' in mode else 'standard'}.bin"
        manager = DownloadManager()

        t0 = time.perf_counter()
        job = await manager.create_job(base_url, str(dest), ifaces)

        if "Boost" in mode:
            await manager.toggle_boost(job.job_id, ifaces)

        task = manager._job_tasks[job.job_id]
        await task
        elapsed = time.perf_counter() - t0

        # Verification
        assert dest.exists(), "Output file missing"
        assert dest.stat().st_size == BENCHMARK_SIZE, "File size mismatch"
        computed_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert computed_hash == BENCHMARK_HASH, "Integrity hash mismatch"

        mb_downloaded = job.total_downloaded / (1024 * 1024)
        avg_speed = mb_downloaded / elapsed if elapsed > 0 else 0
        total_retries = len(job.retry_events)
        chunks_count = len(job.chunks)

        results.append({
            "mode": mode,
            "time_sec": elapsed,
            "size_mb": mb_downloaded,
            "speed_mbs": avg_speed,
            "chunks": chunks_count,
            "retries": total_retries,
            "status": job.status,
            "integrity": "PASS" if computed_hash == BENCHMARK_HASH else "FAIL",
        })

    # Print summary table
    print(f"{'Mode':<26} | {'Time (s)':<10} | {'Throughput':<12} | {'Chunks':<8} | {'Retries':<8} | {'Integrity'}")
    print("-" * 78)
    for r in results:
        print(f"{r['mode']:<26} | {r['time_sec']:<10.3f} | {r['speed_mbs']:>7.2f} MB/s | {r['chunks']:<8} | {r['retries']:<8} | {r['integrity']}")
    print("=" * 78)

    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(run_benchmark())
