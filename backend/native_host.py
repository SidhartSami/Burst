"""
Burst Native Messaging Host
Bridges the Chrome extension to the Burst FastAPI backend.
"""

import sys
import json
import struct
import os
import subprocess
import time
import requests
import winreg

API_BASE = "http://localhost:59284"

def get_burst_exe_path() -> str:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Burst.exe"
        )
        path, _ = winreg.QueryValueEx(key, None)
        winreg.CloseKey(key)
        return path
    except FileNotFoundError:
        # Fallback for dev/source mode
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "Burst.exe")

BURST_EXE = get_burst_exe_path()
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "session.token")

def get_token():
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r") as f:
                return f.read().strip()
    except:
        pass
    return ""

SESSION_TOKEN = get_token()
HEADERS = {"X-Burst-Token": SESSION_TOKEN}


# ── Chrome native messaging protocol ─────────────────────────────────────────

def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        sys.exit(0)
    length = struct.unpack("I", raw_length)[0]
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def send_message(data: dict):
    encoded = json.dumps(data).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ── Backend helpers ───────────────────────────────────────────────────────────

def ensure_backend_running():
    """Ping backend; launch Burst.exe if not responding."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("127.0.0.1", 59284))
        return True
    except Exception:
        pass

    # Not running — launch Burst in background
    if os.path.exists(BURST_EXE):
        subprocess.Popen(
            [BURST_EXE, "--headless"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(3)  # give it time to start FastAPI

    # Final check
    try:
        requests.get(f"{API_BASE}/interfaces?benchmark=false", timeout=5, headers=HEADERS)
        requests.get(f"{API_BASE}/interfaces?benchmark=false", timeout=10, headers=HEADERS)
        return True
    except requests.ConnectionError:
        return False


def get_interfaces():
    resp = requests.get(f"{API_BASE}/interfaces?benchmark=false", timeout=10, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    # Return list of IPs from active interfaces
    return [iface["ip_address"] for iface in data.get("interfaces", []) if iface.get("ip_address")]


def get_default_path(filename: str) -> str:
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5, headers=HEADERS)
        resp.raise_for_status()
        settings = resp.json().get("settings", {})
        default_dir = settings.get("DOWNLOAD_PATH", "C:\\Burst")
    except Exception:
        default_dir = "C:\\Burst"

    import urllib.parse
    safe_name = os.path.basename(urllib.parse.unquote(filename)) or "download.bin"
    return os.path.join(default_dir, safe_name)


def is_magnet(url: str) -> bool:
    url_lower = url.strip().lower()
    if url_lower.startswith("magnet:"):
        return True
    if url_lower.endswith(".torrent"):
        if url_lower.startswith("http://") or url_lower.startswith("https://"):
            return False
        return True
    return False


def extract_magnet_name(url: str) -> str:
    try:
        if "?" in url:
            import urllib.parse
            qs = url.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            names = params.get("dn", [])
            if names:
                return names[0]
    except:
        pass
    return "torrent"


# ── Main handler ──────────────────────────────────────────────────────────────

def handle(message: dict) -> dict:
    url = message.get("url", "").strip()
    if not url:
        return {"success": False, "error": "No URL provided"}

    if url in ("BURST_INTERNAL_CHECK", "__ping__") or url.endswith("__ping__") or "__ping" in url:
        # Just answer the ping without doing anything else
        ensure_backend_running()
        return {"success": True, "ping": "pong"}

    if not ensure_backend_running():
        return {"success": False, "error": "Could not connect to Burst. Is the app installed?"}

    try:
        interface_ips = get_interfaces()
    except Exception as e:
        return {"success": False, "error": f"Failed to get interfaces: {e}"}

    try:
        if is_magnet(url):
            magnet_name = extract_magnet_name(url)
            payload = {
                "url": url,
                "output_path": get_default_path(magnet_name),
                "interface_ips": interface_ips,
            }
            resp = requests.post(f"{API_BASE}/download", json=payload, timeout=10, headers=HEADERS)
        else:
            filename = url.split("/")[-1].split("?")[0] or "download.bin"
            payload = {
                "url": url,
                "output_path": get_default_path(filename),
                "interface_ips": interface_ips,
            }
            resp = requests.post(f"{API_BASE}/download", json=payload, timeout=10, headers=HEADERS)

        resp.raise_for_status()
        return {"success": True, "data": resp.json()}

    except requests.HTTPError as e:
        return {"success": False, "error": f"Backend error {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    msg = read_message()
    result = handle(msg)
    send_message(result)
