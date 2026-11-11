#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
Signature: di-scrape dari page RSC (bukan HMAC client-side)
Mode: Playwright (default) | curl_cffi (--no-browser)
"""

import argparse
import asyncio
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
WWW_URL = "https://www.vidio.com"
LIVE_URL = "https://m.vidio.com/live"
OUTPUT_FILE = "playlist.m3u"
DELAY = 0.3
RETRY_WAIT = 5
IMPERSONATE = "chrome"

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://www.vidio.com/"
ORIGIN = "https://www.vidio.com"

STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# ==================== COOKIES (MINIMAL) ====================
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
    """Prioritas: square_image.url → image_landscape_url → cover_url"""
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

JS_REFRESH_SESSION = """
async () => {
    try {
        const resp = await fetch('https://m.vidio.com/interactions.json', {
            method: 'GET',
            headers: {
                'x-api-key': 'CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w=',
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

JS_FETCH_CHANNELS = """
async () => {
    try {
        const resp = await fetch(
            'https://api.vidio.com/livestreamings?stream_type=tv_stream',
            {
                method: 'GET',
                headers: {
                    'x-api-key': 'CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w=',
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

# Signature di-scrape dari RSC page — BUKAN di-compute client-side
JS_GET_SIGNATURE = """
async (chId) => {
    try {
        const rscParam = Math.random().toString(36).slice(2, 12);
        const resp = await fetch(
            `https://m.vidio.com/live/${chId}?_rsc=${rscParam}`,
            {
                method: 'GET',
                headers: {
                    'rsc': '1',
                    'accept': '*/*',
                },
                credentials: 'include',
            }
        );
        if (!resp.ok) return {error: 'rsc_fail', status: resp.status};

        const text = await resp.text();

        // Cari streamSignature di RSC payload
        // Pattern: "streamSignature":{"clientId":"...","signature":"..."}
        const patterns = [
            /"streamSignature"\s*:\s*\{\s*"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"/,
            /"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"/,
        ];

        for (const pat of patterns) {
            const m = text.match(pat);
            if (m) {
                return {clientId: m[1], signature: m[2]};
            }
        }

        return {error: 'no_signature_found', sample: text.substring(0, 300)};
    } catch(e) {
        return {error: e.message};
    }
}
"""

JS_FETCH_STREAM = """
async (chId, clientId, signature) => {
    let luws = '';
    const m = document.cookie.match(/(?:^|;\\s*)luws=([^;]+)/);
    if (m) luws = m[1];

    try {
        const resp = await fetch(
            `https://api.vidio.com/livestreamings/${chId}/stream?initialize=true`,
            {
                method: 'GET',
                headers: {
                    'x-api-key': 'CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w=',
                    'x-api-platform': 'web-desktop',
                    'x-secure-level': '2',
                    'x-client': clientId,
                    'x-signature': signature,
                    'Origin': 'https://www.vidio.com',
                    'Referer': 'https://www.vidio.com/',
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
    print("  Browser terbuka — login manual di sana.")
    print("  Script otomatis deteksi & simpan cookies.")
    print("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--no-sandbox"],
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://m.vidio.com", timeout=60000)
        print("\n  → Login di browser yang terbuka...")
        print("  → Script lanjut otomatis setelah login terdeteksi.\n")

        logged_in = False
        for i in range(60):
            await asyncio.sleep(2)
            cookies = await context.cookies("https://m.vidio.com")
            if any(c["name"] == "plenty_id" for c in cookies):
                logged_in = True
                break
            if i % 5 == 0 and i > 0:
                print(f"  ... menunggu login ({i * 2}s)")

        if not logged_in:
            log.error("  Timeout! Login tidak terdeteksi dalam 120 detik.")
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
        log.info("  ✓ Storage state → %s", _STATE_FILE)

        await browser.close()
        print(f"\n  ✓ Selesai! Run berikutnya otomatis pakai cookies ini.\n")


# ═══════════════════════════════════════════════════════════════
# MODE: PLAYWRIGHT
# ═══════════════════════════════════════════════════════════════
async def run_playwright(output_file: str, retries: int, drm_fallback: bool):
    from playwright.async_api import async_playwright

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: Playwright (headless)")
    print("  Signature: scraped from RSC page")
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

        # [1/4] Inject + refresh
        print("\n[1/4] Inject cookies + refresh session ...")
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
        ch_data = await page.evaluate(JS_FETCH_CHANNELS)
        if ch_data.get("error"):
            log.error("  Gagal: %s", ch_data)
            await context.close()
            return

        channels = parse_channels(ch_data)
        log.info("  ✓ %d channels", len(channels))

        # [3/4] Fetch streams (signature dari page)
        print("\n[3/4] Fetching stream URLs ...")

        # Test 1 channel
        test_sig = await page.evaluate(JS_GET_SIGNATURE, channels[0]["id"])
        if test_sig.get("error"):
            log.error("  ✗ Gagal ambil signature: %s", test_sig)
            await context.close()
            return
        log.info("  ✓ Signature OK: clientId=%s...", test_sig["clientId"][:16])

        test_stream = await page.evaluate(
            JS_FETCH_STREAM, channels[0]["id"], test_sig["clientId"], test_sig["signature"]
        )
        if not test_stream.get("hls"):
            log.error("  ✗ Stream test gagal: %s", test_stream)
            await context.close()
            return
        log.info("  ✓ Stream test OK: %s", channels[0]["title"])

        start = time.time()
        stream_urls = {}
        failed = []

        for i, ch in enumerate(channels, 1):
            ch_id = ch["id"]

            # Ambil signature dari page
            sig = await page.evaluate(JS_GET_SIGNATURE, ch_id)
            if sig.get("error"):
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗ sig: %s", i, len(channels), ch["title"], sig["error"])
                await asyncio.sleep(DELAY)
                continue

            # Fetch stream
            result = await page.evaluate(JS_FETCH_STREAM, ch_id, sig["clientId"], sig["signature"])
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
                sig = await page.evaluate(JS_GET_SIGNATURE, ch_id)
                if sig.get("error"):
                    still.append(ch)
                    continue
                result = await page.evaluate(JS_FETCH_STREAM, ch_id, sig["clientId"], sig["signature"])
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

    import re

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: curl_cffi (TLS impersonation)")
    print("  Signature: scraped from RSC page")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # [1/4] Refresh session
    print("\n[1/4] Refresh session ...")
    try:
        session.get(SITE_URL, timeout=30)
    except Exception as e:
        log.warning("  Visit gagal: %s", e)

    luws = session.cookies.get("luws", "") or f"{str(uuid.uuid4()).upper()}_"
    ahoy_visitor = session.cookies.get("ahoy_visitor", "")
    log.info("  ✓ luws: %s...", luws[:16])

    try:
        resp = session.get(
            f"{SITE_URL}/interactions.json",
            headers={
                "x-api-key": API_KEY,
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
        else:
            log.warning("  interactions.json HTTP %d", resp.status_code)
    except Exception as e:
        log.warning("  Verify gagal: %s", e)

    # [2/4] Fetch channels
    print("\n[2/4] Fetching channels ...")
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": ahoy_visitor,
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
        "luws": luws,
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
    }
    try:
        resp = session.get(f"{API_BASE}/livestreamings", params={"stream_type": "tv_stream"},
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

    def get_signature(ch_id: int) -> tuple:
        """Ambil clientId + signature dari RSC page."""
        try:
            rsc_param = uuid.uuid4().hex[:10]
            resp = session.get(
                f"{SITE_URL}/live/{ch_id}?_rsc={rsc_param}",
                headers={"rsc": "1", "Accept": "*/*"},
                timeout=15,
            )
            if resp.status_code != 200:
                return "", ""
            text = resp.text
            # Pattern 1: "streamSignature":{"clientId":"...","signature":"..."}
            m = re.search(
                r'"streamSignature"\s*:\s*\{\s*"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"',
                text
            )
            if not m:
                m = re.search(
                    r'"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"',
                    text
                )
            if m:
                return m.group(1), m.group(2)
        except Exception:
            pass
        return "", ""

    def fetch_stream(ch_id: int, client_id: str, signature: str) -> str:
        """Fetch stream URL dengan signature dari page."""
        try:
            resp = session.get(
                f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                headers={
                    "x-api-key": API_KEY,
                    "x-api-platform": "web-desktop",
                    "x-secure-level": "2",
                    "x-client": client_id,
                    "x-signature": signature,
                    "Origin": ORIGIN,
                    "Referer": REFERER,
                    "luws": luws,
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("attributes", {}).get("hls", "")
        except Exception:
            pass
        return ""

    # Test 1 channel
    test_cid, test_sig = get_signature(channels[0]["id"])
    if not test_cid:
        log.error("  ✗ Gagal ambil signature untuk channel pertama")
        sys.exit(1)
    test_hls = fetch_stream(channels[0]["id"], test_cid, test_sig)
    if not test_hls:
        log.error("  ✗ Stream test gagal")
        sys.exit(1)
    log.info("  ✓ Stream test OK: %s", channels[0]["title"])

    start = time.time()
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        ch_id = ch["id"]
        client_id, signature = get_signature(ch_id)
        if not client_id:
            failed.append(ch)
            log.warning("  [%d/%d] %s ✗ (no signature)", i, len(channels), ch["title"])
            time.sleep(DELAY)
            continue

        hls = fetch_stream(ch_id, client_id, signature)
        if hls:
            stream_urls[ch_id] = hls
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
            ch_id = ch["id"]
            client_id, signature = get_signature(ch_id)
            if not client_id:
                still.append(ch)
                continue
            hls = fetch_stream(ch_id, client_id, signature)
            if hls:
                stream_urls[ch_id] = hls
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
