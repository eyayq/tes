#!/usr/bin/env python3
"""
hls-proxy.py — Single-file HLS restream proxy with residential proxy upstream.
Zero dependencies (stdlib only).

Usage:
    python3 hls-proxy.py

Env vars (opsional, override default):
    HLS_PORT        - Listen port (default 8089)
    HLS_HOST        - Bind address (default 0.0.0.0)
    TOKEN_TTL       - Cache TTL in seconds (default 1700)
    CHANNELS_CONF   - Path to channels.conf (default ./channels.conf)
"""

import os
import re
import sys
import time
import random
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin
from pathlib import Path
from datetime import datetime, timezone

# ─── Config ───────────────────────────────────────────────────────────────────
PORT        = int(os.environ.get("HLS_PORT", "8089"))
HOST        = os.environ.get("HLS_HOST", "0.0.0.0")
TOKEN_TTL   = int(os.environ.get("TOKEN_TTL", "1700"))
CONF_FILE   = os.environ.get("CHANNELS_CONF", "./channels.conf")
ALLOWED_IPS = os.environ.get("HLS_ALLOWED_IPS", "").split(",")

# ── Residential Proxy ──
PROXY_HOST   = "64.137.96.74"
PROXY_PORT   = "6641"
PROXY_USER   = "svr00000"
PROXY_PASS   = "bhns0000"
PROXY_URL    = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

UPSTREAM_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("hls-proxy")

# ─── Proxy Opener ─────────────────────────────────────────────────────────────
def build_opener() -> urllib.request.OpenerDirector:
    handler = urllib.request.ProxyHandler({
        "http": PROXY_URL,
        "https": PROXY_URL,
    })
    return urllib.request.build_opener(handler)


# ─── Channel Config ───────────────────────────────────────────────────────────
# channels.conf format (pipe-delimited):
#   slug|name|logo|group|source_url|mode
#   mode: "iframe" or "direct"

channels: dict[str, dict] = {}
channels_lock = threading.Lock()


def load_channels():
    global channels
    path = Path(CONF_FILE)
    if not path.exists():
        log.error("CHANNELS_CONF not found: %s", CONF_FILE)
        return

    chs = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        slug, name, logo, group, source_url = parts[0], parts[1], parts[2], parts[3], parts[4]
        mode = parts[5].strip() if len(parts) > 5 else "iframe"
        chs[slug] = {
            "name": name,
            "logo": logo,
            "group": group,
            "source_url": source_url,
            "mode": mode,
        }

    with channels_lock:
        channels = chs
    log.info("Loaded %d channels from %s", len(chs), CONF_FILE)


# ─── Stream URL Cache ─────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def get_cached(slug: str) -> str | None:
    with _cache_lock:
        entry = _cache.get(slug)
        if entry and (time.time() - entry["ts"]) < TOKEN_TTL:
            return entry["url"]
    return None


def set_cache(slug: str, url: str):
    with _cache_lock:
        _cache[slug] = {"url": url, "ts": time.time()}
    log.info("Cached stream for '%s' (TTL %ds)", slug, TOKEN_TTL)


def invalidate_cache(slug: str):
    with _cache_lock:
        _cache.pop(slug, None)
    log.info("Invalidated cache for '%s'", slug)


# ─── Fetch via Proxy ──────────────────────────────────────────────────────────
def fetch_url(url: str, headers: dict | None = None, timeout: int = 15) -> tuple[int, bytes]:
    """Fetch a URL via residential proxy. Returns (status_code, body)."""
    opener = build_opener()

    req = urllib.request.Request(url)
    hdrs = {
        "User-Agent": UPSTREAM_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.5",
    }
    if headers:
        hdrs.update(headers)
    for k, v in hdrs.items():
        req.add_header(k, v)

    try:
        resp = opener.open(req, timeout=timeout)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        log.warning("Fetch failed: %s → %s", url[:80], e)
        return 0, b""


# ─── Scrape Fresh Stream URL ──────────────────────────────────────────────────
def extract_m3u8_from_html(html: str, base_url: str) -> str | None:
    """Extract m3u8 URL from HTML (direct or via iframe)."""
    # Pattern 1: direct m3u8 in HTML
    m = re.search(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', html)
    if m:
        return m.group(1)

    # Pattern 2: JS variable
    m = re.search(r'(?:hls_url|stream_url|video_url|src)\s*[:=]\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html)
    if m:
        return m.group(1)

    # Pattern 3: iframe → fetch iframe → look for m3u8
    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
    if m:
        iframe_src = m.group(1)
        if not iframe_src.startswith("http"):
            iframe_src = urljoin(base_url, iframe_src)
        log.info("  Iframe: %s", iframe_src[:100])

        code, body = fetch_url(iframe_src, headers={"Referer": base_url})
        if code == 200 and body:
            html2 = body.decode("utf-8", errors="replace")
            m2 = re.search(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', html2)
            if m2:
                return m2.group(1)
            m2 = re.search(r'(?:hls_url|stream_url|video_url|src)\s*[:=]\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html2)
            if m2:
                return m2.group(1)

    return None


def scrape_stream_url(slug: str, source_url: str, mode: str) -> str | None:
    """Scrape a fresh m3u8 URL from the source page via residential proxy."""
    log.info("Scraping '%s' → %s", slug, source_url)
    code, body = fetch_url(source_url)

    if code != 200 or not body:
        log.warning("  Scrape failed: HTTP %d", code)
        return None

    html = body.decode("utf-8", errors="replace")
    url = extract_m3u8_from_html(html, source_url)

    if url:
        log.info("  ✓ Found: %s", url[:100])
    else:
        log.warning("  ✗ No m3u8 found in %s", source_url[:80])

    return url


# ─── M3U8 Playlist Rewriting ──────────────────────────────────────────────────
def rewrite_playlist(content: str, upstream_url: str, proxy_base: str) -> str:
    """Rewrite all URLs in an m3u8 playlist to go through the proxy."""
    lines = content.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith("#"):
            # Rewrite URI= in EXT tags (encryption keys, etc.)
            if "URI=" in stripped:
                stripped = re.sub(
                    r'URI=["\']([^"\']+)["\']',
                    lambda m: f'URI="{proxy_base}/proxy?url={urllib.parse.quote(m.group(1), safe="")}"',
                    stripped,
                )
                line = stripped
            out.append(line)
        else:
            # Resolve relative → absolute
            if stripped.startswith("http"):
                abs_url = stripped
            else:
                abs_url = urljoin(upstream_url, stripped)
            out.append(f"{proxy_base}/proxy?url={urllib.parse.quote(abs_url, safe='')}")
    return "\n".join(out)


# ─── HTTP Handler ─────────────────────────────────────────────────────────────
class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "HLSProxy/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _allowed(self) -> bool:
        if not ALLOWED_IPS or ALLOWED_IPS == [""]:
            return True
        return self.client_address[0] in ALLOWED_IPS

    def do_GET(self):
        if not self._allowed():
            self.send_response(403)
            self.end_headers()
            return

        parsed = urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/channel/"):
            self._handle_channel(path[len("/channel/"):])
        elif path == "/proxy":
            self._handle_proxy(qs)
        elif path == "/health":
            self._send(200, "OK", "text/plain")
        else:
            self._send(404, "Not found", "text/plain")

    # ── /channel/<slug> → resolve fresh URL, proxy the m3u8 ──
    def _handle_channel(self, slug: str):
        with channels_lock:
            ch = channels.get(slug)
        if not ch:
            self._send(404, f"Unknown channel: {slug}", "text/plain")
            return

        source_url = ch["source_url"]
        mode = ch["mode"]

        # Try cache first
        stream_url = get_cached(slug)

        # Scrape if cache miss or expired
        if not stream_url:
            stream_url = scrape_stream_url(slug, source_url, mode)
            if not stream_url:
                self._send(502, "Failed to scrape stream URL", "text/plain")
                return
            set_cache(slug, stream_url)

        # Fetch the m3u8 via proxy
        opener = build_opener()
        req = urllib.request.Request(stream_url)
        req.add_header("User-Agent", UPSTREAM_UA)
        req.add_header("Referer", source_url + "/")
        req.add_header("Origin", urlparse(source_url).scheme + "://" + urlparse(source_url).netloc)

        try:
            resp = opener.open(req, timeout=10)
            m3u8_body = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            log.warning("  M3U8 fetch failed: %s", e)
            invalidate_cache(slug)
            self._send(502, f"Upstream error: {e}", "text/plain")
            return

        # Rewrite
        host_header = self.headers.get("Host", f"{HOST}:{PORT}")
        scheme = "https" if host_header.startswith("localhost") is False and "443" in host_header else "http"
        proxy_base = f"{scheme}://{host_header}"
        rewritten = rewrite_playlist(m3u8_body, stream_url, proxy_base)

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(rewritten.encode())

    # ── /proxy?url=<encoded> → forward to upstream via residential proxy ──
    def _handle_proxy(self, qs: dict):
        target = qs.get("url", [None])[0]
        if not target:
            self._send(400, "Missing url param", "text/plain")
            return

        opener = build_opener()
        req = urllib.request.Request(target)
        req.add_header("User-Agent", UPSTREAM_UA)

        parsed = urlparse(target)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        req.add_header("Referer", origin + "/")
        req.add_header("Origin", origin)

        try:
            resp = opener.open(req, timeout=30)
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")

            # If it's an m3u8, rewrite it
            if ".m3u8" in target or "mpegurl" in content_type:
                text = body.decode("utf-8", errors="replace")
                host_header = self.headers.get("Host", f"{HOST}:{PORT}")
                scheme = "https" if "443" in host_header else "http"
                proxy_base = f"{scheme}://{host_header}"
                body = rewrite_playlist(text, target, proxy_base).encode()
                content_type = "application/vnd.apple.mpegurl"

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except urllib.error.HTTPError as e:
            if e.code in (403, 410):
                log.info("  Upstream %d — token likely expired", e.code)
            self._send(e.code, f"Upstream error: {e.code}", "text/plain")
        except Exception as e:
            self._send(502, f"Proxy error: {e}", "text/plain")

    def _send(self, code: int, msg: str, ctype: str = "text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(msg.encode())


# ─── Background Token Refresher ───────────────────────────────────────────────
def background_refresher():
    """Periodically refresh all channel tokens before they expire."""
    while True:
        time.sleep(TOKEN_TTL - 60)
        with channels_lock:
            slugs = list(channels.keys())
        for slug in slugs:
            try:
                ch = channels[slug]
                url = scrape_stream_url(slug, ch["source_url"], ch["mode"])
                if url:
                    set_cache(slug, url)
            except Exception as e:
                log.warning("  Refresher error for '%s': %s", slug, e)
        log.info("Background refresh complete (%d channels)", len(slugs))


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    load_channels()

    # Start background refresher
    t = threading.Thread(target=background_refresher, daemon=True)
    t.start()

    server = ThreadingHTTPServer((HOST, PORT), ProxyHandler)
    log.info("HLS Proxy listening on http://%s:%d", HOST, PORT)
    log.info("  Channels: %d", len(channels))
    log.info("  Proxy:    %s:%s (%s)", PROXY_HOST, PROXY_PORT, PROXY_USER)
    log.info("  TTL:      %ds", TOKEN_TTL)
    log.info("")
    log.info("  Player URL: http://%s:%d/channel/<slug>", HOST, PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()   
