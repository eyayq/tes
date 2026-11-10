#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR
- GQL API (gql.twitch.tv) — TANPA AUTH/OAuth
- PlaybackAccessToken (persisted query)
- Output: playlist.m3u (HLS .m3u8)
- Token expired: ~6 jam
"""

import re
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

# Hash terbaru (streamlink 8.0.0, Nov 2025)
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

# ─── Default channels (olahraga + gaming populer) ────────────────
DEFAULT_CHANNELS = [
    # Olahraga
    "twitchdrew",
    "twitchsports",
    "twitchnba",
    "twitchnfl",
    "twitchmlb",
    "twitchnhl",
    "twitchufc",
    "twitchf1",
    "twitchwwe",
    "twitchpremierleague",
    "twitchchampionsleague",
    "twitchboxing",
    "twitchgolf",
    "twitchtennis",
    # Gaming / Esport
    "shroud",
    "xqc",
    "pokimane",
    "ninja",
    "summit1g",
    "tenz",
    "bugha",
    "scout",
    "faker",
    "caps",
    "donk",
    "s1mple",
    "zywoo",
    "donk",
    "m0NESY",
    "twistzz",
    "jks",
    "nitr0",
    "ropz",
    "stewie2k",
    "electronic",
    "niko",
    "device",
    "matteo",
    "fame",
    "sjuush",
    "jame",
    "halluzinaati",
    "b1t",
    "spinx",
    "kyxsan",
    "mzinho",
    "flamez",
    "xertion",
    "s3ren",
    "twitchlol",
    "twitchvalorant",
    "twitchcs2",
    "twitchdota2",
    "twitchapex",
    "twitchoverwatch",
    "twitchfortnite",
    "twitchrainbow6",
    "twitchwarzone",
    "twitchcallofduty",
    "twitcheldenring",
    "twitchgta",
    "twitchminecraft",
    "twitchleagueoflegends",
    "twitchpubg",
    "twitchapexlegends",
    "twitchvalorant",
    "twitchcs2",
    "twitchdota2",
    "twitchoverwatch2",
    "twitchrainbowsix",
    "twitchwarzone",
    "twitchcallofduty",
    "twitcheldenring",
    "twitchgta",
    "twitchminecraft",
    "twitchleagueoflegends",
    "twitchpubg",
    "twitchapexlegends",
    "twitchvalorant",
    "twitchcs2",
    "twitchdota2",
    "twitchoverwatch2",
    "twitchrainbowsix",
    "twitchwarzone",
    "twitchcallofduty",
    "twitcheldenring",
    "twitchgta",
    "twitchminecraft",
    "twitchleagueoflegends",
    "twitchpubg",
    "twitchapexlegends",
]

# ─── Session ─────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update({
    "Client-ID": CLIENT_ID,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
})


# ─── Get stream URL via GQL ──────────────────────────────────────
def get_stream_url(channel, retries=2):
    """Get HLS URL for a Twitch channel via GQL PlaybackAccessToken."""
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

            r = _session.post(
                "https://gql.twitch.tv/gql",
                json=payload,
                timeout=TIMEOUT,
            )

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
                # Channel offline
                return None

            token_data = stream.get("playbackAccessToken", {})
            token = token_data.get("value", "")
            sig = token_data.get("signature", "")

            if not token or not sig:
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None

            # Build m3u8 URL
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


# ─── Get channel info (name, game, viewers) ──────────────────────
def get_channel_info(channel):
    """Get basic info about a channel via GQL."""
    try:
        payload = [{
            "operationName": "StreamMetadata",
            "variables": {"channelLogin": channel},
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "252a46e3f5b1ddc431b396e688331d8d020daec27079893ac7d4e6db759a7402",
                }
            }
        }]

        r = _session.post(
            "https://gql.twitch.tv/gql",
            json=payload,
            timeout=TIMEOUT,
        )

        if r.status_code != 200:
            return None

        data = r.json()
        if isinstance(data, list):
            data = data[0]

        info = data.get("data", {}).get("StreamMetadata", {})
        if not info:
            return None

        return {
            "channel": channel,
            "name": info.get("broadcaster", {}).get("displayName", channel),
            "game": info.get("game", {}).get("name", "Unknown"),
            "viewers": info.get("viewers", 0),
            "title": info.get("title", ""),
            "logo": info.get("broadcaster", {}).get("profileImageURL", ""),
        }

    except Exception:
        return None


# ─── Worker ──────────────────────────────────────────────────────
def fetch_worker(args):
    idx, channel = args
    try:
        url = get_stream_url(channel)
        info = get_channel_info(channel) if url else None
        return (idx, channel, url, info, None)
    except Exception as e:
        return (idx, channel, None, None, str(e))


# ─── Generate playlist ───────────────────────────────────────────
def generate_playlist(output_file, channels, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR")
    print(f"  {len(channels)} channels | {max_workers} threads")
    print("=" * 55)

    # Fetch all (PARALEL)
    print(f"\n[1/2] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(channels)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_worker, (i, ch)): i
            for i, ch in enumerate(channels)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, channel, url, info, error = future.result()
            results[idx] = (channel, url, info, error)
            done_count += 1
            if done_count % 20 == 0:
                print(f"  Progress: {done_count}/{len(channels)}")

    # Build playlist
    print(f"\n[2/2] Membangun playlist...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for item in results:
        if item is None:
            continue
        channel, url, info, error = item

        if error:
            error_count += 1
            continue
        if not url:
            offline_count += 1
            continue

        # Get metadata
        if info:
            name = info.get("name", channel)
            game = info.get("game", "Twitch")
            logo = info.get("logo", "")
            title = info.get("title", "")
        else:
            name = channel
            game = "Twitch"
            logo = ""
            title = ""

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
        description='Twitch M3U Playlist Generator')
    parser.add_argument('--output', '-o', default='twitch.m3u',
                        help='File output M3U (default: twitch.m3u)')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 20)')
    parser.add_argument('--channels', '-c', type=str, default=None,
                        help='File berisi list channel (1 per baris)')
    parser.add_argument('--channel', '-ch', type=str, default=None,
                        help='Single channel untuk test')
    args = parser.parse_args()

    # Determine channel list
    if args.channel:
        channels = [args.channel]
    elif args.channels:
        with open(args.channels, 'r') as f:
            channels = [line.strip().lower() for line in f if line.strip()]
    else:
        channels = DEFAULT_CHANNELS

    # Remove duplicates, preserve order
    seen = set()
    unique_channels = []
    for ch in channels:
        if ch not in seen:
            seen.add(ch)
            unique_channels.append(ch)

    generate_playlist(
        output_file=args.output,
        channels=unique_channels,
        max_workers=args.workers,
    )   
