#!/usr/bin/env python3
"""Test: curl_cffi TANPA proxy + error handling"""
from curl_cffi import requests as cr

mid = 8431507
url = (f"https://pler.bhns.workers.dev/?bro=https://apisplus.live/v3.1/matches/view?id={mid}"
       f"&sport_id=football&tournament_id=copa-do-brasil-u20"
       f"&lang=en&lcfg=en&fw=3.1&has_regions=1")

r = cr.get(
    url,
    headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/157.0.0.0 Mobile Safari/537.36",
        "Origin": "https://en97.sportplus.watch",
        "Referer": "https://en97.sportplus.watch/",
        "Accept-Language": "id-ID,id;q=0.6",
    },
    impersonate="chrome",
    timeout=15,
)

print(f"HTTP: {r.status_code}")
print(f"Content-Type: {r.headers.get('content-type', '?')}")
print(f"Body length: {len(r.text)}")
print(f"Body preview: {r.text[:200]}")

try:
    data = r.json()
    if data is None:
        print("❌ r.json() = None")
    else:
        item = data.get("item", {})
        ls = item.get("ls")
        cc = item.get("resolve", {}).get("cc", "?")
        print(f"cc={cc}, ls={'✅ '+str(len(ls))+' URL' if ls else '❌ null'}")
except Exception as e:
    print(f"❌ Parse error: {e}")   
