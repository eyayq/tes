#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
Auth: STATIC key + HMAC-SHA256 signature + user headers
Mode: Playwright (default) | curl_cffi (--no-browser)
"""

import argparse
import asyncio
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
IMPERSONATE = "chrome"

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

STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# ==================== USER INFO (dari sniffing) ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"

# ==================== COOKIES (MINIMAL) ====================
VIDIO_COOKIES = [
    {"name": "ahoy_visitor", "value": "3489c20a-d32e-468a-bd1b-8f7d150fa132", "domain": ".vidio.com", "path": "/"},
    {"name": "plenty_id", "value": USER_ID, "domain": ".vidio.com", "path": "/"},
    {"name": "remember_user_token", "value": "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T0RVME5EUXVOVGMwT1RRME5TSmQiLCJleHAiOiIyMDI4LTExLTExVDA4OjI0OjA0LjU3NFoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D--b871f122936d7bcfb13b0c6d8f8663c3d97e0595", "domain": ".vidio.com", "path": "/"},
    {"name": "country_id", "value": "ID", "domain": ".vidio.com", "path": "/"},
]

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
_BROWSER_PROFILE = os.path.join(_BASE_DIR, ".vidio_browser")
_COOKIES_FILE = os.path.join(_BASE_DIR, ".vidio_cookies.json")
_STATE_FILE = os.path.join(_BASE_DIR, ".vidio_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== UTILS ====================
def _load_cookies():
    env = os.environ.get("VIDIO_COOKIES", "")
    if env:
        try:
            return json.loads(env)
        except json.JSONDecodeError:
            pass
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    if VIDIO_COOKIES:
        return VIDIO_COOKIES
    return None


def generate_signature() -> tuple:
    """
    Generate x-client + x-signature.
    x-client = float timestamp (contoh: 1794398285.98)
    x-signature = HMAC-SHA256(key="V1d10D3v:{t}", message=str(t))
    """
    t = time.time()  # FLOAT
    t_str = str(t)
    key = f"V1d10D3v:{t_str}"
    signature = hmac.new(
        key.encode(),
        t_str.encode(),
        hashlib.sha256
    ).hexdigest()
    return t_str, signature


def get_luws() -> str:
    """Format: UUID_userid (contoh: 1209139B-..._186957857)"""
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
        web_url = attrs.get("web_url", "") or f"/live/{ch_id}"
        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "logo": extract_logo(attrs),
            "web_url": web_url,
        })
    existing = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing:
            channels.append({"id": ch_id, "title": title, "logo": "", "web_url": f"/live/{ch_id}"})
    return channels


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str, drm_fallback: bool = True):
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


# ═══════════════════════════════════════════════════════════════
# JS SNIPPETS
# ═══════════════════════════════════════════════════════════════

JS_REFRESH_SESSION = r"""
async () => {
    try {
        const resp = await fetch('https://m.vidio.com/interactions.json', {
            method: 'GET',
            headers: {
                'x-api-platform': 'web-mobile',
                'x-secure-level': '2',
                'x-auth-prefer': 'AccessToken',
                'Accept': '*/*',
            },
            credentials: 'include',
        });
        if (resp.status === 200) return await resp.json();
        return {error: true, status: resp.status};
    } catch(e) {
        return {error: e.message};
    }
}
"""

JS_FETCH_CHANNELS = r"""
async (apiKey) => {
    try {
        const resp = await fetch(
            'https://api.vidio.com/livestreamings?stream_type=tv_stream',
            {
                method: 'GET',
                headers: {
                    'x-api-key': apiKey,
                    'x-api-platform': 'web-mobile',
                    'x-secure-level': '2',
                    'x-api-app-info': 'js/m.vidio.com',
                    'x-user-login': 'true',
                    'Accept': 'application/json',
                    'Content-Type': 'application/vnd.api+json',
                },
                credentials: 'include',
            }
        );
        if (resp.status === 200) return await resp.json();
        return {error: true, status: resp.status};
    } catch(e) {
        return {error: true, message: e.message};
    }
}
"""

# STREAM: signature di-compute di browser (HMAC-SHA256)
# Header persis seperti sniffing yang berhasil
JS_FETCH_STREAM = r"""
async (chId) => {
    // Generate signature (sama seperti Vidio JS)
    const t = Date.now() / 1000;  // FLOAT
    const tStr = t.toString();
    const key = 'V1d10D3v:' + tStr;

    let signature = '';
    try {
        const encoder = new TextEncoder();
        const cryptoKey = await crypto.subtle.importKey(
            'raw', encoder.encode(key),
            {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']
        );
        const sigBuf = await crypto.subtle.sign('HMAC', cryptoKey, encoder.encode(tStr));
        signature = Array.from(new Uint8Array(sigBuf))
            .map(b => b.toString(16).padStart(2, '0')).join('');
    } catch(e) {
        return {error: 'sig_fail', detail: e.message};
    }

    // luws format: UUID_userid
    const luws = (crypto.randomUUID ? crypto.randomUUID() : '00000000-0000-0000-0000-000000000000').toUpperCase() + '_186957857';

    try {
        const resp = await fetch(
            `https://api.vidio.com/livestreamings/${chId}/stream?initialize=true`,
            {
                method: 'GET',
                headers: {
                    'x-api-key': 'CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w=',
                    'x-api-platform': 'web-mobile',
                    'x-secure-level': '2',
                    'x-client': tStr,
                    'x-signature': signature,
                    'x-user-id': '186957857',
                    'x-user-email': 'bhianesee05@gmail.com',
                    'luws': luws,
                    'Accept': '*/*',
                },
                credentials: 'include',
            }
        );
        if (resp.status === 200) {
            const data = await resp.json();
            return {hls: data?.data?.attributes?.hls || '', status: 200};
        }
        return {hls: '', status: resp.status};
    } catch(e) {
        return {error: e.message};
    }
}
"""


# ═══════════════════════════════════════════════════════════════
# MODE: AUTO COOKIE
# ═══════════════════════════════════════════════════════════════
async def auto_login_and_save():
    from playwright.async_api import async_playwright

    print("=" * 55)
    print("  Vidio Auto Cookie")
    print("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, channel="chrome", args=["--no-sandbox"],
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://m.vidio.com", timeout=60000)
        print("\n  → Login di browser...\n")

        logged_in = False
        for i in range(60):
            await asyncio.sleep(2)
            cookies = await context.cookies("https://m.vidio.com")
            if any(c["name"] == "plenty_id" for c in cookies):
                logged_in = True
                break
            if i % 5 == 0 and i > 0:
                print(f"  ... ({i * 2}s)")

        if not logged_in:
            log.error("  Timeout!")
            await browser.close()
            return

        cookies = await context.cookies("https://m.vidio.com")
        relevant_names = {
            "ahoy_visitor", "plenty_id", "access_token",
            "ahoy_visit", "remember_user_token", "_vidio_session",
            "country_id", "luws"
        }
        relevant = [c for c in cookies if c["name"] in relevant_names]

        with open(_COOKIES_FILE, "w") as f:
            json.dump(relevant, f, indent=2)
        log.info("  ✓ %d cookies → %s", len(relevant), _COOKIES_FILE)
        await context.storage_state(path=_STATE_FILE)
        await browser.close()
        print(f"\n  ✓ Selesai!\n")


# ═══════════════════════════════════════════════════════════════
# MODE: PLAYWRIGHT
# ═══════════════════════════════════════════════════════════════
async def run_playwright(output_file: str, retries: int, drm_fallback: bool):
    from playwright.async_api import async_playwright

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: Playwright (headless)")
    print("  Auth: static key + HMAC signature + user headers")
    print("=" * 55)

    cookies = _load_cookies()
    if not cookies:
        log.error("Tidak ada cookies! Jalankan: python vidio_m3u.py --auto-cookie")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # [1/4] Init
        print("\n[1/4] Init session ...")
        await context.add_cookies(cookies)
        log.info("  ✓ Inject %d cookies", len(cookies))

        await page.goto(SITE_URL, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        result = await page.evaluate(JS_REFRESH_SESSION)
        if result.get("current_user"):
            user = result["current_user"]
            log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
            log.info("  ✓ Sub: %s", "Premium" if user.get("has_active_subscription") else "Free")
        else:
            log.error("  ✗ Session gagal! Jalankan: python vidio_m3u.py --auto-cookie")
            await context.close()
            return

        # [2/4] Fetch channels
        print("\n[2/4] Fetching channels ...")
        ch_data = await page.evaluate(JS_FETCH_CHANNELS, STATIC_API_KEY)
        if ch_data.get("error"):
            log.error("  Gagal: %s", ch_data)
            await context.close()
            return

        channels = parse_channels(ch_data)
        log.info("  ✓ %d channels", len(channels))

        # [3/4] Fetch streams
        print("\n[3/4] Fetching stream URLs ...")

        # Test 1 channel
        test_result = await page.evaluate(JS_FETCH_STREAM, channels[0]["id"])
        if not test_result.get("hls"):
            log.error("  ✗ Stream test gagal: %s", test_result)
            await context.close()
            return
        log.info("  ✓ Stream test OK: %s", channels[0]["title"])

        start = time.time()
        stream_urls = {}
        failed = []

        for i, ch in enumerate(channels, 1):
            result = await page.evaluate(JS_FETCH_STREAM, ch["id"])
            hls = result.get("hls", "")
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
            else:
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗ (%s)", i, len(channels), ch["title"],
                           result.get("status", result.get("error", "?")))
            await asyncio.sleep(DELAY)

        # Retry
        for attempt in range(1, retries + 1):
            if not failed:
                break
            log.info("  Retry %d/%d (%d) ...", attempt, retries, len(failed))
            await asyncio.sleep(RETRY_WAIT)
            still = []
            for ch in failed:
                result = await page.evaluate(JS_FETCH_STREAM, ch["id"])
                hls = result.get("hls", "")
                if hls:
                    stream_urls[ch["id"]] = hls
                    log.info("  [R%d] %s ✓", attempt, ch["title"])
                else:
                    still.append(ch)
                await asyncio.sleep(DELAY)
            failed = still

        elapsed = time.time() - start
        log.info("  Total: %d | OK: %d | Fail: %d | %.1fs",
                 len(channels), len(stream_urls), len(failed), elapsed)

        if failed:
            for ch in failed:
                log.info("    - [%s] %s", ch["id"], ch["title"])

        if not stream_urls and not drm_fallback:
            log.error("  SEMUA GAGAL.")
            await context.close()
            return

        await context.close()

    # [4/4] Generate
    print(f"\n[4/4] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file, drm_fallback)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


# ═══════════════════════════════════════════════════════════════
# MODE: CURL_CFFI
# ═══════════════════════════════════════════════════════════════
def run_curl_cffi(output_file: str, retries: int, drm_fallback: bool):
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("Error: pip install 'curl-cffi<0.14'")
        sys.exit(1)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: curl_cffi | Auth: static + HMAC + user headers")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # [1/4] Init
    print("\n[1/4] Init session ...")
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
            timeout=15,
        )
        if resp.status_code == 200:
            user = resp.json().get("current_user", {})
            if user:
                log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
            else:
                log.warning("  ✗ Tidak login")
    except Exception:
        pass

    # [2/4] Fetch channels
    print("\n[2/4] Fetching channels ...")
    ch_headers = {
        "x-api-key": STATIC_API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Origin": ORIGIN,
        "Referer": REFERER,
    }
    try:
        resp = session.get(f"{API_BASE}/livestreamings",
                           params={"stream_type": "tv_stream"},
                           headers=ch_headers, timeout=30)
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

        headers = {
            "x-api-key": STATIC_API_KEY,
            "x-api-platform": "web-mobile",
            "x-secure-level": "2",
            "x-client": x_client,
            "x-signature": x_signature,
            "x-user-id": USER_ID,
            "x-user-email": USER_EMAIL,
            "luws": luws,
            "User-Agent": USER_AGENT,
            "Origin": ORIGIN,
            "Referer": REFERER,
            "Accept": "*/*",
        }
        try:
            resp = session.get(
                f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                headers=headers, timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("attributes", {}).get("hls", "")
        except Exception:
            pass
        return ""

    # Test
    test_hls = fetch_stream(channels[0]["id"])
    if not test_hls:
        log.error("  ✗ Stream test gagal")
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

    # [4/4] Generate
    print(f"\n[4/4] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file, drm_fallback)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--no-browser", action="store_true",
                        help="Pakai curl_cffi (tanpa Playwright)")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--no-drm-fallback", action="store_true",
                        help="Jangan fallback ke DRM pattern")
    parser.add_argument("--auto-cookie", action="store_true",
                        help="Buka browser untuk login manual → simpan cookies")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.auto_cookie:
        asyncio.run(auto_login_and_save())
        return

    drm_fallback = not args.no_drm_fallback

    if args.no_browser:
        run_curl_cffi(args.output, args.retries, drm_fallback)
    else:
        asyncio.run(run_playwright(args.output, args.retries, drm_fallback))


if __name__ == "__main__":
    main()   
