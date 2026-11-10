#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
Gunakan API /categories/live/sections untuk ambil SEMUA channel.

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
"""

import argparse
import json
import sys
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
PAGE_SIZE = 50  # channel per request

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

# Pattern stream URL (DRM, tanpa token, selalu aktif)
STREAM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== FETCH CHANNELS ====================
def fetch_all_channels(session) -> list:
    """Fetch semua channel dari /categories/live/sections dengan pagination."""
    channels = {}
    page = 1

    while True:
        url = f"{API_BASE}/categories/live/sections"
        params = {
            "page[number]": page,
            "page[size]": PAGE_SIZE,
            "included": "contents",
        }
        headers = {
            "x-api-key": API_KEY,
            "x-api-platform": "web-mobile",
            "x-secure-level": "2",
            "x-visitor-id": "3489c20a-d32e-468a-bd1b-8f7d150fa132",
            "x-api-app-info": "js/m.vidio.com",
            "x-user-login": "false",
            "Accept": "application/json",
            "Content-Type": "application/vnd.api+json",
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Origin": ORIGIN,
        }

        log.info("  Fetching page %d ...", page)
        try:
            resp = session.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code != 200:
                log.warning("  HTTP %d, stopping pagination", resp.status_code)
                break
        except Exception as e:
            log.error("  Fetch error: %s", e)
            break

        data = resp.json()

        # Parse included contents (livestreaming)
        included = data.get("included", [])
        new_count = 0
        for item in included:
            if item.get("type") != "content":
                continue
            attrs = item.get("attributes", {})
            if attrs.get("content_type") != "livestreaming":
                continue

            content_id = attrs.get("content_id")
            if not content_id or content_id in channels:
                continue

            channels[content_id] = {
                "id": content_id,
                "title": attrs.get("alt_title", attrs.get("title", "Unknown")),
                "program": attrs.get("title", ""),
                "cover_url": attrs.get("cover_url", ""),
                "web_url": attrs.get("web_url", ""),
            }
            new_count += 1

        log.info("  Page %d: %d new channels (total: %d)", page, new_count, len(channels))

        # Cek pagination
        links = data.get("links", {})
        next_url = links.get("next")
        if not next_url and new_count == 0:
            break
        if new_count < PAGE_SIZE:
            break
        page += 1

    return list(channels.values())


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        group = ch["title"]       # nama channel (Mentari TV, Indosiar, dll)
        program = ch["program"]   # program saat ini
        logo = ch["cover_url"]
        stream_url = STREAM_PATTERN.format(id=ch_id)

        # Title: "Channel | Program"
        display_title = f"{group} | {program}" if program and program != group else group

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{display_title}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Source: /categories/live/sections (ALL channels)")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    log.info("Fetching all channels ...")
    channels = fetch_all_channels(session)

    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("\n  Total: %d channels", len(channels))
    for ch in channels[:10]:
        log.info("    [%s] %s | %s", ch["id"], ch["title"], ch["program"])
    if len(channels) > 10:
        log.info("    ... +%d more", len(channels) - 10)

    log.info("\nGenerating M3U ...")
    generate_m3u(channels, args.output)


if __name__ == "__main__":
    main()   
