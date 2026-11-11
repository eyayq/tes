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
import re
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
LIVE_URL = "https://m.vidio.com/live"
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


def fetch_rsc_channels(session) -> list:
    """
    Fetch RSC /live → parse SEMUA channel.
    - content_type:"livestreaming" → channel langsung (title = nama channel)
    - content_type:"livestreaming_schedule" → ambil livestreaming_id + livestreaming_title
    """
    headers = {
        "RSC": "1",
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.8",
        "Referer": "https://m.vidio.com/",
        "User-Agent": USER_AGENT,
    }

    log.info("Fetching RSC %s ...", LIVE_URL)
    try:
        resp = session.get(LIVE_URL, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  HTTP %d", resp.status_code)
            return []
    except Exception as e:
        log.error("  Error: %s", e)
        return []

    rsc_text = resp.text
    log.info("  RSC: %d bytes", len(rsc_text))

    channels = {}

    # === Pattern 1: content_type:"livestreaming" (channel langsung) ===
    # Format: "content_id":870,"content_type":"livestreaming","title":"MNCTV","cover_url":"..."
    pat1 = re.compile(
        r'"content_id"\s*:\s*(\d+),\s*"content_type"\s*:\s*"livestreaming"\s*,\s*"title"\s*:\s*"([^"]+)"'
    )
    for m in pat1.finditer(rsc_text):
        ch_id = int(m.group(1))
        title = m.group(2)
        if ch_id in channels:
            continue
        # Ambil cover_url setelah title
        after = rsc_text[m.end():m.end() + 500]
        cover_m = re.search(r'"cover_url"\s*:\s*"([^"]+)"', after)
        cover_url = cover_m.group(1) if cover_m else ""
        channels[ch_id] = {"id": ch_id, "title": title, "cover_url": cover_url}

    # === Pattern 2: content_type:"livestreaming_schedule" (schedule item) ===
    # Format: "content_id":5430445,"content_type":"livestreaming_schedule","title":"Spotlite",
    #         "livestreaming_title":"Trans7",...,"links":{"self":{"meta":{"livestreaming_id":734,...}}}
    pat2 = re.compile(
        r'"content_type"\s*:\s*"livestreaming_schedule"'
    )
    for m in pat2.finditer(rsc_text):
        # Ambil context SEKITAR match (cukup lebar)
        start = max(0, m.start() - 100)
        end = min(len(rsc_text), m.end() + 1000)
        context = rsc_text[start:end]

        # Ambil livestreaming_id dari links.self.meta
        ls_id_m = re.search(r'"livestreaming_id"\s*:\s*(\d+)', context)
        if not ls_id_m:
            continue
        ch_id = int(ls_id_m.group(1))
        if ch_id in channels:
            continue

        # Ambil livestreaming_title (nama channel)
        ls_title_m = re.search(r'"livestreaming_title"\s*:\s*"([^"]+)"', context)
        title = ls_title_m.group(1) if ls_title_m else f"Channel {ch_id}"

        # Ambil cover_url
        cover_m = re.search(r'"cover_url"\s*:\s*"([^"]+)"', context)
        cover_url = cover_m.group(1) if cover_m else ""

        channels[ch_id] = {"id": ch_id, "title": title, "cover_url": cover_url}

    log.info("  Found %d channels", len(channels))
    return list(channels.values())


def fetch_stream_url(session, ch_id: int) -> str:
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-user-id": USER_ID,
        "x-user-email": USER_EMAIL,
        "x-user-login": "true",
        "luws": LUWS,
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
        logo = ch["cover_url"]
        stream_url = stream_urls.get(ch_id, "")

        if not stream_url:
            log.warning("  [SKIP] No stream: %s", title)
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

    # Step 1: Parse RSC
    channels = fetch_rsc_channels(session)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels:
        log.info("    [%s] %s", ch["id"], ch["title"])

    # Step 2: Fetch streams
    log.info("\nFetching stream URLs ...")
    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        hls = fetch_stream_url(session, ch["id"])
        if hls:
            stream_urls[ch["id"]] = hls
            log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
        else:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗", i, len(channels), ch["title"])
        time.sleep(DELAY)

    # Step 3: Retry
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        log.info("\nRetry %d/%d (%d) ...", attempt, args.retries, len(failed))
        time.sleep(RETRY_WAIT)
        still_failed = []
        for ch in failed:
            hls = fetch_stream_url(session, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [RETRY] %s ✓", ch["title"])
            else:
                still_failed.append(ch)
            time.sleep(DELAY)
        failed = still_failed

    # Summary
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

    # Step 4: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
