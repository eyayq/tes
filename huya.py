#!/usr/bin/env python3
"""scrape.py — Generate playlist.m3u dari Sanka Vollerei (API Publik)"""

import json
import time
import random
import os
import requests
import urllib3

urllib3.disable_warnings()

# ==============================
# KONFIGURASI
# ==============================
BASE    = "https://www.sankavollerei.web.id/anime"
UA      = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
PROXY   = "voifrfqj:w50vbiwbunum@23.26.71.145:5628"
OUTPUT  = "playlist.m3u"
PAGE    = 3  # jumlah halaman ongoing yang di-scrape (1 halaman ≈ 20-30 anime)

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, */*",
    "Accept-Language": "id-ID,id;q=0.9",
}

# ==============================
# FETCH
# ==============================
def fetch(url: str, params: dict | None = None) -> dict | None:
    for attempt, use_proxy in enumerate([False, True]):
        try:
            proxies = None
            if use_proxy:
                proxies = {"http": f"http://{PROXY}", "https": f"http://{PROXY}"}
            r = requests.get(url, headers=HEADERS, params=params,
                             timeout=20, proxies=proxies, verify=False)
            if r.status_code != 200:
                print(f"    [DEBUG] HTTP {r.status_code} | {url[:70]}")
                continue
            data = r.json()
            if data.get("status") is False:
                print(f"    [DEBUG] API error: {data.get('message', 'unknown')}")
                continue
            return data
        except Exception as e:
            print(f"    [DEBUG] {type(e).__name__}: {e}")
            continue
    return None


# ==============================
# COLLECT ANIME (dari ongoing-anime)
# ==============================
def collect_animes(pages: int) -> list[dict]:
    animes = []
    seen = set()
    for p in range(1, pages + 1):
        print(f"  Halaman {p}/{pages}...")
        d = fetch(f"{BASE}/ongoing-anime", params={"page": p})
        if d is None:
            break
        items = d.get("data", [])
        if isinstance(items, dict):
            items = items.get("data", [])
        if not isinstance(items, list):
            break
        for a in items:
            slug = a.get("slug") or a.get("url")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            animes.append(a)
        time.sleep(random.uniform(1, 2))
    return animes


# ==============================
# GET EPISODES dari detail anime
# ==============================
def get_episodes(slug: str) -> list[dict]:
    d = fetch(f"{BASE}/anime/{slug}")
    if d is None:
        return []
    data = d.get("data", {})
    eps = data.get("episodes", [])
    if isinstance(eps, list):
        return eps
    return []


# ==============================
# GET STREAM URL
# ==============================
def get_stream(episode_slug: str) -> str:
    """Ambil server pertama dari episode, lalu ambil URL stream."""
    d = fetch(f"{BASE}/episode/{episode_slug}")
    if d is None:
        return ""
    data = d.get("data", {})
    servers = data.get("servers", [])
    if not isinstance(servers, list) or not servers:
        return ""
    # Ambil server pertama (biasanya HD)
    server = servers[0]
    server_id = server.get("id") or server.get("server_id") or server.get("url")
    if not server_id:
        return ""
    # Fetch stream
    s = fetch(f"{BASE}/server/{server_id}")
    if s is None:
        return ""
    sd = s.get("data", {})
    url = sd.get("url") or sd.get("file") or sd.get("stream") or ""
    if isinstance(url, dict):
        url = url.get("url") or url.get("file") or ""
    return url if isinstance(url, str) else ""


# ==============================
# MAIN
# ==============================
def main():
    m3u = "#EXTM3U\n"
    count = 0

    print("[1/3] Ambil daftar anime ongoing...")
    animes = collect_animes(PAGE)
    if not animes:
        print("❌ Tidak ada anime ditemukan")
        return

    print(f"[2/3] Scrape {len(animes)} anime...")

    for i, a in enumerate(animes, 1):
        slug  = a.get("slug") or a.get("url", "")
        title = a.get("title", "Unknown")
        logo  = a.get("image_poster") or a.get("image") or a.get("thumbnail") or ""
        genre = a.get("genre", "Anime")
        if isinstance(genre, list):
            genre = genre[0] if genre else "Anime"
        elif "," in str(genre):
            genre = str(genre).split(",")[0].strip()

        print(f"  [{i}/{len(animes)}] {title}")
        time.sleep(random.uniform(0.5, 1.5))

        # Ambil episode list
        eps = get_episodes(slug)
        if not eps:
            continue
        print(f"    {len(eps)} episodes")

        for ep in eps:
            ep_slug = ep.get("slug") or ep.get("url") or ""
            num     = ep.get("number") or ep.get("episode") or "?"
            if not ep_slug:
                continue

            time.sleep(random.uniform(0.3, 1.0))
            link = get_stream(ep_slug)
            if not link:
                continue

            m3u += f'#EXTINF:-1'
            if logo:
                m3u += f' tvg-logo="{logo}"'
            m3u += f' group-title="{genre}"'
            m3u += f',{title} - EP {num}\n'
            m3u += f'{link}\n'
            count += 1

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u)

    print(f"[3/3] ✅ Selesai")
    print(f"    File: {OUTPUT}")
    print(f"    Entries: {count}")
    print(f"    Size: {len(m3u):,} bytes")


if __name__ == "__main__":
    main()   
