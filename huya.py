#!/usr/bin/env python3
"""Test: match live 8431507 (Sao Paulo U20)"""
import requests, json

API = "https://apisplus.live"
H = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Origin": "https://en97.sportplus.watch",
    "Referer": "https://en97.sportplus.watch/",
}

# Match live yang kamu temukan
mid = 8431507
sport_alias = "football"
t_alias = "copa-do-brasil-u20"

# 1. Fetch view
r = requests.get(
    f"{API}/v3.1/matches/view?id={mid}"
    f"&sport_id={sport_alias}&tournament_id={t_alias}"
    f"&lang=en&lcfg=en&fw=3.1&has_regions=1",
    headers=H, timeout=15
)
item = r.json().get("item", {})
ls = item.get("ls")
cc = item.get("resolve", {}).get("cc", "?")

print(f"Match: {item.get('name')}")
print(f"Status: {item.get('status')}")
print(f"CC: {cc}")
print(f"ls: {len(ls)} URL" if ls else "ls: null")

if ls:
    for i, url in enumerate(ls):
        print(f"\n  [{i+1}] {url[:80]}...")
        
        # 2. Test m3u8
        r2 = requests.get(url, headers={
            "User-Agent": H["User-Agent"],
            "Origin": "https://en97.sportplus.watch",
            "Referer": "https://en97.sportplus.watch/",
        }, timeout=10)
        
        if r2.status_code == 200:
            lines = r2.text.strip().split("\n")
            ts_count = sum(1 for l in lines if ".ts" in l)
            print(f"      ✅ HTTP {r2.status_code} | {ts_count} segment .ts")
            print(f"      Target duration: "
                  f"{[l.split(':')[1] for l in lines if 'TARGETDURATION' in l]}")
        else:
            print(f"      ❌ HTTP {r2.status_code}")

# 3. Contoh M3U entry
if ls:
    from datetime import datetime, timezone, timedelta
    WIB = timezone(timedelta(hours=7))
    start = datetime.fromisoformat(item["start"]).astimezone(WIB)
    home = item["home"]["name"]
    away = item["away"]["name"]
    title = f"{home} vs {away}"
    name = f"{title} ({start.strftime('%H:%M')} {start.strftime('%d-%m-%Y')})"
    
    print(f"\n{'='*50}")
    print("CONTOH M3U ENTRY:")
    print(f"#EXTINF:-1,tvg-logo=\"https://cdn.ovbstdn.com/onexbet/pk{item['home']['pk_id']}.png\" "
          f"group-title=\"Football - Brazil. Copa do Brasil U20\" "
          f"title=\"{title}\" name=\"{name}\"")
    print(f"{name}")
    print(f"{ls[0]}")
    print(f"#EXTVLCOPT:http-user-agent=\"{H['User-Agent']}\"")
    from urllib.parse import urlparse
    d = urlparse(ls[0])
    print(f"#EXTVLCOPT:http-referrer=\"{d.scheme}://{d.netloc}/\"")   
