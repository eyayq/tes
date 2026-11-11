#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
API Key: dynamic via POST /auth (web-desktop)
Signature: extracted from www.vidio.com page HTML
Mode: Playwright (default) | curl_cffi (--no-browser)
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
import logging
from pathlib import Path

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
API_AUTH = "https://api.vidio.com/auth"
SITE_URL = "https://m.vidio.com"
WWW_URL = "https://www.vidio.com"
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

STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# ==================== COOKIES ====================
VIDIO_COOKIES = [
    {"name": "ahoy_visitor", "value": "3489c20a-d32e-468a-bd1b-8f7d150fa132", "domain": ".vidio.com", "path": "/"},
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


def extract_signature_from_html(html: str) -> tuple:
    """
    Extract clientId + signature dari HTML.
    Handle escaped quotes (Next.js RSC): \\"clientId\\":\\"...\\",\\"signature\\":\\"...\\"
    """
    patterns = [
        r'\\"clientId\\":\s*\\"([^"\\]+)\\"\s*,\s*\\"signature\\":\s*\\"([^"\\]+)\\"',
        r'"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"',
        r'clientId.{0,10}[:=].{0,10}["\\]+([^"\\,\s]{10,})["\\]+.{0,80}signature.{0,10}[:=].{0,10}["\\]+([^"\\,\s]{10,})["\\]+',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1), m.group(2)
    return "", ""


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
        lines.append(f'#EXTVLCOPT:http-referrer=https://www.vidio.com/')
        lines.append(f'#EXTVLCOPT:http-origin=https://www.vidio.com')
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ═══════════════════════════════════════════════════════════════
# JS SNIPPETS
# ═══════════════════════════════════════════════════════════════

JS_GET_API_KEY = r"""
async () => {
    try {
        const resp = await fetch('https://api.vidio.com/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
        });
        if (resp.status === 200) {
            const data = await resp.json();
            // Handle both flat and nested response
            const key = data.api_key || (data.data && data.data.attributes && data.data.attributes.api_key) || '';
            return {api_key: key};
        }
        return {error: true, status: resp.status};
    } catch(e) {
        return {error: e.message};
    }
}
"""

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

# STREAM: web-desktop + dynamic key + signature from page
# TANPA luws, TANPA x-user-login (sama seperti Streamlink)
JS_FETCH_STREAM = r"""
async ({chId, apiKey, clientId, signature}) => {
    try {
        const resp = await fetch(
            `https://api.vidio.com/livestreamings/${chId}/stream?initialize=true`,
            {
                method: 'GET',
                headers: {
                    'x-api-key': apiKey,
                    'x-api-platform': 'web-desktop',
                    'x-secure-level': '2',
                    'x-client': clientId,
                    'x-signature': signature,
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
    print("  Stream: web-desktop + dynamic key + page signature")
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

        api_key_result = await page.evaluate(JS_GET_API_KEY)
        if api_key_result.get("api_key"):
            dynamic_key = api_key_result["api_key"]
            log.info("  ✓ Dynamic API Key: %s...", dynamic_key[:20])
        else:
            log.warning("  ✗ Gagal dynamic key, fallback static")
            dynamic_key = STATIC_API_KEY

        # [2/4] Fetch channels (static key, web-mobile)
        print("\n[2/4] Fetching channels ...")
        ch_data = await page.evaluate(JS_FETCH_CHANNELS, STATIC_API_KEY)
        if ch_data.get("error"):
            log.error("  Gagal: %s", ch_data)
            await context.close()
            return

        channels = parse_channels(ch_data)
        log.info("  ✓ %d channels", len(channels))

        # [3/4] Fetch streams (dynamic key, web-desktop, www.vidio.com)
        print("\n[3/4] Fetching stream URLs ...")

        start = time.time()
        stream_urls = {}
        failed = []

        for i, ch in enumerate(channels, 1):
            ch_id = ch["id"]
            web_url = ch["web_url"]

            # Navigate ke www.vidio.com (desktop) untuk signature
            try:
                await page.goto(
                    f"{WWW_URL}{web_url}",
                    timeout=30000,
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(1)
            except Exception as e:
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗ nav: %s", i, len(channels), ch["title"], str(e)[:50])
                continue

            # Extract signature
            try:
                html = await page.content()
            except Exception:
                await asyncio.sleep(2)
                try:
                    html = await page.content()
                except Exception:
                    failed.append(ch)
                    log.warning("  [%d/%d] %s ✗ content", i, len(channels), ch["title"])
                    continue

            client_id, signature = extract_signature_from_html(html)
            if not client_id:
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗ (no sig)", i, len(channels), ch["title"])
                continue

            # Fetch stream (web-desktop + dynamic key)
            result = await page.evaluate(JS_FETCH_STREAM, {
                "chId": ch_id,
                "apiKey": dynamic_key,
                "clientId": client_id,
                "signature": signature,
            })
            hls = result.get("hls", "")
            if hls:
                stream_urls[ch_id] = hls
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
                ch_id = ch["id"]
                try:
                    await page.goto(
                        f"{WWW_URL}{ch['web_url']}",
                        timeout=30000,
                        wait_until="domcontentloaded",
                    )
                    await asyncio.sleep(1)
                except Exception:
                    still.append(ch)
                    continue

                try:
                    html = await page.content()
                except Exception:
                    await asyncio.sleep(2)
                    try:
                        html = await page.content()
                    except Exception:
                        still.append(ch)
                        continue

                client_id, signature = extract_signature_from_html(html)
                if not client_id:
                    still.append(ch)
                    continue

                result = await page.evaluate(JS_FETCH_STREAM, {
                    "chId": ch_id,
                    "apiKey": dynamic_key,
                    "clientId": client_id,
                    "signature": signature,
                })
                hls = result.get("hls", "")
                if hls:
                    stream_urls[ch_id] = hls
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
                log.info("    - [%s] %s (%s)", ch["id"], ch["title"], ch["web_url"])

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
    print("  Mode: curl_cffi | Stream: web-desktop + dynamic key")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # [1/4] Init
    print("\n[1/4] Init session ...")
    try:
        session.get(SITE_URL, timeout=30)
    except Exception as e:
        log.warning("  Visit gagal: %s", e)

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

    try:
        resp = session.post(API_AUTH, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            dynamic_key = data.get("api_key") or (data.get("data", {}).get("attributes", {}).get("api_key", ""))
            if dynamic_key:
                log.info("  ✓ Dynamic API Key: %s...", dynamic_key[:20])
            else:
                dynamic_key = STATIC_API_KEY
                log.warning("  ✗ Key kosong, fallback static")
        else:
            dynamic_key = STATIC_API_KEY
            log.warning("  ✗ POST /auth HTTP %d", resp.status_code)
    except Exception:
        dynamic_key = STATIC_API_KEY

    # [2/4] Fetch channels
    print("\n[2/4] Fetching channels ...")
    headers = {
        "x-api-key": STATIC_API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
    }
    try:
        resp = session.get(f"{API_BASE}/livestreamings",
                           params={"stream_type": "tv_stream"},
                           headers=headers, timeout=30)
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

    def get_signature(web_url: str) -> tuple:
        try:
            resp = session.get(
                f"{WWW_URL}{web_url}",
                headers={"Accept": "text/html", "User-Agent": USER_AGENT},
                timeout=20,
            )
            if resp.status_code == 200 and "streamSignature" in resp.text:
                return extract_signature_from_html(resp.text)
        except Exception:
            pass
        return "", ""

    def fetch_stream(ch_id: int, client_id: str, signature: str) -> str:
        try:
            resp = session.get(
                f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                headers={
                    "x-api-key": dynamic_key,
                    "x-api-platform": "web-desktop",
                    "x-secure-level": "2",
                    "x-client": client_id,
                    "x-signature": signature,
                    "User-Agent": USER_AGENT,
                    "Origin": "https://www.vidio.com",
                    "Referer": "https://www.vidio.com/",
                    "Accept": "*/*",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("attributes", {}).get("hls", "")
        except Exception:
            pass
        return ""

    # Test
    test_ch = channels[0]
    test_cid, test_sig = get_signature(test_ch["web_url"])
    if not test_cid:
        log.error("  ✗ Gagal signature: %s", test_ch["web_url"])
        sys.exit(1)
    log.info("  ✓ Signature: clientId=%s sig=%s...", test_cid, test_sig[:20])

    test_hls = fetch_stream(test_ch["id"], test_cid, test_sig)
    if not test_hls:
        log.error("  ✗ Stream test gagal")
        sys.exit(1)
    log.info("  ✓ Stream test OK: %s", test_ch["title"])

    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        client_id, signature = get_signature(ch["web_url"])
        if not client_id:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗ (no sig)", i, len(channels), ch["title"])
            time.sleep(DELAY)
            continue

        hls = fetch_stream(ch["id"], client_id, signature)
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
            client_id, signature = get_signature(ch["web_url"])
            if not client_id:
                still.append(ch)
                continue
            hls = fetch_stream(ch["id"], client_id, signature)
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
