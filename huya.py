#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v15 (in-browser, persisted queries)
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
def _extract_channel(node, channels, seen):
    b = node.get("broadcaster", {})
    name = b.get("displayName", "")
    if not name:
        name = node.get("displayName", "") or node.get("login", "")
    if name and name.lower() not in seen:
        seen.add(name.lower())
        game = node.get("game", {})
        channels.append({
            "id": name.lower(),
            "name": name,
            "game": game.get("name", "Unknown") if isinstance(game, dict) else "Unknown",
            "viewers": node.get("viewersCount", 0),
            "title": node.get("title", ""),
            "logo": b.get("profileImageURL", "") or node.get("profileImageURL", ""),
        })


def _deep_extract(obj, channels, seen):
    if isinstance(obj, dict):
        if "broadcaster" in obj and "displayName" in obj.get("broadcaster", {}):
            _extract_channel(obj, channels, seen)
        elif "login" in obj and "displayName" in obj:
            _extract_channel(obj, channels, seen)
        for v in obj.values():
            _deep_extract(v, channels, seen)
    elif isinstance(obj, list):
        for item in obj:
            _deep_extract(item, channels, seen)


# ─── Fetch LIVE channels (persisted queries) ────────────────────
def get_live_channels(page, hdrs, count=100):
    channels = []
    seen = set()

    # 1. FrontPageNew_User (max 18)
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
        print(f"  FrontPageNew_User: {len(channels)} channels")

    # 2. FeaturedContentCarouselStreams (max 8)
    before = len(channels)
    payload2 = [{
        "operationName": "FeaturedContentCarouselStreams",
        "variables": {"language": "en", "first": 8, "acceptedMature": True},
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "4c96356ae0f580a65a4b16fc131e95434e3a2f631b4a325b7e1c4059487f6f15"
            }
        }
    }]
    data2 = gql_in_browser(page, payload2, hdrs)
    if data2:
        if isinstance(data2, list):
            data2 = data2[0]
        if "data" in data2:
            _deep_extract(data2["data"], channels, seen)
    print(f"  +FeaturedContent: {len(channels) - before} baru")

    # 3. Shelves (variable, bisa 20-50+)
    before = len(channels)
    payload3 = [{
        "operationName": "Shelves",
        "variables": {
            "imageWidth": 50,
            "itemsPerRow": 4,
            "platform": "web",
            "limit": 5,
            "requestID": "",
            "includeCostreaming": True,
            "context": {
                "clientApp": "twilight",
                "location": "home",
                "referrerDomain": "www.twitch.tv",
                "viewportHeight": 1760,
                "viewportWidth": 980
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
    print(f"  +Shelves: {len(channels) - before} baru")

    # 4. Coba lagi Shelves dengan limit lebih besar
    if len(channels) < count:
        before = len(channels)
        payload4 = [{
            "operationName": "Shelves",
            "variables": {
                "imageWidth": 50,
                "itemsPerRow": 4,
                "platform": "web",
                "limit": 10,
                "requestID": "",
                "includeCostreaming": True,
                "context": {
                    "clientApp": "twilight",
                    "location": "home",
                    "referrerDomain": "www.twitch.tv",
                    "viewportHeight": 1760,
                    "viewportWidth": 980
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
        data4 = gql_in_browser(page, payload4, hdrs)
        if data4:
            if isinstance(data4, list):
                data4 = data4[0]
            if "data" in data4:
                _deep_extract(data4["data"], channels, seen)
        print(f"  +Shelves(10): {len(channels) - before} baru")

    return channels[:count]


# ─── Get stream URL ──────────────────────────────────────────────
def get_stream_url(page, hdrs, channel):
    payload = [{
        "operationName": "PrefetchPlaybackAccessToken",
        "variables": {
            "login": channel,
            "playerType": "site",
            "platform": "web",
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": GQL_HASH,
            }
        }
    }]

    data = gql_in_browser(page, payload, hdrs)
    if not data:
        return None

    if isinstance(data, list):
        data = data[0]

    if "errors" in data:
        return None

    token = ""
    sig = ""

    spa = data.get("data", {}).get("streamPlaybackAccessToken", {})
    if spa:
        token = spa.get("value", "")
        sig = spa.get("signature", "")

    if not token:
        stream_info = data.get("data", {}).get("streamerInfo", {})
        stream = stream_info.get("stream", {})
        token_data = stream.get("playbackAccessToken", {})
        token = token_data.get("value", "")
        sig = token_data.get("signature", "")

    if not token or not sig:
        return None

    ch_lower = channel.lower()
    play_session_id = uuid.uuid4().hex
    p_val = random.randint(1000000, 9999999)

    acmb_obj = {
        "AppVersion": hdrs.get("client_version", "a989fade-ca86-4058-933e-10133becfe0f"),
        "ClientApp": "twilight",
        "URL": "https://www.twitch.tv/"
    }
    acmb = base64.b64encode(json.dumps(acmb_obj, separators=(',', ':')).encode()).decode()

    url = (
        f"https://usher.ttvnw.net/api/v2/channel/hls/{ch_lower}.m3u8"
        f"?acmb={acmb}"
        f"&allow_source=true"
        f"&browser_family=chrome"
        f"&browser_version=157.0"
        f"&cdm=wv"
        f"&enable_score=true"
        f"&fast_bread=true"
        f"&include_unavailable=true"
        f"&lang=en"
        f"&os_name=Linux"
        f"&os_version=undefined"
        f"&p={p_val}"
        f"&platform=web"
        f"&play_session_id={play_session_id}"
        f"&player_backend=mediaplayer"
        f"&player_version=1.59.0-rc.1"
        f"&playlist_include_framerate=true"
        f"&reassignments_supported=true"
        f"&sig={quote(sig, safe='')}"
        f"&supported_codecs=av1,h265,h264"
        f"&token={quote(token, safe='')}"
        f"&transcode_mode=cbr_v1"
    )
    return url


# ─── Main ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Twitch M3U v15')
    parser.add_argument('--output', '-o', default='twitch.m3u')
    parser.add_argument('--channels', '-c', type=int, default=100)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v15")
    print(f"  Max {args.channels} channels")
    print("=" * 55)

    cookies = _load_cookies()

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
        else:
            print(f"\n[1/3] No cookies found")

        # ── Interceptors ──
        captured = {
            "session_id": None,
            "device_id": None,
            "client_version": None,
            "authorization": None,
            "integrity": None,
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

        # ── Load page ──
        page.goto("https://www.twitch.tv", timeout=60000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

        print("  Menunggu headers dari halaman...")
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

        print(f"  Login:         OK")
        print(f"  Session-ID:    {captured['session_id']}")
        print(f"  Device-ID:     {captured['device_id']}")
        print(f"  Integrity:     {'SET' if captured['integrity'] else 'NOT FOUND'}")

        hdrs = {
            "session_id": captured["session_id"],
            "device_id": captured["device_id"],
            "client_version": captured["client_version"] or "a989fade-ca86-4058-933e-10133becfe0f",
            "authorization": captured["authorization"] or "",
            "integrity": captured["integrity"] or "",
        }

        # ── Fetch channels (persisted queries) ──
        print(f"\n[2/3] Fetching live channels...")
        channels = get_live_channels(page, hdrs, count=args.channels)
        print(f"  Total channel: {len(channels)}")

        if not channels:
            print("  [ERROR] Tidak ada channel live!")
            context.close()
            return

        # ── Get stream URLs ──
        print(f"\n[3/3] Mengambil stream URL ({len(channels)} channels)...")
        lines = ['#EXTM3U']
        live_count = 0
        offline_count = 0

        for i, ch in enumerate(channels):
            time.sleep(random.uniform(0.1, 0.3))
            url = get_stream_url(page, hdrs, ch["id"])

            if not url:
                offline_count += 1
                continue

            name = ch.get("name", ch.get("id", ""))
            game = ch.get("game", "Twitch")
            logo = ch.get("logo", "")
            title = ch.get("title", "")
            display_name = f"{name} — {title}" if title else name

            extinf = f'#EXTINF:-1'
            if logo:
                extinf += f' tvg-logo="{logo}"'
            extinf += f' group-title="{game}"'
            extinf += f',{display_name}'

            lines.append(extinf)
            lines.append(url)
            live_count += 1

            if (i + 1) % 10 == 0:
                print(f"  Progress: {i + 1}/{len(channels)}")

        context.close()

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline")
    print(f"  Output: {args.output}")
    print(f"  Stream token expired: ~6 jam")
    print(f"{'=' * 55}")


if __name__ == '__main__':
    main()   
