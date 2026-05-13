"""Burst — FastAPI application with settings and download management."""
from __future__ import annotations

import asyncio
import time
import sys
import os

# Redirect stdout/stderr to devnull for --noconsole mode
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import os

from downloader import DownloadManager, analyze_url
from interfaces import get_active_interfaces_dict
from torrent import start_torrent_download, active_torrents
from speedtest import benchmark_interfaces
import config
from history_manager import load_history, save_to_history
from setup_utils import apply_firewall_rules
from contextlib import asynccontextmanager
import tkinter as tk
from tkinter import filedialog


class DownloadRequest(BaseModel):
    url: str = Field(min_length=5)
    output_path: str = Field(min_length=1)
    interface_ips: List[str]
    bandwidth_limits: Optional[Dict[str, int]] = None


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=5)
    interface_ip: str | None = None


class AddInterfacesRequest(BaseModel):
    interface_ips: List[str]

class AddInterfaceRequest(BaseModel):
    interface_ip: str

class RemoveInterfaceRequest(BaseModel):
    interface_ip: str

class TorrentStartRequest(BaseModel):
    magnet_uri: str
    output_path: str
    interface_ips: List[str]
    bandwidth_limits: Optional[Dict[str, int]] = None


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run firewall setup in a separate thread so it doesn't block the startup
    from threading import Thread
    Thread(target=apply_firewall_rules, daemon=True).start()
    
    # Start polling loop
    task = asyncio.create_task(interface_polling_loop())
    yield
    # Shutdown: Clean up if needed
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Burst API", version="0.2.0", lifespan=lifespan)
manager = DownloadManager()
active_sockets: Dict[str, Set[WebSocket]] = {}
event_bus: Set[WebSocket] = set()

async def broadcast_event(event_type: str, data: Any):
    """Notify all global event listeners."""
    message = {"type": event_type, "data": data}
    disconnected = set()
    for ws in event_bus:
        try:
            await ws.send_json(message)
        except:
            disconnected.add(ws)
    for ws in disconnected:
        event_bus.discard(ws)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _interfaces_by_ip() -> Dict[str, Dict[str, Any]]:
    return {str(item["ip_address"]): item for item in get_active_interfaces_dict()}


# ---------------------------------------------------------------------------
# Interface & speed endpoints
# ---------------------------------------------------------------------------
@app.get("/interfaces")
async def interfaces(benchmark: bool = True) -> Dict[str, Any]:
    base = get_active_interfaces_dict()
    if benchmark:
        speed_data = await benchmark_interfaces(base)
        by_ip = {str(item["ip_address"]): item for item in speed_data}
        for interface in base:
            matched = by_ip.get(str(interface["ip_address"]))
            interface["speed_mb_s"] = matched.get("speed_mb_s", 0.0) if matched else 0.0
            interface["speedtest_error"] = matched.get("error") if matched else None
    else:
        for interface in base:
            interface["speed_mb_s"] = 0.0
            interface["speedtest_error"] = None
    return {"interfaces": base, "count": len(base)}


@app.post("/speedtest")
async def speedtest() -> Dict[str, Any]:
    interfaces_data = get_active_interfaces_dict()
    results = await benchmark_interfaces(interfaces_data)
    return {"results": results}


# ---------------------------------------------------------------------------
# Analyze & download endpoints
# ---------------------------------------------------------------------------
@app.post("/analyze")
async def analyze(payload: AnalyzeRequest) -> Dict[str, Any]:
    try:
        result = await analyze_url(payload.url, payload.interface_ip)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/download")
async def start_download(payload: DownloadRequest) -> Dict[str, Any]:
    # Auto-detect download type
    is_torrent = payload.url.startswith("magnet:") or payload.url.lower().endswith(".torrent")
    
    if is_torrent:
        try:
            job = await start_torrent_download(payload.url, payload.output_path, payload.interface_ips, payload.bandwidth_limits)
            # Only broadcast if it's not an internal check (though pings usually aren't magnets)
            if "BURST_INTERNAL_CHECK" not in payload.url:
                await broadcast_event("new_job", {"job_id": job.job_id})
            return {"job_id": job.job_id, "status": job.status, "type": "torrent"}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Regular multi-interface downloader
    all_ifaces = _interfaces_by_ip()
    selected = [all_ifaces[ip] for ip in payload.interface_ips if ip in all_ifaces]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid interfaces selected")
    try:
        job = await manager.create_job(payload.url, payload.output_path, selected, payload.bandwidth_limits)
        
        # Don't notify UI about internal health checks
        if "BURST_INTERNAL_CHECK" not in payload.url:
            await broadcast_event("new_job", {"job_id": job.job_id})
            
        return {"job_id": job.job_id, "status": job.status, "type": "download"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/download/{job_id}")
async def get_download_status(job_id: str) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.post("/download/{job_id}/cancel")
async def cancel_download(job_id: str) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.is_cancelled = True
    return {"status": "cancelled"}


@app.post("/download/{job_id}/interfaces")
async def add_interfaces_to_job(job_id: str, payload: AddInterfacesRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    all_ifaces = _interfaces_by_ip()
    selected = [all_ifaces[ip] for ip in payload.interface_ips if ip in all_ifaces]
    results = []
    for iface in selected:
        try:
            result = await manager.add_interface(job_id, iface)
            print(f"[ADD_IFACE] {iface['ip_address']} -> {result}")
            results.append(result)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": len(selected), "results": results}


@app.get("/active-jobs")
async def get_active_jobs() -> Dict[str, Any]:
    """Returns a list of all active (not completed/failed) job IDs, excluding system pings."""
    regular_ids = [
        jid for jid, job in manager.jobs.items() 
        if job.status not in ("completed", "failed")
        and "BURST_INTERNAL_CHECK" not in job.url
    ]
    torrent_ids = [jid for jid, job in active_torrents.items() if job.status not in ("completed", "failed")]
    return {"job_ids": regular_ids + torrent_ids}


@app.get("/history")
async def get_history():
    # 1. Start with the persistent history from disk
    all_history = load_history()
    
    # 2. Get current session jobs (regular and torrents)
    current_jobs = [j.to_dict() for j in manager.jobs.values()]
    current_jobs += [j.to_dict() for j in active_torrents.values()]
    
    # Filter: Only completed or failed jobs, skip system checks
    finished_current = [
        j for j in current_jobs 
        if j["status"] in ("completed", "failed") 
        and j.get("filename") != "BURST_INTERNAL_CHECK"
        and "BURST_INTERNAL_CHECK" not in (j.get("url") or "")
    ]
    
    # 3. Merge them (disk history + current finished jobs)
    # Using job_id to avoid duplicates
    existing_ids = {item.get("job_id") for item in all_history}
    for j in finished_current:
        if j.get("job_id") not in existing_ids:
            all_history.append(j)
            save_to_history(j) # Persist it immediately if it's new
            
    # Sort by finished_at desc
    all_history.sort(key=lambda x: x.get("finished_at", 0) or 0, reverse=True)
    return {"history": all_history}


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------
@app.get("/settings")
async def get_settings() -> Dict[str, Any]:
    return {"settings": config.load_settings()}


@app.post("/settings")
async def update_settings(payload: SettingsUpdate) -> Dict[str, Any]:
    updated = config.save_settings(payload.settings)
    return {"settings": updated}


@app.post("/settings/reset")
async def reset_settings() -> Dict[str, Any]:
    return {"settings": config.reset_settings()}


@app.get("/select-path")
async def select_path():
    """Opens a native directory selection dialog and returns the selected path."""
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory()
        root.destroy()
        return {"path": path if path else None}
    except Exception as e:
        return {"path": None, "error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket for real-time progress & Interface Hotplug Polling
# ---------------------------------------------------------------------------
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    event_bus.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.discard(websocket)

@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str) -> None:
    if job_id == "interfaces":
        return await websocket_interfaces(websocket)
        
    await websocket.accept()
    active_sockets.setdefault(job_id, set()).add(websocket)
    try:
        while True:
            job = manager.get_job(job_id)
            if not job:
                tjob = active_torrents.get(job_id)
                if tjob:
                    await websocket.send_json(tjob.to_dict())
                    if tjob.status in {"completed", "failed"}:
                        break
                else:
                    await websocket.send_json({"error": "Job not found"})
                    break
            else:
                await websocket.send_json(job.to_dict())
                if job.status in {"completed", "failed"}:
                    break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    finally:
        active_sockets.get(job_id, set()).discard(websocket)

interfaces_ws_sockets: Set[WebSocket] = set()

async def websocket_interfaces(websocket: WebSocket) -> None:
    await websocket.accept()
    interfaces_ws_sockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        interfaces_ws_sockets.discard(websocket)

async def interface_polling_loop():
    last_interfaces = {}
    while True:
        try:
            current_interfaces = get_active_interfaces_dict()
            current_dict = {str(i["ip_address"]): i for i in current_interfaces}
            
            if last_interfaces:
                added = []
                removed = []
                for ip, i in current_dict.items():
                    if ip not in last_interfaces:
                        added.append(i)
                for ip, i in last_interfaces.items():
                    if ip not in current_dict:
                        removed.append(i)
                        
                for i in added:
                    ip = i["ip_address"]
                    msg = {"event": "interface_added", "interface": {"name": i["name"], "ip": ip, "type": i.get("interface_type", ""), "speed": 0}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
                for i in removed:
                    ip = i["ip_address"]
                    msg = {"event": "interface_removed", "interface": {"name": i["name"], "ip": ip}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
            
            last_interfaces = current_dict
        except Exception:
            pass
        await asyncio.sleep(3)

# startup/shutdown handled by lifespan decorator

@app.post("/download/{job_id}/add_interface")
async def add_single_interface_to_job(job_id: str, payload: AddInterfaceRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        # Check if it's a torrent job
        tjob = active_torrents.get(job_id)
        if not tjob:
            raise HTTPException(status_code=404, detail="Job not found")
        all_ifaces = get_active_interfaces_dict()
        iface = next((i for i in all_ifaces if i["ip_address"] == payload.interface_ip), None)
        if not iface:
            raise HTTPException(status_code=404, detail="Interface not found")
        return await tjob.add_interface(payload.interface_ip, iface["name"])
    all_ifaces = _interfaces_by_ip()
    iface = all_ifaces.get(payload.interface_ip)
    if not iface:
        raise HTTPException(status_code=404, detail="Interface not found")
    try:
        result = await manager.add_interface(job_id, iface)
        print(f"[ADD_IFACE] Single add {payload.interface_ip} -> {result}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": payload.interface_ip, "detail": result}

@app.post("/download/{job_id}/remove_interface")
async def remove_interface_from_job(job_id: str, payload: RemoveInterfaceRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        # Check if it's a torrent job
        tjob = active_torrents.get(job_id)
        if not tjob:
            raise HTTPException(status_code=404, detail="Job not found")
        return await tjob.remove_interface(payload.interface_ip)
        
    try:
        result = await manager.remove_interface(job_id, payload.interface_ip)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/download/{job_id}/pause")
async def pause_download(job_id: str) -> Dict[str, Any]:
    try:
        return await manager.pause_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/download/{job_id}/resume")
async def resume_download(job_id: str) -> Dict[str, Any]:
    try:
        return await manager.resume_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/download/{job_id}/cancel")
async def cancel_download(job_id: str) -> Dict[str, Any]:
    # Check regular manager first
    job = manager.get_job(job_id)
    if job:
        return await manager.cancel_job(job_id)
    
    # Check torrent manager
    tjob = active_torrents.get(job_id)
    if tjob:
        return await tjob.cancel()
    
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/torrent/start")
async def start_torrent_api(req: TorrentStartRequest) -> Dict[str, str]:
    if not req.interface_ips:
        raise HTTPException(status_code=400, detail="No interfaces selected")
    try:
        job = await start_torrent_download(req.magnet_uri, req.output_path, req.interface_ips, req.bandwidth_limits)
        await broadcast_event("new_job", {"job_id": job.job_id})
        return {"job_id": job.job_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/torrent/{job_id}")
async def get_torrent_status(job_id: str) -> Dict[str, Any]:
    job = active_torrents.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()

# ---------------------------------------------------------------------------
# Serve Production UI
# ---------------------------------------------------------------------------
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.join(os.path.dirname(__file__), "..")

    return os.path.join(base_path, relative_path)

frontend_dist = resource_path("frontend/dist")

if os.path.exists(frontend_dist):
    # Mount assets folder for JS/CSS
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    # Catch-all to serve index.html for any other URL (SPA support)
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # Check if the requested path is a file in the dist root (like favicon.ico)
        file_path = os.path.join(frontend_dist, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

if __name__ == "__main__":
    import uvicorn
    import webview
    from threading import Thread

    # 1. Define the server thread
    def start_server():
        # Full silent config for Uvicorn
        full_silence_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(message)s", "use_colors": False},
                "access": {"()": "uvicorn.logging.AccessFormatter", "fmt": "%(message)s", "use_colors": False},
            },
            "handlers": {"null": {"class": "logging.NullHandler"}},
            "loggers": {
                "uvicorn": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.error": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.access": {"handlers": ["null"], "level": "CRITICAL"},
            },
        }
        # Passing 'app' object directly is safer for PyInstaller
        uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_config=full_silence_config, use_colors=False)

    # 2. Start server in background
    t = Thread(target=start_server)
    t.daemon = True
    t.start()

    # 3. Create the API for Window Controls
    class WindowAPI:
        def __init__(self):
            self.window = None
        def set_window(self, window):
            self.window = window

        def close(self):
            if self.window: self.window.destroy()
        def minimize(self):
            if self.window: self.window.minimize()
        def maximize(self):
            if self.window:
                if self.window.fullscreen:
                    self.window.toggle_fullscreen()
                else:
                    self.window.maximize()

    api = WindowAPI()

    # 4. Define the background loader
    def load_logic(window):
        # Wait for server to be ready
        import time
        time.sleep(2)
        window.load_url("http://127.0.0.1:8000")

    # 5. Create and Start instantly
    window = webview.create_window(
        "Burst", 
        url=None, # Start empty so it's instant
        width=950, 
        height=680, 
        frameless=True, 
        easy_drag=True,
        resizable=False,
        js_api=api
    )
    api.set_window(window)
    
    # Start the engine - this runs the load_logic in the background
    webview.start(load_logic, window)
