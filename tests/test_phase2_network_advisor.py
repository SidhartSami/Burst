import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from interfaces import NetworkInterface, analyze_interface_topology


def test_topology_no_interfaces():
    result = analyze_interface_topology([])
    assert result["status"] == "NO_INTERNET"
    assert result["can_bond"] is False
    assert result["severity"] == "error"


def test_topology_single_interface():
    iface = NetworkInterface(
        name="Wi-Fi",
        ip_address="192.168.1.50",
        is_up=True,
        interface_type="WiFi",
        netmask="255.255.255.0",
        subnet="192.168.1.0/24",
        gateway="192.168.1.1",
    )
    result = analyze_interface_topology([iface])
    assert result["status"] == "SINGLE_INTERFACE"
    assert result["can_bond"] is False
    assert result["severity"] == "info"
    assert "Wi-Fi" in result["message"]


def test_topology_subnet_collision_detected():
    if1 = NetworkInterface(
        name="Wi-Fi",
        ip_address="192.168.1.50",
        is_up=True,
        interface_type="WiFi",
        netmask="255.255.255.0",
        subnet="192.168.1.0/24",
        gateway="192.168.1.1",
    )
    if2 = NetworkInterface(
        name="Ethernet",
        ip_address="192.168.1.75",
        is_up=True,
        interface_type="Ethernet",
        netmask="255.255.255.0",
        subnet="192.168.1.0/24",
        gateway="192.168.1.1",
    )
    result = analyze_interface_topology([if1, if2])
    assert result["status"] == "COLLISION_DETECTED"
    assert result["can_bond"] is False
    assert result["severity"] == "warning"
    assert "Wi-Fi" in result["colliding_interfaces"]
    assert "Ethernet" in result["colliding_interfaces"]
    assert "shared subnet (192.168.1.0/24)" in result["message"]


def test_topology_gateway_collision_detected():
    # Different subnets configured but pointing to identical default gateway
    if1 = NetworkInterface(
        name="Wi-Fi",
        ip_address="10.0.1.50",
        is_up=True,
        interface_type="WiFi",
        netmask="255.255.255.0",
        subnet="10.0.1.0/24",
        gateway="10.0.0.1",
    )
    if2 = NetworkInterface(
        name="Ethernet",
        ip_address="10.0.2.50",
        is_up=True,
        interface_type="Ethernet",
        netmask="255.255.255.0",
        subnet="10.0.2.0/24",
        gateway="10.0.0.1",
    )
    result = analyze_interface_topology([if1, if2])
    assert result["status"] == "COLLISION_DETECTED"
    assert result["can_bond"] is False
    assert "shared gateway (10.0.0.1)" in result["message"]


def test_topology_optimal_multi_wan():
    if1 = NetworkInterface(
        name="Ethernet (Fiber)",
        ip_address="192.168.1.50",
        is_up=True,
        interface_type="Ethernet",
        netmask="255.255.255.0",
        subnet="192.168.1.0/24",
        gateway="192.168.1.1",
    )
    if2 = NetworkInterface(
        name="Mobile Hotspot (5G)",
        ip_address="192.168.43.12",
        is_up=True,
        interface_type="USB",
        netmask="255.255.255.0",
        subnet="192.168.43.0/24",
        gateway="192.168.43.1",
    )
    result = analyze_interface_topology([if1, if2])
    assert result["status"] == "OPTIMAL_MULTI_WAN"
    assert result["can_bond"] is True
    assert result["severity"] == "success"
    assert len(result["colliding_interfaces"]) == 0


def test_open_folder_api(tmp_path):
    import asyncio
    import main
    from unittest.mock import patch

    test_file = tmp_path / "sample.zip"
    test_file.write_text("dummy")

    with patch("subprocess.Popen") as mock_popen:
        req = main.OpenFolderRequest(path=str(test_file))
        res = asyncio.run(main.open_folder(req))
        assert res["status"] == "success"
        assert mock_popen.called


