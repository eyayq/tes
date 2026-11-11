#!/usr/bin/env python3
"""
VIDIO M3U PLAYLIST GENERATOR v5
Semua API call via Playwright (TLS fingerprint konsisten).

Dependencies:
    pip install playwright
    playwright install chromium --with-deps

Run:
    xvfb-run python vidio_m3u.py
"""

import asyncio
import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path

# ─── Konfigurasi ─────────────────────────────────────────────────
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
LIVE_URL = "https://m.vidio.com/live"
CHANNEL_URL = "https://m.vidio.com/live/205-indosiar"
OUTPUT_FILE = "playlist.m3u"
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

# ═══════════════════════════════════════════════════════════════
# COOKIES — UPDATE DI SINI SAJA
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


# ─── JS: fetch stream URL via browser (TLS matches) ─────────────
JS_FETCH_STREAM = """
async (chId) => {
    const t = Date.now() / 1000;
    const xClient = t.toString();

    // Generate x-signature: HMAC-SHA256(key="V1d10D3v:{int_t}", msg=xClient)
    const intT = Math.floor(t);
    const key = `V1d10D3v:${intT}`;

    let signature = '';
    try {
        const encoder = new TextEncoder();
        const cryptoKey = await crypto.subtle.importKey(
            'raw', encoder.encode(key),
            {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']
        );
        const sigBuf = await crypto.subtle.sign('HMAC', cryptoKey, encoder.encode(xClient));
        signature = Array.from(new Uint8Array(sigBuf))
            .map(b => b.toString(16).padStart(2, '0')).join('');
    } catch(e) {
        return {error: 'sig_fail', detail: e.message};
    }

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
                    'x-user-id': '186957857',
                    'x-user-email': 'bhianesee05@gmail.com',
                    'Accept': '*/*',
                },
                credentials: 'include',
            }
        );
        if (resp.status === 200) {
            const data = await resp.json();
            const hls = data?.data?.attributes?.hls || '';
            return {hls: hls, status: 200};
        }
        return {hls: '', status: resp.status, body: await resp.text()};
    } catch(e) {
        return {error: e.message};
    }
}
"""

# ─── JS: fetch channels via browser ─────────────────────────────
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
        if (resp.status === 200) {
            return await resp.json();
        }
        return {error: true, status: resp.status};
    } catch(e) {
        return {error: true, message: e.message};
    }
}
"""


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
        lines.append(f'#EXTVLCOPT:http-referrer=https://m.vidio.com/')
        lines.append(f'#EXTVLCOPT:http-origin=https://m.vidio.com')
        lines.append(stream_url)
        count += 1
    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ─── MAIN ────────────────────────────────────────────────────────
async def async_main(output_file, retries):
    from playwright.async_api import async_playwright

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Semua API via Playwright (TLS match)")
    print("=" * 55)

    cookies = _load_cookies()
    if not cookies:
        log.error("Tidak ada cookies!")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # [1/3] Inject + login
        print("\n[1/3] Auto login ...")
        await context.add_cookies(cookies)
        log.info("  ✓ Inject %d cookies", len(cookies))

        await page.goto(LIVE_URL, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.goto(CHANNEL_URL, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # Verify login
        is_logged_in = await page.evaluate(
            "() => document.cookie.includes('plenty_id')"
        )
        if is_logged_in:
            log.info("  ✓ Login OK")
        else:
            log.warning("  ✗ Tidak login")

        # [2/3] Fetch channels + streams (via browser fetch)
        print("\n[2/3] Fetching channels + streams ...")

        # Fetch channels
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
            square = attrs.get("square_image", {})
            landscape = attrs.get("image_landscape_url", "")
            logo = square.get("url", "") or landscape
            channels.append({"id": ch_id, "title": title, "logo": logo})

        # Add extras
        existing = {c["id"] for c in channels}
        for ch_id, title in EXTRA_CHANNELS.items():
            if ch_id not in existing:
                channels.append({"id": ch_id, "title": title, "logo": ""})

        log.info("  Channels: %d", len(channels))

        # Test 1 channel
        test_result = await page.evaluate(JS_FETCH_STREAM, channels[0]["id"])
        if test_result.get("hls"):
            log.info("  ✓ Stream access OK")
        else:
            log.error("  ✗ Test stream gagal: %s", test_result)
            log.error("  Update VIDIO_COOKIES di script.")
            await context.close()
            return

        # Fetch all streams
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
        log.info("\n  Total: %d | OK: %d | Fail: %d | Time: %.1fs",
                 len(channels), len(stream_urls), len(failed), elapsed)

        if failed:
            for ch in failed:
                log.info("    - [%s] %s", ch["id"], ch["title"])

        if not stream_urls:
            log.error("  SEMUA GAGAL.")
            await context.close()
            return

        await context.close()

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
