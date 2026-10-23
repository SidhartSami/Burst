"""Turbo Stream Dispatcher Proxy for Burst.

Runs a local HTTP CONNECT proxy on 127.0.0.1:59285.
When the browser routes video streams (e.g. *.googlevideo.com, *.ttvnw.net, *.nflxvideo.net)
through this proxy, each streaming connection is intelligently scheduled across active
network interfaces (Wi-Fi, 5G Hotspot, Ethernet) using native socket binding.

Key Scheduling Capabilities:
1. Host- & Path-aware request classification (works inside HTTPS CONNECT tunnels).
2. Weighted assignment (Best Finish Time) based on rolling EWMA throughput & RTT.
3. Fast failover (<1.8s setup retry) with strict local vs. remote error differentiation.
4. Two-strike mid-stream drop protection (local errors only; remote resets / throttles excluded).
5. Exponential backoff quarantine (15s -> 30s -> 60s) with 1 MB sustained byte-window reset.
6. Idle tunnel harvesting (15s bidirectional quiescence only, safe for H2 multiplexing).
7. Conservative cold-start seeding & declared 1.2 MB chunk estimate for video tunnels.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("burst.proxy")

DEFAULT_PROXY_PORT = 59285
BUFFER_SIZE = 65536
CONNECT_TIMEOUT = 1.8  # Fast failover connection deadline (<2s)
IDLE_TUNNEL_TIMEOUT = 15.0  # Max seconds of zero bidirectional traffic before harvesting keepalive tunnel
SUSTAINED_RESET_BYTES = 1_000_000  # 1 MB clean transfer required to fully reset backoff

# Fixed chunk size estimate for HTTPS video tunnels (where content-length is encrypted)
DEFAULT_TUNNEL_CHUNK_ESTIMATE_BYTES = 1_200_000  # ~1.2 MB per media segment

# Local interface / routing errno codes that point at the local interface hardware/stack
LOCAL_INTERFACE_ERRNOS = {
    getattr(errno, "ENETUNREACH", 10051),
    getattr(errno, "EHOSTUNREACH", 10065),
    getattr(errno, "EADDRNOTAVAIL", 10049),
    getattr(errno, "ENETDOWN", 10050),
    getattr(errno, "ENETRESET", 10052),
    getattr(errno, "EHOSTDOWN", 10064),
}

# Regex patterns for host-based classification (CRUCIAL for HTTPS CONNECT tunnels)
RE_BULK_HOST = re.compile(
    r"(googlevideo\.com|ttvnw\.net|nflxvideo\.net|vimeocdn\.com|dailymotion\.com|video\.|cdn\.)",
    re.IGNORECASE,
)
RE_LATENCY_HOST = re.compile(
    r"(youtube\.com|ytimg\.com|ggpht\.com|twitch\.tv|jtvnw\.net|netflix\.com)",
    re.IGNORECASE,
)

# Regex patterns for plain HTTP path-based classification
RE_LOW_LATENCY_PATH = re.compile(
    r"(\.m3u8|\.mpd|\.json|/api/|/youtubei/|/init\.mp4|\.xml|videoplayback.*&(?:itag=140|live=1)|\.key)",
    re.IGNORECASE,
)
RE_BULK_SEGMENT_PATH = re.compile(
    r"(\.ts|\.m4s|\.m4v|\.mp4|\.webm|videoplayback|\.dash|range=|\.fmp4)",
    re.IGNORECASE,
)


def is_local_interface_error(exc: BaseException) -> bool:
    """Returns True if the error originates from the local network interface or binding,
    and False if it is a remote server error (connection refused, remote reset, DNS, HTTP throttle).
    """
    if isinstance(exc, asyncio.TimeoutError):
        # Bound socket connect timeout indicates local link stalled / dropped packets
        return True

    # Check for gaierror (DNS resolution failure is a remote/target issue, not adapter-specific)
    if isinstance(exc, socket.gaierror):
        return False

    # ConnectionRefusedError is sent by remote target rejecting the port
    if isinstance(exc, ConnectionRefusedError):
        return False

    if isinstance(exc, OSError):
        err_num = getattr(exc, "errno", None)
        if err_num in LOCAL_INTERFACE_ERRNOS:
            return True
        # Check error message strings for interface / binding problems
        msg = str(exc).lower()
        if "bind" in msg or "network is unreachable" in msg or "host is unreachable" in msg:
            return True

    return False


@dataclass
class InterfaceMetrics:
    """Rolling performance and health metrics for a specific network interface."""

    ip: str
    # Cold-start defaults: conservative 15 Mbps throughput, 45 ms RTT
    ewma_throughput_mbps: float = 15.0
    ewma_rtt_ms: float = 45.0
    has_real_samples: bool = False
    consecutive_failures: int = 0
    quarantine_count: int = 0
    quarantined_until: float = 0.0
    active_requests: int = 0
    total_bytes: int = 0
    total_requests: int = 0
    bytes_since_last_failure: int = 0
    last_success_ts: float = field(default_factory=time.time)

    @property
    def is_healthy(self) -> bool:
        if self.consecutive_failures >= 2 and time.time() < self.quarantined_until:
            return False
        return True

    def record_rtt(self, rtt_ms: float) -> None:
        """Update RTT using Exponential Weighted Moving Average (alpha = 0.25)."""
        rtt_ms = max(1.0, min(rtt_ms, 3000.0))
        if not self.has_real_samples:
            self.ewma_rtt_ms = rtt_ms
        else:
            self.ewma_rtt_ms = (0.75 * self.ewma_rtt_ms) + (0.25 * rtt_ms)

    def record_throughput(self, bytes_transferred: int, duration_sec: float) -> None:
        """Update throughput using EWMA (alpha = 0.20) for non-trivial transfers."""
        if duration_sec <= 0.05 or bytes_transferred < 16384:
            return
        mbps = (bytes_transferred * 8.0) / (duration_sec * 1_000_000.0)
        mbps = max(0.5, min(mbps, 5000.0))
        if not self.has_real_samples:
            self.ewma_throughput_mbps = mbps
            self.has_real_samples = True
        else:
            self.ewma_throughput_mbps = (0.80 * self.ewma_throughput_mbps) + (0.20 * mbps)

    def record_success(self, bytes_transferred: int = 0) -> None:
        self.consecutive_failures = 0
        self.quarantined_until = 0.0
        self.total_bytes += bytes_transferred
        self.bytes_since_last_failure += bytes_transferred
        self.total_requests += 1
        self.last_success_ts = time.time()

        # Reset exponential backoff count only after a sustained window (>1 MB)
        if self.bytes_since_last_failure >= SUSTAINED_RESET_BYTES:
            self.quarantine_count = 0

    def record_failure(self) -> None:
        """Records a verified local link failure with two-strike exponential backoff."""
        self.consecutive_failures += 1
        self.bytes_since_last_failure = 0
        if self.consecutive_failures >= 2:
            self.quarantine_count += 1
            # Exponential backoff: 15s * (2 ^ (count - 1)), max 120s
            backoff_sec = min(120.0, 15.0 * (2 ** max(0, self.quarantine_count - 1)))
            self.quarantined_until = time.time() + backoff_sec
            logger.warning(
                f"[PROXY] Interface {self.ip} quarantined for {backoff_sec:.0f}s (strike {self.consecutive_failures}, count {self.quarantine_count})"
            )

    def expected_finish_time(self, estimated_bytes: int) -> float:
        """Calculates expected completion time in seconds:
        T = (RTT / 1000) + (Bytes / Bandwidth) * (1 + 0.35 * active_requests)
        Cold-start penalty applied slightly if interface has never completed a real transfer.
        """
        rtt_sec = self.ewma_rtt_ms / 1000.0
        bw_bytes_sec = (self.ewma_throughput_mbps * 1_000_000.0) / 8.0
        transfer_sec = estimated_bytes / max(bw_bytes_sec, 10000.0)
        concurrency_penalty = 1.0 + (0.35 * self.active_requests)

        # Mild cold-start dampener to avoid dumping all initial bursts into an unmeasured link
        cold_penalty = 1.2 if not self.has_real_samples else 1.0
        return (rtt_sec + (transfer_sec * concurrency_penalty)) * cold_penalty


class TurboStreamProxy:
    """Asyncio HTTP CONNECT & Forwarding proxy that bonds streaming traffic across interfaces."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PROXY_PORT):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self._is_running = False
        self._lock = asyncio.Lock()

        # Telemetry & stats
        self.total_connections = 0
        self.active_connections = 0
        self.recent_targets: List[Dict[str, any]] = []
        self._interfaces_fn: Optional[Callable[[], any]] = None

        # Metrics per interface IP
        self.metrics: Dict[str, InterfaceMetrics] = {}

    def set_interfaces_provider(self, fn: Callable[[], any]):
        """Set a callable that returns list of active interface IP strings or interface dicts."""
        self._interfaces_fn = fn

    def _get_metric(self, ip: str) -> InterfaceMetrics:
        if ip not in self.metrics:
            self.metrics[ip] = InterfaceMetrics(ip=ip)
        return self.metrics[ip]

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
            # Filter out loopback and empty
            return [ip for ip in ips if ip and not ip.startswith("127.")]
        except Exception as e:
            logger.debug(f"[PROXY] Error getting interface IPs: {e}")
            return []

    def classify_request(self, host: str, target: str, is_connect: bool = False) -> str:
        """Classifies incoming request into 'latency', 'bulk', or 'standard'.
        For HTTPS CONNECT tunnels (where path is encrypted), host regex is used.
        """
        host_clean = host.split(":")[0].lower()

        if is_connect:
            # HTTPS tunnel: examine host domain
            if RE_BULK_HOST.search(host_clean):
                return "bulk"
            if RE_LATENCY_HOST.search(host_clean):
                return "latency"
            return "standard"

        # Plain HTTP: examine both host and path
        path_host = f"{host_clean}{target}"
        if RE_LOW_LATENCY_PATH.search(path_host):
            return "latency"
        if RE_BULK_SEGMENT_PATH.search(path_host):
            return "bulk"
        if RE_BULK_HOST.search(host_clean):
            return "bulk"
        if RE_LATENCY_HOST.search(host_clean):
            return "latency"
        return "standard"

    async def select_interface_for_request(
        self,
        request_type: str = "standard",
        estimated_bytes: Optional[int] = None,
        exclude_ips: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Selects the best interface using weighted scoring based on request type and link health."""
        if estimated_bytes is None:
            estimated_bytes = (
                DEFAULT_TUNNEL_CHUNK_ESTIMATE_BYTES if request_type == "bulk" else 64_000
            )

        available = self._get_available_ips()
        if not available:
            return None

        if exclude_ips:
            filtered = [ip for ip in available if ip not in exclude_ips]
            if filtered:
                available = filtered

        async with self._lock:
            # Filter healthy candidates first
            healthy = [ip for ip in available if self._get_metric(ip).is_healthy]
            candidates = healthy if healthy else available

            if len(candidates) == 1:
                return candidates[0]

            if request_type == "latency":
                # Manifests / APIs: Pick lowest RTT link
                best_ip = min(
                    candidates,
                    key=lambda ip: self._get_metric(ip).ewma_rtt_ms
                    * (1.0 + 0.25 * self._get_metric(ip).active_requests),
                )
                return best_ip

            elif request_type == "bulk":
                # Video chunks (DASH / HLS / YouTube): Pick best estimated finish time
                best_ip = min(
                    candidates,
                    key=lambda ip: self._get_metric(ip).expected_finish_time(estimated_bytes),
                )
                return best_ip

            else:
                # Balanced finish time
                best_ip = min(
                    candidates,
                    key=lambda ip: self._get_metric(ip).expected_finish_time(estimated_bytes),
                )
                return best_ip

    async def _select_next_interface(self) -> Optional[str]:
        """Backward-compatible interface selector using weighted assignment."""
        return await self.select_interface_for_request(request_type="bulk")

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
        """Returns live proxy statistics and interface telemetry."""
        bytes_by_iface = {ip: m.total_bytes for ip, m in self.metrics.items()}
        active_by_iface = {ip: m.active_requests for ip, m in self.metrics.items()}
        interface_telemetry = {}
        for ip, m in self.metrics.items():
            interface_telemetry[ip] = {
                "ewma_throughput_mbps": round(m.ewma_throughput_mbps, 2),
                "ewma_rtt_ms": round(m.ewma_rtt_ms, 1),
                "healthy": m.is_healthy,
                "has_real_samples": m.has_real_samples,
                "consecutive_failures": m.consecutive_failures,
                "quarantine_count": m.quarantine_count,
                "bytes_since_last_failure": m.bytes_since_last_failure,
                "active_requests": m.active_requests,
                "total_bytes": m.total_bytes,
            }

        return {
            "running": self.is_running,
            "host": self.host,
            "port": self.port,
            "total_connections": self.total_connections,
            "active_connections": self.active_connections,
            "bytes_by_interface": bytes_by_iface,
            "active_by_interface": active_by_iface,
            "interface_telemetry": interface_telemetry,
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

                # HOST-BASED CLASSIFICATION for HTTPS tunnels
                req_type = self.classify_request(host, target, is_connect=True)
                remote_reader, remote_writer, bound_ip = await self._connect_with_retry(
                    host=host,
                    port=port,
                    req_type=req_type,
                )

                if not remote_writer:
                    client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    await client_writer.drain()
                    client_writer.close()
                    return

                # Send 200 Connection Established to client
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()

                self._record_target(host, port, bound_ip, req_type)
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

                req_type = self.classify_request(target_host, path, is_connect=False)
                remote_reader, remote_writer, bound_ip = await self._connect_with_retry(
                    host=target_host,
                    port=port,
                    req_type=req_type,
                )

                if not remote_writer:
                    client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    await client_writer.drain()
                    client_writer.close()
                    return

                # Send request line + preserved headers
                http_ver = parts[2] if len(parts) > 2 else "HTTP/1.1"
                remote_writer.write(f"{method} {path} {http_ver}\r\n".encode("latin1"))
                for h in headers_list:
                    remote_writer.write(h)
                remote_writer.write(b"\r\n")
                await remote_writer.drain()

                self._record_target(target_host, port, bound_ip, req_type)
                await self._tunnel(client_reader, client_writer, remote_reader, remote_writer, bound_ip)

        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.debug(f"[PROXY] Client handling error: {e}")
        finally:
            self.active_connections = max(0, self.active_connections - 1)
            if bound_ip:
                metric = self._get_metric(bound_ip)
                metric.active_requests = max(0, metric.active_requests - 1)
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    async def _connect_with_retry(
        self,
        host: str,
        port: int,
        req_type: str = "standard",
    ) -> Tuple[Optional[asyncio.StreamReader], Optional[asyncio.StreamWriter], Optional[str]]:
        """Fast failover connect: attempts preferred link.
        Differentiates strictly between local interface errors (timeouts, bind failures, unreachables)
        and remote errors (refused connections, DNS errors, server resets).
        Only local interface faults penalize the link strike count.
        """
        excluded: Set[str] = set()
        max_attempts = 3

        for attempt in range(max_attempts):
            selected_ip = await self.select_interface_for_request(
                request_type=req_type,
                exclude_ips=excluded if excluded else None,
            )

            start_t = time.perf_counter()
            try:
                if selected_ip:
                    metric = self._get_metric(selected_ip)
                    metric.active_requests += 1

                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(
                            host,
                            port,
                            local_addr=(selected_ip, 0),
                        ),
                        timeout=CONNECT_TIMEOUT,
                    )
                    rtt_ms = (time.perf_counter() - start_t) * 1000.0
                    metric.record_rtt(rtt_ms)
                    return reader, writer, selected_ip
                else:
                    # Default route fallback
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=CONNECT_TIMEOUT,
                    )
                    return reader, writer, None

            except Exception as e:
                if selected_ip:
                    metric = self._get_metric(selected_ip)
                    metric.active_requests = max(0, metric.active_requests - 1)

                    # Only penalize interface if the failure is verified local to the adapter
                    if is_local_interface_error(e):
                        metric.record_failure()
                        excluded.add(selected_ip)
                        logger.debug(
                            f"[PROXY] Local interface failover: Link {selected_ip} failed for {host}:{port} ({e}). Retrying on alternative link..."
                        )
                    else:
                        logger.debug(
                            f"[PROXY] Remote server error for {host}:{port} ({e}). Interface {selected_ip} not penalized."
                        )
                else:
                    break

        # Final fallback to standard default route if specific interfaces failed
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=3.0,
            )
            return reader, writer, None
        except Exception as e:
            logger.error(f"[PROXY] All connection attempts failed for {host}:{port}: {e}")
            return None, None, None

    def _record_target(self, host: str, port: int, bound_ip: Optional[str], req_type: str = "standard"):
        """Records recent host for live observability."""
        self.recent_targets.append({
            "host": host,
            "port": port,
            "interface_ip": bound_ip or "default",
            "type": req_type,
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
        """Pipes data bi-directionally between browser client and remote server.
        - True idle harvesting: Only harvests when NEITHER direction has seen bytes for IDLE_TUNNEL_TIMEOUT (15s).
          Remote timeouts during slow server responses never trigger idle close.
        - Two-strike mid-stream rule: Only local socket unreachables/drops count as interface strikes.
          Remote resets, throttles, and client tab closes are strictly ignored.
        """
        metric = self._get_metric(interface_ip) if interface_ip else None
        transferred_bytes = 0
        tunnel_start = time.perf_counter()
        encountered_local_drop = False
        last_activity_ts = time.time()
        transfer_done = asyncio.Event()

        async def client_to_remote():
            nonlocal last_activity_ts
            try:
                while self._is_running and not transfer_done.is_set():
                    data = await client_reader.read(BUFFER_SIZE)
                    if not data:
                        break
                    last_activity_ts = time.time()
                    remote_writer.write(data)
                    await remote_writer.drain()
            except Exception:
                pass
            finally:
                transfer_done.set()
                try:
                    remote_writer.close()
                except Exception:
                    pass

        async def remote_to_client():
            nonlocal transferred_bytes, encountered_local_drop, last_activity_ts
            try:
                while self._is_running and not transfer_done.is_set():
                    data = await remote_reader.read(BUFFER_SIZE)
                    if not data:
                        break
                    last_activity_ts = time.time()
                    client_writer.write(data)
                    await client_writer.drain()
                    transferred_bytes += len(data)
            except OSError as e:
                # Distinguish local hardware/adapter faults from remote resets
                if is_local_interface_error(e):
                    encountered_local_drop = True
                    logger.debug(f"[PROXY] Local interface drop on {interface_ip}: {e}")
                else:
                    logger.debug(f"[PROXY] Remote server disconnect/reset on {interface_ip}: {e} (not penalized)")
            except Exception:
                pass
            finally:
                transfer_done.set()
                try:
                    client_writer.close()
                except Exception:
                    pass

        async def idle_watcher():
            """Closes tunnel ONLY when bidirectional traffic has ceased entirely for IDLE_TUNNEL_TIMEOUT seconds."""
            try:
                while self._is_running and not transfer_done.is_set():
                    await asyncio.sleep(2.0)
                    idle_sec = time.time() - last_activity_ts
                    if idle_sec >= IDLE_TUNNEL_TIMEOUT:
                        logger.debug(
                            f"[PROXY] Idle tunnel harvested on {interface_ip} after {idle_sec:.1f}s quiescence to allow rebalancing"
                        )
                        break
            except asyncio.CancelledError:
                pass
            finally:
                transfer_done.set()

        t1 = asyncio.create_task(client_to_remote())
        t2 = asyncio.create_task(remote_to_client())
        t3 = asyncio.create_task(idle_watcher())

        # Wait until transfer finishes or idle watcher triggers quiescence close
        await transfer_done.wait()

        for t in [t1, t2, t3]:
            t.cancel()

        try:
            remote_writer.close()
            await remote_writer.wait_closed()
        except Exception:
            pass

        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass

        duration = time.perf_counter() - tunnel_start
        if metric:
            if encountered_local_drop:
                # Local drop verified: apply two-strike rule
                metric.record_failure()
            else:
                metric.record_success(transferred_bytes)
                metric.record_throughput(transferred_bytes, duration)


# Global singleton
turbo_proxy = TurboStreamProxy()


def set_windows_system_proxy(enable: bool, server: str = "127.0.0.1:59285"):
    """Toggles Windows system proxy in HKCU so all browsers (Zen, Chrome, Edge, etc.) route through Burst."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if enable:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            winreg.SetValueEx(
                key,
                "ProxyOverride",
                0,
                winreg.REG_SZ,
                "<local>;127.0.0.1;localhost;127.0.0.1:59284;127.0.0.1:5173",
            )
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
