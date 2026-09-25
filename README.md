<div align="center">
  <img src="assets/logo.png" width="120" height="120" alt="Burst Logo">
  <h1>Burst</h1>
  <p><strong>Combine your Wi-Fi, Ethernet, and Mobile Hotspot into one ultra-fast download connection.</strong></p>

  <p>
    <a href="https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe">
      <img src="https://img.shields.io/badge/Download-Burst%20for%20Windows%20(v2.0.0)-f97316?style=for-the-badge&logo=windows&logoColor=white" alt="Download Burst for Windows">
    </a>
  </p>

  [![Version](https://img.shields.io/badge/Version-v2.0.0-f97316.svg)](https://github.com/SidhartSami/Burst/releases)
  [![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011%20(64--bit)-0078D6.svg)](https://www.microsoft.com/windows)
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

**Burst** is a free, community-driven download accelerator for Windows that bonds all your available internet connections together. When you download a file or torrent, Burst divides it into pieces and downloads them simultaneously across your **Wi-Fi, Ethernet cable, and USB/Hotspot connections**, combining their speeds into one powerful stream.

Burst is built fully in the open — the engine, the scheduler, the reliability logic, and the roadmap all live in this repository. Issues, pull requests, and forks are welcome.

* No expensive hardware required.
* No paid VPNs or remote bonding subscriptions.
* 100% free, MIT-licensed, and open source.

---

## Demo

<!--
  TODO: Add your recorded demo here.
  Easiest options:
  1) Drop an .mp4 into a new GitHub Issue/PR comment box — GitHub will host it and give you
     a user-content URL. Use that URL as the link target below.
  2) Convert the clip to a short looping GIF and reference it directly with an <img> tag —
     GIFs autoplay inline in the README, which usually gets more attention than a static
     thumbnail people have to click.
-->

<p align="center">
  <a href="VIDEO_URL_HERE">
    <img src="assets/demo-thumbnail.png" alt="Watch the Burst demo" width="720">
  </a>
</p>

<p align="center"><em>Click to watch: multi-interface bonding, live speed dashboard, and torrent file selection in action.</em></p>

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
Click below to download the official Windows installer:

**[Download Burst Setup (v2.0.0)](https://github.com/SidhartSami/Burst/releases/latest/download/Burst_Setup_v2.0.0.exe)**

### 2. Install
Run `Burst_Setup_v2.0.0.exe` and follow the setup wizard. The installer sets up desktop shortcuts and configures Windows Firewall permissions automatically.

### 3. Connect Your Networks & Download
1. Connect your PC to two or more connections (e.g. Wi-Fi + your phone's USB tethering hotspot, or Wi-Fi + Ethernet).
2. Open Burst — you will see your active network adapters listed at the top.
3. Paste any download URL, magnet link, or drop a `.torrent` file into the app, and click **Download**.

---

## Browser Extensions

Burst can capture your browser downloads with a single click:

* **Chrome & Brave & Edge**: Install via the [Chrome Web Store](https://chrome.google.com/webstore/detail/burst/pblmhjepeacmfphcnaaekefjnipfkcfd) or enable during setup.
* **Firefox & Zen Browser**: Enable during setup to register the companion extension.

---

## Roadmap / Help Wanted

Burst is Windows-only today. These are the areas where contributions would help most:

* **macOS support** — interface detection/binding via `networksetup`, packaging.
* **Linux support** — interface binding via `SO_BINDTODEVICE`, packaging (AppImage/deb).
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
<p>Not yet — Burst is currently Windows-only. See the Roadmap section above; contributions for macOS/Linux support are welcome.</p>
</details>

---

## License

Burst is licensed under the [MIT License](LICENSE). Free for personal and commercial use.

<div align="center">
  <sub>Built by <a href="https://github.com/SidhartSami">Sidhart Sami</a> and contributors.</sub>
</div>
