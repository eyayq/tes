#!/usr/bin/env python3
"""
RBTV Playlist Generator (v16 — GitHub Actions)
Alur: signing key → match list → match detail (streamId) → stream detail → 
      ROT47 decode → AES token → final m3u8 URL
"""

import requests
import json
import gzip
import re
import zlib
import sys
import time
import base64
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
    from Crypto.Util.Padding import pad
    HAS_AES = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Util.Padding import pad
        HAS_AES = True
    except ImportError:
        HAS_AES = False

# ==================== CONFIG ====================

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
PORT = 8080
MAX_MATCHES = 30
TIMEOUT = 10
SPORT_TYPE = 0
LANGUAGE = 34
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

# ==================== AES KEY MATERIAL ====================
# ⚠️  WAJIB: Ambil dari src/crypto/stream-token.ts di repo fctv33-stream-resolver
#    (sharoon7171/fctv33-stream-resolver atau fork-nya)
#    Key = 32 bytes (AES-256), IV = 16 bytes (CBC)
#
# Contoh format (GANTI dengan nilai asli dari source):
#   AES_KEY = bytes.fromhex("...")  # 32 bytes
#   AES_IV  = bytes.fromhex("...")  # 16 bytes
#
# Atau jika string:
#   AES_KEY = b"your-32-byte-key-here!!!!!".ljust(32, b'\x00')
#   AES_IV  = b"your-16-byte-iv!!!"

AES_KEY = None  # ← ISI DI SINI (32 bytes)
AES_IV = None   # ← ISI DI SINI (16 bytes)

DEBUG = True  # Set True untuk debug, False untuk produksi


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


# ==================== STREAM ID EXTRACTION ====================

def extract_stream_id(data, _depth=0):
    if _depth > 5:
        return None
    if isinstance(data, dict):
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


# ==================== CRYPTO ====================

def rot47_decode(s):
    """ROT47: shift chars 33-119 by 47 positions."""
    if isinstance(s, bytes):
        s = s.decode('ascii', errors='ignore')
    result = []
    for c in s:
        code = ord(c)
        if 33 <= code <= 119:
            code = (code - 33 + 47) % 87 + 33
        result.append(chr(code))
    return ''.join(result)


def rot47_encode(s):
    """ROT47 encode (inverse)."""
    if isinstance(s, bytes):
        s = s.decode('ascii', errors='ignore')
    result = []
    for c in s:
        code = ord(c)
        if 33 <= code <= 119:
            code = (code - 33 - 47) % 87 + 33
        result.append(chr(code))
    return ''.join(result)


def build_tokenized_url(obfuscated_url, rb_session):
    """
    Build the final tokenized m3u8 URL:
    1. ROT47-decode the obfuscated URL
    2. Drop first 8 characters (leading marker)
    3. AES-256-CBC encrypt the rb-session string
    4. Base64-encode the ciphertext
    5. Insert 'token-{base64}a' into the URL path
    """
    # Step 1: ROT47 decode
    decoded = rot47_decode(obfuscated_url)

    # Step 2: Drop first 8 characters (leading marker)
    decoded = decoded[8:]

    if DEBUG:
        print(f"    [DEBUG] After ROT47+strip: {decoded[:100]}", flush=True)

    # Step 3-4: AES encrypt session token
    if not HAS_AES or AES_KEY is None or AES_IV is None:
        print("    [ERROR] AES not available or key not set!", file=sys.stderr, flush=True)
        return None

    try:
        session_bytes = rb_session.encode('utf-8')
        padded = pad(session_bytes, 16)  # PKCS7 padding
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        ciphertext = cipher.encrypt(padded)
        token_b64 = base64.b64encode(ciphertext).decode('ascii')
    except Exception as e:
        print(f"    [ERROR] AES encrypt failed: {e}", file=sys.stderr, flush=True)
        return None

    # Step 5: Insert token into URL path
    # Pattern: https://.../token-{base64}a/...m3u8
    # The decoded URL likely has a placeholder or we insert after the host path
    # Common pattern: the URL has a path like /live/... and we insert token-...a/
    
    # Try to find where to insert the token
    # Usually: {base_url}/token-{token}a/{rest_of_path}
    if '/' in decoded:
        parts = decoded.split('/', 3)  # [scheme://host, '', rest]
        if len(parts) >= 3:
            # Insert token after the first path segment
            base = '/'.join(parts[:2])
            rest = parts[2] if len(parts) > 2 else ''
            tokenized_url = f"{base}/token-{token_b64}a/{rest}"
        else:
            tokenized_url = f"{decoded}/token-{token_b64}a"
    else:
        tokenized_url = f"{decoded}?token={token_b64}a"

    if DEBUG:
        print(f"    [DEBUG] Tokenized URL: {tokenized_url[:120]}", flush=True)

    return tokenized_url


def extract_obfuscated_url(data):
    """
    Extract the obfuscated URL string from stream detail response.
    The response contains a ROT47-encoded URL (not a plain HTTP URL).
    """
    if isinstance(data, bytes):
        # Look for the longest printable string (likely the obfuscated URL)
        # ROT47-encoded URLs will be printable ASCII (33-126 range)
        candidates = re.findall(rb'[\x21-\x7e]{20,}', data)
        if candidates:
            # Pick the longest one (most likely the URL)
            longest = max(candidates, key=len)
            return longest.decode('ascii')
        return None
    elif isinstance(data, (dict, list)):
        json_str = json.dumps(data)
        # Look for long string values
        m = re.findall(r'"([^"]{20,})"', json_str)
        if m:
            longest = max(m, key=len)
            return longest
        return None
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
    if match_id:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=102&code=103&code=104&code=105"
               f"&matchId={match_id}&sportType={sport_type}&stream=true")
    else:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=100&sportType={sport_type or 0}&stream=true")

    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)

    # Try hex MD5 first
    m = re.search(rb'[a-f0-9]{32}', content)
    if m:
        return m.group().decode()
    
    # Fallback: try any 32-char alphanumeric
    m = re.search(rb'[a-zA-Z0-9]{32}', content)
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


def get_match_detail(match_id, sport_type, fallback_md5):
    """
    Get match detail. Tries code=102 key first, falls back to code=100 key.
    """
    # Try dedicated key (code=102)
    detail_md5 = get_signing_key(match_id=match_id, sport_type=sport_type)
    
    if not detail_md5:
        # Fallback: use the live list key
        if DEBUG:
            print(f"    [DEBUG] code=102 key failed, using code=100 key", flush=True)
        detail_md5 = fallback_md5
    
    if not detail_md5:
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
    Get stream detail. Returns (data, rb_session, raw_content).
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

    rb_session = resp.headers.get("rb-session", "")

    if DEBUG:
        # Dump raw content for debugging
        if isinstance(content, bytes) and len(content) < 2000:
            printable = content.decode('utf-8', errors='replace')
            print(f"    [DEBUG] Raw stream detail ({len(content)} bytes): "
                  f"{printable[:200]}", flush=True)
        elif isinstance(data, dict):
            print(f"    [DEBUG] Stream detail JSON keys: {list(data.keys())}", flush=True)
            # Dump values that look like URLs or encoded strings
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 15:
                    print(f"    [DEBUG]   {k} = {v[:80]}...", flush=True)

    return data, rb_session, content


# ==================== MAIN ====================

def main():
    t0 = time.time()

    # Check AES key
    if not HAS_AES:
        print("WARNING: pycryptodome not installed. "
              "Run: pip install pycryptodome", file=sys.stderr, flush=True)
    if AES_KEY is None or AES_IV is None:
        print("WARNING: AES_KEY/AES_IV not set! "
              "Get them from fctv33-stream-resolver src/crypto/stream-token.ts",
              file=sys.stderr, flush=True)

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

    # Step 3: For each match → streamId → obfuscated URL → tokenized m3u8
    print("Step 3: Fetching stream URLs...", flush=True)
    entries = []

    def fetch_stream(match):
        name = match["name"]
        match_id = match["id"]
        sport_type = match["sport_type"]

        # 3a: Get match detail → extract streamId
        detail = get_match_detail(match_id, sport_type, md5)
        if detail is None:
            print(f"  [WARN] No detail for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        stream_id = extract_stream_id(detail)
        if not stream_id:
            print(f"  [WARN] No streamId for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        if DEBUG:
            print(f"    [DEBUG] {name}: streamId={stream_id}", flush=True)

        # 3b: Get stream detail → obfuscated URL + rb-session
        stream_data, rb_session, raw_content = get_stream_detail(
            stream_id, match_id, sport_type)

        if not rb_session:
            print(f"  [WARN] No rb-session for {name}", file=sys.stderr, flush=True)
            return (name, None)

        # 3c: Extract obfuscated URL from response
        obfuscated = None
        if isinstance(stream_data, bytes):
            obfuscated = extract_obfuscated_url(stream_data)
        elif isinstance(stream_data, dict):
            # Try common field names
            for key in ("url", "streamUrl", "playUrl", "hlsUrl", "src", "link"):
                if key in stream_data and isinstance(stream_data[key], str):
                    obfuscated = stream_data[key]
                    break
            if not obfuscated:
                obfuscated = extract_obfuscated_url(stream_data)

        if not obfuscated:
            print(f"  [WARN] No obfuscated URL for {name}. "
                  f"data_type={type(stream_data).__name__}",
                  file=sys.stderr, flush=True)
            # Debug dump
            if isinstance(stream_data, bytes):
                print(f"    [DEBUG] Content: {stream_data[:200]}",
                      file=sys.stderr, flush=True)
            return (name, None)

        if DEBUG:
            print(f"    [DEBUG] Obfuscated: {obfuscated[:80]}...", flush=True)

        # 3d: Build tokenized URL
        final_url = build_tokenized_url(obfuscated, rb_session)
        if not final_url:
            print(f"  [FAIL] Token build failed for {name}", file=sys.stderr, flush=True)
            return (name, None)

        return (name, final_url)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_stream, m) for m in matches]
        for f in as_completed(futures, timeout=TIMEOUT * 6):
            try:
                result = f.result(timeout=TIMEOUT * 3)
                if result and result[1]:
                    entries.append(result)
                    print(f"  [OK] {result[0]}", flush=True)
                    if DEBUG:
                        print(f"       → {result[1][:100]}", flush=True)
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
