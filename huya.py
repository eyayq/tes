import time
import random
import logging
import argparse
import subprocess
import threading
import re
import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import requests

# ─── DEFAULTS ─────────────────────────────────────────────────────────────────
MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"
TOKEN_FILE = "ki.json"

MAX_PER_CATEGORY = 10    # stream per kategori
MAX_STREAMS = 1000       # total maksimal stream
LANGUAGE = "id"          # bahasa: "id", "en", "ja", dll.

# ─── KICK OAUTH CREDENTIALS ───────────────────────────────────────────────────
# Daftar di: https://kick.com/settings/developer
KICK_CLIENT_ID = os.getenv("KICK_CLIENT_ID", "01M6YCH4D5Y8TGGQAMP25TDJ42")
KICK_CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "a6ba1635efe7f460edcc486675594df1c9156a813ffc93d677841121a43fe239")

YOUTUBE_CHANNELS = [
    ("https://www.youtube.com/@MPLIndonesia/live", "MPL Indonesia"),
    ("https://www.youtube.com/@mlbbindonesia/live", "MLBB Indonesia"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kick-m3u")


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


# ─── OAUTH: Client Credentials ────────────────────────────────────────────────
class KickOAuth:
    """App Access Token via client_credentials (server-to-server)."""

    TOKEN_URL = "https://id.kick.com/oauth/token"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.expires_at: float = 0

    def get_token(self) -> Optional[str]:
        """Get valid token (cache + auto-refresh)."""
        # Cek cache
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token

        # Coba load dari file
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE) as f:
                    data = json.load(f)
                if data.get("expires_at", 0) > time.time() + 60:
                    self.access_token = data["access_token"]
                    self.expires_at = data["expires_at"]
                    log.info("Token dimuat dari file")
                    return self.access_token
            except Exception:
                pass

        # Fetch token baru
        log.info("Mengambil App Access Token...")
        try:
            r = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                log.error("Token error: %s - %s", r.status_code, r.text[:200])
                return None

            data = r.json()
            self.access_token = data["access_token"]
            self.expires_at = time.time() + data.get("expires_in", 3600)

            # Simpan ke file
            with open(TOKEN_FILE, "w") as f:
                json.dump({
                    "access_token": self.access_token,
                    "expires_at": self.expires_at,
                }, f)

            log.info("Token berhasil (exp: %ds)", data.get("expires_in", "?"))
            return self.access_token

        except Exception as e:
            log.error("Token error: %s", e)
            return None


# ─── HTTP (dengan Bearer token) ───────────────────────────────────────────────
_local = threading.local()
_oauth: Optional[KickOAuth] = None


def get_oauth() -> KickOAuth:
    global _oauth
    if _oauth is None:
        _oauth = KickOAuth(KICK_CLIENT_ID, KICK_CLIENT_SECRET)
    return _oauth


def get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


def api_get(path: str, params: dict | None = None) -> Optional[dict | list]:
    """GET request ke api.kick.com/public/v1 dengan Bearer token."""
    token = get_oauth().get_token()
    if not token:
        return None

    url = f"https://api.kick.com/public/v1{path}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, headers=headers,
                                  timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                # Token expired → refresh
                log.warning("401: Token expired, refresh...")
                get_oauth().access_token = None
                get_oauth().expires_at = 0
                token = get_oauth().get_token()
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


# ─── KICK: Fetch categories ──────────────────────────────────────────────────
def fetch_categories() -> list[dict]:
    """Fetch kategori dari /public/v1/categories."""
    data = api_get("/categories", params={"limit": 100})
    if not data:
        return []

    items = data.get("data", data.get("categories", []))
    if isinstance(items, list):
        return items
    return []


# ─── KICK: Fetch livestreams ─────────────────────────────────────────────────
def fetch_livestreams(category_id: int | None = None,
                      max_streams: int = 10,
                      language: str = "id") -> list[dict]:
    """
    Fetch livestreams dari /public/v1/livestreams.
    Sort: viewer_count (paling ramai dulu).
    """
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params = {
            "limit": min(100, max_streams - len(all_streams)),
            "sort": "viewer_count",
            "language": language,
        }
        if category_id:
            params["category_id"] = category_id
        if cursor:
            params["cursor"] = cursor

        data = api_get("/livestreams", params=params)
        if not data:
            break

        items = data.get("data", data.get("livestreams", []))
        if not isinstance(items, list) or not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])

        # Cursor pagination
        cursor = data.get("next_cursor") or data.get("cursor")
        if not cursor:
            break

        smart_sleep(0.1)

    return all_streams[:max_streams]


# ─── KICK: Proses stream ─────────────────────────────────────────────────────
def extract_playback_url(stream_data: dict) -> Optional[str]:
    """
    Struktur response api.kick.com:
      stream_data["playback_url"] = {
          "dvr": "https://stream.kick.com/...",
          "live": "https://...m3u8?token=..."
      }
    """
    pb = stream_data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or ""
    if isinstance(pb, str) and pb.startswith("http"):
        return pb

    # Fallback: field lain
    live = stream_data.get("live")
    if isinstance(live, dict):
        return live.get("hls_url") or live.get("playback_url") or ""
    if isinstance(live, str) and live.startswith("http"):
        return live

    return stream_data.get("hls_url") or ""


def process_kick_stream(stream_data: dict, group_name: str) -> Optional[Stream]:
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("slug")
        or (stream_data.get("channel", {}) or {}).get("slug", "")
        or (stream_data.get("broadcaster", {}) or {}).get("slug", "")
    )
    if not channel_slug:
        return None

    viewers = (
        stream_data.get("viewer_count")
        or stream_data.get("viewers")
        or 0
    )

    playback_url = extract_playback_url(stream_data)
    if not playback_url:
        return None

    title = (
        stream_data.get("session_title")
        or stream_data.get("title")
        or (stream_data.get("channel", {}) or {}).get("session_title", "")
        or "Untitled"
    )
    started_at = stream_data.get("start_time") or stream_data.get("created_at")
    tvg_logo = (
        stream_data.get("profile_picture")
        or (stream_data.get("channel", {}) or {}).get("profile_picture", "")
        or (stream_data.get("broadcaster", {}) or {}).get("profile_picture", "")
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


# ─── YOUTUBE ─────────────────────────────────────────────────────────────────
def get_youtube_m3u8(channel_url: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["yt-dlp", "-g", "-f", "b[protocol=m3u8]",
             "--extractor-args", "youtube:player_client=web_safari",
             "--no-warnings", "--socket-timeout", "15",
             channel_url],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def get_youtube_channel_logo(channel_url: str) -> str:
    try:
        r = requests.get(channel_url.replace("/live", ""), timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            match = re.search(r'og:image"\s+content="([^"]+)"', r.text)
            if match:
                return match.group(1)
    except Exception:
        pass
    return "https://www.youtube.com/img/icons/1x/channel-icon.png"


def process_youtube(item: tuple) -> Optional[Stream]:
    url, name = item
    m3u8 = get_youtube_m3u8(url)
    if not m3u8:
        return None
    logo = get_youtube_channel_logo(url)
    return Stream(
        channel_slug=name.lower().replace(" ", "_"),
        title=f"{name} LIVE",
        playback_url=m3u8,
        group_title="YouTube",
        tvg_logo=logo,
        started_at=None,
        viewers=0,
        platform="YouTube",
    )


# ─── M3U ─────────────────────────────────────────────────────────────────────
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
            f'tvg-id="{s.platform}.{s.group_title}.{s.channel_slug}",'
            f'tvg-name="{s.channel_slug}",'
            f'tvg-logo="{s.tvg_logo}",'
            f'group-title="{s.group_title}"'
        )
        lines.append(f"#EXTINF:-1,{attrs} {format_title(s)}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kick + YouTube M3U Scraper (OAuth API)")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--kick-only", action="store_true")
    parser.add_argument("--yt-only", action="store_true")
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    parser.add_argument("--language", "-l", default=LANGUAGE)
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper (OAuth) ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s",
             args.max_per_category, args.max_streams, args.language)

    # Cek credentials
    if KICK_CLIENT_ID == "your_client_id_here":
        log.error("❌ Set KICK_CLIENT_ID dan KICK_CLIENT_SECRET!")
        log.error("   export KICK_CLIENT_ID=xxx")
        log.error("   export KICK_CLIENT_SECRET=xxx")
        return

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        # Step 1: Fetch categories
        categories = fetch_categories()
        log.info("Kategori: %d", len(categories))

        all_raw: list[tuple[dict, str]] = []
        seen: set[str] = set()

        # Step 2: Fetch livestreams per kategori
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
                            slug = (s.get("channel_slug")
                                    or s.get("slug")
                                    or (s.get("channel", {}) or {}).get("slug", ""))
                            if slug and slug not in seen:
                                seen.add(slug)
                                all_raw.append((s, cat_name))

                            if len(all_raw) >= args.max_streams:
                                break
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
                slug = (s.get("channel_slug")
                        or s.get("slug")
                        or (s.get("channel", {}) or {}).get("slug", ""))
                if slug and slug not in seen:
                    seen.add(slug)
                    all_raw.append((s, "All"))

        log.info("Stream unik: %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

        # Step 3: Proses
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

        kick_count = sum(1 for s in all_streams if s.platform == "Kick")
        log.info("Kick valid: %d", kick_count)

    # ─── YOUTUBE ─────────────────────────────────────────────────────────────
    if not args.kick_only and YOUTUBE_CHANNELS:
        log.info("─── YOUTUBE ───")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for fut in as_completed(pool.submit(process_youtube, ch) for ch in YOUTUBE_CHANNELS):
                try:
                    s = fut.result()
                    if s:
                        log.info("  ✅ %s LIVE", s.title)
                        all_streams.append(s)
                except Exception:
                    pass

    # ── Output ──
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    if len(all_streams) > args.max_streams:
        all_streams = all_streams[:args.max_streams]

    with open(args.output, "w") as f:
        f.write(build_m3u(all_streams))
    log.info("✅ %d stream → %s (%.1fs)", len(all_streams), args.output, time.time() - t0)


if __name__ == "__main__":
    main()   
