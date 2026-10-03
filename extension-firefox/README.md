# Burst Browser Extension

Firefox companion extension for the [Burst Download Manager](https://github.com/SidhartSami/Burst).

## What it does

- **Icon status**: Orange = Burst running, Gray = Burst offline
- **Torrent & Magnet Interception**: Automatically catches `.torrent` file downloads and `magnet:` links and sends them to Burst.
- **Normal Browser Downloads Untouched**: PDFs, documents, images, web pages, and other regular files are **never** interrupted — they download smoothly in your browser as usual.
- **Right-click menu**: "Download with Burst" available when right-clicking links.
- **No UI**: No popup, no dropdown, no settings — just the clean status icon.

## How it works

```
Magnet link / .torrent download
        ↓
Browser Extension (background.js)
        ↓  chrome.runtime.sendNativeMessage()
Native Host (native_host.bat → native_host.py)
        ↓  HTTP POST
Burst FastAPI (localhost:59284)
        ↓
Download engine (bandwidth bonded)
```

No localhost URLs are exposed to the browser. All communication goes through the browser's secure native messaging pipe.

## Requirements

- Burst desktop app installed (handles registry configuration)
- Firefox
