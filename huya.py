#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (via Proxy)
Semua API call lewat proxy (IP server tercatat)
"""

import json
import sys
import time
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
API_PROXY = "https://exsample.d-n-s.name/api.php"
STREAM_PROXY = "https://exsample.d-n-s.name/ch.m3u8"
PROXY_SECRET = "ganti-dengan-secret-panjang"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.3

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://m.vidio.com"
REFERER = "https://m.vidio.com/"

DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)
# =====================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("vidio-m3u")


def api_get(params: dict) -> dict:
    """Fetch via API proxy."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_PROXY}?auth={PROXY_SECRET}&{qs}"
    resp = cffi_requests.get(url, impersonate=IMPERSONATE, timeout=30)
    if resp.status_code != 200:
        log.warning("  API error: %d — %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def fetch_all_channels() -> list:
    """Fetch semua channel via proxy."""
    log.info("Fetching channels via proxy...")
    channels = {}
    page = 1

    while True:
        data = api_get({"action": "channels", "page": page})
        if not data:
            break

        sections = data.get("data", [])
        if isinstance(sections, dict):
            sections = [sections]

        for section in sections:
            attrs = section.get("attributes", {})
            section_title = attrs.get("title", "Lainnya")
            contents = attrs.get("contents", [])

            for item in contents:
                if item.get("content_type") != "livestreaming":
                    continue
                ch_id = item.get("content_id")
                if not ch_id or ch_id in channels:
                    continue

                channels[ch_id] = {
                    "id": int(ch_id),
                    "title": item.get("alt_title") or item.get("title", "Unknown"),
                    "logo": item.get("cover_url", ""),
                    "group": section_title,
                    "stream_url": item.get("stream_url", ""),
                }

        links = data.get("links", {})
        if links.get("next"):
            page += 1
            time.sleep(DELAY)
        else:
            break

    result = list(channels.values())
    log.info("  Found %d channels", len(result))
    return result


def fetch_stream_url(ch_id: int) -> str:
    """Fetch stream URL via proxy."""
    data = api_get({"action": "stream", "id": ch_id})
    return data.get("data", {}).get("attributes", {}).get("hls", "")


def push_to_proxy(channels: list):
    """Push stream URLs ke proxy (cache)."""
    body = {"channels": [{"id": ch["id"], "url": ch.get("stream_url", "")} for ch in channels if ch.get("stream_url")]}
    if not body["channels"]:
        return {"ok": False, "count": 0}
    url = f"{STREAM_PROXY}?auth={PROXY_SECRET}"
    resp = cffi_requests.post(url, json=body, impersonate=IMPERSONATE, timeout=30)
    if resp.status_code != 200:
        log.warning("  Push gagal: %d", resp.status_code)
        return {"error": resp.text[:200]}
    return resp.json()


def generate_m3u(channels: list, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = ch.get("group", "Lainnya")

        # URL: proxy upstream (refresh otomatis)
        stream_url = f"{STREAM_PROXY}?ch={ch_id}"

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={UA}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append(f'#EXTVLCOPT:http-referer={REFERER}')
        lines.append(f'#KODIPROP:inputstream.adaptive.user_agent={UA}')
        lines.append(f'#KODIPROP:inputstream.adaptive.headers=Origin:{ORIGIN};Referer:{REFERER}')
        lines.append(stream_url)
        lines.append("")
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


def main():
    print("=" * 55)
    print("  Vidio Live TV → M3U (via Proxy)")
    print("  API: " + API_PROXY)
    print("  Stream: " + STREAM_PROXY)
    print("=" * 55)

    # Step 1: Fetch channels via proxy
    channels = fetch_all_channels()
    if not channels:
        log.error("Tidak ada channel.")
        sys.exit(1)

    # Group summary
    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups:")
    for g, names in groups.items():
        log.info("    [%s] %d: %s", g, len(names), ", ".join(names[:5]) + ("..." if len(names) > 5 else ""))

    # Step 2: Fetch stream URLs (hanya yang kosong)
    need_fetch = [ch for ch in channels if not ch.get("stream_url")]
    if need_fetch:
        log.info("\nFetching stream URLs for %d channels...", len(need_fetch))
        for i, ch in enumerate(need_fetch, 1):
            url = fetch_stream_url(ch["id"])
            if url:
                ch["stream_url"] = url
                log.info("  [%d/%d] %s ✓", i, len(need_fetch), ch["title"])
            else:
                ch["stream_url"] = DRM_PATTERN.format(id=ch["id"])
                log.warning("  [%d/%d] %s → DRM fallback", i, len(need_fetch), ch["title"])
            time.sleep(DELAY)

    # Step 3: Push ke proxy (cache)
    log.info("\nPushing to proxy...")
    result = push_to_proxy(channels)
    log.info("  Proxy: %s", result)

    # Step 4: Generate M3U
    log.info("\nGenerating M3U...")
    generate_m3u(channels, OUTPUT_FILE)


if __name__ == "__main__":
    main()   
