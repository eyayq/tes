#!/usr/bin/env python3
"""
Scrape live YouTube streams dari escharts.com/live/mobile-legends
→ Generate M3U dengan tvg-logo & group-title
"""

import re
import json
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi.requests import Session

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PROXY = "https://a.bhns.workers.dev/?url="
SOURCE_URL = "https://escharts.com/live/mobile-legends"
OUTPUT_FILE = "mlbb_live.m3u"
MAX_WORKERS = 5
GROUP_TITLE = "Mobile Legends"
CLIENT_NAME = "WEB_EMBEDDED_PLAYER"
CLIENT_VERSION = "2.20261105.09.00"
# ──────────────────────────────────────────────────────────────────────────────


def proxy_get(url: str, session: Session) -> str:
    """GET via Cloudflare Worker proxy"""
    full = PROXY + urllib.parse.quote(url, safe="")
    r = session.get(full, impersonate="chrome", timeout=30)
    return r.text


def proxy_post(url: str, payload: dict, session: Session) -> dict:
    """POST via Cloudflare Worker proxy"""
    full = PROXY + urllib.parse.quote(url, safe="")
    r = session.post(
        full,
        json=payload,
        impersonate="chrome",
        timeout=30,
        headers={"Content-Type": "application/json"},
    )
    return r.json()


def extract_youtube_ids(html: str) -> list[str]:
    """
    Ekstrak YouTube video ID dari HTML.
    Cari pattern:
      - iframe src="https://www.youtube.com/embed/VIDEO_ID..."
      - data-video-id="VIDEO_ID"
      - "videoId":"VIDEO_ID"
      - youtube.com/watch?v=VIDEO_ID
    """
    ids = set()

    # iframe embed
    ids.update(re.findall(r'youtube\.com/embed/([A-Za-z0-9_-]{11})', html))
    # data attributes
    ids.update(re.findall(r'data-video-id=["\']([A-Za-z0-9_-]{11})["\']', html))
    # JSON videoId
    ids.update(re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html))
    # watch URLs
    ids.update(re.findall(r'youtube\.com/watch\?v=([A-Za-z0-9_-]{11})', html))
    # youtu.be
    ids.update(re.findall(r'youtu\.be/([A-Za-z0-9_-]{11})', html))

    return list(ids)


def get_stream_info(video_id: str, session: Session) -> dict:
    """Panggil InnerTube player API untuk dapat stream info"""
    payload = {
        "context": {
            "client": {
                "clientName": CLIENT_NAME,
                "clientVersion": CLIENT_VERSION,
                "hl": "id",
                "gl": "ID",
                "platform": "MOBILE",
            }
        },
        "videoId": video_id,
    }

    url = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
    data = proxy_post(url, payload, session)

    # Cek status
    status = data.get("playabilityStatus", {}).get("status", "")
    if status != "OK":
        reason = data.get("playabilityStatus", {}).get("reason", "unknown")
        return {"id": video_id, "error": reason, "stream_url": "", "title": "", "channel": "", "thumbnail": ""}

    details = data.get("videoDetails", {})
    is_live = details.get("isLiveContent", False)

    # Thumbnail (paling besar)
    thumbnail = ""
    thumbs = details.get("thumbnail", {})
    if thumbs:
        thumbnail = max(thumbs.values(), key=lambda x: x.get("width", 0))["url"]
    # Fallback: maxresdefault
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

    # Stream URL - prefer HLS
    stream_url = ""
    sd = data.get("streamingData", {})
    if sd.get("hlsManifestUrl"):
        stream_url = sd["hlsManifestUrl"]
    elif sd.get("adaptiveFormats"):
        # Ambil format video terbaik (itag 248, 247, 244, 137)
        for itag in (248, 247, 244, 137):
            for f in sd["adaptiveFormats"]:
                if f.get("itag") == itag and f.get("url"):
                    stream_url = f["url"]
                    break
            if stream_url:
                break

    title = details.get("title", "")
    channel = details.get("author", "")

    return {
        "id": video_id,
        "title": title,
        "channel": channel,
        "thumbnail": thumbnail,
        "stream_url": stream_url,
        "live": is_live,
        "viewers": details.get("viewCount", ""),
    }


def process_video(args):
    """Worker untuk ThreadPoolExecutor"""
    video_id, session = args
    try:
        return get_stream_info(video_id, session)
    except Exception as e:
        return {"id": video_id, "error": str(e), "stream_url": "", "title": "", "channel": "", "thumbnail": ""}


def write_m3u(results: list[dict], output: str):
    """Tulis M3U dengan tvg-logo dan group-title"""
    with open(output, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for r in results:
            if not r.get("stream_url"):
                continue
            group = r.get("channel") or GROUP_TITLE
            logo = r.get("thumbnail", "")
            title = r.get("title", r["id"]).replace('"', "'")
            f.write(
                f'#EXTINF:-1 group-title="{group}",tvg-logo="{logo}",{group} | {title}\n'
            )
            f.write(f'{r["stream_url"]}\n')

    valid = sum(1 for r in results if r.get("stream_url"))
    print(f"✅ M3U ditulis ke {output} ({valid}/{len(results)} stream valid)")


def main():
    print(f"📡 Source: {SOURCE_URL}")
    print(f"📝 Output: {OUTPUT_FILE}")
    print()

    # Step 1: Scrape halaman escharts.com
    print("🔍 Step 1: Scrape halaman escharts.com...")
    session_scrape = Session()
    html = proxy_get(SOURCE_URL, session_scrape)
    session_scrape.close()

    if not html or len(html) < 100:
        print("❌ Gagal mengambil halaman (proxy mungkin tidak support GET untuk domain ini)")
        print("   Coba akses langsung tanpa proxy atau cek worker kamu")
        return

    video_ids = extract_youtube_ids(html)
    print(f"   Ditemukan {len(video_ids)} YouTube video ID")

    if not video_ids:
        print("❌ Tidak ada YouTube embed ditemukan di halaman")
        print(f"   HTML length: {len(html)} chars")
        print("   Mungkin halaman butuh JavaScript render (coba pakai playwright/selenium)")
        return

    print(f"   Video IDs: {video_ids[:5]}{'...' if len(video_ids) > 5 else ''}")
    print()

    # Step 2: Fetch stream info concurrent
    print(f"⚡ Step 2: Fetch stream info (max {MAX_WORKERS} concurrent)...")
    sessions = [Session() for _ in range(MAX_WORKERS)]
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for i, vid in enumerate(video_ids):
            s = sessions[i % MAX_WORKERS]
            fut = pool.submit(process_video, (vid, s))
            futures[fut] = vid

        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done = len(results)
            status = "✅" if r.get("stream_url") else "❌"
            title = r.get("title", r.get("error", ""))[:50]
            print(f"   [{done}/{len(video_ids)}] {status} {title}")

    for s in sessions:
        s.close()

    # Step 3: Write M3U
    print()
    write_m3u(results, OUTPUT_FILE)


if __name__ == "__main__":
    main()   
