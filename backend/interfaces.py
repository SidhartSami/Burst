from __future__ import annotations

import socket
from dataclasses import dataclass, asdict
from ipaddress import ip_address, ip_network
from typing import Dict, List, Optional

import platform
import psutil


import re
import subprocess
from dataclasses import dataclass, asdict
from ipaddress import ip_address, ip_network
from typing import Dict, List, Optional, Tuple

import platform
import psutil


@dataclass
class NetworkInterface:
    name: str
    ip_address: str
    is_up: bool
    interface_type: str
    netmask: Optional[str] = None
    subnet: Optional[str] = None
    gateway: Optional[str] = None

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
    "tailscale",
    "wireguard",
    "zerotier",
    "openvpn",
)

CGNAT_NETWORK = ip_network("100.64.0.0/10")


def _is_loopback_or_invalid(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return True
    return addr.is_loopback or addr.is_link_local or addr in CGNAT_NETWORK


def _infer_interface_type(name: str) -> str:
    lowered = name.lower()
    if "usb" in lowered or "rndis" in lowered:
        return "USB"
    if "wi-fi" in lowered or "wifi" in lowered or "wlan" in lowered:
        return "WiFi"
    if "ethernet" in lowered or "lan" in lowered:
        return "Ethernet"
    return "Unknown"


def _get_default_gateways() -> Dict[str, str]:
    """Map interface IP address to default gateway IP."""
    gateways: Dict[str, str] = {}
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output("route print 0.0.0.0", shell=True, text=True, timeout=2)
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    gw = parts[2]
                    iface_ip = parts[3]
                    if not _is_loopback_or_invalid(gw):
                        gateways[iface_ip] = gw
        elif system == "Linux":
            out = subprocess.check_output(["ip", "route", "show", "default"], text=True, timeout=2)
            for line in out.splitlines():
                m = re.search(r"default via ([\d\.]+) .*? src ([\d\.]+)", line)
                if m:
                    gw, iface_ip = m.group(1), m.group(2)
                    if not _is_loopback_or_invalid(gw):
                        gateways[iface_ip] = gw
        elif system == "Darwin":
            out = subprocess.check_output(["netstat", "-rn", "-f", "inet"], text=True, timeout=2)
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 6 and parts[0] == "default":
                    gw = parts[1]
                    # Gateways found, interface names in parts[5]
                    if not _is_loopback_or_invalid(gw):
                        gateways["default"] = gw
    except Exception:
        pass
    return gateways


def _ipv4_and_mask_from_addresses(addresses: list) -> Tuple[Optional[str], Optional[str]]:
    for addr in addresses:
        if addr.family in (socket.AF_INET, int(socket.AF_INET)) and addr.address:
            if not _is_loopback_or_invalid(addr.address):
                return addr.address, getattr(addr, "netmask", None)
    return None, None


def get_active_interfaces() -> List[NetworkInterface]:
    interfaces: List[NetworkInterface] = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    gateways = _get_default_gateways()

    for name, iface_addrs in addrs.items():
        stat = stats.get(name)
        if stat is None or not stat.isup:
            continue

        lowered = name.lower()
        if platform.system() == "Windows":
            if any(key in lowered for key in VIRTUAL_KEYWORDS):
                continue
        elif platform.system() == "Darwin":
            if any(key in lowered for key in VIRTUAL_KEYWORDS) or lowered.startswith("lo") or lowered.startswith("utun"):
                continue
        else:
            if any(key in lowered for key in VIRTUAL_KEYWORDS) or lowered == "lo":
                continue

        ip, netmask = _ipv4_and_mask_from_addresses(iface_addrs)
        if not ip:
            continue

        subnet_str = None
        if netmask:
            try:
                subnet_str = str(ip_network(f"{ip}/{netmask}", strict=False))
            except Exception:
                subnet_str = None

        gateway_ip = gateways.get(ip) or gateways.get("default")

        interfaces.append(
            NetworkInterface(
                name=name,
                ip_address=ip,
                is_up=stat.isup,
                interface_type=_infer_interface_type(name),
                netmask=netmask,
                subnet=subnet_str,
                gateway=gateway_ip,
            )
        )

    return interfaces


def get_active_interfaces_dict() -> List[Dict[str, object]]:
    return [item.to_dict() for item in get_active_interfaces()]


def analyze_interface_topology(interfaces: Optional[List[NetworkInterface]] = None) -> Dict[str, Any]:
    """Smart Interface Advisor: Detect shared subnets, gateway collisions, and multi-WAN efficiency."""
    if interfaces is None:
        interfaces = get_active_interfaces()

    count = len(interfaces)
    if count == 0:
        return {
            "status": "NO_INTERNET",
            "severity": "error",
            "title": "No Active Connections",
            "message": "No valid network adapters found. Connect to Wi-Fi or Ethernet.",
            "recommendation": "Connect your PC to a network to begin downloading.",
            "can_bond": False,
            "colliding_interfaces": [],
        }

    if count == 1:
        iface = interfaces[0]
        return {
            "status": "SINGLE_INTERFACE",
            "severity": "info",
            "title": "Single Connection Active",
            "message": f"Using {iface.name} ({iface.interface_type}). Burst will download via this connection.",
            "recommendation": "Tether your phone via USB or connect Ethernet + Wi-Fi to unlock multi-network bonding boost.",
            "can_bond": False,
            "colliding_interfaces": [],
        }

    # Group by subnet and/or gateway
    subnet_groups: Dict[str, List[NetworkInterface]] = {}
    gateway_groups: Dict[str, List[NetworkInterface]] = {}

    for iface in interfaces:
        if iface.subnet:
            subnet_groups.setdefault(iface.subnet, []).append(iface)
        if iface.gateway:
            gateway_groups.setdefault(iface.gateway, []).append(iface)

    # Check collisions
    colliding = []
    conflict_reason = ""

    for snet, ifaces in subnet_groups.items():
        if len(ifaces) > 1:
            colliding = [i.name for i in ifaces]
            conflict_reason = f"shared subnet ({snet})"
            break

    if not colliding:
        for gw, ifaces in gateway_groups.items():
            if len(ifaces) > 1:
                colliding = [i.name for i in ifaces]
                conflict_reason = f"shared gateway ({gw})"
                break

    if colliding:
        names_str = " and ".join(colliding)
        return {
            "status": "COLLISION_DETECTED",
            "severity": "warning",
            "title": "Shared Network / Router Detected",
            "message": f"{names_str} connect through the same router ({conflict_reason}). Bonding won't increase download speed because both share one ISP pipe.",
            "recommendation": "For true 2x speeds, tether your phone's mobile hotspot (4G/5G) or plug into a separate network.",
            "can_bond": False,
            "colliding_interfaces": colliding,
        }

    # Optimal multi-WAN setup!
    types = [i.interface_type for i in interfaces]
    return {
        "status": "OPTIMAL_MULTI_WAN",
        "severity": "success",
        "title": "Multi-WAN Acceleration Ready",
        "message": f"Detected {count} independent network paths ({', '.join(types)}).",
        "recommendation": "Burst will split chunks and aggregate bandwidth across all connections simultaneously.",
        "can_bond": True,
        "colliding_interfaces": [],
    }

