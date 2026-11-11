#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
All headers match browser sniff exactly.
Requires: pip install curl-cffi
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
import logging
from pathlib import Path

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
DELAY = 0.3
RETRY_WAIT = 5

STATIC_API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
ORIGIN = "https://m.vidio.com"
REFERER = "https://m.vidio.com/"

USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"

STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# ==================== COOKIES ====================
VIDIO_COOKIES = {
    "ahoy_visitor": "3489c20a-d32e-468a-bd1b-8f7d150fa132",
    "plenty_id": USER_ID,
    "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30sImV4cCI6MTc5NDQ0MzM3MX0.vIjiSE4zy4-12PSqMrov5hxB6kwKdr26_EDVmeI6sfY",
    "ahoy_visit": "6acc3493-f9b5-42fd-a80a-e12a2671319b",
    "country_id": "ID",
    "shva": "1",
    "remember_user_token": "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T1RrMU5UY3VOREUyTXpRNElsMD0iLCJleHAiOiIyMDI4LTExLTExVDEyOjE5OjE3LjQxNloiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D--8d03ff9749c920966159108beb64bd0cccab8ac9",
    "_vidio_session": "n8ysEHsLvPzBrPgpvd4gmj6Hp5I8o%2FukVvu9SAK5CQ0K3cUTerR8q2ZcWOdiMpKLr4oCEL%2BAUj0cztRpaxbeHfePR0J%2FIxMhz7%2B7vDbtYZrHNi%2FX9JvzLpvQTWEJulkzyd1I9NNnz91qKqPRQlsUptl%2FV01MH9HEk86YlSujERvG2j3CwaRiowbGgiUw203EttNm%2FlR93IcRFc0Cmq%2BKnlc0312sdmHUpRuk3S1cBX08Nh7jVvVWV%2FKB%2BCZ3BVNKVV70Rzes9Ve2id%2BE%2F3RuNjDG62Ro8Q%3D%3D--3dUc5P2v9pqxcCLG--FEZr6E%2BttRxxgitjGThw9w%3D%3D",
}

# ==================== CLASSIFIKASI ====================
INTERNATIONAL = {
    "Arirang", "ABC Australia", "Euronews", "CNA",
    "France24", "MUSICA", "U-Channel TV", "Makkah TV",
}

EXTRA_CHANNELS = {
    870: "MNCTV", 782: "ANTV", 665: "RCTI", 666: "GTV",
    667: "MDTV", 668: "iNews", 669: "Net", 670: "tvOne",
    671: "One", 672: "O Channel",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_COOKIES_FILE = os.path.join(_BASE_DIR, ".vidio_cookies.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== UTILS ====================
def _load_cookies() -> dict:
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            data = json.load(f)
            if isinstance(data, list):
                return {c["name"]: c["value"] for c in data}
            return data
    return VIDIO_COOKIES.copy()


def generate_signature() -> tuple:
    """x-client = float timestamp, x-signature = HMAC-SHA256"""
    t = str(time.time())
    key = f"V1d10D3v:{t}"
    signature = hmac.new(key.encode(), t.encode(), hashlib.sha256).hexdigest()
    return t, signature


def get_luws() -> str:
    return f"{str(uuid.uuid4()).upper()}_{USER_ID}"


def get_group(title: str) -> str:
    return "TV Internasional" if title in INTERNATIONAL else "TV Nasional"


def extract_logo(attrs: dict) -> str:
    square = attrs.get("square_image", {})
    if isinstance(square, dict) and square.get("url"):
        return square["url"]
    if attrs.get("image_landscape_url"):
        return attrs["image_landscape_url"]
    if attrs.get("cover_url"):
        return attrs["cover_url"]
    return ""


def parse_channels(data: dict) -> list:
    channels = []
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        ch_id = item.get("id")
        if not ch_id:
            continue
        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "logo": extract_logo(attrs),
        })
    existing = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing:
            channels.append({"id": ch_id, "title": title, "logo": ""})
    return channels


# ==================== M3U GENERATOR ====================
def generate_m3u(channels, stream_urls, output_file, drm_fallback=True):
    lines = ["#EXTM3U"]
    count = 0
    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = get_group(title)

        stream_url = stream_urls.get(ch_id, "")
        if not stream_url and drm_fallback:
            stream_url = STREAM_DRM_PATTERN.format(id=ch_id)
        if not stream_url:
            continue

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


# ==================== MAIN ====================
def run(output_file: str, retries: int, drm_fallback: bool):
    try:
        from curl_cffi import requests
    except ImportError:
        print("Error: pip install curl-cffi")
        sys.exit(1)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  TLS: Chrome impersonation (curl_cffi)")
    print("=" * 55)

    session = requests.Session(impersonate="chrome")
    cookies = _load_cookies()

    # [1/4] Refresh session
    print("\n[1/4] Refresh session ...")
    try:
        session.get(SITE_URL, timeout=30)
    except Exception as e:
        log.warning("  Visit gagal: %s", e)

    # Verify
    try:
        resp = session.get(
            f"{SITE_URL}/interactions.json",
            headers={
                "x-api-platform": "web-mobile",
                "x-secure-level": "2",
                "x-auth-prefer": "AccessToken",
                "Accept": "*/*",
            },
            cookies=cookies,
            timeout=15,
        )
        if resp.status_code == 200:
            user = resp.json().get("current_user", {})
            if user:
                log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
                log.info("  ✓ Sub: %s", "Premium" if user.get("has_active_subscription") else "Free")
            else:
                log.warning("  ✗ Tidak login")
        else:
            log.warning("  interactions.json HTTP %d", resp.status_code)
    except Exception as e:
        log.warning("  Verify gagal: %s", e)

    # [2/4] Fetch channels
    print("\n[2/4] Fetching channels ...")
    ch_headers = {
        "sec-ch-ua-platform": '"Android"',
        "x-user-id": USER_ID,
        "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
        "x-api-key": STATIC_API_KEY,
        "sec-ch-ua-mobile": "?1",
        "x-visitor-id": cookies.get("ahoy_visitor", ""),
        "x-api-app-info": "js/m.vidio.com",
        "user-agent": USER_AGENT,
        "accept": "application/json",
        "content-type": "application/vnd.api+json",
        "x-secure-level": "2",
        "x-api-platform": "web-mobile",
        "x-user-login": "true",
        "sec-gpc": "1",
        "origin": ORIGIN,
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": REFERER,
        "accept-language": "id",
        "priority": "u=1, i",
    }
    try:
        resp = session.get(
            f"{API_BASE}/livestreamings",
            params={"stream_type": "tv_stream"},
            headers=ch_headers,
            cookies=cookies,
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("  HTTP %d", resp.status_code)
            sys.exit(1)
    except Exception as e:
        log.error("  Error: %s", e)
        sys.exit(1)

    channels = parse_channels(resp.json())
    log.info("  ✓ %d channels", len(channels))

    # [3/4] Fetch streams
    print("\n[3/4] Fetching stream URLs ...")

    def fetch_stream(ch_id: int) -> str:
        x_client, x_signature = generate_signature()
        luws = get_luws()

        # ALL headers dari sniff 200 OK
        headers = {
            "sec-ch-ua-platform": '"Android"',
            "x-user-id": USER_ID,
            "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
            "x-api-key": STATIC_API_KEY,
            "sec-ch-ua-mobile": "?1",
            "luws": luws,
            "x-signature": x_signature,
            "user-agent": USER_AGENT,
            "x-user-email": USER_EMAIL,
            "x-secure-level": "2",
            "x-api-platform": "web-mobile",
            "x-client": x_client,
            "accept": "*/*",
            "sec-gpc": "1",
            "origin": ORIGIN,
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "referer": REFERER,
            "accept-language": "id",
            "priority": "u=1, i",
        }
        try:
            resp = session.get(
                f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                headers=headers,
                cookies=cookies,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("attributes", {}).get("hls", "")
            else:
                log.debug("  HTTP %d for ch %d", resp.status_code, ch_id)
        except Exception:
            pass
        return ""

    # Test
    test_hls = fetch_stream(channels[0]["id"])
    if not test_hls:
        log.error("  ✗ Stream test gagal: %s", channels[0]["title"])
        log.error("  → Cookies expired? Update .vidio_cookies.json")
        sys.exit(1)
    log.info("  ✓ Stream test OK: %s", channels[0]["title"])

    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        hls = fetch_stream(ch["id"])
        if hls:
            stream_urls[ch["id"]] = hls
            log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
        else:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗", i, len(channels), ch["title"])
        time.sleep(DELAY)

    # Retry
    for attempt in range(1, retries + 1):
        if not failed:
            break
        log.info("  Retry %d/%d (%d) ...", attempt, retries, len(failed))
        time.sleep(RETRY_WAIT)
        still = []
        for ch in failed:
            hls = fetch_stream(ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [R%d] %s ✓", attempt, ch["title"])
            else:
                still.append(ch)
            time.sleep(DELAY)
        failed = still

    elapsed = time.time() - start
    log.info("  Total: %d | OK: %d | Fail: %d | %.1fs",
             len(channels), len(stream_urls), len(failed), elapsed)

    if failed:
        for ch in failed:
            log.info("    - [%s] %s", ch["id"], ch["title"])

    if not stream_urls and not drm_fallback:
        log.error("  SEMUA GAGAL.")
        sys.exit(1)

    # [4/4] Generate
    print(f"\n[4/4] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file, drm_fallback)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--no-drm-fallback", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run(args.output, args.retries, not args.no_drm_fallback)


if __name__ == "__main__":
    main()   
