#!/usr/bin/env python3
"""
RBTV Playlist Generator (v15 FIXED — GitHub Actions)
Alur: signing key → match list → match detail (streamId) → stream detail (M3U8 URL)
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

try:
    from Crypto.Cipher import AES
    HAS_AES = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES
        HAS_AES = True
    except ImportError:
        HAS_AES = False

# ==================== CONFIG ====================

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
PORT = 8080
MAX_MATCHES = 30
TIMEOUT = 10
SPORT_TYPE = 0      # 0 = all sports
LANGUAGE = 34       # Indonesian
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
    "accept-language": "id-ID,id;q=0.6",
    "accept-encoding": "identity",
}


# ==================== PROTOBUF PARSER ====================

def read_varint(data, i):
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
        if wire_type == 0:
            val, i = read_varint(data, i)
            if val is None:
                break
            fields.append((field_num, 0, val))
        elif wire_type == 1:
            if i + 8 > end:
                break
            fields.append((field_num, 1, data[i:i+8]))
            i += 8
        elif wire_type == 2:
            length, i = read_varint(data, i)
            if length is None or length < 0 or i + length > end:
                break
            fields.append((field_num, 2, data[i:i+length]))
            i += length
        elif wire_type == 5:
            if i + 4 > end:
                break
            fields.append((field_num, 5, data[i:i+4]))
            i += 4
        else:
            break
    return fields


def find_vs_name(data):
    m = re.search(rb'[\x20-\x7e]{2,} vs [\x20-\x7e]{2,}', data)
    if m:
        name = m.group().decode('utf-8', errors='replace').strip()
        name = re.sub(r'^[^a-zA-Z\u00c0-\u024f]+', '', name)
        return name
    return None


def extract_matches_from_protobuf(data):
    """Parse /match/live protobuf → list of {name, id, sport_type}"""
    top_fields = parse_message(data)

    field10_data = None
    for fnum, wtype, val in top_fields:
        if fnum == 10 and wtype == 2 and len(val) > 500:
            field10_data = val
            break
    if not field10_data:
        candidates = [(fnum, val) for fnum, wtype, val in top_fields
                      if wtype == 2 and len(val) > 500]
        if candidates:
            candidates.sort(key=lambda x: len(x[1]), reverse=True)
            field10_data = candidates[0][1]
    if not field10_data:
        return []

    inner_fields = parse_message(field10_data)
    matches = []
    for fnum, wtype, val in inner_fields:
        if wtype != 2 or len(val) < 20:
            continue
        match_fields = parse_message(val)
        match_id = None
        sport_type = None
        for mf_num, mf_wire, mf_val in match_fields:
            if mf_wire == 0:
                if mf_num == 1 and mf_val > 1000 and match_id is None:
                    match_id = mf_val
                elif mf_num == 2 and mf_val > 0 and mf_val < 100:
                    sport_type = mf_val
        name = find_vs_name(val)
        if name and match_id:
            matches.append({
                "name": name,
                "id": match_id,
                "sport_type": sport_type or SPORT_TYPE,
            })
    return matches


# ==================== FIXED: RECURSIVE STREAM ID ====================

def extract_stream_id(data, _depth=0):
    """
    Recursively search for streamId (varint in range 10000–9999999).
    Handles JSON dicts and nested protobuf messages up to 5 levels deep.
    """
    if _depth > 5:
        return None

    # --- JSON path ---
    if isinstance(data, dict):
        # Direct key match
        if "streamId" in data and isinstance(data["streamId"], int):
            return data["streamId"]
        for v in data.values():
            result = extract_stream_id(v, _depth + 1)
            if result:
                return result
        return None
    elif isinstance(data, list):
        for item in data:
            result = extract_stream_id(item, _depth + 1)
            if result:
                return result
        return None

    # --- Protobuf path ---
    if not isinstance(data, bytes):
        return None

    fields = parse_message(data)
    candidates = []
    for fnum, wtype, val in fields:
        if wtype == 0 and 10000 < val < 9999999:
            candidates.append(val)
        elif wtype == 2 and len(val) > 10:
            sub = extract_stream_id(val, _depth + 1)
            if sub:
                candidates.append(sub)

    if candidates:
        return candidates[0]
    return None


# ==================== FIXED: M3U8 URL EXTRACTION ====================

def rot47_decode(s):
    """ROT47 decode: shift each char in range 33-119 by 47."""
    result = []
    for c in s:
        code = ord(c)
        if 33 <= code <= 119:
            code = (code - 33 + 47) % 87 + 33
        result.append(chr(code))
    return ''.join(result)


def try_aes_decrypt(data, key, iv):
    """Try AES-256-CBC decrypt. Returns bytes or None."""
    if not HAS_AES:
        return None
    try:
        if len(data) % 16 != 0:
            # PKCS7 padding
            data = data + bytes([16 - (len(data) % 16)]) * (16 - (len(data) % 16))
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(data)
        # Remove PKCS7 padding
        pad_len = decrypted[-1]
        if 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]
        return decrypted
    except Exception:
        return None


def extract_m3u8_url(data, rb_session=""):
    """
    Extract M3U8 URL from stream detail response.
    Handles: plain URL, ROT47-encoded, AES-encrypted.
    """
    # --- Plain URL search ---
    if isinstance(data, bytes):
        m = re.search(rb'(https?://[^\x00-\x1f\x7f-\xff]+\.m3u8[^\x00-\x1f\x7f-\xff]*)', data)
        if m:
            return m.group().decode()
        m = re.search(rb'(https?://[^\x00-\x1f\x7f-\xff]+/cfall/[^\x00-\x1f\x7f-\xff]+)', data)
        if m:
            return m.group().decode()

        # --- ROT47 search ---
        # Look for base64-like or ROT47-like strings
        for m in re.finditer(rb'[\x21-\x7f]{30,}', data):
            candidate = m.group().decode('ascii', errors='ignore')
            decoded = rot47_decode(candidate)
            if '.m3u8' in decoded:
                return decoded.strip()

        # --- AES decrypt using rb-session ---
        if rb_session and HAS_AES:
            try:
                # Try rb-session as key (32 bytes) or derive from it
                key = rb_session.encode('utf-8')[:32].ljust(32, b'\x00')
                iv = b'\x00' * 16
                decrypted = try_aes_decrypt(data, key, iv)
                if decrypted:
                    m = re.search(rb'(https?://[^\x00-\x1f\x7f-\xff]+\.m3u8[^\x00-\x1f\x7f-\xff]*)', decrypted)
                    if m:
                        return m.group().decode()
            except Exception:
                pass

    # --- JSON path ---
    elif isinstance(data, (dict, list)):
        json_str = json.dumps(data)
        m = re.search(r'(https?://[^"\s\\]+\.m3u8[^"\s\\]*)', json_str)
        if m:
            return m.group(1)
        m = re.search(r'(https?://[^"\s\\]+/cfall/[^"\s\\]+)', json_str)
        if m:
            return m.group(1)

        # ROT47 on string values
        def search_rot47(obj):
            if isinstance(obj, str) and len(obj) > 20:
                decoded = rot47_decode(obj)
                if '.m3u8' in decoded:
                    return decoded.strip()
            elif isinstance(obj, dict):
                for v in obj.values():
                    r = search_rot47(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for item in obj:
                    r = search_rot47(item)
                    if r:
                        return r
            return None
        return search_rot47(data)

    return None


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


def try_json_or_protobuf(content):
    try:
        text = content.decode('utf-8')
        if text.strip().startswith('{') or text.strip().startswith('['):
            return ('json', json.loads(text))
    except Exception:
        pass
    return ('protobuf', content)


# ==================== API ====================

def get_signing_key(match_id=None, sport_type=None):
    """
    Get signing key.
    - Live list:   /api/common/bs?code=100&sportType=Y
    - Match detail: /api/common/bs?code=102&code=103&code=104&code=105&matchId=X&sportType=Y
    """
    if match_id:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=102&code=103&code=104&code=105"
               f"&matchId={match_id}&sportType={sport_type}&stream=true")
    else:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=100&sportType={sport_type or 0}&stream=true")

    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)

    m = re.search(rb'[a-f0-9]{32}', content)
    if m:
        return m.group().decode()
    return None


def get_live_matches(md5):
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={SPORT_TYPE}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    if fmt == "json":
        if isinstance(data, dict):
            return (data.get("data") or data.get("list")
                    or data.get("matches") or data.get("items") or [])
        return data if isinstance(data, list) else []

    return extract_matches_from_protobuf(content)


def get_match_detail(match_id, sport_type):
    """
    FIXED: Get a DEDICATED signing key (code=102) for match detail.
    """
    detail_md5 = get_signing_key(match_id=match_id, sport_type=sport_type)
    if not detail_md5:
        print(f"  [WARN] No detail key for match {match_id}",
              file=sys.stderr, flush=True)
        return None

    url = (f"{DATA_URL}/sfver{detail_md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}"
           f"&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)
    return data


def get_stream_detail(stream_id, match_id, sport_type):
    """
    Get stream detail. Returns (data, rb_session_header).
    """
    url = (f"{DATA_URL}/api/stream/detail"
           f"?streamId={stream_id}"
           f"&siteType={SITE_TYPE}"
           f"&continent={CONTINENT}"
           f"&country={COUNTRY}"
           f"&digit={DIGIT}"
           f"&matchId={match_id}"
           f"&sportType={sport_type}")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    # Capture rb-session from response headers
    rb_session = resp.headers.get("rb-session", "")

    return data, rb_session


# ==================== MAIN ====================

def main():
    t0 = time.time()

    # Step 1: Get signing key for live list (code=100)
    print("Step 1: Getting signing key (code=100)...", flush=True)
    md5 = get_signing_key(sport_type=SPORT_TYPE)
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)
    print(f"  Key: {md5}", flush=True)

    # Step 2: Get live matches
    print("Step 2: Getting live matches...", flush=True)
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"  Found {len(matches)} matches", flush=True)

    if not matches:
        print("  No live matches.", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    for m in matches[:5]:
        print(f"    - {m['name']} (id={m['id']}, sport={m['sport_type']})", flush=True)

    # Step 3: For each match → get streamId → get M3U8 URL
    print("Step 3: Fetching stream URLs...", flush=True)
    entries = []

    def fetch_stream(match):
        name = match["name"]
        match_id = match["id"]
        sport_type = match["sport_type"]

        # 3a: Get match detail (dedicated key code=102) → extract streamId
        detail = get_match_detail(match_id, sport_type)
        if detail is None:
            print(f"  [WARN] No detail for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        stream_id = extract_stream_id(detail)

        if not stream_id:
            print(f"  [WARN] No streamId for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        # 3b: Get stream detail → extract M3U8 URL
        stream_data, rb_session = get_stream_detail(stream_id, match_id, sport_type)
        m3u8_url = extract_m3u8_url(stream_data, rb_session)

        if not m3u8_url:
            # Debug: dump what we got
            if isinstance(stream_data, bytes):
                urls = re.findall(rb'https?://[^\x00-\x1f\x7f-\xff]{10,}', stream_data)
                print(f"  [WARN] No M3U8 for {name}. "
                      f"rb_session={rb_session[:20] if rb_session else 'none'} "
                      f"URLs found: {[u.decode()[:80] for u in urls[:3]]}",
                      file=sys.stderr, flush=True)
            elif isinstance(stream_data, dict):
                print(f"  [WARN] No M3U8 for {name}. "
                      f"Keys: {list(stream_data.keys())[:10]}",
                      file=sys.stderr, flush=True)
            return (name, None)

        return (name, m3u8_url)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_stream, m) for m in matches]
        for f in as_completed(futures, timeout=TIMEOUT * 6):
            try:
                result = f.result(timeout=TIMEOUT * 3)
                if result and result[1]:
                    entries.append(result)
                    print(f"  [OK] {result[0]} → {result[1][:60]}...", flush=True)
                elif result:
                    print(f"  [FAIL] {result[0]}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"  [ERROR] {e}", file=sys.stderr, flush=True)

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

    print(f"\n{'='*55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'='*55}", flush=True)


if __name__ == "__main__":
    main()   
