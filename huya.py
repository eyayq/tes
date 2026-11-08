#!/usr/bin/env python3
"""
RBTV Playlist Generator (v10 FINAL — GitHub Actions)
API response = PROTOBUF → parse structure → fetch detail per match → extract stream URL
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
    """Read varint. Returns (value, new_i)."""
    result = 0
    shift = 0
    while i < len(data):
        byte = data[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not (byte & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            return None, i
    return None, i


def parse_fields(data, offset=0, end=None):
    """
    Parse protobuf fields. Returns list of (field_num, wire_type, value).
    wire_type: 0=varint, 1=64bit, 2=length-delimited, 5=32bit
    value: int (varint), bytes (length-delimited/64bit/32bit)
    """
    if end is None:
        end = len(data)
    fields = []
    i = offset
    while i < end:
        tag, i = read_varint(data, i)
        if tag is None or i >= end:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07

        if field_num == 0:  # invalid
            break

        if wire_type == 0:  # varint
            value, i = read_varint(data, i)
            if value is None:
                break
            fields.append((field_num, 0, value))
        elif wire_type == 1:  # 64-bit
            if i + 8 > end:
                break
            fields.append((field_num, 1, data[i:i + 8]))
            i += 8
        elif wire_type == 2:  # length-delimited
            length, i = read_varint(data, i)
            if length is None or length < 0 or i + length > end:
                break
            fields.append((field_num, 2, data[i:i + length]))
            i += length
        elif wire_type == 5:  # 32-bit
            if i + 4 > end:
                break
            fields.append((field_num, 5, data[i:i + 4]))
            i += 4
        else:
            break

    return fields


def extract_strings_recursive(data, depth=0, max_depth=5):
    """Extract all readable strings from nested protobuf."""
    if depth > max_depth:
        return []
    strings = []
    fields = parse_fields(data)
    for field_num, wire_type, value in fields:
        if wire_type == 2:  # length-delimited (string or nested message)
            try:
                s = value.decode('utf-8')
                if all(32 <= ord(c) < 127 or ord(c) > 0x200 for c in s):
                    if len(s) >= 2 and not s.startswith('\x00'):
                        strings.append(s)
                        continue
            except (UnicodeDecodeError, ValueError):
                pass
            # Try as nested message
            strings.extend(extract_strings_recursive(value, depth + 1, max_depth))
    return strings


def extract_match_entries(data):
    """
    Parse protobuf to extract match entries with IDs and names.
    Strategy: find the large repeated field (field 10), parse each sub-message.
    """
    top_fields = parse_fields(data)

    # Find the large length-delimited field that contains matches
    matches_field = None
    for field_num, wire_type, value in top_fields:
        if wire_type == 2 and len(value) > 1000:
            matches_field = value
            break

    if not matches_field:
        # Fallback: try all large fields
        for field_num, wire_type, value in top_fields:
            if wire_type == 2 and len(value) > 100:
                matches_field = value
                break

    if not matches_field:
        return []

    # Parse sub-messages inside the matches field
    sub_fields = parse_fields(matches_field)
    matches = []

    for field_num, wire_type, value in sub_fields:
        if wire_type != 2 or len(value) < 10:
            continue

        # Parse each sub-message as a match entry
        entry_fields = parse_fields(value)
        match_data = {"strings": [], "integers": [], "urls": []}

        for f_num, f_wire, f_val in entry_fields:
            if f_wire == 0:  # varint (integer)
                if f_val > 100:  # skip small values (flags, types, etc.)
                    match_data["integers"].append(f_val)
            elif f_wire == 2:  # string or nested
                try:
                    s = f_val.decode('utf-8')
                    if all(32 <= ord(c) < 127 or ord(c) > 0x200 for c in s):
                        if 'vs' in s or 'VS' in s:
                            match_data["strings"].append(("name", s))
                        elif s.startswith('http'):
                            match_data["urls"].append(s)
                        elif len(s) >= 2:
                            match_data["strings"].append(("other", s))
                except (UnicodeDecodeError, ValueError):
                    # Nested message — recurse for URLs
                    nested_urls = re.findall(
                        rb'(https?://[^\x00-\x1f\x7f-\xff]{10,})', f_val)
                    for u in nested_urls:
                        match_data["urls"].append(u.decode())

        # Only keep entries that have a "name" (contains "vs")
        names = [s for tag, s in match_data["strings"] if tag == "name"]
        if names:
            # Pick the most likely match ID: largest integer in reasonable range
            candidate_ids = [x for x in match_data["integers"] if 10000 < x < 999999999]
            match_id = max(candidate_ids) if candidate_ids else None
            matches.append({
                "name": names[0],
                "id": match_id,
                "all_ints": match_data["integers"],
                "urls": match_data["urls"],
            })

    return matches


# ==================== DECODE ====================

def decode_api_response(resp) -> bytes:
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()

    if not content:
        return b""

    if enc == "gzip" or content[:2] == b'\x1f\x8b':
        try:
            content = gzip.decompress(content)
        except Exception:
            pass
    elif enc == "br" and HAS_BROTLI:
        try:
            content = brotli.decompress(content)
        except Exception:
            pass
    elif enc == "deflate":
        try:
            content = zlib.decompress(content)
        except Exception:
            try:
                content = zlib.decompress(content, -zlib.MAX_WBITS)
            except Exception:
                pass
    elif enc == "zstd" and HAS_ZSTD:
        try:
            content = zstd.ZstdDecompressor().decompress(content)
        except Exception:
            pass

    return content


def try_json_or_protobuf(content: bytes):
    """Returns ('json', data) or ('protobuf', bytes)."""
    try:
        text = content.decode('utf-8')
        if text.strip().startswith('{') or text.strip().startswith('['):
            return ('json', json.loads(text))
    except Exception:
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

    # Protobuf: parse structure
    matches = extract_match_entries(content)
    print(f"[DEBUG] Protobuf parsed: {len(matches)} match entries",
          file=sys.stderr, flush=True)
    if matches:
        for m in matches[:5]:
            print(f"  - {m['name']} (id={m['id']}, urls={len(m['urls'])})",
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
    return content  # return raw bytes for URL extraction


def extract_stream_url(data) -> str | None:
    """Extract stream URL from JSON dict or protobuf bytes."""
    if isinstance(data, (dict, list)):
        json_str = json.dumps(data)
        patterns = [
            rb'(https?://[^"\s]+/cfall/[^"\s]+/super/[^"\s]+)',
            rb'(https?://[^"\s]+\.m3u8[^"\s]*)',
            rb'(https?://[^"\s]+/super/[^"\s]+)',
        ]
        for p in patterns:
            m = re.search(p, json_str.encode())
            if m:
                return m.group(1).decode()
        m = re.search(
            r'"(?:streamUrl|playUrl|hlsUrl|videoUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"',
            json_str)
        if m:
            return m.group(1)
        return None

    # Protobuf bytes: search for URLs
    if isinstance(data, bytes):
        patterns = [
            rb'(https?://[^\x00-\x1f\x7f-\xff]+/cfall/[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+)',
            rb'(https?://[^\x00-\x1f\x7f-\xff]+\.m3u8[^\x00-\x1f\x7f-\xff]*)',
            rb'(https?://[^\x00-\x1f\x7f-\xff]+/super/[^\x00-\x1f\x7f-\xff]+)',
        ]
        for p in patterns:
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
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = [pool.submit(try_url, u) for u in endpoints]
        for f in as_completed(futures, timeout=12):
            try:
                result = f.result(timeout=3)
                if result:
                    return result
            except Exception:
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
        print("No live matches found. Writing empty playlist.",
              file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    entries = []

    def fetch_one(match):
        name = match.get("name", "Unknown")
        match_id = match.get("id")

        # 1) Check if URL already in the live response
        for url in match.get("urls", []):
            if '/cfall/' in url or '/super/' in url or '.m3u8' in url:
                return (name, url)

        # 2) Fetch detail
        if match_id:
            detail = get_match_detail(md5, match_id, sport_type=SPORT_TYPE)
            stream_url = extract_stream_url(detail)
            if stream_url:
                return (name, stream_url)

            # 3) Try Live API
            stream_url = get_stream_from_live_api(md5, match_id, SPORT_TYPE)
            if stream_url:
                return (name, stream_url)

        # 4) No ID — try Live API without ID (unlikely to work)
        return (name, None)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_one, m)
                   for m in matches if isinstance(m, dict)]
        for f in as_completed(futures, timeout=TIMEOUT * 4):
            try:
                result = f.result(timeout=TIMEOUT * 2)
                if result and result[1]:
                    entries.append(result)
                elif result:
                    print(f"[WARN] No stream URL for: {result[0]}",
                          file=sys.stderr, flush=True)
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

    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    print(f"\n{'=' * 55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'=' * 55}", flush=True)


if __name__ == "__main__":
    main()   
