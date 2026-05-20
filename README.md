<div align="center">
  <img src="assets/logo.png" width="120" height="120" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>The High-Performance Multi-Interface Download Manager</strong></p>

  [![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)](https://www.microsoft.com/windows)
  [![Version](https://img.shields.io/badge/version-1.2.1-orange.svg)](https://github.com/SidhartSami/Burst/releases)
</div>

---

## ⚡ What is Burst?

**Burst** bonds multiple network interfaces (WiFi + Ethernet + LTE) into a single high-speed download stream. Instead of your OS picking one connection, Burst splits files into chunks and binds each chunk to a different interface — combining their speeds without a VPN or VPS.

## ✨ Features

- 🚀 **Bandwidth Bonding** — Combine WiFi, Ethernet, and LTE for aggregated download speeds
- 🌐 **Browser Extension** — Right-click any link → "Download with Burst" in Chrome, Edge, and Firefox
- 🧲 **Magnet & Torrent Support** — Torrent chunks distributed across all bonded interfaces via libtorrent
- ⚡ **Boost Mode** — Prioritize any download, spawning 3 workers per interface for maximum throughput
- 💻 **burst-cli** — `burst-cli pip install torch` routes Python package downloads through the bonding engine
- � **System Tray + Autostart** — Runs silently on boot, extension works without opening the app
- 🧠 **Intelligent Chunking** — Latency-aware chunk sizing with orphaned chunk reassignment
- 📦 **One-Click Installer** — Professional Windows installer via Inno Setup

## 🚀 Getting Started

### Installation
1. Go to the [Releases](https://github.com/SidhartSami/Burst/releases) page
2. Download `Burst_Setup_v1.2.1.exe` 
3. Run the installer — Burst starts on boot automatically

### Browser Extension
- **Chrome / Edge**: [Install from Chrome Web Store](https://chrome.google.com/webstore/detail/burst/pblmhjepeacmfphcnaaekefjnipfkcfd)
- **Firefox**: Coming soon to Firefox Add-ons

### burst-cli
```bash
burst-cli pip install numpy
burst-cli pip install -r requirements.txt
```

### Developer Setup
```bash
git clone https://github.com/SidhartSami/Burst.git

# Backend
pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && npm run build && cd ..

# Run from source
python backend/main.py

# Build exe
pyinstaller Burst.spec
pyinstaller burst-cli.spec

# Build installer
iscc installer.iss
```

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn, aiohttp |
| Engine | libtorrent, socket-level interface binding |
| Frontend | React, Tailwind-style CSS, Lucide Icons |
| GUI | PyWebView |
| Packaging | PyInstaller, Inno Setup |

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
<div align="center">
  Built with ❤️ by SidhartSami
</div>
