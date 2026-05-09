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
        self.status = "downloading"
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
        }

async def start_torrent_download(magnet_uri: str, output_path: str, interface_ips: list):
    job = TorrentJob(magnet_uri, output_path, interface_ips)
    active_torrents[job.job_id] = job

    for ip in interface_ips:
        settings = {
            'user_agent': 'burst/0.2',
            'listen_interfaces': f'{ip}:6881',
            'outgoing_interfaces': ip,
        }
        ses = lt.session(settings)
        
        params = lt.parse_magnet_uri(magnet_uri)
        params.save_path = output_path
        h = ses.add_torrent(params)
        
        job.sessions.append((ip, ses))
        job.handles.append((ip, h))

    asyncio.create_task(_monitor_torrent(job))
    return job

async def _monitor_torrent(job: TorrentJob):
    # Wait for metadata
    while job._running:
        all_metadata = [h.has_metadata() for ip, h in job.handles]
        if any(all_metadata):
            break
        await asyncio.sleep(1)

    while job._running:
        total_speed = 0
        max_progress = 0
        max_downloaded = 0
        total_seeders = 0
        total_leechers = 0
        total_size = 0
        
        all_finished = True

        for ip, h in job.handles:
            s = h.status()
            job.speeds[ip] = s.download_rate
            total_speed += s.download_rate
            job.peers_per_interface[ip] = s.num_peers
            total_seeders = max(total_seeders, s.num_seeds)
            total_leechers = max(total_leechers, s.num_peers - s.num_seeds)
            max_progress = max(max_progress, s.progress)
            max_downloaded = max(max_downloaded, s.total_wanted_done)
            total_size = max(total_size, s.total_wanted)
            if not s.is_finished:
                all_finished = False

        job.speed_combined = total_speed
        job.progress = max_progress
        job.downloaded = max_downloaded
        job.seeders = total_seeders
        job.leechers = total_leechers
        job.total_size = total_size

        if all_finished:
            job.status = "completed"
            job._running = False
            break
            
        await asyncio.sleep(1)

def get_torrent_status(job_id: str):
    job = active_torrents.get(job_id)
    if job:
        return job.to_dict()
    return None
