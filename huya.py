#!/usr/bin/env python3
"""
RBTV Proxy Server (v6 FINAL)
- Data API + Live API (dual source)
- HLS stream disamarkan (URL .json, content-type application/zip)
- Parallel fetch, timeout, ThreadingHTTPServer
"""

import requests
import json
import gzip
import re
import time
import zlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
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

PORT = 8080
MAX_MATCHES = 50
TIMEOUT = 15
SPORT_TYPE = 1        # 1 = football (default site)
LANGUAGE = 34         # Indonesian

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "accept-encoding": "identity",
}

SEG_HEADERS = {
    "user-agent": HEADERS["user-agent"],
    "referer": "https://mimi01eo.fut0newsiryroquite.cfd/",
    "origin": "null",
    "accept": "*/*",
    "accept-encoding": "identity",
}


# ==================== DECODE ====================

def decode_bytes(content: bytes) -> bytes:
    """Decompress based on magic bytes."""
    if content[:2] == b'\x1f\x8b':
        return gzip.decompress(content)
    if content[:2] == b'PK':
        return content  # ZIP passthrough
    if content[:4] == b'\x28\xb5\x2f\xfd':
        if HAS_ZSTD:
            return zstd.ZstdDecompressor().decompress(content)
        return content
    return content


def decode_response(resp) -> str:
    content = resp.content
    enc = resp.headers.get("content-encoding", "").lower()
    if enc == "gzip" or content[:2] == b'\x1f\x8b':
        content = gzip.decompress(content)
    elif enc == "br" and HAS_BROTLI:
        content = brotli.decompress(content)
    elif enc == "deflate":
        try:
            content = zlib.decompress(content)
        except Exception:
            content = zlib.decompress(content, -zlib.MAX_WBITS)
    elif enc == "zstd" and HAS_ZSTD:
        content = zstd.ZstdDecompressor().decompress(content)
    return content.decode("utf-8", errors="replace")


# ==================== API: DATA ====================

def get_signing_key(sport_type=SPORT_TYPE):
    """Get MD5 signing key from Data API."""
    url = f"{DATA_URL}/api/common/bs?code=100&sportType={sport_type}&stream=true"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    m = re.search(r'[a-f0-9]{32}', resp.text)
    if not m:
        m = re.search(rb'[a-f0-9]{32}', resp.content)
        return m.group().decode() if m else None
    return m.group()


def get_live_matches(md5, sport_type=SPORT_TYPE):
    """Get live match list from Data API."""
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    data = json.loads(decode_response(resp))
    if isinstance(data, dict):
        return (data.get("data") or data.get("list")
                or data.get("matches") or data.get("items") or [])
    return data if isinstance(data, list) else []


def get_match_detail(md5, match_id, sport_type=SPORT_TYPE):
    """Get match detail from Data API."""
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    return json.loads(decode_response(resp))


# ==================== API: LIVE (STREAM) ====================

def get_stream_from_live_api(md5, match_id, sport_type=SPORT_TYPE):
    """
    Try to get stream URL from Live API.
    Tries multiple endpoint patterns.
    """
    endpoints = [
        f"{LIVE_URL}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL}/sfver{md5}/api/match/stream?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL}/sfver{md5}/api/match/detail?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true",
        f"{LIVE_URL_EU}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 200:
                data = json.loads(decode_response(resp))
                stream_url = extract_stream_url(data)
                if stream_url:
                    return stream_url
        except Exception:
            continue
    return None


# ==================== EXTRACT ====================

def extract_stream_url(data):
    """Extract stream/playlist URL from any JSON response."""
    json_str = json.dumps(data)

    # Pattern 1: /cfall/.../super/...
    m = re.search(r'(https?://[^"\s]+/cfall/[^"\s]+/super/[^"\s]+)', json_str)
    if m:
        return m.group(1)

    # Pattern 2: .m3u8
    m = re.search(r'(https?://[^"\s]+\.m3u8[^"\s]*)', json_str)
    if m:
        return m.group(1)

    # Pattern 3: /super/...
    m = re.search(r'(https?://[^"\s]+/super/[^"\s]+)', json_str)
    if m:
        return m.group(1)

    # Pattern 4: streamUrl / playUrl / hlsUrl fields
    m = re.search(r'"(?:streamUrl|playUrl|hlsUrl|videoUrl|liveUrl)"\s*:\s*"(https?://[^"]+)"', json_str)
    if m:
        return m.group(1)

    return None


def get_match_name(detail_data, match_data):
    for src in [detail_data, match_data]:
        if not isinstance(src, dict):
            continue
        for key in ["title", "name", "matchName", "event"]:
            if src.get(key) and isinstance(src[key], str):
                return src[key]
        home = src.get("homeTeam") or src.get("home") or ""
        away = src.get("awayTeam") or src.get("away") or ""
        if isinstance(home, dict):
            home = home.get("name", "")
        if isinstance(away, dict):
            away = away.get("name", "")
        if home and away:
            return f"{home} vs {away}"
    return "Unknown"


# ==================== M3U REWRITER ====================

def rewrite_m3u8(m3u8_content: str, base_url: str, port: int) -> str:
    """Rewrite segment URLs to go through proxy."""
    lines = m3u8_content.strip().split("\n")
    rewritten = []
    parsed_base = urlparse(base_url)

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            rewritten.append(line)
        elif line.startswith("http"):
            rewritten.append(f"http://localhost:{port}/seg?url={quote(line, safe='')}")
        else:
            if line.startswith("/"):
                seg_url = f"{parsed_base.scheme}://{parsed_base.netloc}{line}"
            else:
                base_path = parsed_base.path.rsplit("/", 1)[0]
                seg_url = f"{parsed_base.scheme}://{parsed_base.netloc}{base_path}/{line}"
            rewritten.append(f"http://localhost:{port}/seg?url={quote(seg_url, safe='')}")

    return "\n".join(rewritten) + "\n"


# ==================== HTTP HANDLER ====================

class RBTVProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        try:
            if path == "/playlist.m3u":
                self.handle_playlist()
            elif path == "/hls":
                self.handle_hls(params)
            elif path == "/seg":
                self.handle_segment(params)
            elif path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                body = (f"<h1>RBTV Proxy v6</h1>"
                        f"<p><a href='/playlist.m3u'>Playlist</a></p>"
                        f"<p>Max: {MAX_MATCHES} | Timeout: {TIMEOUT}s | Sport: {SPORT_TYPE}</p>")
                self.wfile.write(body.encode())
            else:
                self.send_error(404)
        except Exception as e:
            print(f"[ERROR] {path}: {e}", flush=True)
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

    def handle_playlist(self):
        md5 = get_signing_key(SPORT_TYPE)
        if not md5:
            self.send_error(500, "Gagal ambil signing key")
            return

        matches = get_live_matches(md5, sport_type=SPORT_TYPE)
        matches = matches[:MAX_MATCHES]

        entries = []

        def fetch_one(match):
            match_id = (match.get("id") or match.get("matchId")
                        or match.get("eventId"))
            sport_type = match.get("sportType", SPORT_TYPE)
            if not match_id:
                return None

            # 1) Try Data API detail
            detail = get_match_detail(md5, match_id, sport_type=sport_type)
            stream_url = extract_stream_url(detail)

            # 2) Fallback: try Live API
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
                    print(f"[WARN] {e}", flush=True)

        lines = ["#EXTM3U"]
        for name, stream_url in entries:
            tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
            lines.append(f"http://localhost:{PORT}/hls?url={quote(stream_url, safe='')}")

        body = "\n".join(lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def handle_hls(self, params):
        """
        Fetch playlist (URL .json, content-type application/zip)
        → isinya m3u8 → rewrite segment URLs
        """
        playlist_url = params.get("url", [""])[0]
        if not playlist_url:
            self.send_error(400, "Missing url")
            return

        resp = requests.get(playlist_url, headers=SEG_HEADERS, timeout=TIMEOUT)

        # Decode content (m3u8 disamarkan sebagai zip)
        content = resp.content
        enc = resp.headers.get("content-encoding", "").lower()
        if enc == "gzip" or content[:2] == b'\x1f\x8b':
            content = gzip.decompress(content)
        elif enc == "br" and HAS_BROTLI:
            content = brotli.decompress(content)
        elif enc == "deflate":
            try:
                content = zlib.decompress(content)
            except Exception:
                content = zlib.decompress(content, -zlib.MAX_WBITS)

        m3u8_text = content.decode("utf-8", errors="replace")

        # Kalau bukan m3u8 valid, coba langsung return
        if not m3u8_text.strip().startswith("#EXTM3U"):
            print(f"[WARN] Response bukan m3u8: {m3u8_text[:200]}", flush=True)

        rewritten = rewrite_m3u8(m3u8_text, playlist_url, PORT)
        body = rewritten.encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def handle_segment(self, params):
        """
        Proxy segment: content-type asal application/zip,
        isinya MP2T → return sebagai video/mp2t
        """
        seg_url = params.get("url", [""])[0]
        if not seg_url:
            self.send_error(400, "Missing url")
            return

        resp = requests.get(seg_url, headers=SEG_HEADERS, timeout=TIMEOUT)

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(resp.content)))
        self.send_header("Cache-Control", "max-age=120")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp.content)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {format % args}", flush=True)


# ==================== MAIN ====================

def main():
    print("=" * 55, flush=True)
    print("  RBTV Proxy Server (v6 FINAL)", flush=True)
    print(f"  Port: {PORT}", flush=True)
    print(f"  Data API: {DATA_URL}", flush=True)
    print(f"  Live API: {LIVE_URL}", flush=True)
    print(f"  Max matches: {MAX_MATCHES} | Timeout: {TIMEOUT}s", flush=True)
    print(f"  Sport: {SPORT_TYPE} | Language: {LANGUAGE}", flush=True)
    print("=" * 55, flush=True)
    print(flush=True)
    print(f"  ▶ http://localhost:{PORT}/playlist.m3u", flush=True)
    print(f"  (Ctrl+C untuk stop)", flush=True)
    print(flush=True)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), RBTVProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()   
