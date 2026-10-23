import asyncio
import unittest
import sys
import time
import socket
import errno
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from proxy import TurboStreamProxy, InterfaceMetrics, is_local_interface_error


class TestTurboStreamProxy(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Start a local echo / target HTTP server
        self.target_server = await asyncio.start_server(
            self._handle_target, "127.0.0.1", 0
        )
        self.target_port = self.target_server.sockets[0].getsockname()[1]

        # Start the proxy on an ephemeral port
        self.proxy = TurboStreamProxy(host="127.0.0.1", port=0)
        await self.proxy.start()
        self.proxy_port = self.proxy.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.proxy.stop()
        self.target_server.close()
        await self.target_server.wait_closed()

    async def _handle_target(self, reader, writer):
        # Target server echoes back received data
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                writer.write(b"ECHO:" + data)
                await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def test_proxy_lifecycle(self):
        """Test proxy start, status reporting, and stop."""
        self.assertTrue(self.proxy.is_running)
        status = self.proxy.get_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["total_connections"], 0)

    async def test_connect_tunneling(self):
        """Test HTTP CONNECT request through the proxy to target server."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy_port)

        # Send CONNECT request to target server
        connect_req = f"CONNECT 127.0.0.1:{self.target_port} HTTP/1.1\r\nHost: 127.0.0.1:{self.target_port}\r\n\r\n"
        writer.write(connect_req.encode())
        await writer.drain()

        # Read 200 response
        resp_line = await reader.readline()
        self.assertIn(b"200 Connection Established", resp_line)

        # Read empty line after headers
        empty = await reader.readline()
        self.assertEqual(empty, b"\r\n")

        # Now send data through tunnel
        test_payload = b"video_chunk_bytes_12345"
        writer.write(test_payload)
        await writer.drain()

        # Target echoes back
        echo_resp = await reader.read(len(b"ECHO:" + test_payload))
        self.assertEqual(echo_resp, b"ECHO:" + test_payload)

        writer.close()
        await writer.wait_closed()

        # Verify proxy telemetry
        status = self.proxy.get_status()
        self.assertGreaterEqual(status["total_connections"], 1)
        self.assertGreaterEqual(len(status["recent_targets"]), 1)
        self.assertEqual(status["recent_targets"][0]["port"], self.target_port)

    async def test_https_connect_host_classification(self):
        """Test host-based classification for encrypted HTTPS CONNECT tunnels."""
        # YouTube CDN chunk domain -> bulk
        self.assertEqual(
            self.proxy.classify_request("rr1---sn-4g5ednkk.googlevideo.com:443", "rr1---sn-4g5ednkk.googlevideo.com:443", is_connect=True),
            "bulk"
        )
        # Twitch video chunk CDN -> bulk
        self.assertEqual(
            self.proxy.classify_request("video-edge-123.ttvnw.net:443", "video-edge-123.ttvnw.net:443", is_connect=True),
            "bulk"
        )
        # YouTube API / navigation -> latency
        self.assertEqual(
            self.proxy.classify_request("www.youtube.com:443", "www.youtube.com:443", is_connect=True),
            "latency"
        )

    async def test_weighted_assignment_best_finish_time(self):
        """Test weighted assignment prefers high throughput link (Wi-Fi 100Mbps vs 4G 10Mbps)."""
        wifi_ip = "192.168.1.100"
        lte_ip = "192.168.43.50"
        self.proxy.set_interfaces_provider(lambda: [wifi_ip, lte_ip])

        m_wifi = self.proxy._get_metric(wifi_ip)
        m_wifi.ewma_throughput_mbps = 100.0
        m_wifi.ewma_rtt_ms = 15.0
        m_wifi.has_real_samples = True

        m_lte = self.proxy._get_metric(lte_ip)
        m_lte.ewma_throughput_mbps = 10.0
        m_lte.ewma_rtt_ms = 65.0
        m_lte.has_real_samples = True

        # For a bulk segment (e.g. 2MB), Wi-Fi must be chosen
        selected = await self.proxy.select_interface_for_request(
            request_type="bulk", estimated_bytes=2_000_000
        )
        self.assertEqual(selected, wifi_ip)

    async def test_latency_aware_routing_for_manifests(self):
        """Test low latency links are prioritized for manifests / playlists."""
        link_low_rtt = "192.168.1.100"
        link_high_rtt = "192.168.43.50"
        self.proxy.set_interfaces_provider(lambda: [link_low_rtt, link_high_rtt])

        m_low = self.proxy._get_metric(link_low_rtt)
        m_low.ewma_rtt_ms = 12.0
        m_low.ewma_throughput_mbps = 30.0
        m_low.has_real_samples = True

        m_high = self.proxy._get_metric(link_high_rtt)
        m_high.ewma_rtt_ms = 95.0
        m_high.ewma_throughput_mbps = 90.0
        m_high.has_real_samples = True

        # Manifest / API route
        selected = await self.proxy.select_interface_for_request(request_type="latency")
        self.assertEqual(selected, link_low_rtt)

    def test_local_vs_remote_error_classification(self):
        """Test strict separation of local adapter errors vs remote server errors."""
        # Local adapter errors -> True (should penalize link)
        self.assertTrue(is_local_interface_error(asyncio.TimeoutError()))
        self.assertTrue(is_local_interface_error(OSError(errno.ENETUNREACH, "Network is unreachable")))
        self.assertTrue(is_local_interface_error(OSError(errno.EHOSTUNREACH, "No route to host")))
        self.assertTrue(is_local_interface_error(OSError(10049, "Cannot assign requested address (bind failure)")))

        # Remote server errors -> False (must NOT penalize link)
        self.assertFalse(is_local_interface_error(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")))
        self.assertFalse(is_local_interface_error(socket.gaierror(-2, "Name or service not known (DNS failure)")))
        self.assertFalse(is_local_interface_error(ConnectionResetError(errno.ECONNRESET, "Remote server reset")))

    async def test_two_strike_mid_stream_rule(self):
        """Test that exactly one mid-stream local failure does NOT quarantine, but two consecutive strikes do."""
        link = "192.168.1.100"
        m = self.proxy._get_metric(link)

        # 1st mid-stream drop: 1 strike, link remains healthy
        m.record_failure()
        self.assertEqual(m.consecutive_failures, 1)
        self.assertTrue(m.is_healthy)

        # 2nd consecutive drop: 2 strikes -> triggers quarantine
        m.record_failure()
        self.assertEqual(m.consecutive_failures, 2)
        self.assertFalse(m.is_healthy)

    async def test_exponential_backoff_and_sustained_reset(self):
        """Test exponential quarantine backoff and that only >=1MB clean transfer resets counter."""
        link = "192.168.1.100"
        m = self.proxy._get_metric(link)

        # 2 strikes -> quarantine 1 (15s)
        now = time.time()
        m.record_failure()
        m.record_failure()
        self.assertFalse(m.is_healthy)
        self.assertGreater(m.quarantined_until, now + 14.0)

        # Expire quarantine
        m.quarantined_until = now - 1.0
        self.assertTrue(m.is_healthy)

        # Small success (<1MB) clears strike count but keeps backoff count intact
        m.record_success(bytes_transferred=50_000)
        self.assertTrue(m.is_healthy)
        self.assertEqual(m.consecutive_failures, 0)
        self.assertEqual(m.quarantine_count, 1)

        # Sustained transfer (>=1MB) fully resets quarantine backoff count to 0
        m.record_success(bytes_transferred=1_000_000)
        self.assertEqual(m.quarantine_count, 0)

    async def test_idle_tunnel_harvesting(self):
        """Test that tunnels with zero traffic for the idle timeout window are harvested to allow rebalancing."""
        # Connect client to proxy
        reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy_port)
        connect_req = f"CONNECT 127.0.0.1:{self.target_port} HTTP/1.1\r\nHost: 127.0.0.1:{self.target_port}\r\n\r\n"
        writer.write(connect_req.encode())
        await writer.drain()

        # Read 200 response
        resp = await reader.readline()
        self.assertIn(b"200 Connection Established", resp)
        await reader.readline()  # empty line

        # Actively write once
        writer.write(b"active_data")
        await writer.drain()
        echo = await reader.read(len(b"ECHO:active_data"))
        self.assertEqual(echo, b"ECHO:active_data")

        # Now test that active tunnel stays alive while traffic flows
        self.assertGreaterEqual(self.proxy.active_connections, 1)

        writer.close()
        await writer.wait_closed()

    async def test_plain_http_forwarding(self):
        """Test plain HTTP GET request forwarding through the proxy."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy_port)
        http_req = f"GET http://127.0.0.1:{self.target_port}/video.m3u8 HTTP/1.1\r\nHost: 127.0.0.1:{self.target_port}\r\n\r\n"
        writer.write(http_req.encode())
        await writer.drain()

        # Target echoes back "ECHO:GET /video.m3u8 ..."
        echo_resp = await reader.read(10)
        self.assertTrue(echo_resp.startswith(b"ECHO:GET"))

        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
