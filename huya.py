#!/usr/bin/env python3
"""
RBTV Playlist Generator (v12 FINAL — GitHub Actions)
Proper protobuf structure parse: field10 → repeated field1 → field1=ID
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


# ==================== PROTOBUF PARSER ====================

def read_varint(data, i):
    """Read varint at position i. Returns (value, new_i) or (None, i)."""
    result = 0
    shift = 0
    while i < len(data) and shift < 35:
        b = data[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
    return None, i


def parse_message(data, start=0, end=None):
    """
    Parse a protobuf message. Returns list of (field_num, wire_type, value).
    value: int for varint, bytes for length-delimited/64bit/32bit.
    """
    if end is None:
        end = len(data)
    fields = []
    i = start

    while i < end:
        tag, i = read_varint(data, i)
        if tag is None or tag == 0 or i >= end:
            break

        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            val, i = read_varint(data, i)
            if val is None:
                break
            fields.append((field_num, 0, val))
        elif wire_type == 1:  # 64-bit
            if i + 8 > end:
                break
            fields.append((field_num, 1, data[i:i+8]))
            i += 8
        elif wire_type == 2:  # length-delimited
            length, i = read_varint(data, i)
            if length is None or length < 0 or i + length > end:
                break
            fields.append((field_num, 2, data[i:i+length]))
            i += length
        elif wire_type == 5:  # 32-bit
            if i + 4 > end:
                break
            fields.append((field_num, 5, data[i:i+4]))
            i += 4
        else:
            break  # unknown wire type, stop

    return fields


def find_vs_name(data):
    """Find 'X vs Y' string in a protobuf sub-message bytes."""
    # Search for printable string containing " vs "
    m = re.search(rb'[\x20-\x7e]{2,} vs [\x20-\x7e]{2,}', data)
    if m:
        name = m.group().decode('utf-8', errors='replace')
        # Clean leading/trailing junk
        name = name.strip()
        # Remove leading non-alpha chars that are protobuf artifacts
        name = re.sub(r'^[^a-zA-Z\u00c0-\u024f]+', '', name)
        return name
    return None


def extract_matches_from_protobuf(data):
    """
    Parse: top-level field 10 → repeated field 1 (each = one match)
    Each match: field 1 (varint) = match_id, contains "X vs Y" string
    """
    # Step 1: Parse top-level
    top_fields = parse_message(data)
    
    # Find field 10 (the large length-delimited one containing all matches)
    field10_data = None
    for fnum, wtype, val in top_fields:
        if fnum == 10 and wtype == 2 and len(val) > 500:
            field10_data = val
            break
    
    # Fallback: largest field
    if not field10_data:
        candidates = [(fnum, val) for fnum, wtype, val in top_fields 
                      if wtype == 2 and len(val) > 500]
        if candidates:
            candidates.sort(key=lambda x: len(x[1]), reverse=True)
            field10_data = candidates[0][1]
    
    if not field10_data:
        print("[DEBUG] Could not find field 10 (matches container)", file=sys.stderr, flush=True)
        return []

    # Step 2: Parse field 10 content → should contain repeated field 1 messages
    inner_fields = parse_message(field10_data)
    
    matches = []
    for fnum, wtype, val in inner_fields:
        # Each match is field 1 (or possibly other field numbers) with wire type 2
        if wtype != 2 or len(val) < 20:
            continue
        
        # Step 3: Parse each match sub-message
        match_fields = parse_message(val)
        
        # Find match ID: field 1, wire type 0 (varint)
        match_id = None
        for mf_num, mf_wire, mf_val in match_fields:
            if mf_num == 1 and mf_wire == 0 and mf_val > 1000:
                match_id = mf_val
                break
        
        # Find "vs" name
        name = find_vs_name(val)
        
        if name:
            matches.append({
                "name": name,
                "id": match_id,
                "raw_len": len(val),
            })
    
    return matches


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


def try_json_or_protobuf(content):
    try:
        text = content.decode('utf-8')
        if text.strip().startswith('{') or text.strip().startswith('['):
            return ('json', json.loads(text))
    except:
        pass
    return ('protobuf', content)


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
                    or data.get("matches") or data.get("items") or [])
        return data if isinstance(data, list) else []

    matches = extract_matches_from_protobuf(content)
    print(f"[DEBUG] Parsed: {len(matches)} matches", file=sys.stderr, flush=True)
    for m in matches[:5]:
        print(f"  - {m['name']} (id={m['id']}, len={m['raw_len']})",
              file=sys.stderr, flush=True)
    return matches


def get_match_detail(md5, match_id, sport_type):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)
    if fmt == "json":
        return data
    return content


def extract_stream_url(data):
    """Extract stream URL from JSON or protobuf bytes."""
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
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"Found {len(matches)} matches", flush=True)

    if not matches:
        print("No live matches found.", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    entries = []

    def fetch_one(match):
        name = match["name"]
        match_id = match.get("id")

        if not match_id:
            print(f"[WARN] No ID: {name}", file=sys.stderr, flush=True)
            return (name, None)

        # Fetch detail
        detail = get_match_detail(md5, match_id, sport_type=SPORT_TYPE)
        stream_url = extract_stream_url(detail)
        if stream_url:
            return (name, stream_url)

        # Try Live API
        stream_url = get_stream_from_live_api(md5, match_id, SPORT_TYPE)
        return (name, stream_url)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_one, m) for m in matches]
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
