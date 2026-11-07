#!/usr/bin/env python3
"""
Kick M3U Playlist Scraper
- POST /api/v1/stream/{video_id}/playback → m3u8 dengan token
- Thread pool + rate limit handling
- Max/min limit untuk scrape
"""

import time
import random
import logging
import argparse
import threading
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests

# ─── Config ───────────────────────────────────────────────────────────────────
MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"

# ─── Batas Scrape ─────────────────────────────────────────────────────────────
MAX_STREAMS = 3000        # maksimal jumlah stream yang di-scrape
MIN_VIEWERS = 10          # minimal viewers (skip stream di bawah ini)
MAX_CATEGORIES = 200      # maksimal kategori yang di-scrape (0 = semua)

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


# ─── HTTP Session (thread-local) ──────────────────────────────────────────────
_local = threading.local()

def get_session() -> cffi_requests.Session:
    if not hasattr(_local, "session"):
        _local.session = cffi_requests.Session(impersonate="chrome124")
    return _local.session


def smart_sleep(base=0.3):
    time.sleep(base + random.uniform(0, 0.4))


def http_get(url: str, params: dict | None = None):
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429! Tunggu %ds (attempt %d/%d)", wait, attempt + 1, RETRY_COUNT)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


def http_post(url: str, json_body: dict, headers: dict | None = None) -> dict | None:
    for attempt in range(RETRY_COUNT):
        try:
            r = get_session().post(url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429 POST! Tunggu %ds (attempt %d/%d)", wait, attempt + 1, RETRY_COUNT)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


# ─── KICK: POST Playback (m3u8 + token) ───────────────────────────────────────
def post_playback(video_id: str, channel_slug: str) -> Optional[str]:
    body = {
        "video_player": {
            "player": {
                "player_name": "web_mobile",
                "player_version": "web_mobile_2b9149d9",
                "player_software": "IVS Player",
                "player_software_version": "1.56.2"
            },
            "mux_sdk": {"sdk_available": True},
            "pal_sdk": {"sdk_available": False, "nonce": ""},
            "datazoom_sdk": {
                "sdk_available": True,
                "datazoom_sdk_version": "2.33.0",
                "om_sdk_version": "1.6.6"
            },
            "google_ads_sdk": {"sdk_available": False},
            "ima_sdk": {"sdk_available": False}
        },
        "video_session": {
            "page_type": "channel",
            "player_remote_played": False,
            "enable_sampling": False,
            "url_path": channel_slug,
            "autoplay_behaviour": "click",
            "play_muted": False,
            "viewer_connection_type": ""
        },
        "user_session": {
            "session_id": str(uuid.uuid4()),
            "player_device_id": str(uuid.uuid4()),
            "browser_lang": "en",
            "non_personalised_ads": True,
            "gdpr": 0
        }
    }

    headers = {
        "x-app-platform": "web",
        "origin": "https://kick.com",
        "referer": "https://kick.com/",
        "content-type": "application/json",
        "accept": "application/json",
    }

    result = http_post(
        f"https://web.kick.com/api/v1/stream/{video_id}/playback",
        json_body=body,
        headers=headers,
    )
    if result:
        return result.get("playback_url", {}).get("live")
    return None


def get_video_id(channel_slug: str) -> Optional[str]:
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}")
    if not data:
        return None
    livestream = data.get("livestream")
    if not livestream or not isinstance(livestream, dict):
        return None
    return livestream.get("video_id")


# ─── KICK: Subcategories ──────────────────────────────────────────────────────
def fetch_all_subcategories() -> list[dict]:
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
        if page % 50 == 0:
            log.info("Subcategories: %d (page %d)", len(all_subs), page)
        smart_sleep(0.2)

    log.info("Total subcategories: %d", len(all_subs))
    return all_subs


# ─── KICK: Live per kategori ──────────────────────────────────────────────────
def fetch_live_streams(subcategory_slug: str) -> list[dict]:
    all_streams = []
    page = 1
    while True:
        data = http_get(
            "https://kick.com/stream/livestreams/en",
            params={"page": page, "limit": 50, "subcategory": subcategory_slug},
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
            if data.get("last_page", 0) and page >= data["last_page"]:
                break
        elif len(items) < 50:
            break

        page += 1
        smart_sleep(0.1)

    return all_streams


# ─── KICK: Proses stream ──────────────────────────────────────────────────────
def process_kick_stream(stream_data: dict, category_name: str) -> Optional[Stream]:
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("channel_name")
        or (stream_data.get("channel", {}) or {}).get("slug", "")
    )
    if not channel_slug:
        return None

    # Filter: min viewers
    viewers = stream_data.get("viewer_count") or stream_data.get("viewers") or 0
    if viewers < MIN_VIEWERS:
        return None

    # Cek video_id dari response
    video_id = (
        stream_data.get("video_id")
        or (stream_data.get("livestream", {}) or {}).get("video_id")
        or (stream_data.get("channel", {}) or {}).get("livestream", {}).get("video_id")
        or ""
    )

    if not video_id:
        video_id = get_video_id(channel_slug)
    if not video_id:
        return None

    # POST playback → m3u8 + token
    playback_url = post_playback(video_id, channel_slug)
    if not playback_url:
        return None

    title = stream_data.get("title") or stream_data.get("session_title") or "Untitled"
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


# ─── M3U FORMAT ───────────────────────────────────────────────────────────────
def format_title(s: Stream) -> str:
    base = s.title
    if s.started_at:
        try:
            dt = datetime.fromisoformat(s.started_at.replace("Z", "+00:00")).astimezone()
            base = f"{base} [{dt.strftime('%H:%M')} {dt.strftime('%d/%m/%Y')}]"
        except Exception:
            pass
    return base


def build_m3u(streams: list[Stream]) -> str:
    lines = ["#EXTM3U"]
    for s in streams:
        attrs = (
            f'tvg-id="{s.group_title}.{s.channel_slug}",'
            f'tvg-name="{s.channel_slug}",'
            f'tvg-logo="{s.tvg_logo}",'
            f'group-title="{s.group_title}"'
        )
        lines.append(f"#EXTINF:-1,{attrs} {format_title(s)}")
        lines.append(s.playback_url)
    return "\n".join(lines) + "\n"


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kick M3U Scraper")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE, help="Output file")
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS, help="Thread count")
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS, help="Max streams to scrape")
    parser.add_argument("--min-viewers", type=int, default=MIN_VIEWERS, help="Min viewers threshold")
    parser.add_argument("--max-categories", type=int, default=MAX_CATEGORIES, help="Max categories (0=all)")
    args = parser.parse_args()

    log.info("=== Kick M3U Scraper ===")
    log.info("Workers: %d | Max Streams: %d | Min Viewers: %d | Max Categories: %d",
             args.workers, args.max_streams, args.min_viewers, args.max_categories)
    log.info("Output: %s", args.output)

    all_streams: list[Stream] = []

    # 1. Ambil semua subcategories
    subcategories = fetch_all_subcategories()

    # Filter: viewers > 0
    active = [s for s in subcategories if s.get("viewers", 0) > 0]
    log.info("Kategori aktif: %d / %d", len(active), len(subcategories))

    # Sort by viewers desc → ambil kategori terpopuler dulu
    active.sort(key=lambda x: x.get("viewers", 0), reverse=True)

    # Limit kategori
    if args.max_categories > 0:
        active = active[:args.max_categories]
        log.info("Dibatasi: %d kategori terpopuler", len(active))

    # 2. Fetch live streams per kategori (paralel)
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
                    slug = (
                        s.get("channel_slug")
                        or s.get("channel_name")
                        or (s.get("channel", {}) or {}).get("slug", "")
                    )
                    # Filter min viewers
                    v = s.get("viewer_count") or s.get("viewers") or 0
                    if v < args.min_viewers:
                        continue
                    # Dedup
                    if slug and slug not in seen:
                        seen.add(slug)
                        all_raw.append((s, sub.get("name", sub.get("slug", ""))))
            except Exception:
                pass

    log.info("Stream unik (viewers >= %d): %d", args.min_viewers, len(all_raw))

    # Limit max streams
    if args.max_streams > 0:
        all_raw = all_raw[:args.max_streams]
        log.info("Dibatasi: %d stream", len(all_raw))

    # 3. POST playback per stream (paralel) → m3u8 + token
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_kick_stream, raw, cat): raw
            for raw, cat in all_raw
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0:
                log.info("Playback: %d/%d", done, len(futures))
            try:
                s = fut.result()
                if s:
                    all_streams.append(s)
            except Exception:
                pass

    # 4. Sort + output
    all_streams.sort(key=lambda s: s.viewers, reverse=True)
    log.info("Stream valid: %d", len(all_streams))

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(build_m3u(all_streams))

    log.info("✅ Selesai! File: %s (%d stream)", args.output, len(all_streams))


if __name__ == "__main__":
    main()   
