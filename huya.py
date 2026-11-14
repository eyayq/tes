"""
sporteurope_iptv.py — IPTV M3U: LIVE + UPCOMING + PAST
Group-title:
  - Live:      nama sport (Badminton, Tennis, dll)
  - Upcoming:  "Upcoming"
  - Past:      "Past"
Prefix nama:
  - [LIVE] / [UPCOMING] / [PAST]
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
    if not image_id:
        return ""
    uid = image_id.replace("-", "")
    path = "/".join(uid[:8]) + "/" + uid[8:12] + "/" + uid[12:16] + "/" + uid[16:20] + "/" + uid[20:]
    return f"{IMG_CDN}/{path}/{size}.webp"


def to_wib(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(WIB)
        return f"{dt.strftime('%H:%M')} WIB {dt.strftime('%d-%m-%Y')}"
    except:
        return ""


def get_m3u8(stream_urls):
    for su in stream_urls:
        if su["type"] == "LIVESTREAM":
            return su["hls"]
    if stream_urls:
        return stream_urls[0]["hls"]
    return None


# ─── API: LIVE ──────────────────────────────────────────

def get_live_asset_ids():
    url = f"{API_BASE}/api/frontend/public/live-ids/assets?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    return r.json()


def get_player_asset(asset_id):
    """Fetch M3U8 + metadata untuk 1 asset."""
    url = f"{API_BASE}/api/web-player/personal/assets/{asset_id}"
    r = SESSION.get(url, headers=_headers_player())
    if r.status_code != 200:
        return None

    data = r.json()

    stream_urls = []
    for track in data.get("tracks", []):
        for src in track.get("sources", []):
            if src.get("hls"):
                stream_urls.append({"hls": src["hls"], "type": src.get("type", "")})

    if not stream_urls:
        return None

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


# ─── API: UPCOMING (next-livestreams per sport) ────────

def get_header_tags():
    url = f"{API_BASE}/api/web/public/header-tags?lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    data = r.json()
    return [{"id": s["id"], "name": s["name"], "slug": s["slug"]} for s in data.get("data", [])]


def get_next_livestreams(sport_id, page=1, per_page=20):
    """GET /api/web/public/tags/{sport_id}/next-livestreams"""
    url = f"{API_BASE}/api/web/public/tags/{sport_id}/next-livestreams?page={page}&per_page={per_page}&lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    if r.status_code != 200:
        return [], 1
    data = r.json()

    assets = []
    for a in data.get("data", []):
        assets.append({
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "slug": a.get("slug", ""),
            "start_date": a.get("content_start_date", ""),
            "end_date": a.get("content_end_date"),
            "currently_live": a.get("currently_live", False),
            "type": a.get("type", ""),
            "sport": a.get("display_tags", [{}])[0].get("name", "Other") if a.get("display_tags") else "Other",
            "profile_slug": a.get("profile", {}).get("slug", ""),
            "profile_name": a.get("profile", {}).get("name", ""),
            "image_id": a.get("image_id", ""),
        })

    meta = data.get("meta", {})
    return assets, meta.get("last_page", 1)


def get_all_upcoming():
    """Fetch semua upcoming dari semua sport."""
    sports = get_header_tags()
    upcoming = []
    seen_ids = set()

    for sport in sports:
        try:
            assets, last_page = get_next_livestreams(sport["id"], page=1, per_page=20)
            for a in assets:
                if a["id"] not in seen_ids and not a["currently_live"]:
                    seen_ids.add(a["id"])
                    a["sport"] = sport["name"]
                    upcoming.append(a)
        except Exception as e:
            log.debug(f"  {sport['name']}: {e}")
        time.sleep(0.2)

    return upcoming


# ─── API: PAST (dari tag page) ─────────────────────────

def get_tag_assets(sport_id, page=1, per_page=20):
    """GET /api/web/public/tags/{sport_id} — return assets (past + upcoming + live)."""
    url = f"{API_BASE}/api/web/public/tags/{sport_id}?page={page}&per_page={per_page}&lang={LANG}"
    r = SESSION.get(url, headers=_headers())
    if r.status_code != 200:
        return [], 1
    data = r.json()

    assets = []
    for item in data.get("data", []):
        if item.get("type") == "assets-game-table":
            for a in item.get("data", {}).get("assets", []):
                assets.append({
                    "id": a.get("id", ""),
                    "name": a.get("name", ""),
                    "slug": a.get("slug", ""),
                    "start_date": a.get("content_start_date", ""),
                    "end_date": a.get("content_end_date"),
                    "currently_live": a.get("currently_live", False),
                    "type": a.get("type", ""),
                    "sport": a.get("display_tags", [{}])[0].get("name", "Other") if a.get("display_tags") else "Other",
                    "profile_slug": a.get("profile", {}).get("slug", ""),
                    "profile_name": a.get("profile", {}).get("name", ""),
                    "image_id": a.get("image_id", ""),
                })

    meta = data.get("meta", {})
    return assets, meta.get("last_page", 1)


def get_all_past():
    """Fetch past assets (was_live=True, currently_live=False, end_date sudah lewat)."""
    sports = get_header_tags()
    now = datetime.now(timezone.utc)
    past = []
    seen_ids = set()

    for sport in sports:
        try:
            assets, last_page = get_tag_assets(sport["id"], page=1, per_page=20)
            for a in assets:
                if a["id"] in seen_ids:
                    continue
                # Past: sudah selesai (end_date ada dan sudah lewat) ATAU was_live tapi tidak live
                is_past = False
                if a.get("end_date"):
                    try:
                        end_dt = datetime.fromisoformat(a["end_date"].replace("Z", "+00:00"))
                        if end_dt < now:
                            is_past = True
                    except:
                        pass
                elif not a["currently_live"] and a.get("start_date"):
                    try:
                        start_dt = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00"))
                        if start_dt < now - timedelta(hours=1):
                            is_past = True
                    except:
                        pass

                if is_past:
                    seen_ids.add(a["id"])
                    a["sport"] = sport["name"]
                    past.append(a)
        except Exception as e:
            log.debug(f"  {sport['name']}: {e}")
        time.sleep(0.2)

    return past


# ─── M3U GENERATOR ──────────────────────────────────────

def generate_m3u(live_results, upcoming_results, past_results):
    lines = ["#EXTM3U"]

    # 1. LIVE (di atas, group = sport)
    for r in live_results:
        if not r["m3u8"]:
            continue
        time_str = to_wib(r["start_date"])
        title = f"[LIVE] {r['name']} [{time_str}]" if time_str else f"[LIVE] {r['name']}"
        group = r["sport"]
        logo = r["logo"]
        lines.append(f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}')
        lines.append(r["m3u8"])

    # 2. UPCOMING (group = "Upcoming")
    for r in upcoming_results:
        time_str = to_wib(r["start_date"])
        title = f"[UPCOMING] {r['name']} [{time_str}]" if time_str else f"[UPCOMING] {r['name']}"
        group = "Upcoming"
        logo = image_id_to_url(r.get("image_id", ""), "320x180")
        lines.append(f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}')
        lines.append(f"{WEB_BASE}/videos/{r['profile_slug']}/{r['slug']}")

    # 3. PAST (group = "Past")
    for r in past_results:
        time_str = to_wib(r["start_date"])
        title = f"[PAST] {r['name']} [{time_str}]" if time_str else f"[PAST] {r['name']}"
        group = "Past"
        logo = image_id_to_url(r.get("image_id", ""), "320x180")
        lines.append(f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{group}",{title}')
        lines.append(f"{WEB_BASE}/videos/{r['profile_slug']}/{r['slug']}")

    return "\n".join(lines) + "\n"


# ─── MAIN ───────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  📺 SportEurope IPTV — LIVE + UPCOMING + PAST")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}")
    print("=" * 60)

    # 1. LIVE
    print("\n🟢 Fetching LIVE streams...")
    live_ids = get_live_asset_ids()
    print(f"   {len(live_ids)} live assets")

    live_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_player_asset, aid): aid for aid in live_ids}
        for fut in as_completed(futures):
            try:
                data = fut.result()
                if data and data["m3u8"]:
                    live_results.append(data)
            except Exception as e:
                log.debug(f"   Error: {e}")

    print(f"   ✅ {len(live_results)} live with M3U8")

    # 2. UPCOMING
    print("\n📅 Fetching UPCOMING...")
    upcoming = get_all_upcoming()
    print(f"   ✅ {len(upcoming)} upcoming")

    # 3. PAST
    print("\n📁 Fetching PAST...")
    past = get_all_past()
    print(f"   ✅ {len(past)} past")

    # 4. Generate M3U
    m3u = generate_m3u(live_results, upcoming, past)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u)

    # Stats
    live_sports = {}
    for r in live_results:
        live_sports[r["sport"]] = live_sports.get(r["sport"], 0) + 1

    print(f"\n{'═' * 60}")
    print(f"  ✅ {OUTPUT_FILE}")
    print(f"  🟢 LIVE:     {len(live_results)}")
    print(f"  📅 UPCOMING: {len(upcoming)}")
    print(f"  📁 PAST:     {len(past)}")
    print(f"  TOTAL:       {len(live_results) + len(upcoming) + len(past)}")
    print(f"")
    print(f"  Live by sport: {json.dumps(live_sports)}")
    print(f"{'═' * 60}")

    # Preview
    lines = m3u.strip().split("\n")
    print(f"\n  PREVIEW (first 6 entries):")
    print(f"  {'─' * 56}")
    count = 0
    i = 1
    while i < len(lines) and count < 6:
        if lines[i].startswith("#EXTINF"):
            print(f"  {lines[i][:140]}")
            print(f"  {lines[i+1][:100]}")
            print()
            count += 1
            i += 2
        else:
            i += 1

    print(f"  💾 {os.path.abspath(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()   
