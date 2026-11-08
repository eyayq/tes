#!/usr/bin/env python3
"""
RBTV Playlist Generator (v8 FINAL — GitHub Actions)
One-shot: scrape API → output playlist.m3u
"""

import requests
import json
import gzip
import re
import zlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

# ==================== CONFIG ====================

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LIVE_URL = "https://apis-live.ta2mnt200stayr2.cfd"
LIVE_URL_EU = "https://apis-live-defra.ta2mnt200stayr2.cfd"

MAX_MATCHES = 30
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


# ==================== DECODE ====================

def decode_response(resp) -> str:
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()

    if not content:
        print(f"[DEBUG] Empty response! Status: {resp.status_code}, "
              f"Headers: {dict(resp.headers)}", file=sys.stderr, flush=True)
        return ""

    if enc == "gzip" or content[:2] == b'\x1f\x8b':
        try:
            content = gzip.decompress(content)
        except Exception as e:
            print(f"[DEBUG] Gzip decompress failed: {e}", file=sys.stderr, flush=True)
            return ""
    elif enc == "br" and HAS_BROTLI:
        try:
            content = brotli.decompress(content)
        except Exception as e:
            print(f"[DEBUG] Brotli decompress failed: {e}", file=sys.stderr, flush=True)
            return ""
    elif enc == "deflate":
        try:
            content = zlib.decompress(content)
        except Exception:
            try:
                content = zlib.decompress(content, -zlib.MAX_WBITS)
            except Exception as e:
                print(f"[DEBUG] Deflate failed: {e}, raw[:100]={content[:100]}",
                      file=sys.stderr, flush=True)
                return ""
    elif enc == "zstd":
        if HAS_ZSTD:
            try:
                content = zstd.ZstdDecompressor().decompress(content)
            except Exception as e:
                print(f"[DEBUG] Zstd decompress failed: {e}", file=sys.stderr, flush=True)
                return ""
        else:
            print("[DEBUG] zstd encoding but no zstandard lib! "
                  "Install: pip install zstandard", file=sys.stderr, flush=True)
            return ""
    elif enc and enc != "identity":
        print(f"[DEBUG] Unknown encoding: {enc}, content[:100]={content[:100]}",
              file=sys.stderr, flush=True)

    return content.decode("utf-8", errors="replace")


def safe_json(resp, context=""):
    """Decode + parse JSON with error handling. Returns None on failure."""
    text = decode_response(resp)
    if not text.strip():
        print(f"[ERROR] Empty response from {context}", file=sys.stderr, flush=True)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON from {context}: {e}\n"
              f"  First 300 chars: {text[:300]}", file=sys.stderr, flush=True)
        return None


# ==================== API: DATA ====================

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

    print(f"[DEBUG] /match/live status={resp.status_code}, "
          f"len={len(resp.content)}, "
          f"encoding={resp.headers.get('content-encoding', 'none')}",
          file=sys.stderr, flush=True)
    if len(resp.content) > 0:
        print(f"[DEBUG] Raw[:200]={resp.content[:200]}", file=sys.stderr, flush=True)

    data = safe_json(resp, "/match/live")
    if data is None:
        return []

    if isinstance(data, dict):
        return (data.get("data") or data.get("list")
                or data.get("matches") or data.get("items") or [])
    return data if isinstance(data, list) else []


def get_match_detail(md5, match_id, sport_type):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    data = safe_json(resp, f"/match/detail?id={match_id}")
    return data if data is not None else {}


# ==================== API: LIVE (STREAM) ====================

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
                data = safe_json(resp, f"live_api?id={match_id}")
                if data:
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


# ==================== EXTRACT ====================

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

    m = re.search(
        r'"(?:streamUrl|playUrl|hlsUrl|videoUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"',
        json_str)
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


# ==================== MAIN ====================

def main():
    t0 = time.time()

    print("Getting signing key...", flush=True)
    md5 = get_signing_key()
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(1)
    print(f"Key: {md5}", flush=True)

    print("Getting live matches...", flush=True)
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"Found {len(matches)} matches", flush=True)

    if not matches:
        print("No live matches found. Writing empty playlist.",
              file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    entries = []

    def fetch_one(match):
        match_id = (match.get("id") or match.get("matchId")
                    or match.get("eventId"))
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
        futures = [pool.submit(fetch_one, m)
                   for m in matches if isinstance(m, dict)]
        for f in as_completed(futures, timeout=TIMEOUT * 4):
            try:
                result = f.result(timeout=TIMEOUT * 2)
                if result and result[1]:
                    entries.append(result)
            except Exception as e:
                print(f"[WARN] {e}", file=sys.stderr, flush=True)

    elapsed = time.time() - t0
    print(f"\nGenerated {len(entries)} streams in {elapsed:.1f}s", flush=True)

    # Build M3U
    lines = ["#EXTM3U"]
    for name, stream_url in entries:
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
        lines.append(stream_url)

    m3u_content = "\n".join(lines) + "\n"

    # Write file
    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    # Print to stdout (visible in GitHub Actions log)
    print(f"\n{'='*55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'='*55}", flush=True)


if __name__ == "__main__":
    main()   
