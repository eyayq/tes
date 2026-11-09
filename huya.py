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

MAX_PER_CATEGORY = 10
MAX_STREAMS = 1000
LANGUAGE = "id"
CATEGORIES = ["games", "irl", "music", "gambling", "creative"]

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
            "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/156.0.0.0 Mobile Safari/537.36"),
            "x-app-platform": "web",
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


# ─── STEP 1: Fetch list streams (pagination) ─────────────────────────────────
def fetch_livestreams(category: str, language: str, max_streams: int = 200) -> list[dict]:
    """
    GET kick.com/stream/livestreams/{lang}?page=X&limit=50&category=Y
    → list stream dengan channel.slug
    """
    all_streams = []
    page = 1

    while len(all_streams) < max_streams:
        data = _get(f"https://kick.com/stream/livestreams/{language}",
                    params={"page": page, "limit": 50, "category": category})
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


# ─── STEP 2: Fetch playback_url dengan token ─────────────────────────────────
def fetch_channel_playback(channel_slug: str) -> Optional[str]:
    """
    GET kick.com/api/v2/channels/{slug}
    → playback_url = "https://...m3u8?token=eyJ..."  ✅ DENGAN TOKEN!
    """
    data = _get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None

    pb = data.get("playback_url", "")
    if isinstance(pb, str) and pb.startswith("http"):
        return pb
    return None


# ─── STEP 3: Process stream ───────────────────────────────────────────────────
def process_stream(stream_data: dict) -> Optional[Stream]:
    """
    1. Ambil channel.slug dari stream_data
    2. GET /api/v2/channels/{slug} → playback_url (dengan token)
    3. Build Stream object
    """
    channel = stream_data.get("channel", {})
    channel_slug = channel.get("slug", "")
    if not channel_slug:
        return None

    # Fetch playback_url dengan token
    playback_url = fetch_channel_playback(channel_slug)
    if not playback_url:
        return None

    # Metadata
    viewers = stream_data.get("viewer_count") or stream_data.get("viewers") or 0
    title = stream_data.get("session_title", "Untitled")
    started_at = stream_data.get("start_time")

    # Category
    categories = stream_data.get("categories", [])
    if categories:
        group_name = categories[0].get("name", "Unknown")
    else:
        group_name = "Unknown"

    # Logo
    user = channel.get("user", {})
    tvg_logo = user.get("profilepic") or user.get("profile_pic") or f"https://kick.com/{channel_slug}/profile"

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
    parser.add_argument("--categories", type=str, default=",".join(CATEGORIES))
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d, lang=%s, cats=%s",
             args.max_per_category, args.max_streams, args.language, categories)

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        # Step 1: Fetch list per kategori (parallel, pagination)
        all_raw: list[dict] = []
        seen: set[str] = set()

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
                    log.info("  %s: %d stream", cat, len(streams))
                except Exception as e:
                    log.debug("Error %s: %s", cat, e)
                if len(all_raw) >= args.max_streams:
                    break

        log.info("Stream unik: %d (list: %.1fs)", len(all_raw), time.time() - t0)

        # Filter per kategori
        per_cat_count: dict[str, int] = {}
        filtered: list[dict] = []
        for s in all_raw:
            cats = s.get("categories", [])
            cat_name = cats[0].get("name", "Unknown") if cats else "Unknown"
            per_cat_count[cat_name] = per_cat_count.get(cat_name, 0) + 1
            if per_cat_count[cat_name] <= args.max_per_category:
                filtered.append(s)
            if len(filtered) >= args.max_streams:
                break

        log.info("After filter: %d stream", len(filtered))

        # Step 2: Fetch playback_url per stream (parallel)
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
