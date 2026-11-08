import time
import random
import logging
import argparse
import subprocess
import threading
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests

# ─── DEFAULTS ─────────────────────────────────────────────────────────────────
MAX_WORKERS = 5
REQUEST_TIMEOUT = 15
RETRY_COUNT = 4
OUTPUT_FILE = "playlist.m3u"

MAX_PER_CATEGORY = 5    # stream per kategori
MAX_STREAMS = 10       # total maksimal stream

YOUTUBE_CHANNELS = [
    ("https://www.youtube.com/@MPLIndonesia/live", "MPL Indonesia"),
    ("https://www.youtube.com/@mlbbindonesia/live", "MLBB Indonesia"),
]

KICK_CATEGORIES = ["games", "irl", "music", "gambling", "creative"]

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

def get_session():
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
                try:
                    return r.json()
                except Exception:
                    return None
            elif r.status_code == 429:
                wait = 5 * (3 ** attempt)
                log.warning("429 rate limit! Tunggu %ds (attempt %d/%d)",
                           wait, attempt + 1, RETRY_COUNT)
                time.sleep(wait)
            else:
                time.sleep(1)
        except Exception:
            time.sleep(2)
    return None


# ─── KICK: Live per kategori ─────────────────────────────────────────────────
def fetch_live_by_category(category: str, max_streams: int) -> list[dict]:
    all_streams = []
    page = 1
    while len(all_streams) < max_streams:
        data = http_get("https://kick.com/stream/livestreams/en",
                        params={
                            "page": page,
                            "limit": 50,
                            "category": category,
                        })
        if not data:
            break

        items = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if not items:
            break

        all_streams.extend(items[:max_streams - len(all_streams)])

        if isinstance(data, dict):
            if data.get("last_page", 0) and page >= data["last_page"]:
                break
        elif len(items) < 50:
            break

        page += 1
        smart_sleep(0.1)

    return all_streams[:max_streams]


# ─── KICK: Proses stream ─────────────────────────────────────────────────────
def extract_playback_url(stream_data: dict) -> Optional[str]:
    """
    Struktur: stream_data["playback_url"] = {
        "dvr": "https://stream.kick.com/...",
        "imasdk": "",
        "live": "https://...m3u8?token=..."
    }
    """
    pb = stream_data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or ""
    if isinstance(pb, str) and pb.startswith("http"):
        return pb

    live = stream_data.get("live")
    if isinstance(live, dict):
        return live.get("hls_url") or live.get("playback_url") or live.get("url") or ""
    if isinstance(live, str) and live.startswith("http"):
        return live

    return stream_data.get("hls_url") or ""


def process_kick_stream(stream_data: dict, group_name: str) -> Optional[Stream]:
    channel_slug = (
        stream_data.get("channel_slug")
        or stream_data.get("channel_name")
        or (stream_data.get("channel", {}) or {}).get("slug", "")
    )
    if not channel_slug:
        return None

    viewers = stream_data.get("viewer_count") or stream_data.get("viewers") or 0

    playback_url = extract_playback_url(stream_data)
    if not playback_url:
        playback_url = fetch_playback_url(channel_slug)
    if not playback_url:
        return None

    title = stream_data.get("title") or stream_data.get("session_title") or "Untitled"
    started_at = stream_data.get("start_time") or stream_data.get("created_at")
    tvg_logo = (
        stream_data.get("channel_profile_pic")
        or (stream_data.get("channel", {}) or {}).get("profile_picture", "")
        or stream_data.get("profile_picture", "")
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


def fetch_playback_url(channel_slug: str) -> Optional[str]:
    data = http_get(f"https://kick.com/api/v2/channels/{channel_slug}/livestream")
    if not data:
        return None
    pb = data.get("playback_url")
    if isinstance(pb, dict):
        return pb.get("live") or ""
    if isinstance(pb, str) and pb.startswith("http"):
        return pb
    live = data.get("live")
    if isinstance(live, dict):
        return live.get("hls_url") or live.get("playback_url") or ""
    if isinstance(live, str) and live.startswith("http"):
        return live
    return ""


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
        r = get_session().get(channel_url.replace("/live", ""), timeout=REQUEST_TIMEOUT)
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
    parser = argparse.ArgumentParser(description="Kick + YouTube M3U Scraper (FAST)")
    parser.add_argument("--output", "-o", default=OUTPUT_FILE)
    parser.add_argument("--workers", "-w", type=int, default=MAX_WORKERS)
    parser.add_argument("--kick-only", action="store_true")
    parser.add_argument("--yt-only", action="store_true")
    parser.add_argument("--max-per-category", type=int, default=MAX_PER_CATEGORY)
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    args = parser.parse_args()

    t0 = time.time()
    log.info("=== Kick + YouTube M3U Scraper (FAST) ===")
    log.info("Settings: max-per-cat=%d, max-streams=%d",
             args.max_per_category, args.max_streams)
    all_streams: list[Stream] = []

    # ─── KICK (5 request, 1 per kategori) ───────────────────────────────────
    if not args.yt_only:
        log.info("─── KICK ───")
        all_raw: list[tuple[dict, str]] = []
        seen: set[str] = set()

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(fetch_live_by_category, cat, args.max_per_category): cat
                for cat in KICK_CATEGORIES
            }
            for fut in as_completed(futures):
                cat = futures[fut]
                try:
                    streams = fut.result()
                    for s in streams:
                        slug = (s.get("channel_slug")
                                or s.get("channel_name")
                                or (s.get("channel", {}) or {}).get("slug", ""))
                        if slug and slug not in seen:
                            seen.add(slug)
                            all_raw.append((s, cat))
                    log.info("  %s: %d stream", cat, len(streams))
                except Exception as e:
                    log.debug("Error %s: %s", cat, e)

        log.info("Stream unik: %d (fetch: %.1fs)", len(all_raw), time.time() - t0)

        # Proses (extract playback_url)
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
