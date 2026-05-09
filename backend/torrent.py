import asyncio
import libtorrent as lt
import time
import uuid

# Active torrent jobs mapping
active_torrents = {}

class TorrentJob:
    def __init__(self, magnet_uri: str, output_path: str, interface_ips: list):
        self.job_id = str(uuid.uuid4())
        self.magnet_uri = magnet_uri
        self.output_path = output_path
        self.interface_ips = interface_ips
        self.status = "fetching_metadata"
        self.progress = 0.0
        self.total_size = 0
        self.downloaded = 0
        self.speed_combined = 0
        self.speeds = {ip: 0 for ip in interface_ips}
        self.peers_per_interface = {ip: 0 for ip in interface_ips}
        self.seeders = 0
        self.leechers = 0
        self.sessions = []
        self.handles = []
        self._running = True
        self.started_at = time.time()
        self.finished_at = None
        self.error = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
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
        }

async def start_torrent_download(magnet_uri: str, output_path: str, interface_ips: list, bandwidth_limits: dict = None):
    job = TorrentJob(magnet_uri, output_path, interface_ips)
    active_torrents[job.job_id] = job

    for ip in interface_ips:
        print(f"[TORRENT] Starting session for interface {ip}")
        settings = lt.settings_pack()
        settings['user_agent'] = 'burst/0.2'
        settings['listen_interfaces'] = f'{ip}:6881'
        settings['outgoing_interfaces'] = ip
        
        ses = lt.session(settings)
        
        # Bootstrap DHT with well-known routers
        ses.add_dht_router("router.bittorrent.com", 6881)
        ses.add_dht_router("router.utorrent.com", 6881)
        ses.add_dht_router("dht.transmissionbt.com", 6881)
        ses.start_dht()
        print(f"[TORRENT] DHT started for {ip}")
        
        print(f"[TORRENT] Adding magnet: {magnet_uri[:80]}...")
        params = lt.parse_magnet_uri(magnet_uri)
        params.save_path = output_path
        h = ses.add_torrent(params)
        
        job.sessions.append((ip, ses))
        job.handles.append((ip, h))

    asyncio.create_task(_monitor_torrent(job))
    return job

async def _monitor_torrent(job: TorrentJob):
    # Wait for metadata with timeout
    metadata_timeout = 60  # seconds
    start_wait = time.time()
    
    while job._running:
        has_any_metadata = False
        for ip, h in job.handles:
            try:
                if h.has_metadata():
                    has_any_metadata = True
                    break
            except Exception:
                pass
        
        if has_any_metadata:
            print(f"[TORRENT] Metadata received!")
            break
        
        elapsed = time.time() - start_wait
        if elapsed > metadata_timeout:
            job.status = "failed"
            job.error = "Could not fetch torrent metadata after 60s — check firewall or try another magnet link"
            job._running = False
            job.finished_at = time.time()
            print(f"[TORRENT] Metadata timeout after {metadata_timeout}s")
            return
        
        # Log every 5 seconds
        if int(elapsed) % 5 == 0 and int(elapsed) > 0:
            states = []
            for ip, h in job.handles:
                try:
                    s = h.status()
                    states.append(f"{ip}: state={s.state}, peers={s.num_peers}")
                except Exception as e:
                    states.append(f"{ip}: error={e}")
            print(f"[TORRENT] Waiting for metadata... {int(elapsed)}s elapsed — {', '.join(states)}")
        
        await asyncio.sleep(1)
    
    if not job._running:
        return
    
    job.status = "downloading"
    print(f"[TORRENT] Download started")
    
    no_peers_start = time.time()
    warned_firewall = False

    while job._running:
        total_speed = 0
        max_progress = 0
        max_downloaded = 0
        total_seeders = 0
        total_leechers = 0
        total_size = 0
        total_peers = 0
        
        all_finished = True

        for ip, h in job.handles:
            try:
                s = h.status()
                job.speeds[ip] = s.download_rate
                total_speed += s.download_rate
                job.peers_per_interface[ip] = s.num_peers
                total_peers += s.num_peers
                total_seeders = max(total_seeders, s.num_seeds)
                total_leechers = max(total_leechers, s.num_peers - s.num_seeds)
                max_progress = max(max_progress, s.progress)
                max_downloaded = max(max_downloaded, s.total_wanted_done)
                total_size = max(total_size, s.total_wanted)
                if not s.is_finished:
                    all_finished = False
            except Exception as e:
                print(f"[TORRENT] Error reading status for {ip}: {e}")

        job.speed_combined = total_speed
        job.progress = max_progress
        job.downloaded = max_downloaded
        job.seeders = total_seeders
        job.leechers = total_leechers
        job.total_size = total_size

        # Firewall warning if no peers after 30s
        if total_peers > 0:
            no_peers_start = time.time()
        elif not warned_firewall and (time.time() - no_peers_start) > 30:
            print("[TORRENT] If no peers connect within 30s, check Windows Firewall — allow Python through firewall")
            warned_firewall = True

        if all_finished and total_size > 0:
            job.status = "completed"
            job._running = False
            job.finished_at = time.time()
            print(f"[TORRENT] Download completed!")
            break
            
        await asyncio.sleep(1)

def get_torrent_status(job_id: str):
    job = active_torrents.get(job_id)
    if job:
        return job.to_dict()
    return None
