// Burst Extension — background.js
// Intercepts downloads and magnet links, routes them to Burst via native messaging.

const NATIVE_HOST = "com.burst.download.manager";

// ── Native messaging ──────────────────────────────────────────────────────────

function sendToBurst(url) {
  return new Promise((resolve, reject) => {
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
}

// ── Download interception ─────────────────────────────────────────────────────

chrome.downloads.onCreated.addListener(async (downloadItem) => {
  // Check if Burst interception is enabled
  const { burstEnabled } = await chrome.storage.local.get({ burstEnabled: true });
  if (!burstEnabled) return;

  const url = downloadItem.url;

  // Only intercept real file downloads, not page navigations
  const isFileDownload =
    downloadItem.filename ||
    /\.(zip|rar|7z|tar|gz|exe|msi|iso|mp4|mkv|mp3|pdf|apk|dmg|bin|img)(\?|$)/i.test(url);

  if (!isFileDownload) return;

  // Cancel the browser's own download
  chrome.downloads.cancel(downloadItem.id);
  chrome.downloads.erase({ id: downloadItem.id });

  try {
    const result = await sendToBurst(url);
    if (result.success) {
      notify("Download started in Burst", url.split("/").pop()?.split("?")[0] || url);
    } else {
      notify("Burst Error", result.error || "Unknown error", true);
    }
  } catch (err) {
    notify("Burst Error", `Could not reach Burst: ${err}`, true);
  }
});

// ── Context menu — right-click any link ──────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "burst-download-link",
    title: "Download with Burst",
    contexts: ["link"],
  });

  chrome.contextMenus.create({
    id: "burst-download-magnet",
    title: "Send Magnet to Burst",
    contexts: ["link"],
    targetUrlPatterns: ["magnet:*"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  const url = info.linkUrl;
  if (!url) return;

  try {
    const result = await sendToBurst(url);
    if (result.success) {
      const label = url.startsWith("magnet:") ? "Torrent queued in Burst" : "Download started in Burst";
      notify(label, url.startsWith("magnet:") ? "Magnet link sent" : url.split("/").pop() || url);
    } else {
      notify("Burst Error", result.error || "Unknown error", true);
    }
  } catch (err) {
    notify("Burst Error", `Could not reach Burst: ${err}`, true);
  }
});

// ── Notifications ─────────────────────────────────────────────────────────────

function notify(title, message, isError = false) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: isError ? "icons/icon48.png" : "icons/icon48.png",
    title,
    message: message.length > 80 ? message.slice(0, 80) + "…" : message,
    priority: isError ? 2 : 0,
  });
}

// ── Messages from popup ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SEND_URL") {
    sendToBurst(msg.url)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ success: false, error: err }));
    return true; // keep channel open for async
  }

  if (msg.type === "CHECK_STATUS") {
    chrome.runtime.sendNativeMessage(
      NATIVE_HOST,
      { url: "__ping__" },
      (response) => {
        // Any response (even an error about __ping__) means the host is alive
        const alive = !chrome.runtime.lastError;
        sendResponse({ alive });
      }
    );
    return true;
  }
});
