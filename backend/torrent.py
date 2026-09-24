from __future__ import annotations
import asyncio
import time
import uuid
import socket
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

lt = None

def _init_lt():
    global lt
    if lt is None:
        import libtorrent as _lt
        lt = _lt

active_torrents: Dict[str, "TorrentJob"] = {}

import sys
if getattr(sys, 'frozen', False):
    APPDATA = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
    DHT_DIR = Path(APPDATA) / "Burst"
    DHT_DIR.mkdir(parents=True, exist_ok=True)
    DHT_STATE_FILE = DHT_DIR / "dht_state.dat"
else:
    DHT_STATE_FILE = Path("dht_state.dat")

# Characters illegal in Windows file/directory names
_WINDOWS_ILLEGAL = re.compile(r'[<>:"/|?*]')

def _sanitize_path(path: str) -> str:
    """Sanitize a file-system path — replace Windows-illegal characters in each
    name component (not the drive letter or path separators)."""
    drive, rest = os.path.splitdrive(path)
    # Split on both kinds of separator, sanitize each part, then rejoin
    parts = re.split(r'[\\/]', rest)
    clean = []
    for part in parts:
        if part:
            part = _WINDOWS_ILLEGAL.sub('-', part)  # replace illegal chars
            part = part.strip('. ')                  # strip leading/trailing dots/spaces
            part = part or '_'                       # don't leave empty components
        clean.append(part)
    return drive + os.sep.join(clean)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

def _make_settings(ip: Optional[str] = None) -> dict:
    _init_lt()
    base = {
        "user_agent": "qBittorrent/4.6.3",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_natpmp": True,
        "enable_upnp": True,
        "dht_bootstrap_nodes": (
            "router.bittorrent.com:6881,"
            "router.utorrent.com:6881,"
            "dht.transmissionbt.com:6881,"
            "dht.libtorrent.org:25401"
        ),
        "connection_speed": 50,
        "num_want": 300,
        "request_timeout": 20,
        "peer_timeout": 30,
        "min_reconnect_time": 2,
        "min_announce_interval": 3,
        "announce_to_all_tiers": True,
        "announce_to_all_trackers": True,
        "alert_mask": int(
            lt.alert.category_t.error_notification
            | lt.alert.category_t.tracker_notification
            | lt.alert.category_t.peer_notification
        ),
        "disk_io_write_mode": int(lt.io_buffer_mode_t.disable_os_cache),
    }
    if ip:
        # IPv6 requires brackets in listen_interfaces
        listen_ip = f"[{ip}]" if ":" in ip else ip
        base["listen_interfaces"] = f"{listen_ip}:{_find_free_port()}"
        base["outgoing_interfaces"] = ip
    else:
        # Fixed single port for meta session — only one runs at a time,
        # and a well-known port avoids firewall and DHT bootstrap issues.
        base["listen_interfaces"] = "0.0.0.0:6881"
    return base


def _bootstrap_dht(ses: lt.session):
    _init_lt()
    # In libtorrent 2.0, dht_bootstrap_nodes in settings handles router discovery.
    # Legacy ses.add_dht_router() is only called if dht_bootstrap_nodes is not available.
    if not hasattr(ses, "get_settings") or "dht_bootstrap_nodes" not in ses.get_settings():
        routers = [
            ("router.bittorrent.com", 6881),
            ("router.utorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("router.bitcomet.com", 6881),
            ("dht.libtorrent.org", 25401),
        ]
        for host, port in routers:
            try:
                ses.add_dht_router(host, port)
            except Exception as e:
                print(f"[TORRENT] Error adding DHT router {host}:{port} - {e}")


def _save_dht_state(ses: lt.session):
    """Persist DHT routing table so next session bootstraps instantly."""
    _init_lt()
    try:
        state = ses.save_state()
        DHT_STATE_FILE.write_bytes(lt.bencode(state))
    except Exception as e:
        print(f"[TORRENT] DHT state save error: {e}")


def _load_dht_state(ses: lt.session):
    """Load persisted DHT routing table if available."""
    _init_lt()
    try:
        if DHT_STATE_FILE.exists():
            state = lt.bdecode(DHT_STATE_FILE.read_bytes())
            ses.load_state(state)
            print("[TORRENT] DHT state loaded from disk — fast bootstrap!")
    except Exception as e:
        print(f"[TORRENT] DHT state load error (harmless): {e}")


# HTTPS/HTTP trackers on port 443/80 — ISP cannot block these
# UDP trackers included as fallback for users without ISP restrictions
TRACKERS = [
    # Official Linux Distro Trackers (for bare distro ISO magnets)
    "http://torrent.ubuntu.com:6969/announce",
    "http://ipv6.torrent.ubuntu.com:6969/announce",
    "http://bttracker.debian.org:6969/announce",

    # Robust HTTPS trackers
    "https://tracker.bt-hash.com:443/announce",
    "https://tracker.tamersunion.org:443/announce",
    "https://tracker.loligirl.cn:443/announce",
    "https://tracker.gbitt.info:443/announce",
    "https://tr.ready4.icu:443/announce",
    "https://tracker.foreverpirates.co:443/announce",
    
    # HTTP trackers
    "http://tracker.opentrackr.org:1337/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.gbitt.info:80/announce",
    "http://open.stealth.si:80/announce",
    "http://vps02.net.orel.ru:80/announce",
    "http://tracker.internetwarriors.net:1337/announce",
    
    # UDP trackers
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]


def _add_trackers(handle):
    _init_lt()
    for tier, url in enumerate(TRACKERS):
        handle.add_tracker({"url": url, "tier": tier})


# ---------------------------------------------------------------------------
# TorrentJob
# ---------------------------------------------------------------------------

class TorrentJob:
    def __init__(
        self,
        magnet_uri: str,
        output_path: str,
        interface_ips: List[str],
        job_id: str = None,
        bandwidth_limits: dict = None,
        resume_data: dict = None,
        file_priorities: Optional[Dict[int, int] | List[int]] = None,
        wait_for_selection: bool = False,
        paused: bool = False,
    ):
        self.job_id = job_id or str(uuid.uuid4())
        self.magnet_uri = magnet_uri
        self.output_path = output_path
        self.interface_ips = list(interface_ips)
        self.bandwidth_limits = bandwidth_limits or {}
        self.filename = self._extract_name(magnet_uri)

        # File selection: file index -> priority (0 = do not download, 1..7 = download, 4 = default)
        self.file_priorities: Dict[int, int] = {}
        if file_priorities:
            if isinstance(file_priorities, list):
                self.file_priorities = {i: int(p) for i, p in enumerate(file_priorities)}
            elif isinstance(file_priorities, dict):
                self.file_priorities = {int(k): int(v) for k, v in file_priorities.items()}

        self.wait_for_selection: bool = bool(wait_for_selection or paused)
        self._selection_ready_event = asyncio.Event()
        if not self.wait_for_selection:
            self._selection_ready_event.set()

        self.status = "paused" if (paused and not magnet_uri.startswith("magnet:")) else "fetching_metadata"
        self.progress = 0.0
        self.torrent_total_size = 0
        self.total_size = 0
        self.selected_size = 0
        self.selected_downloaded = 0
        self.downloaded = 0
        self.boosted = False
        
        if resume_data:
            self.progress = resume_data.get("progress", 0.0)
            self.torrent_total_size = resume_data.get("torrent_total_size", resume_data.get("expected_size", 0))
            self.total_size = self.torrent_total_size
            self.selected_size = resume_data.get("selected_size", self.total_size)
            self.downloaded = resume_data.get("downloaded", resume_data.get("total_downloaded", 0))
            self.selected_downloaded = resume_data.get("selected_downloaded", self.downloaded)
            self.status = resume_data.get("status", "fetching_metadata")
            self.boosted = resume_data.get("boosted", False)
            if "file_priorities" in resume_data and not self.file_priorities:
                self.file_priorities = {int(k): int(v) for k, v in resume_data["file_priorities"].items()}

        self.speed_combined = 0
        self.speeds: Dict[str, int] = {ip: 0 for ip in interface_ips}
        self.peers_per_interface: Dict[str, int] = {ip: 0 for ip in interface_ips}
        self.seeders = 0
        self.leechers = 0

        self._meta_session: Optional[lt.session] = None
        self._meta_handle = None
        self._torrent_info: Optional[lt.torrent_info] = None

        self.sessions: List[tuple] = []
        self.handles: List[tuple] = []

        self._running = True
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.error: Optional[str] = None

    def get_files(self) -> List[dict]:
        """Return list of files with path, size, priority, and progress."""
        if not self._torrent_info:
            return []
        ti = self._torrent_info
        num = ti.num_files()

        prog_bytes = [0] * num
        if self.handles:
            try:
                prog_bytes = self.handles[0][1].file_progress()
            except Exception:
                pass

        files = []
        for i in range(num):
            f_path = ti.files().file_path(i)
            f_size = ti.files().file_size(i)
            prio = self.file_priorities.get(i, 4)
            done = prog_bytes[i] if i < len(prog_bytes) else 0
            ratio = (done / f_size) if f_size > 0 else 1.0
            files.append({
                "index": i,
                "path": f_path,
                "size": f_size,
                "priority": prio,
                "downloaded": done,
                "progress": ratio,
                "wanted": prio > 0,
            })
        return files

    async def set_file_priorities(self, priorities: Dict[int, int] | List[int]) -> dict:
        """Dynamically update file download priorities across all active interface handles."""
        _init_lt()
        if not isinstance(priorities, (dict, list)):
            raise ValueError(f"Invalid priorities type: {type(priorities)}. Must be dict or list.")

        parsed_priorities: Dict[int, int] = {}
        if isinstance(priorities, list):
            for i, p in enumerate(priorities):
                try:
                    p_val = int(p)
                except (ValueError, TypeError):
                    raise ValueError(f"Priority value for index {i} must be an integer, got {p!r}")
                if p_val < 0 or p_val > 7:
                    raise ValueError(f"Priority {p_val} out of valid range [0, 7]")
                parsed_priorities[i] = p_val
        elif isinstance(priorities, dict):
            for k, v in priorities.items():
                try:
                    idx = int(k)
                except (ValueError, TypeError):
                    raise ValueError(f"File index must be an integer, got {k!r}")
                if idx < 0:
                    raise ValueError(f"File index cannot be negative, got {idx}")
                try:
                    p_val = int(v)
                except (ValueError, TypeError):
                    raise ValueError(f"Priority value for index {idx} must be an integer, got {v!r}")
                if p_val < 0 or p_val > 7:
                    raise ValueError(f"Priority {p_val} out of valid range [0, 7]")
                parsed_priorities[idx] = p_val

        if self._torrent_info:
            num = self._torrent_info.num_files()
            for idx in parsed_priorities.keys():
                if idx >= num:
                    raise ValueError(f"File index {idx} out of range [0, {num - 1}]")

        self.file_priorities.update(parsed_priorities)

        if self._torrent_info:
            num = self._torrent_info.num_files()
            prio_list = [self.file_priorities.get(i, 4) for i in range(num)]
            for ip, h in list(self.handles):
                try:
                    h.prioritize_files(prio_list)
                except Exception as e:
                    print(f"[TORRENT] Error updating file priorities on handle {ip}: {e}")

            # Recalculate selected_size without overwriting total_size
            self.selected_size = sum(
                self._torrent_info.files().file_size(i)
                for i in range(num)
                if self.file_priorities.get(i, 4) > 0
            )

        return {
            "status": "success",
            "file_priorities": self.file_priorities,
            "torrent_total_size": self.torrent_total_size,
            "total_size": self.total_size,
            "selected_size": self.selected_size,
        }

    async def select_all(self, priority: int = 4) -> dict:
        """Select all files with given priority (1-7)."""
        if priority < 1 or priority > 7:
            raise ValueError(f"Priority {priority} out of valid range [1, 7]")
        if not self._torrent_info:
            raise ValueError("Torrent metadata not yet available")
        num = self._torrent_info.num_files()
        return await self.set_file_priorities({i: priority for i in range(num)})

    async def deselect_all(self) -> dict:
        """Deselect all files (priority 0)."""
        if not self._torrent_info:
            raise ValueError("Torrent metadata not yet available")
        num = self._torrent_info.num_files()
        return await self.set_file_priorities({i: 0 for i in range(num)})

    def _is_in_directory(self, file_path: str, dir_path: str) -> bool:
        f_norm = file_path.replace("\\", "/").strip("/").lower()
        d_norm = dir_path.replace("\\", "/").strip("/").lower()
        if not d_norm:
            return True
        parts = f_norm.split("/")
        d_parts = d_norm.split("/")
        if len(d_parts) >= len(parts):
            return False
        for i in range(len(parts) - len(d_parts)):
            if parts[i:i + len(d_parts)] == d_parts:
                return True
        return False

    async def select_directory(self, dir_path: str, priority: int = 4) -> dict:
        """Select all files under dir_path with given priority."""
        if priority < 1 or priority > 7:
            raise ValueError(f"Priority {priority} out of valid range [1, 7]")
        if not self._torrent_info:
            raise ValueError("Torrent metadata not yet available")
        matching = {}
        ti = self._torrent_info
        for i in range(ti.num_files()):
            f_path = ti.files().file_path(i)
            if self._is_in_directory(f_path, dir_path):
                matching[i] = priority
        if not matching:
            return {"status": "no_files_matched", "dir_path": dir_path, "file_priorities": self.file_priorities}
        return await self.set_file_priorities(matching)

    async def deselect_directory(self, dir_path: str) -> dict:
        """Deselect all files under dir_path."""
        if not self._torrent_info:
            raise ValueError("Torrent metadata not yet available")
        matching = {}
        ti = self._torrent_info
        for i in range(ti.num_files()):
            f_path = ti.files().file_path(i)
            if self._is_in_directory(f_path, dir_path):
                matching[i] = 0
        if not matching:
            return {"status": "no_files_matched", "dir_path": dir_path, "file_priorities": self.file_priorities}
        return await self.set_file_priorities(matching)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "type": "torrent",
            "progress": self.progress,
            "speed_combined": self.speed_combined,
            "speeds": self.speeds,
            "peers_per_interface": self.peers_per_interface,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "status": self.status,
            "expected_size": self.selected_size if self.selected_size > 0 else self.total_size,
            "total_downloaded": self.selected_downloaded,
            "torrent_total_size": self.torrent_total_size,
            "total_size": self.total_size,
            "selected_size": self.selected_size,
            "selected_downloaded": self.selected_downloaded,
            "downloaded": self.downloaded,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "interface_ips": self.interface_ips,
            "magnet_uri": self.magnet_uri,
            "bandwidth_limits": self.bandwidth_limits,
            "boosted": self.boosted,
            "file_priorities": self.file_priorities,
            "files": self.get_files() if self._torrent_info else [],
        }

    def _extract_name(self, magnet_uri: str) -> str:
        """Try to extract display name (&dn=) from magnet URI or filename from path."""
        try:
            if "?" in magnet_uri and magnet_uri.startswith("magnet:"):
                import urllib.parse
                qs = magnet_uri.split("?", 1)[1]
                params = urllib.parse.parse_qs(qs)
                names = params.get("dn", [])
                if names:
                    return names[0]
            elif magnet_uri.endswith(".torrent"):
                from pathlib import Path
                return Path(magnet_uri).stem
        except:
            pass
        return "torrent"

    async def add_interface(self, ip: str, interface_name: str = "") -> dict:
        if any(s_ip == ip for s_ip, _ in self.sessions):
            return {"status": "already_exists"}
        if self._torrent_info is None:
            # Queue it — will be added once metadata arrives
            if ip not in self.interface_ips:
                self.interface_ips.append(ip)
                self.speeds[ip] = 0
                self.peers_per_interface[ip] = 0
            return {"status": "queued_for_metadata"}

        print(f"[TORRENT] Dynamically adding interface {ip}")
        ses = lt.session(_make_settings(ip))
        _bootstrap_dht(ses)
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(self._torrent_info)
        atp.save_path = self.output_path
        atp.storage_mode = lt.storage_mode_t.storage_mode_sparse
        if self.file_priorities:
            num = self._torrent_info.num_files()
            atp.file_priorities = [self.file_priorities.get(i, 4) for i in range(num)]
        h = ses.add_torrent(atp)
        _add_trackers(h)

        self.sessions.append((ip, ses))
        self.handles.append((ip, h))
        if ip not in self.interface_ips:
            self.interface_ips.append(ip)
        self.speeds[ip] = 0
        self.peers_per_interface[ip] = 0
        return {"status": "added", "ip": ip}

    async def remove_interface(self, ip: str) -> dict:
        idx = next((i for i, (s_ip, _) in enumerate(self.sessions) if s_ip == ip), -1)
        if idx == -1:
            # May not have session yet (queued during metadata) — just remove from list
            if ip in self.interface_ips:
                self.interface_ips.remove(ip)
            return {"status": "removed", "ip": ip}

        print(f"[TORRENT] Dynamically removing interface {ip}")
        self.sessions.pop(idx)
        h_idx = next((i for i, (h_ip, _) in enumerate(self.handles) if h_ip == ip), -1)
        if h_idx != -1:
            self.handles.pop(h_idx)
        self.speeds.pop(ip, None)
        self.peers_per_interface.pop(ip, None)
        if ip in self.interface_ips:
            self.interface_ips.remove(ip)
        return {"status": "removed", "ip": ip}

    async def cancel(self):
        print(f"[TORRENT] Cancelling job {self.job_id}")
        self._running = False
        self._selection_ready_event.set()
        self.status = "failed"
        self.error = "User cancelled"
        self.finished_at = time.time()
        return {"status": "cancelled", "job_id": self.job_id}

    async def pause(self) -> dict:
        print(f"[TORRENT] Pausing job {self.job_id}")
        self.status = "paused"
        for ip, h in list(self.handles):
            try:
                h.pause()
            except Exception as e:
                print(f"[TORRENT] Pause handle error: {e}")
        return {"status": "paused"}

    async def resume(self) -> dict:
        print(f"[TORRENT] Resuming job {self.job_id}")
        self.wait_for_selection = False
        self._selection_ready_event.set()
        self.status = "downloading"
        for ip, h in list(self.handles):
            try:
                h.resume()
            except Exception as e:
                print(f"[TORRENT] Resume handle error: {e}")
        return {"status": "resumed"}

    async def toggle_boost(self, active_interfaces: List[dict] = None) -> dict:
        self.boosted = not getattr(self, "boosted", False)
        
        if self.boosted:
            # 1. Add all active interfaces if not already added
            if active_interfaces:
                for iface in active_interfaces:
                    ip = iface["ip_address"]
                    if ip not in self.interface_ips:
                        try:
                            await self.add_interface(ip, iface["name"])
                        except Exception as e:
                            print(f"[TORRENT BOOST] Failed to add interface {ip}: {e}")
            
            # 2. Increase speed and peer limit
            for ip, ses in self.sessions:
                try:
                    settings = ses.get_settings()
                    settings["num_want"] = 600
                    settings["connection_speed"] = 100
                    ses.apply_settings(settings)
                except Exception as e:
                    print(f"[TORRENT BOOST] Failed to increase settings: {e}")
        else:
            # Revert peer limit to standard
            for ip, ses in self.sessions:
                try:
                    settings = ses.get_settings()
                    settings["num_want"] = 300
                    settings["connection_speed"] = 50
                    ses.apply_settings(settings)
                except Exception as e:
                    print(f"[TORRENT BOOST] Failed to reset settings: {e}")
                    
        return {"status": "success", "boosted": self.boosted}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_torrent_download(
    magnet_uri: str,
    output_path: str,
    interface_ips: List[str],
    bandwidth_limits: Optional[dict] = None,
    job_id: Optional[str] = None,
    resume_data: Optional[dict] = None,
    file_priorities: Optional[Dict[int, int] | List[int]] = None,
    wait_for_selection: bool = False,
    paused: bool = False,
) -> TorrentJob:
    # Normalize local torrent file path if it is a file URI or base64 data, or download remote torrent files
    _init_lt()
    normalized_uri = magnet_uri.strip()
    if normalized_uri.startswith("file:///"):
        normalized_uri = normalized_uri[8:]
    elif normalized_uri.startswith("file://"):
        normalized_uri = normalized_uri[7:]
    elif normalized_uri.startswith("base64:"):
        try:
            import base64
            import tempfile
            parts = normalized_uri.split(":", 2)
            filename = parts[1]
            b64_data = parts[2]
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, filename)
            with open(temp_path, "wb") as temp_file:
                temp_file.write(base64.b64decode(b64_data))
            normalized_uri = temp_path
        except Exception as e:
            print(f"[TORRENT] Failed to decode base64 torrent: {e}")
    elif normalized_uri.startswith(("http://", "https://")) and (normalized_uri.endswith(".torrent") or ".torrent" in normalized_uri.split("?")[0]):
        try:
            import requests
            import tempfile
            r = requests.get(normalized_uri, timeout=15, verify=False)
            r.raise_for_status()
            filename = normalized_uri.split("/")[-1].split("?")[0] or "download.torrent"
            if not filename.endswith(".torrent"):
                filename += ".torrent"
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, filename)
            with open(temp_path, "wb") as temp_file:
                temp_file.write(r.content)
            normalized_uri = temp_path
        except Exception as e:
            print(f"[TORRENT] Failed to download remote torrent file: {e}")
    
    import urllib.parse
    normalized_uri = urllib.parse.unquote(normalized_uri)
    if normalized_uri.endswith(".torrent") or os.path.exists(normalized_uri):
        normalized_uri = os.path.normpath(normalized_uri)
        
    magnet_uri = normalized_uri

    # 1. Prevent duplicate jobs if the exact same torrent is already active/paused
    if not job_id:
        for existing_job in active_torrents.values():
            if existing_job.magnet_uri == magnet_uri and existing_job.status not in ("completed", "failed"):
                if existing_job.status in ("paused", "waiting_reconnect"):
                    await existing_job.resume()
                return existing_job

    # 2. Path collision handling — only for NEW downloads, not for loaded resumes
    final_path = _sanitize_path(output_path)   # strip Windows-illegal chars (colons etc.)
    if not job_id:
        active_paths = [j.output_path for j in active_torrents.values() if j.status not in ("completed", "failed")]
        if final_path in active_paths:
            counter = 1
            while f"{final_path}({counter})" in active_paths:
                counter += 1
            final_path = f"{final_path}({counter})"

    job = TorrentJob(
        magnet_uri,
        final_path,
        interface_ips,
        job_id=job_id,
        bandwidth_limits=bandwidth_limits,
        resume_data=resume_data,
        file_priorities=file_priorities,
        wait_for_selection=wait_for_selection,
        paused=paused,
    )
    active_torrents[job.job_id] = job
    try:
        Path(final_path).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[TORRENT] Directory error: {e}")
    asyncio.create_task(_run_torrent(job, bandwidth_limits or {}))
    return job


def inspect_torrent(torrent_path_or_uri: str) -> dict:
    """Inspect a .torrent file or local path to extract file list and metadata before downloading."""
    _init_lt()
    path = torrent_path_or_uri.strip()
    if path.startswith("file:///"):
        path = path[8:]
    elif path.startswith("file://"):
        path = path[7:]
    
    ti = lt.torrent_info(path)
    files = []
    for i in range(ti.num_files()):
        files.append({
            "index": i,
            "path": ti.files().file_path(i),
            "size": ti.files().file_size(i),
            "priority": 4,
            "wanted": True,
        })
    return {
        "name": ti.name(),
        "total_size": ti.total_size(),
        "num_files": ti.num_files(),
        "piece_length": ti.piece_length(),
        "num_pieces": ti.num_pieces(),
        "files": files,
    }


# ---------------------------------------------------------------------------
# Internal: two-phase engine
# ---------------------------------------------------------------------------

async def _run_torrent(job: TorrentJob, bandwidth_limits: dict):
    # ── Phase 1: metadata with unbound session ─────────────────────────────
    is_local_file = job.magnet_uri.endswith(".torrent") and Path(job.magnet_uri).exists()
    
    if not is_local_file:
        job.status = "fetching_metadata"
        print("[TORRENT] Phase 1: fetching metadata with unbound session…")

        meta_ses = lt.session(_make_settings(ip=None))
        _load_dht_state(meta_ses)  # warm DHT routing table before bootstrap calls
        _bootstrap_dht(meta_ses)   # explicitly ping each router so routing table populates fast

        job._meta_session = meta_ses

        params = lt.parse_magnet_uri(job.magnet_uri)
        params.save_path = job.output_path
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        # Ensure zero payload bytes download before selection
        params.flags |= lt.torrent_flags.upload_mode

        meta_handle = meta_ses.add_torrent(params)
        job._meta_handle = meta_handle
        _add_trackers(meta_handle)
        # Force immediate announce to all trackers — without this, libtorrent
        # may wait up to 30 minutes for the first announce cycle.
        try:
            meta_handle.force_reannounce()
        except Exception as e:
            print(f"[TORRENT] force_reannounce error (harmless): {e}")

        METADATA_TIMEOUT = 180  # 3 min — longer timeout for cold DHT + ISP UDP blocks
        start = time.time()

        last_log = 0.0
        while job._running:
            await asyncio.sleep(0.1)
            elapsed = time.time() - start

            # Pop and log meta session alerts
            try:
                alerts = meta_ses.pop_alerts()
                for alert in alerts:
                    print(f"[TORRENT META ALERT] {alert.message()}")
            except Exception as e:
                print(f"[TORRENT] Error popping meta alerts: {e}")

            try:
                s = meta_handle.status()
            except Exception as e:
                print(f"[TORRENT] meta handle error: {e}")
                continue

            if s.has_metadata:
                print(f"[TORRENT] Metadata received after {elapsed:.0f}s!")
                job._torrent_info = meta_handle.torrent_file()
                _save_dht_state(meta_ses)  # save DHT for next time — faster bootstrap
                try:
                    meta_ses.remove_torrent(meta_handle)
                except Exception:
                    pass
                break

            if elapsed > METADATA_TIMEOUT:
                _save_dht_state(meta_ses)  # save even on timeout — DHT table is still valuable
                job.status = "failed"
                job.error = (
                    "Could not fetch torrent metadata. "
                    "Try a .torrent file instead of a magnet link."
                )
                job._running = False
                job.finished_at = time.time()
                print(f"[TORRENT] Metadata timeout. state={s.state} peers={s.num_peers}")
                return

            if elapsed - last_log >= 10:
                last_log = elapsed
                dht_running = getattr(meta_ses, "is_dht_running", lambda: True)()
                print(
                    f"[TORRENT] Waiting for metadata… {elapsed:.0f}s "
                    f"state={s.state} peers={s.num_peers} "
                    f"dht_running={dht_running}"
                )
                for t in meta_handle.trackers():
                    last_err = getattr(t, 'last_error', None)
                    err_str = str(last_err) if last_err else "none"
                    msg = t.get("message", "") or "no response yet"
                    print(f"  tracker: {t['url']}  msg={msg!r}  last_error={err_str}")

        if not job._running:
            return
    else:
        # Load local .torrent file
        try:
            job._torrent_info = lt.torrent_info(job.magnet_uri)
            print(f"[TORRENT] Loaded local torrent file: {job.magnet_uri}")
        except Exception as e:
            job.status = "failed"
            job.error = f"Invalid .torrent file: {e}"
            job._running = False
            job.finished_at = time.time()
            return

    ti = job._torrent_info
    num = ti.num_files()
    job.torrent_total_size = ti.total_size()
    job.total_size = ti.total_size()
    if job.file_priorities:
        job.selected_size = sum(ti.files().file_size(i) for i in range(num) if job.file_priorities.get(i, 4) > 0)
    else:
        job.selected_size = ti.total_size()

    # Magnet flow: if wait_for_selection or paused, pause and wait for resume/selection
    if job.wait_for_selection or job.status == "paused":
        job.status = "paused"
        print(f"[TORRENT] Metadata ready. Paused waiting for file selection.")
        await job._selection_ready_event.wait()
        if not job._running:
            return

    # ── Phase 2: bound sessions per interface ──────────────────────────────
    print("[TORRENT] Phase 2: starting per-interface bound sessions…")
    job.status = "downloading"

    for ip in list(job.interface_ips):
        try:
            ses = lt.session(_make_settings(ip=ip))
            _bootstrap_dht(ses)
            atp = lt.add_torrent_params()
            atp.ti = lt.torrent_info(ti)
            atp.save_path = job.output_path
            atp.storage_mode = lt.storage_mode_t.storage_mode_sparse
            if job.file_priorities:
                atp.file_priorities = [job.file_priorities.get(i, 4) for i in range(num)]

            limit = bandwidth_limits.get(ip)
            if limit:
                ses.set_upload_rate_limit(0)
                ses.set_download_rate_limit(int(limit * 1024))

            h = ses.add_torrent(atp)
            _add_trackers(h)
            try:
                h.force_reannounce()
            except Exception as e:
                print(f"[TORRENT] force_reannounce error on Phase 2 (harmless): {e}")
 
            job.sessions.append((ip, ses))
            job.handles.append((ip, h))
            print(f"[TORRENT] Session started for {ip}")
        except Exception as e:
            print(f"[TORRENT] Error starting session for {ip}: {e}")

    await _monitor_download(job)


def _is_piece_wanted(ti: lt.torrent_info, piece_idx: int, file_priorities: Dict[int, int]) -> bool:
    if not file_priorities:
        return True
    p_len = ti.piece_length()
    p_start = piece_idx * p_len
    p_end = min(p_start + p_len - 1, ti.total_size() - 1)
    offset = 0
    for f_idx in range(ti.num_files()):
        f_size = ti.files().file_size(f_idx)
        f_start = offset
        f_end = offset + f_size - 1
        offset += f_size
        if not (p_end < f_start or p_start > f_end):
            if file_priorities.get(f_idx, 4) > 0:
                return True
    return False


async def _monitor_download(job: TorrentJob):
    no_peers_since = time.time()
    warned = False

    FINISHED_STATE_VALS = {
        int(lt.torrent_status.states.seeding),
        int(lt.torrent_status.states.finished),
    }

    from interfaces import get_active_interfaces

    while job._running:
        await asyncio.sleep(1)

        if job.status == "paused":
            job.speed_combined = 0
            for ip in list(job.speeds.keys()):
                job.speeds[ip] = 0
            continue

        # 1. Pop and log alerts from active sessions
        for ip, ses in list(job.sessions):
            try:
                alerts = ses.pop_alerts()
                for alert in alerts:
                    print(f"[TORRENT ALERT] [{ip}] {alert.message()}")
            except Exception as e:
                print(f"[TORRENT] Error popping alerts for {ip}: {e}")

        # 2. Check if bound interfaces are still active
        active_ips = {iface.ip_address for iface in get_active_interfaces()}
        for ip, h in list(job.handles):
            if ip not in active_ips and not ip.startswith("127.") and ip != "::1":
                print(f"[TORRENT] WARNING: Bound interface {ip} is no longer active. Dropping session.")
                # Find and clean up the session
                ses_idx = next((i for i, (s_ip, _) in enumerate(job.sessions) if s_ip == ip), -1)
                if ses_idx != -1:
                    _, ses = job.sessions.pop(ses_idx)
                    try:
                        ses.remove_torrent(h)
                    except Exception as e:
                        print(f"[TORRENT] Error removing torrent from session {ip}: {e}")
                # Remove handle
                h_idx = next((i for i, (h_ip, _) in enumerate(job.handles) if h_ip == ip), -1)
                if h_idx != -1:
                    job.handles.pop(h_idx)
                
                # Remove from stats mapping
                job.speeds.pop(ip, None)
                job.peers_per_interface.pop(ip, None)
                if ip in job.interface_ips:
                    job.interface_ips.remove(ip)

        total_speed = 0
        # Seed from last-known values so a transient error or empty poll cycle
        # can never zero-out previously reported progress/downloaded bytes.
        max_progress = job.progress
        max_selected_downloaded = job.selected_downloaded
        max_downloaded = job.downloaded
        total_seeders = 0
        total_peers = 0
        all_finished = True

        ti = job._torrent_info
        num_pieces = ti.num_pieces() if ti else 0
        union_pieces = [False] * num_pieces if num_pieces > 0 else []
        has_union = False

        for ip, h in list(job.handles):
            try:
                s = h.status()
            except Exception:
                all_finished = False
                continue

            job.speeds[ip] = s.download_rate
            total_speed += s.download_rate
            job.peers_per_interface[ip] = s.num_peers
            total_peers += s.num_peers
            total_seeders = max(total_seeders, s.num_seeds)
            max_selected_downloaded = max(max_selected_downloaded, s.total_wanted_done)
            max_downloaded = max(max_downloaded, s.total_done)

            if num_pieces > 0:
                try:
                    p_vec = s.pieces
                    if len(p_vec) == num_pieces:
                        has_union = True
                        for idx in range(num_pieces):
                            if p_vec[idx]:
                                union_pieces[idx] = True
                except Exception:
                    pass

            is_finished = (
                getattr(s, "is_finished", False)
                or getattr(s, "is_seeding", False)
                or int(s.state) in FINISHED_STATE_VALS
                or (s.total_wanted > 0 and s.total_wanted_done >= s.total_wanted)
            )
            if not is_finished:
                all_finished = False

        # Multi-handle progress merge: calculate piece union across all active interfaces
        if has_union and ti and num_pieces > 0:
            p_len = ti.piece_length()
            t_size = ti.total_size()
            merged_wanted_bytes = 0
            for idx in range(num_pieces):
                if union_pieces[idx] and _is_piece_wanted(ti, idx, job.file_priorities):
                    p_start = idx * p_len
                    p_bytes = min(p_len, t_size - p_start)
                    merged_wanted_bytes += p_bytes
            max_selected_downloaded = max(max_selected_downloaded, merged_wanted_bytes)

        target_size = job.selected_size if job.selected_size > 0 else job.total_size
        if target_size > 0:
            calc_prog = min(1.0, max_selected_downloaded / target_size)
            max_progress = max(max_progress, calc_prog)
            if max_selected_downloaded >= target_size:
                all_finished = True
            else:
                all_finished = False
        elif 's' in locals():
            max_progress = max(max_progress, s.progress)

        job.speed_combined = total_speed
        job.progress = max_progress
        job.selected_downloaded = max_selected_downloaded
        job.downloaded = max_downloaded
        job.seeders = total_seeders
        job.leechers = max(0, total_peers - total_seeders)

        if total_peers > 0:
            no_peers_since = time.time()
        elif not warned and (time.time() - no_peers_since) > 30:
            print("[TORRENT] No peers for 30s — check Windows Firewall")
            warned = True

        if all_finished and (job.selected_size > 0 or job.total_size > 0) and len(job.handles) > 0:
            job.status = "completed"
            job._running = False
            job.finished_at = time.time()
            print(f"[TORRENT] Download completed! {job.selected_downloaded / 1e6:.1f} MB")
            break