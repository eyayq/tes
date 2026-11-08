import requests
import json
import re
import sys
import time
import random
import gzip
import zlib

try:
    import brotli
except ImportError:
    brotli = None

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LANGUAGE = 34
SITE_TYPE = 2001
CONTINENT = "AS"
COUNTRY = "ID"
DIGIT = "trd"

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


def decode(resp):
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()
    if enc == "br" and brotli:
        try:
            content = brotli.decompress(content)
        except Exception:
            pass
    return content


# Step 1: Get signing key
print("=== Step 1: Signing Key ===")
resp = session.get(f"{DATA_URL}/api/common/bs?code=100&sportType=0&stream=true", timeout=15)
content = decode(resp)
m = re.search(rb'[a-f0-9]{32}', content)
md5 = m.group().decode() if m else None
print(f"Key: {md5}")
if not md5:
    print("FAIL: no key")
    sys.exit(1)

# Step 2: Get first live match
print("\n=== Step 2: Live Matches (first 3) ===")
time.sleep(2)
resp = session.get(
    f"{DATA_URL}/sfver{md5}/api/match/live?sportType=0&language={LANGUAGE}&stream=true",
    timeout=15
)
content = decode(resp)
print(f"Content-Type: {resp.headers.get('content-type')}")
print(f"Size: {len(content)} bytes")
print(f"First 200 bytes: {content[:200]!r}")

# Try JSON
try:
    data = json.loads(content)
    print(f"\nParsed as JSON! Keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
    # Extract first match
    if isinstance(data, dict):
        matches = data.get("data") or data.get("list") or data.get("matches") or []
    else:
        matches = data
    if isinstance(matches, list) and len(matches) > 0:
        m0 = matches[0]
        print(f"First match: {json.dumps(m0, indent=2)[:500]}")
except Exception:
    print("\nNot JSON — protobuf or other binary")
    # Try to find "vs" names
    vs_matches = re.findall(rb'[\x20-\x7e]{2,}\s+vs\s+[\x20-\x7e]{2,}', content)
    print(f"Found {len(vs_matches)} 'vs' names")
    for v in vs_matches[:3]:
        print(f"  {v.decode()}")
    sys.exit(0)

if not matches:
    print("No matches found")
    sys.exit(0)

# Pick first match with a clear ID
match = matches[0]
match_id = match.get("id") or match.get("matchId") or match.get("match_id")
sport_type = match.get("sportType") or match.get("sport_type") or 0
name = match.get("name") or match.get("matchName") or "Unknown"

print(f"\nUsing match: {name} (id={match_id}, sport={sport_type})")

# Step 3: Match detail
print(f"\n=== Step 3: Match Detail ===")
time.sleep(2)
resp = session.get(
    f"{DATA_URL}/sfver{md5}/api/match/detail?matchId={match_id}&sportType={sport_type}"
    f"&language={LANGUAGE}&stream=true",
    timeout=15
)
content = decode(resp)
print(f"Status: {resp.status_code}")
print(f"Content-Type: {resp.headers.get('content-type')}")
print(f"Size: {len(content)} bytes")
print(f"First 300 bytes: {content[:300]!r}")

try:
    detail = json.loads(content)
    print(f"\nParsed as JSON!")
    print(json.dumps(detail, indent=2)[:1000])
except Exception:
    print("\nBinary/protobuf response")
    detail = content

# Try to find streamId in detail
stream_id = None
if isinstance(detail, dict):
    def find_key(d, key, depth=0):
        if depth > 5:
            return None
        if isinstance(d, dict):
            if key in d:
                return d[key]
            for v in d.values():
                r = find_key(v, key, depth+1)
                if r:
                    return r
        elif isinstance(d, list):
            for item in d:
                r = find_key(item, key, depth+1)
                if r:
                    return r
        return None
    stream_id = find_key(detail, "streamId") or find_key(detail, "stream_id") or find_key(detail, "id")
    # Also look for any "stream" related keys
    def find_stream_keys(d, depth=0, results=None):
        if results is None:
            results = []
        if depth > 5:
            return results
        if isinstance(d, dict):
            for k, v in d.items():
                if "stream" in k.lower() or "url" in k.lower() or "play" in k.lower():
                    results.append(f"{'  '*depth}{k} = {str(v)[:100]}")
                find_stream_keys(v, depth+1, results)
        elif isinstance(d, list):
            for item in d:
                find_stream_keys(item, depth+1, results)
        return results
    stream_keys = find_stream_keys(detail)
    if stream_keys:
        print("\nStream/URL related keys found:")
        for sk in stream_keys[:20]:
            print(f"  {sk}")

if not stream_id:
    print("\nCould not find streamId in JSON. Raw detail (first 500):")
    print(str(detail)[:500])
    sys.exit(0)

print(f"\nStream ID: {stream_id}")

# Step 4: Stream detail
print(f"\n=== Step 4: Stream Detail ===")
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
print(f"Status: {resp.status_code}")
print(f"Content-Type: {resp.headers.get('content-type')}")
print(f"Headers of interest:")
for h in ["rb-session", "set-cookie", "content-encoding", "x-request-id"]:
    v = resp.headers.get(h)
    if v:
        print(f"  {h}: {v[:100]}")

content = decode(resp)
print(f"\nSize: {len(content)} bytes")
print(f"First 500 bytes: {content[:500]!r}")

# Try JSON
try:
    stream_data = json.loads(content)
    print(f"\n✓ Parsed as JSON!")
    print(json.dumps(stream_data, indent=2)[:2000])
except Exception:
    print("\n✗ Not JSON — binary")
    # Look for hex/AES patterns
    print(f"\nLooking for URL-like patterns...")
    # Find printable strings > 20 chars
    strings = re.findall(rb'[\x20-\x7e]{20,}', content)
    print(f"Found {len(strings)} printable strings > 20 chars:")
    for s in strings[:20]:
        print(f"  {s.decode()[:120]}")
    # Find base64-like
    b64s = re.findall(rb'[A-Za-z0-9+/]{32,}={0,2}', content)
    print(f"\nFound {len(b64s)} base64-like strings:")
    for b in b64s[:10]:
        print(f"  {b.decode()[:120]}")

print("\n=== DONE ===")
print("Save this output and share it — I'll adjust the extraction logic.")   
