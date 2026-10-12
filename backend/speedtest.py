"""Burst — Per-interface speed benchmarking."""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Dict, List, Optional

import aiohttp

import config

TEST_PAYLOAD_BYTES = 2_500_000  # 2.5 MB payload for fast & accurate measurement


def _interface_connector(local_ip: str) -> aiohttp.TCPConnector:
    family = socket.AF_INET6 if ":" in local_ip else socket.AF_INET
    return aiohttp.TCPConnector(family=family, local_addr=(local_ip, 0))



async def benchmark_interface(local_ip: str, name: str) -> Dict[str, object]:
    ping_ms: Optional[float] = None
    started = time.perf_counter()
    total_read = 0

    try:
        connector = _interface_connector(local_ip)
        timeout = aiohttp.ClientTimeout(total=7.0, connect=3.0)
        test_url = f"https://speed.cloudflare.com/__down?bytes={TEST_PAYLOAD_BYTES}"

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Measure ping latency
            ping_start = time.perf_counter()
            try:
                async with session.head("https://speed.cloudflare.com/__down?bytes=0") as ping_res:
                    if ping_res.status < 500:
                        ping_ms = round((time.perf_counter() - ping_start) * 1000, 1)
            except Exception:
                ping_ms = None

            # Measure bandwidth
            bench_start = time.perf_counter()
            async with session.get(test_url) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total_read += len(chunk)
                    # Safety cutoff at 6 seconds max
                    if (time.perf_counter() - bench_start) > 6.0:
                        break

            elapsed = max(time.perf_counter() - bench_start, 0.001)

        speed_mb_s = (total_read / (1024 * 1024)) / elapsed
        speed_mbps = speed_mb_s * 8.0

        return {
            "name": name,
            "ip_address": local_ip,
            "speed_mb_s": round(speed_mb_s, 2),
            "speed_mbps": round(speed_mbps, 2),
            "ping_ms": ping_ms,
            "bytes_tested": total_read,
            "error": None,
        }
    except Exception as exc:
        return {
            "name": name,
            "ip_address": local_ip,
            "speed_mb_s": 0.0,
            "speed_mbps": 0.0,
            "ping_ms": ping_ms,
            "bytes_tested": total_read,
            "error": str(exc),
        }


async def benchmark_interfaces(interfaces: List[Dict[str, object]]) -> List[Dict[str, object]]:
    tasks = [
        benchmark_interface(str(iface["ip_address"]), str(iface["name"]))
        for iface in interfaces
        if iface.get("ip_address")
    ]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)

