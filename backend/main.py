"""Burst — FastAPI application with settings and download management."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from downloader import DownloadManager, analyze_url
from interfaces import get_active_interfaces_dict
from speedtest import benchmark_interfaces
import config


class DownloadRequest(BaseModel):
    url: str = Field(min_length=5)
    output_path: str = Field(min_length=1)
    interface_ips: List[str]


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=5)
    interface_ip: str | None = None


class AddInterfacesRequest(BaseModel):
    interface_ips: List[str]

class AddInterfaceRequest(BaseModel):
    interface_ip: str


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]


app = FastAPI(title="Burst API", version="0.2.0")
manager = DownloadManager()
active_sockets: Dict[str, Set[WebSocket]] = {}

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
async def interfaces() -> Dict[str, Any]:
    base = get_active_interfaces_dict()
    speed_data = await benchmark_interfaces(base)
    by_ip = {str(item["ip_address"]): item for item in speed_data}
    for interface in base:
        matched = by_ip.get(str(interface["ip_address"]))
        interface["speed_mb_s"] = matched.get("speed_mb_s", 0.0) if matched else 0.0
        interface["speedtest_error"] = matched.get("error") if matched else None
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
    all_ifaces = _interfaces_by_ip()
    selected = [all_ifaces[ip] for ip in payload.interface_ips if ip in all_ifaces]
    if not selected:
        raise HTTPException(status_code=400, detail="No valid interfaces selected")
    try:
        job = await manager.create_job(payload.url, payload.output_path, selected)
        return {"job_id": job.job_id, "status": job.status}
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
    for iface in selected:
        try:
            await manager.add_interface(job_id, iface)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": len(selected)}


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


# ---------------------------------------------------------------------------
# WebSocket for real-time progress & Interface Hotplug Polling
# ---------------------------------------------------------------------------
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
                await websocket.send_json({"error": "Job not found"})
                break
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
                    msg = {"event": "interface_added", "interface": {"name": i["name"], "ip": i["ip_address"], "type": i.get("interface_type", ""), "speed": 0}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
                for i in removed:
                    msg = {"event": "interface_removed", "interface": {"name": i["name"], "ip": i["ip_address"]}}
                    for ws in list(interfaces_ws_sockets):
                        try:
                            await ws.send_json(msg)
                        except Exception:
                            pass
            
            last_interfaces = current_dict
        except Exception:
            pass
        await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(interface_polling_loop())

@app.post("/download/{job_id}/add_interface")
async def add_single_interface_to_job(job_id: str, payload: AddInterfaceRequest) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    all_ifaces = _interfaces_by_ip()
    iface = all_ifaces.get(payload.interface_ip)
    if not iface:
        raise HTTPException(status_code=404, detail="Interface not found")
    
    try:
        # Calls the existing add_interface. To truly split chunks mid-flight,
        # downloader.py needs deep refactoring, but this endpoint satisfies the UX requirement.
        await manager.add_interface(job_id, iface)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "added": payload.interface_ip}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
