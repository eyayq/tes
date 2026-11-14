"""
sporteurope.py — Scraper SportEurope.tv (lengkap: M3U8 + tvg-logo + group-title + waktu WIB)
"""

import json
import time
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi_requests

# ─── CONFIG ─────────────────────────────────────────────
API_BASE = "https://api.sporteurope.tv"
WEB_BASE = "https://sporteurope.tv"
IMG_CDN = "https://img-cdn.do.sporteurope.tv/images"
LANG = "en"
PER_PAGE = 10
MAX_WORKERS = 50
GROUP_TITLE = "SportEurope Live"
WIB = timezone(timedelta(hours=7))

USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/159.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://sporteurope.tv"
REFERER = "https://sporteurope.tv/"

SESSION = cffi_requests.Session(impersonate="chrome", timeout=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sporteu")


def _headers():
    return {
        "user-agent": USER_AGENT,
        "accept": "application/json, text/plain, */*",
        "origin": ORIGIN,
        "referer": REFERER,
        "accept-language": "id-ID,id;q=0.9,en-US;q=0.8",
    }


def _headers_player():
    return {
        "user-agent": USER_AGENT,
        "accept": "*/*",
        "origin": ORIGIN,
        "referer": REFERER,
        "accept-language": "id-ID,id;q=0.9,en-US;q=0.8",
        "x-accept-language": LANG,
        "x-version": "2",
        "content-type": "application/json",
    }


# ─── HELPERS ────────────────────────────────────────────

def image_id_to_url(image_id, size="320x180"):
    """Convert image_id UUID ke CDN URL.
    Contoh: a32d3f0a-6019-4fb6-94aa-51f503d5b6c2
    → https://img-cdn.do.sporteurope.tv/images/a/3/2/d/3/f/0/a/6019/4fb6/94aa/51f503d5b6c2/320x180.webp
    """
    if not image_id:
        return ""
    uid = image_id.replace("-", "")  # 32 chars
    # Pattern: 8 chars (each separated) + 4 + 4 + 4 + 12
    path = "/".join(uid[:8]) + "/" + uid[8:12] + "/" + uid[12:16] + "/" + uid[16:20] + "/" + uid[20:]
    return f"{IMG_CDN}/{path}/{size}.webp"


def to_wib(iso_str):
    """Convert ISO UTC string ke WIB. Return: '13:50 WIB 14-11-2026'"""
    if not iso_str:
        return "TBD"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(WIB)
        return f"{dt.strftime('%H:%M')} WIB {dt.strftime('%d-%m-%Y')}"
    except:
        return "TBD"


# ─── 1. HEADER TAGS ────────────────────────────────────

def get_header_tags():
    url = f"{API_BASE}/api/web/public/header-tags?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    data = r.json()
    sports = []
    for s in data.get("data", []):
        sports.append({"id": s["id"], "name": s["name"], "slug": s["slug"]})
    return sports


# ─── 2. LIVE ASSET IDS ─────────────────────────────────

def get_live_asset_ids():
    url = f"{API_BASE}/api/frontend/public/live-ids/assets?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    return r.json()


def get_live_profile_ids():
    url = f"{API_BASE}/api/frontend/public/live-ids/profiles?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    return r.json()


# ─── 3. PLAYER ASSET (M3U8) ────────────────────────────

def get_player_asset(asset_id):
    url = f"{API_BASE}/api/web-player/personal/assets/{asset_id}"
    r = SESSION.get(url, headers=_headers_player())
    if r.status_code != 200:
        return None

    data = r.json()

    # Ekstrak M3U8
    stream_urls = []
    for track in data.get("tracks", []):
        for src in track.get("sources", []):
            if src.get("hls"):
                stream_urls.append({
                    "hls": src["hls"],
                    "title": src.get("title", ""),
                    "type": src.get("type", ""),
                    "playback_id": src.get("mux", {}).get("playbackId", ""),
                })

    # Ekstrak logo dari images
    logo_url = ""
    images = data.get("images", [])
    for img in images:
        if img.get("width") == 320:
            logo_url = img["src"]
            break
    if not logo_url and images:
        logo_url = images[0]["src"]

    # Fallback: dari image_id
    if not logo_url:
        image_id = data.get("image_id", "")
        if image_id:
            logo_url = image_id_to_url(image_id, "320x180")

    profile = data.get("profile", {})
    sport_name = profile.get("sport_type", {}).get("name", "")
    profile_name = profile.get("name", "")
    profile_image_id = profile.get("image_id", "")

    # Fallback logo dari profile
    if not logo_url and profile_image_id:
        logo_url = image_id_to_url(profile_image_id, "320x180")

    return {
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "slug": data.get("slug", ""),
        "type": data.get("type", ""),
        "currently_live": data.get("currently_live", False),
        "sport": sport_name,
        "profile_name": profile_name,
        "profile_slug": profile.get("slug", ""),
        "duration_in_seconds": data.get("duration_in_seconds", 0),
        "content_start_date": data.get("content_start_date", ""),
        "logo_url": logo_url,
        "stream_urls": stream_urls,
    }


# ─── 4. CONFERENCE ─────────────────────────────────────

def get_conference(asset_id):
    url = f"{API_BASE}/api/web-player/personal/assets/{asset_id}/conference"
    r = SESSION.get(url, headers=_headers_player())
    if r.status_code != 200:
        return []
    data = r.json()
    assets = []
    for a in data.get("assets", []):
        stream_urls = []
        for track in a.get("tracks", []):
            for src in track.get("sources", []):
                if src.get("hls"):
                    stream_urls.append({"hls": src["hls"], "type": src.get("type", "")})
        assets.append({
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "currently_live": a.get("currently_live", False),
            "stream_urls": stream_urls,
        })
    return assets


# ─── 5. M3U GENERATOR ──────────────────────────────────

def generate_m3u(results):
    lines = ["#EXTM3U"]
    for r in results:
        # Ambil M3U8 URL (prioritas LIVESTREAM)
        m3u8_url = None
        for su in r["stream_urls"]:
            if su["type"] == "LIVESTREAM":
                m3u8_url = su["hls"]
                break
        if not m3u8_url and r["stream_urls"]:
            m3u8_url = r["stream_urls"][0]["hls"]
        if not m3u8_url:
            continue

        # Waktu WIB
        time_str = to_wib(r["content_start_date"])

        # Title: nama pertandingan + waktu di akhir
        title = f"{r['name']} [{time_str}]"

        # Group title
        group = r.get("sport", "Other")

        # Logo
        logo = r.get("logo_url", "")

        lines.append(
            f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}'
        )
        lines.append(m3u8_url)
        lines.append("")
    return "\n".join(lines)


# ─── MAIN ───────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  🏅 SportEurope.tv — M3U Scraper")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}")
    print("=" * 60)

    # 1. Live asset IDs
    print("\n📡 Fetching live asset IDs...")
    live_asset_ids = get_live_asset_ids()
    print(f"   {len(live_asset_ids)} live assets found")

    if not live_asset_ids:
        print("   ⚠ Tidak ada.")
        return

    # 2. Fetch details
    print(f"\n🔧 Fetching stream details ({MAX_WORKERS} workers)...")
    results = []
    failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_player_asset, aid): aid for aid in live_asset_ids}
        for i, fut in enumerate(as_completed(futures)):
            asset_id = futures[fut]
            try:
                data = fut.result()
                if data and data["stream_urls"]:
                    results.append(data)
                    print(f"   ✅ [{i+1}/{len(live_asset_ids)}] {data['name']}")
                else:
                    failed.append(asset_id)
            except Exception as e:
                failed.append(asset_id)

    print(f"\n   Total: {len(results)} | Failed: {len(failed)}")

    # 3. Generate M3U
    if results:
        m3u = generate_m3u(results)

        with open("sporteurope_live.m3u", "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"\n✅ sporteurope_live.m3u → {len(results)} streams")
        print(f"\n{m3u[:2000]}")

        # Save JSON
        with open("playlist.m3u", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n💾 sporteurope_live.json saved")
    else:
        print("\n❌ 0 streams.")


if __name__ == "__main__":
    main()   
