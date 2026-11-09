import time
import random
import logging
import argparse
import subprocess
import threading
import re
import json
import os
import uuid as uuid_lib
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

MAX_PER_CATEGORY = 100
MAX_STREAMS = 3000
LANGUAGE = "id"
CATEGORIES = ["games", "irl", "music", "gambling", "creative"]
LANGUAGES = ["id", "jp", "en"]  # untuk featured/recommended

# ─── SESSION TOKEN ────────────────────────────────────────────────────────────
KICK_SESSION_TOKEN = os.getenv(
    "KICK_SESSION_TOKEN",
    "kat_aBAhHd_aLVu-4dyfYCzpja9SVvFyEXbOeXwoYHfxAuQ"
)

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


def _get(url: str, params: dict | None = None) -> Optional[dict]:
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


def _post(url: str, json_body: dict) -> Optional[dict]:
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().post(url, json=json_body, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429! Tunggu %ds", wait)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: kick.com/stream/livestreams (PAGINATION, banyak stream)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_livestreams(category: str, language: str, max_streams: int = 200) -> list[dict]:
    """
    GET kick.com/stream/livestreams/{lang}?page=X&limit=135&category=Y
    → ratusan stream, pagination, channel.slug
    """
    all_streams = []
    page = 1

    while len(all_streams) < max_streams:
        data = _get(f"https://kick.com/stream/livestreams/{language}",
                    params={"page": page, "limit": 100, "category": category})
        if not data:
            break

        items = data.get("data", [])
        if not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])

        last_page = data.get("last_page", 0)
        if last_page and page >= last_page:
            break
        if len(items) < 50:
            break

        page += 1
        smart_sleep(0.2)

    return all_streams[:max_streams]


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: web.kick.com/api/v1/livestreams/featured (stream populer)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_featured(language: str) -> list[dict]:
    """
    GET web.kick.com/api/v1/livestreams/featured?language=X
    → ~14-24 stream populer, sudah punya UUID + channel.slug
    """
    data = _get("https://web.kick.com/api/v1/livestreams/featured",
                params={"language": language})
    if not data:
        return []
    return (data.get("data") or {}).get("livestreams", [])


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: web.kick.com/api/v1/recommendations/livestreams/list (cursor)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_recommended(language: str, max_streams: int = 50) -> list[dict]:
    """
    GET web.kick.com/api/v1/recommendations/livestreams/list?limit=50&language=X
    → stream recommended, cursor pagination, sudah punya UUID + channel.slug
    """
    all_streams = []
    cursor = None

    while len(all_streams) < max_streams:
        params = {"limit": 50, "language": language}
        if cursor:
            params["cursor"] = cursor

        data = _get("https://web.kick.com/api/v1/recommendations/livestreams/list",
                    params=params)
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
# TOKEN: kick.com/api/v2/channels/{slug} → playback_url (DENGAN TOKEN)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_channel_playback(channel_slug: str) -> Optional[str]:
    """
    GET kick.com/api/v2/channels/{slug}
    → playback_url = "https://...m3u8?token=eyJ..."  ✅
    """
    data = _get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None

    pb = data.get("playback_url", "")
    if isinstance(pb, str) and pb.startswith("http"):
        return pb
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN (FALLBACK): POST web.kick.com/api/v1/stream/{uuid}/playback
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_playback_by_uuid(stream_uuid: str) -> Optional[str]:
    """
    POST web.kick.com/api/v1/stream/{uuid}/playback
    → playback_url["live"] = "https://...m3u8?token=eyJ..."
    (fallback jika /api/v2/channels/{slug} gagal)
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
            "session_id": str(uuid_lib.uuid4()),
            "player_device_id": str(uuid_lib.uuid4()),
            "browser_lang": "id",
            "non_personalised_ads": True,
            "gdpr": 0,
        },
    }

    data = _post(f"https://web.kick.com/api/v1/stream/{stream_uuid}/playback",
                 json_body=body)
    if not data:
        return None
    pb = data.get("playback_url", {})
    if isinstance(pb, dict):
        return pb.get("live") or ""
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS: Normalisasi semua source → Stream object
# ═══════════════════════════════════════════════════════════════════════════════

def process_stream(stream_data: dict) -> Optional[Stream]:
    """
    Handle 2 format stream_data:

    Format A (kick.com/stream/livestreams):
    {
        "channel": {"slug": "donoci", "user": {"username": "Donoci", "profilepic": "..."}},
        "session_title": "...",
        "viewer_count": 0,
        "start_time": "2026-11-09 04:58:03",
        "categories": [{"name": "Call of Duty: Warzone"}]
    }

    Format B (web.kick.com featured/recommended):
    {
        "id": "01a1bd05-...",
        "channel": {"slug": "nuxanor", "profile_pic": "...", "username": "Nuxanor"},
        "title": "...",
        "viewer_count": 1628,
        "start_time": "2026-11-08T21:16:45Z",
        "category": {"name": "Just Chatting"}
    }
    """
    channel = stream_data.get("channel", {})
    channel_slug = channel.get("slug", "")
    if not channel_slug:
        return None

    # ── Fetch playback_url (dengan token) ──
    playback_url = fetch_channel_playback(channel_slug)

    # Fallback: jika /api/v2/channels gagal, coba POST by UUID
    if not playback_url:
        stream_uuid = stream_data.get("id", "")
        if stream_uuid and "-" in stream_uuid:  # UUID format
            playback_url = fetch_playback_by_uuid(stream_uuid)

    if not playback_url:
        return None

    # ── Metadata (handle 2 format) ──
    viewers = stream_data.get("viewer_count") or stream_data.get("viewers") or 0
    title = stream_data.get("session_title") or stream_data.get("title") or "Untitled"
    started_at = stream_data.get("start_time")

    # Category (format A: "categories" list, format B: "category" dict)
    categories = stream_data.get("categories", [])
    if categories:
        group_name = categories[0].get("name", "Unknown")
    else:
        category = stream_data.get("category", {})
        group_name = category.get("name", "Unknown")

    # Logo (format A: channel.user.profilepic, format B: channel.profile_pic)
    user = channel.get("user", {})
    tvg_logo = (user.get("profilepic")
                or user.get("profile_pic")
                or channel.get("profile_pic")
                or f"https://kick.com/{channel_slug}/profile")

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
            if raw.isdigit():
                dt = datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone()
            else:
                try:
                    dt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).astimezone()
                except ValueError:
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
    parser.add_argument("--categories", type=str, default=",".join(CATEGORIES))
    parser.add_argument("--languages", type=str, default=",".join(LANGUAGES),
                        help="Languages untuk featured/recommended (default: id,en)")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    languages = [l.strip() for l in args.languages.split(",") if l.strip()]

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s, cats=%s, langs=%s",
             args.max_per_category, args.max_streams, args.language, categories, languages)

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        all_raw: list[dict] = []
        seen: set[str] = set()

        # ── SOURCE 1: kick.com/stream/livestreams (pagination) ──
        log.info("[1/3] Fetching kick.com/stream/livestreams...")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(fetch_livestreams, cat, args.language, args.max_streams): cat
                for cat in categories
            }
            for fut in as_completed(futures):
                cat = futures[fut]
                try:
                    streams = fut.result()
                    for s in streams:
                        slug = s.get("channel", {}).get("slug", "")
                        if slug and slug not in seen:
                            seen.add(slug)
                            all_raw.append(s)
                    log.info("  livestreams/%s: %d stream", cat, len(streams))
                except Exception as e:
                    log.debug("Error livestreams %s: %s", cat, e)
                if len(all_raw) >= args.max_streams:
                    break

        # ── SOURCE 2: web.kick.com featured ──
        log.info("[2/3] Fetching web.kick.com featured...")
        for lang in languages:
            featured = fetch_featured(lang)
            for s in featured:
                slug = s.get("channel", {}).get("slug", "")
                if slug and slug not in seen:
                    seen.add(slug)
                    all_raw.append(s)
            log.info("  featured/%s: %d stream", lang, len(featured))
            smart_sleep(0.3)

        # ── SOURCE 3: web.kick.com recommended ──
        log.info("[3/3] Fetching web.kick.com recommended...")
        for lang in languages:
            recommended = fetch_recommended(lang, args.max_streams)
            for s in recommended:
                slug = s.get("channel", {}).get("slug", "")
                if slug and slug not in seen:
                    seen.add(slug)
                    all_raw.append(s)
            log.info("  recommended/%s: %d stream", lang, len(recommended))
            smart_sleep(0.3)

        log.info("Stream total (3 source): %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

        # ── Filter per kategori ──
        per_cat_count: dict[str, int] = {}
        filtered: list[dict] = []
        for s in all_raw:
            # Handle 2 format category
            cats = s.get("categories", [])
            if cats:
                cat_name = cats[0].get("name", "Unknown")
            else:
                cat_name = s.get("category", {}).get("name", "Unknown")

            per_cat_count[cat_name] = per_cat_count.get(cat_name, 0) + 1
            if per_cat_count[cat_name] <= args.max_per_category:
                filtered.append(s)
            if len(filtered) >= args.max_streams:
                break

        log.info("After filter: %d stream", len(filtered))

        # ── Fetch playback_url (dengan token) per stream ──
        if filtered:
            log.info("Fetching playback_url (dengan token)...")
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(process_stream, s): s for s in filtered}
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
