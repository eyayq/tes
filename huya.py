#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator (Merged)
Mode: Playwright (default) atau curl_cffi (--no-browser)
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
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

# Fallback: DRM pattern
STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

# ==================== COOKIES ====================
# F12 → Application → Cookies → https://m.vidio.com
VIDIO_COOKIES = [
    {"name": "ahoy_visitor", "value": "3489c20a-d32e-468a-bd1b-8f7d150fa132", "domain": ".vidio.com", "path": "/"},
    {"name": "plenty_id", "value": "186957857", "domain": ".vidio.com", "path": "/"},
    {"name": "access_token", "value": "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30sImV4cCI6MTc5NDQ0MzM3MX0.vIjiSE4zy4-12PSqMrov5hxB6kwKdr26_EDVmeI6sfY", "domain": ".vidio.com", "path": "/"},
    {"name": "ahoy_visit", "value": "8e0d4b18-a964-4bb8-9d3e-83adf56c6c82", "domain": ".vidio.com", "path": "/"},
    {"name": "remember_user_token", "value": "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T0RVME5EUXVOVGMwT1RRME5TSmQiLCJleHAiOiIyMDI4LTExLTExVDA4OjI0OjA0LjU3NFoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D--b871f122936d7bcfb13b0c6d8f8663c3d97e0595", "domain": ".vidio.com", "path": "/"},
    {"name": "_vidio_session", "value": "XcRDQ6s4yJ4D3m0v1nTGizcmWrdwjFul6N1X8k215TM%2FA9maSnrBYr3Yn3dI%2FTw6%2BZ2FQVaGnIDGjEvMMT%2Fauz9rE1tNyzLTILDSVjBgOtCHnspmOwYfzcUQA1JjfJarkwNbF5XSS1tejS5HkJhpg%2BCqTHunb4R3ehOUzmLqzrO6yfYShm2qHglIEW%2BC5FgYefFu%2BSzZLysGqVqp%2FQIelAZ71mgA9R0iliGMWb2x8jeBbtRvUKzk53dFStgb4mz%2F1APmp%2B0Ek4ACie44qYYhGehuDeHDHUQp08eo%2FUT98lvx3IWdSD%2FEiDVdGLS%2FyxgdwfumLIP0Sp5W6EhrHNknsmq60iJN0nO5mSA91pqwTq%2BGfQ8nRkEMgDycmRo7El%2FC9DAQ4xuaLjpWgtCNfyVcRziu--30px8M%2BKKTCuqRNZ--xPr0ywCQXF6Z1qFjCrYMTA%3D%3D", "domain": ".vidio.com", "path": "/"},
    {"name": "country_id", "value": "ID", "domain": ".vidio.com", "path": "/"},
]

# ==================== CLASSIFIKASI GROUP ====================
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
    if VIDIO_COOKIES:
        return VIDIO_COOKIES
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    return None


def generate_x_signature() -> tuple:
    """
    Generate x-signature dan x-client untuk Vidio API.
    Algoritma: HMAC-SHA256(key="V1d10D3v:{t}", message=str(t))
    KEDUANYA PAKAI INTEGER — BUKAN FLOAT!
    """
    t = int(time.time())  # INTEGER
    secret_key = f"V1d10D3v:{t}"
    message = str(t)  # STRING DARI INTEGER

    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    x_client = str(t)  # INTEGER STRING
    return x_client, signature


def get_group(title: str) -> str:
    """Klasifikasi group berdasarkan nama channel."""
    return "TV Internasional" if title in INTERNATIONAL else "TV Nasional"


def extract_logo(attrs: dict) -> str:
    """
    Ekstrak logo channel dari response API.
    Prioritas: square_image.url → image_landscape_url → cover_url
    """
    square = attrs.get("square_image", {})
    if isinstance(square, dict) and square.get("url"):
        return square["url"]
    if attrs.get("image_landscape_url"):
        return attrs["image_landscape_url"]
    if attrs.get("cover_url"):
        return attrs["cover_url"]
    return ""


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str, drm_fallback: bool = True):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = get_group(title)

        # Prioritas: non-DRM (dari API) > DRM (pattern)
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
# MODE 1: PLAYWRIGHT (default, lebih reliable)
# ═══════════════════════════════════════════════════════════════

# JS: fetch channels via browser
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

# JS: fetch stream URL — FIXED SIGNATURE (integer, bukan float)
JS_FETCH_STREAM = """
async (chId) => {
    // FIX: pakai integer timestamp, BUKAN float
    const t = Math.floor(Date.now() / 1000);
    const xClient = t.toString();
    const key = `V1d10D3v:${t}`;

    // Generate x-signature: HMAC-SHA256
    let signature = '';
    try {
        const encoder = new TextEncoder();
        const cryptoKey = await crypto.subtle.importKey(
            'raw', encoder.encode(key),
            {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']
        );
        // FIX: message = xClient (integer string), BUKAN float
        const sigBuf = await crypto.subtle.sign('HMAC', cryptoKey, encoder.encode(xClient));
        signature = Array.from(new Uint8Array(sigBuf))
            .map(b => b.toString(16).padStart(2, '0')).join('');
    } catch(e) {
        return {error: 'sig_fail', detail: e.message};
    }

    // Baca luws dari document.cookie → kirim sebagai HEADER
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
                    'x-api-platform': 'web-mobile',
                    'x-secure-level': '2',
                    'x-client': xClient,
                    'x-signature': signature,
                    'x-user-login': 'true',
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
        return {hls: '', status: resp.status, body: await resp.text()};
    } catch(e) {
        return {error: e.message};
    }
}
"""


async def run_playwright(output_file: str, retries: int, drm_fallback: bool):
    from playwright.async_api import async_playwright

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: Playwright (TLS browser match)")
    print("=" * 55)

    cookies = _load_cookies()
    if not cookies:
        log.error("Tidak ada cookies! Set VIDIO_COOKIES di script atau file .vidio_cookies.json")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # [1/3] Inject cookies + navigate
        print("\n[1/3] Init browser + inject cookies ...")
        await context.add_cookies(cookies)
        log.info("  ✓ Inject %d cookies", len(cookies))

        await page.goto(LIVE_URL, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.goto("https://m.vidio.com/live/205-indosiar", timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # Verify
        is_logged_in = await page.evaluate("() => document.cookie.includes('plenty_id')")
        has_luws = await page.evaluate("() => document.cookie.includes('luws')")
        if is_logged_in:
            log.info("  ✓ Login OK")
        else:
            log.warning("  ✗ Tidak login — stream mungkin gagal")
        if has_luws:
            log.info("  ✓ luws aktif")
        else:
            log.warning("  ✗ luws belum ada")

        # [2/3] Fetch channels + streams
        print("\n[2/3] Fetching channels + streams ...")

        ch_data = await page.evaluate(JS_FETCH_CHANNELS)
        if ch_data.get("error"):
            log.error("  Gagal fetch channels: %s", ch_data)
            await context.close()
            return

        channels = []
        for item in ch_data.get("data", []):
            attrs = item.get("attributes", {})
            ch_id = item.get("id")
            if not ch_id:
                continue
            ch_id = int(ch_id)
            title = attrs.get("title", "Unknown")
            logo = extract_logo(attrs)  # FIX: pakai square_image.url
            channels.append({"id": ch_id, "title": title, "logo": logo})

        # Add extra channels if not in API response
        existing = {c["id"] for c in channels}
        for ch_id, title in EXTRA_CHANNELS.items():
            if ch_id not in existing:
                channels.append({"id": ch_id, "title": title, "logo": ""})

        log.info("  Channels: %d", len(channels))

        # Test 1 channel
        test_result = await page.evaluate(JS_FETCH_STREAM, channels[0]["id"])
        if test_result.get("hls"):
            log.info("  ✓ Stream access OK: %s", channels[0]["title"])
        else:
            log.error("  ✗ Test stream gagal: %s", test_result)
            log.error("  → Update VIDIO_COOKIES di script (F12 → Application → Cookies)")
            await context.close()
            return

        # Fetch all
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
            log.info("  Retry %d/%d (%d channels) ...", attempt, retries, len(failed))
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
        log.info("\n  Total: %d | OK: %d | Fail: %d | Time: %.1fs",
                 len(channels), len(stream_urls), len(failed), elapsed)

        if failed:
            for ch in failed:
                log.info("    - [%s] %s", ch["id"], ch["title"])

        if not stream_urls and not drm_fallback:
            log.error("  SEMUA GAGAL dan DRM fallback off.")
            await context.close()
            return

        await context.close()

    # [3/3] Generate
    print(f"\n[3/3] Generating M3U ...")
    generate_m3u(channels, stream_urls, output_file, drm_fallback)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


# ═══════════════════════════════════════════════════════════════
# MODE 2: CURL_CFFI (lightweight, tanpa browser)
# ═══════════════════════════════════════════════════════════════

def run_curl_cffi(output_file: str, retries: int, drm_fallback: bool):
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("Error: pip install 'curl-cffi<0.14'")
        sys.exit(1)

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Mode: curl_cffi (TLS impersonation)")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Get luws + cookies
    log.info("Visiting %s ...", SITE_URL)
    try:
        session.get(SITE_URL, timeout=30)
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    luws = session.cookies.get("luws", "")
    if not luws:
        log.info("  luws tidak di-set, generate UUID baru")
        luws = f"{str(uuid.uuid4()).upper()}_"
    else:
        log.info("  ✓ luws: %s", luws[:20] + "...")

    ahoy_visitor = session.cookies.get("ahoy_visitor", "")
    log.info("  ✓ ahoy_visitor: %s", ahoy_visitor[:20] + "..." if ahoy_visitor else "N/A")

    # Step 2: Fetch all channels
    log.info("\nFetching channels ...")
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
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
        resp = session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.error("  HTTP %d — cek cookies/API key", resp.status_code)
            sys.exit(1)
    except Exception as e:
        log.error("  Error: %s", e)
        sys.exit(1)

    data = resp.json()
    channels = []
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        ch_id = item.get("id")
        if not ch_id:
            continue
        channels.append({
            "id": int(ch_id),
            "title": attrs.get("title", "Unknown"),
            "logo": extract_logo(attrs),  # FIX: square_image.url
        })

    # Add extras
    existing = {c["id"] for c in channels}
    for ch_id, title in EXTRA_CHANNELS.items():
        if ch_id not in existing:
            channels.append({"id": ch_id, "title": title, "logo": ""})

    log.info("  Found %d channels", len(channels))

    # Step 3: Fetch stream URLs
    log.info("\nFetching stream URLs ...")
    stream_urls = {}
    failed = []

    for i, ch in enumerate(channels, 1):
        ch_id = ch["id"]
        x_client, x_signature = generate_x_signature()

        stream_headers = {
            "x-api-key": API_KEY,
            "x-api-platform": "web-mobile",
            "x-secure-level": "2",
            "luws": luws,
            "x-client": x_client,
            "x-signature": x_signature,
            "x-user-login": "true",
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Origin": ORIGIN,
            "Accept": "*/*",
        }

        try:
            resp = session.get(
                f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                headers=stream_headers, timeout=15
            )
            if resp.status_code == 200:
                hls = resp.json().get("data", {}).get("attributes", {}).get("hls", "")
                if hls:
                    stream_urls[ch_id] = hls
                    log.info("  [%d/%d] %s ✓", i, len(channels), ch["title"])
                    continue
        except Exception:
            pass

        failed.append(ch)
        log.warning("  [%d/%d] %s ✗", i, len(channels), ch["title"])
        time.sleep(DELAY)

    # Retry
    for attempt in range(1, retries + 1):
        if not failed:
            break
        log.info("  Retry %d/%d (%d channels) ...", attempt, retries, len(failed))
        time.sleep(RETRY_WAIT)
        still = []
        for ch in failed:
            ch_id = ch["id"]
            x_client, x_signature = generate_x_signature()
            stream_headers = {
                "x-api-key": API_KEY,
                "x-api-platform": "web-mobile",
                "x-secure-level": "2",
                "luws": luws,
                "x-client": x_client,
                "x-signature": x_signature,
                "x-user-login": "true",
                "User-Agent": USER_AGENT,
                "Referer": REFERER,
                "Origin": ORIGIN,
                "Accept": "*/*",
            }
            try:
                resp = session.get(
                    f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true",
                    headers=stream_headers, timeout=15
                )
                if resp.status_code == 200:
                    hls = resp.json().get("data", {}).get("attributes", {}).get("hls", "")
                    if hls:
                        stream_urls[ch_id] = hls
                        log.info("  [R%d] %s ✓", attempt, ch["title"])
                        continue
            except Exception:
                pass
            still.append(ch)
            time.sleep(DELAY)
        failed = still

    log.info("\n  Total: %d | OK: %d | Fail: %d", len(channels), len(stream_urls), len(failed))

    # Step 4: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, output_file, drm_fallback)
    print(f"\n{'=' * 55}")
    print(f"  ✓ {len(stream_urls)} channels → {output_file}")
    print(f"{'=' * 55}")


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator (Merged)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--no-browser", action="store_true",
                        help="Pakai curl_cffi (tanpa Playwright)")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--no-drm-fallback", action="store_true",
                        help="Jangan fallback ke DRM pattern")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    drm_fallback = not args.no_drm_fallback

    if args.no_browser:
        run_curl_cffi(args.output, args.retries, drm_fallback)
    else:
        asyncio.run(run_playwright(args.output, args.retries, drm_fallback))


if __name__ == "__main__":
    main()   
