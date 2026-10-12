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
  badge.innerHTML = '<span style="color: #ff8800; margin-right: 3px; font-size: 11px;">⚡</span><span style="color: #e4e4e7; font-weight: 500;">Burst</span>';
  badge.title = "Download with Burst Multi-WAN Acceleration";
  badge.setAttribute("data-burst-url", targetUrl);

  // Subtle, neutral styling that looks native on any website
  Object.assign(badge.style, {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    marginLeft: "6px",
    padding: "1px 7px",
    fontSize: "11px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    lineHeight: "15px",
    background: "#1e1e24",
    border: "1px solid rgba(255, 136, 0, 0.35)",
    borderRadius: "6px",
    cursor: "pointer",
    verticalAlign: "middle",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.2)",
    transition: "all 0.15s ease",
    userSelect: "none",
    zIndex: "9999",
  });

  badge.addEventListener("mouseenter", () => {
    badge.style.background = "#2a2a32";
    badge.style.borderColor = "#ff8800";
    badge.style.transform = "translateY(-1px)";
    badge.style.boxShadow = "0 2px 6px rgba(255, 136, 0, 0.25)";
  });

  badge.addEventListener("mouseleave", () => {
    badge.style.background = "#1e1e24";
    badge.style.borderColor = "rgba(255, 136, 0, 0.35)";
    badge.style.transform = "translateY(0)";
    badge.style.boxShadow = "0 1px 2px rgba(0, 0, 0, 0.2)";
  });

  badge.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();

    const originalHtml = badge.innerHTML;
    badge.innerHTML = '<span style="color: #60a5fa; margin-right: 3px;">⚡</span><span style="color: #60a5fa; font-weight: 500;">Sending...</span>';
    badge.style.borderColor = "#3b82f6";

    chrome.runtime.sendMessage({ type: "SEND_URL", url: targetUrl }, (response) => {
      badge.innerHTML = '<span style="color: #34d399; margin-right: 3px;">✓</span><span style="color: #34d399; font-weight: 500;">Sent to Burst</span>';
      badge.style.borderColor = "#10b981";
      badge.style.background = "#18261e";

      setTimeout(() => {
        badge.innerHTML = originalHtml;
        badge.style.borderColor = "rgba(255, 136, 0, 0.35)";
        badge.style.background = "#1e1e24";
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
