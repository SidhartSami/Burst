// Burst Extension — content.js
// 1. Intercepts magnet: link clicks.
// 2. Sniffs downloadable file links (.zip, .exe, .iso, .tar, .mp4, etc.)
//    and injects a 1-click "⚡ Burst" badge for instant accelerated downloading.

const DOWNLOAD_EXTENSIONS_REGEX =
  /\.(zip|rar|7z|tar|gz|tgz|bz2|xz|iso|img|bin|exe|msi|dmg|pkg|deb|rpm|appimage|apk|mp4|mkv|mov|avi|flac|wav)(\?.*)?$/i;

function isDownloadableLink(anchor) {
  const href = anchor.href || "";
  if (!href || href.startsWith("javascript:") || href.startsWith("#")) return false;
  if (href.startsWith("magnet:")) return false; // Handled separately
  if (anchor.hasAttribute("download")) return true;
  try {
    const url = new URL(href, window.location.href);
    return DOWNLOAD_EXTENSIONS_REGEX.test(url.pathname);
  } catch {
    return DOWNLOAD_EXTENSIONS_REGEX.test(href);
  }
}

function createBurstBadge(targetUrl) {
  const badge = document.createElement("span");
  badge.className = "burst-download-badge";
  badge.textContent = "⚡ Burst";
  badge.title = "Download with Burst Multi-WAN Acceleration";
  badge.setAttribute("data-burst-url", targetUrl);

  // Styling
  Object.assign(badge.style, {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    marginLeft: "6px",
    padding: "1px 7px",
    fontSize: "11px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    fontWeight: "600",
    color: "#ffffff",
    background: "linear-gradient(135deg, #ff6b00 0%, #ff8800 100%)",
    borderRadius: "10px",
    cursor: "pointer",
    verticalAlign: "middle",
    boxShadow: "0 1px 3px rgba(0,0,0,0.25)",
    transition: "transform 0.15s ease, background 0.15s ease",
    userSelect: "none",
    lineHeight: "16px",
    zIndex: "9999",
  });

  badge.addEventListener("mouseenter", () => {
    badge.style.transform = "scale(1.08)";
    badge.style.boxShadow = "0 2px 5px rgba(255, 107, 0, 0.4)";
  });

  badge.addEventListener("mouseleave", () => {
    badge.style.transform = "scale(1)";
    badge.style.boxShadow = "0 1px 3px rgba(0,0,0,0.25)";
  });

  badge.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();

    const originalText = badge.textContent;
    badge.textContent = "⚡ Sending...";
    badge.style.background = "#3b82f6";

    chrome.runtime.sendMessage({ type: "SEND_URL", url: targetUrl }, (response) => {
      if (response && response.success) {
        badge.textContent = "⚡ Sent!";
        badge.style.background = "#10b981";
      } else {
        badge.textContent = "⚡ Sent to Burst";
        badge.style.background = "#ff8800";
      }
      setTimeout(() => {
        badge.textContent = originalText;
        badge.style.background = "linear-gradient(135deg, #ff6b00 0%, #ff8800 100%)";
      }, 2500);
    });
  });

  return badge;
}

function attachBadges(container = document) {
  const anchors = container.querySelectorAll("a[href]");
  for (const anchor of anchors) {
    if (anchor.getAttribute("data-burst-scanned")) continue;
    anchor.setAttribute("data-burst-scanned", "true");

    if (isDownloadableLink(anchor)) {
      const badge = createBurstBadge(anchor.href);
      if (anchor.nextSibling) {
        anchor.parentNode.insertBefore(badge, anchor.nextSibling);
      } else {
        anchor.parentNode.appendChild(badge);
      }
    }
  }
}

// ── Magnet Click Interception ────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const anchor = e.target.closest("a");
  if (!anchor) return;

  const href = anchor.href || "";
  if (!href.startsWith("magnet:")) return;

  e.preventDefault();
  e.stopImmediatePropagation();

  chrome.runtime.sendMessage({ type: "SEND_URL", url: href }, (response) => {
    if (response?.success) {
      const original = anchor.textContent;
      anchor.textContent = "⚡ Sent to Burst!";
      setTimeout(() => (anchor.textContent = original), 2000);
    }
  });
}, true);

// Initial badge attachment
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => attachBadges());
} else {
  attachBadges();
}

// Observer for dynamically injected links (GitHub, SourceForge, etc.)
let scanTimeout = null;
const observer = new MutationObserver(() => {
  if (scanTimeout) clearTimeout(scanTimeout);
  scanTimeout = setTimeout(() => {
    attachBadges();
  }, 400);
});
observer.observe(document.body || document.documentElement, {
  childList: true,
  subtree: true,
});
