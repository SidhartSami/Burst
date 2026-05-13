import asyncio
import libtorrent as lt
import time
import uuid
import socket
from pathlib import Path
from typing import Dict, List, Optional

active_torrents: Dict[str, "TorrentJob"] = {}

DHT_STATE_FILE = Path("dht_state.dat")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

def _make_settings(ip: Optional[str] = None) -> dict:
    base = {
        "user_agent": "burst/0.2",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_natpmp": True,
        "enable_upnp": True,
        "dht_bootstrap_nodes": (
            "router.bittorrent.com:6881,"
            "router.utorrent.com:6881,"
            "dht.transmissionbt.com:6881,"
            "dht.aelitis.com:6881"
        ),
        "connection_speed": 20,
        "num_want": 200,
        "request_timeout": 10,
        "peer_timeout": 20,
        "min_reconnect_time": 3,
        "min_announce_interval": 5,
        "announce_to_all_tiers": True,
        "announce_to_all_trackers": True,
    }
    if ip:
        base["listen_interfaces"] = f"{ip}:{_find_free_port()}"
        base["outgoing_interfaces"] = ip
    else:
        base["listen_interfaces"] = f"0.0.0.0:{_find_free_port()}"
    return base


def _save_dht_state(ses: lt.session):
    """Persist DHT routing table so next session bootstraps instantly."""
    try:
        state = ses.save_state()
        DHT_STATE_FILE.write_bytes(lt.bencode(state))
    except Exception as e:
        print(f"[TORRENT] DHT state save error: {e}")


def _load_dht_state(ses: lt.session):
    """Load persisted DHT routing table if available."""
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
    for tier, url in enumerate(TRACKERS):
        handle.add_tracker({"url": url, "tier": tier})


# ---------------------------------------------------------------------------
# TorrentJob
# ---------------------------------------------------------------------------

class TorrentJob:
    def __init__(self, magnet_uri: str, output_path: str, interface_ips: List[str]):
        self.job_id = str(uuid.uuid4())
        self.magnet_uri = magnet_uri
        self.output_path = output_path
        self.interface_ips = list(interface_ips)
        self.filename = self._extract_name(magnet_uri)

        self.status = "fetching_metadata"
        self.progress = 0.0
        self.total_size = 0
        self.downloaded = 0
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
            "expected_size": self.total_size,
            "total_downloaded": self.downloaded,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "interface_ips": self.interface_ips,
        }

    def _extract_name(self, magnet_uri: str) -> str:
        """Try to extract display name (&dn=) from magnet URI."""
        try:
            if "?" in magnet_uri:
                import urllib.parse
                qs = magnet_uri.split("?", 1)[1]
                params = urllib.parse.parse_qs(qs)
                names = params.get("dn", [])
                if names:
                    return names[0]
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
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(self._torrent_info)
        atp.save_path = self.output_path
        atp.storage_mode = lt.storage_mode_t.storage_mode_sparse
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
        self.status = "failed"
        self.error = "User cancelled"
        self.finished_at = time.time()
        return {"status": "cancelled", "job_id": self.job_id}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_torrent_download(
    magnet_uri: str,
    output_path: str,
    interface_ips: List[str],
    bandwidth_limits: Optional[dict] = None,
) -> TorrentJob:
    # Path collision handling
    final_path = output_path
    active_paths = [j.output_path for j in active_torrents.values() if j.status not in ("completed", "failed")]
    if final_path in active_paths:
        p = Path(final_path)
        # For torrents, the output_path is often a directory, so we handle it slightly differently if needed
        # but the logic remains same for simple collision
        counter = 1
        while f"{final_path}({counter})" in active_paths:
            counter += 1
        final_path = f"{final_path}({counter})"

    job = TorrentJob(magnet_uri, final_path, interface_ips)
    active_torrents[job.job_id] = job
    try:
        Path(final_path).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[TORRENT] Directory error: {e}")
    asyncio.create_task(_run_torrent(job, bandwidth_limits or {}))
    return job


# ---------------------------------------------------------------------------
# Internal: two-phase engine
# ---------------------------------------------------------------------------

async def _run_torrent(job: TorrentJob, bandwidth_limits: dict):
    # ── Phase 1: metadata with unbound session ─────────────────────────────
    print("[TORRENT] Phase 1: fetching metadata with unbound session…")

    meta_ses = lt.session(_make_settings(ip=None))
    _load_dht_state(meta_ses)  # warm DHT from previous session

    meta_ses.add_dht_router("router.bittorrent.com", 6881)
    meta_ses.add_dht_router("router.utorrent.com", 6881)
    meta_ses.add_dht_router("dht.transmissionbt.com", 6881)

    job._meta_session = meta_ses

    params = lt.parse_magnet_uri(job.magnet_uri)
    params.save_path = job.output_path
    params.storage_mode = lt.storage_mode_t.storage_mode_sparse

    meta_handle = meta_ses.add_torrent(params)
    job._meta_handle = meta_handle
    _add_trackers(meta_handle)

    METADATA_TIMEOUT = 180  # 3 min — longer timeout for cold DHT + ISP UDP blocks
    start = time.time()

    while job._running:
        await asyncio.sleep(1)
        elapsed = time.time() - start

        try:
            s = meta_handle.status()
        except Exception as e:
            print(f"[TORRENT] meta handle error: {e}")
            continue

        if s.has_metadata:
            print(f"[TORRENT] Metadata received after {elapsed:.0f}s!")
            job._torrent_info = meta_handle.torrent_file()
            _save_dht_state(meta_ses)  # save DHT for next time — faster bootstrap
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

        if int(elapsed) % 10 == 0 and elapsed >= 10:
            dht_nodes = 0
            try:
                dht_nodes = meta_ses.dht_status().nodes
            except:
                pass
            print(
                f"[TORRENT] Waiting for metadata… {elapsed:.0f}s "
                f"state={s.state} peers={s.num_peers} dht_nodes={dht_nodes}"
            )
            for t in meta_handle.trackers()[:4]:
                msg = t.get("message", "") or "no response yet"
                print(f"  {t['url']}: {msg}")

    if not job._running:
        return

    # ── Phase 2: bound sessions per interface ──────────────────────────────
    print("[TORRENT] Phase 2: starting per-interface bound sessions…")
    job.status = "downloading"

    ti = job._torrent_info
    job.total_size = ti.total_size()

    for ip in list(job.interface_ips):
        try:
            ses = lt.session(_make_settings(ip=ip))
            atp = lt.add_torrent_params()
            atp.ti = lt.torrent_info(ti)
            atp.save_path = job.output_path
            atp.storage_mode = lt.storage_mode_t.storage_mode_sparse

            limit = bandwidth_limits.get(ip)
            if limit:
                ses.set_upload_rate_limit(0)
                ses.set_download_rate_limit(int(limit * 1024))

            h = ses.add_torrent(atp)
            _add_trackers(h)

            job.sessions.append((ip, ses))
            job.handles.append((ip, h))
            print(f"[TORRENT] Session started for {ip}")
        except Exception as e:
            print(f"[TORRENT] Error starting session for {ip}: {e}")

    await _monitor_download(job)


async def _monitor_download(job: TorrentJob):
    no_peers_since = time.time()
    warned = False

    FINISHED_STATE_VALS = {
        int(lt.torrent_status.states.seeding),
        int(lt.torrent_status.states.finished),
    }

    while job._running:
        await asyncio.sleep(1)

        total_speed = 0
        max_progress = 0.0
        max_downloaded = 0
        total_seeders = 0
        total_peers = 0
        all_finished = True

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
            max_progress = max(max_progress, s.progress)
            max_downloaded = max(max_downloaded, s.total_wanted_done)

            if int(s.state) not in FINISHED_STATE_VALS:
                all_finished = False

        job.speed_combined = total_speed
        job.progress = max_progress
        job.downloaded = max_downloaded
        job.seeders = total_seeders
        job.leechers = max(0, total_peers - total_seeders)

        if total_peers > 0:
            no_peers_since = time.time()
        elif not warned and (time.time() - no_peers_since) > 30:
            print("[TORRENT] No peers for 30s — check Windows Firewall")
            warned = True

        if all_finished and job.total_size > 0 and len(job.handles) > 0:
            job.status = "completed"
            job._running = False
            job.finished_at = time.time()
            print(f"[TORRENT] Download completed! {job.downloaded / 1e6:.1f} MB")
            break