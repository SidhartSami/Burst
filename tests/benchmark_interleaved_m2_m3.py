"""
Burst — Interleaved 64 MB Benchmark: pre-a7f4a26 vs Milestone-2 vs M3-head.

Each engine runs in its own subprocess (via a temp file) to prevent module pollution.
Runs are interleaved in ABCABC order to cancel OS/cache warmup bias.
Reports median, min, max for each engine.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64


class RangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args): pass
    def handle_error(self, req, addr): pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"ibench-etag"')
        self.end_headers()

    def do_GET(self):
        rh = self.headers.get("Range")
        if not rh:
            self.send_response(200)
            self.send_header("Content-Length", str(len(BENCHMARK_DATA)))
            self.send_header("ETag", '"ibench-etag"')
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
        self.send_header("ETag", '"ibench-etag"')
        self.end_headers()
        self.wfile.write(BENCHMARK_DATA[start:end + 1])


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    def handle_error(self, req, addr): pass


WORKER_TEMPLATE = """\
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND = {backend_repr}
URL     = {url_repr}
DEST    = Path({dest_repr})

BENCHMARK_64MB_SIZE = 64 * 1024 * 1024
BLOCK_1MB = bytes([(i * 41 + 17) % 256 for i in range(1024 * 1024)])
BENCHMARK_DATA = BLOCK_1MB * 64
BENCHMARK_HASH = hashlib.sha256(BENCHMARK_DATA).hexdigest()
BASE_CS = 2 * 1024 * 1024

sys.path.insert(0, BACKEND)
import downloader as dl


async def run():
    ifaces = [{{"name": "Loopback", "ip_address": "127.0.0.1"}}]
    mgr = dl.DownloadManager()
    t0 = time.perf_counter()
    job = await mgr.create_job(URL, str(DEST), ifaces)
    if hasattr(job, "_ranges") and hasattr(job, "expected_size") and job.expected_size > 0:
        n = BENCHMARK_64MB_SIZE // BASE_CS
        job._ranges = [
            (i, i * BASE_CS, min((i + 1) * BASE_CS - 1, BENCHMARK_64MB_SIZE - 1))
            for i in range(n)
        ]
    task = mgr._job_tasks[job.job_id]
    await task
    elapsed = time.perf_counter() - t0

    ok = DEST.exists() and DEST.stat().st_size == BENCHMARK_64MB_SIZE
    if ok:
        h = hashlib.sha256(DEST.read_bytes()).hexdigest()
        ok = h == BENCHMARK_HASH

    status = getattr(job, "status", "?")
    error  = getattr(job, "error", "") or ""
    print(json.dumps({{"elapsed": elapsed, "ok": ok, "status": status, "error": error}}))


asyncio.run(run())
"""


def make_worker_file(backend: str, url: str, dest: Path, tmp_dir: str) -> Path:
    """Write a worker Python file and return its path."""
    script = WORKER_TEMPLATE.format(
        backend_repr=repr(backend.replace("\\", "/")),
        url_repr=repr(url),
        dest_repr=repr(str(dest).replace("\\", "/")),
    )
    p = Path(tmp_dir) / f"worker_{abs(hash(backend + str(dest)))}.py"
    p.write_text(script, encoding="utf-8")
    return p


def run_engine_subprocess(backend: str, url: str, dest: Path, tmp_dir: str) -> dict:
    """Run one download in an isolated subprocess. Return result dict."""
    worker = make_worker_file(backend, url, dest, tmp_dir)
    try:
        result = subprocess.run(
            [sys.executable, str(worker)],
            capture_output=True, text=True, timeout=60
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip().startswith("{")]
        if lines:
            return json.loads(lines[-1])
        return {
            "elapsed": None, "ok": False,
            "status": "subprocess_error",
            "error": (result.stderr or result.stdout)[-400:],
        }
    finally:
        worker.unlink(missing_ok=True)


def main():
    server = ThreadedServer(("127.0.0.1", 0), RangeHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/bench64.bin"

    engines = [
        ("pre-a7f4a26 (d57ec59)",        "C:/Coding/Burst_pre/backend"),
        ("Milestone-2  (f412f6f tag)",    "C:/Coding/Burst_m2/backend"),
        ("M3-head      (single-iface)",   "C:/Coding/Burst/backend"),
    ]

    N_RUNS = 5
    all_times: dict[str, list[float]] = {name: [] for name, _ in engines}

    td = tempfile.TemporaryDirectory()
    out_dir = Path(td.name)
    scripts_dir = td.name

    print("=" * 82)
    print("BURST 64 MB INTERLEAVED BENCHMARK  (5 runs, ABCABC order, subprocess isolation)")
    print(f"Payload: 64 MB  | Server: 127.0.0.1:{port}  | 32 × 2 MB uniform chunks")
    print("=" * 82)

    for run_idx in range(N_RUNS):
        for name, backend in engines:
            dest = out_dir / f"r{run_idx}_{name[:4].strip()}.bin"
            dest.unlink(missing_ok=True)
            try:
                res = run_engine_subprocess(backend, url, dest, scripts_dir)
                if res["ok"] and res["elapsed"] is not None:
                    all_times[name].append(res["elapsed"])
                    print(f"  [{run_idx+1}/5] {name:<46} → {res['elapsed']:.3f} s", flush=True)
                else:
                    err = res["error"][:120].replace("\n", " ")
                    print(f"  [{run_idx+1}/5] {name:<46} → FAIL  {err}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"  [{run_idx+1}/5] {name:<46} → TIMEOUT", flush=True)
            finally:
                dest.unlink(missing_ok=True)

    print("\n" + "=" * 82)
    print(f"{'Engine':<48} | {'Median':>8} | {'Min':>7} | {'Max':>7} | {'±Spread':>8} | {'MB/s':>7}")
    print("-" * 82)
    for name, _ in engines:
        times = all_times[name]
        if not times:
            print(f"{name:<48} | {'NO DATA':>8}")
            continue
        med = statistics.median(times)
        mn  = min(times)
        mx  = max(times)
        spd = (BENCHMARK_64MB_SIZE / (1024 * 1024)) / med
        print(f"{name:<48} | {med:>7.3f}s | {mn:>6.3f}s | {mx:>6.3f}s | ±{(mx-mn)/2:.3f}s | {spd:>6.2f}")
    print("=" * 82)

    server.shutdown()
    td.cleanup()


if __name__ == "__main__":
    main()
