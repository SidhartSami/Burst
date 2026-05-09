# Burst 🚀

**A calmer, smarter, parallel download manager.**

Burst intelligently bonds all your network interfaces — Wi-Fi, Ethernet, USB tethering — to maximize download speed. It dynamically scores each connection, routes failing chunks to healthier interfaces, and adapts in real-time as conditions change.

---

## Features

### 🔀 Intelligent Bandwidth Management
- **Weighted Interface Scoring** — Each interface gets a traffic share proportional to its real-time speed. A 2 MB/s Wi-Fi + 8 MB/s Ethernet = 20%/80% split, updated every 5 seconds.
- **Slow Interface Auto-Drop** — If an interface drops below 50 KB/s for 10 seconds, Burst stops assigning new chunks to it. When it recovers, it's automatically resumed.
- **Orphaned Chunk Reassignment** — If a connection drops mid-download, in-flight chunks are returned to the queue and healthy interfaces absorb them instantly.
- **"Waiting to Reconnect"** — If all interfaces fail, Burst shows a clear banner and resumes automatically the moment any connection comes back.

### 🧩 Dynamic Chunk Strategy
- **Latency-Aware Sizing** — Before downloading, Burst measures round-trip latency to the server per interface. High-latency connections get smaller chunks (cheaper to retry), low-latency ones get larger chunks (higher throughput).
- **Cross-Interface Retry** — Failed chunks don't retry on the same interface. They're routed to the next healthiest connection, with a 15-second cooldown before the original becomes eligible again.
- **Activity Log** — Every retry and reassignment event is logged and visible in the UI.

### ⚡ Hot-Swap Interfaces
Plug in a new tethered phone mid-download, select it in the UI, and Burst spawns a new worker thread to use it immediately — no restart needed.

### ⚙️ Settings Panel
All thresholds are user-configurable from the Settings panel:
- Chunk sizes (base, min, max)
- Weight rebalance interval
- Speed threshold for slow-interface detection
- Grace period, disconnect timeout
- Retry cooldown, max failures
- Changes persist to disk and take effect on next download.

### 🎨 Zen Aesthetics
- Minimalist, card-based interface
- Auto-scaling speed display (B/s → KB/s → MB/s → GB/s)
- Per-interface status badges: **Active** / **Slow** / **Lost** / **Excluded**
- Traffic weight percentages on each interface pill during downloads

---

## Setup

### Backend
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

---

## Architecture

```
backend/
  config.py         — All named constants, persisted settings
  downloader.py     — Core engine: chunk queue, workers, retry, weight scoring
  interfaces.py     — psutil-based network interface discovery
  speedtest.py      — Per-interface Cloudflare benchmarking
  merger.py         — Chunk → final file assembly
  main.py           — FastAPI routes, WebSocket progress, settings API

frontend/
  src/App.jsx       — Full React UI
  src/index.css     — Design system
```

---

## Roadmap

- [ ] **Browser Extensions** — Chrome/Firefox integration to intercept downloads
- [ ] **BitTorrent Support** — Magnet links and P2P chunk distribution
- [ ] **System Tray Agent** — Electron/Tauri wrapper with native notifications
- [ ] **Scheduled Queues** — Night mode, bandwidth throttling
- [ ] **Media Extractors** — YouTube/Twitch URL support via yt-dlp
- [ ] **.torrent / magnet: handlers** — OS-level protocol registration (requires native wrapper)
- [ ] **Single-instance IPC** — Named pipe detection to prevent duplicate processes
