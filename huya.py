#!/usr/bin/env python3
"""
RBTV Proxy Server (v7 FINAL)
- Dual API: Data + Live (parallel)
- HLS stream disamarkan (URL .json, content-type application/zip)
- Fast: parallel endpoints, short timeouts
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

SEG_HEADERS = {
    "user-agent": HEADERS["user-agent"],
    "referer": "https://mimi01eo.fut0newsiryroquite.cfd/",
    "origin": "null",
    "accept": "*/*",
    "accept-encoding": "identity",
}


# ==================== DECODE ====================

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
    url = f"{DATA_URL}/api/common/bs?code=100&sportType={sport_type}&stream=true"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    m = re.search(r'[a-f0-9]{32}', resp.text)
    if not m:
        m = re.search(rb'[a-f0-9]{32}', resp.content)
        return m.group().decode() if m else None
    return m.group()


def get_live_matches(md5, sport_type=SPORT_TYPE):
    url = (f"{DATA_URL}/sfver{md5}/api/match/live"
           f"?sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    data = json.loads(decode_response(resp))
    if isinstance(data, dict):
        return (data.get("data") or data.get("list")
                or data.get("matches") or data.get("items") or [])
    return data if isinstance(data, list) else []


def get_match_detail(md5, match_id, sport_type=SPORT_TYPE):
    url = (f"{DATA_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={LANGUAGE}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    return json.loads(decode_response(resp))


# ==================== API: LIVE (STREAM) ====================

def get_stream_from_live_api(md5, match_id, sport_type=SPORT_TYPE):
    """Try Live API endpoints — PARALLEL, bukan sequential."""
    endpoints = [
        f"{LIVE_URL}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL}/sfver{md5}/api/match/stream?matchId={match_id}&sportType={sport_type}&stream=true",
        f"{LIVE_URL_EU}/sfver{md5}/api/stream/live?matchId={match_id}&sportType={sport_type}&stream=true",
    ]

    def try_url(url):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=LIVE_TIMEOUT)
            if resp.status_code == 200:
                data = json.loads(decode_response(resp))
                return extract_stream_url(data)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = [pool.submit(try_url, url) for url in endpoints]
        for f in as_completed(futures, timeout=10):
            try:
                result = f.result(timeout=2)
                if result:
                    return result
            except Exception:
                continue
    return None


# ==================== EXTRACT ====================

def extract_stream_url(data):
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
                body = (f"<h1>RBTV Proxy v7</h1>"
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
        t0 = time.time()

        md5 = get_signing_key(SPORT_TYPE)
        if not md5:
            self.send_error(500, "Gagal ambil signing key")
            return

        matches = get_live_matches(md5, sport_type=SPORT_TYPE)
        matches = matches[:MAX_MATCHES]
        print(f"[INFO] {len(matches)} matches found, fetching details...", flush=True)

        entries = []

        def fetch_one(match):
            match_id = (match.get("id") or match.get("matchId")
                        or match.get("eventId"))
            sport_type = match.get("sportType", SPORT_TYPE)
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
            for f in as_completed(futures, timeout=TIMEOUT * 3):
                try:
                    result = f.result(timeout=TIMEOUT * 2)
                    if result and result[1]:
                        entries.append(result)
                except Exception as e:
                    print(f"[WARN] {e}", flush=True)

        elapsed = time.time() - t0
        print(f"[INFO] Playlist ready: {len(entries)} streams in {elapsed:.1f}s", flush=True)

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
        playlist_url = params.get("url", [""])[0]
        if not playlist_url:
            self.send_error(400, "Missing url")
            return

        resp = requests.get(playlist_url, headers=SEG_HEADERS, timeout=TIMEOUT)

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
    print("  RBTV Proxy Server (v7 FINAL)", flush=True)
    print(f"  Port: {PORT}", flush=True)
    print(f"  Data API: {DATA_URL}", flush=True)
    print(f"  Live API: {LIVE_URL}", flush=True)
    print(f"  Max: {MAX_MATCHES} | Timeout: {TIMEOUT}s | Live: {LIVE_TIMEOUT}s", flush=True)
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
