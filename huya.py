#!/usr/bin/env python3
"""Test: cek match live + ls[]"""
import requests, time

API = "https://apisplus.live"
H = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Origin": "https://en97.sportplus.watch",
    "Referer": "https://en97.sportplus.watch/",
}

# Tournament yang sering live
tournaments = [
    (1, 118587, "football", "champions-league"),
    (1, 105759, "football", "championship"),
    (1, 1256221, "football", "england-national-league"),
    (10, 2222157, "table_tennis", "2222157"),
    (10, 2365607, "table_tennis", "2365607"),
    (10, 1691055, "table_tennis", "1691055"),
    (10, 2685648, "table_tennis", "2685648"),
    (10, 2685665, "table_tennis", "2685665"),
    (1, 13521, "football", "scottish-premier-league"),
]

total_found = 0

for sport_id, t_id, sport_alias, t_alias in tournaments:
    r = requests.get(
        f"{API}/v3.1/matches/index?fw=3.1&lang=en&lcfg=en"
        f"&offset=25200&other_matches=1"
        f"&sport_id={sport_id}&cur_tournament_id={t_id}",
        headers=H, timeout=15
    )
    if r.status_code != 200:
        print(f"[{t_id}] HTTP {r.status_code}")
        continue

    items = r.json().get("items", [])
    live = [m for m in items if m.get("status") == "live"]
    print(f"\n{'='*50}")
    print(f"[{t_id}] {len(live)} live / {len(items)} total")

    for m in live[:3]:
        mid = m["id"]
        name = m.get("name", "?")
        print(f"  {name}")

        r2 = requests.get(
            f"{API}/v3.1/matches/view?id={mid}"
            f"&sport_id={sport_alias}&tournament_id={t_alias}"
            f"&lang=en&lcfg=en&fw=3.1&has_regions=1",
            headers=H, timeout=15
        )
        if r2.status_code != 200:
            print(f"    view: HTTP {r2.status_code}")
            time.sleep(2)
            continue

        item = r2.json().get("item", {})
        ls = item.get("ls")
        cc = item.get("resolve", {}).get("cc", "?")

        if ls:
            total_found += len(ls)
            print(f"    ✅ cc={cc} | {len(ls)} stream")
            for u in ls:
                print(f"       {u[:75]}...")
        else:
            print(f"    ❌ cc={cc} | ls=null")

        time.sleep(1)

print(f"\n{'='*50}")
print(f"TOTAL: {total_found} stream URL ditemukan")   
