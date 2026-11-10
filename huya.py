#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v16 (in-browser + kategori + proxy)
"""

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

# ═══════════════════════════════════════════════════════════════
# PROXY
# ═══════════════════════════════════════════════════════════════
PROXY = "https://pl-gratosv---al.bhns.workers.dev"
# ═══════════════════════════════════════════════════════════════

# ─── Kategori ───────────────────────────────────────────────────
CATEGORIES = [
    ("Sports", "sports-1"),
    ("Mobile Legends", "mobile-legends-bang-bang"),
    ("Just Chatting", "just-chatting"),
    ("Valorant", "valorant"),
    ("League of Legends", "league-of-legends"),
    ("Minecraft", "minecraft"),
    ("Counter-Strike", "counter-strike"),
    ("Dota 2", "dota-2"),
    ("Apex Legends", "apex-legends"),
    ("Fortnite", "fortnite"),
    ("Overwatch 2", "overwatch-2"),
    ("World of Warcraft", "world-of-warcraft"),
    ("PUBG", "pubg"),
    ("Genshin Impact", "genshin-impact"),
    ("Elden Ring", "elden-ring"),
    ("Call of Duty", "call-of-duty"),
    ("IRL", "irl"),
    ("Music", "music"),
    ("ASMR", "asmr"),
    ("Speedruns", "speedruns"),
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


# ─── In-browser GQL ─────────────────────────────────────────────
def gql_in_browser(page, payload, hdrs):
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
            method: 'POST',
            headers: h,
            body: JSON.stringify(payload)
        });
        return await resp.json();
    }
    """
    return page.evaluate(js, {
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


# ─── Parse helpers ──────────────────────────────────────────────
def _extract_channel(node, channels, seen, category_name=None):
    b = node.get("broadcaster", {})
    name = b.get("displayName", "")
    if not name:
        name = node.get("displayName", "") or node.get("login", "")
    if name and name.lower() not in seen:
        seen.add(name.lower())
        game = node.get("game", {})
        # FIX: pakai game actual dari node, bukan category_name
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
            "category": game_name,  # ← FIX: group-title dari game actual
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


# ─── Scrape via page navigation ─────────────────────────────────
def scrape_category_page(page, url, category_name, channels, seen, max_wait=8):
    responses_data = []

    def on_response(response):
        if "gql.twitch.tv/gql" in response.url:
            try:
                body = response.json()
                responses_data.append(body)
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(url, timeout=30000)
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(max_wait)
    page.remove_listener("response", on_response)

    for data in responses_data:
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "data" in item:
                    _deep_extract(item["data"], channels, seen, category_name)
        elif isinstance(data, dict) and "data" in data:
            _deep_extract(data["data"], channels, seen, category_name)


# ─── Homepage queries ───────────────────────────────────────────
def get_homepage_channels(page, hdrs, channels, seen):
    payload1 = [{
        "operationName": "FrontPageNew_User",
        "variables": {"limit": 18},
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "64bd07a2cbaca80699d62636d966cf6395a5d14a1f0a14282067dcb28b13eb11"
            }
        }
    }]
    data1 = gql_in_browser(page, payload1, hdrs)
    if data1:
        if isinstance(data1, list):
            data1 = data1[0]
        if "data" in data1:
            _deep_extract(data1["data"], channels, seen)

    payload3 = [{
        "operationName": "Shelves",
        "variables": {
            "imageWidth": 50, "itemsPerRow": 4, "platform": "web",
            "limit": 10, "requestID": "", "includeCostreaming": True,
            "context": {
                "clientApp": "twilight", "location": "home",
                "referrerDomain": "www.twitch.tv",
                "viewportHeight": 1760, "viewportWidth": 980
            },
            "verbose": False
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "e563ef7d38b54b509d6744a01f65a9ad9e761209d561908d3d46628fa32a06b5"
            }
        }
    }]
    data3 = gql_in_browser(page, payload3, hdrs)
    if data3:
        if isinstance(data3, list):
            data3 = data3[0]
        if "data" in data3:
            _deep_extract(data3["data"], channels, seen)


# ─── Main ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Twitch M3U v16 (kategori)')
    parser.add_argument('--output', '-o', default='twitch.m3u')
    parser.add_argument('--channels', '-c', type=int, default=100)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--categories', '-k', default="",
                        help='Kategori spesifik (comma-separated slug)')
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    if args.categories:
        slugs = [s.strip() for s in args.categories.split(",")]
        categories = [(s, s) for s in slugs]
    else:
        categories = CATEGORIES

    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v16 (kategori)")
    print(f"  Max {args.channels} channels | {len(categories)} kategori")
    print(f"  Proxy: {PROXY}")
    print("=" * 55)

    cookies = _load_cookies()
    channels = []
    seen = set()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=not args.headed,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        if cookies:
            context.add_cookies(cookies)
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

        def on_response(response):
            if "gql.twitch.tv/integrity" in response.url:
                try:
                    body = response.json()
                    if body.get("token") and not captured["integrity"]:
                        captured["integrity"] = body["token"]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto("https://www.twitch.tv", timeout=60000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

        print("  Menunggu headers...")
        for _ in range(120):
            if captured["session_id"] and captured["integrity"]:
                break
            time.sleep(0.5)
        time.sleep(2)

        if not captured["session_id"]:
            print("  [ERROR] Gagal capture session info!")
            context.close()
            return

        is_logged_in = page.evaluate("() => document.cookie.includes('auth-token')")
        if not is_logged_in:
            print("  [WARN] Belum login!")
            context.close()
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
        get_homepage_channels(page, hdrs, channels, seen)
        print(f"{len(channels)} channels")

        for i, (cat_name, cat_slug) in enumerate(categories):
            if len(channels) >= args.channels:
                break

            url = f"https://www.twitch.tv/directory/category/{cat_slug}"
            before = len(channels)
            print(f"  [{cat_name}] ", end="", flush=True)

            try:
                scrape_category_page(page, url, cat_name, channels, seen, max_wait=6)
            except Exception as e:
                print(f"ERROR: {e}")
                continue

            print(f"+{len(channels) - before} (total: {len(channels)})")
            time.sleep(1)

        context.close()

    # ── Build playlist ──
    channels = channels[:args.channels]
    print(f"\n[3/3] Membangun playlist ({len(channels)} channels)...")

    lines = ['#EXTM3U']
    live_count = 0

    for ch in channels:
        name = ch.get("name", ch.get("id", ""))
        game = ch.get("game", "Twitch")
        logo = ch.get("logo", "")
        title = ch.get("title", "")
        category = ch.get("category", game)
        display_name = f"{name} — {title}" if title else name

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{category}"'
        extinf += f',{display_name}'

        lines.append(extinf)
        lines.append(f"{PROXY}/play.m3u8?id={ch['id']}")
        live_count += 1

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} channels")
    print(f"  Output: {args.output}")
    print(f"  Proxy:  {PROXY}")
    print(f"{'=' * 55}")


if __name__ == '__main__':
    main()   
