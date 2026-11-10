#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
1. Visit m.vidio.com → ambil cookie luws
2. GET /livestreamings?stream_type=tv_stream → semua channel
3. GET /livestreamings/{id}/stream?initialize=true → non-DRM HLS URL
4. Fallback: DRM pattern jika step 3 gagal

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
"""

import argparse
import json
import re
import sys
import time
import uuid
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.5

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

# Fallback: DRM pattern (tanpa token, selalu aktif)
STREAM_DRM_PATTERN = (
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


# ==================== STEP 1: GET LUWS COOKIE ====================
def get_luws(session) -> str:
    """Visit m.vidio.com untuk mendapatkan cookie luws."""
    log.info("Visiting %s untuk ambil cookie luws ...", SITE_URL)
    try:
        resp = session.get(SITE_URL, timeout=30)
        luws = session.cookies.get("luws")
        if luws:
            log.info("  luws: %s", luws)
            return luws
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    # Fallback: generate UUID sendiri (format sama)
    log.info("  luws tidak di-set server, generate UUID baru")
    return f"{str(uuid.uuid4()).upper()}_"


# ==================== STEP 2: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str) -> list:
    """GET /livestreamings?stream_type=tv_stream → semua channel."""
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": session.cookies.get("ahoy_visitor", ""),
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "false",
        "luws": luws,
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
    }

    log.info("Fetching %s?stream_type=tv_stream ...", url)
    try:
        resp = session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  HTTP %d", resp.status_code)
            return []
    except Exception as e:
        log.error("  Error: %s", e)
        return []

    data = resp.json()
    channels = []

    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        ch_id = item.get("id")
        if not ch_id:
            continue

        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "cover_url": attrs.get("cover_url", ""),
            "web_url": attrs.get("web_url", ""),
        })

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STEP 3: FETCH STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int) -> str:
    """
    GET /livestreamings/{id}/stream?initialize=true
    Butuh: luws header + x-client (timestamp)
    """
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client = f"{time.time()}"

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "luws": luws,
        "x-client": x_client,
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
        "Accept": "*/*",
    }

    try:
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            hls = data.get("data", {}).get("attributes", {}).get("hls", "")
            if hls:
                return hls
    except Exception:
        pass

    return ""


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        group = ch["title"]
        logo = ch["cover_url"]

        # Prioritas: non-DRM (dari API) > DRM (pattern)
        stream_url = stream_urls.get(ch_id) or STREAM_DRM_PATTERN.format(id=ch_id)

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{group}'
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
    parser.add_argument("--drm-only", action="store_true",
                        help="Skip non-DRM, langsung pakai DRM pattern")
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  /livestreamings?stream_type=tv_stream + luws cookie")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Get luws
    luws = get_luws(session)

    # Step 2: Fetch all channels
    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels[:10]:
        log.info("    [%s] %s", ch["id"], ch["title"])
    if len(channels) > 10:
        log.info("    ... +%d more", len(channels) - 10)

    # Step 3: Fetch stream URLs
    stream_urls = {}
    if not args.drm_only:
        log.info("\nFetching non-DRM stream URLs ...")
        for i, ch in enumerate(channels, 1):
            ch_id = ch["id"]
            hls = fetch_stream_url(session, luws, ch_id)
            if hls:
                stream_urls[ch_id] = hls
                log.info("  [%d/%d] %s ✓ (non-DRM)", i, len(channels), ch["title"])
            else:
                log.warning("  [%d/%d] %s ✗ (fallback DRM)", i, len(channels), ch["title"])
            time.sleep(DELAY)

        log.info("\n  Non-DRM: %d/%d", len(stream_urls), len(channels))

    # Step 4: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
