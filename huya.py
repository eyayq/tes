#!/usr/bin/env python3
"""
RBTV Proxy Server (v4 FINAL)
- Direct segment proxy (bukan HLS)
- Handle: .json segments → raw video bytes
- Parallel fetch, timeout, ZIP/MP2T detection
"""

import requests
import json
import gzip
import re
import time
import zlib
import io
import zipfile
import threading
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
MAX_MATCHES = 50
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
    """Extract segment/stream URL from match detail."""
    json_str = json.dumps(detail_data)

    # Pattern 1: .m3u8 (HLS)
    m = re.search(r'(https?://[^"]+?\.m3u8)', json_str)
    if m:
        return m.group(1), "hls"

    # Pattern 2: /cfall/.../super/... (direct segment)
    m = re.search(r'(https?://[^"]+/cfall/[^"]+/super/[^"]+)', json_str)
    if m:
        return m.group(1), "segment"

    # Pattern 3: /token-.../cfall/.../super/...
    m = re.search(r'(https?://[^"]+/token-[^"]+/cfall/[^"]+/super/[^"]+)', json_str)
    if m:
        return m.group(1), "segment"

    # Pattern 4: any URL with "super" in path
    m = re.search(r'(https?://[^"]+/super/[^"]+)', json_str)
    if m:
        return m.group(1), "segment"

    return None, None


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


# ==================== SEGMENT DECODE ====================

def decode_segment(content: bytes) -> tuple[bytes, str]:
    """
    Detect and decode segment content.
    Returns (decoded_bytes, content_type)
    """
    # MP2T sync byte
    if len(content) > 0 and content[0] == 0x47:
        return content, "video/mp2t"

    # ZIP (PK magic)
    if content[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Find video file
                video_files = [n for n in zf.namelist()
                               if n.endswith(('.ts', '.mp2t', '.mp4', '.m4v', '.mkv'))]
                if video_files:
                    return zf.read(video_files[0]), "video/mp2t"
                # Fallback: first file
                return zf.read(zf.namelist()[0]), "video/mp2t"
        except zipfile.BadZipFile:
            pass

    # GZIP
    if content[:2] == b'\x1f\x8b':
        content = gzip.decompress(content)
        if content[0] == 0x47:
            return content, "video/mp2t"
        return content, "video/mp2t"

    # Fallback: return as-is
    return content, "video/mp2t"


# ==================== M3U REWRITER (for HLS case) ====================

def rewrite_m3u8(m3u8_content, base_url, port):
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
                body = (f"<h1>RBTV Proxy v4</h1>"
                        f"<p><a href='/playlist.m3u'>Playlist</a></p>"
                        f"<p>Max matches: {MAX_MATCHES}, Timeout: {TIMEOUT}s</p>")
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

        # Parallel fetch match details
        entries = []

        def fetch_one(match):
            match_id = match.get("id") or match.get("matchId") or match.get("eventId")
            sport_type = match.get("sportType", 1)
            if not match_id:
                return None
            detail = get_match_detail(md5, match_id, sport_type=sport_type)
            stream_url, stream_type = extract_stream_url(detail)
            name = get_match_name(detail, match)
            return (name, stream_url, stream_type)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_one, m): m for m in matches if isinstance(m, dict)}
            for f in as_completed(futures, timeout=TIMEOUT * 3):
                try:
                    result = f.result(timeout=TIMEOUT)
                    if result:
                        entries.append(result)
                except Exception as e:
                    print(f"[WARN] match fetch failed: {e}", flush=True)

        # Build playlist
        lines = ["#EXTM3U"]
        for name, stream_url, stream_type in entries:
            if not stream_url:
                continue
            tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            if stream_type == "hls":
                lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
                lines.append(f"http://localhost:{PORT}/hls?url={quote(stream_url, safe='')}")
            else:
                lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
                lines.append(f"http://localhost:{PORT}/seg?url={quote(stream_url, safe='')}")

        body = "\n".join(lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def handle_hls(self, params):
        m3u8_url = params.get("url", [""])[0]
        if not m3u8_url:
            self.send_error(400, "Missing url")
            return

        resp = requests.get(m3u8_url, headers=SEG_HEADERS, timeout=TIMEOUT)
        rewritten = rewrite_m3u8(resp.text, m3u8_url, PORT)

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

        content, content_type = decode_segment(resp.content)

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "max-age=120")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {format % args}", flush=True)


# ==================== MAIN ====================

def main():
    print("=" * 55, flush=True)
    print("  RBTV Proxy Server (v4 FINAL)", flush=True)
    print(f"  Port: {PORT}", flush=True)
    print(f"  Max matches: {MAX_MATCHES}", flush=True)
    print(f"  Timeout: {TIMEOUT}s", flush=True)
    print("=" * 55, flush=True)
    print(flush=True)
    print(f"  ▶ Buka di VLC/mpv/TiviMate:", flush=True)
    print(f"    http://localhost:{PORT}/playlist.m3u", flush=True)
    print(flush=True)
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
