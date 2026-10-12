// Burst Extension — background.js
// Minimal extension: icon status (orange/gray).
// Intercepts ONLY magnet links and .torrent files.
// All other files (PDFs, documents, media, executables, web pages, etc.) download normally in browser.
// No popup, no settings, no dropdown.

const NATIVE_HOST = "com.burst.download.manager";
const API_BASE = "http://127.0.0.1:59284";

const actionApi = chrome.action || (typeof browser !== "undefined" && browser.action);

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

chrome.downloads.onDeterminingFilename.addListener((downloadItem, suggest) => {
  if (!isBurstOnline || !isTorrentOrMagnet(downloadItem)) {
    // NOT a torrent/magnet or Burst is offline — allow browser to download normally
    suggest();
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
    const mode = msg.mode || "video";
    if (msg.enabled) {
      enableTurboStreamProxy(mode);
    } else {
      disableTurboStreamProxy();
    }
    // Sync with Burst backend
    fetch(`${API_BASE}/proxy/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !!msg.enabled }),
    }).catch(() => {});

    sendResponse({ success: true, enabled: msg.enabled, mode });
    return true;
  }

  if (msg.type === "OPEN_BURST_APP") {
    chrome.tabs.create({ url: API_BASE });
    return true;
  }
});

// ── Turbo Stream Proxy Controller ─────────────────────────────────────────────

function enableTurboStreamProxy(mode = "video") {
  const proxyApi = (typeof browser !== "undefined" && browser.proxy) ? browser.proxy : chrome.proxy;
  if (!proxyApi || !proxyApi.settings) {
    console.warn("[BURST PROXY] proxy.settings API not available in this browser.");
    return;
  }

  // Detect Firefox / Zen Browser
  const isFirefox = (typeof browser !== "undefined" && browser.proxy && browser.proxy.settings && (typeof window === "undefined" || !window.chrome)) ||
                    (typeof navigator !== "undefined" && /firefox|zen/i.test(navigator.userAgent || ""));

  if (isFirefox) {
    let ffValue;
    if (mode === "all") {
      ffValue = {
        proxyType: "manual",
        http: "http://127.0.0.1:59285",
        ssl: "http://127.0.0.1:59285",
        passthrough: "127.0.0.1, localhost, 127.0.0.1:59284"
      };
    } else {
      const pacData = `function FindProxyForURL(url, host) {
        if (
          dnsDomainIs(host, ".googlevideo.com") || host === "googlevideo.com" ||
          dnsDomainIs(host, ".youtube.com") || host === "youtube.com" ||
          dnsDomainIs(host, ".ytimg.com") || host === "ytimg.com" ||
          dnsDomainIs(host, ".ggpht.com") || host === "ggpht.com" ||
          dnsDomainIs(host, ".twitch.tv") || host === "twitch.tv" ||
          dnsDomainIs(host, ".ttvnw.net") || host === "ttvnw.net" ||
          dnsDomainIs(host, ".netflix.com") || host === "netflix.com" ||
          dnsDomainIs(host, ".nflxvideo.net") || host === "nflxvideo.net" ||
          shExpMatch(url, "*videoplayback*") ||
          shExpMatch(url, "*.m3u8*") ||
          shExpMatch(url, "*.mpd*")
        ) {
          return "PROXY 127.0.0.1:59285; DIRECT";
        }
        return "DIRECT";
      }`;
      const pacDataUri = "data:application/x-ns-proxy-autoconfig;base64," + btoa(pacData);
      ffValue = {
        proxyType: "autoConfig",
        autoConfigUrl: pacDataUri
      };
    }

    proxyApi.settings.set({ value: ffValue }).then(() => {
      console.log(`[BURST PROXY] Turbo Stream Proxy active (Firefox/Zen, ${mode}) on 127.0.0.1:59285`);
      chrome.storage.local.remove(["proxyError"]);
      if (actionApi && actionApi.setBadgeText) {
        actionApi.setBadgeText({ text: mode === "all" ? "⚡ALL" : "⚡ON" });
        actionApi.setBadgeBackgroundColor({ color: "#ff8800" });
      }
    }).catch((err) => {
      console.error("[BURST PROXY] Error setting proxy in Firefox/Zen:", err);
      chrome.storage.local.set({ proxyError: err.message || String(err) });
    });
    return;
  }

  // Chromium / Chrome
  let proxyConfig;
  if (mode === "all") {
    proxyConfig = {
      mode: "fixed_servers",
      rules: {
        singleProxy: {
          scheme: "http",
          host: "127.0.0.1",
          port: 59285
        },
        bypassList: ["<local>", "127.0.0.1:59284", "localhost:59284"]
      }
    };
  } else {
    const pacData = `
      function FindProxyForURL(url, host) {
        if (
          dnsDomainIs(host, ".googlevideo.com") || host === "googlevideo.com" ||
          dnsDomainIs(host, ".youtube.com") || host === "youtube.com" ||
          dnsDomainIs(host, ".ytimg.com") || host === "ytimg.com" ||
          dnsDomainIs(host, ".ggpht.com") || host === "ggpht.com" ||
          dnsDomainIs(host, ".twitch.tv") || host === "twitch.tv" ||
          dnsDomainIs(host, ".ttvnw.net") || host === "ttvnw.net" ||
          dnsDomainIs(host, ".jtvnw.net") || host === "jtvnw.net" ||
          dnsDomainIs(host, ".netflix.com") || host === "netflix.com" ||
          dnsDomainIs(host, ".nflxvideo.net") || host === "nflxvideo.net" ||
          dnsDomainIs(host, ".nflxext.com") || host === "nflxext.com" ||
          dnsDomainIs(host, ".vimeo.com") || host === "vimeo.com" ||
          dnsDomainIs(host, ".dailymotion.com") || host === "dailymotion.com" ||
          shExpMatch(url, "*videoplayback*") ||
          shExpMatch(url, "*.m3u8*") ||
          shExpMatch(url, "*.mpd*")
        ) {
          return "PROXY 127.0.0.1:59285; DIRECT";
        }
        return "DIRECT";
      }
    `;
    proxyConfig = {
      mode: "pac_script",
      pacScript: { data: pacData }
    };
  }

  chrome.proxy.settings.set(
    { value: proxyConfig, scope: "regular" },
    () => {
      if (chrome.runtime.lastError) {
        const err = chrome.runtime.lastError.message;
        console.error("[BURST PROXY] Error setting proxy:", err);
        chrome.storage.local.set({ proxyError: err });
      } else {
        console.log(`[BURST PROXY] Turbo Stream Proxy active (${mode}) on 127.0.0.1:59285`);
        chrome.storage.local.remove(["proxyError"]);
        if (actionApi && actionApi.setBadgeText) {
          actionApi.setBadgeText({ text: mode === "all" ? "⚡ALL" : "⚡ON" });
          actionApi.setBadgeBackgroundColor({ color: "#ff8800" });
        }
      }
    }
  );

  // Strip Alt-Svc response headers so Chrome does NOT bypass the proxy via UDP QUIC
  if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.updateDynamicRules) {
    chrome.declarativeNetRequest.updateDynamicRules({
      addRules: [
        {
          id: 59285,
          priority: 1,
          action: {
            type: "modifyHeaders",
            responseHeaders: [{ header: "alt-svc", operation: "remove" }]
          },
          condition: {
            urlFilter: "*",
            resourceTypes: ["xmlhttprequest", "media", "main_frame", "sub_frame", "other"]
          }
        }
      ],
      removeRuleIds: [59285]
    }).catch((e) => console.debug("[BURST PROXY] declarativeNetRequest not available:", e));
  }
}

function disableTurboStreamProxy() {
  const proxyApi = (typeof browser !== "undefined" && browser.proxy) ? browser.proxy : chrome.proxy;
  if (!proxyApi || !proxyApi.settings) return;

  const isFirefox = (typeof browser !== "undefined" && browser.proxy && browser.proxy.settings && (typeof window === "undefined" || !window.chrome)) ||
                    (typeof navigator !== "undefined" && /firefox|zen/i.test(navigator.userAgent || ""));

  if (isFirefox) {
    proxyApi.settings.set({ value: { proxyType: "system" } }).catch(() => {});
  } else {
    chrome.proxy.settings.clear({ scope: "regular" }, () => {});
  }

  if (actionApi && actionApi.setBadgeText) {
    actionApi.setBadgeText({ text: "" });
  }

  if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.updateDynamicRules) {
    chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [59285]
    }).catch(() => {});
  }

  chrome.storage.local.remove(["proxyError"]);

  fetch(`${API_BASE}/proxy/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: false }),
  }).catch(() => {});
}

// Restore proxy status on browser startup if user had enabled it
if (chrome.storage && chrome.storage.local) {
  chrome.storage.local.get(["turboStreamEnabled", "turboStreamMode"], (res) => {
    if (res && res.turboStreamEnabled) {
      enableTurboStreamProxy(res.turboStreamMode || "video");
    }
  });
}

