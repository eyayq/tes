#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (Complete)
==================================================
- Scrape semua channel dari /categories/live/sections (included[], type="content")
- Non-DRM: fetch via API stream (valid 2 jam)
- DRM: fallback ke URL DRM + license proxy (Cloudflare Worker)
- Auto-refresh loop (opsional)

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
    python vidio_m3u.py --loop          # auto-refresh tiap 115 menit
    python vidio_m3u.py --drm-only      # skip non-DRM, langsung DRM
    python vidio_m3u.py -o my.m3u       # custom output
    python vidio_m3u.py --debug         # lihat response mentah
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
DELAY = 0.3
REFRESH_MINUTES = 115

# License proxy (Cloudflare Worker)
LICENSE_WORKER = "https://b.bhns.workers.dev/license.mpd"

# DRM stream pattern
STREAM_DRM_HLS = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# API Key
API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

# ==================== USER (UPDATE DI SINI) ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T1RneU5ERXVNekkyTkRFM05DSmQi"
    "LCJleHAiOiIyMDI4LTExLTExVDExOjU3OjIxLjMyNloiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--2c508b1dd9d15adf9f96cfcbfdbcfd3ef41d76c5"
)
# ==================================================================

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== X-SIGNATURE ====================
def generate_x_signature() -> tuple:
    t = str(time.time())
    secret_key = f"V1d10D3v:{t}"
    signature = hmac.new(
        secret_key.encode(),
        t.encode(),
        hashlib.sha256
    ).hexdigest()
    return t, signature


# ==================== STEP 1: BOOTSTRAP SESSION ====================
def bootstrap_session(session) -> str:
    log.info("Bootstrap session ...")

    session.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    session.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("country_id", "ID", domain=".vidio.com", path="/")

    try:
        resp = session.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", resp.status_code)
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    at = session.cookies.get("access_token")
    if at:
        log.info("  ✓ access_token: %s...", at[:30])

    luws = session.cookies.get("luws", "")
    if not luws:
        luws = f"{str(uuid.uuid4()).upper()}_{USER_ID}"
    log.info("  ✓ luws: %s", luws)
    return luws


# ==================== STEP 2: VERIFY LOGIN ====================
def verify_login(session) -> bool:
    h = {
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-auth-prefer": "AccessToken",
        "origin": "https://m.vidio.com",
        "referer": "https://m.vidio.com/",
        "accept": "*/*",
        "accept-language": "id",
    }
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
    """
    GET /categories/live/sections → parse included[] (type="content", content_type="livestreaming").
    """
    url = f"{API_BASE}/categories/live/sections"
    h = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR,
        "x-user-id": USER_ID,
        "luws": luws,
        "origin": "https://m.vidio.com",
        "referer": "https://m.vidio.com",
        "accept": "application/json",
        "accept-language": "id",
    }

    log.info("Fetching all channels ...")
    channels = {}
    page = 1

    while True:
        params = {"page[number]": page, "page[size]": 10, "included": "contents"}
        try:
            resp = session.get(url, params=params, headers=h, timeout=30)
            if resp.status_code != 200:
                log.warning("  Page %d: HTTP %d %s", page, resp.status_code, resp.text[:200])
                break
        except Exception as e:
            log.error("  Page %d error: %s", page, e)
            break

        data = resp.json()

        # Build section title mapping
        sections = data.get("data", [])
        if isinstance(sections, dict):
            sections = [sections]

        section_id_to_title = {}
        section_contents_map = {}

        for section in sections:
            sec_id = str(section.get("id", ""))
            attrs = section.get("attributes", {})
            section_id_to_title[sec_id] = attrs.get("title", "Lainnya")

            rels = section.get("relationships", {})
            contents_rel = rels.get("contents", {})
            content_list = contents_rel.get("data", [])
            if isinstance(content_list, list):
                section_contents_map[sec_id] = content_list

        # Parse included[]
        included = data.get("included", [])
        if page == 1 and included:
            types = {}
            for item in included:
                t = item.get("type", "?")
                types[t] = types.get(t, 0) + 1
            log.info("  [DEBUG] included types: %s", types)
            # Sample
            for item in included:
                if item.get("type") == "content":
                    log.info("  [DEBUG] sample: %s", json.dumps(item, ensure_ascii=False)[:400])
                    break

        for item in included:
            if item.get("type") != "content":
                continue

            attrs = item.get("attributes", {})

            # Filter: hanya livestreaming
            content_type = attrs.get("content_type", "")
            if content_type != "livestreaming":
                continue

            # Channel ID: dari attributes.content_id
            ch_id = attrs.get("content_id")
            if not ch_id:
                # Fallback: extract dari id "section-123-livestreaming-204"
                id_str = str(item.get("id", ""))
                parts = id_str.split("-")
                try:
                    ch_id = int(parts[-1])
                except (ValueError, IndexError):
                    continue
            try:
                ch_id = int(ch_id)
            except (ValueError, TypeError):
                continue

            if ch_id in channels:
                continue

            # Title: prefer alt_title (nama channel), fallback title
            title = attrs.get("alt_title") or attrs.get("title", "Unknown")

            # Logo
            logo = attrs.get("cover_url", "")
            if not logo:
                sq = attrs.get("square_image")
                if isinstance(sq, dict):
                    logo = sq.get("url", "")
                elif isinstance(sq, str):
                    logo = sq

            # Group: cari dari section relationships
            group = "Lainnya"
            item_id_str = str(ch_id)
            for sec_id, content_list in section_contents_map.items():
                for c in content_list:
                    c_id = str(c.get("id", ""))
                    if item_id_str in c_id:
                        group = section_id_to_title.get(sec_id, "Lainnya")
                        break
                if group != "Lainnya":
                    break

            channels[ch_id] = {
                "id": ch_id,
                "title": title,
                "logo": logo,
                "group": group,
                "drm_stream": attrs.get("stream_url", ""),
            }

        # Pagination
        links = data.get("links", {})
        if links.get("next"):
            page += 1
            time.sleep(DELAY)
        else:
            break

    result = list(channels.values())
    log.info("  Found %d unique channels", len(result))
    return result


# ==================== STEP 4: FETCH NON-DRM STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int) -> str:
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    h = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-user-id": USER_ID,
        "x-user-email": USER_EMAIL,
        "luws": luws,
        "x-client": x_client,
        "x-signature": x_signature,
        "origin": "https://m.vidio.com",
        "referer": "https://m.vidio.com",
        "sec-fetch-site": "same-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "accept": "*/*",
        "accept-language": "id",
    }

    try:
        resp = session.get(url, headers=h, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            attrs = data.get("data", {}).get("attributes", {})
            hls = attrs.get("hls", "")
            if hls:
                return hls
            log.debug("  No hls for ch %d", ch_id)
        else:
            log.debug("  HTTP %d for ch %d: %s", resp.status_code, ch_id, resp.text[:100])
    except Exception as e:
        log.debug("  Exception ch %d: %s", ch_id, e)
    return ""


# ==================== STEP 5: GENERATE M3U ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0
    non_drm_count = 0
    drm_count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = ch.get("group", "Lainnya")

        non_drm_url = stream_urls.get(ch_id, "")
        drm_url = ch.get("drm_stream", "") or STREAM_DRM_HLS.format(id=ch_id)
        license_url = f"{LICENSE_WORKER}?id={ch_id}"

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )

        if non_drm_url:
            lines.append(non_drm_url)
            non_drm_count += 1
        else:
            lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
            lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_url}")
            lines.append(drm_url)
            drm_count += 1

        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels: %d non-DRM, %d DRM)", output_file, count, non_drm_count, drm_count)


# ==================== MAIN ====================
def run_once(output_file: str, drm_only: bool, retries: int) -> bool:
    session = cffi_requests.Session(impersonate=IMPERSONATE)

    luws = bootstrap_session(session)

    if not verify_login(session):
        log.error("Login gagal! Update REMEMBER_TOKEN.")
        return False

    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        return False

    # Summary
    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups (%d):", len(groups))
    for g, names in sorted(groups.items()):
        preview = ", ".join(names[:4]) + ("..." if len(names) > 4 else "")
        log.info("    [%s] %d: %s", g, len(names), preview)

    # Fetch non-DRM URLs
    stream_urls = {}
    if not drm_only:
        log.info("\nFetching non-DRM URLs (%d channels) ...", len(channels))
        failed = []
        for i, ch in enumerate(channels, 1):
            hls = fetch_stream_url(session, luws, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
            else:
                failed.append(ch)
            if i % 10 == 0 or i == len(channels):
                log.info("  Progress: %d/%d (%d ok, %d fail)", i, len(channels), len(stream_urls), len(failed))
            time.sleep(DELAY)

        # Retry
        for attempt in range(1, retries + 1):
            if not failed:
                break
            log.info("  Retry %d/%d (%d failed) ...", attempt, retries, len(failed))
            time.sleep(5)
            still = []
            for ch in failed:
                hls = fetch_stream_url(session, luws, ch["id"])
                if hls:
                    stream_urls[ch["id"]] = hls
                else:
                    still.append(ch)
                time.sleep(DELAY)
            failed = still

        log.info("  Non-DRM: %d/%d", len(stream_urls), len(channels))

    # Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, output_file)
    return True


def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--drm-only", action="store_true",
                        help="Skip non-DRM, langsung DRM + license worker")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--loop", action="store_true",
                        help="Auto-refresh tiap 115 menit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Non-DRM: direct | DRM: via license worker")
    print(f"  License: {LICENSE_WORKER}")
    print("=" * 55)

    if args.loop:
        log.info("Loop mode: refresh every %d minutes", REFRESH_MINUTES)
        while True:
            try:
                run_once(args.output, args.drm_only, args.retries)
            except Exception as e:
                log.error("Error: %s", e)
            log.info("Sleeping %d minutes ...", REFRESH_MINUTES)
            time.sleep(REFRESH_MINUTES * 60)
    else:
        if not run_once(args.output, args.drm_only, args.retries):
            sys.exit(1)


if __name__ == "__main__":
    main()   
