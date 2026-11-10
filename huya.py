#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v14.1 (in-browser GQL)
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
MAX_WORKERS = 10
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BROWSER_PROFILE = os.path.join(_BASE_DIR, ".twitch_browser")


# ─── In-browser GQL ─────────────────────────────────────────────
def gql_in_browser(page, payload):
    js = """
    async (payload) => {
        const resp = await fetch('https://gql.twitch.tv/gql', {
            method: 'POST',
            headers: {
                'Content-Type': 'text/plain;charset=UTF-8',
                'Client-ID': '%s'
            },
            body: JSON.stringify(payload)
        });
        return await resp.json();
    }
    """ % CLIENT_ID
    return page.evaluate(js, payload)


# ─── Fetch LIVE channels ─────────────────────────────────────────
def get_live_channels(page, count=100):
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

        data = gql_in_browser(page, {"query": query, "variables": variables})
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


# ─── Get stream URL (in-browser) ─────────────────────────────────
def get_stream_url(page, channel):
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

    data = gql_in_browser(page, payload)
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
    session_id = uuid.uuid4().hex
    p_val = random.randint(1000000, 9999999)

    acmb_obj = {
        "AppVersion": "a989fade-ca86-4058-933e-10133becfe0f",
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
        f"&play_session_id={session_id}"
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
    parser = argparse.ArgumentParser(description='Twitch M3U v14.1 (in-browser)')
    parser.add_argument('--output', '-o', default='twitch.m3u')
    parser.add_argument('--channels', '-c', type=int, default=100)
    parser.add_argument('--headed', action='store_true',
                        help='Buka browser terlihat (untuk login pertama kali)')
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v14.1 (in-browser)")
    print(f"  {args.channels} channels")
    print("=" * 55)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        print(f"\n[1/3] Membuka twitch.tv...")
        page.goto("https://www.twitch.tv", timeout=60000)
        # FIX: pakai domcontentloaded, bukan networkidle
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(5)  # Kasih waktu untuk GQL requests initial

        # Cek apakah sudah login
        is_logged_in = page.evaluate("() => document.cookie.includes('auth-token')")
        if not is_logged_in:
            print("  [WARN] Belum login!")
            if not args.headed:
                print("  Jalankan dengan --headed untuk login manual.")
                context.close()
                return
            # Tunggu user login (max 120 detik)
            print("  Menunggu login... (max 2 menit)")
            for _ in range(240):
                time.sleep(0.5)
                if page.evaluate("() => document.cookie.includes('auth-token')"):
                    is_logged_in = True
                    break
            if not is_logged_in:
                print("  [ERROR] Login timeout!")
                context.close()
                return

        print(f"  Login: OK")

        print(f"\n[2/3] Fetching live channels...")
        channels = get_live_channels(page, count=args.channels)
        print(f"  Channel live: {len(channels)}")

        if not channels:
            print("  [ERROR] Tidak ada channel live!")
            context.close()
            return

        print(f"\n[3/3] Mengambil stream URL...")
        lines = ['#EXTM3U']
        live_count = 0
        offline_count = 0

        for i, ch in enumerate(channels):
            time.sleep(random.uniform(0.1, 0.3))
            url = get_stream_url(page, ch["id"])

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
