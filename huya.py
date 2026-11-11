

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
# COOKIES — UPDATE DI SINI SAJA (dari browser DevTools)
# F12 → Application → Cookies → https://m.vidio.com
# ═══════════════════════════════════════════════════════════════
VIDIO_COOKIES = [
    {"name": "ahoy_visitor", "value": "3489c20a-d32e-468a-bd1b-8f7d150fa132", "domain": ".vidio.com", "path": "/"},
    {"name": "ahoy_visit", "value": "a3846ac7-aa8d-4da6-a541-0c143ba6e352", "domain": ".vidio.com", "path": "/"},
    {"name": "luws", "value": "9980CC03-6E54-454A-4E3A-8C84D2BCEFDC_186957857", "domain": ".vidio.com", "path": "/"},
    {"name": "remember_user_token", "value": "eyJfcmFpbHMi...--4eeecff53f178ea4f8362b341a43b9bb53b51274", "domain": ".vidio.com", "path": "/"},
    {"name": "access_token", "value": "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30sImV4cCI6MTc5NDQ0MzM3MX0.vIjiSE4zy4-12PSqMrov5hxB6kwKdr26_EDVmeI6sfY", "domain": ".vidio.com", "path": "/"},
    {"name": "country_id", "value": "ID", "domain": ".vidio.com", "path": "/"},
]
# ═══════════════════════════════════════════════════════════════

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
    """Load cookies: env var → script → file."""
    env_cookies = os.environ.get("VIDIO_COOKIES", "")
    if env_cookies:
        try:
            return json.loads(env_cookies)
        except json.JSONDecodeError:
            pass
    if VIDIO_COOKIES:
        return VIDIO_COOKIES
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    return None


# ─── X-Signature ─────────────────────────────────────────────────
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


# ─── Auto Login via Playwright ───────────────────────────────────
async def auto_login():
    """
    Playwright persistent context → inject cookies → visit m.vidio.com
    → capture luws + user_id + user_email dari session.
    Return: (luws, user_id, user_email)
    """
    from playwright.async_api import async_playwright

    cookies = _load_cookies()
    if not cookies:
        log.error("Tidak ada cookies! Update VIDIO_COOKIES di script.")
        return "", "", ""

    captured = {"luws": "", "user_id": "", "user_email": ""}

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Inject cookies
        await context.add_cookies(cookies)
        log.info("  ✓ Inject %d cookies", len(cookies))

        # Intercept interactions.json untuk ambil user_id + email
        def on_response(response):
            if "interactions.json" in response.url:
                asyncio.ensure_future(_capture_user(response))

        async def _capture_user(response):
            try:
                body = await response.json()
                user = body.get("current_user", {})
                if user:
                    captured["user_id"] = str(user.get("user_id", ""))
                    captured["user_email"] = user.get("email", "")
            except Exception:
                pass

        page.on("response", on_response)

        # Navigate to m.vidio.com
        await page.goto(SITE_URL, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_load_state("domcontentloaded", timeout=30000)

        # Wait for luws cookie + user data
        for _ in range(30):
            all_cookies = {c["name"]: c["value"] for c in await context.cookies()}
            captured["luws"] = all_cookies.get("luws", "")
            if captured["luws"] and captured["user_id"]:
                break
            await asyncio.sleep(1)

        # Fallback: ambil luws dari document.cookie
        if not captured["luws"]:
            doc_cookie = await page.evaluate("() => document.cookie")
            for part in doc_cookie.split(";"):
                part = part.strip()
                if part.startswith("luws="):
                    captured["luws"] = part[5:]
                    break

        # Fallback: ambil user_id dari cookies (plenty_id)
        if not captured["user_id"]:
            all_cookies = {c["name"]: c["value"] for c in await context.cookies()}
            captured["user_id"] = all_cookies.get("plenty_id", "")

        await context.close()

    return captured["luws"], captured["user_id"], captured["user_email"]


# ─── Fetch Channels ──────────────────────────────────────────────
def fetch_tv_stream_channels(session, luws: str, user_id: str) -> list:
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true" if user_id else "false",
        "x-user-id": user_id,
        "luws": luws,
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


def fetch_all_channels(session, luws: str, user_id: str) -> list:
    channels = fetch_tv_stream_channels(session, luws, user_id)
    existing_ids = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing_ids:
            channels.append({"id": ch_id, "title": title, "logo": ""})
    log.info("  Total: %d channels", len(channels))
    return channels


# ─── Fetch Stream URL ────────────────────────────────────────────
def fetch_stream_url(session, ch_id: int, luws: str, user_id: str, user_email: str) -> str:
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
            data = resp.json()
            return data.get("data", {}).get("attributes", {}).get("hls", "")
    except Exception:
        pass

    return ""


# ─── M3U Generator ───────────────────────────────────────────────
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


# ─── Main ────────────────────────────────────────────────────────
async def async_main(output_file, retries):
    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Auto-login: Playwright persistent context")
    print("=" * 55)

    # [1/3] Auto login
    print("\n[1/3] Auto login ...")
    luws, user_id, user_email = await auto_login()

    if not luws:
        log.error("  ✗ Gagal ambil luws!")
        log.error("  Update VIDIO_COOKIES di script.")
        return

    log.info("  ✓ luws: %s", luws)
    log.info("  ✓ user_id: %s", user_id or "(anonymous)")
    log.info("  ✓ email: %s", user_email or "(none)")

    # [2/3] Fetch channels + streams
    print("\n[2/3] Fetching channels + streams ...")
    session = cffi_requests.Session(impersonate=IMPERSONATE)
    channels = fetch_all_channels(session, luws, user_id)

    if not channels:
        log.error("Tidak ada channel.")
        return

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels:
        log.info("    [%s] %s", ch["id"], ch["title"])

    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        hls = fetch_stream_url(session, ch["id"], luws, user_id, user_email)
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
        log.info("\n  Retry %d/%d (%d) ...", attempt, retries, len(failed))
        time.sleep(RETRY_WAIT)
        still_failed = []
        for ch in failed:
            hls = fetch_stream_url(session, ch["id"], luws, user_id, user_email)
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
        log.error("\n  SEMUA GAGAL. Cookies kemungkinan expired.")
        log.error("  Update VIDIO_COOKIES di script.")
        return

    # [3/3] Generate M3U
    print(f"\n[3/3] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file)

    print(f"\n{'=' * 55}")
    print(f"  ✓: {len(stream_urls)} channels")
    print(f"  ✓: {output_file}")
    print(f"{'=' * 55}")


# ─── Entry point ─────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vidio M3U Generator')
    parser.add_argument('-o', '--output', type=str, default=OUTPUT_FILE)
    parser.add_argument('--retries', type=int, default=2)
    args = parser.parse_args()

    asyncio.run(async_main(args.output, args.retries))   
