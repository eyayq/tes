#!/usr/bin/env python3
"""Kick M3U Scraper — Rate Limit Fixed"""

import time
import random
import logging
import argparse
import subprocess
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests

MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"

# YouTube channels
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


# ─── HTTP with smart backoff ──────────────────────────────────────────────────
_local = threading.local()

def get_session():
    if not hasattr(_local, "session"):
        _local.session = cffi_requests.Session(impersonate="chrome124")
    return _local.session


def smart_sleep(base=0.3):
    """Sleep dengan jitter untuk hindari pattern detection."""
    time.sleep(base + random.uniform(0, 0.4))


def http_get(url: str, params: dict | None = None):
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                # Exponential backoff: 5s, 15s, 45s, 135s
                wait = 5 * (3 ** attempt)
                log.warning("429 rate limit! Tunggu %ds (attempt %d/%d)",
                           wait, attempt + 1, RETRY_COUNT)
                time.sleep(wait)
            else:
                log.debug("HTTP %s → %s", r.status_code, url)
                time.sleep(1)
        except Exception as e:
            log.debug("Error: %s", e)
            time.sleep(2)
    return None


# ─── KICK: Subcategories ──────────────────────────────────────────────────────
def fetch_all_subcategories() -> list[dict]:
    all_subs = []
    seen: set[str] = set()
    page = 1
    while True:
        data = http_get("https://kick.com/api/v1/subcategories",
                        params={"page": page, "limit": 32})
        if not data:
            break

        items = data.get("data", [])
        if not items:
            break

        new = 0
        for item in items:
            slug = item.get("slug")
            if slug and slug not in seen:
                seen.add(slug)
                all_subs.append(item)
                new += 1

        if new == 0:
            break

        last_page = data.get("last_page", 0)
        if last_page and page >= last_page:
            break

        page += 1
        smart_sleep(0.2)  # 0.2-0.6s antar halaman

    log.info("Total subcategories: %d", len(all_subs))
    return all_subs


# ─── KICK: Live per kategori ──────────────────────────────────────────────────
def fetch_live_streams(subcategory_slug: str) -> list[dict]:
    all_streams = []
    page = 1
    while True:
        data = http_get("https://kick.com/stream/livestreams/en",
                        params={"page": page, "limit": 50, "subcategory": subcategory_slug})
        if not data:
            break

        items = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if not items:
            break

        all_streams.extend(items)

        if isinstance(data, dict):
            if data.get("last_page", 0) and page >= data["last_page"]:
                break
        elif len(items) < 50:
            break

        page += 1
        smart_sleep(0.1)

    return all_streams


# ─── KICK: Proses stream ──────────────────────────────────────────────────────
def process_kick_stream(stream_data: dict, category_name: str) -> Optional[Stream]:
    """
    PENTING: Cek apakah playback_url SUDAH ada di response kategori.
    Kalau iya → tidak perlu request tambahan.
    """
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("channel_name")
        or (stream_data.get("channel", {}) or {}).get("slug", "")
    )
    if not channel_slug:
        return None

    # ── Cek playback_url langsung dari response ──
    playback_url = (
        stream_data.get("playback_url")
        or (stream_data.get("channel", {}) or {}).get("playback_url")
        or stream_data.get("hls_url")
        or ""
    )

    # Kalau tidak ada, baru fetch (1 request tambahan)
    if not playback_url:
        playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

    title = stream_data.get("title") or stream_data.get("session_title") or "Untitled"
    viewers = stream_data.get("viewer_count") or stream_data.get("viewers") or 0
    started_at = stream_data.get("start_time") or stream_data.get("created_at")
    tvg_logo = (
        stream_data.get("channel_profile_pic")
        or (stream_data.get("channel", {}) or {}).get("profile_picture", "")
        or stream_data.get("profile_picture", "")
        or ""
    )

    return Stream(
        channel_slug=channel_slug,
        title=title,
        playback_url=playback_url,
        group_title=category_name,
        tvg_logo=tvg_logo,
        started_at=started_at,
        viewers=viewers,
    )


def fetch_playback_url(channel_slug: str) -> Optional[str]:
    """Fallback: fetch playback_url dari channel API (1 request)."""
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None
    return data.get("playback_url")


# ─── YOUTUBE ──────────────────────────────────────────────────────────────────
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


def process_youtube(item: tuple) -> Optional[Stream]:
    url, name = item
    m3u8 = get_youtube_m3u8(url)
    if not m3u8:
        return None
    return Stream(
        channel_slug=name.lower().replace(" ", "_"),
        title=f"{name} LIVE",
        playback_url=m3u8,
        group_title="YouTube",
        tvg_logo=f"https://i.ytimg.com/googletv?user_id={name.replace(' ', '')}",
        started_at=None,
        viewers=0,
        platform="YouTube",
    )


# ─── M3U ──────────────────────────────────────────────────────────────────────
def format_title(s: Stream) -> str:
    base = s.title
    if s.started_at:
        try:
            dt = datetime.fromisoformat(s.started_at.replace("Z", "+00:00")).astimezone()
            base = f"{base} [{dt.strftime('%H:%M')} {dt.strftime('%d/%m/%Y')}]"
        except:
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


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--kick-only", action="store_true")
    parser.add_argument("--yt-only", action="store_true")
    args = parser.parse_args()

    log.info("=== Kick + YouTube M3U Scraper ===")
    all_streams: list[Stream] = []

    # ─── KICK ────────────────────────────────────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")

        subcategories = fetch_all_subcategories()

        # Filter: hanya kategori yang viewers > 0
        active = [s for s in subcategories if s.get("viewers", 0) > 0]
        log.info("Kategori aktif (viewers>0): %d / %d", len(active), len(subcategories))

        # Fetch live per kategori
        all_raw: list[tuple[dict, str]] = []
        seen: set[str] = set()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(fetch_live_streams, sub["slug"]): sub
                for sub in active if sub.get("slug")
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                sub = futures[fut]
                if done % 50 == 0:
                    log.info("Kategori: %d/%d", done, len(futures))
                try:
                    for s in fut.result():
                        slug = (s.get("channel_slug")
                                or s.get("channel_name")
                                or (s.get("channel", {}) or {}).get("slug", ""))
                        if slug and slug not in seen:
                            seen.add(slug)
                            all_raw.append((s, sub.get("name", sub.get("slug", ""))))
                except:
                    pass

        log.info("Stream unik: %d", len(all_raw))

        # Proses (playback_url sudah di response → minim request)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_kick_stream, raw, cat): raw
                for raw, cat in all_raw
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 500 == 0:
                    log.info("Stream: %d/%d", done, len(futures))
                try:
                    s = fut.result()
                    if s:
                        all_streams.append(s)
                except:
                    pass

        log.info("Kick valid: %d", sum(1 for s in all_streams if s.platform == "Kick"))

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
                except:
                    pass

    # ─── OUTPUT ──────────────────────────────────────────────────────────────
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    with open(args.output, "w") as f:
        f.write(build_m3u(all_streams))
    log.info("✅ %d stream → %s", len(all_streams), args.output)


if __name__ == "__main__":
    main()   
