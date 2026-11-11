#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (LOGIN)
================================================
Run:
    python vidio_m3u.py
"""

import argparse
import hashlib
import hmac
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

# ==================== KREDENSIAL (EDIT DI SINI) ====================
LUWS = "9980CC03-6E54-454A-4E3A-8C84D2BCEFDC_186957857"
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
# ===================================================================

API_BASE = "https://api.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.3
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

EXTRA_CHANNELS = {
    870: "MNCTV",
    782: "ANTV",
    665: "RCTI",
    666: "GTV",
    667: "MDTV",
    668: "iNews",
    669: "Net",
    670: "tvOne",
    671: "One",
    672: "O Channel",
}

INTERNATIONAL = {
    "Arirang", "ABC Australia", "Euronews", "CNA",
    "France24", "MUSICA", "U-Channel TV", "Makkah TV",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


def generate_x_signature() -> tuple:
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


def fetch_tv_stream_channels(session) -> list:
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
        "x-user-id": USER_ID,
        "luws": LUWS,
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
    }

    log.info("Fetching channels ...")
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
        ch_id = int(ch_id)
        title = attrs.get("title", "Unknown")
        square = attrs.get("square_image", {})
        landscape = attrs.get("image_landscape_url", "")
        logo = square.get("url", "") or landscape
        channels.append({"id": ch_id, "title": title, "logo": logo})

    log.info("  Found %d channels", len(channels))
    return channels


def fetch_all_channels(session) -> list:
    channels = fetch_tv_stream_channels(session)
    existing_ids = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing_ids:
            channels.append({"id": ch_id, "title": title, "logo": ""})
    log.info("  Total: %d channels", len(channels))
    return channels


def fetch_stream_url(session, ch_id: int, use_login: bool = True) -> str:
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "luws": LUWS,
        "x-client": x_client,
        "x-signature": x_signature,
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
        "Accept": "*/*",
    }

    if use_login:
        headers["x-user-id"] = USER_ID
        headers["x-user-email"] = USER_EMAIL
        headers["x-user-login"] = "true"
    else:
        headers["x-user-login"] = "false"

    try:
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("attributes", {}).get("hls", "")
    except Exception:
        pass

    return ""


def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0
    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch["logo"]
        stream_url = stream_urls.get(ch_id, "")
        if not stream_url:
            continue
        group = "TV Internasional" if title in INTERNATIONAL else "TV Nasional"
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Login: %s" % USER_EMAIL)
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)
    channels = fetch_all_channels(session)
    if not channels:
        log.error("Tidak ada channel.")
        sys.exit(1)

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels:
        log.info("    [%s] %s", ch["id"], ch["title"])

    # === TEST: coba 1 channel dengan login, jika gagal coba tanpa login ===
    log.info("\nTesting stream access ...")
    test_ch = channels[0]
    hls_login = fetch_stream_url(session, test_ch["id"], use_login=True)
    use_login = True

    if not hls_login:
        log.info("  Login gagal, coba tanpa login ...")
        hls_nologin = fetch_stream_url(session, test_ch["id"], use_login=False)
        if hls_nologin:
            use_login = False
            log.info("  ✓ Berhasil tanpa login (luws login expired)")
        else:
            log.error("  ✗ Gagal dengan dan tanpa login")
            log.error("  LUWS SUDAH EXPIRED! Update di script:")
            log.error("  1. Buka m.vidio.com di browser (login)")
            log.error("  2. F12 → Application → Cookies → copy 'luws'")
            log.error("  3. Paste di baris LUWS = \"...\" di atas")
            sys.exit(1)
    else:
        log.info("  ✓ Login OK")

    # === Fetch semua stream ===
    log.info("\nFetching stream URLs (login=%s) ...", use_login)
    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        hls = fetch_stream_url(session, ch["id"], use_login=use_login)
        if hls:
            stream_urls[ch["id"]] = hls
            log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
        else:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗", i, len(channels), ch["title"])
        time.sleep(DELAY)

    # Retry
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        log.info("\nRetry %d/%d (%d) ...", attempt, args.retries, len(failed))
        time.sleep(RETRY_WAIT)
        still_failed = []
        for ch in failed:
            hls = fetch_stream_url(session, ch["id"], use_login=use_login)
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [RETRY] %s ✓", ch["title"])
            else:
                still_failed.append(ch)
            time.sleep(DELAY)
        failed = still_failed

    elapsed = time.time() - start
    log.info("\n  ─────────────────────────────────")
    log.info("  Total : %d", len(channels))
    log.info("  OK    : %d", len(stream_urls))
    log.info("  Fail  : %d", len(failed))
    log.info("  Time  : %.1fs", elapsed)
    log.info("  ─────────────────────────────────")

    if failed:
        for ch in failed:
            log.info("    - [%s] %s", ch["id"], ch["title"])

    if not stream_urls:
        log.error("\n  SEMUA GAGAL. Luws kemungkinan expired.")
        log.error("  Update LUWS di script (baris atas).")
        sys.exit(1)

    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
