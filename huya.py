#!/usr/bin/env python3
"""
Kick M3U Scraper
OAuth 2.1 Client Credentials + Unofficial API (curl_cffi)
"""

import time
import random
import logging
import argparse
import threading
import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import requests
from curl_cffi import requests as cffi_requests

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"
TOKEN_FILE = "kick_token.json"
MAX_PER_CATEGORY = 10
MAX_STREAMS = 1000
LANGUAGE = "id"

KICK_CLIENT_ID = os.getenv("KICK_CLIENT_ID", "01M6YCH4D5Y8TGGQAMP25TDJ42")
KICK_CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "a6ba1635efe7f460edcc486675594df1c9156a813ffc93d677841121a43fe239")

API_BASE = "https://api.kick.com/public/v1"
OAUTH_TOKEN_URL = "https://id.kick.com/oauth/token"

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


# ═══════════════════════════════════════════════════════════════════════════════
# OAUTH — Client Credentials
# ═══════════════════════════════════════════════════════════════════════════════

class KickOAuth:
    """App Access Token via client_credentials (server-to-server)."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.expires_at: float = 0
        self._lock = threading.Lock()

    def _load_from_file(self) -> bool:
        if not os.path.exists(TOKEN_FILE):
            return False
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.expires_at = data.get("expires_at", 0)
            log.info("Token dimuat dari %s", TOKEN_FILE)
            return bool(self.access_token)
        except Exception:
            return False

    def _save_to_file(self):
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": self.access_token,
                "expires_at": self.expires_at,
            }, f, indent=2)

    def _fetch_token(self) -> bool:
        log.info("Mengambil App Access Token...")
        for attempt in range(RETRY_COUNT):
            try:
                r = requests.post(
                    OAUTH_TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if r.status_code == 200:
                    data = r.json()
                    self.access_token = data["access_token"]
                    self.expires_at = time.time() + data.get("expires_in", 3600)
                    self._save_to_file()
                    log.info("Token berhasil (exp: %ds)", data.get("expires_in", "?"))
                    return True
                elif r.status_code == 404:
                    log.warning("404: id.kick.com down. Retry %d/%d dalam 15s...",
                                attempt + 1, RETRY_COUNT)
                    time.sleep(15)
                else:
                    log.error("Token error: %s - %s", r.status_code, r.text[:200])
                    return False
            except Exception as e:
                log.warning("Token error: %s. Retry %d/%d...", e, attempt + 1, RETRY_COUNT)
                time.sleep(10)
        return False

    def get_token(self) -> Optional[str]:
        with self._lock:
            if self.access_token and time.time() < self.expires_at - 60:
                return self.access_token
            if not self.access_token:
                self._load_from_file()
                if self.access_token and time.time() < self.expires_at - 60:
                    return self.access_token
            if not self._fetch_token():
                return None
            return self.access_token


_oauth: Optional[KickOAuth] = None

def get_oauth() -> KickOAuth:
    global _oauth
    if _oauth is None:
        _oauth = KickOAuth(KICK_CLIENT_ID, KICK_CLIENT_SECRET)
    return _oauth


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP CLIENTS
# ═══════════════════════════════════════════════════════════════════════════════

_oauth_session = requests.Session()
_local = threading.local()


def get_cf_session() -> cffi_requests.Session:
    """curl_cffi session untuk bypass Cloudflare (kick.com)."""
    if not hasattr(_local, "cf_session"):
        _local.cf_session = cffi_requests.Session(impersonate="chrome124")
    return _local.cf_session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


# ── OAuth API (api.kick.com) ──────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None) -> Optional[dict]:
    """GET ke api.kick.com/public/v1 dengan Bearer token."""
    token = get_oauth().get_token()
    if not token:
        return None

    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(RETRY_COUNT):
        try:
            r = _oauth_session.get(url, params=params, headers=headers,
                                   timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                log.warning("401: Token expired, refresh...")
                get_oauth().access_token = None
                get_oauth().expires_at = 0
                token = get_oauth().get_token()
                if not token:
                    return None
                headers["Authorization"] = f"Bearer {token}"
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                log.debug("HTTP %s: %s", r.status_code, r.text[:200])
                time.sleep(1)
        except Exception as e:
            log.debug("Error: %s", e)
            time.sleep(2)
    return None


# ── Unofficial API (kick.com) via curl_cffi ───────────────────────────────────

def cf_get(url: str, params: dict | None = None) -> Optional[dict]:
    """GET ke kick.com (unofficial, bypass Cloudflare)."""
    for attempt in range(RETRY_COUNT):
        try:
            r = get_cf_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429 (CF)! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# KICK API
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_categories() -> list[dict]:
    """Fetch categories dari /public/v1/categories (paginated)."""
    all_cats = []
    cursor = None

    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor

        data = api_get("/categories", params=params)
        if not data:
            break

        items = data.get("data", [])
        if not isinstance(items, list) or not items:
            break

        all_cats.extend(items)
        cursor = (data.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
        smart_sleep(0.1)

    return all_cats


def fetch_livestreams(category_id: int | None = None,
                      max_streams: int = 10,
                      language: str = "id") -> list[dict]:
    """
    Fetch livestreams dari /public/v1/livestreams.
    Sort: viewer_count desc. Cursor pagination.
    """
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params: dict = {
            "limit": min(100, max(24, max_streams - len(all_streams))),
            "sort": "viewer_count",
        }
        if category_id:
            params["category_id"] = category_id
        if language:
            params["language"] = language
        if cursor:
            params["cursor"] = cursor

        data = api_get("/livestreams", params=params)
        if not data:
            break

        items = data.get("data", [])
        if not isinstance(items, list) or not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])
        cursor = (data.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
        smart_sleep(0.1)

    return all_streams[:max_streams]


def process_kick_stream(stream_data: dict) -> Optional[Stream]:
    """
    Proses satu stream:
    1. Ambil info dari OAuth API (slug, title, viewers, category)
    2. Fetch channel info dari kick.com (playback_url + logo)
    """
    channel_slug = stream_data.get("slug", "")
    if not channel_slug:
        return None

    viewers = stream_data.get("viewer_count", 0)
    title = stream_data.get("stream_title", "Untitled")
    started_at = stream_data.get("started_at")
    category = stream_data.get("category", {})
    group_name = category.get("name", "Unknown")

    # Satu request ke kick.com → playback_url + logo
    ch_data = cf_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not ch_data or not ch_data.get("livestream"):
        return None  # offline

    playback_url = ch_data.get("playback_url", "")
    if isinstance(playback_url, str):
        playback_url = playback_url.replace("\\/", "/")
    if not playback_url:
        return None

    tvg_logo = (
        ch_data.get("profile_picture")
        or ch_data.get("profile_pic")
        or f"https://kick.com/{channel_slug}/profile"
    )

    return Stream(
        channel_slug=channel_slug,
        title=title,
        playback_url=playback_url,
        group_title=group_name,
        tvg_logo=tvg_logo,
        started_at=started_at,
        viewers=viewers,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M3U BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def format_title(s: Stream) -> str:
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
    lines = ["#EXTM3U"]
    for s in streams:
        attrs = (
            f'tvg-id="Kick.{s.group_title}.{s.channel_slug}",'
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
    parser = argparse.ArgumentParser(description="Kick M3U Scraper (OAuth + curl_cffi)")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    parser.add_argument("--language", "-l", default=LANGUAGE)
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick M3U Scraper (Client Credentials + CF) ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s",
             args.max_per_category, args.max_streams, args.language)

    # ── Validate credentials ──────────────────────────────────────────────────
    if not KICK_CLIENT_ID or not KICK_CLIENT_SECRET:
        log.error("Set KICK_CLIENT_ID dan KICK_CLIENT_SECRET!")
        log.error("  export KICK_CLIENT_ID=xxx")
        log.error("  export KICK_CLIENT_SECRET=xxx")
        return

    # ── OAuth: ensure valid token ─────────────────────────────────────────────
    token = get_oauth().get_token()
    if not token:
        log.error("Gagal mendapatkan token. Cek koneksi / id.kick.com status.")
        return

    all_streams: list[Stream] = []

    # ── Step 1: Fetch categories ──────────────────────────────────────────────
    categories = fetch_categories()
    log.info("Kategori: %d", len(categories))

    all_raw: list[dict] = []
    seen: set[str] = set()

    # ── Step 2: Fetch livestreams per kategori (parallel) ─────────────────────
    if categories:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {}
            for cat in categories:
                cat_id = cat.get("id")
                cat_name = cat.get("name", "unknown")
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
                        slug = s.get("slug", "")
                        if slug and slug not in seen:
                            seen.add(slug)
                            all_raw.append(s)
                    log.info("  %s: %d stream", cat_name, len(streams))
                except Exception as e:
                    log.debug("Error %s: %s", cat_name, e)

                if len(all_raw) >= args.max_streams:
                    break
    else:
        log.info("Fallback: semua livestreams (tanpa kategori)")
        streams = fetch_livestreams(None, args.max_per_category, args.language)
        for s in streams:
            slug = s.get("slug", "")
            if slug and slug not in seen:
                seen.add(slug)
                all_raw.append(s)

    log.info("Stream unik: %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

    # ── Step 3: Fetch playback_url per stream (parallel) ──────────────────────
    if all_raw:
        log.info("Fetching playback_url (%d stream)...", len(all_raw))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_kick_stream, s): s
                for s in all_raw
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 20 == 0:
                    log.info("  Progress: %d/%d", done, len(futures))
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

    log.info("OK: %d stream -> %s (%.1fs)", len(all_streams), args.output, time.time() - t0)


if __name__ == "__main__":
    main()   
