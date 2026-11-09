import time
import random
import logging
import argparse
import subprocess
import threading
import re
import json
import os
import uuid
from pathlib import Path
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

MAX_PER_CATEGORY = 10    # stream per kategori
MAX_STREAMS = 1000       # total maksimal stream
LANGUAGE = "id"

# ─── SESSION TOKEN ────────────────────────────────────────────────────────────
# Cara ambil: F12 → Application → Cookies → kick.com → session_token
KICK_SESSION_TOKEN = os.getenv("KICK_SESSION_TOKEN", "kat_paste_token_disini")

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


# ─── HTTP ─────────────────────────────────────────────────────────────────────
_local = threading.local()

def get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers.update({
            "Authorization": f"Bearer {KICK_SESSION_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/156.0.0.0 Mobile Safari/537.36"),
            "x-app-platform": "web",
            "Origin": "https://kick.com",
            "Referer": "https://kick.com/",
        })
    return _local.session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


def web_get(path: str, params: dict | None = None) -> Optional[dict]:
    """GET ke web.kick.com/api/v1."""
    url = f"https://web.kick.com/api/v1{path}"
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                log.error("401: Token expired! Update KICK_SESSION_TOKEN.")
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


def web_post(path: str, json_body: dict) -> Optional[dict]:
    """POST ke web.kick.com/api/v1."""
    url = f"https://web.kick.com/api/v1{path}"
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().post(url, json=json_body, timeout=REQUEST_TIMEOUT)
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


# ─── KICK: Fetch streams ──────────────────────────────────────────────────────
def fetch_featured(language: str) -> list[dict]:
    """GET /livestreams/featured"""
    data = web_get("/livestreams/featured", params={"language": language})
    if not data:
        return []
    return (data.get("data") or {}).get("livestreams", [])


def fetch_recommended(language: str, max_streams: int) -> list[dict]:
    """GET /recommendations/livestreams/list (cursor pagination)."""
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params = {"limit": 50, "language": language}
        if cursor:
            params["cursor"] = cursor

        data = web_get("/recommendations/livestreams/list", params=params)
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


# ─── KICK: Fetch playback_url (POST /stream/{id}/playback) ───────────────────
def fetch_playback_url(stream_id: str) -> Optional[str]:
    """
    POST /stream/{stream_id}/playback
    Response: {"playback_url": {"live": "https://...m3u8?token=...", ...}}
    """
    body = {
        "video_player": {
            "player": {
                "player_name": "web_mobile",
                "player_version": "web_mobile_fd20d6ce",
                "player_software": "IVS Player",
                "player_software_version": "1.56.2",
            },
            "mux_sdk": {"sdk_available": True},
            "pal_sdk": {"sdk_available": False, "nonce": ""},
            "datazoom_sdk": {"sdk_available": True, "datazoom_sdk_version": "2.33.0", "om_sdk_version": "1.6.6"},
            "google_ads_sdk": {"sdk_available": False},
            "ima_sdk": {"sdk_available": False},
        },
        "video_session": {
            "page_type": "channel",
            "player_remote_played": False,
            "enable_sampling": False,
            "url_path": "",
            "autoplay_behaviour": "click",
            "play_muted": False,
            "viewer_connection_type": "",
        },
        "user_session": {
            "session_id": str(uuid.uuid4()),
            "player_device_id": str(uuid.uuid4()),
            "browser_lang": "id",
            "non_personalised_ads": True,
            "gdpr": 0,
        },
    }

    data = web_post(f"/stream/{stream_id}/playback", json_body=body)
    if not data:
        return None

    pb = data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or ""

    return None


# ─── KICK: Process stream ─────────────────────────────────────────────────────
def process_kick_stream(stream_data: dict) -> Optional[Stream]:
    """
    stream_data:
    {
        "id": "01a1bd05-5248-797f-bb88-e9cf29d76cf8",
        "channel": {"slug": "nuxanor", "profile_pic": "https://...", "username": "Nuxanor"},
        "category": {"name": "Just Chatting", "slug": "just-chatting"},
        "title": "🚨 OMFG...",
        "viewer_count": 1628,
        "start_time": "2026-11-08T21:16:45Z"
    }
    """
    stream_id = stream_data.get("id", "")
    if not stream_id:
        return None

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

    # Fetch playback_url
    playback_url = fetch_playback_url(stream_id)
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
    parser = argparse.ArgumentParser(description="Kick + YouTube M3U Scraper")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--kick-only", action="store_true")
    parser.add_argument("--yt-only", action="store_true")
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    parser.add_argument("--language", "-l", default=LANGUAGE)
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s",
             args.max_per_category, args.max_streams, args.language)

    if "paste_token" in KICK_SESSION_TOKEN and not args.yt_only:
        log.error("❌ Set KICK_SESSION_TOKEN!")
        log.error("   Cara: F12 → Application → Cookies → kick.com → session_token")
        log.error("   export KICK_SESSION_TOKEN=kat_xxx")
        return

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        all_raw: list[dict] = []
        seen: set[str] = set()
        per_cat_count: dict[str, int] = {}

        # Source 1: Featured
        featured = fetch_featured(args.language)
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
        recommended = fetch_recommended(args.language, args.max_streams)
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

        # Fetch playback_url per stream
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
