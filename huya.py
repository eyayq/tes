#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
- Scrape semua channel
- Non-DRM: URL langsung dari API (dengan hmac+sign)
- DRM: URL CDN pattern (otomatis, tanpa proxy)
- EXT VLCOPT per channel

Dependencies:
    pip install "curl-cffi<0.14"

Run:
    python vidio_m3u.py
    python vidio_m3u.py -o my.m3u
    python vidio_m3u.py --loop
    python vidio_m3u.py --debug
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
    print("pip install 'curl-cffi<0.14'"); sys.exit(1)

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.3
REFRESH_MINUTES = 115

# DRM URL pattern (otomatis, tanpa hmac)
STREAM_DRM_HLS = "https://etslive-v3-vidio-com-tokenized.akamaized.net/stream/{id}/file/drm/hls/master.m3u8"
STREAM_DRM_DASH = "https://etslive-v3-vidio-com-tokenized.akamaized.net/stream/{id}/file/drm/dash/stream.mpd"

# License worker
LICENSE_WORKER = "https://ch.bhns.workers.dev/dh.mpd?id={id}"

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

# ==================== USER ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T1RneU5ERXVNekkyTkRFM05DSmQi"
    "LCJleHAiOiIyMDI4LTExLTExVDExOjU3OjIxLjMyNloiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--2c508b1dd9d15adf9f96cfcbfdbcfd3ef41d76c5"
)
# ==================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("vidio")


# ==================== X-SIGNATURE ====================
def generate_x_signature():
    t = str(time.time())
    sig = hmac.new(f"V1d10D3v:{t}".encode(), t.encode(), hashlib.sha256).hexdigest()
    return t, sig


# ==================== BOOTSTRAP ====================
def bootstrap_session(s):
    log.info("Bootstrap ...")
    s.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    s.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    s.cookies.set("country_id", "ID", domain=".vidio.com", path="/")
    try:
        r = s.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", r.status_code)
    except Exception as e:
        log.warning("  %s", e)

    at = s.cookies.get("access_token")
    if at:
        log.info("  ✓ access_token: %s...", at[:30])

    luws = s.cookies.get("luws", "") or f"{str(uuid.uuid4()).upper()}_{USER_ID}"
    log.info("  ✓ luws: %s", luws)
    return luws


# ==================== VERIFY ====================
def verify_login(s):
    try:
        r = s.get(f"{SITE_URL}/interactions.json",
                  headers={"x-api-platform": "web-mobile", "x-secure-level": "2",
                           "x-auth-prefer": "AccessToken", "origin": ORIGIN, "referer": REFERER},
                  timeout=15)
        u = r.json().get("current_user", {})
        if u:
            log.info("  ✓ Login: %s (%s)", u.get("display_name"),
                     "Premium" if u.get("has_active_subscription") else "Free")
            return True
    except Exception as e:
        log.warning("  %s", e)
    return False


# ==================== FETCH CHANNELS ====================
def fetch_all_channels(s, luws):
    h = {
        "x-api-key": API_KEY, "x-api-platform": "web-mobile", "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR, "x-user-id": USER_ID, "x-user-login": "true",
        "x-api-app-info": "js/m.vidio.com", "luws": luws,
        "origin": ORIGIN, "referer": REFERER, "accept": "application/json", "accept-language": "id",
    }
    ch = {}

    # /livestreamings
    log.info("Fetching /livestreamings ...")
    try:
        r = s.get(f"{API_BASE}/livestreamings", params={"stream_type": "tv_stream"}, headers=h, timeout=30)
        if r.status_code == 200:
            for item in r.json().get("data", []):
                a = item.get("attributes", {})
                cid = item.get("id")
                if not cid: continue
                try: cid = int(cid)
                except: continue
                logo = ""
                sq = a.get("square_image")
                if isinstance(sq, dict): logo = sq.get("url", "")
                elif isinstance(sq, str): logo = sq
                if not logo: logo = a.get("image_landscape_url", "") or a.get("cover_url", "")
                ch[cid] = {"id": cid, "title": a.get("title", "?"), "logo": logo, "group": "Lainnya"}
            log.info("  %d channels", len(ch))
    except Exception as e:
        log.error("  %s", e)

    # Sections (grouping + extra channels)
    log.info("Fetching sections ...")
    secs = []
    page = 1
    while True:
        try:
            r = s.get(f"{API_BASE}/categories/live/sections",
                      params={"page[number]": page, "page[size]": 10, "included": "contents"},
                      headers=h, timeout=30)
            if r.status_code != 200: break
        except: break
        d = r.json()
        ss = d.get("data", [])
        if isinstance(ss, dict): ss = [ss]
        for sec in ss:
            t = sec.get("attributes", {}).get("title", "Lainnya")
            self_u = sec.get("links", {}).get("self", "")
            if not self_u: self_u = f"{API_BASE}/sections/{sec.get('id')}-{t.lower().replace(' ', '-')}"
            secs.append({"title": t, "url": self_u})
        for it in d.get("included", []):
            if it.get("type") != "content": continue
            _parse_item(it, "Lainnya", ch)
        if d.get("links", {}).get("next"):
            page += 1; time.sleep(DELAY)
        else: break

    # Per-section
    for sec in secs:
        u = sec["url"] + ("&" if "?" in sec["url"] else "?") + "content_size=50"
        try:
            r = s.get(u, headers=h, timeout=30)
            if r.status_code != 200: continue
        except: continue
        d = r.json()
        items = []
        if isinstance(d, dict):
            a = d.get("attributes", {})
            if isinstance(a, dict) and a.get("contents"): items.extend(a["contents"])
            items.extend(d.get("included", []))
            dd = d.get("data", [])
            if isinstance(dd, list): items.extend(dd)
            elif isinstance(dd, dict): items.append(dd)
        n = 0
        for it in items:
            if _parse_item(it, sec["title"], ch): n += 1
        log.info("  [%s] +%d", sec["title"], n)
        time.sleep(DELAY)

    result = list(ch.values())
    log.info("  Total: %d", len(result))
    return result


def _parse_item(it, grp, ch):
    a = it.get("attributes", {})
    if a.get("content_type", "") != "livestreaming":
        if "livestreaming" not in it.get("type", "").lower(): return False
    cid = a.get("content_id")
    if not cid:
        p = str(it.get("id", "")).split("-")
        try: cid = int(p[-1])
        except:
            try: cid = int(it.get("id", ""))
            except: return False
    try: cid = int(cid)
    except: return False
    if cid in ch:
        if grp != "Lainnya": ch[cid]["group"] = grp
        return False
    t = a.get("alt_title") or a.get("title", "?")
    logo = a.get("cover_url", "")
    if not logo:
        sq = a.get("square_image")
        if isinstance(sq, dict): logo = sq.get("url", "")
        elif isinstance(sq, str): logo = sq
    ch[cid] = {"id": cid, "title": t, "logo": logo, "group": grp}
    return True


# ==================== FETCH NON-DRM URL ====================
def fetch_non_drm(s, luws, ch_id):
    """
    Fetch non-DRM URL dari API.
    Return: URL dengan hmac+sign (valid 2 jam) atau "" jika gagal.
    """
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    t, sig = generate_x_signature()
    h = {
        "x-api-key": API_KEY, "x-api-platform": "web-mobile", "x-secure-level": "2",
        "x-user-id": USER_ID, "x-user-email": USER_EMAIL, "luws": luws,
        "x-client": t, "x-signature": sig,
        "origin": ORIGIN, "referer": REFERER,
        "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
        "accept": "*/*", "accept-language": "id",
    }
    try:
        r = s.get(url, headers=h, timeout=15)
        if r.status_code == 200:
            hls = r.json().get("data", {}).get("attributes", {}).get("hls", "")
            if hls:
                return hls
        elif r.status_code == 403:
            try:
                err = r.json()
                title = err.get("errors", [{}])[0].get("title", "")
                log.debug("  ch %d: 403 %s", ch_id, title)
            except: pass
    except Exception as e:
        log.debug("  ch %d: %s", ch_id, e)
    return ""


# ==================== GENERATE M3U ====================
def generate_m3u(channels, non_drm_urls, output_file):
    """
    - Non-DRM: URL langsung (sudah ada hmac+sign)
    - DRM: URL CDN pattern (otomatis) + license worker
    """
    lines = ["#EXTM3U"]
    count = 0
    non_drm_count = 0
    drm_count = 0

    for c in channels:
        cid = c["id"]
        title = c["title"]
        logo = c.get("logo", "")
        grp = c.get("group", "Lainnya")

        non_drm = non_drm_urls.get(cid, "")
        drm_url = STREAM_DRM_HLS.format(id=cid)
        license_url = LICENSE_WORKER.format(id=cid)

        lines.append(
            f'#EXTINF:-1 tvg-id="{cid}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{grp}",{title}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')

        if non_drm:
            # Non-DRM: langsung playable (hmac+sign sudah ada di URL)
            lines.append(non_drm)
            non_drm_count += 1
        else:
            # DRM: URL CDN + license
            lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
            lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_url}")
            lines.append(drm_url)
            drm_count += 1

        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d ch: %d non-DRM, %d DRM)", output_file, count, non_drm_count, drm_count)


# ==================== MAIN ====================
def run_once(output_file, retries):
    s = cffi_requests.Session(impersonate=IMPERSONATE)
    luws = bootstrap_session(s)

    if not verify_login(s):
        log.error("Login gagal! Update REMEMBER_TOKEN.")
        return False

    channels = fetch_all_channels(s, luws)
    if not channels:
        log.error("Tidak ada channel.")
        return False

    # Summary
    groups = {}
    for c in channels:
        groups.setdefault(c["group"], []).append(c["title"])
    log.info("\n  Groups (%d):", len(groups))
    for g, names in sorted(groups.items()):
        log.info("    [%s] %d: %s", g, len(names), ", ".join(names[:4]) + ("..." if len(names) > 4 else ""))

    # Fetch non-DRM
    log.info("\nFetching non-DRM URLs (%d) ...", len(channels))
    non_drm_urls = {}
    failed = []
    for i, c in enumerate(channels, 1):
        hls = fetch_non_drm(s, luws, c["id"])
        if hls:
            non_drm_urls[c["id"]] = hls
        else:
            failed.append(c)
        if i % 10 == 0 or i == len(channels):
            log.info("  %d/%d (%d ok, %d fail)", i, len(channels), len(non_drm_urls), len(failed))
        time.sleep(DELAY)

    # Retry
    for attempt in range(1, retries + 1):
        if not failed: break
        log.info("  Retry %d/%d (%d) ...", attempt, retries, len(failed))
        time.sleep(5)
        still = []
        for c in failed:
            hls = fetch_non_drm(s, luws, c["id"])
            if hls:
                non_drm_urls[c["id"]] = hls
            else:
                still.append(c)
            time.sleep(DELAY)
        failed = still

    log.info("  Non-DRM: %d/%d", len(non_drm_urls), len(channels))

    # Generate
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, non_drm_urls, output_file)
    return True


def main():
    p = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    p.add_argument("-o", "--output", default=OUTPUT_FILE)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--loop", action="store_true", help="Auto-refresh tiap 115 menit")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()

    if a.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U Generator")
    print("  Non-DRM: direct (hmac+sign)")
    print("  DRM: CDN pattern + license worker")
    print(f"  License: {LICENSE_WORKER}")
    print("=" * 55)

    if a.loop:
        log.info("Loop mode: refresh every %d min", REFRESH_MINUTES)
        while True:
            try:
                run_once(a.output, a.retries)
            except Exception as e:
                log.error("Error: %s", e)
            log.info("Sleeping %d min ...", REFRESH_MINUTES)
            time.sleep(REFRESH_MINUTES * 60)
    else:
        if not run_once(a.output, a.retries):
            sys.exit(1)


if __name__ == "__main__":
    main()   
