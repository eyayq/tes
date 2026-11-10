#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
Ambil SEMUA channel + stream URL non-DRM.
6 channel besar (SCTV, Indosiar, TRANS TV, Trans7, Moji, Kompas TV)
mungkin butuh penanganan khusus — debug block disertakan.

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
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
    """HMAC-SHA256(key="V1d10D3v:{t}", msg=str(t))"""
    t = int(time.time())
    secret_key = f"V1d10D3v:{t}"
    message = str(t)
    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return str(t), signature


# ==================== STEP 1: GET LUWS ====================
def get_luws(session) -> str:
    """Visit m.vidio.com untuk cookie luws."""
    log.info("Visiting %s ...", SITE_URL)
    try:
        session.get(SITE_URL, timeout=30)
        luws = session.cookies.get("luws")
        if luws:
            log.info("  luws: %s", luws)
            return luws
    except Exception as e:
        log.warning("  Error: %s", e)

    log.info("  Generate luws baru")
    return f"{str(uuid.uuid4()).upper()}_"


# ==================== STEP 2: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str) -> list:
    """GET /livestreamings?stream_type=tv_stream → SEMUA channel."""
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

        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "cover_url": attrs.get("cover_url", ""),
            "web_url": attrs.get("web_url", ""),
            "is_live": attrs.get("is_live", False),
        })

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STEP 3: FETCH STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int, verbose: bool = False) -> str:
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
            if verbose:
                log.info("  [DEBUG %d] 200 but no hls: %s", ch_id, json.dumps(data)[:400])
        else:
            if verbose:
                log.info("  [DEBUG %d] HTTP %d: %s", ch_id, resp.status_code, resp.text[:300])
    except Exception as e:
        if verbose:
            log.info("  [DEBUG %d] Exception: %s", ch_id, e)

    return ""


# ==================== DEBUG: TEST CHANNEL YANG GAGAL ====================
def debug_failed_channels(session, luws: str, failed: list):
    """Debug channel yang gagal untuk lihat response sebenarnya."""
    if not failed:
        return

    log.info("\n" + "=" * 50)
    log.info("DEBUG: Testing %d failed channels", len(failed))
    log.info("=" * 50)

    # Test satu channel pertama yang gagal
    ch = failed[0]
    ch_id = ch["id"]
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

    log.info("\n  Channel: %s (ID: %d)", ch["title"], ch_id)
    log.info("  URL: %s", url)
    log.info("  x-client: %s", x_client)
    log.info("  x-signature: %s", x_signature)
    log.info("  luws: %s", luws)

    try:
        resp = session.get(url, headers=headers, timeout=15)
        log.info("  Status: %d", resp.status_code)
        log.info("  Headers: %s", dict(resp.headers))
        log.info("  Body: %s", resp.text[:500])
    except Exception as e:
        log.info("  Error: %s", e)

    # Coba tanpa x-signature
    log.info("\n  --- Coba TANPA x-signature ---")
    headers_no_sig = {k: v for k, v in headers.items() if k != "x-signature"}
    try:
        resp2 = session.get(url, headers=headers_no_sig, timeout=15)
        log.info("  Status: %d", resp2.status_code)
        log.info("  Body: %s", resp2.text[:500])
    except Exception as e:
        log.info("  Error: %s", e)

    # Coba dengan x-client float (seperti web-mobile asli)
    log.info("\n  --- Coba x-client FLOAT ---")
    t_float = f"{time.time()}"
    x_sig_float = hmac.new(
        f"V1d10D3v:{int(time.time())}".encode(),
        t_float.encode(),
        hashlib.sha256
    ).hexdigest()
    headers_float = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "luws": luws,
        "x-client": t_float,
        "x-signature": x_sig_float,
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
        "Accept": "*/*",
    }
    try:
        resp3 = session.get(url, headers=headers_float, timeout=15)
        log.info("  Status: %d", resp3.status_code)
        log.info("  Body: %s", resp3.text[:500])
    except Exception as e:
        log.info("  Error: %s", e)

    log.info("\n" + "=" * 50)


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
    parser.add_argument("--retries", type=int, default=2,
                        help="Max retry attempts (default: 2)")
    parser.add_argument("--debug", action="store_true",
                        help="Debug failed channels")
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  ALL channels + x-signature HMAC-SHA256")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Get luws
    luws = get_luws(session)

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

    # Step 4: Retry failed
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        log.info("\nRetry %d/%d (%d channels, wait %ds) ...",
                 attempt, args.retries, len(failed), RETRY_WAIT)
        time.sleep(RETRY_WAIT)

        still_failed = []
        for ch in failed:
            ch_id = ch["id"]
            hls = fetch_stream_url(session, luws, ch_id)
            if hls:
                stream_urls[ch_id] = hls
                log.info("  [RETRY %d] %s ✓", attempt, ch["title"])
            else:
                still_failed.append(ch)
                log.warning("  [RETRY %d] %s ✗", attempt, ch["title"])
            time.sleep(RETRY_DELAY)
        failed = still_failed

    # Step 5: Debug jika diminta
    if failed and args.debug:
        debug_failed_channels(session, luws, failed)

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

    # Step 6: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
