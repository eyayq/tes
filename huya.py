"""
sporteurope_iptv.py — Generate IPTV M3U playlist dari SportEurope.tv
Output: sporteurope.m3u (siap di-load ke IPTV player)
"""

import json
import time
import logging
import os
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi_requests

# ─── CONFIG ─────────────────────────────────────────────
API_BASE = "https://api.sporteurope.tv"
WEB_BASE = "https://sporteurope.tv"
IMG_CDN = "https://img-cdn.do.sporteurope.tv/images"
LANG = "en"
MAX_WORKERS = 50
OUTPUT_FILE = "playlist.m3u"
GROUP_TITLE = "badminton court"
WIB = timezone(timedelta(hours=7))

USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/159.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://sporteurope.tv"
REFERER = "https://sporteurope.tv/"

SESSION = cffi_requests.Session(impersonate="chrome", timeout=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sporteu")


# ─── HEADERS ────────────────────────────────────────────

def _headers():
    return {
        "user-agent": USER_AGENT,
        "accept": "application/json, text/plain, */*",
        "origin": ORIGIN,
        "referer": REFERER,
        "accept-language": "en-US,en;q=0.9",
    }


def _headers_player():
    return {
        "user-agent": USER_AGENT,
        "accept": "*/*",
        "origin": ORIGIN,
        "referer": REFERER,
        "accept-language": "en-US,en;q=0.9",
        "x-accept-language": LANG,
        "x-version": "2",
        "content-type": "application/json",
    }


# ─── HELPERS ────────────────────────────────────────────

def image_id_to_url(image_id, size="320x180"):
    """UUID → CDN image URL"""
    if not image_id:
        return ""
    uid = image_id.replace("-", "")
    path = "/".join(uid[:8]) + "/" + uid[8:12] + "/" + uid[12:16] + "/" + uid[16:20] + "/" + uid[20:]
    return f"{IMG_CDN}/{path}/{size}.webp"


def to_wib(iso_str):
    """ISO UTC → 'HH:MM WIB DD-MM-YYYY'"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(WIB)
        return f"{dt.strftime('%H:%M')} WIB {dt.strftime('%d-%m-%Y')}"
    except:
        return ""


def get_m3u8(stream_urls):
    """Ambil URL M3U8 (prioritas LIVESTREAM)."""
    for su in stream_urls:
        if su["type"] == "LIVESTREAM":
            return su["hls"]
    if stream_urls:
        return stream_urls[0]["hls"]
    return None


# ─── API ────────────────────────────────────────────────

def get_live_asset_ids():
    """GET semua asset ID yang sedang live (1 request, fast)."""
    url = f"{API_BASE}/api/frontend/public/live-ids/assets?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    return r.json()


def get_player_asset(asset_id):
    """GET detail + M3U8 untuk 1 asset."""
    url = f"{API_BASE}/api/web-player/personal/assets/{asset_id}"
    r = SESSION.get(url, headers=_headers_player())
    if r.status_code != 200:
        return None

    data = r.json()

    # Stream URLs
    stream_urls = []
    for track in data.get("tracks", []):
        for src in track.get("sources", []):
            if src.get("hls"):
                stream_urls.append({
                    "hls": src["hls"],
                    "type": src.get("type", ""),
                })

    if not stream_urls:
        return None

    # Logo
    logo_url = ""
    images = data.get("images", [])
    for img in images:
        if img.get("width") == 320:
            logo_url = img["src"]
            break
    if not logo_url and images:
        logo_url = images[0]["src"]
    if not logo_url:
        logo_url = image_id_to_url(data.get("image_id", ""), "320x180")

    profile = data.get("profile", {})
    sport = profile.get("sport_type", {}).get("name", "Other")

    return {
        "name": data.get("name", ""),
        "sport": sport,
        "logo": logo_url,
        "start_date": data.get("content_start_date", ""),
        "m3u8": get_m3u8(stream_urls),
    }


# ─── M3U GENERATOR ──────────────────────────────────────

def generate_m3u(results):
    """Generate IPTV M3U playlist."""
    lines = ["#EXTM3U"]

    for r in results:
        if not r["m3u8"]:
            continue

        # Nama + waktu di akhir
        time_str = to_wib(r["start_date"])
        if time_str:
            title = f"{r['name']} [{time_str}]"
        else:
            title = r["name"]

        # Group by sport
        group = r["sport"]

        # Logo
        logo = r["logo"]

        lines.append(
            f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}'
        )
        lines.append(r["m3u8"])

    return "\n".join(lines) + "\n"


# ─── MAIN ───────────────────────────────────────────────

def scrape():
    """1x scrape, return list results."""
    # 1. Get all live asset IDs
    asset_ids = get_live_asset_ids()
    log.info(f"Found {len(asset_ids)} live assets")

    if not asset_ids:
        return []

    # 2. Fetch details concurrently
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_player_asset, aid): aid for aid in asset_ids}
        for fut in as_completed(futures):
            try:
                data = fut.result()
                if data and data["m3u8"]:
                    results.append(data)
            except Exception as e:
                log.debug(f"Error: {e}")

    # Sort by start date (terbaru dulu)
    results.sort(key=lambda x: x.get("start_date", ""), reverse=True)
    return results


def main():
    print("=" * 60)
    print(f"  📺 SportEurope IPTV Playlist Generator")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}")
    print("=" * 60)

    results = scrape()

    if not results:
        print("  ⚠ Tidak ada live stream.")
        return

    # Generate M3U
    m3u = generate_m3u(results)

    # Write file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u)

    # Stats
    sports = {}
    for r in results:
        sports[r["sport"]] = sports.get(r["sport"], 0) + 1

    print(f"\n  ✅ {OUTPUT_FILE} → {len(results)} channels")
    print(f"  Sports: {json.dumps(sports, indent=4)}")

    # Preview
    print(f"\n{'─' * 60}")
    print("  PREVIEW (first 3):")
    print(f"{'─' * 60}")
    lines = m3u.strip().split("\n")
    preview = 0
    i = 1  # skip #EXTM3U
    while i < len(lines) and preview < 3:
        if lines[i].startswith("#EXTINF"):
            print(f"\n  {lines[i][:150]}...")
            print(f"  {lines[i+1][:80]}...")
            preview += 1
            i += 2
        else:
            i += 1

    print(f"\n  💾 Saved: {os.path.abspath(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()   
