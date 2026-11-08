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
AES_KEY = None  # ← ISI DI SINI (32 bytes)
AES_IV = None   # ← ISI DI SINI (16 bytes)

DEBUG = True
DEBUG_RAW = True  # Dump raw response (hex+ascii) untuk diagnosa


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


def dump_protobuf(data, indent=0, max_depth=4):
    """Dump protobuf structure for debugging."""
    if indent > max_depth:
        return
    prefix = "  " * indent
    fields = parse_message(data)
    if not fields:
        print(f"{prefix}(no valid protobuf fields, {len(data)} bytes)", flush=True)
        print(f"{prefix}raw: {data[:80]!r}", flush=True)
        return
    for fnum, wtype, val in fields:
        wtype_name = {0: "varint", 1: "64bit", 2: "bytes", 5: "32bit"}.get(wtype, f"wt{wtype}")
        if wtype == 0:
            print(f"{prefix}field {fnum} ({wtype_name}): {val}", flush=True)
        elif wtype == 2:
            try:
                text = val.decode('utf-8')
                if all(32 <= ord(c) < 127 or c in '\n\t' for c in text) and len(val) < 200:
                    print(f"{prefix}field {fnum} (string): {text!r}", flush=True)
                    continue
            except (UnicodeDecodeError, ValueError):
                pass
            print(f"{prefix}field {fnum} (bytes, {len(val)}B):", flush=True)
            sub_fields = parse_message(val)
            if sub_fields:
                dump_protobuf(val, indent + 1, max_depth)
            else:
                print(f"{prefix}  raw: {val[:60]!r}", flush=True)
        else:
            print(f"{prefix}field {fnum} ({wtype_name}): {val!r}", flush=True)


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

def extract_stream_id(data, match_id=None, _depth=0):
    """
    Extract the real streamId from match detail.
    streamId is in a nested stream sub-message, NOT the matchId.
    Reference: matchId=4334855 → streamId=819962
    """
    if _depth > 6:
        return None
    if isinstance(data, dict):
        # Direct JSON field
        if "streamId" in data and isinstance(data["streamId"], int):
            if data["streamId"] != match_id:
                return data["streamId"]
        # Also check nested "streams" or "channels" arrays
        for key in ("streams", "channels", "streamList", "items"):
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    if isinstance(item, dict) and "streamId" in item:
                        sid = item["streamId"]
                        if isinstance(sid, int) and sid != match_id:
                            return sid
        for v in data.values():
            result = extract_stream_id(v, match_id, _depth + 1)
            if result:
                return result
        return None
    elif isinstance(data, list):
        for item in data:
            result = extract_stream_id(item, match_id, _depth + 1)
            if result:
                return result
        return None
    if not isinstance(data, bytes):
        return None

    fields = parse_message(data)
    if not fields:
        return None

    # Strategy 1: Nested sub-messages that look like stream entries
    # Stream sub-message: field1=streamId(varint), possibly field2=siteType, field3=name
    for fnum, wtype, val in fields:
        if wtype == 2 and 5 < len(val) < 500:
            sub_fields = parse_message(val)
            if not sub_fields:
                continue
            # Check first varint in sub-message
            for sf_num, sf_wire, sf_val in sub_fields:
                if sf_wire == 0:
                    if sf_val != match_id and 100 < sf_val < 999999999:
                        # Additional check: sub-message should have multiple fields
                        # (a stream entry has at least streamId + something else)
                        if len(sub_fields) >= 2:
                            return sf_val
                    break

    # Strategy 2: Top-level varints (exclude matchId), prefer higher field numbers
    candidates = []
    for fnum, wtype, val in fields:
        if wtype == 0 and val != match_id and 100 < val < 999999999:
            candidates.append((fnum, val))
    if candidates:
        # Prefer field numbers that are NOT 1 (field 1 is usually matchId)
        non_one = [c for c in candidates if c[0] != 1]
        if non_one:
            return non_one[0][1]
        return candidates[0][1]

    return None


# ==================== CRYPTO ====================

def rot47_decode(s):
    if isinstance(s, bytes):
        s = s.decode('ascii', errors='ignore')
    result = []
    for c in s:
        code = ord(c)
        if 33 <= code <= 119:
            code = (code - 33 + 47) % 87 + 33
        result.append(chr(code))
    return ''.join(result)


def build_tokenized_url(obfuscated_url, rb_session):
    decoded = rot47_decode(obfuscated_url)
    decoded = decoded[8:]

    if DEBUG:
        print(f"    [DEBUG] After ROT47+strip: {decoded[:100]}", flush=True)

    if not HAS_AES or AES_KEY is None or AES_IV is None:
        print("    [ERROR] AES not available or key not set!", file=sys.stderr, flush=True)
        return None

    try:
        session_bytes = rb_session.encode('utf-8')
        padded = pad(session_bytes, 16)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        ciphertext = cipher.encrypt(padded)
        token_b64 = base64.b64encode(ciphertext).decode('ascii')
    except Exception as e:
        print(f"    [ERROR] AES encrypt failed: {e}", file=sys.stderr, flush=True)
        return None

    if '/' in decoded:
        parts = decoded.split('/', 3)
        if len(parts) >= 3:
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
    if isinstance(data, bytes):
        candidates = re.findall(rb'[\x21-\x7e]{20,}', data)
        if candidates:
            longest = max(candidates, key=len)
            return longest.decode('ascii')
        return None
    elif isinstance(data, (dict, list)):
        json_str = json.dumps(data)
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
        stripped = text.strip()
        if stripped.startswith('{') or stripped.startswith('['):
            return ('json', json.loads(text))
    except Exception:
        pass
    return ('protobuf', content)


def raw_debug(label, resp, content):
    """Print raw response info for debugging."""
    if not DEBUG_RAW:
        return
    print(f"    [RAW] {label}: status={resp.status_code}, "
          f"len={len(content)}, enc={resp.headers.get('content-encoding','none')}",
          flush=True)
    if content:
        # Hex dump first 60 bytes
        hex_str = ' '.join(f'{b:02x}' for b in content[:60])
        print(f"    [RAW]   hex: {hex_str}", flush=True)
        # ASCII printable
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in content[:80])
        print(f"    [RAW]   asc: {ascii_str}", flush=True)


# ==================== API ====================

def get_signing_key(match_id=None, sport_type=None):
    """
    Get signing key from /api/common/bs.
    Reference: Live list uses code 0x64 (100); match detail uses code 0x66 (102).
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

    if DEBUG_RAW:
        raw_debug(f"signing_key({'detail' if match_id else 'live'})", resp, content)

    # Try hex MD5 (32 hex chars)
    m = re.search(rb'[a-f0-9]{32}', content)
    if m:
        return m.group().decode()

    # Try any 32-char alphanumeric
    m = re.search(rb'[a-zA-Z0-9]{32}', content)
    if m:
        return m.group().decode()

    # Try 6-char prefix (some implementations use short prefix)
    m = re.search(rb'[a-f0-9]{6}', content)
    if m:
        if DEBUG:
            print(f"    [DEBUG] Only found 6-char key: {m.group().decode()}", flush=True)
        return m.group().decode()

    # Last resort: any hex string >= 6 chars
    m = re.search(rb'[a-f0-9]{6,}', content)
    if m:
        return m.group().decode()

    return None


def get_live_matches(md5):
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={SPORT_TYPE}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)

    if DEBUG_RAW:
        raw_debug("live_matches", resp, content)

    fmt, data = try_json_or_protobuf(content)

    if fmt == "json":
        if isinstance(data, dict):
            return (data.get("data") or data.get("list")
                    or data.get("matches") or data.get("items") or [])
        return data if isinstance(data, list) else []
    return extract_matches_from_protobuf(content)


def get_match_detail(match_id, sport_type, fallback_md5):
    """
    Get match detail.
    Reference: match detail uses code 0x66 (102), path /sfver{key}/api/match/detail
    Returns (data, raw_content) so caller can debug.
    """
    detail_md5 = get_signing_key(match_id=match_id, sport_type=sport_type)

    if not detail_md5:
        if DEBUG:
            print(f"    [DEBUG] code=102 key failed, using code=100 key", flush=True)
        detail_md5 = fallback_md5

    if not detail_md5:
        return None, None

    url = (f"{DATA_URL}/sfver{detail_md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}"
           f"&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)

    if DEBUG_RAW:
        raw_debug(f"match_detail(id={match_id})", resp, content)

    fmt, data = try_json_or_protobuf(content)

    # If it's protobuf but parse_message returns nothing, it's likely an error
    if fmt == "protobuf" and isinstance(data, bytes):
        fields = parse_message(data)
        if not fields and len(data) < 100:
            # Probably an error message, not real protobuf
            err_text = data.decode('utf-8', errors='replace').strip()
            if DEBUG:
                print(f"    [DEBUG] Match detail returned non-protobuf error: "
                      f"{err_text!r} (status={resp.status_code})", flush=True)
            return None, content

    return data, content


def get_stream_detail(stream_id, match_id, sport_type):
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

    if DEBUG_RAW:
        raw_debug(f"stream_detail(id={stream_id})", resp, content)

    fmt, data = try_json_or_protobuf(content)
    rb_session = resp.headers.get("rb-session", "")

    if DEBUG:
        if isinstance(data, dict):
            print(f"    [DEBUG] Stream detail JSON keys: {list(data.keys())}", flush=True)
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 15:
                    print(f"    [DEBUG]   {k} = {v[:80]}...", flush=True)
        elif isinstance(content, bytes) and len(content) < 2000:
            printable = content.decode('utf-8', errors='replace')
            print(f"    [DEBUG] Raw stream detail: {printable[:200]}", flush=True)

    return data, rb_session, content


# ==================== MAIN ====================

def main():
    t0 = time.time()

    if not HAS_AES:
        print("WARNING: pycryptodome not installed. "
              "Run: pip install pycryptodome", file=sys.stderr, flush=True)
    if AES_KEY is None or AES_IV is None:
        print("WARNING: AES_KEY/AES_IV not set!", file=sys.stderr, flush=True)

    # Step 1: Get signing key for live list (code=100)
    print("Step 1: Getting signing key (code=100)...", flush=True)
    md5 = get_signing_key(sport_type=SPORT_TYPE)
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(1)
    print(f"  Key: {md5} (len={len(md5)})", flush=True)

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
        detail, raw_content = get_match_detail(match_id, sport_type, md5)
        if detail is None:
            print(f"  [WARN] No detail for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        # DEBUG: dump structure
        if DEBUG and isinstance(detail, bytes):
            print(f"    [DEBUG] === Protobuf dump for {name} ({len(detail)}B) ===",
                  flush=True)
            dump_protobuf(detail)
            print(f"    [DEBUG] === End dump ===", flush=True)
        elif DEBUG and isinstance(detail, dict):
            print(f"    [DEBUG] === JSON keys for {name}: {list(detail.keys())} ===",
                  flush=True)
            # Print stream-related keys
            for k, v in detail.items():
                if "stream" in k.lower() or "channel" in k.lower():
                    print(f"    [DEBUG]   {k} = {json.dumps(v, ensure_ascii=False)[:200]}",
                          flush=True)

        stream_id = extract_stream_id(detail, match_id=match_id)
        if not stream_id:
            print(f"  [WARN] No streamId for {name} (id={match_id})",
                  file=sys.stderr, flush=True)
            return (name, None)

        if DEBUG:
            print(f"    [DEBUG] {name}: streamId={stream_id} (matchId={match_id})",
                  flush=True)

        # 3b: Get stream detail → obfuscated URL + rb-session
        stream_data, rb_session, raw_content = get_stream_detail(
            stream_id, match_id, sport_type)

        if not rb_session:
            print(f"  [WARN] No rb-session for {name}", file=sys.stderr, flush=True)
            return (name, None)

        # 3c: Extract obfuscated URL
        obfuscated = None
        if isinstance(stream_data, bytes):
            obfuscated = extract_obfuscated_url(stream_data)
        elif isinstance(stream_data, dict):
            for key in ("url", "streamUrl", "playUrl", "hlsUrl", "src", "link",
                        "obfuscatedUrl", "encodedUrl"):
                if key in stream_data and isinstance(stream_data[key], str):
                    obfuscated = stream_data[key]
                    break
            if not obfuscated:
                # Try nested
                for k, v in stream_data.items():
                    if isinstance(v, dict):
                        for key2 in ("url", "streamUrl", "playUrl", "hlsUrl"):
                            if key2 in v and isinstance(v[key2], str):
                                obfuscated = v[key2]
                                break
                    if obfuscated:
                        break
            if not obfuscated:
                obfuscated = extract_obfuscated_url(stream_data)

        if not obfuscated:
            print(f"  [WARN] No obfuscated URL for {name}. "
                  f"data_type={type(stream_data).__name__}",
                  file=sys.stderr, flush=True)
            if isinstance(stream_data, bytes):
                print(f"    [DEBUG] Content: {stream_data[:200]!r}",
                      file=sys.stderr, flush=True)
            elif isinstance(stream_data, dict):
                print(f"    [DEBUG] JSON: {json.dumps(stream_data, ensure_ascii=False)[:300]}",
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
