#!/usr/bin/env python3
"""scrape.py — Generate playlist.m3u dari Sanka Vollerei"""

import json
import time
import random
from curl_cffi import requests as cffi_requests

# ==============================
# KONFIGURASI
# ==============================
BASE    = "https://www.sankavollerei.web.id/anime"
PROXY   = "http://voifrfqj:w50vbiwbunum@23.26.71.145:5628"
OUTPUT  = "playlist.m3u"
PAGES   = 3

# ==============================
# FETCH (Direct → Proxy)
# ==============================
def fetch(url: str, params: dict | None = None) -> dict | None:
    # 1. Direct
    d = _get(url, params, proxies=None)
    if d is not None:
        return d
    # 2. Proxy
    d = _get(url, params, proxies={"https": PROXY, "http": PROXY})
    if d is not None:
        return d
    return None


def _get(url: str, params: dict | None, proxies: dict | None) -> dict | None:
    try:
        r = cffi_requests.get(
            url,
            params=params,
            impersonate="chrome",
            timeout=20,
            proxies=proxies,
            verify=False,
        )
        if r.status_code != 200:
            print(f"    [DEBUG] HTTP {r.status_code} | {url[:70]}")
            return None
        return r.json()
    except Exception as e:
        print(f"    [DEBUG] {type(e).__name__}: {e}")
        return None

# ==============================
# COLLECT ANIME
# ==============================
def collect_animes(pages: int) -> list[dict]:
    animes = []
    seen = set()
    for p in range(1, pages + 1):
        print(f"  Halaman {p}/{pages}...")
        d = fetch(f"{BASE}/ongoing-anime", params={"page": p})
        if d is None:
            break
        items = d.get("data", d)
        if isinstance(items, dict):
            items = items.get("data", items.get("list", items.get("results", [])))
        if not isinstance(items, list) or not items:
            if p == 1:
                with open("debug_home.json", "w") as f:
                    json.dump(d, f, indent=2, ensure_ascii=False)
                print("    [DEBUG] debug_home.json")
            break
        for a in items:
            if not isinstance(a, dict):
                continue
            slug = a.get("slug") or a.get("url") or a.get("id")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            animes.append(a)
        time.sleep(random.uniform(1, 2))
    return animes

# ==============================
# GET EPISODES
# ==============================
def get_episodes(slug: str) -> list[dict]:
    d = fetch(f"{BASE}/anime/{slug}")
    if d is None:
        return []
    data = d.get("data", d)
    if isinstance(data, dict):
        eps = data.get("episodes", data.get("episode_list", []))
        if isinstance(eps, list):
            return eps
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("id" in v[0] or "number" in v[0]):
                return v
    return []

# ==============================
# GET STREAM URL
# ==============================
def get_stream(episode_slug: str) -> str:
    d = fetch(f"{BASE}/episode/{episode_slug}")
    if d is None:
        return ""
    data = d.get("data", d)
    if not isinstance(data, dict):
        return ""
    servers = data.get("servers", data.get("server", []))
    if isinstance(servers, list) and servers:
        server = servers[0]
        server_id = server.get("id") or server.get("server_id") or server.get("url")
        if server_id:
            s = fetch(f"{BASE}/server/{server_id}")
            if s:
                sd = s.get("data", s)
                url = sd.get("url") or sd.get("file") or sd.get("stream") or ""
                if isinstance(url, str) and url.startswith("http"):
                    return url
    url = data.get("url") or data.get("file") or data.get("stream") or ""
    if isinstance(url, str) and url.startswith("http"):
        return url
    return ""

# ==============================
# MAIN
# ==============================
def main():
    m3u = "#EXTM3U\n"
    count = 0

    print("[1/3] Ambil daftar anime ongoing...")
    animes = collect_animes(PAGES)
    if not animes:
        print("❌ Tidak ada anime ditemukan")
        return

    print(f"[2/3] Scrape {len(animes)} anime...")

    for i, a in enumerate(animes, 1):
        slug  = a.get("slug") or a.get("url") or str(a.get("id", ""))
        title = a.get("title") or a.get("name") or "Unknown"
        logo  = a.get("image_poster") or a.get("image") or a.get("thumbnail") or ""
        genre = a.get("genre") or a.get("category") or "Anime"
        if isinstance(genre, list):
            genre = genre[0] if genre else "Anime"
        elif isinstance(genre, str) and "," in genre:
            genre = genre.split(",")[0].strip()

        print(f"  [{i}/{len(animes)}] {title}")
        time.sleep(random.uniform(0.5, 1.5))

        eps = get_episodes(slug)
        if not eps:
            continue
        print(f"    {len(eps)} eps")

        for ep in eps:
            if not isinstance(ep, dict):
                continue
            ep_slug = ep.get("slug") or ep.get("url") or str(ep.get("id", ""))
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
