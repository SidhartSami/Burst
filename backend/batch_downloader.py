"""Burst — Batch download scanner: extracts download links from a webpage."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# Common file extensions that are worth downloading
DOWNLOADABLE_EXTENSIONS = {
    ".exe", ".msi", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flac", ".wav",
    ".apk", ".ipa", ".dmg", ".pkg", ".deb", ".rpm",
    ".iso", ".img", ".dmg",
    # Video platforms often use these
    ".m3u8", ".ts",
}


def is_downloadable_url(url: str) -> bool:
    """Return True if a URL looks like a file worth batch-downloading."""
    parsed = urlparse(url.lower())
    path = parsed.path
    return any(path.endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS)


def guess_filename(url: str) -> str:
    """Extract a reasonable filename from a URL."""
    parsed = urlparse(url)
    filename = parsed.path.split("/")[-1]
    if not filename or "/" not in parsed.path:
        filename = f"download_{hash(url) % 100000}"
    filename = filename.split("?")[0]
    return filename or "download.bin"


async def scan_page(url: str, timeout: float = 15.0) -> Dict[str, Any]:
    """
    Fetch a URL and extract all downloadable links from it.
    Returns {urls: [{url, filename, size_estimate}], total: int, error: Optional[str]}
    Includes retry logic, bot-detection-aware headers, and download-attribute support.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    async with aiohttp.ClientSession() as session:
        last_error = None

        for attempt in range(2):
            try:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        # Retry on non-200
                        await asyncio.sleep(2)
                        continue

                    content_type = resp.headers.get("Content-Type", "")
                    text = await resp.text()

                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        return {"urls": [], "total": 0, "error": f"Not an HTML page (Content-Type: {content_type})"}

                    soup = BeautifulSoup(text, "html.parser")

                    found: List[Dict[str, str]] = []
                    seen = set()

                    # Extract from <a href> with known file extension
                    for tag in soup.find_all("a", href=True):
                        href = tag["href"]
                        full_url = urljoin(url, href)
                        if full_url.startswith("http") and full_url not in seen:
                            seen.add(full_url)
                            if is_downloadable_url(full_url):
                                found.append({"url": full_url, "filename": guess_filename(full_url), "size_estimate": None})

                    # Extract from <a download> tags regardless of extension (explicit download markers)
                    for tag in soup.find_all("a", href=True, download=True):
                        href = tag["href"]
                        full_url = urljoin(url, href)
                        if full_url.startswith("http") and full_url not in seen:
                            seen.add(full_url)
                            filename = tag.get("download")
                            if isinstance(filename, str) and filename:
                                pass  # use the download attr value as filename
                            else:
                                filename = guess_filename(full_url)
                            found.append({"url": full_url, "filename": filename, "size_estimate": None})

                    # Also extract from <video src>, <source src>
                    for tag in soup.find_all(["video", "source"], src=True):
                        src = tag["src"]
                        full_url = urljoin(url, src)
                        if full_url.startswith("http") and full_url not in seen:
                            seen.add(full_url)
                            found.append({"url": full_url, "filename": guess_filename(full_url), "size_estimate": None})

                    # If no <a href> tags found at all, it's likely a JS-rendered page
                    if not soup.find_all("a", href=True):
                        return {
                            "urls": [],
                            "total": 0,
                            "error": "Page returned no links — may require JavaScript to load",
                        }

                    # Deduplicate by URL
                    unique = []
                    unique_urls = set()
                    for item in found:
                        if item["url"] not in unique_urls:
                            unique_urls.add(item["url"])
                            unique.append(item)

                    return {"urls": unique, "total": len(unique), "error": None}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e) if isinstance(e, aiohttp.ClientError) else "Timeout"
                if attempt == 0:
                    await asyncio.sleep(2)
                continue
            except Exception as e:
                return {"urls": [], "total": 0, "error": f"Unexpected error: {e}"}

        return {"urls": [], "total": 0, "error": f"Scan failed after retries: {last_error}"}