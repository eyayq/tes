#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v16.4 (fix async response.json)
"""

import asyncio
import time
import argparse
import uuid
import random
import base64
import json
import os
from urllib.parse import quote

# ─── Konfigurasi ─────────────────────────────────────────────────
GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

PROXY = "https://pl-gratosv---al.bhns.workers.dev"

CATEGORIES = [
    ("sports-1", "sports-1"),
    ("mobile-legends-bang-bang", "mobile-legends-bang-bang"),
]

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BROWSER_PROFILE = os.path.join(_BASE_DIR, ".twitch_browser")
_COOKIES_FILE = os.path.join(_BASE_DIR, ".twitch_cookies.json")


def _load_cookies():
    env_cookies = os.environ.get("TWITCH_COOKIES", "")
    if env_cookies:
        try:
            return json.loads(env_cookies)
        except json.JSONDecodeError:
            pass
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            return json.load(f)
    return None


# ─── Parse helpers ──────────────────────────────────────────────
def _extract_channel(node, channels, seen, category_name=None):
    b = node.get("broadcaster", {})
    name = b.get("displayName", "")
    if not name:
        name = node.get("displayName", "") or node.get("login", "")
    if name and name.lower() not in seen:
        seen.add(name.lower())
        game = node.get("game", {})
        game_name = game.get("name", "") if isinstance(game, dict) else ""
        if not game_name:
            game_name = category_name or "Unknown"
        channels.append({
            "id": name.lower(),
            "name": name,
            "game": game_name,
            "viewers": node.get("viewersCount", 0),
            "title": node.get("title", ""),
            "logo": b.get("profileImageURL", "") or node.get("profileImageURL", ""),
            "category": game_name,
        })


def _deep_extract(obj, channels, seen, category_name=None):
    if isinstance(obj, dict):
        if "broadcaster" in obj and "displayName" in obj.get("broadcaster", {}):
            _extract_channel(obj, channels, seen, category_name)
        elif "login" in obj and "displayName" in obj:
            _extract_channel(obj, channels, seen, category_name)
        for v in obj.values():
            _deep_extract(v, channels, seen, category_name)
    elif isinstance(obj, list):
        for item in obj:
            _deep_extract(item, channels, seen, category_name)


# ─── Scrape kategori (FIX: await response.json() di async context) ──
async def scrape_category(page, cat_name, cat_slug, channels, seen, max_wait=10):
    response_objects = []

    def on_response(response):
        if "gql.twitch.tv/gql" in response.url:
            response_objects.append(response)

    page.on("response", on_response)

    url = f"https://www.twitch.tv/directory/category/{cat_slug}"
    before = len(channels)

    await page.goto(url, timeout=30000, wait_until="commit")
    await page.wait_for_load_state("domcontentloaded", timeout=20000)

    if "/directory/category/" not in page.url:
        print(f"    [WARN] Redirect: {page.url}")
        page.remove_listener("response", on_response)
        return

    await asyncio.sleep(max_wait)

    for _ in range(5):
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(1.5)

    page.remove_listener("response", on_response)

    # ── AWAIT response.json() di sini ──
    for resp in response_objects:
        try:
            body = await resp.json()
            if isinstance(body, list):
                for item in body:
                    if isinstance(item, dict) and "data" in item:
                        _deep_extract(item["data"], channels, seen, cat_name)
            elif isinstance(body, dict) and "data" in body:
                _deep_extract(body["data"], channels, seen, cat_name)
        except Exception:
            pass

    added = len(channels) - before
    print(f"    GQL responses: {len(response_objects)}, channels baru: +{added}")


# ─── Homepage queries ───────────────────────────────────────────
async def get_homepage_channels(page, hdrs, channels, seen):
    js = """
    async ({payload, headers}) => {
        const h = {
            'Content-Type': 'text/plain;charset=UTF-8',
            'Client-ID': headers.clientId,
            'Authorization': headers.authorization,
            'X-Device-Id': headers.xDeviceId,
            'Client-Session-Id': headers.clientSessionId,
            'Client-Version': headers.clientVersion
        };
        if (headers.clientIntegrity) {
            h['Client-Integrity'] = headers.clientIntegrity;
        }
        const resp = await fetch('https://gql.twitch.tv/gql', {
            method: 'POST', headers: h, body: JSON.stringify(payload)
        });
        return await resp.json();
    }
    """

    async def gql(payload):
        return await page.evaluate(js, {
            "payload": payload,
            "headers": {
                "clientId": GQL_CLIENT_ID,
                "authorization": hdrs.get("authorization", ""),
                "xDeviceId": hdrs.get("device_id", ""),
                "clientSessionId": hdrs.get("session_id", ""),
                "clientVersion": hdrs.get("client_version", ""),
                "clientIntegrity": hdrs.get("integrity", ""),
            }
        })

    d1 = await gql([{
        "operationName": "FrontPageNew_User",
        "variables": {"limit": 18},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "64bd07a2cbaca80699d62636d966cf6395a5d14a14282067dcb28b13eb11"}}
    }])
    if d1:
        if isinstance(d1, list): d1 = d1[0]
        if "data" in d1: _deep_extract(d1["data"], channels, seen)

    d3 = await gql([{
        "operationName": "Shelves",
        "variables": {
            "imageWidth": 50, "itemsPerRow": 4, "platform": "web",
            "limit": 10, "requestID": "", "includeCostreaming": True,
            "context": {"clientApp": "twilight", "location": "home",
                        "referrerDomain": "www.twitch.tv", "viewportHeight": 1760, "viewportWidth": 980},
            "verbose": False
        },
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "e563ef7d38b54b509d6744a01f65a9ad9e761209d561908d3d46628fa32a06b5"}}
    }])
    if d3:
        if isinstance(d3, list): d3 = d3[0]
        if "data" in d3: _deep_extract(d3["data"], channels, seen)


# ─── Main (async) ───────────────────────────────────────────────
async def async_main(max_channels, headed):
    from playwright.async_api import async_playwright

    cookies = _load_cookies()
    channels = []
    seen = set()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=not headed,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        if cookies:
            await context.add_cookies(cookies)
            print(f"\n[1/3] Cookies di-inject ({len(cookies)} cookies)")

        captured = {
            "session_id": None, "device_id": None,
            "client_version": None, "authorization": None, "integrity": None,
        }

        def on_request(request):
            if "gql.twitch.tv/gql" in request.url:
                h = request.headers
                if not captured["session_id"]:
                    captured["session_id"] = h.get("client-session-id")
                    captured["device_id"] = h.get("x-device-id")
                    captured["client_version"] = h.get("client-version")
                    captured["authorization"] = h.get("authorization")
                integrity = h.get("client-integrity")
                if integrity and not captured["integrity"]:
                    captured["integrity"] = integrity

        def on_response_integrity(response):
            if "gql.twitch.tv/integrity" in response.url:
                # Simpan untuk di-await nanti
                asyncio.ensure_future(_capture_integrity(response, captured))

        async def _capture_integrity(response, captured):
            try:
                body = await response.json()
                if body.get("token") and not captured["integrity"]:
                    captured["integrity"] = body["token"]
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response_integrity)

        await page.goto("https://www.twitch.tv", timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=30000)

        print("  Menunggu headers...")
        for _ in range(120):
            if captured["session_id"] and captured["integrity"]:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(2)

        if not captured["session_id"]:
            print("  [ERROR] Gagal capture session info!")
            await context.close()
            return

        is_logged_in = await page.evaluate("() => document.cookie.includes('auth-token')")
        if not is_logged_in:
            print("  [WARN] Belum login!")
            await context.close()
            return

        print(f"  Login: OK")

        hdrs = {
            "session_id": captured["session_id"],
            "device_id": captured["device_id"],
            "client_version": captured["client_version"] or "a989fade-ca86-4058-933e-10133becfe0f",
            "authorization": captured["authorization"] or "",
            "integrity": captured["integrity"] or "",
        }

        # ── Scrape ──
        print(f"\n[2/3] Scrape channels...")

        print(f"  [Homepage] ...", end=" ", flush=True)
        await get_homepage_channels(page, hdrs, channels, seen)
        print(f"{len(channels)} channels")

        for cat_name, cat_slug in CATEGORIES:
            print(f"  [{cat_name}] ...", end=" ", flush=True)
            await scrape_category(page, cat_name, cat_slug, channels, seen, max_wait=10)
            print(f"(total: {len(channels)})")
            await asyncio.sleep(2)

        await context.close()

    # ── Build playlist ──
    channels = channels[:max_channels]
    print(f"\n[3/3] Membangun playlist ({len(channels)} channels)...")

    lines = ['#EXTM3U']
    for ch in channels:
        name = ch.get("name", ch.get("id", ""))
        logo = ch.get("logo", "")
        title = ch.get("title", "")
        category = ch.get("category", "Twitch")
        display_name = f"{name} — {title}" if title else name

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{category}"'
        extinf += f',{display_name}'

        lines.append(extinf)
        lines.append(f"{PROXY}/play.m3u8?id={ch['id']}")

    output = "playlist.m3u"
    with open(output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {len(channels)} channels")
    print(f"  Output: {output}")
    print(f"  Proxy:  {PROXY}")
    print(f"{'=' * 55}")


# ─── Entry point ─────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Twitch M3U v16.4')
    parser.add_argument('--channels', '-c', type=int, default=100)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()

    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v16.4")
    print(f"  Kategori: Sports + Mobile Legends")
    print(f"  Proxy: {PROXY}")
    print("=" * 55)

    asyncio.run(async_main(args.channels, args.headed))   
