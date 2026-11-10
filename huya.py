#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (Hybrid)
================================================
Playwright (ambil luws) → curl_cffi (semua API calls)

Dependencies:
    pip install "curl-cffi<0.14" playwright
    playwright install chromium --with-deps

Run:
    python vidio_m3u.py
    python vidio_m3u.py --luws "1EA8FFB6-2EA1-317B-91DF-F3625D79BB13_"
"""

import argparse
import hashlib
import hmac
import json
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

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.5
RETRY_DELAY = 3.0
RETRY_WAIT = 5

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
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


# ==================== X-SIGNATURE ====================
def generate_x_signature() -> tuple:
    """
    x-client = float timestamp (seperti browser)
    x-signature = HMAC-SHA256(key="V1d10D3v:{int_t}", msg=x_client)
    """
    t_float = time.time()
    t_int = int(t_float)
    x_client = f"{t_float}"
    secret_key = f"V1d10D3v:{t_int}"
    signature = hmac.new(
        secret_key.encode(),
        x_client.encode(),
        hashlib.sha256
    ).hexdigest()
    return x_client, signature


# ==================== STEP 1: GET LUWS ====================
def get_luws_playwright() -> str:
    """
    Load m.vidio.com di Playwright → JS set document.cookie luws → ambil.
    Hanya butuh 1 page load (~3-5 detik).
    """
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright tidak tersedia")
        return ""

    log.info("Mengambil luws via Playwright ...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 412, "height": 915},
            locale="id-ID",
        )
        page = context.new_page()
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=30000)

        # Tunggu JS set cookie luws
        for _ in range(10):
            cookies = context.cookies()
            for c in cookies:
                if c["name"] == "luws":
                    browser.close()
                    return c["value"]
            time.sleep(1)

        browser.close()

    return ""


def get_luws(manual: str = "") -> str:
    """
    Urutan:
    1. Manual (--luws)
    2. Playwright (otomatis)
    3. Random UUID (fallback, 6 channel besar gagal)
    """
    if manual:
        log.info("  luws (manual): %s", manual)
        return manual

    # Coba Playwright
    luws = get_luws_playwright()
    if luws:
        log.info("  luws (playwright): %s", luws)
        return luws

    # Fallback
    luws = f"{str(uuid.uuid4()).upper()}_"
    log.warning("  luws (random): %s", luws)
    log.warning("  ⚠ 6 channel besar mungkin gagal")
    return luws


# ==================== STEP 2: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str) -> list:
    """GET /livestreamings?stream_type=tv_stream"""
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

    log.info("Fetching all channels ...")
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

        # Ambil logo: square_image atau image_landscape
        square = attrs.get("square_image", {})
        landscape = attrs.get("image_landscape_url", "")
        logo = square.get("url", "") or landscape

        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "cover_url": logo,
            "web_url": attrs.get("links", {}).get("watchpage", ""),
            "is_live": "ongoing_schedule" in item.get("relationships", {}),
        })

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STEP 3: FETCH STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int) -> str:
    """GET /livestreamings/{id}/stream?initialize=true"""
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "luws": luws,
        "x-client": x_client,
        "x-signature": x_signature,
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
        stream_url = stream_urls.get(ch_id, "")

        if not stream_url:
            log.warning("  [SKIP] No stream: %s", group)
            continue

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
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--luws", type=str, default="",
                        help="luws cookie (manual, dari browser DevTools)")
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Hybrid: Playwright (luws) + curl_cffi (API)")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Get luws
    luws = get_luws(args.luws)

    # Step 2: Fetch ALL channels
    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    live_count = sum(1 for c in channels if c.get("is_live"))
    log.info("\n  Total: %d channels (%d live, %d off)",
             len(channels), live_count, len(channels) - live_count)

    for ch in channels:
        status = "● LIVE" if ch.get("is_live") else "○ OFF"
        log.info("    [%s] %s  %s", ch["id"], ch["title"], status)

    # Step 3: Fetch stream URLs
    log.info("\nFetching stream URLs ...")
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        ch_id = ch["id"]
        hls = fetch_stream_url(session, luws, ch_id)
        if hls:
            stream_urls[ch_id] = hls
            log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
        else:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗", i, len(channels), ch["title"])
        time.sleep(DELAY)

    # Step 4: Retry
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        log.info("\nRetry %d/%d (%d channels) ...", attempt, args.retries, len(failed))
        time.sleep(RETRY_WAIT)
        still_failed = []
        for ch in failed:
            hls = fetch_stream_url(session, luws, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [RETRY %d] %s ✓", attempt, ch["title"])
            else:
                still_failed.append(ch)
            time.sleep(RETRY_DELAY)
        failed = still_failed

    # Summary
    log.info("\n  ─────────────────────────────────")
    log.info("  Total : %d", len(channels))
    log.info("  OK    : %d", len(stream_urls))
    log.info("  Fail  : %d", len(failed))
    log.info("  ─────────────────────────────────")

    if failed:
        log.info("  Tanpa stream:")
        for ch in failed:
            log.info("    - [%s] %s", ch["id"], ch["title"])

    # Step 5: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
