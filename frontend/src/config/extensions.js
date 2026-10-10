/**
 * Official browser extension store URLs and URL validation utilities.
 */

export const BROWSER_EXTENSION_URLS = {
  chrome: "https://chromewebstore.google.com/detail/burst-download-manager/dgikbcilhfjcdaemdnadnokpmfejhcjb",
  firefox: "https://addons.mozilla.org/en-US/firefox/addon/burst-download-manager/",
};

const ALLOWED_STORE_HOSTS = new Set([
  "chromewebstore.google.com",
  "addons.mozilla.org",
]);

/**
 * Validates that an extension URL is safe, uses HTTPS, and points to a known official store.
 * @param {string} url
 * @returns {boolean}
 */
export function isValidStoreUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    return ALLOWED_STORE_HOSTS.has(parsed.hostname.toLowerCase());
  } catch {
    return false;
  }
}

/**
 * Safely opens a store URL in the user's default browser.
 * @param {string} url
 * @param {function} [onToast] Optional toast notification callback for error feedback
 */
export function openStoreUrl(url, onToast) {
  if (!isValidStoreUrl(url)) {
    if (onToast) onToast("Extension store URL is not configured or invalid.");
    console.error("Invalid extension store URL attempted:", url);
    return;
  }

  try {
    window.open(url, "_blank");
  } catch (err) {
    console.error("Failed to open extension store URL:", err);
    if (onToast) onToast("Failed to open browser store link.");
  }
}
