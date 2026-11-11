#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
1. Set remember_user_token → visit m.vidio.com → server auto-set cookies
2. GET /livestreamings?stream_type=tv_stream → semua channel
3. GET /livestreamings/{id}/stream?initialize=true → non-DRM HLS URL
   (x-signature = HMAC-SHA256(key="V1d10D3v:{t}", msg=str(t)))
4. Fallback: DRM pattern jika step 3 gagal

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

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

# Fallback: DRM pattern
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

# ==================== USER (HANYA INI YANG PERLU DI-UPDATE) ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T1RrMU5UY3VOREUyTXpRNElsMD0i"
    "LCJleHAiOiIyMDI4LTExLTExVDEyOjE5OjE3LjQxNloiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--8d03ff9749c920966159108beb64bd0cccab8ac9"
)
# ================================================================================

# ==================== CLASSIFIKASI ====================
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


# ==================== X-SIGNATURE ====================
def generate_x_signature() -> tuple:
    """
    Generate x-signature dan x-client untuk Vidio API.
    x-client = float timestamp (contoh: "1794399608.659")
    x-signature = HMAC-SHA256(key="V1d10D3v:{t}", message=str(t))
    """
    t = str(time.time())
    secret_key = f"V1d10D3v:{t}"
    signature = hmac.new(
        secret_key.encode(),
        t.encode(),
        hashlib.sha256
    ).hexdigest()
    return t, signature


# ==================== BASE HEADERS ====================
def base_headers() -> dict:
    """Headers yang match browser (sec-ch-ua, sec-fetch, dll.)"""
    return {
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
        "sec-ch-ua-mobile": "?1",
        "user-agent": USER_AGENT,
        "sec-gpc": "1",
        "origin": ORIGIN,
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": REFERER,
        "accept-language": "id",
        "priority": "u=1, i",
    }


# ==================== STEP 1: BOOTSTRAP SESSION ====================
def bootstrap_session(session) -> str:
    """
    Set remember_user_token → visit m.vidio.com → server auto-set:
    _vidio_session, access_token, plenty_id, ahoy_visit, dll.
    Return: luws (UUID_userid)
    """
    log.info("Bootstrap session ...")

    # Set minimal cookies
    session.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    session.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("country_id", "ID", domain=".vidio.com", path="/")

    # Visit site → server Set-Cookie: _vidio_session, access_token, plenty_id
    try:
        resp = session.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", resp.status_code)
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    # Verify cookies fresh
    vs = session.cookies.get("_vidio_session")
    at = session.cookies.get("access_token")
    pid = session.cookies.get("plenty_id")
    if vs:
        log.info("  ✓ _vidio_session: %s...", vs[:30])
    if at:
        log.info("  ✓ access_token: %s...", at[:30])
    if pid:
        log.info("  ✓ plenty_id: %s", pid)

    # luws: coba dari cookie, kalau tidak ada generate
    luws = session.cookies.get("luws", "")
    if not luws:
        luws = f"{str(uuid.uuid4()).upper()}_{USER_ID}"
    log.info("  ✓ luws: %s", luws)

    return luws


# ==================== STEP 2: VERIFY LOGIN ====================
def verify_login(session) -> bool:
    """GET /interactions.json → cek apakah login aktif."""
    h = base_headers()
    h.update({
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-auth-prefer": "AccessToken",
        "accept": "*/*",
    })
    try:
        resp = session.get(f"{SITE_URL}/interactions.json", headers=h, timeout=15)
        if resp.status_code == 200:
            user = resp.json().get("current_user", {})
            if user:
                log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
                log.info("  ✓ Sub: %s", "Premium" if user.get("has_active_subscription") else "Free")
                return True
    except Exception as e:
        log.warning("  Verify gagal: %s", e)
    return False


# ==================== STEP 3: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str) -> list:
    """GET /livestreamings?stream_type=tv_stream → semua channel."""
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    h = base_headers()
    h.update({
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR,
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
        "x-user-id": USER_ID,
        "luws": luws,
        "accept": "application/json",
        "content-type": "application/vnd.api+json",
    })

    log.info("Fetching channels ...")
    try:
        resp = session.get(url, params=params, headers=h, timeout=30)
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

        # Logo: prioritas square_image.url
        square = attrs.get("square_image", {})
        logo = ""
        if isinstance(square, dict) and square.get("url"):
            logo = square["url"]
        elif attrs.get("image_landscape_url"):
            logo = attrs["image_landscape_url"]
        elif attrs.get("cover_url"):
            logo = attrs["cover_url"]

        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "logo": logo,
        })

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STEP 4: FETCH STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int) -> str:
    """
    GET /livestreamings/{id}/stream?initialize=true
    x-signature = HMAC-SHA256(key="V1d10D3v:{t}", msg=str(t))
    x-client = float timestamp
    """
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    h = base_headers()
    h.update({
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-user-id": USER_ID,
        "x-user-email": USER_EMAIL,
        "luws": luws,
        "x-client": x_client,
        "x-signature": x_signature,
        "accept": "*/*",
    })

    try:
        resp = session.get(url, headers=h, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            hls = data.get("data", {}).get("attributes", {}).get("hls", "")
            if hls:
                return hls
            else:
                log.debug("  No hls in response: %s", json.dumps(data)[:200])
        else:
            log.debug("  HTTP %d for channel %d", resp.status_code, ch_id)
    except Exception as e:
        log.debug("  Exception: %s", e)

    return ""


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = "TV Internasional" if title in INTERNATIONAL else "TV Nasional"

        # Prioritas: non-DRM (dari API) > DRM (pattern)
        stream_url = stream_urls.get(ch_id) or STREAM_DRM_PATTERN.format(id=ch_id)

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
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--drm-only", action="store_true",
                        help="Skip non-DRM, langsung pakai DRM pattern")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Auto: cookies, headers, signature")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Bootstrap (set remember_token → server auto-set cookies)
    luws = bootstrap_session(session)

    # Step 2: Verify login
    if not verify_login(session):
        log.error("Login gagal! Check REMEMBER_TOKEN di script.")
        sys.exit(1)

    # Step 3: Fetch all channels
    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels[:10]:
        log.info("    [%s] %s", ch["id"], ch["title"])
    if len(channels) > 10:
        log.info("    ... +%d more", len(channels) - 10)

    # Step 4: Fetch stream URLs
    stream_urls = {}
    if not args.drm_only:
        log.info("\nFetching non-DRM stream URLs ...")
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

        # Retry
        for attempt in range(1, args.retries + 1):
            if not failed:
                break
            log.info("  Retry %d/%d (%d) ...", attempt, args.retries, len(failed))
            time.sleep(5)
            still = []
            for ch in failed:
                hls = fetch_stream_url(session, luws, ch["id"])
                if hls:
                    stream_urls[ch["id"]] = hls
                    log.info("  [R%d] %s ✓", attempt, ch["title"])
                else:
                    still.append(ch)
                time.sleep(DELAY)
            failed = still

        log.info("\n  Non-DRM: %d/%d", len(stream_urls), len(channels))

    # Step 5: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
