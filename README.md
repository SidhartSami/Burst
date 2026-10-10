<div align="center">
  <img src="assets/logo.png" width="96" height="96" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>Open-source multi-connection download accelerator for Windows and Linux.</strong><br>
  Combine your Wi-Fi, Ethernet, and Mobile Hotspot into one aggregated, ultra-fast connection.</p>

  <p>
    <a href="https://sidhartsami.github.io/getburst/"><strong>Official Website</strong></a> &nbsp;&bull;&nbsp;
    <a href="https://github.com/SidhartSami/Burst/releases/latest"><strong>Download Releases</strong></a> &nbsp;&bull;&nbsp;
    <a href="#key-features"><strong>Features</strong></a> &nbsp;&bull;&nbsp;
    <a href="#browser-extensions"><strong>Extensions</strong></a> &nbsp;&bull;&nbsp;
    <a href="#quick-start"><strong>Quick Start</strong></a> &nbsp;&bull;&nbsp;
    <a href="#building-from-source"><strong>Build from Source</strong></a>
  </p>

  <p>
    <a href="https://github.com/SidhartSami/Burst/releases/latest"><img src="https://img.shields.io/github/v/release/SidhartSami/Burst?color=f97316&label=Version" alt="Latest Version"></a>
    <a href="https://github.com/SidhartSami/Burst/releases"><img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-0078D6" alt="Platforms"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e.svg" alt="MIT License"></a>
    <a href="https://github.com/SidhartSami/Burst/stargazers"><img src="https://img.shields.io/github/stars/SidhartSami/Burst?color=f97316&style=flat" alt="GitHub Stars"></a>
  </p>
</div>

---

## What is Burst?

Most download managers are limited to a single physical internet connection. Even if your computer is plugged into Ethernet and simultaneously connected to high-speed Wi-Fi or a 5G mobile hotspot, standard software only utilizes one interface at a time.

**Burst** changes that. It bonds all available network interfaces into a unified pipe, parallelizing HTTP downloads and BitTorrent traffic across your **Wi-Fi, Ethernet, USB tethering, and cellular hotspots** simultaneously.

* **No paid hardware** or specialized bonding routers required.
* **No remote proxy subscriptions** or third-party VPN accounts needed.
* **100% free, MIT-licensed, and open source** with zero telemetry.

---

## Demo

<div align="center">
  <video src="assets/demo.mp4" width="100%" controls></video>
  <p>
    <a href="assets/demo.mp4"><strong>▶ Watch Demo Video (assets/demo.mp4)</strong></a> &nbsp;&bull;&nbsp;
    <a href="https://sidhartsami.github.io/getburst/"><strong>🌐 Live Interactive Demo on getburst.app</strong></a>
  </p>
  <p><em>Real-time multi-interface bonding, dynamic chunk map, and BitTorrent selective file picking in action.</em></p>
</div>

---

## Downloads & Platform Support

Burst is distributed as native standalone executables with zero runtime dependencies.

| Platform | Format | Architecture | Direct Download |
| :--- | :--- | :--- | :--- |
| **Windows** | Setup Installer (`.exe`) | Windows 10 / 11 (64-bit) | [**Burst_Setup_v2.0.0.exe**](https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe) |
| **Linux** | Portable AppImage (`.AppImage`) | Ubuntu 22.04+, Debian, Fedora, Arch (64-bit) | [**Burst-Linux.AppImage**](https://github.com/SidhartSami/Burst/releases/latest/download/Burst-Linux.AppImage) |

> 🍎 **macOS Support**: Currently under active development for macOS Monterey+ (Universal / Apple Silicon & Intel). Track progress in the [Roadmap](#roadmap--help-wanted).

---

## Key Features

### ⚡ True Multi-Connection Bonding
Plug in an Ethernet cable while connected to Wi-Fi, or enable USB tethering from your smartphone. Burst dynamically detects every healthy network adapter, assigns distinct sockets, and aggregates their bandwidth simultaneously.

### 🧲 Advanced BitTorrent & Magnet Engine
Built directly on top of `libtorrent`. Paste magnet links or drop `.torrent` files into the window to inspect metadata, browse file hierarchies, and selectively prioritize or exclude individual files before downloading begins.

### 📊 Real-Time Chunk Map & Observability
Observe download progress live. Burst features an interactive visual chunk map color-coded by network interface, giving you instant visual feedback on which connection downloaded which piece.

### 🧩 Browser Extension Auto-Capture
Capture downloads directly from your favorite browser. Lightweight companion extensions automatically intercept downloads and route them into Burst's accelerated bonding engine.

### 🛡️ Adaptive Schedulers & Work Stealing
* **Dynamic EWMA Chunk Sizing**: Automatically tunes chunk requests (256 KB to 8 MB) based on each adapter's real-time Exponential Weighted Moving Average throughput.
* **Sub-Chunk Work Stealing**: High-speed adapters steal unstarted chunks from slower or congested adapters to eliminate stragglers.
* **Tail Racing**: Detects lagging chunks and dispatches duplicate racer tasks on idle interfaces. The first to verify claims the chunk, cancelling losing tasks with zero corruption.

### 💾 Safe Pause, Resume & Integrity Checks
Pause and resume downloads anytime. Chunks are validated with SHA-256 integrity checks, and progress is continuously persisted to disk, protecting your downloads against sudden network drops or power outages.

---

## Quick Start

### 1. Installation

#### Windows
Download [**Burst_Setup_v2.0.0.exe**](https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe) and run the installer. The wizard configures desktop shortcuts and automatically registers Windows Firewall rules.

#### Linux
Download [**Burst-Linux.AppImage**](https://github.com/SidhartSami/Burst/releases/latest/download/Burst-Linux.AppImage), make it executable, and launch:
```bash
chmod +x Burst-Linux.AppImage
./Burst-Linux.AppImage
```

### 2. Connect Your Interfaces
Connect your computer to two or more network sources:
* **Option A**: Home Wi-Fi + Ethernet cable plugged into your router.
* **Option B**: Wi-Fi + Smartphone connected via USB cable with **USB Tethering** enabled.
* **Option C**: Built-in Wi-Fi + USB Wi-Fi adapter connected to a secondary network.

### 3. Start Accelerating
Open Burst — active adapters will appear in the top interface bar. Paste any download URL, magnet link, or drop a `.torrent` file to begin downloading at full speed.

---

## Browser Extensions

Capture browser downloads seamlessly with the official Burst companion extension:

| Browser | Installation Method |
| :--- | :--- |
| **Google Chrome / Brave / Opera / Vivaldi** | [**Chrome Web Store**](https://chromewebstore.google.com/detail/burst-download-manager/dgikbcilhfjcdaemdnadnokpmfejhcjb) or load unpacked `extension-chrome.zip` |
| **Microsoft Edge** | Install from Chrome Web Store or enable during setup |
| **Mozilla Firefox / Zen Browser / Floorp** | Load temporary add-on via `about:debugging` using `extension-firefox.zip` |

*Pre-packaged extension bundles are available on the [Releases page](https://github.com/SidhartSami/Burst/releases).*

---

## Building from Source

### Prerequisites
* **Python 3.11+**
* **Node.js 18+** & npm (for the React/Vite frontend)

### Clone & Setup

```bash
# Clone the repository
git clone https://github.com/SidhartSami/Burst.git
cd Burst

# Build the React frontend
cd frontend
npm install
npm run build
cd ..

# Install Python backend dependencies
# For Windows:
pip install -r requirements.txt

# For Linux:
pip install -r requirements-linux.txt

# Run Burst
python launch_linux.py   # Linux
python run_burst.py      # Windows
```

---

## Roadmap & Help Wanted

* ✅ **Linux Support** — Fully completed in v2.0.0 (GTK3 pywebview, multi-interface bonding, AppImage distribution).
* 🔄 **macOS Support** — In progress (`window_mac.py`, `clipboard_mac.py`, Cocoa GUI, and DMG CI pipeline).
* 🧪 **Tail-Racing Benchmarks** — Evaluating racing heuristics on heavily asymmetric network setups (e.g. 100 Mbps fiber vs. 5 Mbps cellular).
* 🌍 **Localization (i18n)** — Community translations for the UI.

Contributions of all kinds are welcome! Check out the [open issues](https://github.com/SidhartSami/Burst/issues) or submit a pull request.

---

## Frequently Asked Questions (FAQ)

<details>
<summary><strong>Do I need a special router or subscription?</strong></summary>
<p>No. Burst runs 100% locally on your machine. It binds standard OS networking sockets directly to each physical network interface.</p>
</details>

<details>
<summary><strong>How do I connect multiple internet connections?</strong></summary>
<p>The simplest methods are:
<ul>
  <li>Connect to Wi-Fi, then plug in an Ethernet cable to your router.</li>
  <li>Connect to Wi-Fi, then plug your phone in via USB and enable <strong>USB Tethering</strong> (using mobile data).</li>
  <li>Plug in an external USB Wi-Fi adapter to connect to a second Wi-Fi network.</li>
</ul>
</p>
</details>

<details>
<summary><strong>Does Burst support torrents and magnet links?</strong></summary>
<p>Yes. Burst includes a full BitTorrent client with DHT metadata resolution, peer exchange, and an interactive file selector.</p>
</details>

<details>
<summary><strong>Can I pause downloads and resume them later?</strong></summary>
<p>Yes. All HTTP and torrent downloads support pause and resume with SHA-256 chunk integrity validation.</p>
</details>

<details>
<summary><strong>Which operating systems are supported?</strong></summary>
<p>Windows 10/11 (64-bit) and Linux (Ubuntu 22.04+, Debian, Fedora, Arch) are fully supported. macOS support is currently under active development.</p>
</details>

---

## License

Burst is open source under the [MIT License](LICENSE). Free for personal and commercial use.

<div align="center">
  <sub>Maintained by <a href="https://github.com/SidhartSami">Sidhart Sami</a> and contributors &bull; <a href="https://sidhartsami.github.io/getburst/">Website</a> &bull; <a href="https://github.com/SidhartSami/Burst">GitHub</a></sub>
</div>
