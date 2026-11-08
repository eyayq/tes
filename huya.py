import requests
import json
import re
import sys
import time
import base64
import gzip
import zlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

try:
    import brotli
except ImportError:
    brotli = None

# === CONFIG ===
DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LANGUAGE = 34
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"

# AES key/IV — RAW UTF-8 bytes, NOT hex-decoded
AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"  # 32 bytes → AES-256
AES_IV  = b"8017d3a8f1400d2f"                  # 16 bytes → CBC

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
    """requests already decompresses gzip/deflate/br — just return content."""
    return resp.content


def find_key(d, keys, depth=0):
    if depth > 6:
        return None
    if isinstance(d, dict):
        for k in keys:
            if k in d:
                return d[k]
        for v in d.values():
            r = find_key(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(d, list):
        for item in d:
            r = find_key(item, keys, depth + 1)
            if r is not None:
                return r
    return None


def find_all_keys(d, pattern, depth=0, results=None):
    if results is None:
        results = []
    if depth > 6:
        return results
    if isinstance(d, dict):
        for k, v in d.items():
            if pattern in k.lower():
                results.append((k, v))
            find_all_keys(v, pattern, depth + 1, results)
    elif isinstance(d, list):
        for item in d:
            find_all_keys(item, pattern, depth + 1, results)
    return results


def encrypt_session(rb_session):
    """AES-256-CBC encrypt rb-session → base64 string"""
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    encrypted = cipher.encrypt(pad(rb_session.encode('utf-8'), 16))
    return base64.b64encode(encrypted).decode()


def build_url(base_url, token_b64):
    """Insert token-{b64}a into the stream URL path."""
    token = f"token-{token_b64}a"
    if ".m3u8" in base_url:
        idx = base_url.rindex(".m3u8")
        final = base_url[:idx] + token + base_url[idx:]
    elif base_url.endswith("/"):
        final = base_url + token
    else:
        final = base_url + "/" + token
    return final


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

try:
    data = json.loads(content)
    if isinstance(data, dict):
        matches = data.get("data") or data.get("list") or data.get("matches") or []
    else:
        matches = data
except Exception:
    print("  Response is not JSON — checking for binary...")
    print(f"  First 200 bytes: {content[:200]!r}")
    sys.exit(1)

if not matches:
    print("  No matches found")
    sys.exit(0)

print(f"  Found {len(matches)} matches")

match = matches[0]
match_id = match.get("id") or match.get("matchId") or match.get("match_id")
sport_type = match.get("sportType") or match.get("sport_type") or 0
name = (match.get("name") or match.get("matchName")
        or match.get("homeTeam", "?") + " vs " + match.get("awayTeam", "?"))
print(f"  Using: {name} (id={match_id}, sport={sport_type})")


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

try:
    detail = json.loads(content)
except Exception:
    print("  Not JSON — binary response")
    print(f"  First 300 bytes: {content[:300]!r}")
    sys.exit(1)

stream_id = find_key(detail, ["streamId", "stream_id"])
if not stream_id:
    stream_fields = find_all_keys(detail, "stream")
    print(f"  Stream-related fields: {stream_fields[:10]}")
    stream_id = find_key(detail, ["id"])

print(f"  Stream ID: {stream_id}")
if not stream_id:
    print("  FAIL: no streamId found")
    print(f"  Full detail: {json.dumps(detail, indent=2)[:1500]}")
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

try:
    stream_data = json.loads(content)
    print(f"  JSON keys: {list(stream_data.keys()) if isinstance(stream_data, dict) else type(stream_data)}")
except Exception:
    print("  Not JSON — binary response")
    print(f"  First 500 bytes: {content[:500]!r}")
    stream_data = None


# === STEP 5: Build Stream URL ===
print("\n" + "=" * 60)
print("STEP 5: Build Stream URL")
print("=" * 60)

f4_value = None
if stream_data:
    f4_value = find_key(stream_data, ["f4", "url", "streamUrl", "m3u8Url", "playUrl"])

if stream_data:
    all_fields = find_all_keys(stream_data, "url") + find_all_keys(stream_data, "f4")
    if all_fields:
        print(f"  URL/f4 fields found:")
        for k, v in all_fields[:10]:
            print(f"    {k} = {str(v)[:120]}")

base_url = None
if f4_value:
    print(f"\n  f4 value: {f4_value[:100]}")
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
        print("  ⚠ f4 doesn't look like a URL even after ROT47")
        print(f"  f4 repr: {repr(f4_value[:80])}")

if not base_url:
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
    if stream_data:
        print(json.dumps(stream_data, indent=2)[:2000])
    else:
        print(content[:1000])
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
