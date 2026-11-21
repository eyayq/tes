#!/usr/bin/env python3
"""scrape.py — Generate playlist.m3u dari Sanka Vollerei"""

import json
import time
import random
import zlib
import base64
import urllib.parse
import requests
import urllib3

urllib3.disable_warnings()

# ==============================
# KONFIGURASI
# ==============================
ID_BASE   = "https://www.sankavollerei.web.id/plananimek/id"
REFERER   = "https://sankanime.web.id/"
ORIGIN    = "https://sankanime.web.id"
UA        = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
PROXY     = "voifrfqj:w50vbiwbunum@23.26.71.145:5628"
OUTPUT    = "playlist.m3u"
PAGES     = 3  # jumlah halaman genre listing

HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.9",
    "Origin": ORIGIN,
    "Referer": REFERER,
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "sec-ch-ua-platform": '"Android"',
    "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
    "sec-ch-ua-mobile": "?1",
}

# ==============================
# DECODE (FIXED)
# ==============================
def decode(body: str) -> dict | None:
    """Decode _encsankaa: base64url → zlib decompress → JSON"""
    raw = json.loads(body)
    enc = raw.get("_encsankaa")
    if not enc:
        return raw
    # base64url → base64
    b64 = enc.replace('-', '+').replace('_', '/')
    pad = 4 - (len(b64) % 4)
    if pad != 4:
        b64 += '=' * pad
    dec = base64.b64decode(b64)
    # zlib decompress (data punya zlib header 0x78 0xDA)
    out = zlib.decompress(dec)
    return json.loads(out)

# ==============================
# FETCH (Direct → Proxy)
# ==============================
def fetch(url: str) -> dict | None:
    for use_proxy in [False, True]:
        try:
            proxies = None
            if use_proxy:
                proxies = {"http": f"http://{PROXY}", "https": f"http://{PROXY}"}
            r = requests.get(url, headers=HEADERS, timeout=20,
                             proxies=proxies, verify=False)
            if r.status_code != 200:
                print(f"    [DEBUG] HTTP {r.status_code} | {url[:70]}")
                continue
            return decode(r.text)
        except Exception as e:
            print(f"    [DEBUG] {type(e).__name__}: {e}")
            continue
    return None

# ==============================
# COLLECT ANIME (dari genre listing)
# ==============================
def collect_animes(genre_id: int = 23, pages: int = PAGES) -> list[dict]:
    """Ambil anime dari genre listing (genre_id=23 = Action, 33 = dll)"""
    animes = []
    seen = set()
    for p in range(pages):
        print(f"  Halaman {p+1}/{pages}...")
        d = fetch(f"{ID_BASE}/genre/{genre_id}?page={p}&sort=views&genre_in=")
        if d is None:
            break
        # Struktur bisa: {"data": [...]} atau {"data": {"data": [...]}}
        items = d.get("data", d)
        if isinstance(items, dict):
            items = items.get("data", items.get("list", []))
        if not isinstance(items, list):
            # dump untuk debug
            with open("debug_genre.json", "w") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            print(f"    [DEBUG] Struktur tak dikenal → debug_genre.json")
            break
        for a in items:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            animes.append(a)
        time.sleep(random.uniform(1, 2))
    return animes

# ==============================
# GET DETAIL (episode list)
# ==============================
def get_detail(anime_id: int) -> dict | None:
    return fetch(f"{ID_BASE}/detail/{anime_id}?page=0")

# ==============================
# GET STREAM URL
# ==============================
def get_stream(episode_id: int) -> str:
    d = fetch(f"{ID_BASE}/stream/{episode_id}")
    if not d:
        return ""
    # Cari URL di dalam response
    data = d.get("data", d)
    if isinstance(data, dict):
        url = (data.get("url") or data.get("file") or data.get("stream")
               or data.get("src") or "")
        if isinstance(url, dict):
            url = url.get("url") or url.get("file") or ""
        if isinstance(url, str) and url:
            return unwrap(url)
    # Fallback: cari di nested
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, str) and v.startswith("http"):
                return unwrap(v)
    return ""

def unwrap(u: str) -> str:
    if "proxy?url=" in u:
        pos = u.index("proxy?url=")
        return urllib.parse.unquote(u[pos + 10:])
    return u

# ==============================
# MAIN
# ==============================
def main():
    m3u = "#EXTM3U\n"
    count = 0

    print("[1/3] Ambil daftar anime...")
    animes = collect_animes(genre_id=23, pages=PAGES)
    if not animes:
        print("❌ Tidak ada anime ditemukan")
        return

    print(f"[2/3] Scrape {len(animes)} anime...")

    for i, a in enumerate(animes, 1):
        a_id   = a["id"]
        title  = a.get("title") or a.get("name") or "Unknown"
        logo   = a.get("image_poster") or a.get("image") or a.get("thumbnail") or ""
        genre  = a.get("genre") or a.get("category") or "Anime"
        if isinstance(genre, list):
            genre = genre[0] if genre else "Anime"
        elif isinstance(genre, str) and "," in genre:
            genre = genre.split(",")[0].strip()

        print(f"  [{i}/{len(animes)}] {title}")
        time.sleep(random.uniform(0.5, 1.5))

        detail = get_detail(a_id)
        if detail is None:
            continue

        # Ekstrak episode list dari detail
        eps = extract_episodes(detail)
        if not eps:
            if i == 1:
                with open("debug_detail.json", "w") as f:
                    json.dump(detail, f, indent=2, ensure_ascii=False)
                print("    [DEBUG] debug_detail.json")
            continue

        print(f"    {len(eps)} eps")

        for ep in eps:
            if not isinstance(ep, dict):
                continue
            ep_id = ep.get("id")
            num   = ep.get("number") or ep.get("episode") or "?"
            if not ep_id:
                continue

            time.sleep(random.uniform(0.3, 1.0))
            link = get_stream(ep_id)
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


def extract_episodes(detail: dict) -> list:
    """Cari list episode di dalam response detail."""
    data = detail.get("data", detail)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Coba key umum
        for key in ["episodes", "episode_list", "list", "data"]:
            v = data.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # Fallback: cari list of dict yang punya "id"
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
                return v
    return []


if __name__ == "__main__":
    main()   
