<div align="center">
  <img src="assets/logo.png" width="120" height="120" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>The High-Performance Multi-Interface Download Manager</strong></p>

  [![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)](https://www.microsoft.com/windows)
  [![Version](https://img.shields.io/badge/version-1.1.2-orange.svg)](https://github.com/SidhartSami/Burst/releases)
</div>

---

## ⚡ What is Burst?

**Burst** is a high-performance download manager that bonds multiple network interfaces (such as WiFi, Ethernet, and cellular LTE) into a single, combined high-speed download pipeline. By splitting files into chunk segments and binding them to individual network interfaces at the socket level, Burst aggregates your bandwidth without needing a VPN or a remote VPS.

---

## ✨ Features

- 🚀 **Bandwidth Bonding** — Bond WiFi, Ethernet, and LTE adapters to multiply download speeds.
- 🌐 **Browser Extensions** — Direct right-click integration ("Download with Burst") in Chrome, Edge, Firefox, and Zen Browser.
- 🧲 **Magnet & Torrent Support** — Highly parallel torrent chunk downloading distributed across all interfaces using `libtorrent`.
- 📅 **Flexible Scheduler** — Schedule downloads for specific times with options for daily or weekly recurrence.
- ⚡ **Boost Mode** — Prioritize any download by spawning 3 parallel workers per interface for absolute throughput saturation.
- 💻 **CLI Integration** — Run pip installations directly through the bonding engine (`burst-cli pip install torch`).
- 🔔 **Tray & Background Execution** — Minimized-to-tray autostart on Windows boot lets browser extensions send downloads without the main GUI active.
- 📊 **Windows Taskbar Progress** — Native taskbar overlay integration displaying total download progress, paused states, and errors.
- 🧠 **Dynamic Interface Rebalancing** — Latency-aware chunk sizing with orphaned chunk reassignment and automatic exclusion of degraded connections.

---

## 🚀 Getting Started

### Installation
1. Go to the [Releases](https://github.com/SidhartSami/Burst/releases) page.
2. Download and run `Burst_Setup_v1.1.2.exe`.
3. Complete the onboarding screen to authorize extension integration.

### Browser Extension
- **Chrome / Edge**: [Install from Chrome Web Store](https://chrome.google.com/webstore/detail/burst/pblmhjepeacmfphcnaaekefjnipfkcfd)
- **Firefox / Zen**: Included in the installation folder under `{app}\extension-firefox` for easy developer-mode load.

### CLI Usage
```bash
burst-cli pip install numpy
burst-cli pip install -r requirements.txt
```

---

## 🛠️ Developer Setup

```bash
# Clone the repository
git clone https://github.com/SidhartSami/Burst.git
cd Burst

# Install Backend Dependencies
pip install -r backend/requirements.txt

# Install & Build Frontend Production Assets
cd frontend
npm install
npm run build
cd ..

# Run in Development Mode
python backend/main.py

# Recompile Executable (outputs to dist/)
pyinstaller -y Burst.spec
pyinstaller -y native_host.spec

# Recompile Installer (requires Inno Setup 6)
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, FastAPI, Uvicorn, aiohttp |
| **Engine** | libtorrent, socket-level interface binding, psutil |
| **Frontend** | React, Tailwind CSS styles, Lucide Icons |
| **GUI** | PyWebView |
| **Packaging** | PyInstaller, Inno Setup |

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
<div align="center">
  Built with ❤️ by SidhartSami
</div>
