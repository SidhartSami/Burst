<div align="center">
  <img src="logo.png" width="120" height="120" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>The High-Performance Multi-Interface Download Manager</strong></p>
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)](https://www.microsoft.com/windows)
</div>

---

## ⚡ What is Burst?

**Burst** is a next-generation download manager designed to maximize your bandwidth by **bonding multiple network interfaces** into a single, high-speed download stream. Whether you're combining Ethernet, Wi-Fi, and 5G/4G tethering, Burst intelligently distributes chunks across all active connections to ensure you're using every bit of speed available.
<img width="800" height="450" alt="VideoProject1-ezgif com-video-to-gif-converter" src="https://github.com/user-attachments/assets/5c9138b6-9886-46c2-828d-33f43bd8f0a3" />

## ✨ Key Features

- 🚀 **Bandwidth Bonding**: Combine multiple network interfaces (Wi-Fi, Ethernet, LTE) for aggregated download speeds.
- 📂 **Multi-Source Engine**: Support for standard HTTP/HTTPS and high-performance **Torrent** downloads via `libtorrent`.
- 🧠 **Intelligent Chunking**: Latency-aware chunk sizing and orphaned chunk reassignment for maximum efficiency.
- 🎨 **Modern Frameless UI**: A sleek, dark-themed interface built with React, providing a premium desktop experience.
- 🛡️ **Automated Firewall Setup**: Zero-config installation with automated Windows Firewall rule management.
- 📦 **One-Click Installer**: Professional "Next-Next-Finish" setup experience powered by Inno Setup.

## 🚀 Getting Started

### Installation
1. Go to the [Releases](https://github.com/YOUR_USERNAME/Burst/releases) page.
2. Download `Burst_Setup_v1.0.exe`.
3. Run the installer and launch Burst!

### Developer Setup
If you want to build Burst from source:
```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/Burst.git

# Install Backend Dependencies
pip install -r backend/requirements.txt

# Build Frontend
cd frontend
npm install
npm run build

# Build Executable
pyinstaller --onefile --noconsole --uac-admin --name "Burst" --icon "logo.png" --add-data "frontend/dist;frontend/dist" --add-data "logo.png;." backend/main.py
```

## 🛠️ Tech Stack
- **Frontend**: React, Tailwind-style CSS, Lucide Icons
- **Backend**: FastAPI (Python), Uvicorn
- **GUI Wrapper**: PyWebView
- **Engine**: Libtorrent, Aiohttp
- **Packaging**: PyInstaller, Inno Setup

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
<div align="center">
  Built with ❤️ by SidhartSami
</div>
