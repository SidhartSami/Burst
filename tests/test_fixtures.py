"""
Shared fixtures and mock HTTP server for Burst HTTP tests.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import http.server
import json as _json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import config
from downloader import (
    Chunk,
    ChunkStatus,
    DownloadJob,
    DownloadManager,
    InterfaceProgress,
    InsufficientDiskSpaceError,
    StalledDownloadError,
    URLAnalysis,
    analyze_url,
    calculate_backoff,
    check_disk_space_preflight,
    is_non_retryable_error,
    plan_adaptive_chunks,
    redact_url,
    sanitize_exception_text,
)

# Test payload: 256 KB deterministic data
TEST_DATA = bytes([(i * 31 + 7) % 256 for i in range(256 * 1024)])
TEST_DATA_HASH = hashlib.sha256(TEST_DATA).hexdigest()


class MockHttpHandler(http.server.BaseHTTPRequestHandler):
    """Configurable HTTP handler simulating various server behaviors."""
    etag = "v1-valid-etag"
    last_modified = "Wed, 23 Sep 2026 12:00:00 GMT"
    fail_first_n_requests = 0
    stall_endpoints = set()
    no_range_endpoints = set()
    truncate_endpoints = set()
    oversized_endpoints = set()
    wrong_range_endpoints = set()
    mid_200_endpoints = set()
    stall_after_bytes_endpoints = set()
    mid_fail_endpoints = set()
    slow_endpoints = set()
    mid_mutation_endpoints = set()
    mutation_abort_endpoints = set()
    all_fail_endpoints = set()
    request_counts = {}
    recorded_requests = []

    def log_message(self, format, *args):
        pass

    def handle_error(self, request, client_address):
        pass

    @classmethod
    def _format_etag(cls):
        if not cls.etag:
            return None
        val = str(cls.etag).strip()
        if val.startswith(('W/', 'w/', '"')):
            return val
        return f'"{val}"'

    def do_HEAD(self):
        endpoint = self.path.split("?")[0]
        if endpoint.startswith("/redirect_status_"):
            code = int(endpoint.split("_")[-1])
            self.send_response(code)
            self.send_header("Location", "/redirect_target")
            self.end_headers()
            return
        if endpoint == "/redirect_loop":
            self.send_response(302)
            self.send_header("Location", "/redirect_loop")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(TEST_DATA)))
        self.send_header("Content-Type", "application/octet-stream")
        et = self._format_etag()
        if et:
            self.send_header("ETag", et)
        if self.last_modified:
            self.send_header("Last-Modified", self.last_modified)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        endpoint = self.path.split("?")[0]
        self.request_counts[endpoint] = self.request_counts.get(endpoint, 0) + 1
        count = self.request_counts[endpoint]
        range_header = self.headers.get("Range")
        self.recorded_requests.append((endpoint, range_header, dict(self.headers)))

        # Redirect simulations
        if endpoint == "/redirect_loop":
            self.send_response(302)
            self.send_header("Location", "/redirect_loop")
            self.end_headers()
            return

        if endpoint.startswith("/redirect_status_"):
            code = int(endpoint.split("_")[-1])
            self.send_response(code)
            self.send_header("Location", "/redirect_target")
            self.end_headers()
            return

        if endpoint == "/redirect_target":
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(TEST_DATA)
            return

        # Fail first N requests simulation
        if endpoint == "/fail_first" and count <= self.fail_first_n_requests:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # All-fail simulation: probe (0-0) succeeds, but all chunk requests fail with 500
        if endpoint in self.all_fail_endpoints:
            if range_header == "bytes=0-0":
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes 0-0/{len(TEST_DATA)}")
                self.send_header("Content-Length", "1")
                et = self._format_etag()
                if et:
                    self.send_header("ETag", et)
                if self.last_modified:
                    self.send_header("Last-Modified", self.last_modified)
                self.end_headers()
                self.wfile.write(TEST_DATA[0:1])
                return
            else:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        # Immediate stall simulation (sleep longer than STALL_TIMEOUT_SECONDS)
        if endpoint in self.stall_endpoints:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            time.sleep(0.45)
            return

        # Fallback / No-range endpoint
        if endpoint in self.no_range_endpoints or not range_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.send_header("Content-Type", "application/octet-stream")
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA)
            return

        # Mid-download HTTP 200: probe range (bytes=0-0) returns 206, but worker requests get 200 OK
        if endpoint in self.mid_200_endpoints:
            if range_header == "bytes=0-0":
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes 0-0/{len(TEST_DATA)}")
                self.send_header("Content-Length", "1")
                et = self._format_etag()
                if et:
                    self.send_header("ETag", et)
                if self.last_modified:
                    self.send_header("Last-Modified", self.last_modified)
                self.end_headers()
                self.wfile.write(TEST_DATA[0:1])
                return
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(TEST_DATA)))
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(TEST_DATA)
                return

        # Handle Range request
        m = re.match(r"^bytes=(\d+)-(\d+)?$", range_header)
        if not m:
            self.send_response(416)
            self.end_headers()
            return

        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(TEST_DATA) - 1
        end = min(end, len(TEST_DATA) - 1)
        length = end - start + 1

        # Mid-flight mutation: on chunk request, change ETag and respond
        if endpoint in self.mid_mutation_endpoints and range_header != "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            self.send_header("ETag", '"v2-mutated-etag"')
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])
            return

        # Mutation abort simulation: request 1 (probe bytes=0-0) -> 206, request 2 (chunk 0) -> 206, request 3 (chunk 1) -> 206 with mutated ETag
        if endpoint in self.mutation_abort_endpoints and range_header != "bytes=0-0":
            if count >= 3:
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
                self.send_header("Content-Length", str(length))
                self.send_header("ETag", '"v2-mutated-etag"')
                self.end_headers()
                self.wfile.write(TEST_DATA[start : end + 1])
                return

        # Wrong Content-Range simulation
        if endpoint in self.wrong_range_endpoints and range_header != "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes 0-10/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])
            return

        # Oversized data simulation
        if endpoint in self.oversized_endpoints and range_header != "bytes=0-0":
            oversized_len = length + 500
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(oversized_len))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1] + b"Z" * 500)
            return

        # Stall after initial bytes simulation on worker chunk requests (not probe bytes=0-0)
        if endpoint in self.stall_after_bytes_endpoints and range_header != "bytes=0-0" and count <= 2:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + 50])
            self.wfile.flush()
            time.sleep(1.0)
            return

        # Slow-but-progressing simulation (delay between chunks, well within 0.25s stall timeout)
        if endpoint in self.slow_endpoints:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            step = 8192
            for i in range(start, end + 1, step):
                self.wfile.write(TEST_DATA[i : min(i + step, end + 1)])
                self.wfile.flush()
                time.sleep(0.015)
            return

        # Mid-download socket severance simulation
        if endpoint in self.mid_fail_endpoints and count == 1:
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
            self.send_header("Content-Length", str(length))
            et = self._format_etag()
            if et:
                self.send_header("ETag", et)
            if self.last_modified:
                self.send_header("Last-Modified", self.last_modified)
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + 512])
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            except Exception:
                pass
            return

        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_DATA)}")
        et = self._format_etag()
        if et:
            self.send_header("ETag", et)
        if self.last_modified:
            self.send_header("Last-Modified", self.last_modified)

        if endpoint in self.truncate_endpoints and count == 1:
            truncated_len = max(1, length // 2)
            self.send_header("Content-Length", str(truncated_len))
            self.end_headers()
            self.wfile.write(TEST_DATA[start : start + truncated_len])
        else:
            self.send_header("Content-Length", str(length))
            self.end_headers()
            self.wfile.write(TEST_DATA[start : end + 1])


class BaseHttpTest(unittest.IsolatedAsyncioTestCase):
    """Base class for HTTP tests providing an in-process mock server."""
    @classmethod
    def setUpClass(cls):
        config.save_settings({
            "BASE_CHUNK_SIZE": 64 * 1024,
            "MIN_CHUNK_SIZE": 16 * 1024,
            "MAX_CHUNK_SIZE": 64 * 1024,
            "STALL_TIMEOUT_SECONDS": 0.25,
            "RETRY_BACKOFF_BASE": 0.01,
            "RETRY_BACKOFF_MAX": 0.05,
            "RETRY_JITTER_MAX": 0.005,
            "RETRY_ATTEMPTS": 3,
            "EXCLUDED_INTERFACE_COOLDOWN": 0.5,
            "SINGLE_INTERFACE_RECONNECT_TIMEOUT": 0.5,
        })
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockHttpHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        config.reset_settings()

    def setUp(self):
        MockHttpHandler.etag = "v1-valid-etag"
        MockHttpHandler.last_modified = "Wed, 23 Sep 2026 12:00:00 GMT"
        MockHttpHandler.fail_first_n_requests = 0
        MockHttpHandler.stall_endpoints.clear()
        MockHttpHandler.no_range_endpoints.clear()
        MockHttpHandler.truncate_endpoints.clear()
        MockHttpHandler.oversized_endpoints.clear()
        MockHttpHandler.wrong_range_endpoints.clear()
        MockHttpHandler.mid_200_endpoints.clear()
        MockHttpHandler.stall_after_bytes_endpoints.clear()
        MockHttpHandler.mid_fail_endpoints.clear()
        MockHttpHandler.slow_endpoints.clear()
        MockHttpHandler.mid_mutation_endpoints.clear()
        MockHttpHandler.mutation_abort_endpoints.clear()
        MockHttpHandler.all_fail_endpoints.clear()
        MockHttpHandler.request_counts.clear()
        MockHttpHandler.recorded_requests.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name)
        self.manager = DownloadManager()
        self.iface = [{"name": "Loopback", "ip_address": "127.0.0.1"}]

    def tearDown(self):
        self.temp_dir.cleanup()
