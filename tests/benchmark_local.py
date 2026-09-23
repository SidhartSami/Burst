"""
Burst — Local HTTP Download Engine Benchmark (Milestone 2).

Compares:
1. Baseline (Single-stream / Pre-change architecture)
2. Standard Multi-part (1 Worker, chunked)
3. Boost Mode (3 Concurrent Workers)

Under two real-world server profiles:
- Unthrottled Server (Loopback I/O saturation)
- Throttled Server (Simulating 4 MB/s per-connection ISP/Server rate limit)
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import re
import socket
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import config
from downloader import DownloadManager, ChunkStatus

BENCHMARK_SIZE = 8 * 1024 * 1024  # 8 MB payload for snappy benchmark execution
BENCHMARK_DATA = bytes([(i * 37 + 13) % 256 for i in range(BENCHMARK_SIZE)])
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()


class BenchmarkHttpHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    throttle_bytes_per_sec = 0  # 0 means unthrottled

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    def _write_data(self, data: bytes):
        if self.throttle_bytes_per_sec <= 0:
            self.wfile.write(data)
            return

        # Write in 32 KB blocks with pacing
        block_size = 32 * 1024
        delay = block_size / self.throttle_bytes_per_sec
        for offset in range(0, len(data), block_size):
            chunk = data[offset : offset + block_size]
            t_start = time.perf_counter()
            self.wfile.write(chunk)
            self.wfile.flush()
            t_spent = time.perf_counter() - t_start
            if delay > t_spent:
                time.sleep(delay - t_spent)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"bench-etag-8mb"')
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if not range_header or self.path.endswith("no_range.bin"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", '"bench-etag-8mb"')
            self.end_headers()
            self._write_data(BENCHMARK_DATA)
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
        self.send_header("ETag", '"bench-etag-8mb"')
        self.end_headers()
        self._write_data(BENCHMARK_DATA[start : end + 1])


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # Silence broken pipe / abort traces
        pass


async def run_benchmark():
    server = ThreadedHTTPServer(("127.0.0.1", 0), BenchmarkHttpHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    temp_dir = tempfile.TemporaryDirectory()
    out_dir = Path(temp_dir.name)
    ifaces = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    print("=" * 80)
    print(f"BURST HTTP DOWNLOAD ENGINE BENCHMARK (File size: {BENCHMARK_SIZE / (1024*1024):.1f} MB)")
    print("=" * 80)

    # Scenarios to evaluate
    scenarios = [
        # (Profile, Throttling Bps, Mode, Endpoint, Boost)
        ("Unthrottled", 0, "Baseline (Single-Stream)", f"http://127.0.0.1:{port}/no_range.bin", False),
        ("Unthrottled", 0, "Standard (1 Worker)", f"http://127.0.0.1:{port}/bench.bin", False),
        ("Unthrottled", 0, "Boost Mode (3 Workers)", f"http://127.0.0.1:{port}/bench.bin", True),
        ("Throttled (4 MB/s cap)", 4 * 1024 * 1024, "Baseline (Single-Stream)", f"http://127.0.0.1:{port}/no_range.bin", False),
        ("Throttled (4 MB/s cap)", 4 * 1024 * 1024, "Standard (1 Worker)", f"http://127.0.0.1:{port}/bench.bin", False),
        ("Throttled (4 MB/s cap)", 4 * 1024 * 1024, "Boost Mode (3 Workers)", f"http://127.0.0.1:{port}/bench.bin", True),
    ]

    results = []

    for profile, throttle_bps, mode, url, boosted in scenarios:
        BenchmarkHttpHandler.throttle_bytes_per_sec = throttle_bps
        dest = out_dir / f"bench_{profile[:4]}_{mode[:4]}.bin"
        if dest.exists():
            dest.unlink()

        manager = DownloadManager()
        t0 = time.perf_counter()
        job = await manager.create_job(url, str(dest), ifaces)

        if boosted:
            await manager.toggle_boost(job.job_id, ifaces)

        task = manager._job_tasks[job.job_id]
        await task
        elapsed = time.perf_counter() - t0

        # Verification
        assert dest.exists(), f"Output file {dest} missing"
        assert dest.stat().st_size == BENCHMARK_SIZE, f"File size mismatch: {dest.stat().st_size} vs {BENCHMARK_SIZE}"
        computed_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        assert computed_hash == BENCHMARK_HASH, "Integrity hash mismatch"

        mb_downloaded = job.total_downloaded / (1024 * 1024)
        avg_speed = mb_downloaded / elapsed if elapsed > 0 else 0
        chunks_count = len(job.chunks) if job.chunks else 1

        results.append({
            "profile": profile,
            "mode": mode,
            "time_sec": elapsed,
            "speed_mbs": avg_speed,
            "chunks": chunks_count,
            "integrity": "PASS",
        })

    # Print summary table
    print(f"{'Server Profile':<24} | {'Architecture / Mode':<28} | {'Time (s)':<10} | {'Throughput':<12} | {'Chunks':<6} | {'Integrity'}")
    print("-" * 96)
    for r in results:
        print(f"{r['profile']:<24} | {r['mode']:<28} | {r['time_sec']:<10.3f} | {r['speed_mbs']:>7.2f} MB/s | {r['chunks']:<6} | {r['integrity']}")
    print("=" * 96)

    server.shutdown()
    temp_dir.cleanup()


if __name__ == "__main__":
    asyncio.run(run_benchmark())
