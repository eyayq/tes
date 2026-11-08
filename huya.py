import time
import random
import logging
import argparse
import subprocess
import threading
import re
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import requests
from curl_cffi import requests as cffi_requests

# ─── DEFAULTS ─────────────────────────────────────────────────────────────────
MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"

MAX_PER_CATEGORY = 10    # stream per kategori
MAX_STREAMS = 1000       # total maksimal stream
LANGUAGE = "id"

# ─── PLAYWRIGHT (auto token) ──────────────────────────────────────────────────
USER_DATA_DIR = Path(__file__).parent / "kick_browser"
TOKEN_FILE = Path(__file__).parent / "kick_token.json"
KICK_URL = "https://kick.com"

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


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO TOKEN (Playwright)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_token(headless: bool = True) -> Optional[str]:
    """Ambil session_token dari kick.com via Playwright."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1920, "height": 1080},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(KICK_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        token = None
        cookies = context.cookies()
        for c in cookies:
            if c["name"] == "session_token" and c["value"].startswith("kat_"):
                token = c["value"]
                break

        if not token and not headless:
            log.info("⚠️  Belum login. Silakan login di browser yang terbuka...")
            for _ in range(60):
                page.wait_for_timeout(2000)
                for c in context.cookies():
                    if c["name"] == "session_token" and c["value"].startswith("kat_"):
                        token = c["value"]
                        break
                if token:
                    break

        context.close()

        if token:
            with open(TOKEN_FILE, "w") as f:
                json.dump({"session_token": token, "saved_at": time.time()}, f)
            log.info("✅ Token tersimpan: %s...", token[:20])
        return token


def get_session_token() -> Optional[str]:
    """
    1. Cek cache (kick_token.json)
    2. Headless Playwright
    3. Visible Playwright (login manual)
    """
    # 1. Cache
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            token = data.get("session_token")
            saved_at = data.get("saved_at", 0)
            if token and time.time() - saved_at < 25 * 86400:
                log.info("Token dimuat dari cache")
                return token
        except Exception:
            pass

    # 2. Headless
    log.info("Mencoba ambil token (headless)...")
    try:
        token = _extract_token(headless=True)
        if token:
            return token
    except Exception as e:
        log.debug("Headless gagal: %s", e)

    # 3. Visible (login manual)
    log.info("Membuka browser untuk login manual...")
    try:
        token = _extract_token(headless=False)
        return token
    except Exception as e:
        log.error("Gagal ambil token: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════════════════════

_local = threading.local()

def get_session(token: str) -> requests.Session:
    if not hasattr(_local, "session") or _local.get("token") != token:
        _local.session = requests.Session()
        _local.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/156.0.0.0 Mobile Safari/537.36"),
            "x-app-platform": "web",
        })
        _local.token = token
    return _local.session


_cf_local = threading.local()

def get_cf_session():
    if not hasattr(_cf_local, "session"):
        _cf_local.session = cffi_requests.Session(impersonate="chrome124")
    return _cf_local.session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


def web_get(path: str, params: dict | None = None, token: str = "") -> Optional[dict]:
    """GET ke web.kick.com/api/v1."""
    url = f"https://web.kick.com/api/v1{path}"
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session(token).get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                log.error("401: Token expired!")
                return None
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


def cf_get(url: str, params: dict | None = None):
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
# KICK: FETCH STREAMS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_featured(language: str, token: str) -> list[dict]:
    """GET /livestreams/featured → data.livestreams"""
    data = web_get("/livestreams/featured", params={"language": language}, token=token)
    if not data:
        return []
    return (data.get("data") or {}).get("livestreams", [])


def fetch_recommended(language: str, max_streams: int, token: str) -> list[dict]:
    """GET /recommendations/livestreams/list (cursor pagination)."""
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params = {"limit": 50, "language": language}
        if cursor:
            params["cursor"] = cursor

        data = web_get("/recommendations/livestreams/list", params=params, token=token)
        if not data:
            break

        inner = data.get("data") or {}
        items = inner.get("livestreams", [])
        if not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])

        cursor = (inner.get("pagination") or {}).get("next_cursor", "")
        if not cursor:
            break
        smart_sleep(0.2)

    return all_streams[:max_streams]


# ═══════════════════════════════════════════════════════════════════════════════
# KICK: PLAYBACK URL
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_playback_url(channel_slug: str) -> Optional[str]:
    """GET kick.com/api/v2/channels/{slug}/livestream → playback_url["live"]"""
    data = cf_get(f"https://kick.com/api/v2/channels/{channel_slug}/livestream")
    if not data:
        return None

    pb = data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or ""
    if isinstance(pb, str) and pb.startswith("http"):
        return pb

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# KICK: PROCESS
# ═══════════════════════════════════════════════════════════════════════════════

def process_kick_stream(stream_data: dict) -> Optional[Stream]:
    """
    stream_data dari web.kick.com:
    {
        "channel": {"slug": "nuxanor", "profile_pic": "https://...", "username": "Nuxanor"},
        "category": {"name": "Just Chatting", "slug": "just-chatting"},
        "title": "🚨 OMFG...",
        "viewer_count": 1628,
        "start_time": "2026-11-08T21:16:45Z"
    }
    """
    channel = stream_data.get("channel", {})
    channel_slug = channel.get("slug", "")
    if not channel_slug:
        return None

    viewers = stream_data.get("viewer_count", 0)
    title = stream_data.get("title", "Untitled")
    started_at = stream_data.get("start_time")
    category = stream_data.get("category", {})
    group_name = category.get("name", "Unknown")
    tvg_logo = channel.get("profile_pic", f"https://kick.com/{channel_slug}/profile")

    playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

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
# YOUTUBE
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# M3U
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
    parser = argparse.ArgumentParser(description="Kick + YouTube M3U Scraper")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--kick-only", action="store_true")
    parser.add_argument("--yt-only", action="store_true")
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    parser.add_argument("--language", "-l", default=LANGUAGE)
    parser.add_argument("--no-playwright", action="store_true",
                        help="Skip auto-token (pakai token dari file/env)")
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s",
             args.max_per_category, args.max_streams, args.language)

    # ── Token ──
    token = None
    if not args.yt_only:
        if not args.no_playwright:
            token = get_session_token()
        else:
            # Dari env atau file
            token = os.getenv("KICK_SESSION_TOKEN")
            if not token and TOKEN_FILE.exists():
                try:
                    token = json.loads(TOKEN_FILE.read_text()).get("session_token")
                except Exception:
                    pass

        if not token:
            log.error("❌ Gagal ambil session token!")
            log.error("   Jalankan: python kick_m3u.py (tanpa --no-playwright)")
            log.error("   Atau set: export KICK_SESSION_TOKEN=kat_xxx")
            return

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        all_raw: list[dict] = []
        seen: set[str] = set()
        per_cat_count: dict[str, int] = {}

        # Source 1: Featured
        featured = fetch_featured(args.language, token)
        log.info("Featured: %d stream", len(featured))
        for s in featured:
            slug = s.get("channel", {}).get("slug", "")
            cat = s.get("category", {}).get("name", "Unknown")
            if slug and slug not in seen:
                seen.add(slug)
                per_cat_count[cat] = per_cat_count.get(cat, 0) + 1
                if per_cat_count[cat] <= args.max_per_category:
                    all_raw.append(s)

        # Source 2: Recommended
        recommended = fetch_recommended(args.language, args.max_streams, token)
        log.info("Recommended: %d stream", len(recommended))
        for s in recommended:
            slug = s.get("channel", {}).get("slug", "")
            cat = s.get("category", {}).get("name", "Unknown")
            if slug and slug not in seen:
                seen.add(slug)
                per_cat_count[cat] = per_cat_count.get(cat, 0) + 1
                if per_cat_count[cat] <= args.max_per_category:
                    all_raw.append(s)
            if len(all_raw) >= args.max_streams:
                break

        log.info("Stream unik: %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

        # Fetch playback_url
        if all_raw:
            log.info("Fetching playback_url...")
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(process_kick_stream, s): s for s in all_raw}
                done = 0
                for fut in as_completed(futures):
                    done += 1
                    if done % 20 == 0:
                        log.info("  Playback: %d/%d", done, len(futures))
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
