#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v2
- Fetch channel LIVE dari Twitch API (bukan list hardcoded)
- GQL PrefetchPlaybackAccessToken
- Output: twitch.m3u (HLS .m3u8)
- Token expired: ~6 jam
"""

import json
import time
import threading
import argparse
import uuid
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ─── Konfigurasi ─────────────────────────────────────────────────
TIMEOUT = 15
MAX_WORKERS = 20
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

_session = requests.Session()
_session.headers.update({
    "Client-ID": CLIENT_ID,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})


# ─── Fetch TOP LIVE channels dari Twitch ─────────────────────────
def get_live_channels(count=100, category=None):
    """Fetch channels yang SEDANG LIVE dari Twitch API."""
    channels = []
    offset = 0
    page_size = 100

    while len(channels) < count:
        payload = {
            "query": """
            query TopStreams($first: Int!, $offset: Int, $category: String) {
                topStreams(first: $first, offset: $offset, category: $category) {
                    edges {
                        node {
                            id
                            displayName
                            game {
                                name
                            }
                            viewerCount
                            title
                            stream {
                                id
                            }
                            profileImageURL(width: 100)
                        }
                    }
                    pageInfo {
                        hasNextPage
                    }
                }
            }
            """,
            "variables": {
                "first": page_size,
                "offset": offset,
                "category": category,
            }
        }

        try:
            r = _session.post("https://gql.twitch.tv/gql", json=payload, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            data = r.json()
            edges = data.get("data", {}).get("topStreams", {}).get("edges", [])
            if not edges:
                break
            for edge in edges:
                node = edge.get("node", {})
                if node.get("stream"):
                    channels.append({
                        "id": node.get("displayName", "").lower(),
                        "name": node.get("displayName", ""),
                        "game": node.get("game", {}).get("name", "Unknown"),
                        "viewers": node.get("viewerCount", 0),
                        "title": node.get("title", ""),
                        "logo": node.get("profileImageURL", ""),
                    })
            has_next = data.get("data", {}).get("topStreams", {}).get("pageInfo", {}).get("hasNextPage", False)
            if not has_next:
                break
            offset += page_size
        except Exception:
            break

    return channels[:count]


# ─── Get stream URL via GQL ──────────────────────────────────────
def get_stream_url(channel, retries=2):
    for attempt in range(retries + 1):
        try:
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

            r = _session.post("https://gql.twitch.tv/gql", json=payload, timeout=TIMEOUT)

            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None

            data = r.json()
            if isinstance(data, list):
                data = data[0]

            stream_info = data.get("data", {}).get("streamerInfo", {})
            stream = stream_info.get("stream")

            if not stream:
                return None

            token_data = stream.get("playbackAccessToken", {})
            token = token_data.get("value", "")
            sig = token_data.get("signature", "")

            if not token or not sig:
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None

            session_id = str(uuid.uuid4())
            url = (
                f"https://usher.ttvnw.net/api/channel/hls/{channel}.m3u8"
                f"?sig={sig}"
                f"&token={quote(token, safe='')}"
                f"&allow_source=true"
                f"&allow_audio_only=true"
                f"&player_backend=mediaplayer"
                f"&playlist_include_framerate=true"
                f"&player_version=1.41.0-rc.1"
                f"&platform=web"
                f"&play_session_id={session_id}"
            )
            return url

        except Exception:
            if attempt < retries:
                time.sleep(1)

    return None


# ─── Worker ──────────────────────────────────────────────────────
def fetch_worker(args):
    idx, ch = args
    try:
        url = get_stream_url(ch["id"])
        return (idx, ch, url, None)
    except Exception as e:
        return (idx, ch, None, str(e))


# ─── Generate playlist ───────────────────────────────────────────
def generate_playlist(output_file, max_channels, max_workers=MAX_WORKERS, category=None):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v2")
    print(f"  {max_channels} channels | {max_workers} threads")
    if category:
        print(f"  Kategori: {category}")
    print("=" * 55)

    # Step 1: Fetch live channels
    print(f"\n[1/3] Fetching live channels...")
    channels = get_live_channels(count=max_channels, category=category)
    print(f"  Channel live ditemukan: {len(channels)}")

    if not channels:
        print("  [ERROR] Tidak ada channel live!")
        return

    # Step 2: Get stream URLs (PARALEL)
    print(f"\n[2/3] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(channels)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_worker, (i, ch)): i
            for i, ch in enumerate(channels)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, ch, url, error = future.result()
            results[idx] = (ch, url, error)
            done_count += 1
            if done_count % 20 == 0:
                print(f"  Progress: {done_count}/{len(channels)}")

    # Step 3: Build playlist
    print(f"\n[3/3] Membangun playlist...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for item in results:
        if item is None:
            continue
        ch, url, error = item

        if error:
            error_count += 1
            continue
        if not url:
            offline_count += 1
            continue

        name = ch.get("name", ch.get("id", ""))
        game = ch.get("game", "Twitch")
        logo = ch.get("logo", "")
        title = ch.get("title", "")
        viewers = ch.get("viewers", 0)

        display_name = f"{name} — {title}" if title else name

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{game}"'
        extinf += f',{display_name}'

        lines.append(extinf)
        lines.append(url)
        live_count += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline | {error_count} error")
    print(f"  Output: {output_file}")
    print(f"  Token expired: ~6 jam")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Twitch M3U Playlist Generator v2')
    parser.add_argument('--output', '-o', default='twitch.m3u',
                        help='File output M3U (default: twitch.m3u)')
    parser.add_argument('--channels', '-c', type=int, default=100,
                        help='Jumlah channel (default: 100)')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 20)')
    parser.add_argument('--category', '-g', type=str, default=None,
                        help='Filter kategori (misal: "Just Chatting", "Sports")')
    args = parser.parse_args()

    generate_playlist(
        output_file=args.output,
        max_channels=args.channels,
        max_workers=args.workers,
        category=args.category,
    )   
