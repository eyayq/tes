#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v5 (FINAL)
- Helix API (REST) untuk fetch channels — lebih stabil dari GQL
- GQL PrefetchPlaybackAccessToken + Client-Integrity
- Fallback ke streamlink jika GQL gagal
- Rate limit handling + exponential backoff
- Output: twitch.m3u (HLS .m3u8)
- Token expired: ~6 jam
"""

import json
import time
import argparse
import uuid
import random
import threading
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ─── Konfigurasi ─────────────────────────────────────────────────
TIMEOUT = 15
MAX_WORKERS = 10
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

_session = requests.Session()
_session.headers.update({
    "Client-ID": CLIENT_ID,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://www.twitch.tv",
    "Referer": "https://www.twitch.tv/",
})

# ─── Client-Integrity (di-fetch 1x, cache 16 jam) ────────────────
_integrity = None
_integrity_lock = threading.Lock()


def get_client_integrity():
    """Fetch Client-Integrity token (expires ~16 hours)."""
    global _integrity
    with _integrity_lock:
        if _integrity and time.time() - _integrity.get('ts', 0) < 54000:
            return _integrity.get('value')
    try:
        r = _session.get("https://gql.twitch.tv/integrity", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            _integrity = {
                'value': data.get('integrity', ''),
                'ts': time.time(),
            }
            return _integrity['value']
    except Exception:
        pass
    return None


# ─── GQL Request (retry + backoff + Client-Integrity) ────────────
def _gql_request(payload, retries=5):
    for attempt in range(retries + 1):
        try:
            headers = {}
            integrity = get_client_integrity()
            if integrity:
                headers["Client-Integrity"] = integrity

            r = _session.post(
                "https://gql.twitch.tv/gql",
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            )
            if r.status_code == 429:
                wait = 2 ** attempt + random.uniform(0, 2)
                print(f"  [RATE LIMIT] Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            if r.status_code == 503:
                wait = 3 ** attempt
                print(f"  [SERVER 503] Waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(2)
                    continue
                return None
            data = r.json()
            # Cek "server error" di body
            if isinstance(data, list) and data and "errors" in data[0]:
                msg = data[0]["errors"][0].get("message", "")
                if "server error" in msg.lower() or "service error" in msg.lower():
                    wait = 2 ** attempt + random.uniform(0, 2)
                    print(f"  [SERVER ERROR] Retry in {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                if "PersistedQueryNotFound" in msg:
                    print(f"  [ERROR] Hash expired: {msg}")
                    return None
            return data
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None


# ─── Fetch LIVE channels via HELIX API (REST, lebih stabil) ──────
def get_live_channels_helix(count=100):
    """Fetch live channels via Twitch Helix REST API."""
    channels = []
    after = ""

    while len(channels) < count:
        url = "https://api.twitch.tv/helix/streams"
        params = {
            "first": min(100, count - len(channels)),
        }
        if after:
            params["after"] = after

        try:
            r = _session.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = 2 ** len(channels)
                print(f"  [HELIX RATE LIMIT] Waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"  [HELIX HTTP {r.status_code}]")
                break

            data = r.json()
            streams = data.get("data", [])
            if not streams:
                break

            for s in streams:
                channels.append({
                    "id": s.get("user_login", "").lower(),
                    "name": s.get("user_name", ""),
                    "game": s.get("game_name", "Unknown"),
                    "viewers": s.get("viewer_count", 0),
                    "title": s.get("title", ""),
                    "logo": s.get("user_id", ""),  # placeholder
                })

            after = data.get("pagination", {}).get("cursor", "")
            if not after:
                break

        except Exception as e:
            print(f"  [HELIX ERROR] {e}")
            break

    # Fetch logos via Helix users API
    if channels:
        try:
            user_ids = []
            for ch in channels:
                # Re-fetch user_id
                pass
            # Skip logo fetch for speed — use default
        except Exception:
            pass

    return channels[:count]


# ─── Fetch LIVE channels via GQL (fallback) ─────────────────────
def get_live_channels_gql(count=100):
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

        data = _gql_request({"query": query, "variables": variables}, retries=3)
        if data is None:
            break

        if "errors" in data:
            print(f"  [GQL ERROR] {data['errors'][0]['message']}")
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


# ─── Get stream URL via GQL ──────────────────────────────────────
def get_stream_url_gql(channel, retries=3):
    for attempt in range(retries + 1):
        try:
            payload = [{
                "operationName": "PrefetchPlaybackAccessToken",
                "variables": {
                    "login": channel,
                    "playerType": "embed",
                    "platform": "web",
                },
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": GQL_HASH,
                    }
                }
            }]

            data = _gql_request(payload, retries=0)
            if data is None:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

            if isinstance(data, list):
                data = data[0]

            if "errors" in data:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

            stream_info = data.get("data", {}).get("streamerInfo", {})
            stream = stream_info.get("stream")

            if not stream:
                return None

            token_data = stream.get("playbackAccessToken", {})
            token = token_data.get("value", "")
            sig = token_data.get("signature", "")

            if not token or not sig:
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

            session_id = str(uuid.uuid4())
            ch_lower = channel.lower()

            url = (
                f"https://usher.ttvnw.net/api/v2/channel/hls/{ch_lower}.m3u8"
                f"?sig={quote(sig, safe='')}"
                f"&token={quote(token, safe='')}"
                f"&platform=web"
                f"&allow_source=true"
                f"&allow_audio_only=true"
                f"&playlist_include_framerate=true"
                f"&supported_codecs=av1,h265,h264"
                f"&player_backend=mediaplayer"
                f"&player_version=1.41.0-rc.1"
                f"&play_session_id={session_id}"
            )
            return url

        except Exception:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))

    return None


# ─── Get stream URL via streamlink (fallback) ────────────────────
def get_stream_url_streamlink(channel):
    try:
        import streamlink
        sl = streamlink.Streamlink()
        streams = sl.streams(f"https://twitch.tv/{channel}", check_freshness=True)
        if streams:
            # Ambil kualitas terbaik
            for quality in ["source", "1080p60", "1080p", "720p60", "720p", "480p", "360p", "best"]:
                if quality in streams:
                    return streams[quality].url
        return None
    except Exception:
        return None


# ─── Get stream URL (GQL → streamlink) ───────────────────────────
def get_stream_url(channel):
    url = get_stream_url_gql(channel)
    if url:
        return url
    # Fallback ke streamlink
    url = get_stream_url_streamlink(channel)
    return url


# ─── Worker ──────────────────────────────────────────────────────
def fetch_worker(args):
    idx, ch = args
    try:
        time.sleep(random.uniform(0.1, 0.3))
        url = get_stream_url(ch["id"])
        return (idx, ch, url, None)
    except Exception as e:
        return (idx, ch, None, str(e))


# ─── Generate playlist ───────────────────────────────────────────
def generate_playlist(output_file, max_channels, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v5")
    print(f"  {max_channels} channels | {max_workers} threads")
    print("=" * 55)

    # Step 1: Fetch live channels (Helix → GQL → Fallback)
    print(f"\n[1/3] Fetching live channels...")
    channels = get_live_channels_helix(count=max_channels)
    source = "Helix API"

    if not channels:
        print("  [INFO] Helix gagal, coba GQL...")
        channels = get_live_channels_gql(count=max_channels)
        source = "GQL"

    if not channels:
        print("  [FALLBACK] API gagal, pakai known channels...")
        fallback = [
            "shroud", "xqc", "pokimane", "ninja", "summit1g",
            "tenz", "bugha", "scout", "faker", "caps",
            "donk", "s1mple", "zywoo", "m0nesy", "twistzz",
            "jks", "nitr0", "ropz", "stewie2k", "electronic",
            "niko", "device", "fame", "sjuush", "jame",
            "b1t", "spinx", "kyxsan", "mzinho", "flamez",
            "dr_disrespect", "trainwreckstv", "wellplay",
            "moistcr1tdial", "lirik", "jynxzi", "vergel",
            "sneaky", "austinholovious", "adrian", "tarik",
        ]
        for ch in fallback:
            channels.append({
                "id": ch,
                "name": ch.capitalize(),
                "game": "Unknown",
                "viewers": 0,
                "title": "",
                "logo": "",
            })
        source = "Fallback"

    print(f"  Channel live ditemukan: {len(channels)} (source: {source})")

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

        display_name = f"{name} — {title}" if title else name

        extinf = f'#EXTINF:-1'
        if logo and logo.startswith("http"):
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
        description='Twitch M3U Playlist Generator v5')
    parser.add_argument('--output', '-o', default='twitch.m3u',
                        help='File output M3U (default: twitch.m3u)')
    parser.add_argument('--channels', '-c', type=int, default=100,
                        help='Jumlah channel (default: 100)')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 10)')
    args = parser.parse_args()

    generate_playlist(
        output_file=args.output,
        max_channels=args.channels,
        max_workers=args.workers,
    )   
