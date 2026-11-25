#!/usr/bin/env python3
"""Test dengan curl_cffi (TLS fingerprint browser) + proxy"""
from curl_cffi import requests as cr

PROXY = "http://svr00000:bhns0000@64.137.96.74:6641"

r = cr.get(
    "https://apisplus.live/v3.1/matches/view?id=8431507"
    "&sport_id=football&tournament_id=copa-do-brasil-u20"
    "&lang=en&lcfg=en&fw=3.1&has_regions=1",
    headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/157.0.0.0 Mobile Safari/537.36",
        "Origin": "https://en97.sportplus.watch",
        "Referer": "https://en97.sportplus.watch/",
    },
    impersonate="chrome",  # TLS fingerprint Chrome
    proxy=PROXY,
    timeout=15,
)

item = r.json().get("item", {})
ls = item.get("ls")
cc = item.get("resolve", {}).get("cc", "?")
print(f"cc={cc}, ls={'✅ '+str(len(ls))+' URL' if ls else '❌ null'}")

if ls:
    for u in ls:
        print(f"  {u[:70]}...")   
