#!/usr/bin/env python3
"""
Kick M3U Playlist Scraper
Scrape semua kategori live streaming Kick → playlist.m3u
"""

import time
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests

# ─── Config ───────────────────────────────────────────────────────────────────
MAX_WORKERS = 10          # jumlah thread paralel
REQUEST_TIMEOUT = 15      # detik
RETRY_COUNT = 3           # retry per request
RETRY_DELAY = 2           # detik antar retry
OUTPUT_FILE = "playlist.m3u"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kick-m3u")


@dataclass
class Stream:
    channel_slug: str
    title: str
    playback_url: str
    group_title: str       # kategori
    tvg_logo: str          # logo channel
    started_at: Optional[str]  # ISO format
    viewers: int


# ─── HTTP Session (thread-safe per thread) ───────────────────────────────────
import threading
_local = threading.local()

def get_session() -> cffi_requests.Session:
    if not hasattr(_local, "session"):
        _local.session = cffi_requests.Session(impersonate="chrome124")
    return _local.session


def http_get(url: str, params: dict | None = None) -> dict | None:
    """GET dengan retry."""
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = RETRY_DELAY * (attempt + 1) * 3
                log.warning("Rate limited, tunggu %ds...", wait)
                time.sleep(wait)
            else:
                log.debug("HTTP %s untuk %s", r.status_code, url)
        except Exception as e:
            log.debug("Error %s (attempt %d/%d): %s", url, attempt+1, RETRY_COUNT, e)
            time.sleep(RETRY_DELAY)
    return None


# ─── Step 1: Ambil semua subkategori ─────────────────────────────────────────
def fetch_all_subcategories() -> list[dict]:
    """Fetch semua subkategori (paginated)."""
    all_subs = []
    cursor = 0
    session = cffi_requests.Session(impersonate="chrome124")
    while True:
        r = session.get(
            "https://kick.com/api/v1/subcategories",
            params={"cursor": cursor, "limit": 100},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        items = data.get("data", [])
        all_subs.extend(items)
        log.info("Subkategori: %d diambil...", len(all_subs))

        next_cursor = data.get("pagination", {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor
        time.sleep(0.3)
    return all_subs


# ─── Step 2: Ambil live streams per kategori ─────────────────────────────────
def fetch_live_in_category(sub: dict) -> list[dict]:
    """Fetch semua stream live di satu subkategori."""
    slug = sub.get("slug")
    if not slug:
        return []
    data = http_get(
        f"https://kick.com/api/v1/categories/{slug}",
        params={"limit": 0},  # 0 = semua
    )
    if not data:
        return []
    return data.get("data", [])


# ─── Step 3: Ambil playback_url per channel ──────────────────────────────────
def fetch_playback_url(channel_slug: str) -> Optional[str]:
    """Fetch playback_url (m3u8) dari channel."""
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None
    return data.get("playback_url")


# ─── Step 4: Proses satu stream (dipanggil dari thread pool) ─────────────────
def process_stream(live: dict, category_name: str) -> Optional[Stream]:
    """
    Proses satu entry livestream → Stream object.
    Ambil playback_url dari channel API.
    """
    channel_slug = live.get("channel_slug")
    if not channel_slug:
        return None

    playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

    started_at = live.get("start_time")
    tvg_logo = live.get("channel_profile_pic", "")

    return Stream(
        channel_slug=channel_slug,
        title=live.get("title", "Untitled"),
        playback_url=playback_url,
        group_title=category_name,
        tvg_logo=tvg_logo,
        started_at=started_at,
        viewers=live.get("viewer_count", 0),
    )


# ─── Step 5: Format M3U ──────────────────────────────────────────────────────
def format_stream_title(stream: Stream) -> str:
    """Format: {title} [{HH:MM} {DD/MM/YYYY}]"""
    base = stream.title
    if stream.started_at:
        try:
            dt = datetime.fromisoformat(stream.started_at.replace("Z", "+00:00"))
            local = dt.astimezone()  # convert ke local time
            time_str = local.strftime("%H:%M")
            date_str = local.strftime("%d/%m/%Y")
            base = f"{base} [{time_str} {date_str}]"
        except Exception:
            pass
    return base


def build_m3u(streams: list[Stream]) -> str:
    """Build M3U playlist string."""
    lines = ["#EXTM3U", "#EXTINF:-1"]
    for s in streams:
        title = format_stream_title(s)
        attrs = [
            f'tvg-id="{s.group_title}.{s.channel_slug}"',
            f'tvg-name="{s.channel_slug}"',
            f'tvg-logo="{s.tvg_logo}"',
            f'group-title="{s.group_title}"',
        ]
        lines.append(",".join(attrs) + f" {title}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== Kick M3U Scraper ===")

    # 1. Ambil semua subkategori
    log.info("Mengambil semua subkategori...")
    subcategories = fetch_all_subcategories()
    log.info("Total subkategori: %d", len(subcategories))

    # 2-4. Fetch live + playback_url (paralel per kategori)
    all_streams: list[Stream] = []
    seen_slugs: set[str] = set()  # dedup

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # Submit per kategori
        futures = {}
        for sub in subcategories:
            cat_name = sub.get("name", sub.get("slug", "Unknown"))
            fut = pool.submit(fetch_live_in_category, sub)
            futures[fut] = cat_name

        # Kumpulkan hasil kategori
        category_lives: dict[str, list[dict]] = {}
        for fut in as_completed(futures):
            cat_name = futures[fut]
            try:
                lives = fut.result()
                if lives:
                    category_lives[cat_name] = lives
            except Exception as e:
                log.warning("Error kategori %s: %s", cat_name, e)

    log.info("Kategori dengan stream live: %d", len(category_lives))

    # 5. Fetch playback_url per stream (paralel)
    tasks = []
    for cat_name, lives in category_lives.items():
        for live in lives:
            slug = live.get("channel_slug")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                tasks.append((live, cat_name))

    log.info("Total stream unik: %d — fetching playback URLs...", len(tasks))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_stream, live, cat_name): live.get("channel_slug")
            for live, cat_name in tasks
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0:
                log.info("Progress: %d/%d", done, len(futures))
            try:
                stream = fut.result()
                if stream:
                    all_streams.append(stream)
            except Exception as e:
                log.debug("Error stream %s: %s", futures[fut], e)

    # 6. Sort by viewers desc
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    log.info("Stream valid dengan playback_url: %d", len(all_streams))

    # 7. Tulis M3U
    m3u_content = build_m3u(all_streams)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    log.info("✅ Selesai! File: %s (%d stream)", OUTPUT_FILE, len(all_streams))


if __name__ == "__main__":
    main()   
