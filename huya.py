#!/usr/bin/env python3
import requests, json

API = "https://apisplus.live"
H = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Origin": "https://en97.sportplus.watch",
    "Referer": "https://en97.sportplus.watch/",
}

# Ambil 3 match pertama dari UCL
r = requests.get(
    f"{API}/v3.1/matches/index?fw=3.1&lang=en&lcfg=en"
    f"&offset=25200&other_matches=1&sport_id=1&cur_tournament_id=118587",
    headers=H, timeout=15
)
matches = r.json().get("items", [])[:5]

print(f"Dapat {len(matches)} match\n")

for m in matches:
    mid = m["id"]
    name = m.get("name", "?")
    status = m.get("status", "?")
    print(f"  {name} [{status}]")

    # Fetch view
    r2 = requests.get(
        f"{API}/v3.1/matches/view?id={mid}"
        f"&sport_id=football&tournament_id=champions-league"
        f"&lang=en&lcfg=en&fw=3.1&has_regions=1",
        headers=H, timeout=15
    )
    item = r2.json().get("item", {})
    ls = item.get("ls")
    streams = item.get("streams", [])

    if ls:
        print(f"    ✅ ls: {len(ls)} URL")
        for u in ls:
            print(f"       {u[:80]}...")
    else:
        print(f"    ❌ ls: null")
        if streams:
            print(f"       streams: {len(streams)} (bookmaker redirect)")
    print()   
