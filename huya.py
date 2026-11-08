import requests
import json
import re
import sys
import time
import base64
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

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
MAX_MATCHES = 50
TIMEOUT = 15
SPORT_TYPE = 0
LANGUAGE = 34
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"
MAX_WORKERS = 5

# *** SET YOUR CLOUDFLARE WORKER URL HERE ***
# After deploying the worker, replace with your actual URL
WORKER_URL = "https://pl-gratosv-al.bhns.workers.dev"

REQUEST_DELAY = (0.5, 1.5)
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
}

AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"
AES_IV = b"8017d3a8f1400d2f"

WIB = timezone(timedelta(hours=7))

SPORT_NAMES = {
    1: "Football", 2: "Basketball", 3: "Tennis", 4: "Baseball",
    5: "Ice Hockey", 6: "Cricket", 7: "Volleyball", 8: "Badminton",
    9: "Table Tennis", 10: "Handball", 11: "Rugby", 12: "Golf",
    13: "Basketball", 14: "American Football", 15: "MMA", 16: "Boxing",
    17: "Cycling", 18: "Formula 1", 19: "Esports", 20: "Snooker",
    21: "Floorball", 22: "Hockey", 23: "Lacrosse", 24: "Netball",
    25: "Polo", 26: "Squash", 27: "Surfing", 28: "Swimming",
    29: "Weightlifting", 30: "Wrestling", 90: "Golf",
}

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
        except requests.exceptions.RequestException:
            if attempt < MAX_RETRIES - 1:
                continue
            return None
        if resp.status_code in (429, 503):
            time.sleep(RETRY_BACKOFF * (attempt + 1))
            continue
        if resp.status_code != 200:
            return None
        return resp
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


def find_string_fields(data, min_len=5, max_len=500):
    results = []
    _find_strings_recursive(data, results, 0, min_len, max_len)
    return results


def _find_strings_recursive(data, results, depth, min_len, max_len):
    if depth > 3:
        return
    fields = parse_message(data)
    for fnum, wtype, val in fields:
        if wtype == 2:
            try:
                text = val.decode('utf-8')
                if all(32 <= ord(c) < 127 or c in '\n\t' for c in text):
                    if min_len <= len(text) <= max_len:
                        results.append(text)
                    continue
            except (UnicodeDecodeError, ValueError):
                pass
            _find_strings_recursive(val, results, depth + 1, min_len, max_len)


def find_vs_name(data):
    m = re.search(rb'[\x20-\x7e]{2,} vs [\x20-\x7e]{2,}', data)
    if m:
        name = m.group().decode('utf-8', errors='replace').strip()
        name = re.sub(r'^[^a-zA-Z\u00c0-\u024f]+', '', name)
        return name
    return None


def find_logo_url(data):
    strings = find_string_fields(data, min_len=10, max_len=300)
    for s in strings:
        if s.startswith("http") and (
            "logo" in s.lower() or
            "team" in s.lower() or
            "icon" in s.lower() or
            s.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif'))
        ):
            return s
    for s in strings:
        if s.startswith("http") and ('.png' in s or '.jpg' in s or '.webp' in s):
            return s
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
        timestamp = None
        for mf_num, mf_wire, mf_val in match_fields:
            if mf_wire == 0:
                if mf_num == 1 and mf_val > 1000 and match_id is None:
                    match_id = mf_val
                elif mf_num == 2 and mf_val > 0 and mf_val < 200:
                    sport_type = mf_val
                elif mf_num == 3 and mf_val > 1_000_000_000_000:
                    timestamp = mf_val
        name = find_vs_name(val)
        logo = find_logo_url(val)
        if name and match_id:
            matches.append({
                "name": name,
                "id": match_id,
                "sport_type": sport_type or SPORT_TYPE,
                "timestamp": timestamp,
                "logo": logo or "",
            })
    return matches


# ==================== STREAM ID EXTRACTION ====================

def extract_stream_id(data, match_id=None):
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
                for sf_num, sf_wire, sf_val in stream_fields:
                    if sf_wire == 0 and sf_num == 1:
                        if sf_val != match_id and 100 < sf_val < 999999999:
                            return sf_val

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
    return None


# ==================== CRYPTO ====================

def rot47_decode(s):
    if isinstance(s, bytes):
        s = s.decode('ascii', errors='ignore')
    result = []
    for c in s:
        code = ord(c)
        if 33 <= code <= 126:
            code = (code - 33 + 47) % 94 + 33
        result.append(chr(code))
    return ''.join(result)


def _check_rot47_url(raw):
    try:
        s = raw.decode('ascii')
    except (UnicodeDecodeError, ValueError):
        return None
    decoded = rot47_decode(s)
    if len(decoded) > 12 and decoded[8:12] == "http":
        return raw.decode('ascii')
    return None


def extract_obfuscated_url(data):
    if isinstance(data, bytes):
        fields = parse_message(data)
        for fnum, wtype, val in fields:
            if wtype == 2 and len(val) > 20:
                candidate = _check_rot47_url(val)
                if candidate:
                    return candidate
                sub_fields = parse_message(val)
                for sf_num, sf_wire, sf_val in sub_fields:
                    if sf_wire == 2 and len(sf_val) > 20:
                        candidate = _check_rot47_url(sf_val)
                        if candidate:
                            return candidate
        candidates = re.findall(rb'[\x21-\x7e]{20,}', data)
        for c in sorted(candidates, key=len, reverse=True):
            candidate = _check_rot47_url(c)
            if candidate:
                return candidate
        return None
    elif isinstance(data, (dict, list)):
        json_str = json.dumps(data)
        m = re.findall(r'"([^"]{20,})"', json_str)
        for s in sorted(m, key=len, reverse=True):
            candidate = _check_rot47_url(s.encode())
            if candidate:
                return candidate
    return None


def extract_referer(data):
    if isinstance(data, bytes):
        strings = find_string_fields(data, min_len=10, max_len=200)
        for s in strings:
            if s.startswith("http") and "m3u8" not in s and len(s) < 100:
                parsed = urlparse(s)
                if parsed.netloc and not parsed.path.strip('/'):
                    return s
        for s in strings:
            if s.startswith("http") and s.count('/') <= 2 and len(s) < 80:
                return s
    return None


def build_tokenized_url(obfuscated_url, rb_session):
    decoded = rot47_decode(obfuscated_url)
    decoded = decoded[8:]
    if not decoded.startswith("http"):
        idx = decoded.find("http")
        if idx == -1:
            return None
        decoded = decoded[idx:]

    m3u8_idx = decoded.find('.m3u8')
    if m3u8_idx != -1:
        decoded = decoded[:m3u8_idx + 5]

    if not HAS_AES:
        return None

    try:
        session_bytes = rb_session.encode('utf-8')
        padded = pad(session_bytes, 16)
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        ciphertext = cipher.encrypt(padded)
        token_b64 = base64.b64encode(ciphertext).decode('ascii')
    except Exception:
        return None

    token_b64 = quote(token_b64, safe='')
    parsed = urlparse(decoded)
    encoded_path = quote(parsed.path, safe='/')
    token_segment = f"token-{token_b64}a"

    if encoded_path.startswith("/"):
        new_path = f"/{token_segment}{encoded_path}"
    else:
        new_path = f"{token_segment}/{encoded_path}"

    result = parsed._replace(path=new_path)
    return urlunparse(result)


# ==================== DECODE ====================

def decode_api_response(resp) -> bytes:
    return resp.content


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
            raw = (data.get("data") or data.get("list")
                   or data.get("matches") or data.get("items") or [])
        else:
            raw = data if isinstance(data, list) else []
        matches = []
        for m in raw:
            if isinstance(m, dict):
                matches.append({
                    "name": m.get("name") or m.get("matchName")
                            or m.get("homeTeam", "?") + " vs " + m.get("awayTeam", "?"),
                    "id": m.get("id") or m.get("matchId") or m.get("match_id"),
                    "sport_type": m.get("sportType") or m.get("sport_type") or 0,
                    "timestamp": m.get("timestamp") or m.get("startTime") or m.get("kickOffTime"),
                    "logo": m.get("logo") or m.get("homeLogo") or "",
                })
        return matches
    return extract_matches_from_protobuf(content)


def process_match(match, fallback_md5):
    name = match["name"]
    match_id = match["id"]
    sport_type = match["sport_type"]
    timestamp = match.get("timestamp")
    logo = match.get("logo", "")

    detail_md5 = get_signing_key(match_id=match_id, sport_type=sport_type)
    if not detail_md5:
        detail_md5 = fallback_md5
    if not detail_md5:
        return None

    url = (f"{DATA_URL}/sfver{detail_md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}"
           f"&language={LANGUAGE}&stream=true")
    resp = safe_get(url)
    if resp is None:
        return None

    content = decode_api_response(resp)
    fmt, data = try_json_or_protobuf(content)

    stream_id = None
    if fmt == "protobuf" and isinstance(data, bytes):
        stream_id = extract_stream_id(data, match_id=match_id)
    elif fmt == "json" and isinstance(data, dict):
        def _find(d, keys, depth=0):
            if depth > 5:
                return None
            if isinstance(d, dict):
                for k in keys:
                    if k in d:
                        return d[k]
                for v in d.values():
                    r = _find(v, keys, depth + 1)
                    if r is not None:
                        return r
            elif isinstance(d, list):
                for item in d:
                    r = _find(item, keys, depth + 1)
                    if r is not None:
                        return r
            return None
        stream_id = _find(data, ["streamId", "stream_id"])

    if not stream_id:
        return None

    s_url = (f"{DATA_URL}/api/stream/detail"
             f"?streamId={stream_id}&siteType={SITE_TYPE}"
             f"&continent={CONTINENT}&country={COUNTRY}"
             f"&digit={DIGIT}&matchId={match_id}&sportType={sport_type}")
    s_resp = safe_get(s_url)
    if s_resp is None:
        return None

    s_content = decode_api_response(s_resp)
    rb_session = s_resp.headers.get("rb-session", "")
    if not rb_session:
        return None

    s_fmt, s_data = try_json_or_protobuf(s_content)

    obfuscated = None
    if isinstance(s_data, bytes):
        obfuscated = extract_obfuscated_url(s_data)
    elif isinstance(s_data, dict):
        for key in ("url", "streamUrl", "playUrl", "hlsUrl", "src", "link",
                    "obfuscatedUrl", "encodedUrl", "f4"):
            if key in s_data and isinstance(s_data[key], str):
                obfuscated = s_data[key]
                break
        if not obfuscated:
            obfuscated = extract_obfuscated_url(s_data)

    if not obfuscated:
        return None

    final_url = build_tokenized_url(obfuscated, rb_session)
    if not final_url:
        return None

    referer = extract_referer(s_data)
    if not referer:
        parsed_url = urlparse(final_url)
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}"
    if not referer.endswith('/'):
        referer += '/'

    return {
        "name": name,
        "url": final_url,
        "referer": referer,
        "sport_type": sport_type,
        "timestamp": timestamp,
        "logo": logo,
    }


# ==================== M3U BUILDER (WORKER) ====================

def format_time_wib(ts_ms):
    if not ts_ms:
        return ""
    try:
        if ts_ms > 1_000_000_000_000:
            ts_ms = ts_ms // 1000
        dt = datetime.fromtimestamp(ts_ms, tz=WIB)
        return f"[{dt.strftime('%H:%M')} WIB {dt.strftime('%d-%m-%Y')}]"
    except Exception:
        return ""


def build_worker_url(stream_url, referer):
    """Build the Cloudflare Worker proxy URL."""
    return (f"{WORKER_URL}/?url={quote(stream_url, safe='')}"
            f"&referer={quote(referer, safe='')}")


def build_m3u(entries):
    entries.sort(key=lambda x: x.get("timestamp") or 0)

    lines = ["#EXTM3U"]
    for e in entries:
        name = e["name"]
        url = e["url"]
        referer = e.get("referer", "")
        sport_type = e.get("sport_type", 0)
        logo = e.get("logo", "")
        ts = e.get("timestamp")

        group = SPORT_NAMES.get(sport_type, f"Sport {sport_type}")
        time_str = format_time_wib(ts)
        full_title = f"{name} {time_str}".strip()
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)

        # Route through Cloudflare Worker
        proxied_url = build_worker_url(url, referer)

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{full_title}'
        )
        lines.append(f'#EXTVLCOPT:http-origin={referer.rstrip("/")}')
        lines.append(f'#EXTVLCOPT:http-referrer={referer}')
        lines.append(
            '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Linux; Android 10; K) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36'
        )
        lines.append(proxied_url)

    return "\n".join(lines) + "\n"


# ==================== MAIN ====================

def main():
    t0 = time.time()

    if not HAS_AES:
        print("ERROR: pycryptodome not installed. Run: pip install pycryptodome",
              file=sys.stderr, flush=True)
        sys.exit(1)

    if "your-worker-name" in WORKER_URL:
        print("WARNING: WORKER_URL not set! Set it in CONFIG section.",
              file=sys.stderr, flush=True)
        print("  Deploy worker.js to Cloudflare, then set:", file=sys.stderr, flush=True)
        print(f'  WORKER_URL = "https://your-worker.your-user.workers.dev"',
              file=sys.stderr, flush=True)
        sys.exit(1)

    print("Step 1: Getting signing key...", flush=True)
    md5 = get_signing_key(sport_type=SPORT_TYPE)
    if not md5:
        print("ERROR: Failed to get signing key", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(1)
    print(f"  Key: {md5}", flush=True)

    print("Step 2: Getting live matches...", flush=True)
    matches = get_live_matches(md5)[:MAX_MATCHES]
    print(f"  Found {len(matches)} matches", flush=True)

    if not matches:
        print("  No live matches.", file=sys.stderr, flush=True)
        with open("playlist.m3u", "w") as f:
            f.write("#EXTM3U\n")
        sys.exit(0)

    for m in matches[:5]:
        t = format_time_wib(m.get("timestamp"))
        logo_mark = " [logo]" if m.get("logo") else ""
        print(f"    - {m['name']} (id={m['id']}, sport={m['sport_type']}, {t}){logo_mark}",
              flush=True)

    print(f"Step 3: Fetching stream URLs ({MAX_WORKERS} threads)...", flush=True)
    entries = []
    completed = 0
    total = len(matches)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_match, m, md5): m for m in matches}
        for future in as_completed(futures):
            completed += 1
            match = futures[future]
            try:
                result = future.result()
                if result:
                    entries.append(result)
                    t = format_time_wib(result.get("timestamp"))
                    print(f"  [{completed}/{total}] {result['name']} {t} → OK",
                          flush=True)
                else:
                    print(f"  [{completed}/{total}] {match['name']} → FAIL",
                          flush=True)
            except Exception as ex:
                print(f"  [{completed}/{total}] {match['name']} → ERROR: {ex}",
                      flush=True)

    elapsed = time.time() - t0
    print(f"\nGenerated {len(entries)} streams in {elapsed:.1f}s", flush=True)

    m3u_content = build_m3u(entries)
    with open("playlist.m3u", "w") as f:
        f.write(m3u_content)

    print(f"\n{'='*60}", flush=True)
    print(m3u_content[:2000], flush=True)
    if len(m3u_content) > 2000:
        print(f"... ({len(m3u_content)} chars total)", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"\nSaved to: playlist.m3u", flush=True)
    print(f"\n⚠ Worker URL: {WORKER_URL}", flush=True)
    print(f"  Import playlist.m3u to your IPTV/Dalvik player.", flush=True)
    print(f"  ⚠ If streams don't play, CDN may be IP-locking (Worker uses CF IP).", flush=True)


if __name__ == "__main__":
    main()   
