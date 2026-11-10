#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v14.7 (capture integrity from /integrity response)
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
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
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


# ─── In-browser GQL via page.evaluate ────────────────────────────
def gql_in_browser(page, payload, hdrs):
    """
    Fetch GQL dari dalam page context.
    Menggunakan SEMUA header termasuk client-integrity.
    """
    js = """
    async ({payload, headers}) => {
        const h = {};
        h['Content-Type'] = headers.contentType;
        h['Client-ID'] = headers.clientId;
        h['Authorization'] = headers.authorization;
        h['X-Device-Id'] = headers.xDeviceId;
        h['Client-Session-Id'] = headers.clientSessionId;
        h['Client-Version'] = headers.clientVersion;
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
            "contentType": "text/plain;charset=UTF-8",
            "clientId": CLIENT_ID,
            "authorization": hdrs.get("authorization", ""),
            "xDeviceId": hdrs.get("device_id", ""),
            "clientSessionId": hdrs.get("session_id", ""),
            "clientVersion": hdrs.get("client_version", ""),
            "clientIntegrity": hdrs.get("integrity", ""),
        }
    })


# ─── Fetch LIVE channels ─────────────────────────────────────────
def get_live_channels(page, hdrs, count=100):
    channels = []
    after = None

    while len(channels) < count:
        query = """
        query Streams($first: Int!, $after: Cursor) {
            streams(first: $first, after: $after) {
                edges {
                    node {
                        id
                        title
                        viewersCount
                        broadcaster {
                            id
                            displayName
                            profileImageURL(width: 100)
                        }
                        game {
                            name
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """

        variables = {
            "first": min(30, count - len(channels)),
            "after": after,
        }

        data = gql_in_browser(page, {"query": query, "variables": variables}, hdrs)
        if not data or "errors" in data:
            if data and "errors" in data:
                print(f"  [GQL ERROR] {data['errors'][0].get('message', 'unknown')}")
            break

        streams_data = data.get("data", {}).get("streams", {})
        edges = streams_data.get("edges", [])
        if not edges:
            break

        for edge in edges:
            node = edge.get("node", {})
            broadcaster = node.get("broadcaster", {})
            channels.append({
                "id": broadcaster.get("displayName", "").lower(),
                "name": broadcaster.get("displayName", ""),
                "game": node.get("game", {}).get("name", "Unknown"),
                "viewers": node.get("viewersCount", 0),
                "title": node.get("title", ""),
                "logo": broadcaster.get("profileImageURL", ""),
            })

        page_info = streams_data.get("pageInfo", {})
        if not page_info.get("hasNextPage", False):
            break
        after = page_info.get("endCursor")

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
    parser = argparse.ArgumentParser(description='Twitch M3U v14.7')
    parser.add_argument('--output', '-o', default='twitch.m3u')
    parser.add_argument('--channels', '-c', type=int, default=100)
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v14.7")
    print(f"  {args.channels} channels")
    print("=" * 55)

    cookies = _load_cookies()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # ── Inject cookies ──
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
            "integrity": None,  # ← BARU: dari /integrity response
        }

        # Intercept GQL request (untuk session_id, device_id, dll.)
        def on_request(request):
            if "gql.twitch.tv/gql" in request.url:
                h = request.headers
                if not captured["session_id"]:
                    captured["session_id"] = h.get("client-session-id")
                    captured["device_id"] = h.get("x-device-id")
                    captured["client_version"] = h.get("client-version")
                    captured["authorization"] = h.get("authorization")
                # Jika ada integrity di request, capture juga
                integrity = h.get("client-integrity")
                if integrity and not captured["integrity"]:
                    captured["integrity"] = integrity

        # Intercept /integrity RESPONSE (untuk dapat token langsung)
        def on_response(response):
            if "gql.twitch.tv/integrity" in response.url:
                try:
                    body = response.json()
                    token = body.get("token")
                    if token and not captured["integrity"]:
                        captured["integrity"] = token
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # ── Load page ──
        page.goto("https://www.twitch.tv", timeout=60000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

        # Tunggu sampai session info + integrity tersedia
        print("  Menunggu headers dari halaman...")
        for i in range(120):  # max 60 detik
            if captured["session_id"] and captured["integrity"]:
                break
            time.sleep(0.5)

        # Jika integrity belum ada, tunggu lebih lama
        if not captured["integrity"]:
            print("  Integrity belum ada, menunggu tambahan...")
            for i in range(60):  # max 30 detik lagi
                if captured["integrity"]:
                    break
                time.sleep(0.5)

        time.sleep(2)

        if not captured["session_id"]:
            print("  [ERROR] Gagal capture session info!")
            context.close()
            return

        # Cek login
        is_logged_in = page.evaluate("() => document.cookie.includes('auth-token')")
        if not is_logged_in:
            print("  [WARN] Belum login!")
            context.close()
            return

        print(f"  Login:         OK")
        print(f"  Session-ID:    {captured['session_id']}")
        print(f"  Device-ID:     {captured['device_id']}")
        print(f"  Auth:          {captured['authorization'][:30]}...")
        print(f"  Integrity:     {'SET (' + captured['integrity'][:30] + '...)' if captured['integrity'] else 'NOT FOUND'}")

        if not captured["integrity"]:
            print("  [WARN] Integrity token tidak tersedia — mungkin gagal.")
            print("  GQL kemungkinan tetap reject tanpa header ini.")

        # ── Build headers ──
        hdrs = {
            "session_id": captured["session_id"],
            "device_id": captured["device_id"],
            "client_version": captured["client_version"] or "a989fade-ca86-4058-933e-10133becfe0f",
            "authorization": captured["authorization"] or f"OAuth {cookies[0]['value'] if cookies else ''}",
            "integrity": captured["integrity"] or "",
        }

        # ── Fetch channels ──
        print(f"\n[2/3] Fetching live channels...")
        channels = get_live_channels(page, hdrs, count=args.channels)
        print(f"  Channel live: {len(channels)}")

        if not channels:
            print("  [ERROR] Tidak ada channel live!")
            context.close()
            return

        # ── Get stream URLs ──
        print(f"\n[3/3] Mengambil stream URL...")
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

            if (i + 1) % 20 == 0:
                print(f"  Progress: {i + 1}/{len(channels)}")

        context.close()

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline")
    print(f"  Output: {args.output}")
    print(f"{'=' * 55}")


if __name__ == '__main__':
    main()   
