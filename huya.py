"""
sporteurope.py — Scraper SportEurope.tv API
Endpoint yang di-replicate:
  1. /api/web/public/header-tags
  2. /api/statistics/time
  3. /api/web/public/tags/{sport_id}/profiles
  4. /api/web/public/assets/{profile_slug}/{asset_slug}
"""

import json
import time
import logging
from datetime import datetime, timezone
from curl_cffi import requests as cffi_requests

# ─── CONFIG ─────────────────────────────────────────────
API_BASE = "https://api.sporteurope.tv"
WEB_BASE = "https://sporteurope.tv"
LANG = "en"
PER_PAGE = 10

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


# ─── 1. HEADER TAGS (Sport Types) ──────────────────────

def get_header_tags():
    """GET /api/web/public/header-tags?lang=en"""
    url = f"{API_BASE}/api/web/public/header-tags?lang={LANG}"
    log.info(f"GET {url}")
    r = SESSION.get(url, headers=_headers())
    data = r.json()
    sports = []
    for s in data.get("data", []):
        sports.append({
            "id": s["id"],
            "name": s["name"],
            "slug": s["slug"],
            "image_id": s.get("image_id", ""),
        })
    log.info(f"  → {len(sports)} sport types")
    return sports


# ─── 2. SERVER TIME ────────────────────────────────────

def get_server_time():
    """GET /api/statistics/time?lang=en"""
    url = f"{API_BASE}/api/statistics/time?lang={LANG}"
    log.info(f"GET {url}")
    r = SESSION.get(url, headers=_headers())
    data = r.json()
    ts = data.get("t", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    log.info(f"  → {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return dt


# ─── 3. PROFILES PER SPORT ─────────────────────────────

def get_profiles(sport_id, page=1, per_page=PER_PAGE):
    """GET /api/web/public/tags/{sport_id}/profiles?page=N&per_page=N&lang=en"""
    url = f"{API_BASE}/api/web/public/tags/{sport_id}/profiles?page={page}&per_page={per_page}&lang={LANG}"
    log.info(f"GET {url}")
    r = SESSION.get(url, headers=_headers())
    data = r.json()

    profiles = []
    for p in data.get("data", []):
        profiles.append({
            "id": p["id"],
            "name": p["name"],
            "slug": p["slug"],
            "type": p["type"],
            "currently_live": p.get("currently_live", False),
            "sport_type_id": p.get("sport_type_id", ""),
        })

    meta = data.get("meta", {})
    log.info(f"  → page {meta.get('current_page')}/{meta.get('last_page')} ({len(profiles)} items)")
    return profiles, meta


def get_all_profiles(sport_id):
    """Fetch semua halaman profiles."""
    all_profiles = []
    page = 1
    while True:
        profiles, meta = get_profiles(sport_id, page=page)
        all_profiles.extend(profiles)
        if page >= meta.get("last_page", 1):
            break
        page += 1
        time.sleep(0.2)
    return all_profiles


# ─── 4. ASSET DETAIL ───────────────────────────────────

def get_asset(profile_slug, asset_slug):
    """GET /api/web/public/assets/{profile_slug}/{asset_slug}?lang=en"""
    url = f"{API_BASE}/api/web/public/assets/{profile_slug}/{asset_slug}?lang={LANG}"
    log.info(f"GET {url}")
    r = SESSION.get(url, headers=_headers())
    if r.status_code != 200:
        log.warning(f"  → HTTP {r.status_code}")
        return None
    data = r.json()
    log.info(f"  → {data.get('name', '?')} | live={data.get('currently_live')} | type={data.get('type')}")
    return data


# ─── MAIN ───────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  🏅 SportEurope.tv API Scraper")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Server time
    get_server_time()

    # 2. Sport types
    print("\n── 1. Header Tags ──")
    sports = get_header_tags()
    for s in sports:
        print(f"   {s['name']} ({s['slug']})")

    # 3. Profiles — contoh: Badminton (id dari response)
    print("\n── 2. Profiles (Badminton, page 1) ──")
    badminton_id = "936ecdb2-0ddd-4c80-b115-358e8e07739f"
    profiles, meta = get_profiles(badminton_id, page=1, per_page=10)
    for p in profiles:
        live_mark = "🟢" if p["currently_live"] else "⚪"
        print(f"   {live_mark} {p['name']} [{p['type']}]")

    # 4. Asset detail — contoh dari request kamu
    print("\n── 3. Asset Detail ──")
    asset = get_asset("deutscherbadmintonverband", "4-dbv-rangliste-o19-mellensee-2026-0")
    if asset:
        print(f"   Name:     {asset.get('name')}")
        print(f"   Live:     {asset.get('currently_live')}")
        print(f"   Type:     {asset.get('type')}")
        print(f"   Duration: {asset.get('duration_in_seconds', 0) // 60} min")
        print(f"   Profile:  {asset.get('profile', {}).get('name')}")
        print(f"   Sport:    {asset.get('profile', {}).get('sport_type', {}).get('name')}")
        print(f"   Start:    {asset.get('content_start_date')}")
        print(f"   URL:      {WEB_BASE}/videos/{asset.get('profile', {}).get('slug')}/{asset.get('slug')}")

    # ─── BONUS: Scan semua sport untuk live profiles ───
    print("\n── 4. Scan Live Profiles (semua sport) ──")
    live_count = 0
    for sport in sports:
        try:
            all_profiles = get_all_profiles(sport["id"])
            live = [p for p in all_profiles if p["currently_live"]]
            if live:
                print(f"   🟢 {sport['name']}: {len(live)} live")
                for p in live:
                    print(f"      - {p['name']} [{p['type']}] slug={p['slug']}")
                live_count += len(live)
        except Exception as e:
            print(f"   ❌ {sport['name']}: {e}")
        time.sleep(0.3)

    print(f"\n   Total live profiles: {live_count}")


if __name__ == "__main__":
    main()   
