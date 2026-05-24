/**
 * Translates raw download error messages into clear, actionable, and friendly English.
 * 
 * @param {string} raw - The raw error string from the download backend.
 * @returns {string} - The translated user-friendly error message.
 */
export function friendlyError(raw) {
  if (!raw) return "Something went wrong";
  const r = raw.toLowerCase();
  if (r.includes("getaddrinfo failed") || r.includes("name or service not known"))
    return "Cannot reach server — check your internet connection";
  if (r.includes("ssl") || r.includes("certificate"))
    return "Secure connection failed — the server may be down";
  if (r.includes("timeout") || r.includes("timed out"))
    return "Connection timed out — server took too long to respond";
  if (r.includes("connection refused"))
    return "Server refused the connection";
  if (r.includes("404"))
    return "File not found (404) — the link may be broken";
  if (r.includes("403"))
    return "Access denied (403) — you may need permission to download this";
  if (r.includes("no space"))
    return "Not enough disk space to save the file";
  if (r.includes("permission"))
    return "Permission denied — try changing the download folder";
  // yt-dlp specific
  if (r.includes("age") && r.includes("verif"))
    return "This video requires age verification — yt-dlp cannot download it";
  if (r.includes("video unavailable") || r.includes("has been removed"))
    return "This video is unavailable or has been removed";
  if (r.includes("private video"))
    return "This video is private";
  if (r.includes("geo") && (r.includes("block") || r.includes("restrict")))
    return "This video is not available in your region";
  if (r.includes("yt-dlp not installed"))
    return "yt-dlp is not installed — video downloading is unavailable";
  return "Download failed — please check the link and try again";
}
