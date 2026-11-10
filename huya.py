#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
Fetch RSC via curl_cffi, extract stream_url (atau generate dari pattern).

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
"""

import argparse
import json
import re
import sys
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
OUTPUT_FILE = "playlist.m3u"
LIVE_URL = "https://m.vidio.com/live"
IMPERSONATE = "chrome"

# Pattern stream URL (DRM - tanpa token, selalu aktif)
STREAM_PATTERN = "https://etslive-v3-vidio-com-tokenized.akamaized.net/stream/{id}/file/drm/hls/master.m3u8"

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


# ==================== PARSING RSC ====================
def extract_channels(rsc_text: str) -> list:
    """Parse RSC untuk semua livestreaming schedule."""
    channels = {}

    for match in re.finditer(r'"content_type"\s*:\s*"livestreaming_schedule"', rsc_text):
        start = rsc_text.rfind('{', 0, match.start())
        depth = 0
        end = start
        for i in range(start, len(rsc_text)):
            if rsc_text[i] == '{':
                depth += 1
            elif rsc_text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        json_str = rsc_text[start:end]
        try:
            obj = json.loads(json_str)
            meta = obj.get("links", {}).get("self", {}).get("meta", {})
            ls_id = meta.get("livestreaming_id")
            if ls_id and ls_id not in channels:
                channels[ls_id] = {
                    "livestreaming_id": ls_id,
                    "schedule_id": meta.get("schedule_id"),
                    "title": obj.get("title", "Unknown"),
                    "cover_url": obj.get("cover_url", ""),
                    "livestreaming_title": obj.get("livestreaming_title", "Unknown"),
                    "stream_url": obj.get("stream_url", ""),
                }
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue

    return list(channels.values())


# ==================== STREAM URL ====================
def get_stream_url(ch: dict) -> str:
    """
    Ambil stream_url dari RSC, jika kosong generate dari pattern.
    Pattern: https://etslive-v3-vidio-com-tokenized.akamaized.net/stream/{id}/file/drm/hls/master.m3u8
    """
    url = ch.get("stream_url", "")
    if url:
        return url
    # Generate dari pattern (selalu aktif, tanpa token/expiry)
    return STREAM_PATTERN.format(id=ch["livestreaming_id"])


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ls_id = ch["livestreaming_id"]
        title = ch["title"]
        group = ch["livestreaming_title"]
        logo = ch["cover_url"]
        stream_url = get_stream_url(ch)

        if not stream_url:
            log.warning("  [SKIP] %s | %s", group, title)
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ls_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{group} | {title}'
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
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)
    headers = {
        "RSC": "1",
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.6",
        "Referer": REFERER,
    }

    log.info("Fetching %s ...", LIVE_URL)
    try:
        resp = session.get(LIVE_URL, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.error("HTTP %d", resp.status_code)
            sys.exit(1)
    except Exception as e:
        log.error("Fetch failed: %s", e)
        sys.exit(1)

    rsc_text = resp.text
    log.info("RSC: %d bytes", len(rsc_text))

    channels = extract_channels(rsc_text)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("Found %d channels:", len(channels))
    for ch in channels:
        src = "RSC" if ch.get("stream_url") else "pattern"
        log.info("  [%s] %s | %s  (stream: %s)",
                 ch["livestreaming_id"],
                 ch["livestreaming_title"],
                 ch["title"],
                 src)

    generate_m3u(channels, args.output)


if __name__ == "__main__":
    main()   
