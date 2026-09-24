<div align="center">
  <img src="assets/logo.png" width="128" height="128" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>Aggregate Your Network Interfaces into a Single High-Speed Download Pipeline</strong></p>

  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://www.microsoft.com/windows)
  [![Release](https://img.shields.io/badge/Release-v1.0--candidate-success.svg)](https://github.com/SidhartSami/Burst/releases)
  [![Tests: 95/95 Passing](https://img.shields.io/badge/Tests-95%2F95%20Passing-brightgreen.svg)](tests/)
</div>

---

## ⚡ What is Burst?

**Burst** is an open-source download accelerator that combines multiple active internet connections—such as **Wi-Fi, Ethernet, and Mobile Hotspots (4G/5G)**—into a single aggregated stream. 

Unlike channel bonding solutions that require costly VPN subscriptions or remote proxy servers, Burst operates entirely on your local machine. It partitions downloads into dynamic byte-range chunks and binds them directly to specific network interfaces at the OS socket level.

---

## 🚀 Key Features

* ⚡ **True Bandwidth Aggregation** — Download concurrently over Ethernet, Wi-Fi, and LTE without third-party servers or VPNs.
* 🧲 **HTTP & Torrent Engine** — Full support for direct HTTP/HTTPS downloads, `.torrent` files, and live BitTorrent swarms via libtorrent.
* 📁 **Selective Torrent Downloading** — Interactive file-tree browser with native file priorities (0–7) and folder selection before downloading metadata.
* 📊 **Real-Time Observability** — Live dashboard displaying per-adapter throughput, EWMA health status, and a visual chunk distribution map.
* 🛡️ **Fault-Tolerant & Resilient** — Dynamic chunk slicing, automated worker stall recovery, seamless failover if an adapter disconnects, and preflight disk safety checks.
* 🌐 **Browser Extensions** — One-click download capture for Google Chrome, Microsoft Edge, Mozilla Firefox, and Zen Browser.
* 🔄 **Smart Resume** — SHA-256 validated checkpointing and sparse disk allocation allow instant pause, resume, and restart recovery.

---

## 📥 Quick Start

### 1. Download & Install
Download the latest Windows installer (`Burst_Setup.exe`) from the [Releases](https://github.com/SidhartSami/Burst/releases) page and run the setup wizard.

### 2. Connect Your Interfaces
Connect your computer to two or more network links (e.g., your home Wi-Fi and an Ethernet cable, or Wi-Fi and a tethered phone hotspot). Burst detects all active adapters automatically.

### 3. Start Downloading
Paste any direct download link or magnet URI into Burst, or click **"Download with Burst"** from your web browser.

---

## 🌐 Browser Extensions

Integrate Burst directly with your favorite browser:
* **Chrome & Edge**: Install via the [Chrome Web Store](https://chrome.google.com/webstore/detail/burst/pblmhjepeacmfphcnaaekefjnipfkcfd).
* **Firefox & Zen**: Load the unpacked extension from the installation directory (`extension-firefox/`) via `about:debugging`.

---

## 💻 CLI Integration

Burst includes a companion CLI for scriptable downloads and accelerating package installations:

```bash
# Accelerate pip package downloads across bonded interfaces
burst-cli pip install torch torchvision

# Direct file download
burst-cli download https://example.com/large-dataset.zip
```

---

## 🛠️ Developer Setup

```bash
# 1. Clone repository
git clone https://github.com/SidhartSami/Burst.git
cd Burst

# 2. Set up backend environment
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt

# 3. Build frontend
cd frontend
npm install
npm run build
cd ..

# 4. Run application
python backend/main.py

# 5. Run test suite (95 tests)
pytest -v
```

---

## 🏗️ Architecture

| Component | Technology | Description |
|---|---|---|
| **Core Engine** | Python 3.11, `libtorrent 2.0`, `asyncio` | Socket-level interface binding, Range splitting, BitTorrent engine |
| **API Server** | FastAPI, Uvicorn, WebSockets | Low-latency telemetry streaming (<15 KB batched payloads) |
| **Desktop UI** | React, Tailwind CSS, PyWebView | Native desktop window with live sparklines & chunk grid |
| **Integrations** | Native Messaging Host, Manifest V3 | Browser right-click interception and CLI pip wrapper |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
