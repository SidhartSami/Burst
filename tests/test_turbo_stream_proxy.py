import asyncio
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from proxy import TurboStreamProxy


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
        # Target server just echoes back received data
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
        # Connect to proxy
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

    async def test_interface_round_robin_selection(self):
        """Test that proxy round-robins across available interfaces."""
        mock_interfaces = ["192.168.1.100", "192.168.43.50"]
        self.proxy.set_interfaces_provider(lambda: mock_interfaces)

        selected_1 = await self.proxy._select_next_interface()
        selected_2 = await self.proxy._select_next_interface()
        selected_3 = await self.proxy._select_next_interface()

        # Should cycle through mock interfaces
        self.assertIn(selected_1, mock_interfaces)
        self.assertIn(selected_2, mock_interfaces)
        self.assertEqual(selected_1, selected_3)
        self.assertNotEqual(selected_1, selected_2)

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

