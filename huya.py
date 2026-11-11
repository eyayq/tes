#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (Proxy Worker)
======================================================
- Scrape semua channel dari /livestreamings + /categories/live/sections
- Stream URL: https://ch.bhns.workers.dev/ch.m3u8?id={id}
- License URL: https://ch.bhns.workers.dev/dh.m3u8?id={id}
- Worker auto-refresh token (setiap request = URL fresh)

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
    python vidio_m3u.py -o my.m3u
    python vidio_m3u.py --debug
"""

import argparse
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

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.3

# Proxy Worker URLs
STREAM_URL = "https://ch.bhns.workers.dev/ch.m3u8?id={id}"
LICENSE_URL = "https://ch.bhns.workers.dev/dh.m3u8?id={id}"

# API Key
API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

# Browser headers (untuk EXT VLCOPT)
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

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
        log.warning("  Error: %s", e)

    at = session.cookies.get("access_token")
    if at:
        log.info("  ✓ access_token: %s...", at[:30])

    import uuid
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
        "origin": ORIGIN,
        "referer": REFERER,
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
    h = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR,
        "x-user-id": USER_ID,
        "luws": luws,
        "origin": ORIGIN,
        "referer": REFERER,
        "accept": "application/json",
        "accept-language": "id",
    }

    channels = {}

    # === SOURCE 1: /livestreamings ===
    log.info("Fetching channels via /livestreamings ...")
    try:
        resp = session.get(
            f"{API_BASE}/livestreamings",
            params={"stream_type": "tv_stream"},
            headers=h, timeout=30
        )
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                attrs = item.get("attributes", {})
                ch_id = item.get("id")
                if not ch_id:
                    continue
                try:
                    ch_id = int(ch_id)
                except (ValueError, TypeError):
                    continue

                logo = ""
                sq = attrs.get("square_image")
                if isinstance(sq, dict):
                    logo = sq.get("url", "")
                elif isinstance(sq, str):
                    logo = sq
                if not logo:
                    logo = attrs.get("image_landscape_url", "") or attrs.get("cover_url", "")

                channels[ch_id] = {
                    "id": ch_id,
                    "title": attrs.get("title", "Unknown"),
                    "logo": logo,
                    "group": "Lainnya",
                }
            log.info("  /livestreamings: %d channels", len(channels))
        else:
            log.warning("  HTTP %d", resp.status_code)
    except Exception as e:
        log.error("  Error: %s", e)

    # === SOURCE 2: /categories/live/sections (grouping) ===
    log.info("Fetching sections for grouping ...")
    section_list = []
    page = 1
    while True:
        params = {"page[number]": page, "page[size]": 10, "included": "contents"}
        try:
            resp = session.get(
                f"{API_BASE}/categories/live/sections",
                params=params, headers=h, timeout=30
            )
            if resp.status_code != 200:
                break
        except Exception:
            break

        data = resp.json()
        sections = data.get("data", [])
        if isinstance(sections, dict):
            sections = [sections]

        for section in sections:
            sec_id = section.get("id", "")
            attrs = section.get("attributes", {})
            title = attrs.get("title", "Lainnya")
            links = section.get("links", {})
            self_url = links.get("self", "")
            if not self_url:
                slug = title.lower().replace(" ", "-")
                self_url = f"{API_BASE}/sections/{sec_id}-{slug}"
            section_list.append({"id": sec_id, "title": title, "self_url": self_url})

        # Parse included[]
        _parse_included(data.get("included", []), channels)

        links_data = data.get("links", {})
        if links_data.get("next"):
            page += 1
            time.sleep(DELAY)
        else:
            break

    log.info("  Found %d sections", len(section_list))

    # === SOURCE 3: Per-section ===
    log.info("Fetching per-section ...")
    for sec in section_list:
        sec_url = sec["self_url"]
        if "?" in sec_url:
            sec_url += "&content_size=50"
        else:
            sec_url += "?content_size=50"

        try:
            resp = session.get(sec_url, headers=h, timeout=30)
            if resp.status_code != 200:
                continue
        except Exception:
            continue

        data = resp.json()
        items = []
        if isinstance(data, dict):
            attrs = data.get("attributes", {})
            if isinstance(attrs, dict) and attrs.get("contents"):
                items.extend(attrs["contents"])
            included = data.get("included", [])
            if included:
                items.extend(included)
            d = data.get("data", [])
            if isinstance(d, list):
                items.extend(d)
            elif isinstance(d, dict):
                items.append(d)

        found = 0
        for item in items:
            if _parse_content_item(item, sec["title"], channels):
                found += 1

        log.info("  [%s] %d new", sec["title"], found)
        time.sleep(DELAY)

    result = list(channels.values())
    log.info("  Total: %d unique channels", len(result))
    return result


def _parse_included(included: list, channels: dict):
    for item in included:
        if item.get("type") != "content":
            continue
        _parse_content_item(item, "Lainnya", channels)


def _parse_content_item(item: dict, group: str, channels: dict) -> bool:
    attrs = item.get("attributes", {})

    content_type = attrs.get("content_type", "")
    if content_type != "livestreaming":
        item_type = item.get("type", "")
        if "livestreaming" not in item_type.lower():
            return False

    ch_id = attrs.get("content_id")
    if not ch_id:
        id_str = str(item.get("id", ""))
        parts = id_str.split("-")
        try:
            ch_id = int(parts[-1])
        except (ValueError, IndexError):
            try:
                ch_id = int(id_str)
            except ValueError:
                return False

    try:
        ch_id = int(ch_id)
    except (ValueError, TypeError):
        return False

    if ch_id in channels:
        if group != "Lainnya":
            channels[ch_id]["group"] = group
        return False

    title = attrs.get("alt_title") or attrs.get("title", "Unknown")
    logo = attrs.get("cover_url", "")
    if not logo:
        sq = attrs.get("square_image")
        if isinstance(sq, dict):
            logo = sq.get("url", "")
        elif isinstance(sq, str):
            logo = sq
        if not logo:
            logo = attrs.get("image_landscape_url", "")

    channels[ch_id] = {
        "id": ch_id,
        "title": title,
        "logo": logo,
        "group": group,
    }
    return True


# ==================== STEP 4: GENERATE M3U ====================
def generate_m3u(channels: list, output_file: str):
    """
    Semua channel pakai proxy worker:
    - Stream: https://ch.bhns.workers.dev/ch.m3u8?id={id}
    - License: https://ch.bhns.workers.dev/dh.m3u8?id={id}
    """
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = ch.get("group", "Lainnya")

        stream = STREAM_URL.format(id=ch_id)
        license_url = LICENSE_URL.format(id=ch_id)

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )

        # EXT VLCOPT
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')

        # Kodi DRM
        lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
        lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_url}")

        lines.append(stream)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator (Proxy)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U (Proxy Worker)")
    print(f"  Stream:  {STREAM_URL}")
    print(f"  License: {LICENSE_URL}")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    luws = bootstrap_session(session)

    if not verify_login(session):
        log.error("Login gagal! Update REMEMBER_TOKEN.")
        sys.exit(1)

    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    # Summary
    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups (%d):", len(groups))
    for g, names in sorted(groups.items()):
        preview = ", ".join(names[:4]) + ("..." if len(names) > 4 else "")
        log.info("    [%s] %d: %s", g, len(names), preview)

    # Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, args.output)


if __name__ == "__main__":
    main()   
