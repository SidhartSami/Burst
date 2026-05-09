from __future__ import annotations

import socket
from dataclasses import dataclass, asdict
from ipaddress import ip_address
from typing import Dict, List, Optional

import psutil


@dataclass
class NetworkInterface:
    name: str
    ip_address: str
    is_up: bool
    interface_type: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


VIRTUAL_KEYWORDS = (
    "virtual",
    "vmware",
    "hyper-v",
    "loopback",
    "wsl",
    "docker",
    "veth",
    "vpn",
    "tun",
    "tap",
)


def _is_loopback_or_invalid(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return True
    return addr.is_loopback or addr.is_link_local


def _infer_interface_type(name: str) -> str:
    lowered = name.lower()
    if "usb" in lowered or "rndis" in lowered:
        return "USB"
    if "wi-fi" in lowered or "wifi" in lowered or "wlan" in lowered:
        return "WiFi"
    if "ethernet" in lowered or "lan" in lowered:
        return "Ethernet"
    return "Unknown"


def _ipv4_from_addresses(addresses: list) -> Optional[str]:
    for addr in addresses:
        # On Windows, psutil can expose address family as either enum or int.
        if addr.family in (socket.AF_INET, int(socket.AF_INET)) and addr.address:
            if not _is_loopback_or_invalid(addr.address):
                return addr.address
    return None


def get_active_interfaces() -> List[NetworkInterface]:
    interfaces: List[NetworkInterface] = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for name, iface_addrs in addrs.items():
        stat = stats.get(name)
        if stat is None or not stat.isup:
            continue

        lowered = name.lower()
        if any(key in lowered for key in VIRTUAL_KEYWORDS):
            continue

        ip = _ipv4_from_addresses(iface_addrs)
        if not ip:
            continue

        interfaces.append(
            NetworkInterface(
                name=name,
                ip_address=ip,
                is_up=stat.isup,
                interface_type=_infer_interface_type(name),
            )
        )

    return interfaces


def get_active_interfaces_dict() -> List[Dict[str, object]]:
    return [item.to_dict() for item in get_active_interfaces()]
