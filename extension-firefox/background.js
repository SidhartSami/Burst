// Burst Extension (Firefox) — background.js
// Minimal extension: icon status (orange/gray).
// Intercepts ONLY magnet links and .torrent files.
// All other files (PDFs, documents, media, executables, web pages, etc.) download normally in browser.
// No popup, no settings, no dropdown.

const NATIVE_HOST = "com.burst.download.manager";
const API_BASE = "http://127.0.0.1:59284";

const actionApi = (typeof browser !== "undefined" && browser.action) || chrome.action;

// ── Status & Icon Management ──────────────────────────────────────────────────

let isBurstOnline = false;

function setIconStatus(online) {
  isBurstOnline = online;
  const suffix = online ? "_orange.png" : "_gray.png";
  const iconPaths = {
    16: `icons/icon16${suffix}`,
    32: `icons/icon32${suffix}`,
    48: `icons/icon48${suffix}`,
    128: `icons/icon128${suffix}`,
  };

  if (actionApi && actionApi.setIcon) {
    actionApi.setIcon({ path: iconPaths });
  }
  if (actionApi && actionApi.setTitle) {
    actionApi.setTitle({
      title: online
        ? "Burst is running"
        : "Burst is offline (open Burst to connect)",
    });
  }
}

async function checkBurstStatus() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 1200);
    const res = await fetch(`${API_BASE}/settings`, {
      method: "GET",
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      setIconStatus(true);
      return true;
    }
  } catch (err) {
    // Backend not responding
  }
  setIconStatus(false);
  return false;
}

// Check status on startup and install
chrome.runtime.onStartup.addListener(() => checkBurstStatus());

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    chrome.tabs.create({ url: "https://sidhartsami.github.io/getburst/welcome" });
  }
  checkBurstStatus();
  setupContextMenu();
});

// Periodic alarm to keep status fresh
chrome.alarms.create("burst-status-check", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "burst-status-check") checkBurstStatus();
});

setInterval(checkBurstStatus, 10000);
checkBurstStatus();

// ── Toolbar Icon Click ────────────────────────────────────────────────────────
// No popup or dropdown: clicking icon brings Burst window to front if running
if (actionApi && actionApi.onClicked) {
  actionApi.onClicked.addListener(async () => {
    const online = await checkBurstStatus();
    if (online) {
      try {
        await fetch(`${API_BASE}/show`, { method: "POST" });
      } catch (e) {}
    } else {
      notify("Burst is Offline", "Open the Burst application to connect.", true);
    }
  });
}

// ── Native Messaging & HTTP Fallback ──────────────────────────────────────────

async function sendToBurst(url) {
  // 1. Try Native Messaging host
  try {
    const nativeRes = await new Promise((resolve, reject) => {
      chrome.runtime.sendNativeMessage(
        NATIVE_HOST,
        { url },
        (response) => {
          if (chrome.runtime.lastError) {
            reject(chrome.runtime.lastError.message);
          } else {
            resolve(response);
          }
        }
      );
    });
    if (nativeRes && nativeRes.success) {
      return nativeRes;
    }
  } catch (err) {
    // Native messaging host not found or not registered — fallback to HTTP API
  }

  // 2. Direct HTTP API Fallback (Port 59284)
  const isTorrent = url.startsWith("magnet:") || url.includes(".torrent");
  const endpoint = isTorrent ? `${API_BASE}/torrent/add` : `${API_BASE}/download`;
  const body = isTorrent
    ? { source: url, output_path: "C:/Burst-Downloads/" }
    : { url, output_path: "C:/Burst-Downloads/", interface_ips: [] };

  const resp = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (resp.ok) {
    const data = await resp.json().catch(() => ({}));
    return { success: true, ...data };
  } else {
    const errText = await resp.text().catch(() => "Server error");
    throw new Error(`Burst API responded ${resp.status}: ${errText}`);
  }
}

// ── Download Interception (Torrent & Magnet ONLY) ─────────────────────────────
// We ONLY intercept magnet links and .torrent files.
// PDFs and other files are NEVER interrupted — browser handles them as normal.

function isTorrentOrMagnet(downloadItem) {
  const rawUrl = downloadItem.finalUrl || downloadItem.url || "";
  const urlLower = rawUrl.toLowerCase().trim();

  // Magnet link
  if (urlLower.startsWith("magnet:")) return true;

  // URL ends with .torrent (ignoring query/hash)
  try {
    const parsed = new URL(rawUrl);
    if (parsed.pathname.toLowerCase().endsWith(".torrent")) {
      return true;
    }
  } catch (e) {
    if (urlLower.split(/[?#]/)[0].endsWith(".torrent")) {
      return true;
    }
  }

  // Filename suggested by browser ends with .torrent
  if (downloadItem.filename && downloadItem.filename.toLowerCase().endsWith(".torrent")) {
    return true;
  }

  // MIME type is torrent
  if (downloadItem.mime && downloadItem.mime.toLowerCase() === "application/x-bittorrent") {
    return true;
  }

  return false;
}

chrome.downloads.onCreated.addListener((downloadItem) => {
  if (!isBurstOnline || !isTorrentOrMagnet(downloadItem)) {
    // NOT a torrent/magnet or Burst is offline — let Firefox handle it normally
    return;
  }

  // Cancel the browser download and send to Burst instead
  chrome.downloads.cancel(downloadItem.id, () => {
    chrome.downloads.erase({ id: downloadItem.id });
  });

  const url = downloadItem.finalUrl || downloadItem.url;
  sendToBurst(url)
    .then((result) => {
      if (result && result.success) {
        setIconStatus(true);
        const name = downloadItem.filename || url.split("/").pop()?.split("?")[0] || "torrent";
        notify("Torrent queued in Burst", name);
      } else {
        notify("Burst Error", result?.error || "Unknown error", true);
      }
    })
    .catch((err) => {
      setIconStatus(false);
      notify("Burst Error", `Could not reach Burst: ${err}`, true);
    });
});

// ── Context Menu — right-click any link ──────────────────────────────────────

function setupContextMenu() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "burst-download",
      title: "Download with Burst",
      contexts: ["link"],
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info) => {
  const url = info.linkUrl || info.srcUrl;
  if (!url) return;

  try {
    const result = await sendToBurst(url);
    if (result && result.success) {
      setIconStatus(true);
      const isTorrent = url.startsWith("magnet:") || url.includes(".torrent");
      const label = isTorrent ? "Torrent queued in Burst" : "Download started in Burst";
      const detail = isTorrent
        ? "Magnet link sent"
        : (url.split("/").pop()?.split("?")[0] || url);
      notify(label, detail);
    } else {
      notify("Burst Error", result?.error || "Unknown error", true);
    }
  } catch (err) {
    setIconStatus(false);
    notify("Burst Error", `Could not reach Burst: ${err}`, true);
  }
});

// ── Notifications ─────────────────────────────────────────────────────────────

function notify(title, message, isError = false) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: isError ? "icons/icon48_gray.png" : "icons/icon48_orange.png",
    title,
    message: message.length > 80 ? message.slice(0, 80) + "…" : message,
    priority: isError ? 2 : 0,
  });
}

// ── Messages from content scripts (magnet links) ─────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SEND_URL") {
    sendToBurst(msg.url)
      .then((result) => {
        setIconStatus(true);
        sendResponse(result);
      })
      .catch((err) => {
        sendResponse({ success: false, error: err });
      });
    return true;
  }

  if (msg.type === "CHECK_STATUS") {
    checkBurstStatus().then((alive) => sendResponse({ alive }));
    return true;
  }

  if (msg.type === "SET_TURBO_STREAM") {
    if (msg.enabled) {
      enableTurboStreamProxy();
    } else {
      disableTurboStreamProxy();
    }
    sendResponse({ success: true, enabled: msg.enabled });
    return true;
  }

  if (msg.type === "OPEN_BURST_APP") {
    chrome.tabs.create({ url: API_BASE });
    return true;
  }
});

// ── Turbo Stream Proxy Controller ─────────────────────────────────────────────

function enableTurboStreamProxy() {
  const proxyApi = chrome.proxy || (typeof browser !== "undefined" && browser.proxy);
  if (!proxyApi || !proxyApi.settings) return;
  const pacData = `
    function FindProxyForURL(url, host) {
      if (shExpMatch(host, "*.googlevideo.com") || host === "googlevideo.com" ||
          shExpMatch(host, "*.ttvnw.net") || shExpMatch(host, "*.twitch.tv") ||
          shExpMatch(host, "*.nflxvideo.net")) {
        return "PROXY 127.0.0.1:59285; DIRECT";
      }
      return "DIRECT";
    }
  `;
  proxyApi.settings.set(
    { value: { mode: "pac_script", pacScript: { data: pacData } }, scope: "regular" },
    () => {
      console.log("[BURST PROXY] Turbo Stream Proxy active on 127.0.0.1:59285");
      if (actionApi && actionApi.setBadgeText) {
        actionApi.setBadgeText({ text: "⚡ON" });
        actionApi.setBadgeBackgroundColor({ color: "#ff8800" });
      }
    }
  );
}

function disableTurboStreamProxy() {
  const proxyApi = chrome.proxy || (typeof browser !== "undefined" && browser.proxy);
  if (!proxyApi || !proxyApi.settings) return;
  proxyApi.settings.clear({ scope: "regular" }, () => {
    console.log("[BURST PROXY] Turbo Stream Proxy cleared.");
    if (actionApi && actionApi.setBadgeText) {
      actionApi.setBadgeText({ text: "" });
    }
  });
}

// Restore proxy status on browser startup if user had enabled it
if (chrome.storage && chrome.storage.local) {
  chrome.storage.local.get(["turboStreamEnabled"], (res) => {
    if (res && res.turboStreamEnabled) {
      enableTurboStreamProxy();
    }
  });
}
