# Burst Browser Extension

Chrome/Edge companion extension for the [Burst Download Manager](https://github.com/SidhartSami/Burst).

## How it works

```
Browser click / magnet link
        ↓
Chrome Extension (background.js)
        ↓  chrome.runtime.sendNativeMessage()
Native Host (native_host.bat → native_host.py)
        ↓  HTTP POST
Burst FastAPI (localhost:59284)
        ↓
Download engine (bandwidth bonded)
```

No localhost URLs are exposed to the browser. All communication goes through Chrome's secure native messaging pipe.

## Requirements

- Burst desktop app installed (handles the registry setup automatically)
- Chrome or Edge

## File Structure

```
extension/
  manifest.json       # Chrome extension manifest v3
  background.js       # Service worker: intercepts downloads + magnet links
  content.js          # Page-level magnet link click interceptor
  popup/
    popup.html
    popup.css
    popup.js
  icons/              # Add icon16.png, icon48.png, icon128.png

native_host.py                      # Python bridge: extension ↔ FastAPI
native_host.bat                     # Launcher wrapper (Chrome requires binary/bat)
com.burst.download.manager.json     # Chrome native messaging manifest
inno_setup_additions.iss            # Paste into existing Burst.iss
backend_changes.py                  # Notes on config.py + App.jsx changes
```

## Setup for Development

1. Add the Inno Setup additions to your existing `Burst.iss` and rebuild the installer, **OR** manually run:
```
reg add "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager" /ve /t REG_SZ /d "C:\path\to\com.burst.download.manager.json" /f
```

2. Load the extension unpacked in Chrome:
   - Go to `chrome://extensions`
   - Enable Developer Mode
   - Click "Load unpacked" → select the `extension/` folder

3. Copy your extension ID from `chrome://extensions` and paste it into `com.burst.download.manager.json`:
```json
"allowed_origins": ["chrome-extension://YOUR_ID_HERE/"]
```

4. Make sure Burst desktop app is running, then test by right-clicking any download link → "Download with Burst ⚡"

## Publishing to Chrome Web Store

1. Zip the `extension/` folder
2. Go to [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole)
3. Pay one-time $5 registration fee
4. Upload the zip, fill in listing details
5. Note in the description: *"Requires Burst Download Manager desktop app"*
