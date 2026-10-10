<div align="center">
  <img src="assets/logo.png" width="120" height="120" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>Combine your Wi-Fi, Ethernet, and Mobile Hotspot into one ultra-fast download connection.</strong></p>

  <p>
    <a href="https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe">
      <img src="https://img.shields.io/badge/Download-Burst%20for%20Windows%20(v2.0.0)-f97316?style=for-the-badge&logo=windows&logoColor=white" alt="Download Burst for Windows">
    </a>
    <a href="https://github.com/SidhartSami/Burst/releases/latest/download/Burst-Linux.AppImage">
      <img src="https://img.shields.io/badge/Download-Burst%20for%20Linux%20AppImage-FCC624?style=for-the-badge&logo=linux&logoColor=black" alt="Download Burst for Linux">
    </a>
  </p>

  [![Version](https://img.shields.io/badge/Version-v2.0.0-f97316.svg)](https://github.com/SidhartSami/Burst/releases)
  [![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-0078D6.svg)](https://github.com/SidhartSami/Burst/releases)
  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
  [![Free & Open Source](https://img.shields.io/badge/Free%20%26-Open%20Source-22c55e.svg)](https://github.com/SidhartSami/Burst)

  <p>
    <a href="https://github.com/SidhartSami/Burst/stargazers"><img src="https://img.shields.io/github/stars/SidhartSami/Burst?style=social" alt="GitHub stars"></a>
    <a href="https://github.com/SidhartSami/Burst/network/members"><img src="https://img.shields.io/github/forks/SidhartSami/Burst?style=social" alt="GitHub forks"></a>
    <a href="https://github.com/SidhartSami/Burst/issues"><img src="https://img.shields.io/github/issues/SidhartSami/Burst?color=orange" alt="Open issues"></a>
    <a href="https://github.com/SidhartSami/Burst/graphs/contributors"><img src="https://img.shields.io/github/contributors/SidhartSami/Burst?color=blue" alt="Contributors"></a>
  </p>
</div>

---

## What is Burst?

Have you ever wished you could combine your home Wi-Fi and your phone's 5G mobile hotspot to download files twice as fast?

**Burst** is a free, community-driven download accelerator for Windows and Linux that bonds all your available internet connections together. When you download a file or torrent, Burst divides it into pieces and downloads them simultaneously across your **Wi-Fi, Ethernet cable, and USB/Hotspot connections**, combining their speeds into one powerful stream.

Burst is built fully in the open — the engine, the scheduler, the reliability logic, and the roadmap all live in this repository. Issues, pull requests, and forks are welcome.

* No expensive hardware required.
* No paid VPNs or remote bonding subscriptions.
* 100% free, MIT-licensed, and open source.

---

## Demo

<div align="center">
  <video src="https://github.com/SidhartSami/Burst/releases/download/v2.0.0/demo.mp4" width="100%" controls></video>

  <p>
    <a href="https://github.com/SidhartSami/Burst/releases/download/v2.0.0/demo.mp4">
      <img src="https://img.shields.io/badge/▶%20Play%20Demo%20Video-demo.mp4-f97316?style=for-the-badge&logo=youtube&logoColor=white" alt="Play Demo Video">
    </a>
    <a href="https://sidhartsami.github.io/getburst/">
      <img src="https://img.shields.io/badge/🌐%20Interactive%20Website-getburst.app-0078D6?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Visit getburst.app">
    </a>
  </p>

  <p>
    <em>Multi-interface bonding, live speed dashboard, and accelerated download engine in action.</em><br>
    <sub>Direct link: <a href="assets/demo.mp4">assets/demo.mp4</a> &bull; Release asset: <a href="https://github.com/SidhartSami/Burst/releases/download/v2.0.0/demo.mp4">v2.0.0/demo.mp4</a></sub>
  </p>
</div>

---

## Key Features

### <img src="https://api.iconify.design/ph/link-bold.svg?color=%23f97316" width="20" valign="middle"> True Multi-Connection Bonding
Plug in an Ethernet cable while connected to Wi-Fi, or turn on USB tethering with your phone's cellular data. Burst automatically detects every active adapter and aggregates their bandwidth simultaneously.

### <img src="https://api.iconify.design/ph/magnet-bold.svg?color=%23f97316" width="20" valign="middle"> Advanced Torrent & Magnet Downloader
Built on the industry-standard `libtorrent` engine. Paste magnet links, drop `.torrent` files, and browse the full file tree to download only the files you want before downloading starts.

### <img src="https://api.iconify.design/ph/chart-bar-bold.svg?color=%23f97316" width="20" valign="middle"> Real-Time Chunk Map & Speed Dashboard
Watch your download progress live! Burst's interactive chunk map shows exactly which network adapter (Wi-Fi, Ethernet, or Mobile Data) is downloading each piece in real time.

### <img src="https://api.iconify.design/ph/globe-bold.svg?color=%23f97316" width="20" valign="middle"> One-Click Browser Integration
Download files directly from your favorite browser. Burst includes lightweight extensions for **Google Chrome, Microsoft Edge, Mozilla Firefox, and Zen Browser**.

### <img src="https://api.iconify.design/ph/arrows-clockwise-bold.svg?color=%23f97316" width="20" valign="middle"> Reliable Pause & Resume
Network drops or need to step away? Burst safely saves your progress with SHA-256 validation. Pause, resume, or restart your PC without losing downloaded data.

### <img src="https://api.iconify.design/ph/moon-bold.svg?color=%23f97316" width="20" valign="middle"> Clean Dark Mode Interface
Designed from the ground up for Windows with a fast, modern dark-mode interface, audio completion chimes, and system tray minimization.

---

## How to Install & Use Burst

### 1. Download
Click below to download the latest release for your platform:

* **Windows**: **[Download Burst Setup (v2.0.0)](https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe)** (`.exe` installer)
* **Linux**: **[Download Burst-Linux.AppImage](https://github.com/SidhartSami/Burst/releases/latest/download/Burst-Linux.AppImage)** (portable AppImage)

### 2. Install & Run

#### Windows
Run `Burst_Setup_v2.0.0.exe` and follow the setup wizard. The installer sets up desktop shortcuts and configures Windows Firewall permissions automatically.

#### Linux (Ubuntu, Debian, Fedora, Arch, WSL2)
Download `Burst-Linux.AppImage`, make it executable, and run it:
```bash
chmod +x Burst-Linux.AppImage
./Burst-Linux.AppImage
```

### 3. Connect Your Networks & Download
1. Connect your PC to two or more connections (e.g. Wi-Fi + your phone's USB tethering hotspot, or Wi-Fi + Ethernet).
2. Open Burst — you will see your active network adapters listed at the top.
3. Paste any download URL, magnet link, or drop a `.torrent` file into the app, and click **Download**.

---

## Browser Extensions

Burst can capture your browser downloads with a single click:

* **Chrome & Brave & Edge**: Install via the [Chrome Web Store](https://chromewebstore.google.com/detail/burst-download-manager/dgikbcilhfjcdaemdnadnokpmfejhcjb) or enable during setup.
* **Firefox & Zen Browser**: Enable during setup to register the companion extension.

---

## Roadmap / Help Wanted

These are the areas where contributions would help most:

* ✅ **Linux support** — Completed in v2.0.0 (GTK3 pywebview, multi-interface bonding, AppImage distribution).
* **macOS support** — interface detection/binding via `networksetup`, packaging (.dmg / app bundle).
* **Tail racing tuning** — currently experimental and off by default; benchmarking on real asymmetric networks would help decide whether it ships on by default.
* **Localization** — UI translations.

If any of this interests you, open an issue to say what you're picking up before starting, so effort isn't duplicated.

---

## Contributing

Contributions of all sizes are welcome — bug reports, docs fixes, feature ideas, and pull requests.

1. Check open [issues](https://github.com/SidhartSami/Burst/issues) or the Roadmap above for something to work on.
2. Fork the repo and create a feature branch.
3. Open a pull request describing what changed and why.

If you find Burst useful, starring the repo helps others discover it too.

---

## Frequently Asked Questions (FAQ)

<details>
<summary><strong>Do I need a special router or subscription?</strong></summary>
<p>No! Burst runs completely on your local computer. It uses standard Windows networking sockets to split and request different parts of your files across your connected network interfaces.</p>
</details>

<details>
<summary><strong>How do I connect multiple internet connections on one laptop/PC?</strong></summary>
<p>The easiest ways are:
<ul>
  <li>Connect to your home Wi-Fi, then plug in an Ethernet cable to your router.</li>
  <li>Connect to your Wi-Fi, then connect your smartphone via USB cable and turn on <em>USB Tethering</em> (using your 4G/5G data).</li>
  <li>Use an external USB Wi-Fi dongle to connect to two separate Wi-Fi networks at once.</li>
</ul>
</p>
</details>

<details>
<summary><strong>Does Burst support torrents and magnet links?</strong></summary>
<p>Yes! Burst includes a full BitTorrent client with DHT, peer exchange, and an interactive file selector that lets you choose specific files to download.</p>
</details>

<details>
<summary><strong>Can I pause downloads and resume them later?</strong></summary>
<p>Yes. Both regular downloads and torrents can be paused and resumed at any time without losing progress.</p>
</details>

<details>
<summary><strong>What are the system requirements?</strong></summary>
<p>Windows 10 or Windows 11 (64-bit), and at least one active network connection (two or more recommended to take advantage of bandwidth aggregation).</p>
</details>

<details>
<summary><strong>Is macOS or Linux supported?</strong></summary>
<p>Windows and Linux (via portable AppImage) are both officially supported! macOS support is currently under development (see Roadmap above).</p>
</details>

---

## License

Burst is licensed under the [MIT License](LICENSE). Free for personal and commercial use.

<div align="center">
  <sub>Built by <a href="https://github.com/SidhartSami">Sidhart Sami</a> and contributors.</sub>
</div>
