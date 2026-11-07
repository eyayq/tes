#!/usr/bin/env python3
"""
RBTV HLS Proxy Server (FINAL v3)
Handle: .json segments → raw video bytes
"""

import requests
import json
import gzip
import re
import time
import zlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

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

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.rbtvplus.ws",
    "referer": "https://www.rbtvplus.ws/",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
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
        except:
            content = zlib.decompress(content, -zlib.MAX_WBITS)
    elif enc == "zstd" and HAS_ZSTD:
        content = zstd.ZstdDecompressor().decompress(content)
    return content.decode("utf-8", errors="replace")

# ==================== API ====================

def get_signing_key(sport_type=0):
    url = f"{BASE_URL}/api/common/bs?code=100&sportType={sport_type}&stream=true"
    resp = requests.get(url, headers=HEADERS)
    m = re.search(r'[a-f0-9]{32}', resp.text)
    if not m:
        m = re.search(rb'[a-f0-9]{32}', resp.content)
        return m.group().decode() if m else None
    return m.group()

def get_live_matches(md5, sport_type=0, language=34):
    url = f"{BASE_URL}/sfver{md5}/api/match/live?sportType={sport_type}&language={language}&stream=true"
    resp = requests.get(url, headers=HEADERS)
    data = json.loads(decode_response(resp))
    if isinstance(data, dict):
        return data.get("data") or data.get("list") or data.get("matches") or data.get("items") or []
    return data

def get_match_detail(md5, match_id, sport_type=1, language=34):
    url = (f"{BASE_URL}/sfver{md5}/api/match/detail"
           f"?matchId={match_id}&sportType={sport_type}&language={language}&stream=true")
    resp = requests.get(url, headers=HEADERS)
    return json.loads(decode_response(resp))

def extract_m3u8_url(detail_data):
    json_str = json.dumps(detail_data)
    m = re.search(r'(https?://[^"]+?\.m3u8)', json_str)
    if m:
        return m.group(1)
    m = re.search(r'(https?://[^"]+?/token-[^"]+?/cfall/[^"]+?/super/[^"]+)', json_str)
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
        if isinstance(home, dict): home = home.get("name", "")
        if isinstance(away, dict): away = away.get("name", "")
        if home and away:
            return f"{home} vs {away}"
    return "Unknown"

# ==================== M3U REWRITER ====================

def rewrite_m3u8(m3u8_content, base_url, port):
    """Rewrite segment URLs agar lewat proxy"""
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
            # Relative → absolute
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
                self.wfile.write(f"<h1>RBTV Proxy</h1><p><a href='/playlist.m3u'>Playlist</a></p>".encode())
            else:
                self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))
    
    def handle_playlist(self):
        md5 = get_signing_key(0)
        if not md5:
            self.send_error(500, "Gagal ambil signing key")
            return
        
        matches = get_live_matches(md5, sport_type=0)
        lines = ["#EXTM3U"]
        
        for match in matches:
            if not isinstance(match, dict):
                continue
            match_id = match.get("id") or match.get("matchId") or match.get("eventId")
            sport_type = match.get("sportType", 1)
            if not match_id:
                continue
            try:
                detail = get_match_detail(md5, match_id, sport_type=sport_type)
                m3u8_url = extract_m3u8_url(detail)
                name = get_match_name(detail, match)
                if m3u8_url:
                    tvg_id = re.sub(r'[^a-zA-Z0-9_]', '_', name)
                    lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}",{name}')
                    lines.append(f"http://localhost:{PORT}/hls?url={quote(m3u8_url, safe='')}")
                time.sleep(0.3)
            except:
                pass
        
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
        
        headers = {
            "user-agent": HEADERS["user-agent"],
            "referer": "https://mimi01eo.fut0newsiryroquite.cfd/",
            "origin": "null",
            "accept": "*/*",
            "accept-encoding": "identity",
        }
        
        resp = requests.get(m3u8_url, headers=headers, timeout=10)
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
        """
        Proxy segment: fetch .json → return raw bytes sebagai video/mp2t
        (Content-type asli application/zip, tapi isinya video)
        """
        seg_url = params.get("url", [""])[0]
        if not seg_url:
            self.send_error(400, "Missing url")
            return
        
        headers = {
            "user-agent": HEADERS["user-agent"],
            "referer": "https://mimi01eo.fut0newsiryroquite.cfd/",
            "origin": "null",
            "accept": "*/*",
            "accept-encoding": "identity",
        }
        
        resp = requests.get(seg_url, headers=headers, timeout=10)
        
        # Return raw bytes sebagai video
        # Player (VLC/mpv) akan interpret sebagai MP2T
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(resp.content)))
        self.send_header("Cache-Control", "max-age=120")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp.content)
    
    def log_message(self, format, *args):
        pass


# ==================== MAIN ====================

def main():
    print("=" * 55)
    print("  RBTV HLS Proxy Server (v3 FINAL)")
    print(f"  Port: {PORT}")
    print("=" * 55)
    print(f"\n  ▶ Buka di VLC/mpv/TiviMate:")
    print(f"    http://localhost:{PORT}/playlist.m3u")
    print(f"\n  (Ctrl+C untuk stop)\n")
    
    server = HTTPServer(("0.0.0.0", PORT), RBTVProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()

if __name__ == "__main__":
    main()   
