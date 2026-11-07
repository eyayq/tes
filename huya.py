#!/usr/bin/env python3
"""
Kick M3U Playlist Scraper (FIXED)
Scrape semua subkategori → live streams → playback_url → playlist.m3u
"""

import time
import logging
import argparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional
import threading

from curl_cffi import requests as cffi_requests

# ─── Config ───────────────────────────────────────────────────────────────────
MAX_WORKERS = 105
REQUEST_TIMEOUT = 15
RETRY_COUNT = 3
RETRY_DELAY = 2

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


# ─── Step 1: Ambil semua subcategories (page-based pagination) ────────────────
def fetch_all_subcategories() -> list[dict]:
    """Fetch semua subcategories menggunakan page pagination."""
    all_subs = []
    page = 1
    while True:
        data = http_get(
            "https://kick.com/api/v1/subcategories",
            params={"page": page, "limit": 100},
        )
        if not data:
            break

        items = data.get("data", [])
        if not items:
            break

        all_subs.extend(items)
        log.info("Subcategories: %d diambil (page %d)...", len(all_subs), page)

        # Cek apakah ada page berikutnya
        last_page = data.get("last_page", 0)
        current = data.get("current_page", page)
        if current >= last_page:
            break
        page += 1
        time.sleep(0.2)

    return all_subs


# ─── Step 2: Ambil live streams per subcategory ───────────────────────────────
def fetch_live_streams(subcategory_slug: str) -> list[dict]:
    """
    Fetch semua live streams di satu subcategory.
    Endpoint: kick.com/stream/livestreams/en
    """
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

        # Response bisa berupa list atau dict dengan "data"
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("data", data.get("results", []))
        else:
            break

        if not items:
            break

        all_streams.extend(items)

        # Pagination check
        if isinstance(data, dict):
            last_page = data.get("last_page", 0)
            current = data.get("current_page", page)
            if current >= last_page:
                break
        else:
            # Jika list, berhenti jika kurang dari limit
            if len(items) < 50:
                break

        page += 1
        time.sleep(0.1)

    return all_streams


# ─── Step 3: Ambil playback_url ───────────────────────────────────────────────
def fetch_playback_url(channel_slug: str) -> Optional[str]:
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None
    return data.get("playback_url")


# ─── Step 4: Proses satu stream ───────────────────────────────────────────────
def process_stream(stream_data: dict, category_name: str) -> Optional[Stream]:
    """Proses satu stream entry → Stream object."""
    # Ekstrak channel slug (variasi field name)
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("channel_name")
        or stream_data.get("slug")
        or ""
    )
    if not channel_slug:
        # Coba dari nested channel object
        ch = stream_data.get("channel", {})
        if isinstance(ch, dict):
            channel_slug = ch.get("slug") or ch.get("name") or ""
    if not channel_slug:
        return None

    # Ambil playback_url
    playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

    # Ekstrak info
    title = stream_data.get("title") or stream_data.get("session_title") or "Untitled"
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
    # Coba dari nested channel
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
    )


# ─── Step 5: Format M3U ──────────────────────────────────────────────────────
def format_stream_title(stream: Stream) -> str:
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
    lines = ["#EXTM3U"]
    for s in streams:
        title = format_stream_title(s)
        attrs = (
            f'tvg-id="{s.group_title}.{s.channel_slug}",'
            f'tvg-name="{s.channel_slug}",'
            f'tvg-logo="{s.tvg_logo}",'
            f'group-title="{s.group_title}"'
        )
        lines.append(f"#EXTINF:-1,{attrs} {title}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kick M3U Scraper")
    parser.add_argument("--output", "-o", default="playlist.m3u", help="Output file")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS, help="Thread count")
    args = parser.parse_args()

    log.info("=== Kick M3U Scraper ===")
    log.info("Workers: %d | Output: %s", args.workers, args.output)

    # 1. Ambil semua subcategories
    log.info("Mengambil semua subcategories...")
    subcategories = fetch_all_subcategories()
    log.info("Total subcategories: %d", len(subcategories))

    # Filter: hanya yang viewers > 0 (ada stream live)
    active_subs = [s for s in subcategories if s.get("viewers", 0) > 0]
    log.info("Subcategories dengan stream live: %d", len(active_subs))

    # 2. Fetch live streams per subcategory (paralel)
    all_raw_streams: list[tuple[dict, str]] = []  # (stream_data, category_name)
    seen_slugs: set[str] = set()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for sub in active_subs:
            slug = sub.get("slug")
            name = sub.get("name", slug)
            if slug:
                fut = pool.submit(fetch_live_streams, slug)
                futures[fut] = (slug, name)

        done = 0
        for fut in as_completed(futures):
            done += 1
            slug, name = futures[fut]
            if done % 20 == 0:
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

    log.info("Total stream unik: %d", len(all_raw_streams))

    # 3. Fetch playback_url per stream (paralel)
    all_streams: list[Stream] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_stream, raw, cat): raw
            for raw, cat in all_raw_streams
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0:
                log.info("Playback URL progress: %d/%d", done, len(futures))
            try:
                stream = fut.result()
                if stream:
                    all_streams.append(stream)
            except Exception as e:
                log.debug("Error stream: %s", e)

    # 4. Sort by viewers desc
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    log.info("Stream valid: %d", len(all_streams))

    # 5. Tulis M3U
    m3u_content = build_m3u(all_streams)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    log.info("✅ Selesai! File: %s (%d stream)", args.output, len(all_streams))


if __name__ == "__main__":
    main()   
