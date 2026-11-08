#!/usr/bin/env python3
"""
RBTV Playlist Generator (v11 FINAL — GitHub Actions)
Protobuf: extract "vs" names + nearby varint IDs via binary scan
"""

import requests
import json
import gzip
import re
import zlib
import sys
import time
import struct
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

MAX_MATCHES = 50
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

def decode_api_response(resp) -> bytes:
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()
    if not content:
        return b""
    if enc == "gzip" or content[:2] == b'\x1f\x8b':
        try: content = gzip.decompress(content)
        except: pass
    elif enc == "br" and HAS_BROTLI:
        try: content = brotli.decompress(content)
        except: pass
    elif enc == "deflate":
        try: content = zlib.decompress(content)
        except:
            try: content = zlib.decompress(content, -zlib.MAX_WBITS)
            except: pass
    elif enc == "zstd" and HAS_ZSTD:
        try: content = zstd.ZstdDecompressor().decompress(content)
        except: pass
    return content


def try_json_or_protobuf(content: bytes):
    try:
        text = content.decode('utf-8')
        if text.strip().startswith('{') or text.strip().startswith('['):
            return ('json', json.loads(text))
    except:
        pass
    return ('protobuf', content)


# ==================== PROTOBUF BINARY EXTRACTOR ====================

def read_varint_at(data, i):
    """Read varint at position i. Returns (value, bytes_consumed)."""
    result = 0
    shift = 0
    start = i
    while i < len(data) and shift < 35:
        byte = data[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not (byte & 0x80):
            return result, i - start
        shift += 7
    return None, i - start


def find_match_entries(data: bytes) -> list[dict]:
    """
    Find match entries by:
    1. Find all "X vs Y" strings in binary
    2. For each, scan backwards for \x08 + varint (field 1 = match ID)
    """
    matches = []
    seen_names = set()

    # Find all UTF-8 strings containing " vs " or " VS "
    # Protobuf strings are: [tag_byte][length_varint][string_bytes]
    # We search for the pattern in raw bytes
    
    # First pass: find all "vs" string positions
    vs_positions = []
    for m in re.finditer(rb'[\x20-\x7e]{3,} vs [\x20-\x7e]{3,}', data):
        vs_positions.append((m.start(), m.end(), m.group().decode()))

    print(f"[DEBUG] Found {len(vs_positions)} 'vs' strings in binary",
          file=sys.stderr, flush=True)

    for pos_start, pos_end, name in vs_positions:
        if name in seen_names:
            continue
        seen_names.add(name)

        # Scan backwards from pos_start to find \x08 + varint (match ID)
        # The ID should be within ~500 bytes before the name
        match_id = None
        search_start = max(0, pos_start - 500)
        
        i = pos_start - 1
        while i >= search_start:
            if data[i] == 0x08:  # field 1, wire type 0 (varint)
                val, consumed = read_varint_at(data, i + 1)
                if val is not None and 10000 < val < 9999999999:
                    match_id = val
                    break
            i -= 1

        matches.append({
            "name": name,
            "id": match_id,
            "pos": pos_start,
        })

    return matches


def extract_urls_from_binary(data: bytes) -> list[str]:
    """Find all stream-related URLs in binary."""
    urls = []
    patterns = [
        rb'https?://[^\x00-\x1f\x7f-\xff]+/cfall/[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+',
        rb'https?://[^\x00-\x1f\x7f-\xff]+\.m3u8[^\x00-\x1f\x7f-\xff]*',
        rb'https?://[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+',
    ]
    for p in patterns:
        for m in re.finditer(p, data):
            url = m.group().decode()
            if url not in urls:
                urls.append(url)
    return urls


# ==================== API ====================

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
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    print(f"[DEBUG] /match/live: status={resp.status_code}, "
          f"len={len(content)}, format={fmt}", file=sys.stderr, flush=True)

    if fmt == "json":
        if isinstance(data, dict):
            return (data.get("data") or data.get("list")
                    or data.get("matches") or data.get("items") or []), []
        return (data if isinstance(data, list) else []), []

    # Protobuf
    matches = find_match_entries(content)
    urls = extract_urls_from_binary(content)
    
    print(f"[DEBUG] Parsed: {len(matches)} matches, {len(urls)} URLs",
          file=sys.stderr, flush=True)
    for m in matches[:5]:
        print(f"  - {m['name']} (id={m['id']})", file=sys.stderr, flush=True)
    
    return matches, urls


def get_match_detail(md5, match_id, sport_type):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)
    if fmt == "json":
        return data
    return content  # raw bytes


def extract_stream_url(data) -> str | None:
    if isinstance(data, (dict, list)):
        json_str = json.dumps(data)
        for p in [r'(https?://[^"\s]+/cfall/[^"\s]+/super/[^"\s]+)',
                  r'(https?://[^"\s]+\.m3u8[^"\s]*)',
                  r'(https?://[^"\s]+/super/[^"\s]+)']:
            m = re.search(p, json_str)
            if m:
                return m.group(1)
        return None
    elif isinstance(data, bytes):
        for p in [rb'(https?://[^\x00-\x1f\x7f-\xff]+/cfall/[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+)',
                  rb'(https?://[^\x00-\x1f\x7f-\xff]+\.m3u8[^\x00-\x1f\x7f-\xff]*)',
                  rb'(https?://[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+)']:
            m = re.search(p, data)
            if m:
                return m.group(1).decode()
    return None


# ==================== LIVE API ====================

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
                content = decode_api_response(resp)
                return extract_stream_url(content)
        except:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = [pool.submit(try_url, u) for u in endpoints]
        for f in as_completed(futures, timeout=12):
            try:
                result = f.result(timeout=3)
                if result:
                    return result
            except:
                continue
    return None


# ==================== MAIN ====================

def main():
    t0 = time.time()

    print("Getting signing key...", flush=True)
    md5 = get_signing_key()
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)
    print(f"Key: {md5}", flush=True)

    print("Getting live matches...", flush=True)
    matches, global_urls = get_live_matches(md5)
    print(f"Found {len(matches)} matches", flush=True)

    if not matches:
        print("No live matches found.", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    # If URLs found directly in live response, use them
    if global_urls:
        print(f"[INFO] Found {len(global_urls)} stream URLs in live response!",
              file=sys.stderr, flush=True)
        entries = []
        for i, url in enumerate(global_urls[:MAX_MATCHES]):
            name = matches[i]["name"] if i < len(matches) else f"Stream {i+1}"
            entries.append((name, url))
    else:
        # Need to fetch detail per match
        entries = []

        def fetch_one(match):
            name = match["name"]
            match_id = match.get("id")

            if not match_id:
                print(f"[WARN] No ID for: {name}", file=sys.stderr, flush=True)
                return (name, None)

            detail = get_match_detail(md5, match_id, sport_type=SPORT_TYPE)
            stream_url = extract_stream_url(detail)
            if stream_url:
                return (name, stream_url)

            stream_url = get_stream_from_live_api(md5, match_id, SPORT_TYPE)
            return (name, stream_url)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(fetch_one, m) for m in matches[:MAX_MATCHES]]
            for f in as_completed(futures, timeout=TIMEOUT * 4):
                try:
                    result = f.result(timeout=TIMEOUT * 2)
                    if result and result[1]:
                        entries.append(result)
                    elif result:
                        print(f"[WARN] No URL: {result[0]}", file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"[WARN] {e}", file=sys.stderr, flush=True)

    elapsed = time.time() - t0
    print(f"\nGenerated {len(entries)} streams in {elapsed:.1f}s", flush=True)

    lines = ["#EXTM3U"]
    for name, stream_url in entries:
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
        lines.append(stream_url)

    m3u_content = "\n".join(lines) + "\n"
    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    print(f"\n{'='*55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'='*55}", flush=True)


if __name__ == "__main__":
    main()   
