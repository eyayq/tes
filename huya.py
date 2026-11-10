


#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v8
"""

import time
import argparse
import uuid
import random
import string
import base64
import json
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ─── Konfigurasi ─────────────────────────────────────────────────
TIMEOUT = 15
MAX_WORKERS = 10
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

# ===== ISI TOKEN DARI BROWSER DEVTOOLS (Network → GQL request → authorization header) =====
AUTH_TOKEN = "i6znx2o7mcfa0ut0d2vq2ksp7otyzs"  # contoh: "i6znx2o7mcfa0ut0d2vq2ksp7otyzs"
# =========================================================================================

# ─── Session & Identitas ─────────────────────────────────────────
_session = requests.Session()

_DEVICE_ID = uuid.uuid4().hex[:16]
_CLIENT_SESSION_ID = uuid.uuid4().hex[:16]
_CLIENT_VERSION = None

_session.headers.update({
    "Client-ID": CLIENT_ID,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
    "Origin": "https://www.twitch.tv",
    "Referer": "https://www.twitch.tv/",
    "Content-Type": "application/json",
    "X-Device-Id": _DEVICE_ID,
    "Client-Session-Id": _CLIENT_SESSION_ID,
})


def _get_client_version():
    global _CLIENT_VERSION
    if _CLIENT_VERSION:
        return _CLIENT_VERSION
    try:
        r = _session.get(
            "https://static.twitchcdn.net/config/manifest.json",
            params={"v": "1"},
            timeout=TIMEOUT,
        )
        _CLIENT_VERSION = r.json().get("buildId", str(uuid.uuid4()))
    except Exception:
        _CLIENT_VERSION = str(uuid.uuid4())
    _session.headers["Client-Version"] = _CLIENT_VERSION
    return _CLIENT_VERSION


def _gen_request_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))


# ─── Client Integrity Token ──────────────────────────────────────
_client_integrity = None
_integrity_expiry = 0


def get_client_integrity():
    global _client_integrity, _integrity_expiry

    if _client_integrity and time.time() < _integrity_expiry:
        return _client_integrity

    headers = {
        "Client-ID": CLIENT_ID,
        "X-Device-Id": _DEVICE_ID,
        "Client-Session-Id": _CLIENT_SESSION_ID,
        "Client-Version": _get_client_version(),
        "Client-Request-Id": _gen_request_id(),
    }
    if AUTH_TOKEN:
        headers["Authorization"] = f"OAuth {AUTH_TOKEN}"

    r = _session.post(
        "https://gql.twitch.tv/integrity",
        json={},
        headers=headers,
        timeout=TIMEOUT,
    )

    if r.status_code != 200:
        print(f"  [INTEGRITY] HTTP {r.status_code}")
        return None

    data = r.json()
    token = data.get("token")
    if not token:
        print(f"  [INTEGRITY] No token: {data}")
        return None

    expiry = data.get("expiration", 0)
    _client_integrity = token
    _integrity_expiry = (expiry / 1000) - 7200 if expiry else time.time() + 14 * 3600
    return token


# ─── GQL Request ─────────────────────────────────────────────────
def _gql_request(payload, retries=3):
    integrity = get_client_integrity()

    for attempt in range(retries + 1):
        try:
            headers = {
                "Client-Request-Id": _gen_request_id(),
            }
            if integrity:
                headers["Client-Integrity"] = integrity
            if AUTH_TOKEN:
                headers["Authorization"] = f"OAuth {AUTH_TOKEN}"

            r = _session.post(
                "https://gql.twitch.tv/gql",
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            )

            if r.status_code == 429:
                wait = 2 ** attempt + random.uniform(0, 1)
                print(f"  [RATE LIMIT] Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                if attempt < retries:
                    time.sleep(2)
                    continue
                return None

            data = r.json()

            if isinstance(data, list):
                data = data[0]

            errors = data.get("errors") if isinstance(data, dict) else None
            if errors:
                msg = errors[0].get("message", "unknown")
                if msg.lower() in ("server error", "service error") and attempt < retries:
                    global _client_integrity, _integrity_expiry
                    _client_integrity = None
                    _integrity_expiry = 0
                    integrity = get_client_integrity()
                    wait = 2 ** attempt + random.uniform(0, 2)
                    print(f"  [TRANSIENT] '{msg}' — retry in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                print(f"  [GQL ERROR] {msg}")
                return None

            return data

        except Exception:
            if attempt < retries:
                time.sleep(2)

    return None


# ─── Fetch LIVE channels ─────────────────────────────────────────
def get_live_channels(count=100):
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

        data = _gql_request({"query": query, "variables": variables}, retries=4)
        if data is None:
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

            data = _gql_request(payload, retries=0)
            if data is None:
                if attempt < retries:
                    time.sleep(1)
                    continue
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
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None

            ch_lower = channel.lower()
            session_id = uuid.uuid4().hex
            p_val = random.randint(1000000, 9999999)

            acmb_obj = {
                "AppVersion": _get_client_version(),
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

        except Exception:
            if attempt < retries:
                time.sleep(1)

    return None


# ─── Worker ──────────────────────────────────────────────────────
def fetch_worker(args):
    idx, ch = args
    try:
        time.sleep(random.uniform(0.05, 0.15))
        url = get_stream_url(ch["id"])
        return (idx, ch, url, None)
    except Exception as e:
        return (idx, ch, None, str(e))


# ─── Generate playlist ───────────────────────────────────────────
def generate_playlist(output_file, max_channels, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v8")
    print(f"  {max_channels} channels | {max_workers} threads")
    print("=" * 55)

    print(f"\n[0/3] Initializing...")
    cv = _get_client_version()
    print(f"  Client-Version: {cv}")
    print(f"  Device-ID:      {_DEVICE_ID}")
    print(f"  Session-ID:     {_CLIENT_SESSION_ID}")
    print(f"  Auth-Token:     {'SET' if AUTH_TOKEN else 'NOT SET'}")

    integrity = get_client_integrity()
    if integrity:
        print(f"  Integrity:      OK")
    else:
        print(f"  Integrity:      FAILED")

    print(f"\n[1/3] Fetching live channels...")
    channels = get_live_channels(count=max_channels)
    print(f"  Channel live ditemukan: {len(channels)}")

    if not channels:
        print("  [ERROR] Tidak ada channel live!")
        return

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
    print(f"  Stream token expired: ~6 jam")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Twitch M3U Playlist Generator v8')
    parser.add_argument('--output', '-o', default='twitch.m3u',
                        help='File output M3U (default: twitch.m3u)')
    parser.add_argument('--channels', '-c', type=int, default=100,
                        help='Jumlah channel (default: 100)')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 10)')
    parser.add_argument('--auth-token', '-t', default=AUTH_TOKEN,
                        help='OAuth token dari browser (wajib)')
    args = parser.parse_args()

    if args.auth_token:
        # Override global
        globals()['AUTH_TOKEN'] = args.auth_token
        # Also set on session
        _session.headers["Authorization"] = f"OAuth {args.auth_token}"

    if not AUTH_TOKEN:
        print("[ERROR] AUTH_TOKEN kosong!")
        print("  Ambil dari browser: DevTools → Network → GQL request → header 'authorization'")
        print("  Format: OAuth <token>")
        print("  Atau pakai flag: --auth-token <token>")
        exit(1)

    generate_playlist(
        output_file=args.output,
        max_channels=args.channels,
        max_workers=args.workers,
    )   
