"""Turbo Stream Dispatcher Proxy for Burst.

Runs a local HTTP CONNECT proxy on 127.0.0.1:59285.
When Chrome routes video streams (e.g. *.googlevideo.com, *.ttvnw.net, *.nflxvideo.net)
through this proxy via the Burst browser extension, each streaming connection is
dispatched across different active network interfaces (Wi-Fi, 5G Hotspot, Ethernet)
using native socket binding.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Dict, List, Optional, Set
import time

logger = logging.getLogger("burst.proxy")

DEFAULT_PROXY_PORT = 59285
BUFFER_SIZE = 65536


class TurboStreamProxy:
    """Asyncio HTTP CONNECT proxy that bonds streaming traffic across interfaces."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PROXY_PORT):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self._is_running = False
        self._round_robin_idx = 0
        self._lock = asyncio.Lock()

        # Telemetry & stats
        self.total_connections = 0
        self.active_connections = 0
        self.bytes_by_interface: Dict[str, int] = {}
        self.active_by_interface: Dict[str, int] = {}
        self.recent_targets: List[Dict[str, any]] = []
        self._interfaces_fn = None

    def set_interfaces_provider(self, fn):
        """Set a callable that returns list of active interface IP strings or interface dicts."""
        self._interfaces_fn = fn

    def _get_available_ips(self) -> List[str]:
        if not self._interfaces_fn:
            return []
        try:
            ifaces = self._interfaces_fn()
            ips = []
            if isinstance(ifaces, dict):
                ips = list(ifaces.keys())
            elif isinstance(ifaces, (list, tuple, set)):
                for item in ifaces:
                    if isinstance(item, str):
                        ips.append(item)
                    elif isinstance(item, dict) and "ip_address" in item:
                        ips.append(item["ip_address"])
                    elif hasattr(item, "ip_address"):
                        ips.append(getattr(item, "ip_address"))
            # Filter out localhost and empty
            return [ip for ip in ips if ip and not ip.startswith("127.")]
        except Exception as e:
            logger.debug(f"[PROXY] Error getting interface IPs: {e}")
            return []

    async def _select_next_interface(self) -> Optional[str]:
        available = self._get_available_ips()
        if not available:
            return None

        async with self._lock:
            # Pick next interface in round robin
            self._round_robin_idx = (self._round_robin_idx + 1) % len(available)
            selected = available[self._round_robin_idx]
            return selected

    async def start(self) -> None:
        """Starts the proxy server."""
        if self._is_running:
            return

        try:
            self.server = await asyncio.start_server(
                self._handle_client,
                self.host,
                self.port,
            )
            self._is_running = True
            logger.info(f"[PROXY] Turbo Stream Proxy listening on {self.host}:{self.port}")
        except Exception as e:
            self._is_running = False
            logger.error(f"[PROXY] Failed to start proxy on {self.host}:{self.port}: {e}")
            raise

    async def stop(self) -> None:
        """Stops the proxy server."""
        self._is_running = False
        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass
            self.server = None
        logger.info("[PROXY] Turbo Stream Proxy stopped")

    @property
    def is_running(self) -> bool:
        return self._is_running and self.server is not None

    def get_status(self) -> Dict[str, any]:
        """Returns live proxy statistics."""
        return {
            "running": self.is_running,
            "host": self.host,
            "port": self.port,
            "total_connections": self.total_connections,
            "active_connections": self.active_connections,
            "bytes_by_interface": dict(self.bytes_by_interface),
            "active_by_interface": dict(self.active_by_interface),
            "recent_targets": self.recent_targets[-15:],
            "bonded_interfaces": self._get_available_ips(),
        }

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle incoming client HTTP connection."""
        self.total_connections += 1
        self.active_connections += 1
        bound_ip = None

        try:
            # Read request line (e.g. CONNECT host:port HTTP/1.1)
            request_line_bytes = await asyncio.wait_for(client_reader.readline(), timeout=10.0)
            if not request_line_bytes:
                client_writer.close()
                return

            request_line = request_line_bytes.decode("latin1", errors="replace").strip()
            parts = request_line.split()
            if len(parts) < 2:
                client_writer.close()
                return

            method, target = parts[0].upper(), parts[1]

            # Read headers
            headers_list = []
            host_header = ""
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=5.0)
                if not line or line in (b"\r\n", b"\n", b""):
                    break
                headers_list.append(line)
                decoded_line = line.decode("latin1", errors="replace").strip()
                if decoded_line.lower().startswith("host:"):
                    host_header = decoded_line.split(":", 1)[1].strip()

            if method == "CONNECT":
                # Handle HTTPS CONNECT tunneling
                if ":" in target:
                    host, port_str = target.rsplit(":", 1)
                    port = int(port_str)
                else:
                    host, port = target, 443

                bound_ip = await self._select_next_interface()
                remote_reader, remote_writer = await self._connect_remote(host, port, bound_ip)

                # Send 200 Connection Established to client
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()

                # Log target
                self._record_target(host, port, bound_ip)

                # Bi-directional tunnel
                await self._tunnel(client_reader, client_writer, remote_reader, remote_writer, bound_ip)

            else:
                # Handle plain HTTP forwarding (GET http://... or GET /path with Host)
                target_host = host_header
                port = 80
                path = target

                if target.startswith("http://"):
                    without_proto = target[7:]
                    slash_idx = without_proto.find("/")
                    host_part = without_proto[:slash_idx] if slash_idx != -1 else without_proto
                    path = without_proto[slash_idx:] if slash_idx != -1 else "/"
                    if ":" in host_part:
                        target_host, port_str = host_part.rsplit(":", 1)
                        port = int(port_str)
                    else:
                        target_host = host_part
                elif ":" in target_host:
                    target_host, port_str = target_host.rsplit(":", 1)
                    port = int(port_str)

                if not target_host:
                    client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    await client_writer.drain()
                    client_writer.close()
                    return

                bound_ip = await self._select_next_interface()
                remote_reader, remote_writer = await self._connect_remote(target_host, port, bound_ip)

                # Send request line + preserved headers
                http_ver = parts[2] if len(parts) > 2 else "HTTP/1.1"
                remote_writer.write(f"{method} {path} {http_ver}\r\n".encode("latin1"))
                for h in headers_list:
                    remote_writer.write(h)
                remote_writer.write(b"\r\n")
                await remote_writer.drain()

                self._record_target(target_host, port, bound_ip)
                await self._tunnel(client_reader, client_writer, remote_reader, remote_writer, bound_ip)

        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.debug(f"[PROXY] Client handling error: {e}")
        finally:
            self.active_connections = max(0, self.active_connections - 1)
            if bound_ip and bound_ip in self.active_by_interface:
                self.active_by_interface[bound_ip] = max(0, self.active_by_interface[bound_ip] - 1)
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    async def _connect_remote(
        self,
        host: str,
        port: int,
        interface_ip: Optional[str],
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connects to remote destination with bound local interface, with fallback to default route."""
        if interface_ip:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        host,
                        port,
                        local_addr=(interface_ip, 0),
                    ),
                    timeout=8.0,
                )
                self.active_by_interface[interface_ip] = self.active_by_interface.get(interface_ip, 0) + 1
                return reader, writer
            except Exception as e:
                logger.debug(f"[PROXY] Interface binding to {interface_ip} failed for {host}:{port}: {e}. Falling back to default route.")

        # Fallback to default route without local_addr
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=8.0,
        )
        return reader, writer

    def _record_target(self, host: str, port: int, bound_ip: Optional[str]):
        """Records recent host for live observability."""
        self.recent_targets.append({
            "host": host,
            "port": port,
            "interface_ip": bound_ip or "default",
            "time": time.time(),
        })
        if len(self.recent_targets) > 50:
            self.recent_targets.pop(0)

    async def _tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
        interface_ip: Optional[str],
    ) -> None:
        """Pipes data bi-directionally between browser client and remote server."""
        bound_key = interface_ip or "default"

        async def forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter, is_download: bool):
            try:
                while self._is_running:
                    data = await src.read(BUFFER_SIZE)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()

                    if is_download:
                        self.bytes_by_interface[bound_key] = (
                            self.bytes_by_interface.get(bound_key, 0) + len(data)
                        )
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        t1 = asyncio.create_task(forward(client_reader, remote_writer, is_download=False))
        t2 = asyncio.create_task(forward(remote_reader, client_writer, is_download=True))

        done, pending = await asyncio.wait(
            [t1, t2],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        try:
            remote_writer.close()
            await remote_writer.wait_closed()
        except Exception:
            pass


# Global singleton
turbo_proxy = TurboStreamProxy()


def set_windows_system_proxy(enable: bool, server: str = "127.0.0.1:59285"):
    """Toggles Windows system proxy in HKCU so all browsers (Zen, Chrome, Edge, etc.) route through Burst."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import winreg
        import ctypes
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if enable:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>;127.0.0.1;localhost;127.0.0.1:59284;127.0.0.1:5173")
        winreg.CloseKey(key)

        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        ctypes.windll.Wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        ctypes.windll.Wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        logger.info(f"[PROXY] Windows system proxy {'enabled' if enable else 'disabled'} ({server})")
    except Exception as e:
        logger.debug(f"[PROXY] Could not update Windows system proxy: {e}")


import atexit
atexit.register(set_windows_system_proxy, False)
