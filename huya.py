#!/usr/bin/env python3
"""
TWITCH M3U PLAYLIST GENERATOR v13 (final)
"""

import time
import argparse
import uuid
import random
import string
import base64
import json
import os
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests

# ─── Konfigurasi ─────────────────────────────────────────────────
TIMEOUT = 15
MAX_WORKERS = 10
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_HASH = "c101f277c6ab7de34f64e63c90d698edae0d83ed5fad8efe198596b472ef3337"

# ─── File cache ──────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TOKENS_FILE = os.path.join(_BASE_DIR, ".twitch_tokens.json")
_BROWSER_PROFILE = os.path.join(_BASE_DIR, ".twitch_browser")


# ─── Scrape tokens via Playwright ────────────────────────────────
def scrape_tokens():
    from playwright.sync_api import sync_playwright

    print("[SCRAPE] Membuka browser (headed, butuh X server / xvfb)...")
    print("[SCRAPE] Login jika diminta. Menunggu GQL requests...\n")

    captured = {}
    done = False

    def on_request(request):
        nonlocal done
        if "gql.twitch.tv/gql" in request.url and not done:
            h = request.headers
            auth = h.get("authorization", "")
            device_id = h.get("x-device-id", "")
            session_id = h.get("client-session-id", "")
            client_version = h.get("client-version", "")

            if auth and device_id and session_id:
                captured["auth_token"] = auth.replace("OAuth ", "")
                captured["device_id"] = device_id
                captured["session_id"] = session_id
                captured["client_version"] = client_version
                captured["scraped_at"] = time.time()
                done = True

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            _BROWSER_PROFILE,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.on("request", on_request)
        page.goto("https://www.twitch.tv", timeout=60000)

        for _ in range(120):
            if done:
                break
            time.sleep(0.5)

        context.close()

    if not done:
        print("[SCRAPE] Gagal capture tokens!")
        return None

    with open(_TOKENS_FILE, "w") as f:
        json.dump(captured, f, indent=2)

    print(f"[SCRAPE] Token di-capture & disimpan.")
    print(f"  Auth Token:    {captured['auth_token'][:20]}...")
    print(f"  Device ID:     {captured['device_id']}")
    print(f"  Session ID:    {captured['session_id']}")
    print(f"  Client Ver:    {captured['client_version']}")
    return captured


def load_tokens():
    """Load tokens: cache → env vars → scrape."""
    # 1. Cache
    if os.path.exists(_TOKENS_FILE):
        with open(_TOKENS_FILE) as f:
            tokens = json.load(f)
        if time.time() - tokens.get("scraped_at", 0) < 14 * 3600:
            age_h = (time.time() - tokens["scraped_at"]) / 3600
            print(f"[TOKENS] Cache valid ({age_h:.1f}h lalu)")
            return tokens
        print("[TOKENS] Cache expired (>14h)")

    # 2. Env vars
    env_auth = os.environ.get("TWITCH_AUTH_TOKEN", "")
    env_device = os.environ.get("TWITCH_DEVICE_ID", "")
    env_session = os.environ.get("TWITCH_SESSION_ID", "")
    if env_auth and env_device and env_session:
        print("[TOKENS] Menggunakan env vars")
        return {
            "auth_token": env_auth,
            "device_id": env_device,
            "session_id": env_session,
            "client_version": os.environ.get("TWITCH_CLIENT_VERSION", "a989fade-ca86-4058-933e-10133becfe0f"),
            "scraped_at": time.time(),
        }

    # 3. Scrape via browser
    return scrape_tokens()


# ─── GQL Session ─────────────────────────────────────────────────
_session = cffi_requests.Session(impersonate="chrome152")


def _init_session(tokens):
    """Set header GQL — PERSIS seperti browser (TANPA integrity/request-id)."""
    _session.headers.update({
        "Client-ID": CLIENT_ID,
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
        "X-Device-Id": tokens["device_id"],
        "Client-Session-Id": tokens["session_id"],
        "Client-Version": tokens["client_version"],
        "Content-Type": "text/plain;charset=UTF-8",
        "Authorization": f"OAuth {tokens['auth_token']}",
    })


def _gql_request(payload, retries=3):
    for attempt in range(retries + 1):
        try:
            # TANPA Client-Integrity, TANPA Client-Request-Id
            # (browser tidak mengirim header ini ke /gql)
            body = json.dumps(payload)
            r = _session.post(
                "https://gql.twitch.tv/gql",
                data=body,
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
                if attempt < retries:
                    wait = 2 ** attempt + random.uniform(0, 2)
                    print(f"  [TRANSIENT] '{msg}' — retry in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                print(f"  [GQL ERROR] {msg}")
                return None

            return data

        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                print(f"  [EXC] {e}")

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
def get_stream_url(channel, tokens, retries=2):
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
                "AppVersion": tokens["client_version"],
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
    idx, ch, tokens = args
    try:
        time.sleep(random.uniform(0.05, 0.15))
        url = get_stream_url(ch["id"], tokens)
        return (idx, ch, url, None)
    except Exception as e:
        return (idx, ch, None, str(e))


# ─── Generate playlist ───────────────────────────────────────────
def generate_playlist(output_file, max_channels, max_workers):
    print("=" * 55)
    print("  TWITCH M3U PLAYLIST GENERATOR v13")
    print(f"  {max_channels} channels | {max_workers} threads")
    print("=" * 55)

    tokens = load_tokens()
    if not tokens:
        print("[ERROR] Gagal load tokens.")
        return

    _init_session(tokens)

    print(f"\n[1/3] Fetching live channels...")
    channels = get_live_channels(count=max_channels)
    print(f"  Channel live ditemukan: {len(channels)}")

    if not channels:
        print("  [ERROR] Tidak ada channel live!")
        print("  Kemungkinan session ID expired — update dari browser.")
        return

    print(f"\n[2/3] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(channels)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_worker, (i, ch, tokens)): i
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
        description='Twitch M3U Playlist Generator v13')
    parser.add_argument('--output', '-o', default='playlist.m3u',
                        help='File output M3U')
    parser.add_argument('--channels', '-c', type=int, default=100,
                        help='Jumlah channel')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread')
    parser.add_argument('--refresh', '-r', action='store_true',
                        help='Paksa scrape token baru')
    args = parser.parse_args()

    if args.refresh and os.path.exists(_TOKENS_FILE):
        os.remove(_TOKENS_FILE)
        print("[REFRESH] Cache dihapus — akan scrape baru.\n")

    generate_playlist(
        output_file=args.output,
        max_channels=args.channels,
        max_workers=args.workers,
    )   
