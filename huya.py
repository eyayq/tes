import requests
import json
import re
import sys
import time
import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# === CONFIG ===
DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LANGUAGE = 34
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"

AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"
AES_IV  = b"8017d3a8f1400d2f"

HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-language": "id-ID,id;q=0.6,en-US;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
}

session = requests.Session()
session.headers.update(HEADERS)


# === RAW PROTOBUF DECODER ===
def read_varint(data, pos):
    """Read a varint from data at pos. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def is_printable(data):
    """Check if bytes look like a printable string."""
    if not data:
        return False
    try:
        s = data.decode('utf-8')
        return all(32 <= ord(c) < 127 or c in '\n\t' for c in s)
    except UnicodeDecodeError:
        return False


def parse_protobuf(data):
    """
    Raw protobuf decoder. Returns dict: {field_number: [list of values]}
    - varint (wire 0): int
    - length-delimited (wire 2): str if printable, else nested dict
    - Other wire types: raw bytes
    """
    fields = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = read_varint(data, pos)
        except (IndexError, IndexError):
            break

        field_num = tag >> 3
        wire_type = tag & 0x07

        if field_num == 0:
            break

        if wire_type == 0:  # varint
            value, pos = read_varint(data, pos)
            fields.setdefault(field_num, []).append(value)

        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                break
            fields.setdefault(field_num, []).append(data[pos:pos+8])
            pos += 8

        elif wire_type == 2:  # length-delimited
            length, pos = read_varint(data, pos)
            if pos + length > len(data):
                break
            raw = data[pos:pos+length]
            pos += length

            # Try to interpret as string or nested message
            if is_printable(raw):
                fields.setdefault(field_num, []).append(raw.decode('utf-8'))
            else:
                # Try to parse as nested protobuf
                nested = parse_protobuf(raw)
                if nested:
                    fields.setdefault(field_num, []).append(nested)
                else:
                    fields.setdefault(field_num, []).append(raw)

        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                break
            fields.setdefault(field_num, []).append(data[pos:pos+4])
            pos += 4

        else:
            # Unknown wire type — skip
            break

    return fields


def extract_matches(pb_data):
    """
    Extract match list from protobuf live-matches response.
    Structure: field 10 = repeated match entries
    Each match: field 1 = id, field 2 = sport_type, nested fields for names
    """
    matches = []

    # Top-level: field 10 contains the match list
    match_entries = pb_data.get(10, [])

    for entry in match_entries:
        if not isinstance(entry, dict):
            continue

        # Each entry might be field 1 (single match) or the match itself
        match = entry
        if 1 in entry and isinstance(entry[1], list):
            # entry is a wrapper: field 1 = the actual match
            inner = entry[1][0]
            if isinstance(inner, dict):
                match = inner

        match_id = None
        sport_type = None
        name = ""
        league = ""

        # field 1: match_id (varint)
        if 1 in match and isinstance(match[1], list):
            v = match[1][0]
            if isinstance(v, int) and v > 1000:
                match_id = v

        # field 2: sport_type (varint)
        if 2 in match and isinstance(match[2], list):
            v = match[2][0]
            if isinstance(v, int):
                sport_type = v

        # Search for string fields (name, league)
        def find_strings(d, depth=0):
            """Recursively find all string values in a parsed protobuf dict."""
            results = []
            if depth > 4:
                return results
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, str) and len(item) > 3:
                                results.append(item)
                            elif isinstance(item, dict):
                                results.extend(find_strings(item, depth + 1))
            return results

        strings = find_strings(match)
        if strings:
            # Usually: league name is the longest, or contains ":"
            for s in strings:
                if ":" in s and not league:
                    league = s
                elif len(s) > 3 and not name:
                    name = s
            if not name and strings:
                name = strings[-1]

        if match_id:
            matches.append({
                "id": match_id,
                "sport_type": sport_type or 0,
                "name": name or f"Match {match_id}",
                "league": league,
            })

    return matches


def extract_stream_id(pb_data):
    """Extract streamId from match detail protobuf response."""
    # Search for varint fields that look like stream IDs
    # Stream IDs are typically large numbers in nested fields
    def find_varints(d, depth=0):
        results = []
        if depth > 6:
            return results
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, int) and item > 10000:
                            results.append((k, item))
                        elif isinstance(item, dict):
                            results.extend(find_varints(item, depth + 1))
        return results

    # Also look for string fields that might be stream IDs
    def find_strings(d, depth=0):
        results = []
        if depth > 6:
            return results
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.isdigit() and len(item) > 4:
                            results.append(item)
                        elif isinstance(item, dict):
                            results.extend(find_strings(item, depth + 1))
        return results

    # Try to find stream-related fields
    # In the protobuf, streamId is usually a varint in a nested message
    varints = find_varints(pb_data)
    strings = find_strings(pb_data)

    # Heuristic: streamId is often the largest varint or a specific field
    # Print all candidates for debugging
    return varints, strings


# === HELPERS ===
def rot47(s):
    result = []
    for ch in s:
        code = ord(ch)
        if 33 <= code <= 126:
            result.append(chr((code - 33 + 47) % 94 + 33))
        else:
            result.append(ch)
    return ''.join(result)


def decode(resp):
    return resp.content


def try_parse(content):
    """Try JSON first, then protobuf. Returns (parsed, format)."""
    try:
        return json.loads(content), "json"
    except Exception:
        pass
    pb = parse_protobuf(content)
    if pb:
        return pb, "protobuf"
    return None, "unknown"


def encrypt_session(rb_session):
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    encrypted = cipher.encrypt(pad(rb_session.encode('utf-8'), 16))
    return base64.b64encode(encrypted).decode()


def build_url(base_url, token_b64):
    token = f"token-{token_b64}a"
    if ".m3u8" in base_url:
        idx = base_url.rindex(".m3u8")
        return base_url[:idx] + token + base_url[idx:]
    elif base_url.endswith("/"):
        return base_url + token
    else:
        return base_url + "/" + token


# === STEP 1: Signing Key ===
print("=" * 60)
print("STEP 1: Get Signing Key")
print("=" * 60)
resp = session.get(
    f"{DATA_URL}/api/common/bs?code=100&sportType=0&stream=true",
    timeout=15
)
content = decode(resp)
m = re.search(rb'[a-f0-9]{32}', content)
md5 = m.group().decode() if m else None
print(f"  Key: {md5}")
if not md5:
    print("  FAIL: no key found")
    sys.exit(1)


# === STEP 2: Live Matches ===
print("\n" + "=" * 60)
print("STEP 2: Get Live Matches")
print("=" * 60)
time.sleep(2)
resp = session.get(
    f"{DATA_URL}/sfver{md5}/api/match/live?sportType=0&language={LANGUAGE}&stream=true",
    timeout=15
)
content = decode(resp)
print(f"  Size: {len(content)} bytes")

data, fmt = try_parse(content)
print(f"  Format: {fmt}")

matches = []
if fmt == "json":
    if isinstance(data, dict):
        matches = data.get("data") or data.get("list") or data.get("matches") or []
    elif isinstance(data, list):
        matches = data
    # Convert to our format
    parsed_matches = []
    for m in matches[:20]:
        if isinstance(m, dict):
            parsed_matches.append({
                "id": m.get("id") or m.get("matchId") or m.get("match_id"),
                "sport_type": m.get("sportType") or m.get("sport_type") or 0,
                "name": m.get("name") or m.get("matchName") or "Unknown",
                "league": m.get("league") or m.get("leagueName") or "",
            })
    matches = parsed_matches

elif fmt == "protobuf":
    matches = extract_matches(data)
    # Debug: show raw field structure
    print(f"  Top-level fields: {list(data.keys())}")
    if 10 in data:
        print(f"  Field 10 has {len(data[10])} entries")
        if data[10]:
            first = data[10][0]
            if isinstance(first, dict):
                print(f"  First entry fields: {list(first.keys())}")

else:
    print("  FAIL: cannot parse response")
    print(f"  First 200 bytes: {content[:200]!r}")
    sys.exit(1)

if not matches:
    print("  No matches found")
    # Dump raw protobuf structure for debugging
    if fmt == "protobuf":
        print("\n  Raw protobuf structure (top-level):")
        for k, v in data.items():
            if isinstance(v, list):
                print(f"    field {k}: {len(v)} items")
                if v and isinstance(v[0], dict):
                    print(f"      first item fields: {list(v[0].keys())}")
                elif v:
                    print(f"      first: {str(v[0])[:80]}")
    sys.exit(0)

print(f"  Found {len(matches)} matches")
for i, m in enumerate(matches[:5]):
    print(f"    [{i}] {m['name']} (id={m['id']}, sport={m['sport_type']})")

match = matches[0]
match_id = match["id"]
sport_type = match["sport_type"]
print(f"\n  Using: {match['name']} (id={match_id}, sport={sport_type})")


# === STEP 3: Match Detail ===
print("\n" + "=" * 60)
print("STEP 3: Get Match Detail")
print("=" * 60)
time.sleep(2)
resp = session.get(
    f"{DATA_URL}/sfver{md5}/api/match/detail?matchId={match_id}"
    f"&sportType={sport_type}&language={LANGUAGE}&stream=true",
    timeout=15
)
content = decode(resp)
print(f"  Size: {len(content)} bytes")

detail, fmt = try_parse(content)
print(f"  Format: {fmt}")

stream_id = None
if fmt == "json":
    # Search for streamId in JSON
    def find_key_json(d, keys, depth=0):
        if depth > 6:
            return None
        if isinstance(d, dict):
            for k in keys:
                if k in d:
                    return d[k]
            for v in d.values():
                r = find_key_json(v, keys, depth + 1)
                if r is not None:
                    return r
        elif isinstance(d, list):
            for item in d:
                r = find_key_json(item, keys, depth + 1)
                if r is not None:
                    return r
        return None

    stream_id = find_key_json(detail, ["streamId", "stream_id", "streamID"])

elif fmt == "protobuf":
    varints, strings = extract_stream_id(detail)
    print(f"  Varint candidates (>10000): {varints[:15]}")
    print(f"  String candidates (numeric): {strings[:10]}")

    # Heuristic: streamId is usually in a specific field
    # Try field 10 → field 1, or look for the pattern
    # In the live list, stream info was in field 10 of each match
    # In detail, it's likely similar
    if 10 in detail:
        for entry in detail[10]:
            if isinstance(entry, dict):
                # Look for varint fields that could be streamId
                for k, v in entry.items():
                    if isinstance(v, list):
                        for item in v:
                            if isinstance(item, int) and item > 10000:
                                if stream_id is None or item > stream_id:
                                    stream_id = item
                # Also check nested
                if 1 in entry:
                    for sub in entry[1]:
                        if isinstance(sub, dict):
                            for k2, v2 in sub.items():
                                if isinstance(v2, list):
                                    for item in v2:
                                        if isinstance(item, int) and item > 10000:
                                            if stream_id is None or item > stream_id:
                                                stream_id = item

    # If still no stream_id, try all varints
    if not stream_id and varints:
        # Pick the most likely one (often the largest or in field 1)
        stream_id = varints[0][1]
        print(f"  Using first varint candidate: {stream_id}")

print(f"  Stream ID: {stream_id}")
if not stream_id:
    print("  FAIL: no streamId found")
    if fmt == "protobuf":
        print("  Full protobuf structure:")
        for k, v in detail.items():
            print(f"    field {k}: {str(v)[:200]}")
    else:
        print(json.dumps(detail, indent=2)[:2000])
    sys.exit(0)


# === STEP 4: Stream Detail ===
print("\n" + "=" * 60)
print("STEP 4: Get Stream Detail")
print("=" * 60)
time.sleep(2)
resp = session.get(
    f"{DATA_URL}/api/stream/detail"
    f"?streamId={stream_id}"
    f"&siteType={SITE_TYPE}"
    f"&continent={CONTINENT}"
    f"&country={COUNTRY}"
    f"&digit={DIGIT}"
    f"&matchId={match_id}"
    f"&sportType={sport_type}",
    timeout=15
)

rb_session = resp.headers.get("rb-session", "")
print(f"  Status: {resp.status_code}")
print(f"  rb-session: {rb_session}")

if not rb_session:
    print("  FAIL: no rb-session header")
    sys.exit(1)

content = decode(resp)
print(f"  Size: {len(content)} bytes")

stream_data, fmt = try_parse(content)
print(f"  Format: {fmt}")

# Find f4 / URL field
f4_value = None
if fmt == "json":
    def find_key_json(d, keys, depth=0):
        if depth > 6:
            return None
        if isinstance(d, dict):
            for k in keys:
                if k in d:
                    return d[k]
            for v in d.values():
                r = find_key_json(v, keys, depth + 1)
                if r is not None:
                    return r
        elif isinstance(d, list):
            for item in d:
                r = find_key_json(item, keys, depth + 1)
                if r is not None:
                    return r
        return None

    f4_value = find_key_json(stream_data, ["f4", "url", "streamUrl", "m3u8Url", "playUrl"])
    if stream_data:
        print(f"  JSON keys: {list(stream_data.keys()) if isinstance(stream_data, dict) else type(stream_data)}")
        print(f"  f4/url: {f4_value}")

elif fmt == "protobuf":
    # f4 is likely a string field in the protobuf
    # Search for long string fields (the obfuscated URL)
    def find_long_strings(d, min_len=30, depth=0):
        results = []
        if depth > 6:
            return results
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and len(item) >= min_len:
                            results.append((k, item))
                        elif isinstance(item, dict):
                            results.extend(find_long_strings(item, min_len, depth + 1))
        return results

    long_strings = find_long_strings(stream_data)
    print(f"  Long string fields found: {len(long_strings)}")
    for k, v in long_strings[:10]:
        print(f"    field {k}: {v[:80]}...")

    # The f4 field is typically the longest non-URL string (ROT47 encoded)
    for k, v in long_strings:
        if not v.startswith("http"):
            f4_value = v
            print(f"  Using field {k} as f4: {v[:60]}...")
            break

    if not f4_value:
        # Maybe it's directly a URL
        for k, v in long_strings:
            if v.startswith("http"):
                f4_value = v
                break

    # Debug: show full structure
    if not f4_value:
        print("  Full protobuf structure:")
        for k, v in stream_data.items():
            print(f"    field {k}: {str(v)[:150]}")

# === STEP 5: Build Stream URL ===
print("\n" + "=" * 60)
print("STEP 5: Build Stream URL")
print("=" * 60)

base_url = None
if f4_value:
    print(f"  f4 value: {f4_value[:100]}")
    rot47_decoded = rot47(f4_value)
    print(f"  ROT47(f4): {rot47_decoded[:100]}")

    if "http" in rot47_decoded:
        url_match = re.search(r'https?://[^\s]+', rot47_decoded)
        if url_match:
            base_url = url_match.group()
            print(f"  ✓ URL found in ROT47: {base_url}")
        else:
            for i in range(min(16, len(rot47_decoded))):
                if rot47_decoded[i:].startswith("http"):
                    base_url = rot47_decoded[i:]
                    print(f"  ✓ URL at offset {i}: {base_url}")
                    break
    elif f4_value.startswith("http"):
        base_url = f4_value
        print(f"  ✓ f4 is already a URL: {base_url}")
    else:
        print("  ⚠ f4 doesn't decode to a URL")
        print(f"  f4 repr: {repr(f4_value[:80])}")

if not base_url:
    # Fallback: search raw content for URL
    url_match = re.search(rb'https?://[^\s"\'\\]+\.m3u8[^\s"\'\\]*', content)
    if url_match:
        base_url = url_match.group().decode()
        print(f"  ✓ Found m3u8 URL in raw response: {base_url}")
    else:
        url_match = re.search(rb'https?://[^\s"\'\\]+', content)
        if url_match:
            base_url = url_match.group().decode()
            print(f"  ✓ Found URL in raw response: {base_url}")

if not base_url:
    print("\n  ⚠ Could not determine base URL")
    print("  Raw content (first 500):")
    print(repr(content[:500]))
    sys.exit(0)

print(f"\n  Base URL: {base_url}")

token_b64 = encrypt_session(rb_session)
print(f"  Encrypted token: {token_b64[:60]}...")

final_url = build_url(base_url, token_b64)

print(f"\n{'=' * 60}")
print(f"  ✓ FINAL STREAM URL:")
print(f"  {final_url}")
print(f"{'=' * 60}")
print(f"""
  Playback:
    - VLC:  Media → Open Network Stream → paste URL
    - mpv:  mpv "{final_url}" --header="Referer: https://www.rbtvplus.ws/"
    - curl: curl -H "Referer: https://www.rbtvplus.ws/" "{final_url}"

  ⚠ Token is IP-locked. Must play from same IP.
  ⚠ Token expires after ~30-60 minutes.
""")   
