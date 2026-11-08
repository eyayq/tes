import requests
import json
import gzip
import re
import zlib
import sys
import time
import base64
import random
from urllib.parse import urlparse, quote

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
    from Crypto.Util.Padding import pad, unpad
    HAS_AES = True
except ImportError:
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Util.Padding import pad, unpad
        HAS_AES = True
    except ImportError:
        HAS_AES = False

# ==================== CONFIG ====================

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
PORT = 8080
MAX_MATCHES = 30
TIMEOUT = 15
SPORT_TYPE = 0
LANGUAGE = 34
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"

REQUEST_DELAY = (1.5, 3.0)
MAX_RETRIES = 3
RETRY_BACKOFF = 5

HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-language": "id-ID,id;q=0.6,en-US;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}

# ==================== SPORT TYPE MAPPING ====================

SPORT_NAMES = {
    0: "All Sports",
    1: "Football",
    2: "Basketball",
    3: "Tennis",
    4: "Baseball",
    6: "Cricket",
    7: "Motorsport",
    8: "Rugby",
    9: "American Football",
    10: "Aussie Rules",
    11: "Hockey",
    12: "Badminton",
    13: "Volleyball",
    14: "Fighting",
    15: "Cycling",
    16: "Handball",
    90: "Others",
}

# ==================== AES KEY MATERIAL ====================
AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"  # 32 bytes
AES_IV = b"8017d3a8f1400d2f"  # 16 bytes

DEBUG = True

session = requests.Session()
session.headers.update(HEADERS)


# ==================== RATE-LIMITED REQUEST ====================

def safe_get(url, **kwargs):
    for attempt in range(MAX_RETRIES):
        delay = random.uniform(*REQUEST_DELAY)
        if attempt > 0:
            delay += RETRY_BACKOFF * attempt
        time.sleep(delay)

        try:
            resp = session.get(url, timeout=TIMEOUT, **kwargs)
        except requests.exceptions.RequestException as e:
            print(f"    [RETRY] Network error: {e}", flush=True)
            continue

        if resp.status_code == 429:
            wait = RETRY_BACKOFF * (attempt + 1)
            print(f"    [429] Rate limited, waiting {wait}s "
                  f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
            time.sleep(wait)
            continue

        if resp.status_code == 503:
            wait = RETRY_BACKOFF * (attempt + 1) * 2
            print(f"    [503] Cloudflare challenge, waiting {wait}s", flush=True)
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            print(f"    [HTTP {resp.status_code}] {url[:80]}", flush=True)
            return None

        return resp

    print(f"    [GIVE UP] {url[:80]} after {MAX_RETRIES} attempts", flush=True)
    return None


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
                if all(32 <= ord(c) < 127 or c in '\n\t' for c in text) and len(val) < 300:
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


# ==================== LOGO EXTRACTION ====================

def extract_logo_from_protobuf(data):
    if not isinstance(data, bytes):
        return None

    def _find_url(fields, depth=0):
        if depth > 5:
            return None
        for fnum, wtype, val in fields:
            if wtype == 2:
                try:
                    text = val.decode('utf-8')
                    if (text.startswith('http') and
                            re.search(r'\.(png|jpg|jpeg|webp|svg|gif)', text, re.I)):
                        return text
                    if (text.startswith('http') and
                            re.search(r'(logo|icon|team|badge|crest)', text, re.I)):
                        return text
                except (UnicodeDecodeError, ValueError):
                    pass
                sub_fields = parse_message(val)
                if sub_fields:
                    result = _find_url(sub_fields, depth + 1)
                    if result:
                        return result
        return None

    return _find_url(parse_message(data))


# ==================== STREAM ID EXTRACTION ====================

def extract_stream_id(data, match_id=None, _depth=0):
    if _depth > 6:
        return None
    if isinstance(data, dict):
        if "streamId" in data and isinstance(data["streamId"], int):
            if data["streamId"] != match_id:
                return data["streamId"]
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

    container = None
    for fnum, wtype, val in fields:
        if wtype == 2 and len(val) > 50:
            if fnum == 10:
                container = val
                break
            if container is None or len(val) > len(container):
                container = val

    if container:
        container_fields = parse_message(container)
        for fnum, wtype, val in container_fields:
            if wtype == 2 and fnum == 2 and 10 < len(val) < 200:
                stream_fields = parse_message(val)
                stream_id = None
                for sf_num, sf_wire, sf_val in stream_fields:
                    if sf_wire == 0 and sf_num == 1:
                        stream_id = sf_val
                if stream_id and stream_id != match_id and 100 < stream_id < 999999999:
                    return stream_id

    for fnum, wtype, val in fields:
        if wtype == 2 and 5 < len(val) < 2000:
            sub_fields = parse_message(val)
            if not sub_fields:
                continue
            for sf_num, sf_wire, sf_val in sub_fields:
                if sf_wire == 0 and sf_num == 1:
                    if sf_val != match_id and 100 < sf_val < 999999999:
                        if len(sub_fields) >= 3:
                            return sf_val
                    break
            for sf_num, sf_wire, sf_val in sub_fields:
                if sf_wire == 2 and sf_num == 2 and 10 < len(sf_val) < 200:
                    entry_fields = parse_message(sf_val)
                    for ef_num, ef_wire, ef_val in entry_fields:
                        if ef_wire == 0 and ef_num == 1:
                            if ef_val != match_id and 100 < ef_val < 999999999:
                                return ef_val
                            break

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


def aes_decrypt(data):
    """AES-256-CBC decrypt with PKCS7 unpadding."""
    if not HAS_AES or len(data) < 16 or len(data) % 16 != 0:
        return None
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        plaintext = unpad(cipher.decrypt(data), 16)
        return plaintext
    except Exception:
        return None


def aes_encrypt(data):
    """AES-256-CBC encrypt with PKCS7 padding."""
    if not HAS_AES:
        return None
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        return cipher.encrypt(pad(data, 16))
    except Exception:
        return None


def build_tokenized_url(plain_url, rb_session):
    """Build final tokenized m3u8 URL from plain URL + session."""
    if not (plain_url.startswith('http://') or plain_url.startswith('https://')):
        print(f"    [ERROR] Not a valid URL: {plain_url[:60]!r}", file=sys.stderr, flush=True)
        return None

    encrypted = aes_encrypt(rb_session.encode('utf-8'))
    if not encrypted:
        print("    [ERROR] AES encrypt failed!", file=sys.stderr, flush=True)
        return None

    token_b64 = base64.b64encode(encrypted).decode('ascii')
    token = quote(token_b64, safe='') + 'a'

    parsed = urlparse(plain_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    pathname = parsed.path or ''
    search = ('?' + parsed.query) if parsed.query else ''

    final_url = f"{origin}/token-{token}{pathname}{search}"

    if DEBUG:
        print(f"    [DEBUG] Final URL: {final_url[:120]}", flush=True)

    return final_url


def extract_obfuscated_url(data):
    """
    Extract plain URL from stream detail protobuf.
    URL is stored as AES-256-CBC ciphertext in a protobuf field.
    Steps: find bytes fields (multiple of 16) → AES decrypt → ROT47 decode → plain URL
    """
    if isinstance(data, bytes):
        candidates = []

        def _collect(d, depth=0):
            if depth > 5 or not isinstance(d, bytes):
                return
            fields = parse_message(d)
            for fnum, wtype, val in fields:
                if wtype == 2:
                    if 16 <= len(val) <= 256 and len(val) % 16 == 0:
                        candidates.append((fnum, depth, val))
                    _collect(val, depth + 1)

        _collect(data)

        if DEBUG:
            print(f"    [DEBUG] Found {len(candidates)} AES-size fields:", flush=True)

        for fnum, depth, val in candidates:
            plaintext = aes_decrypt(val)
            if plaintext is None:
                continue

            try:
                text = plaintext.decode('utf-8')
            except (UnicodeDecodeError, ValueError):
                continue

            if DEBUG:
                print(f"    [DEBUG]   f{fnum} d{depth} len={len(val)} "
                      f"decrypted={text[:80]!r}", flush=True)

            # Check ROT47-encoded URL
            decoded = rot47_decode(text)
            if len(decoded) > 8:
                after = decoded[8:]
                if after.startswith('http://') or after.startswith('https://'):
                    if DEBUG:
                        print(f"    [DEBUG]     ROT47 decoded: {after[:80]!r}", flush=True)
                    return after

            # Check plain URL
            if text.startswith('http://') or text.startswith('https://'):
                if DEBUG:
                    print(f"    [DEBUG]     Plain URL: {text[:80]!r}", flush=True)
                return text

            # Check ROT47 without slice
            if decoded.startswith('http://') or decoded.startswith('https://'):
                if DEBUG:
                    print(f"    [DEBUG]     ROT47 (no slice): {decoded[:80]!r}", flush=True)
                return decoded

        # Fallback: ROT47 on printable strings
        strings = []

        def _collect_strings(d, depth=0):
            if depth > 5 or not isinstance(d, bytes):
                return
            fields = parse_message(d)
            for fnum, wtype, val in fields:
                if wtype == 2:
                    if len(val) > 20:
                        try:
                            text = val.decode('utf-8')
                            if all(33 <= ord(c) <= 126 for c in text):
                                strings.append((fnum, depth, text))
                        except (UnicodeDecodeError, ValueError):
                            pass
                    _collect_strings(val, depth + 1)

        _collect_strings(data)

        for fnum, depth, s in sorted(strings, key=lambda x: len(x[2]), reverse=True):
            decoded = rot47_decode(s)
            if len(decoded) > 8:
                after = decoded[8:]
                if after.startswith('http://') or after.startswith('https://'):
                    return after

        return None

    elif isinstance(data, (dict, list)):
        if isinstance(data, dict):
            for key in ("url", "streamUrl", "playUrl", "hlsUrl", "src", "link",
                        "obfuscatedUrl", "encodedUrl"):
                if key in data and isinstance(data[key], str) and len(data[key]) > 20:
                    s = data[key]
                    decoded = rot47_decode(s)
                    if len(decoded) > 8 and (decoded[8:].startswith('http://')
                                              or decoded[8:].startswith('https://')):
                        return decoded[8:]
                    if s.startswith('http'):
                        return s
        json_str = json.dumps(data)
        m = re.findall(r'"([^"]{20,})"', json_str)
        for s in sorted(m, key=len, reverse=True):
            decoded = rot47_decode(s)
            if len(decoded) > 8 and (decoded[8:].startswith('http://')
                                      or decoded[8:].startswith('https://')):
                return decoded[8:]
        if m:
            return max(m, key=len)
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


# ==================== API ====================

def get_signing_key(match_id=None, sport_type=None):
    if match_id:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=102&code=103&code=104&code=105"
               f"&matchId={match_id}&sportType={sport_type}&stream=true")
    else:
        url = (f"{DATA_URL}/api/common/bs"
               f"?code=100&sportType={sport_type or 0}&stream=true")

    resp = safe_get(url)
    if resp is None:
        return None

    content = decode_api_response(resp)

    m = re.search(rb'[a-f0-9]{32}', content)
    if m:
        return m.group().decode()

    m = re.search(rb'[a-zA-Z0-9]{32}', content)
    if m:
        return m.group().decode()

    m = re.search(rb'[a-f0-9]{6,}', content)
    if m:
        return m.group().decode()

    return None


def get_live_matches(md5):
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={SPORT_TYPE}&language={LANGUAGE}&stream=true")
    resp = safe_get(url)
    if resp is None:
        return []

    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    if fmt == "json":
        if isinstance(data, dict):
            return (data.get("data") or data.get("list")
                    or data.get("matches") or data.get("items") or [])
        return data if isinstance(data, list) else []
    return extract_matches_from_protobuf(content)


def get_match_detail(match_id, sport_type, fallback_md5):
    detail_md5 = get_signing_key(match_id=match_id, sport_type=sport_type)

    if not detail_md5:
        detail_md5 = fallback_md5

    if not detail_md5:
        return None, None

    url = (f"{DATA_URL}/sfver{detail_md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}"
           f"&language={LANGUAGE}&stream=true")
    resp = safe_get(url)
    if resp is None:
        return None, None

    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    if fmt == "protobuf" and isinstance(data, bytes):
        fields = parse_message(data)
        if not fields and len(data) < 200:
            err_text = data.decode('utf-8', errors='replace').strip()
            if DEBUG:
                print(f"    [DEBUG] Match detail error: {err_text!r}", flush=True)
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
    resp = safe_get(url)
    if resp is None:
        return None, "", b""

    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)
    rb_session = resp.headers.get("rb-session", "")

    return data, rb_session, content


# ==================== MAIN ====================

def main():
    t0 = time.time()

    if not HAS_AES:
        print("WARNING: pycryptodome not installed. "
              "Run: pip install pycryptodome", file=sys.stderr, flush=True)

    # Step 1
    print("Step 1: Getting signing key (code=100)...", flush=True)
    md5 = get_signing_key(sport_type=SPORT_TYPE)
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(1)
    print(f"  Key: {md5}", flush=True)

    # Step 2
    print("Step 2: Getting live matches...", flush=True)
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"  Found {len(matches)} matches", flush=True)

    if not matches:
        print("  No live matches.", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    for m in matches[:5]:
        sport_name = SPORT_NAMES.get(m['sport_type'], f"Sport {m['sport_type']}")
        print(f"    - {m['name']} (id={m['id']}, {sport_name})", flush=True)

    # Step 3: Sequential
    print("Step 3: Fetching stream URLs (sequential)...", flush=True)
    entries = []

    for idx, match in enumerate(matches):
        name = match["name"]
        match_id = match["id"]
        sport_type = match["sport_type"]
        sport_name = SPORT_NAMES.get(sport_type, f"Sport {sport_type}")

        print(f"  [{idx+1}/{len(matches)}] {name} ({sport_name})...", end=" ", flush=True)

        # 3a: Match detail → streamId + logo
        detail, raw_content = get_match_detail(match_id, sport_type, md5)
        if detail is None:
            print("NO DETAIL", flush=True)
            continue

        if DEBUG and isinstance(detail, bytes) and idx == 0:
            dump_protobuf(detail)

        logo = extract_logo_from_protobuf(detail) if isinstance(detail, bytes) else None

        stream_id = extract_stream_id(detail, match_id=match_id)
        if not stream_id:
            print(f"NO STREAMID (matchId={match_id})", flush=True)
            continue

        if DEBUG:
            print(f"streamId={stream_id}", end=" → ", flush=True)

        # 3b: Stream detail
        stream_data, rb_session, raw = get_stream_detail(
            stream_id, match_id, sport_type)

        if not rb_session:
            print("NO RB-SESSION", flush=True)
            continue

        # 3c: Extract plain URL (AES decrypt + ROT47)
        plain_url = None
        if isinstance(stream_data, bytes):
            plain_url = extract_obfuscated_url(stream_data)
        elif isinstance(stream_data, dict):
            plain_url = extract_obfuscated_url(stream_data)

        if not plain_url:
            print("NO OBFUSCATED URL", flush=True)
            continue

        # 3d: Build tokenized URL
        final_url = build_tokenized_url(plain_url, rb_session)
        if not final_url:
            print("TOKEN BUILD FAILED", flush=True)
            continue

        entries.append({
            "name": name,
            "url": final_url,
            "logo": logo,
            "sport": sport_name,
        })
        print("OK", flush=True)
        if DEBUG:
            print(f"       → {final_url[:100]}", flush=True)
        if logo and DEBUG:
            print(f"       logo: {logo[:80]}", flush=True)

    elapsed = time.time() - t0
    print(f"\nGenerated {len(entries)} streams in {elapsed:.1f}s", flush=True)

    # Build M3U with tvg-logo and group-title
    lines = ["#EXTM3U"]
    for entry in entries:
        name = entry["name"]
        stream_url = entry["url"]
        logo = entry["logo"] or ""
        sport = entry["sport"]
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)

        attrs = f'tvg-id="{tvg_id}" group-title="{sport}"'
        if logo:
            attrs += f' tvg-logo="{logo}"'
        lines.append(f'#EXTINF:-1 {attrs},{name}')
        lines.append(stream_url)

    m3u_content = "\n".join(lines) + "\n"
    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    print(f"\n{'='*55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'='*55}", flush=True)


if __name__ == "__main__":
    main()   
