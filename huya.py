#!/usr/bin/env python3
"""
RBTV Playlist Generator (v9 FINAL — GitHub Actions)
API response = PROTOBUF (bukan JSON) → parse pakai regex dari binary
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


# ==================== PROTOBUF BINARY PARSER ====================

def extract_strings_from_binary(data: bytes) -> list[str]:
    """Extract readable UTF-8 strings from protobuf binary."""
    strings = []
    # Find sequences of printable chars (min 3 chars)
    for m in re.finditer(rb'[\x20-\x7e\xc2-\xf4][\x00-\xff]{2,}', data):
        try:
            s = m.group().decode('utf-8', errors='strict')
            if len(s) >= 3 and not s.startswith('\x00'):
                strings.append(s)
        except Exception:
            continue
    return strings


def parse_protobuf_matches(data: bytes) -> list[dict]:
    """
    Parse protobuf binary to extract match entries.
    Uses structural patterns visible in the binary.
    """
    matches = []
    
    # Extract all readable strings
    all_strings = []
    i = 0
    while i < len(data):
        # Look for length-prefixed strings (protobuf wire type 2)
        # Common pattern: [field_byte][length_byte][string_bytes]
        if data[i] in range(1, 200) and i + 1 < len(data):
            length = data[i + 1]
            if 3 <= length <= 200 and i + 2 + length <= len(data):
                chunk = data[i + 2: i + 2 + length]
                # Check if it's mostly printable
                printable = sum(1 for b in chunk if 32 <= b < 127 or b >= 0xc2) / len(chunk)
                if printable > 0.8:
                    try:
                        s = chunk.decode('utf-8', errors='strict')
                        if s and not s.startswith('\x00'):
                            all_strings.append((i, s))
                            i += 2 + length
                            continue
                    except Exception:
                        pass
        i += 1
    
    # Now try to identify match structures
    # Pattern: league name, then team names, then IDs
    # We'll extract what we can and pair them up
    
    # Extract URLs (stream URLs, logo URLs)
    urls = re.findall(rb'https?://[^\x00-\x1f\x7f-\xff]{10,}', data)
    url_list = [u.decode() for u in urls]
    
    # Extract numeric IDs (protobuf varint fields)
    # Match IDs are typically in specific byte patterns
    
    # For now, return raw data for further processing
    return {
        "strings": [s for _, s in all_strings],
        "urls": url_list,
        "raw_length": len(data),
    }


def decode_api_response(resp) -> bytes:
    """Decode response, return raw bytes (not string)."""
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


def try_json_or_protobuf(content: bytes) -> dict:
    """Try JSON first, fall back to protobuf parsing."""
    # Try JSON
    try:
        text = content.decode('utf-8')
        if text.strip().startswith('{') or text.strip().startswith('['):
            return {"type": "json", "data": json.loads(text)}
    except Exception:
        pass

    # It's protobuf — parse binary
    return {"type": "protobuf", "data": content}


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
    result = try_json_or_protobuf(content)
    
    print(f"[DEBUG] /match/live: status={resp.status_code}, "
          f"len={len(content)}, format={result['type']}", file=sys.stderr, flush=True)
    
    if result["type"] == "json":
        data = result["data"]
        if isinstance(data, dict):
            return (data.get("data") or data.get("list")
                    or data.get("matches") or data.get("items") or [])
        return data if isinstance(data, list) else []
    
    # PROTOBUF: extract match data from binary
    return parse_protobuf_binary(content)


def parse_protobuf_binary(data: bytes) -> list[dict]:
    """
    Extract match entries from protobuf binary.
    Strategy: find repeated message blocks and extract fields.
    """
    matches = []
    
    # Find all embedded URLs
    stream_urls = re.findall(
        rb'(https?://[^/\x00-\x1f\x7f-\xff]+/cfall/[^/\x00-\x1f\x7f-\xff]+/super/[^"\x00-\x1f\x7f-\xff]+)',
        data)
    stream_urls = [u.decode() for u in stream_urls]
    
    # Find all .m3u8 URLs
    m3u8_urls = re.findall(rb'(https?://[^"\x00-\x1f\x7f-\xff]+\.m3u8[^"\x00-\x1f\x7f-\xff]*)', data)
    m3u8_urls = [u.decode() for u in m3u8_urls]
    
    # Extract readable strings for team/league names
    # Protobuf strings are length-prefixed: [field_tag][length][bytes]
    names = []
    i = 0
    while i < len(data) - 2:
        # Look for string fields (wire type 2 = length-delimited)
        tag = data[i]
        field_num = tag >> 3
        wire_type = tag & 0x07
        
        if wire_type == 2 and i + 1 < len(data):
            length = data[i + 1]
            if 2 <= length <= 100 and i + 2 + length <= len(data):
                chunk = data[i + 2: i + 2 + length]
                # Must be valid UTF-8 printable
                try:
                    s = chunk.decode('utf-8')
                    if all(32 <= ord(c) < 127 or ord(c) > 0x200 for c in s):
                        if not s.startswith(('http', 'www.')) and len(s) >= 2:
                            names.append(s)
                    i += 2 + length
                    continue
                except Exception:
                    pass
        
        # Handle multi-byte length (for strings > 127 bytes)
        if wire_type == 2 and i + 2 < len(data) and data[i + 1] >= 0x80:
            # Varint length
            length = 0
            shift = 0
            j = i + 1
            while j < len(data) and j < i + 5:
                length |= (data[j] & 0x7f) << shift
                shift += 7
                if not (data[j] & 0x80):
                    break
                j += 1
            j += 1
            if 2 <= length <= 500 and j + length <= len(data):
                chunk = data[j: j + length]
                try:
                    s = chunk.decode('utf-8')
                    if all(32 <= ord(c) < 127 or ord(c) > 0x200 for c in s):
                        if not s.startswith(('http', 'www.')) and len(s) >= 2:
                            names.append(s)
                    i = j + length
                    continue
                except Exception:
                    pass
        
        i += 1
    
    # Extract numeric IDs (varint fields that look like match IDs)
    # Match IDs are typically 6-10 digit numbers
    match_ids = re.findall(rb'[\x08\x10\x18\x20][\x01-\x09][\x00-\xff]{1,4}', data)
    
    print(f"[DEBUG] Protobuf: {len(names)} names, {len(stream_urls)} stream URLs, "
          f"{len(m3u8_urls)} m3u8 URLs", file=sys.stderr, flush=True)
    
    # Print first few names for debugging
    if names:
        print(f"[DEBUG] Names sample: {names[:10]}", file=sys.stderr, flush=True)
    
    # Build match entries from available data
    # Strategy: pair names (home vs away) with stream URLs
    if stream_urls or m3u8_urls:
        all_urls = stream_urls + m3u8_urls
        for idx, url in enumerate(all_urls):
            # Try to find team names near this URL in the binary
            name = f"Match {idx + 1}"
            if idx < len(names):
                name = names[idx]
            matches.append({"name": name, "url": url, "id": idx})
    
    # If no URLs found, return names as entries (for debugging)
    if not matches and names:
        print(f"[WARN] No stream URLs in protobuf. Names found: {names[:20]}",
              file=sys.stderr, flush=True)
    
    return matches


def get_match_detail(md5, match_id, sport_type):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    content = decode_api_response(resp)
    result = try_json_or_protobuf(content)
    
    if result["type"] == "json":
        return result["data"]
    
    # Protobuf: extract stream URL directly
    return {"protobuf": content}


def extract_stream_url(data) -> str | None:
    """Extract stream URL from JSON dict or protobuf bytes."""
    if isinstance(data, dict):
        if "protobuf" in data:
            content = data["protobuf"]
        else:
            json_str = json.dumps(data)
            m = re.search(r'(https?://[^"\s]+/cfall/[^"\s]+/super/[^"\s]+)', json_str)
            if m:
                return m.group(1)
            m = re.search(r'(https?://[^"\s]+\.m3u8[^"\s]*)', json_str)
            if m:
                return m.group(1)
            m = re.search(r'(https?://[^"\s]+/super/[^"\s]+)', json_str)
            if m:
                return m.group(1)
            m = re.search(
                r'"(?:streamUrl|playUrl|hlsUrl|videoUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"',
                json_str)
            if m:
                return m.group(1)
            return None
    else:
        content = data

    # Search binary for URLs
    m = re.search(rb'(https?://[^/\x00-\x1f\x7f-\xff]+/cfall/[^/\x00-\x1f\x7f-\xff]+/super/[^"\x00-\x1f\x7f-\xff]+)', content)
    if m:
        return m.group().decode()
    m = re.search(rb'(https?://[^"\x00-\x1f\x7f-\xff]+\.m3u8[^"\x00-\x1f\x7f-\xff]*)', content)
    if m:
        return m.group().decode()
    m = re.search(rb'(https?://[^/\x00-\x1f\x7f-\xff]+/super/[^"\x00-\x1f\x7f-\xff]+)', content)
    if m:
        return m.group().decode()
    
    return None


def get_match_name(detail_data, match_data) -> str:
    if isinstance(match_data, dict) and match_data.get("name"):
        return match_data["name"]
    if isinstance(detail_data, dict):
        for key in ["title", "name", "matchName", "event"]:
            if detail_data.get(key) and isinstance(detail_data[key], str):
                return detail_data[key]
    return "Unknown"


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
        for f in as_completed(futures, timeout=10):
            try:
                result = f.result(timeout=2)
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
        match_id = match.get("id")
        sport_type = match.get("sportType", SPORT_TYPE)
        
        # If we already have URL from protobuf parsing
        if match.get("url"):
            return (match.get("name", "Unknown"), match["url"])
        
        if not match_id:
            return None

        detail = get_match_detail(md5, match_id, sport_type=sport_type)
        stream_url = extract_stream_url(detail)

        if not stream_url:
            stream_url = get_stream_from_live_api(md5, match_id, sport_type)

        name = get_match_name(detail, match)
        return (name, stream_url)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(fetch_one, m)
                   for m in matches if isinstance(m, dict)]
        for f in as_completed(futures, timeout=TIMEOUT * 4):
            try:
                result = f.result(timeout=TIMEOUT * 2)
                if result and result[1]:
                    entries.append(result)
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

    print(f"\n{'='*55}", flush=True)
    print(m3u_content, flush=True)
    print(f"{'='*55}", flush=True)


if __name__ == "__main__":
    main()   
