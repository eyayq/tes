#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR
- GQL API (gql.twitch.tv) — tanpa OAuth, tanpa auth
- StreamDirectory untuk listing channels live
- PlaybackAccessToken untuk stream URL (m3u8)
- curl_cffi (Chrome TLS fingerprint)
- ThreadPoolExecutor (parallel)
- tvg-logo + group-title otomatis
- Output: playlist.m3u
"""

import json
import time
import threading
import argparse
import uuid
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_CHANNELS = 500
REQUEST_DELAY = 0.2
TIMEOUT = 15
MAX_WORKERS = 28

# Twitch public web client ID (baked into twitch.tv)
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_URL = "https://gql.twitch.tv/gql"
USHER_URL = "https://usher.ttvnw.net/api/channel/hls/{channel}.m3u8"

# Persisted query hash untuk PlaybackAccessToken
PLAYBACK_TOKEN_HASH = "0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712"

_local = threading.local()


def get_session():
    if not hasattr(_local, 'session'):
        _local.session = cffi_requests.Session(impersonate="chrome")
    return _local.session


# ─── GQL Helper ──────────────────────────────────────────────────
def gql_request(operations):
    """POST to GQL API."""
    s = get_session()
    headers = {
        "Client-ID": CLIENT_ID,
        "Content-Type": "application/json",
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
    }
    try:
        r = s.post(GQL_URL, json=operations, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ─── Step 1: Ambil list channels live (StreamDirectory) ─────────
def get_live_channels(max_channels, category=None):
    """
    Ambil list channels live dari Twitch GQL API.
    Jika category=None → semua kategori (top channels)
    Jika category="Just Chatting" → hanya kategori itu
    """
    all_channels = []
    cursor = ""
    page_size = min(100, max_channels)

    while len(all_channels) < max_channels:
        # Build query
        if category:
            query = (
                'query StreamDirectory($category: String, $limit: Int, $after: String) {\n'
                '  streamDirectory(category: $category, first: $limit, after: $after) {\n'
                '    edges {\n'
                '      node {\n'
                '        __typename\n'
                '        ... on Stream {\n'
                '          id\n'
                '          game {\n'
                '            displayName\n'
                '          }\n'
                '          viewerCount\n'
                '          broadcaster {\n'
                '            login\n'
                '            displayName\n'
                '            profileImageURL(width: 180)\n'
                '          }\n'
                '          title\n'
                '        }\n'
                '      }\n'
                '    }\n'
                '    pageInfo {\n'
                '      hasNextPage\n'
                '      endCursor\n'
                '    }\n'
                '  }\n'
                '}'
            )
            variables = {
                "category": category,
                "limit": page_size,
                "after": cursor,
            }
        else:
            query = (
                'query StreamDirectory($limit: Int, $after: String) {\n'
                '  streamDirectory(first: $limit, after: $after) {\n'
                '    edges {\n'
                '      node {\n'
                '        __typename\n'
                '        ... on Stream {\n'
                '          id\n'
                '          game {\n'
                '            displayName\n'
                '          }\n'
                '          viewerCount\n'
                '          broadcaster {\n'
                '            login\n'
                '            displayName\n'
                '            profileImageURL(width: 180)\n'
                '          }\n'
                '          title\n'
                '        }\n'
                '      }\n'
                '    }\n'
                '    pageInfo {\n'
                '      hasNextPage\n'
                '      endCursor\n'
                '    }\n'
                '  }\n'
                '}'
            )
            variables = {
                "limit": page_size,
                "after": cursor,
            }

        operations = [{
            "operationName": "StreamDirectory",
            "query": query,
            "variables": variables,
        }]

        data = gql_request(operations)
        if not data:
            break

        result = data[0].get("data", {}).get("streamDirectory", {})
        edges = result.get("edges", [])
        if not edges:
            break

        for edge in edges:
            node = edge.get("node", {})
            if node.get("__typename") != "Stream":
                continue

            broadcaster = node.get("broadcaster", {})
            channel_login = broadcaster.get("login", "")
            if not channel_login:
                continue

            all_channels.append({
                'id': node.get("id", ""),
                'login': channel_login,
                'name': broadcaster.get("displayName", channel_login),
                'game': (node.get("game") or {}).get("displayName", "Twitch"),
                'title': node.get("title", ""),
                'viewers': node.get("viewerCount", 0),
                'logo': broadcaster.get("profileImageURL", ""),
            })

        page_info = result.get("pageInfo", {})
        if not page_info.get("hasNextPage", False):
            break
        cursor = page_info.get("endCursor", "")

        print(f"  Page: +{len(edges)} channels (total: {len(all_channels)})")
        time.sleep(REQUEST_DELAY)

    return all_channels[:max_channels]


# ─── Step 2: Get PlaybackAccessToken per channel ────────────────
def get_playback_token(channel_login):
    """
    Get PlaybackAccessToken via GQL persisted query.
    Return: (token, signature) atau (None, None)
    """
    operations = [{
        "operationName": "PlaybackAccessToken_Template",
        "query": (
            'query PlaybackAccessToken_Template($login: String!, $isLive: Boolean!, '
            '$vodID: ID!, $isVod: Boolean!, $playerType: String!) {\n'
            '  streamPlaybackAccessToken(channelName: $login, params: {platform: "web", '
            'playerBackend: "mediaplayer", playerType: $playerType}) @include(if: $isLive) {\n'
            '    value\n'
            '    signature\n'
            '    __typename\n'
            '  }\n'
            '  videoPlaybackAccessToken(id: $vodID, params: {platform: "web", '
            'playerBackend: "mediaplayer", playerType: $playerType}) @include(if: $isVod) {\n'
            '    value\n'
            '    signature\n'
            '    __typename\n'
            '  }\n'
            '}'
        ),
        "variables": {
            "isLive": True,
            "login": channel_login,
            "isVod": False,
            "vodID": "",
            "playerType": "site",
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": PLAYBACK_TOKEN_HASH,
            }
        },
    }]

    data = gql_request(operations)
    if not data:
        return None, None

    token_data = data[0].get("data", {}).get("streamPlaybackAccessToken")
    if not token_data:
        return None, None

    value = token_data.get("value", "")
    signature = token_data.get("signature", "")

    if not value or not signature:
        return None, None

    return value, signature


# ─── Step 3: Build stream URL ───────────────────────────────────
def get_stream_url(channel_login):
    """
    Get m3u8 URL untuk channel.
    1. Get PlaybackAccessToken
    2. Build usher URL
    """
    token, sig = get_playback_token(channel_login)
    if not token or not sig:
        return None

    # URL-encode token (JSON string)
    token_encoded = quote(token, safe='')
    sig_encoded = quote(sig, safe='')

    # Build usher URL
    url = (
        f"{USHER_URL.format(channel=channel_login)}"
        f"?allow_source=true"
        f"&allow_audio_only=true"
        f"&fast_bread=true"
        f"&player_backend=mediaplayer"
        f"&playlist_include_framerate=true"
        f"&reassignments_supported=true"
        f"&sig={sig_encoded}"
        f"&supported_codecs=av1,h265,h264"
        f"&token={token_encoded}"
        f"&type=any"
    )

    return url


# ─── Step 4: Worker ──────────────────────────────────────────────
def fetch_worker(args):
    idx, channel = args
    login = channel['login']
    try:
        stream_url = get_stream_url(login)
    except Exception as e:
        return (idx, channel, None, str(e))
    return (idx, channel, stream_url, None)


# ─── Step 5: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_channels, max_workers=MAX_WORKERS,
                      category=None):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR")
    print(f"  {max_channels} channels | {max_workers} threads")
    if category:
        print(f"  Category: {category}")
    print("=" * 55)

    # Step 1: Ambil list channels live
    print(f"\n[1/3] Mengambil channels live...")
    all_channels = get_live_channels(max_channels, category)
    print(f"\n  Total channels: {len(all_channels)}")

    if not all_channels:
        print("\n  [ERROR] Tidak ada channels live!")
        return

    # Step 2: Stream URL (PARALEL)
    print(f"\n[2/3] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(all_channels)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_worker, (i, ch)): i
            for i, ch in enumerate(all_channels)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, channel, stream_url, error = future.result()
            results[idx] = (channel, stream_url, error)
            done_count += 1
            if done_count % 50 == 0:
                print(f"  Progress: {done_count}/{len(all_channels)}")

    # Step 3: Build playlist
    print(f"\n[3/3] Membangun playlist...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for item in results:
        if item is None:
            continue
        channel, stream_url, error = item

        if error:
            error_count += 1
            continue
        if not stream_url:
            offline_count += 1
            continue

        name = channel['name']
        game = channel.get('game', 'Twitch')
        logo = channel.get('logo', '')

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{game}"'
        extinf += f',{name}'

        lines.append(extinf)
        lines.append(stream_url)
        live_count += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline | {error_count} error")
    print(f"  Output: {output_file}")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Twitch M3U Playlist Generator')
    parser.add_argument('--output', '-o', default='playlist_twitch.m3u',
                        help='File output M3U (default: playlist_twitch.m3u)')
    parser.add_argument('--channels', '-c', type=int, default=MAX_CHANNELS,
                        help=f'Total channel (default: {MAX_CHANNELS})')
    parser.add_argument('--category', '-g', default=None,
                        help='Filter kategori (contoh: "Just Chatting", "League of Legends")')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 18)')
    parser.add_argument('--delay', '-d', type=float, default=REQUEST_DELAY,
                        help='Jeda antar request (default: 0.2)')
    args = parser.parse_args()

    REQUEST_DELAY = args.delay
    generate_playlist(
        output_file=args.output,
        max_channels=args.channels,
        max_workers=args.workers,
        category=args.category,
    )   
