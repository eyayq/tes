#!/usr/bin/env python3
"""
365W → M3U Playlist Generator (v10)
- Tanpa Playwright, tanpa _h, tanpa cookie manual
- Worker auto-extract m3u8 dari stream page (token selalu fresh)
"""

from curl_cffi import requests as cffi_requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

BASE = "https://365w.org"
WORKER = "https://exsample.d-n-s.name/play.m3u8"
UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Referer": BASE + "/",
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

SPORT_MAP = {
    "football": "soccer", "soccer": "soccer",
    "basketball": "basketball", "tennis": "tennis",
    "cricket": "cricket", "baseball": "baseball",
    "handball": "handball", "volleyball": "volleyball",
    "hockey": "hockey", "ice hockey": "hockey",
    "rugby": "rugby-union", "rugby union": "rugby-union",
    "mma": "mma", "boxing": "boxing", "golf": "golf",
    "futsal": "futsal", "table tennis": "table-tennis",
    "cycling": "cycling", "darts": "darts",
    "esports": "esports", "cybersport": "esports",
    "snooker": "snooker", "water polo": "water-polo",
    "beach volleyball": "beach-volleyball", "beach soccer": "beach-soccer",
    "field hockey": "field-hockey", "floorball": "floorball",
    "horse racing": "horse-racing", "motorsport": "motorsport",
    "formula 1": "motorsport", "winter sports": "winter-sports",
}

session = cffi_requests.Session(impersonate="chrome")


def normalize_sport_icon(sport_name, svg_icon=""):
    if svg_icon:
        return svg_icon
    return SPORT_MAP.get(sport_name.lower().strip(), "soccer")


def parse_homepage(html):
    streams = []
    seen_ids = set()
    link_pat = r"""href=["'](?:https?://(?:www\.)?365w\.org)?/stream/(\d+)/([^"']+)["']"""

    for m in re.finditer(link_pat, html):
        sid, slug = m.group(1), m.group(2)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)

        ctx_start = m.end()
        ctx_end = min(len(html), ctx_start + 600)
        ctx = html[ctx_start:ctx_end]

        sport = "Soccer"
        league = "Unknown"
        league_m = re.search(r'<!--w:t-->\s*([^<]+?)\s*·\s*([^<]+?)\s*<!--w:/t-->', ctx)
        if league_m:
            sport = league_m.group(1).strip()
            league = league_m.group(2).strip().rstrip('.')

        team1 = ""
        team2 = ""
        teams_m = re.search(r'<!--w:t-->\s*([^<]+?)\s*[—–]\s*([^<]+?)\s*<!--w:/t-->', ctx)
        if teams_m:
            team1 = teams_m.group(1).strip()
            team2 = teams_m.group(2).strip()
        else:
            parts = slug.split("_vs_")
            team1 = parts[0].replace("_", " ").title() if len(parts) > 0 else ""
            team2 = parts[1].replace("_", " ").title() if len(parts) > 1 else ""

        icon_m = re.search(r'sport_symbols\.svg#([\w-]+)', ctx)
        svg_icon = icon_m.group(1) if icon_m else ""
        sport_icon = normalize_sport_icon(sport, svg_icon)

        streams.append({
            "id": sid, "slug": slug,
            "url": f"{BASE}/stream/{sid}/{slug}",
            "team1": team1, "team2": team2,
            "sport": sport, "league": league,
            "sport_icon": sport_icon,
            "group_title": f"{sport} · {league}" if league != "Unknown" else sport,
        })
    return streams


def check_live_stream(s):
    try:
        r = session.get(s["url"], headers=HEADERS, timeout=8)
        return s, r.status_code == 200
    except Exception:
        return s, False


def main():
    print("=" * 60)
    print("  365W → M3U PLAYLIST GENERATOR (v10)")
    print(f"  Proxy: {WORKER}")
    print("  Mode:  Auto-refresh (tanpa Playwright, tanpa _h)")
    print("=" * 60)

    # 1. Fetch homepage
    home = session.get(BASE, headers=HEADERS, timeout=20)
    html = home.text
    print(f"\n[*] Homepage: {len(html):,} bytes")

    # 2. Parse
    all_streams = parse_homepage(html)
    print(f"[*] Found {len(all_streams)} streams\n")

    if not all_streams:
        print("⚠️  Tidak ada stream.")
        return

    # 3. Check live (parallel)
    print(f"[*] Checking {len(all_streams)} streams (parallel, 10 workers)...")
    live_streams = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_live_stream, s): s for s in all_streams}
        for future in as_completed(futures):
            s, is_live = future.result()
            if is_live:
                live_streams.append(s)
                print(f"  ✓ [{s['group_title']}] {s['team1']} vs {s['team2']}")
            else:
                print(f"  ✗ [{s['group_title']}] {s['team1']} vs {s['team2']}")

    print(f"\n[*] {len(live_streams)}/{len(all_streams)} LIVE\n")

    if not live_streams:
        print("⚠️  Tidak ada stream live.")
        return

    # 4. Generate M3U — URL = stream page (worker yang handle m3u8)
    m3u_path = "playlist.m3u"
    with open(m3u_path, "w") as f:
        f.write("#EXTM3U\n")
        for s in live_streams:
            title = f"{s['team1']} vs {s['team2']}"
            group = s["group_title"]
            logo = f"{BASE}/static/sport_symbols.svg#{s['sport_icon']}"
            # URL = worker + stream page path (worker auto-extract m3u8)
            url = f"{WORKER}/stream/{s['id']}/{s['slug']}"

            f.write(f'#EXTINF:-1 group-title="{group}" tvg-logo="{logo}",{title}\n')
            f.write(f"{url}\n")

    # 5. Output
    print(f"\n{'=' * 60}")
    print(f"  ✅ PLAYLIST: {m3u_path} ({len(live_streams)} entries)")
    print(f"  📡 PROXY:    {WORKER}")
    print(f"  🔁 Mode:     Auto-refresh (token selalu fresh)")
    print(f"{'=' * 60}")

    s = live_streams[0]
    title = f"{s['team1']} vs {s['team2']}"
    url = f"{WORKER}/stream/{s['id']}/{s['slug']}"
    print(f"\nPreview:")
    print(f'  #EXTINF:-1 group-title="{s["group_title"]}" tvg-logo="{BASE}/static/sport_symbols.svg#{s["sport_icon"]}",{title}')
    print(f"  {url}")
    print(f"\n✅ Tidak perlu re-run. Token di-regenerate otomatis oleh worker.")


if __name__ == "__main__":
    main()   
