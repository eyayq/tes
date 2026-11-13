#!/usr/bin/env python3
"""

"""

import argparse
import base64
import json
import sys
import time
import uuid
import logging
from pathlib import Path
from urllib.parse import urlencode, quote

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
PROXY_API = "https://exsample.d-n-s.name/api.php"
PROXY_STREAM = "https://exsample.d-n-s.name/ch.m3u8"
SECRET = "ganti-dengan-secret-panjang"

SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 1.0  # ← NAIKKAN: 1 detik (hindari overload server)
REFRESH_MINUTES = 115

STREAM_DRM_HLS = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)
LICENSE_WORKER = "https://b.bhns.workers.dev/license.mpd"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

# ==================== USER ====================
USER_ID = "186957857"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
AHOY_VISIT = "77150c04-67f5-410d-be84-f1777dcbde34"
PLenty_ID = "186957857"
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFExTXpneE5EZ3VPVGM0TWpNeU9TSmQi"
    "LCJleHAiOiIyMDI4LTExLTEzVDAyOjQ5OjA4Ljk3OFoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--97555f39c412f51a660eccea1b164f6d72428002"
)
_VIDIO_SESSION = (
    "uc%2Fdmb9Wd0QyRez8hMxdo7VoYtGTU%2FyNzKzLXQefq3sqv7WifEmgerkGkm1ysg07vLPetTegYUY28qkGSEkE3RNFrbKrfFu3ywvinKtYXDfgQ9tuzJhDuqS8ozmTfgIeHcpzvRnPkOcqnBdgsFwkNeBHOlxEAgrNRxxdyteRTDILKpqQOsD7krPRrkbw41cNFh3lX8QrFOKdlj4ojUSk%2FxryrL%2BJMN4A9KrxwcIVSSVWRbOJRD20gMdQoveCUhvd2fFQiPhzM3NgwEJxQqOgkfPoLhOtAA%3D%3D"
    "--y2hnlFP4gYRoX1Yy--4qRsvj9oIT8slQKOOrcLDg%3D%3D"
)
# ==================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")

session = None
COOKIE_STR = ""  # di-set setelah bootstrap


# ==================== BOOTSTRAP (di Python) ====================
def bootstrap() -> str:
    """Visit m.vidio.com, kumpulkan semua cookies termasuk access_token."""
    global COOKIE_STR
    log.info("Bootstrap session ...")

    session.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    session.cookies.set("ahoy_visit", AHOY_VISIT, domain=".vidio.com", path="/")
    session.cookies.set("plenty_id", PLenty_ID, domain=".vidio.com", path="/")
    session.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("country_id", "ID", domain=".vidio.com", path="/")
    session.cookies.set("_vidio_session", _VIDIO_SESSION, domain=".vidio.com", path="/")
    session.cookies.set("shva", "1", domain=".vidio.com", path="/")

    try:
        resp = session.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", resp.status_code)
    except Exception as e:
        log.warning("  Bootstrap error: %s", e)

    # Kumpulkan semua cookies jadi string
    cookie_parts = []
    for name, value in session.cookies.items():
        cookie_parts.append(f"{name}={value}")
    COOKIE_STR = "; ".join(cookie_parts)

    at = session.cookies.get("access_token")
    if at:
        try:
            payload = at.split(".")[1]
            payload += "=" * (4 - len(payload) % 4)
            exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
            exp_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(exp))
            log.info("  ✓ access_token: %s... (exp: %s)", at[:30], exp_str)
        except Exception:
            log.info("  ✓ access_token: %s...", at[:30])
    else:
        log.warning("  ✗ access_token tidak di-set!")

    return COOKIE_STR


# ==================== PROXY API CALL ====================
def proxy_api_get(params: dict, timeout: int = 30) -> dict:
    """GET ke proxy API, kirim cookie string."""
    global COOKIE_STR
    params["auth"] = SECRET
    if COOKIE_STR:
        params["cookie"] = COOKIE_STR
    url = f"{PROXY_API}?{urlencode(params)}"
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        log.debug("  API HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.debug("  API error: %s", str(e)[:100])
    return {}


# ==================== STEP 1: VERIFY LOGIN ====================
def verify_login() -> bool:
    log.info("Verify login ...")
    data = proxy_api_get({"api": "interactions"}, timeout=15)
    user = data.get("current_user", {})
    if user:
        log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
        log.info("  ✓ Sub: %s", "Premium" if user.get("has_active_subscription") else "Free")
        return True
    else:
        log.error("  ✗ Login gagal")
        return False


# ==================== STEP 2: FETCH ALL CHANNELS ====================
def fetch_all_channels() -> list:
    log.info("Fetching all channels ...")
    channels = {}
    page = 1

    while True:
        data = proxy_api_get({"api": "channels", "page": page}, timeout=30)
        if not data:
            log.warning("  Page %d: no data", page)
            break

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

        included = data.get("included", [])
        if page == 1 and included:
            types = {}
            for item in included:
                t = item.get("type", "?")
                types[t] = types.get(t, 0) + 1
            log.info("  [DEBUG] included types: %s", types)

        for item in included:
            if item.get("type") != "content":
                continue
            attrs = item.get("attributes", {})
            if attrs.get("content_type", "") != "livestreaming":
                continue

            ch_id = attrs.get("content_id")
            if not ch_id:
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

            title = attrs.get("alt_title") or attrs.get("title", "Unknown")
            logo = attrs.get("cover_url", "")
            if not logo:
                sq = attrs.get("square_image")
                if isinstance(sq, dict):
                    logo = sq.get("url", "")
                elif isinstance(sq, str):
                    logo = sq

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

            drm_stream = attrs.get("stream_url", "")
            is_drm = bool(drm_stream)

            channels[ch_id] = {
                "id": ch_id,
                "title": title,
                "logo": logo,
                "group": group,
                "drm_stream": drm_stream,
                "is_drm": is_drm,
            }

        links = data.get("links", {})
        if links.get("next"):
            page += 1
            time.sleep(DELAY)
        else:
            break

    result = list(channels.values())
    drm_count = sum(1 for c in result if c["is_drm"])
    log.info("  Found %d unique channels (%d DRM, %d non-DRM)", len(result), drm_count, len(result) - drm_count)
    return result


# ==================== STEP 3: FETCH STREAM URL ====================
def fetch_stream_url(ch_id: int) -> str:
    data = proxy_api_get({"api": "stream", "id": ch_id}, timeout=20)
    hls = data.get("data", {}).get("attributes", {}).get("hls", "")
    return hls


# ==================== STEP 4: GENERATE M3U ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0
    non_drm_count = 0
    drm_count = 0
    skip_count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = ch.get("group", "Lainnya")
        is_drm = ch.get("is_drm", False)

        non_drm_url = stream_urls.get(ch_id, "")
        drm_url = ch.get("drm_stream", "") or STREAM_DRM_HLS.format(id=ch_id)
        license_url = f"{LICENSE_WORKER}?id={ch_id}"

        if is_drm:
            lines.append(
                f'#EXTINF:-1 tvg-id="{ch_id}" '
                f'tvg-name="{title}" '
                f'tvg-logo="{logo}" '
                f'group-title="{group}",'
                f'{title}'
            )
            lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
            lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_url}")
            lines.append(drm_url)
            drm_count += 1
            count += 1

        elif non_drm_url:
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
            lines.append(f"{PROXY_STREAM}?id={ch_id}")
            non_drm_count += 1
            count += 1

        else:
            log.debug("  [SKIP] %s (id=%d): non-DRM, stream URL gagal", title, ch_id)
            skip_count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels: %d non-DRM, %d DRM, %d skip)",
             output_file, count, non_drm_count, drm_count, skip_count)


# ==================== MAIN ====================
def run_once(output_file: str, drm_only: bool, retries: int) -> bool:
    global session
    session = cffi_requests.Session(impersonate=IMPERSONATE)

    bootstrap()

    if not verify_login():
        log.error("Login gagal!")
        return False

    channels = fetch_all_channels()
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        return False

    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups (%d):", len(groups))
    for g, names in sorted(groups.items()):
        preview = ", ".join(names[:4]) + ("..." if len(names) > 4 else "")
        log.info("    [%s] %d: %s", g, len(names), preview)

    stream_urls = {}
    non_drm_channels = [ch for ch in channels if not ch["is_drm"]]

    if not drm_only and non_drm_channels:
        log.info("\nFetching stream URLs (%d non-DRM channels) ...", len(non_drm_channels))
        failed = []
        for i, ch in enumerate(non_drm_channels, 1):
            hls = fetch_stream_url(ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
            else:
                failed.append(ch)
            if i % 10 == 0 or i == len(non_drm_channels):
                log.info("  Progress: %d/%d (%d ok, %d fail)", i, len(non_drm_channels), len(stream_urls), len(failed))
            time.sleep(DELAY)

        for attempt in range(1, retries + 1):
            if not failed:
                break
            log.info("  Retry %d/%d (%d failed) ...", attempt, retries, len(failed))
            time.sleep(10)  # ← 10 detik jeda sebelum retry
            still = []
            for ch in failed:
                hls = fetch_stream_url(ch["id"])
                if hls:
                    stream_urls[ch["id"]] = hls
                else:
                    still.append(ch)
                time.sleep(DELAY)
            failed = still

        log.info("  Non-DRM: %d/%d", len(stream_urls), len(non_drm_channels))

    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, output_file)
    return True


def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator (2 Proxy)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--drm-only", action="store_true")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print(f"  Proxy API:    {PROXY_API}")
    print(f"  Proxy Stream: {PROXY_STREAM}")
    print(f"  License:      {LICENSE_WORKER}")
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
