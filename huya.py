#!/usr/bin/env python3
"""
Kick + YouTube M3U Playlist Scraper (Complete)
Scrape semua subkategori Kick live + YouTube channels → playlist.m3u
"""

import time
import logging
import argparse
import subprocess
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests

# ─── Config ───────────────────────────────────────────────────────────────────
MAX_WORKERS = 300
REQUEST_TIMEOUT = 15
RETRY_COUNT = 3
RETRY_DELAY = 2
OUTPUT_FILE = "playlist.m3u"

# YouTube channels yang ingin di-monitor (tambah sesukamu)
YOUTUBE_CHANNELS = [
    ("https://www.youtube.com/@MPLIndonesia/live", "MPL Indonesia", "MPL Indonesia"),
    ("https://www.youtube.com/@mlbbindonesia/live", "MLBB Indonesia", "MLBB Indonesia"),
    # ("https://www.youtube.com/@channelname/live", "Channel Name", "Group Title"),
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


# ─── Thread-local session ─────────────────────────────────────────────────────
_local = threading.local()

def get_session() -> cffi_requests.Session:
    if not hasattr(_local, "session"):
        _local.session = cffi_requests.Session(impersonate="chrome124")
    return _local.session


def http_get(url: str, params: dict | None = None) -> dict | list | None:
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
            log.debug("Error %s (attempt %d/%d): %s", url, attempt + 1, RETRY_COUNT, e)
            time.sleep(RETRY_DELAY)
    return None


# ─── KICK: Step 1 — Ambil semua subcategories ─────────────────────────────────
def fetch_all_subcategories() -> list[dict]:
    """Fetch semua subcategories (dedup + stop di last_page)."""
    all_subs = []
    seen: set[str] = set()
    page = 1
    while True:
        data = http_get(
            "https://kick.com/api/v1/subcategories",
            params={"page": page, "limit": 32},
        )
        if not data:
            break

        items = data.get("data", [])
        if not items:
            break

        new_count = 0
        for item in items:
            slug = item.get("slug")
            if slug and slug not in seen:
                seen.add(slug)
                all_subs.append(item)
                new_count += 1

        log.info("Page %d: +%d baru (total %d)", page, new_count, len(all_subs))

        # Stop jika duplikat atau sudah last_page
        if new_count == 0:
            log.info("Tidak ada data baru — berhenti.")
            break

        last_page = data.get("last_page", 0)
        if last_page and page >= last_page:
            break

        page += 1
        time.sleep(0.1)

    return all_subs


# ─── KICK: Step 2 — Ambil live streams per subcategory ─────────────────────────
def fetch_live_streams(subcategory_slug: str) -> list[dict]:
    """Fetch semua live streams di satu subcategory."""
    all_streams = []
    page = 1
    while True:
        data = http_get(
            "https://kick.com/stream/livestreams/en",
            params={
                "page": page,
                "limit": 50,
                "subcategory": subcategory_slug,
            },
        )
        if not data:
            break

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("data", data.get("results", []))
        else:
            break

        if not items:
            break

        all_streams.extend(items)

        if isinstance(data, dict):
            last_page = data.get("last_page", 0)
            if last_page and page >= last_page:
                break
        else:
            if len(items) < 50:
                break

        page += 1
        time.sleep(0.1)

    return all_streams


# ─── KICK: Step 3 — Ambil playback_url ─────────────────────────────────────────
def fetch_playback_url(channel_slug: str) -> Optional[str]:
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None
    return data.get("playback_url")


# ─── KICK: Step 4 — Proses satu stream ─────────────────────────────────────────
def process_kick_stream(stream_data: dict, category_name: str) -> Optional[Stream]:
    """Proses satu stream entry Kick → Stream object."""
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("channel_name")
        or stream_data.get("slug")
        or ""
    )
    if not channel_slug:
        ch = stream_data.get("channel", {})
        if isinstance(ch, dict):
            channel_slug = ch.get("slug") or ch.get("name") or ""
    if not channel_slug:
        return None

    playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

    title = (
        stream_data.get("title")
        or stream_data.get("session_title")
        or "Untitled"
    )
    viewers = (
        stream_data.get("viewer_count")
        or stream_data.get("viewers")
        or 0
    )
    started_at = (
        stream_data.get("start_time")
        or stream_data.get("started_at")
        or stream_data.get("created_at")
        or None
    )
    tvg_logo = (
        stream_data.get("channel_profile_pic")
        or stream_data.get("profile_picture")
        or stream_data.get("thumbnail_url")
        or ""
    )
    if not tvg_logo:
        ch = stream_data.get("channel", {})
        if isinstance(ch, dict):
            tvg_logo = ch.get("profile_picture") or ch.get("profile_pic") or ""

    return Stream(
        channel_slug=channel_slug,
        title=title,
        playback_url=playback_url,
        group_title=category_name,
        tvg_logo=tvg_logo,
        started_at=started_at,
        viewers=viewers,
        platform="Kick",
    )


# ─── YOUTUBE: Ambil m3u8 via yt-dlp (web_safari client) ─────────────────────────
def get_youtube_m3u8(channel_url: str) -> Optional[str]:
    """Ambil m3u8 YouTube live via yt-dlp dengan client web_safari."""
    try:
        r = subprocess.run(
            [
                "yt-dlp", "-g", "-f", "b[protocol=m3u8]",
                "--extractor-args", "youtube:player_client=web_safari",
                "--no-warnings", "--socket-timeout", "15",
                channel_url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def process_youtube_channel(item: tuple) -> Optional[Stream]:
    """Cek apakah YouTube channel live + ambil m3u8."""
    url, name, group = item
    m3u8 = get_youtube_m3u8(url)
    if not m3u8:
        return None  # offline

    return Stream(
        channel_slug=name.lower().replace(" ", "_"),
        title=f"{name} LIVE",
        playback_url=m3u8,
        group_title=group,
        tvg_logo=f"https://i.ytimg.com/googletv?user_id={name.replace(' ', '')}",
        started_at=None,
        viewers=0,
        platform="YouTube",
    )


# ─── M3U FORMAT ────────────────────────────────────────────────────────────────
def format_stream_title(stream: Stream) -> str:
    """Format: {title} [{HH:MM} {DD/MM/YYYY}]"""
    base = stream.title
    if stream.started_at:
        try:
            dt = datetime.fromisoformat(stream.started_at.replace("Z", "+00:00"))
            local = dt.astimezone()
            base = f"{base} [{local.strftime('%H:%M')} {local.strftime('%d/%m/%Y')}]"
        except Exception:
            pass
    return base


def build_m3u(streams: list[Stream]) -> str:
    """Build M3U playlist string."""
    lines = ["#EXTM3U"]
    for s in streams:
        title = format_stream_title(s)
        attrs = (
            f'tvg-id="{s.platform}.{s.group_title}.{s.channel_slug}",'
            f'tvg-name="{s.channel_slug}",'
            f'tvg-logo="{s.tvg_logo}",'
            f'group-title="{s.group_title}"'
        )
        lines.append(f"#EXTINF:-1,{attrs} {title}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kick + YouTube M3U Scraper")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE, help="Output file")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS, help="Thread count")
    parser.add_argument("--kick-only", action="store_true", help="Skip YouTube")
    parser.add_argument("--yt-only", action="store_true", help="Skip Kick")
    args = parser.parse_args()

    log.info("=== Kick + YouTube M3U Scraper ===")
    log.info("Workers: %d | Output: %s", args.workers, args.output)

    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        # 1. Ambil semua subcategories
        log.info("Mengambil semua subcategories...")
        subcategories = fetch_all_subcategories()
        log.info("Total subcategories unik: %d", len(subcategories))

        # 2. Fetch live streams per subcategory (paralel)
        all_raw_streams: list[tuple[dict, str]] = []
        seen_slugs: set[str] = set()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {}
            for sub in subcategories:
                slug = sub.get("slug")
                name = sub.get("name", slug)
                if slug:
                    fut = pool.submit(fetch_live_streams, slug)
                    futures[fut] = (slug, name)

            done = 0
            for fut in as_completed(futures):
                done += 1
                slug, name = futures[fut]
                if done % 100 == 0:
                    log.info("Kategori progress: %d/%d", done, len(futures))
                try:
                    streams = fut.result()
                    for s in streams:
                        ch_slug = (
                            s.get("channel_slug")
                            or s.get("channel_name")
                            or (s.get("channel", {}) or {}).get("slug", "")
                        )
                        if ch_slug and ch_slug not in seen_slugs:
                            seen_slugs.add(ch_slug)
                            all_raw_streams.append((s, name))
                except Exception as e:
                    log.debug("Error kategori %s: %s", name, e)

        log.info("Total stream unik Kick: %d", len(all_raw_streams))

        # 3. Fetch playback_url per stream (paralel)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_kick_stream, raw, cat): raw
                for raw, cat in all_raw_streams
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 200 == 0:
                    log.info("Playback URL progress: %d/%d", done, len(futures))
                try:
                    stream = fut.result()
                    if stream:
                        all_streams.append(stream)
                except Exception as e:
                    log.debug("Error stream: %s", e)

        log.info("Stream valid Kick: %d", sum(1 for s in all_streams if s.platform == "Kick"))

    # ─── YOUTUBE ─────────────────────────────────────────────────────────────
    if not args.kick_only and YOUTUBE_CHANNELS:
        log.info("─── YOUTUBE ───")
        log.info("Mengecek %d channel YouTube...", len(YOUTUBE_CHANNELS))

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(process_youtube_channel, ch): ch[1]
                for ch in YOUTUBE_CHANNELS
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    stream = fut.result()
                    if stream:
                        log.info("  ✅ %s LIVE", name)
                        all_streams.append(stream)
                    else:
                        log.info("  ❌ %s offline", name)
                except Exception as e:
                    log.debug("Error YouTube %s: %s", name, e)

    # ─── OUTPUT ──────────────────────────────────────────────────────────────
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    log.info("Total stream: %d", len(all_streams))

    m3u_content = build_m3u(all_streams)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    log.info("✅ Selesai! File: %s (%d stream)", args.output, len(all_streams))


if __name__ == "__main__":
    main()   
