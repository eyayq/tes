#!/usr/bin/env python3
"""
RBTV Proxy Server (v5)
HLS stream yang disamarkan (URL .json, content-type application/zip)
Sebenarnya: playlist = m3u8, segment = MP2T
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

BASE_URL = "https://apis-data10.tcpwe138stya.ru"
PORT = 8080
MAX_MATCHES = 100
TIMEOUT = 10

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36",
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

def decode_response(resp):
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


# ==================== API ====================

def get_signing_key(sport_type=0):
    url = f"{BASE_URL}/api/common/bs?code=100&sportType={sport_type}&stream=true"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    m = re.search(r'[a-f0-9]{32}', resp.text)
    if not m:
        m = re.search(rb'[a-f0-9]{32}', resp.content)
        return m.group().decode() if m else None
    return m.group()


def get_live_matches(md5, sport_type=0, language=34):
    url = f"{BASE_URL}/sfver{md5}/api/match/live?sportType={sport_type}&language={language}&stream=true"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    data = json.loads(decode_response(resp))
    if isinstance(data, dict):
        return data.get("data") or data.get("list") or data.get("matches") or data.get("items") or []
    return data


def get_match_detail(md5, match_id, sport_type=1, language=34):
    url = (f"{BASE_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={language}&stream=true")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    return json.loads(decode_response(resp))


# ==================== EXTRACT ====================

def extract_stream_url(detail_data):
    """
    Extract playlist URL dari match detail.
    URL-nya .json tapi isinya m3u8 (disamarkan).
    """
    json_str = json.dumps(detail_data)

    # Pattern: /cfall/.../super/... (URL .json yang sebenarnya m3u8)
    m = re.search(r'(https?://[^"]+/cfall/[^"]+/super/[^"]+)', json_str)
    if m:
        return m.group(1)

    # Fallback: .m3u8 langsung
    m = re.search(r'(https?://[^"]+?\.m3u8)', json_str)
    if m:
        return m.group(1)

    # Fallback: /super/
    m = re.search(r'(https?://[^"]+/super/[^"]+)', json_str)
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

def rewrite_m3u8(m3u8_content, base_url, port):
    """Rewrite segment URLs agar lewat proxy."""
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
                body = (f"<h1>RBTV Proxy v5</h1>"
                        f"<p><a href='/playlist.m3u'>Playlist</a></p>"
                        f"<p>Max: {MAX_MATCHES} | Timeout: {TIMEOUT}s</p>")
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
        md5 = get_signing_key(0)
        if not md5:
            self.send_error(500, "Gagal ambil signing key")
            return

        matches = get_live_matches(md5, sport_type=0)
        if not isinstance(matches, list):
            matches = []
        matches = matches[:MAX_MATCHES]

        entries = []

        def fetch_one(match):
            match_id = match.get("id") or match.get("matchId") or match.get("eventId")
            sport_type = match.get("sportType", 1)
            if not match_id:
                return None
            detail = get_match_detail(md5, match_id, sport_type=sport_type)
            stream_url = extract_stream_url(detail)
            name = get_match_name(detail, match)
            return (name, stream_url)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(fetch_one, m) for m in matches if isinstance(m, dict)]
            for f in as_completed(futures, timeout=TIMEOUT * 3):
                try:
                    result = f.result(timeout=TIMEOUT)
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
        Fetch "playlist" (URL .json, content-type application/zip)
        → isinya sebenarnya m3u8 → rewrite segment URLs
        """
        playlist_url = params.get("url", [""])[0]
        if not playlist_url:
            self.send_error(400, "Missing url")
            return

        resp = requests.get(playlist_url, headers=SEG_HEADERS, timeout=TIMEOUT)

        # Content-nya m3u8 (bukan zip), tinggal decode encoding
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
        tapi isinya MP2T → return sebagai video/mp2t
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
    print("  RBTV Proxy Server (v5)", flush=True)
    print(f"  Port: {PORT} | Max: {MAX_MATCHES} | Timeout: {TIMEOUT}s", flush=True)
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
