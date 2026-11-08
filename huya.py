import requests
import json
import re
import sys
import time
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

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
            "logo" in s.lower() or "team" in s.lower() or "icon" in s.lower()
            or s.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif'))
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
    """Python only gets stream_id. Worker handles the rest."""
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
        stream_id = _find_json(data, ["streamId", "stream_id"])

    if not stream_id:
        return None

    # Worker resolves everything: stream detail → token → m3u8
    gen_url = (f"{WORKER_URL}/gen"
               f"?streamId={stream_id}"
               f"&matchId={match_id}"
               f"&sportType={sport_type}")

    return {
        "name": name,
        "url": gen_url,
        "sport_type": sport_type,
        "timestamp": timestamp,
        "logo": logo,
    }


def _find_json(d, keys, depth=0):
    if depth > 5:
        return None
    if isinstance(d, dict):
        for k in keys:
            if k in d:
                return d[k]
        for v in d.values():
            r = _find_json(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(d, list):
        for item in d:
            r = _find_json(item, keys, depth + 1)
            if r is not None:
                return r
    return None


# ==================== M3U BUILDER ====================

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


def build_m3u(entries):
    entries.sort(key=lambda x: x.get("timestamp") or 0)

    lines = ["#EXTM3U"]
    for e in entries:
        name = e["name"]
        url = e["url"]
        sport_type = e.get("sport_type", 0)
        logo = e.get("logo", "")
        ts = e.get("timestamp")

        group = SPORT_NAMES.get(sport_type, f"Sport {sport_type}")
        time_str = format_time_wib(ts)
        full_title = f"{name} {time_str}".strip()
        tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{full_title}'
        )
        lines.append(
            '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Linux; Android 10; K) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36'
        )
        lines.append(url)

    return "\n".join(lines) + "\n"


# ==================== MAIN ====================

def main():
    t0 = time.time()

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

    print(f"Step 3: Fetching stream IDs ({MAX_WORKERS} threads)...", flush=True)
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
    print(f"Worker: {WORKER_URL}", flush=True)


if __name__ == "__main__":
    main()   
