#!/usr/bin/env python3
"""
Kick M3U Scraper
OAuth 2.1 PKCE (User Access Token) + Public API v2
"""

import time
import random
import logging
import argparse
import threading
import hashlib
import base64
import secrets
import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import requests

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"
TOKEN_FILE = "ki.json"
MAX_PER_CATEGORY = 10
MAX_STREAMS = 1000
LANGUAGE = "id"

KICK_CLIENT_ID = os.getenv("KICK_CLIENT_ID", "01M6YCH4D5Y8TGGQAMP25TDJ42")
KICK_CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "a6ba1635efe7f460edcc486675594df1c9156a813ffc93d677841121a43fe239")
REDIRECT_URI = "http://localhost:3100/"
OAUTH_SCOPES = "user:read channel:read"

API_BASE = "https://api.kick.com/public/v2"
OAUTH_BASE = "https://id.kick.com/oauth"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kick-m3u")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Stream:
    channel_slug: str
    title: str
    playback_url: str
    group_title: str
    tvg_logo: str
    started_at: Optional[str]
    viewers: int
    platform: str = "Kick"


# ═══════════════════════════════════════════════════════════════════════════════
# OAUTH 2.1 — PKCE (Authorization Code Grant)
# ═══════════════════════════════════════════════════════════════════════════════

class KickOAuthPKCE:
    """
    User Access Token via Authorization Code + PKCE.
    Flow:
      1. Generate code_verifier + code_challenge
      2. Buka browser → user login & approve
      3. Local server catch redirect → dapat authorization code
      4. Exchange code → access_token + refresh_token
      5. Auto-refresh saat expired
    """

    def __init__(self, client_id: str, client_secret: str,
                 redirect_uri: str = REDIRECT_URI, scopes: str = OAUTH_SCOPES):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0
        self._lock = threading.Lock()

    # ── PKCE Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        """Generate (code_verifier, code_challenge) pair."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return verifier, challenge

    # ── Token Persistence ─────────────────────────────────────────────────────

    def _load_tokens(self) -> bool:
        """Load cached tokens from file."""
        if not os.path.exists(TOKEN_FILE):
            return False
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.expires_at = data.get("expires_at", 0)
            log.info("Token dimuat dari %s", TOKEN_FILE)
            return bool(self.access_token)
        except Exception:
            return False

    def _save_tokens(self):
        """Persist tokens to file."""
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
            }, f, indent=2)

    # ── Interactive Auth (first run) ──────────────────────────────────────────

    def interactive_auth(self) -> bool:
        """
        Run full PKCE flow: open browser, catch callback, exchange code.
        Returns True on success.
        """
        verifier, challenge = self._generate_pkce()
        state = secrets.token_urlsafe(16)

        # Build authorization URL
        auth_url = (
            f"{OAUTH_BASE}/authorize"
            f"?response_type=code"
            f"&client_id={self.client_id}"
            f"&redirect_uri={self.redirect_uri}"
            f"&scope={self.scopes}"
            f"&code_challenge={challenge}"
            f"&code_challenge_method=S256"
            f"&state={state}"
        )

        log.info("🔐 Membuka browser untuk authorization...")
        log.info("   Jika tidak terbuka, buka manual:\n   %s", auth_url)

        # Start local callback server
        auth_result: dict = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return

                params = parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                returned_state = params.get("state", [None])[0]

                if code and returned_state == state:
                    auth_result["code"] = code
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<h1>✅ Berhasil! Kembali ke terminal.</h1>".encode("utf-8")
                    )
                    log.info("   Callback diterima: code=%s...", code[:20])
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<h1>❌ Error: state mismatch atau code kosong</h1>".encode("utf-8")
                    )
                    log.error("   Callback error: code=%s, state_match=%s",
                              bool(code), returned_state == state)

            def log_message(self, *args):
                pass  # Suppress default logging

        server = HTTPServer(("localhost", 3100), CallbackHandler)
        server.timeout = 300  # 5 menit timeout

        # Open browser
        webbrowser.open(auth_url)

        # Wait for callback (block until we get the code or timeout)
        log.info("   Menunggu authorization (max 5 menit)...")
        while "code" not in auth_result:
            server.handle_request()

        server.server_close()

        # Exchange code for tokens
        return self._exchange_code(auth_result["code"], verifier)

    def _exchange_code(self, code: str, code_verifier: str) -> bool:
        """Exchange authorization code for access + refresh tokens."""
        try:
            r = requests.post(
                f"{OAUTH_BASE}/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": code_verifier,
                    "code": code,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                log.error("Token exchange gagal: %s - %s", r.status_code, r.text[:300])
                return False

            data = r.json()
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token")
            self.expires_at = time.time() + data.get("expires_in", 3600)
            self._save_tokens()

            log.info("✅ Token berhasil! (exp: %ds, refresh: %s)",
                     data.get("expires_in", "?"), "ya" if self.refresh_token else "tidak")
            return True

        except Exception as e:
            log.error("Token exchange error: %s", e)
            return False

    # ── Refresh Token ─────────────────────────────────────────────────────────

    def _refresh(self) -> bool:
        """Refresh access token using refresh_token."""
        if not self.refresh_token:
            return False

        try:
            r = requests.post(
                f"{OAUTH_BASE}/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                log.error("Refresh gagal: %s - %s", r.status_code, r.text[:200])
                return False

            data = r.json()
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self.expires_at = time.time() + data.get("expires_in", 3600)
            self._save_tokens()

            log.info("Token di-refresh (exp: %ds)", data.get("expires_in", "?"))
            return True

        except Exception as e:
            log.error("Refresh error: %s", e)
            return False

    # ── Public: Get Valid Token ───────────────────────────────────────────────

    def get_token(self) -> Optional[str]:
        """
        Get a valid access token.
        Priority: memory cache → file cache → refresh → interactive auth.
        """
        with self._lock:
            # 1. Memory cache still valid
            if self.access_token and time.time() < self.expires_at - 60:
                return self.access_token

            # 2. Try load from file
            if not self.access_token:
                self._load_tokens()
                if self.access_token and time.time() < self.expires_at - 60:
                    return self.access_token

            # 3. Try refresh
            if self.refresh_token:
                log.info("Token expired, mencoba refresh...")
                if self._refresh():
                    return self.access_token

            # 4. Fallback: interactive auth (first run or refresh failed)
            log.warning("⚠️  Tidak ada token valid. Jalankan interactive auth...")
            if self.interactive_auth():
                return self.access_token

            return None


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

_local = threading.local()
_oauth: Optional[KickOAuthPKCE] = None


def get_oauth() -> KickOAuthPKCE:
    global _oauth
    if _oauth is None:
        _oauth = KickOAuthPKCE(KICK_CLIENT_ID, KICK_CLIENT_SECRET)
    return _oauth


def get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


def api_get(path: str, params: dict | None = None) -> Optional[dict | list]:
    """
    GET request ke api.kick.com/public/v2 dengan Bearer token.
    Auto-retry on 429, auto-refresh on 401.
    """
    oauth = get_oauth()
    token = oauth.get_token()
    if not token:
        return None

    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, headers=headers,
                                  timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                log.warning("401: Token expired, refresh...")
                oauth.access_token = None
                oauth.expires_at = 0
                token = oauth.get_token()
                if not token:
                    return None
                headers["Authorization"] = f"Bearer {token}"
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429 rate limit! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                log.debug("HTTP %s: %s", r.status_code, r.text[:200])
                time.sleep(1)
        except Exception as e:
            log.debug("Error: %s", e)
            time.sleep(2)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# KICK API
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_categories() -> list[dict]:
    """Fetch categories dari /public/v2/categories."""
    data = api_get("/categories", params={"limit": 100})
    if not data:
        return []
    return data.get("data", [])


def fetch_livestreams(category_id: int | None = None,
                      max_streams: int = 10,
                      language: str = "id") -> list[dict]:
    """
    Fetch livestreams dari /public/v2/livestreams.
    Sort: viewer_count desc. Cursor pagination.
    """
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params: dict = {
            "limit": min(100, max_streams - len(all_streams)),
            "sort": "viewer_count",
            "language_code": language,
        }
        if category_id:
            params["category_id"] = str(category_id)
        if cursor:
            params["cursor"] = cursor

        data = api_get("/livestreams", params=params)
        if not data:
            break

        items = data.get("data", [])
        if not isinstance(items, list) or not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])
        cursor = data.get("next_cursor") or data.get("cursor")
        if not cursor:
            break

        smart_sleep(0.1)

    return all_streams[:max_streams]


def extract_playback_url(stream_data: dict) -> Optional[str]:
    """
    Extract .m3u8 URL dari response.
    Support multiple response shapes.
    """
    pb = stream_data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or pb.get("dvr") or ""
    if isinstance(pb, str) and pb.startswith("http"):
        return pb.replace("\\/", "/")

    # Nested in stream object
    stream = stream_data.get("stream", {})
    if isinstance(stream, dict):
        return extract_playback_url(stream)

    # Fallback fields
    live = stream_data.get("live")
    if isinstance(live, dict):
        return live.get("hls_url") or live.get("playback_url") or ""
    if isinstance(live, str) and live.startswith("http"):
        return live

    return stream_data.get("hls_url") or ""


def process_kick_stream(stream_data: dict, group_name: str) -> Optional[Stream]:
    """Convert raw API response → Stream dataclass."""
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("slug")
        or (stream_data.get("channel", {}) or {}).get("slug", "")
        or (stream_data.get("broadcaster", {}) or {}).get("slug", "")
        or (stream_data.get("stream", {}) or {}).get("channel_slug", "")
    )
    if not channel_slug:
        return None

    viewers = (
        stream_data.get("viewer_count")
        or stream_data.get("viewers")
        or (stream_data.get("stream", {}) or {}).get("viewer_count", 0)
        or 0
    )

    playback_url = extract_playback_url(stream_data)
    if not playback_url:
        return None

    title = (
        stream_data.get("session_title")
        or stream_data.get("title")
        or (stream_data.get("channel", {}) or {}).get("session_title", "")
        or (stream_data.get("stream", {}) or {}).get("session_title", "")
        or "Untitled"
    )

    started_at = (
        stream_data.get("start_time")
        or stream_data.get("created_at")
        or (stream_data.get("stream", {}) or {}).get("start_time")
    )

    tvg_logo = (
        stream_data.get("profile_picture")
        or (stream_data.get("channel", {}) or {}).get("profile_picture", "")
        or (stream_data.get("broadcaster", {}) or {}).get("profile_picture", "")
        or (stream_data.get("stream", {}) or {}).get("thumbnail", "")
        or f"https://kick.com/{channel_slug}/profile"
    )

    return Stream(
        channel_slug=channel_slug,
        title=title,
        playback_url=playback_url,
        group_title=group_name,
        tvg_logo=tvg_logo,
        started_at=str(started_at) if started_at else None,
        viewers=viewers,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M3U BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def format_title(s: Stream) -> str:
    """Format stream title with start time if available."""
    base = s.title
    if s.started_at:
        try:
            raw = s.started_at
            if raw.isdigit() or (raw.replace('.', '', 1).isdigit() and '.' in raw):
                dt = datetime.fromtimestamp(float(raw), tz=timezone.utc).astimezone()
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
            base = f"{base} [{dt.strftime('%H:%M')} {dt.strftime('%d/%m/%Y')}]"
        except (ValueError, TypeError, OSError):
            pass
    return base


def build_m3u(streams: list[Stream]) -> str:
    """Build M3U playlist string."""
    lines = ["#EXTM3U"]
    for s in streams:
        attrs = (
            f'tvg-id="{s.platform}.{s.group_title}.{s.channel_slug}",'
            f'tvg-name="{s.channel_slug}",'
            f'tvg-logo="{s.tvg_logo}",'
            f'group-title="{s.group_title}"'
        )
        lines.append(f"#EXTINF:-1,{attrs} {format_title(s)}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Kick M3U Scraper (OAuth 2.1 PKCE)"
    )
    parser.add_argument("--output", "-o", default=OUTPUT_FILE,
                        help="Output file path")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS,
                        help="Max concurrent workers")
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY,
                        help="Max streams per category")
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS,
                        help="Total max streams")
    parser.add_argument("--language", "-l", default=LANGUAGE,
                        help="Language filter (id, en, ja, ...)")
    parser.add_argument("--reauth", action="store_true",
                        help="Force re-authentication (ignore cached token)")
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick M3U Scraper (PKCE) ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s",
             args.max_per_category, args.max_streams, args.language)

    # ── Validate credentials ──────────────────────────────────────────────────
    if not KICK_CLIENT_ID or not KICK_CLIENT_SECRET:
        log.error("❌ Set KICK_CLIENT_ID dan KICK_CLIENT_SECRET!")
        log.error("   export KICK_CLIENT_ID=xxx")
        log.error("   export KICK_CLIENT_SECRET=xxx")
        return

    # ── OAuth: ensure valid token ─────────────────────────────────────────────
    if args.reauth:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
            log.info("Token cache dihapus.")

    oauth = get_oauth()
    token = oauth.get_token()
    if not token:
        log.error("❌ Gagal mendapatkan token. Jalankan ulang dengan --reauth?")
        return

    all_streams: list[Stream] = []

    # ── Fetch categories ──────────────────────────────────────────────────────
    categories = fetch_categories()
    log.info("Kategori: %d", len(categories))

    all_raw: list[tuple[dict, str]] = []
    seen: set[str] = set()

    # ── Fetch livestreams per category (parallel) ─────────────────────────────
    if categories:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {}
            for cat in categories:
                cat_id = cat.get("id") or cat.get("category_id")
                cat_name = cat.get("name") or cat.get("slug", "unknown")
                if cat_id:
                    futures[pool.submit(
                        fetch_livestreams, cat_id,
                        args.max_per_category, args.language
                    )] = cat_name

            for fut in as_completed(futures):
                cat_name = futures[fut]
                try:
                    streams = fut.result()
                    for s in streams:
                        slug = (
                            s.get("channel_slug")
                            or s.get("slug")
                            or (s.get("channel", {}) or {}).get("slug", "")
                        )
                        if slug and slug not in seen:
                            seen.add(slug)
                            all_raw.append((s, cat_name))
                    log.info("  %s: %d stream", cat_name, len(streams))
                except Exception as e:
                    log.debug("Error %s: %s", cat_name, e)

                if len(all_raw) >= args.max_streams:
                    break
    else:
        # Fallback: tanpa filter kategori
        log.info("Fallback: semua livestreams (tanpa kategori)")
        streams = fetch_livestreams(None, args.max_per_category, args.language)
        for s in streams:
            slug = (
                s.get("channel_slug")
                or s.get("slug")
                or (s.get("channel", {}) or {}).get("slug", "")
            )
            if slug and slug not in seen:
                seen.add(slug)
                all_raw.append((s, "All"))

    log.info("Stream unik: %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

    # ── Process streams (parallel) ────────────────────────────────────────────
    if all_raw:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_kick_stream, raw, cat): raw
                for raw, cat in all_raw
            }
            for fut in as_completed(futures):
                try:
                    s = fut.result()
                    if s:
                        all_streams.append(s)
                except Exception:
                    pass

    log.info("Kick valid: %d", len(all_streams))

    # ── Output ────────────────────────────────────────────────────────────────
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    if len(all_streams) > args.max_streams:
        all_streams = all_streams[:args.max_streams]

    with open(args.output, "w") as f:
        f.write(build_m3u(all_streams))

    log.info("✅ %d stream → %s (%.1fs)", len(all_streams), args.output, time.time() - t0)


if __name__ == "__main__":
    main()   
