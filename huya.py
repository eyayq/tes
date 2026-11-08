#!/usr/bin/env python3
"""
RBTV Playlist Generator (for GitHub Actions)
One-shot: scrape → output M3U
"""

import requests
import json
import gzip
import re
import zlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LIVE_URL = "https://apis-live.ta2mnt200stayr2.cfd"
LIVE_URL_EU = "https://apis-live-defra.ta2mnt200stayr2.cfd"

MAX_MATCHES = 100
TIMEOUT = 10
LIVE_TIMEOUT = 8
SPORT_TYPE = 1
LANGUAGE = 34

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "accept-encoding": "identity",
}


def decode_response(resp) -> str:
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()
    if enc == "gzip" or content[:2] == b'\x1f\x8b':
        content = gzip.decompress(content)
    elif enc == "br" and HAS_BROTLI:
        content = brotli.decompress(content)
    elif enc == "deflate":
        try:
            content = zlib.decompress(content)
        except Exception:
            content = zlib.decompress(content, -zlib.MAX_WBITS)
    return content.decode("utf-8", errors="replace")


def get_signing_key():
    url = f"{DATA_URL}/api/common/bs?code=100&sportType={SPORT_TYPE}&stream=true"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    m = re.search(r'[a-f0-9]{32}', resp.text)
    if not m:
        m = re.search(rb'[a-f0-9]{32}', resp.content)
        return m.group().decode() if m else None
    return m.group()


def get_live_matches(md5):
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={SPORT_TYPE}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    data = json.loads(decode_response(resp))
    if isinstance(data, dict):
        return (data.get("data") or data.get("list")
                or data.get("matches") or data.get("items") or [])
    return data if isinstance(data, list) else []


def get_match_detail(md5, match_id, sport_type):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    return json.loads(decode_response(resp))


def get_stream_from_live_api(md5, match_id, sport_type):
    endpoints = [
        f"{LIVE_URL}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL}/sfver{md5}/api/match/stream?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL_EU}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
    ]

    def try_url(url):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=LIVE_TIMEOUT)
            if resp.status_code == 200:
                data = json.loads(decode_response(resp))
                return extract_stream_url(data)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = [pool.submit(try_url, u) for u in endpoints]
        for f in as_completed(futures, timeout=10):
            try:
                result = f.result(timeout=2)
                if result:
                    return result
            except Exception:
                continue
    return None


def extract_stream_url(data):
    json_str = json.dumps(data)
    m = re.search(r'(https?://[^"\s]+/cfall/[^"\s]+/super/[^"\s]+)', json_str)
    if m:
        return m.group(1)
    m = re.search(r'(https?://[^"\s]+\.m3u8[^"\s]*)', json_str)
    if m:
        return m.group(1)
    m = re.search(r'(https?://[^"\s]+/super/[^"\s]+)', json_str)
    if m:
        return m.group(1)
    m = re.search(r'"(?:streamUrl|playUrl|hlsUrl|videoUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"', json_str)
    if m:
        return m.group(1)
    return None


def get_match_name(detail_data, match_data):
    for src in [detail_data, match_data]:
        if not isinstance(src, dict):
            continue
        for key in ["title", "name", "matchName", "event"]:
            if src.get(key) and isinstance(src[key], str):
                return src[key]
        home = src.get("homeTeam") or src.get("home") or ""
        away = src.get("awayTeam") or src.get("away") or ""
        if isinstance(home, dict):
            home = home.get("name", "")
        if isinstance(away, dict):
            away = away.get("name", "")
        if home and away:
            return f"{home} vs {away}"
    return "Unknown"


def main():
    print("Getting signing key...", flush=True)
    md5 = get_signing_key()
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr)
        sys.exit(1)
    print(f"Key: {md5}", flush=True)

    print("Getting live matches...", flush=True)
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"Found {len(matches)} matches", flush=True)

    entries = []

    def fetch_one(match):
        match_id = (match.get("id") or match.get("matchId") or match.get("eventId"))
        sport_type = match.get("sportType", SPORT_TYPE)
        if not match_id:
            return None
        detail = get_match_detail(md5, match_id, sport_type=sport_type)
        stream_url = extract_stream_url(detail)
        if not stream_url:
            stream_url = get_stream_from_live_api(md5, match_id, sport_type)
        name = get_match_name(detail, match)
        return (name, stream_url)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_one, m) for m in matches if isinstance(m, dict)]
        for f in as_completed(futures, timeout=TIMEOUT * 3):
            try:
                result = f.result(timeout=TIMEOUT * 2)
                if result and result[1]:
                    entries.append(result)
            except Exception as e:
                print(f"[WARN] {e}", file=sys.stderr, flush=True)

    # Output M3U
    lines = ["#EXTM3U"]
    for name, stream_url in entries:
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
        lines.append(stream_url)

    m3u_content = "\n".join(lines)

    # Write to file (GitHub Actions artifact)
    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    # Also print to stdout
    print(f"\n{'='*55}", flush=True)
    print(f"  Generated: {len(entries)} streams", flush=True)
    print(f"{'='*55}\n", flush=True)
    print(m3u_content, flush=True)


if __name__ == "__main__":
    main()   
