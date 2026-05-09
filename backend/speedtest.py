"""Burst — Per-interface speed benchmarking."""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Dict, List

import aiohttp

import config


def _interface_connector(local_ip: str) -> aiohttp.TCPConnector:
    family = socket.AF_INET6 if ":" in local_ip else socket.AF_INET
    return aiohttp.TCPConnector(family=family, local_addr=(local_ip, 0))


async def benchmark_interface(local_ip: str, name: str) -> Dict[str, object]:
    started = time.perf_counter()
    total_read = 0

    try:
        connector = _interface_connector(local_ip)
        timeout = aiohttp.ClientTimeout(total=config.get("SPEEDTEST_TIMEOUT"))
        test_url = config.get("SPEEDTEST_URL")
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(test_url) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total_read += len(chunk)

        elapsed = max(time.perf_counter() - started, 0.001)
        speed_mb_s = (total_read / (1024 * 1024)) / elapsed
        return {"name": name, "ip_address": local_ip, "speed_mb_s": round(speed_mb_s, 3), "error": None}
    except Exception as exc:
        return {"name": name, "ip_address": local_ip, "speed_mb_s": 0.0, "error": str(exc)}


async def benchmark_interfaces(interfaces: List[Dict[str, object]]) -> List[Dict[str, object]]:
    tasks = [
        benchmark_interface(str(iface["ip_address"]), str(iface["name"]))
        for iface in interfaces
        if iface.get("ip_address")
    ]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)
