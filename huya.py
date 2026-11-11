#!/usr/bin/env python3
"""
VIDIO M3U PLAYLIST GENERATOR v3
Auto-login via Playwright persistent context + cookie injection.
luws di-capture dari request headers (bukan cookie).

Dependencies:
    pip install "curl-cffi<0.14" playwright
    playwright install chromium --with-deps

Run:
    xvfb-run python vidio_m3u.py
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ─── Konfigurasi ─────────────────────────────────────────────────
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
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

# ═══════════════════════════════════════════════════════════════
# COOKIES — UPDATE DI SINI SAJA
# F12 → Application → Cookies → https://m.vidio.com
# ═══════════════════════════════════════════════════════════════
VIDIO_COOKIES = [
    {"name": "ahoy_visitor", "value": "3489c20a-d32e-468a-bd1b-8f7d150fa132", "domain": ".vidio.com", "path": "/"},
    {"name": "plenty_id", "value": "186957857", "domain": ".vidio.com", "path": "/"},
    {"name": "access_token", "value": "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30sImV4cCI6MTc5NDQ0MzM3MX0.vIjiSE4zy4-12PSqMrov5hxB6kwKdr26_EDVmeI6sfY", "domain": ".vidio.com", "path": "/"},
    {"name": "ahoy_visit", "value": "8e0d4b18-a964-4bb8-9d3e-83adf56c6c82", "domain": ".vidio.com", "path": "/"},
    {"name": "remember_user_token", "value": "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T0RVME5EUXVOVGMwT1RRME5TSmQiLCJleHAiOiIyMDI4LTExLTExVDA4OjI0OjA0LjU3NFoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D--b871f122936d7bcfb13b0c6d8f8663c3d97e0595", "domain": ".vidio.com", "path": "/"},
    {"name": "_vidio_session", "value": "XcRDQ6s4yJ4D3m0v1nTGizcmWrdwjFul6N1X8k215TM%2FA9maSnrBYr3Yn3dI%2FTw6%2BZ2FQVaGnIDGjEvMMT%2Fauz9rE1tNyzLTILDSVjBgOtCHnspmOwYfzcUQA1JjfJarkwNbF5XSS1tejS5HkJhpg%2BCqTHunb4R3ehOUzmLqzrO6yfYShm2qHglIEW%2BC5FgYefFu%2BSzZLysGqVqp%2FQIelAZ71mgA9R0iliGMWb2x8jeBbtRvUKzk53dFStgb4mz%2F1APmp%2B0Ek4ACie44qYYhGehuDeHDHUQp08eo%2FUT98lvx3IWdSD%2FEiDVdGLS%2FyxgdwfumLIP0Sp5W6EhrHNknsmq60iJN0nO5mSA91pqwTq%2BGfQ8nRkEMgDycmRo7El%2FC9DAQ4xuaLjpWgtCNfyVcRziu--30px8M%2BKKTCuqRNZ--xPr0ywCQXF6Z1qFjCrYMTA%3D%3D", "domain": ".vidio.com", "path": "/"},
    {"name": "country_id", "value": "ID", "domain": ".vidio.com", "path": "/"},
]   
# ═══════════════════════════════════════════════════════════════

EXTRA_CHANNELS = {
    870: "MNCTV", 782: "ANTV", 665: "RCTI", 666: "GTV",
    667: "MDTV", 668: "iNews", 669: "Net", 670: "tvOne",
    671: "One", 672: "O Channel",
}

INTERNATIONAL = {
    "Arirang", "ABC Australia", "Euronews", "CNA",
    "France24", "MUSICA", "U-Channel TV", "Makkah TV",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BROWSER_PROFILE = os.path.join(_BASE_DIR, ".vidio_browser")
_COOKIES_FILE = os.path.join(_BASE_DIR, ".vidio_cookies.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


def _load_cookies():
    env = os.environ.get("VIDIO_COOKIES", "")
    if env:
        try:
            return json.loads(env)
        except json.JSONDecodeError:
            pass
    if VIDIO_COOKIES:
        return VIDIO_COOKIES
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    return None


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


# ─── AUTO LOGIN ──────────────────────────────────────────────────
async def auto_login() -> dict:
    """
    Playwright → inject cookies → visit m.vidio.com/live
    → capture luws dari REQUEST HEADERS + user_id dari interactions.json.
    """
    result = {"luws": "", "user_id": "", "user_email": "", "logged_in": False}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("  Playwright tidak tersedia")
        return result

    cookies = _load_cookies()
    if not cookies:
        log.warning("  Tidak ada cookies")
        return result

    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                _BROWSER_PROFILE,
                headless=True,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            page = context.pages[0] if context.pages else await context.new_page()

            await context.add_cookies(cookies)
            log.info("  ✓ Inject %d cookies", len(cookies))

            # Intercept REQUEST headers → capture luws
            def on_request(request):
                if "api.vidio.com" in request.url:
                    luws_val = request.headers.get("luws", "")
                    if luws_val and not result["luws"]:
                        result["luws"] = luws_val

            # Intercept interactions.json → user data
            def on_response(response):
                if "interactions.json" in response.url:
                    asyncio.ensure_future(_capture_user(response, result))

            async def _capture_user(response, res):
                try:
                    body = await response.json()
                    user = body.get("current_user", {})
                    if user and user.get("user_id"):
                        res["user_id"] = str(user["user_id"])
                        res["user_email"] = user.get("email", "")
                        res["logged_in"] = True
                except Exception:
                    pass

            page.on("request", on_request)
            page.on("response", on_response)

            # Navigate ke /live (trigger API call → luws di header)
            await page.goto(LIVE_URL, timeout=60000, wait_until="domcontentloaded")

            # Wait: luws + user_id
            for _ in range(30):
                if result["luws"] and result["logged_in"]:
                    break
                # Fallback: document.cookie
                if not result["luws"]:
                    try:
                        doc = await page.evaluate("() => document.cookie")
                        for part in doc.split(";"):
                            part = part.strip()
                            if part.startswith("luws="):
                                result["luws"] = part[5:]
                                break
                    except Exception:
                        pass
                await asyncio.sleep(1)

            # Fallback terakhir: context.cookies()
            if not result["luws"]:
                all_cookies = {c["name"]: c["value"] for c in await context.cookies()}
                result["luws"] = all_cookies.get("luws", "")

            # Fallback: plenty_id
            if not result["user_id"]:
                all_cookies = {c["name"]: c["value"] for c in await context.cookies()}
                result["user_id"] = all_cookies.get("plenty_id", "")
                if result["user_id"]:
                    result["logged_in"] = True

            await context.close()

    except Exception as e:
        log.error("  Playwright error: %s", e)

    return result


# ─── FETCH CHANNELS ──────────────────────────────────────────────
def fetch_channels(session, luws: str, user_id: str) -> list:
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true" if user_id else "false",
        "luws": luws,
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
    }
    if user_id:
        headers["x-user-id"] = user_id

    try:
        resp = session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  Channels HTTP %d", resp.status_code)
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

    existing = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing:
            channels.append({"id": ch_id, "title": title, "logo": ""})

    return channels


# ─── FETCH STREAM ────────────────────────────────────────────────
def fetch_stream(session, ch_id: int, luws: str, user_id: str, user_email: str) -> str:
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_sig = generate_x_signature()

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "luws": luws,
        "x-client": x_client,
        "x-signature": x_sig,
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
        "Accept": "*/*",
    }
    if user_id:
        headers["x-user-id"] = user_id
        headers["x-user-login"] = "true"
        if user_email:
            headers["x-user-email"] = user_email
    else:
        headers["x-user-login"] = "false"

    try:
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("attributes", {}).get("hls", "")
    except Exception:
        pass
    return ""


# ─── M3U GENERATOR ───────────────────────────────────────────────
def generate_m3u(channels, stream_urls, output_file):
    lines = ["#EXTM3U"]
    count = 0
    for ch in channels:
        stream_url = stream_urls.get(ch["id"], "")
        if not stream_url:
            continue
        title = ch["title"]
        logo = ch["logo"]
        group = "TV Internasional" if title in INTERNATIONAL else "TV Nasional"
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["id"]}" '
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


# ─── MAIN ────────────────────────────────────────────────────────
async def async_main(output_file, retries):
    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Auto-login: Playwright persistent context")
    print("=" * 55)

    # [1/3] Auto login
    print("\n[1/3] Auto login ...")
    info = await auto_login()
    luws = info["luws"]
    user_id = info["user_id"]
    user_email = info["user_email"]
    logged_in = info["logged_in"]

    if logged_in:
        log.info("  ✓ Login: %s (ID: %s)", user_email or "unknown", user_id)
    else:
        log.warning("  ✗ Tidak login (cookies expired?)")

    if not luws:
        log.error("  ✗ Gagal ambil luws!")
        log.error("  Update VIDIO_COOKIES di script.")
        return

    log.info("  ✓ luws: %s", luws)

    # [2/3] Fetch
    print("\n[2/3] Fetching channels + streams ...")
    session = cffi_requests.Session(impersonate=IMPERSONATE)
    channels = fetch_channels(session, luws, user_id)

    if not channels:
        log.error("Tidak ada channel.")
        return

    log.info("  Channels: %d", len(channels))

    # Test 1 channel dulu
    test = fetch_stream(session, channels[0]["id"], luws, user_id, user_email)
    if not test and user_id:
        log.info("  Login gagal, coba tanpa login ...")
        user_id = ""
        user_email = ""
        test = fetch_stream(session, channels[0]["id"], luws, "", "")
    if test:
        log.info("  ✓ Stream access OK")
    else:
        log.error("  ✗ Gagal akses stream")
        log.error("  Update VIDIO_COOKIES di script.")
        return

    # Fetch semua
    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        hls = fetch_stream(session, ch["id"], luws, user_id, user_email)
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
            hls = fetch_stream(session, ch["id"], luws, user_id, user_email)
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [R%d] %s ✓", attempt, ch["title"])
            else:
                still.append(ch)
            time.sleep(DELAY)
        failed = still

    elapsed = time.time() - start
    log.info("\n  Total: %d | OK: %d | Fail: %d | Time: %.1fs",
             len(channels), len(stream_urls), len(failed), elapsed)

    if failed:
        for ch in failed:
            log.info("    - [%s] %s", ch["id"], ch["title"])

    if not stream_urls:
        log.error("  SEMUA GAGAL. Update cookies.")
        return

    # [3/3] Generate
    print(f"\n[3/3] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


# ─── Entry point ─────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vidio M3U Generator')
    parser.add_argument('-o', '--output', type=str, default=OUTPUT_FILE)
    parser.add_argument('--retries', type=int, default=2)
    args = parser.parse_args()
    asyncio.run(async_main(args.output, args.retries))   
