#!/usr/bin/env python3
"""scrape.py — Generate playlist.m3u8 dari sankanime"""

import json
import time
import random
import gzip
import base64
import urllib.parse
import requests

# ==============================
# KONFIGURASI
# ==============================
API     = "https://www.sankavollerei.web.id/plananimek/api"
ID_BASE = "https://www.sankavollerei.web.id/plananimek/id"
REFERER = "https://sankanime.web.id/"
ORIGIN  = "https://sankanime.web.id"
UA      = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
PROXY   = "voifrfqj:w50vbiwbunum@23.26.71.145:5628"
ANT_KEY = "cf489396ecea4f10ad119710edae3847"
OUTPUT  = "playlist.m3u"

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
    "sec-ch-ua": '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"',
    "sec-ch-ua-mobile": "?1",
}

# ==============================
# DECODE
# ==============================
def decode(body: str) -> dict | None:
    raw = json.loads(body)
    enc = raw.get("_encsankaa")
    if not enc:
        return raw
    b64 = enc.replace('-', '+').replace('_', '/')
    pad = 4 - (len(b64) % 4)
    if pad != 4:
        b64 += '=' * pad
    dec = base64.b64decode(b64)
    try:
        out = gzip.decompress(dec)
    except Exception:
        import zlib
        out = zlib.decompress(dec, -zlib.MAX_WBITS)
    return json.loads(out)

# ==============================
# FETCH CHAIN
# ==============================
def fetch(url: str) -> dict | None:
    # 1. Direct
    d = _curl(url, proxies=None)
    if d is not None:
        return d

    # 2. Proxy
    d = _curl(url, proxies={"http": f"http://{PROXY}", "https": f"http://{PROXY}"})
    if d is not None:
        return d

    # 3. ScrapingAnt
    target = urllib.parse.quote(url, safe='')
    sa_url = f"https://api.scrapingant.com?url={target}&x-api-key={ANT_KEY}&browser=true&proxy_type=residential"
    try:
        r = requests.get(sa_url, headers={"User-Agent": UA}, timeout=90, verify=False)
        if r.status_code == 200:
            return json.loads(r.text)
    except Exception:
        pass
    return None


def _curl(url: str, proxies: dict | None) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, proxies=proxies, verify=False)
        if r.status_code != 200:
            return None
        return decode(r.text)
    except Exception:
        return None

# ==============================
# COLLECT ANIME
# ==============================
def collect_animes(home: dict) -> list:
    data = home.get("data", {}).get("data", home.get("data", {}))
    if not isinstance(data, dict):
        return []

    seen = set()
    result = []
    for sec in ["hot", "new", "today", "popular", "waiting", "fyp"]:
        items = data.get(sec, [])
        if not isinstance(items, list):
            continue
        for a in items:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            result.append(a)
    return result

# ==============================
# EXTRACT EPISODES
# ==============================
def extract_episodes(eps: dict) -> list:
    if not isinstance(eps, dict):
        return []
    data = eps.get("data", eps)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("id" in v[0] or "number" in v[0]):
                return v
    return []

# ==============================
# EXTRACT STREAM URL
# ==============================
def stream_url(d: dict | None) -> str:
    if not d:
        return ""
    s = d.get("sources") or d.get("data", {}).get("sources")
    if isinstance(s, list) and s:
        f = s[0] if isinstance(s[0], dict) else s
        u = f.get("file") or f.get("url") or f.get("src") or ""
        if u:
            return unwrap(u)
    u = d.get("file") or d.get("url") or d.get("stream") or d.get("data", {}).get("file") or d.get("data", {}).get("url") or ""
    if u:
        return unwrap(u)
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
    import urllib3
    urllib3.disable_warnings()

    m3u = "#EXTM3U\n"
    count = 0

    print("[1/3] Ambil data homepage...")
    home = fetch(f"{ID_BASE}/home")
    if home is None:
        print("❌ Gagal ambil data")
        return

    animes = collect_animes(home)
    if not animes:
        print("❌ Tidak ada anime")
        with open("debug.json", "w") as f:
            json.dump(home, f, indent=2, ensure_ascii=False)
        print("   [DEBUG] debug.json")
        return

    print(f"[2/3] Scrape {len(animes)} anime...")

    for a in animes:
        a_id   = a["id"]
        title  = a.get("title", "Unknown")
        logo   = a.get("image_poster") or a.get("image_cover") or ""
        genre  = a.get("genre", "Anime")
        if "," in genre:
            genre = genre.split(",")[0].strip()

        print(f"  {title}")
        time.sleep(random.uniform(0.5, 2))

        eps = fetch(f"{API}/episodes/{a_id}")
        if eps is None:
            continue

        ep_list = extract_episodes(eps)
        if not ep_list:
            if count == 0 and not _exists("debug_eps.json"):
                with open("debug_eps.json", "w") as f:
                    json.dump(eps, f, indent=2, ensure_ascii=False)
                print("    [DEBUG] debug_eps.json")
            continue

        print(f"    {len(ep_list)} eps")

        for ep in ep_list:
            if not isinstance(ep, dict):
                continue
            ep_id = ep.get("id")
            num   = ep.get("number") or ep.get("episode") or "?"
            if not ep_id:
                continue

            time.sleep(random.uniform(0.3, 1.5))

            st = fetch(f"{API}/stream?id={a_id}&ep={ep_id}&server=hd-2&type=sub")
            if st is None:
                continue

            if count == 0 and not _exists("debug_stream.json"):
                with open("debug_stream.json", "w") as f:
                    json.dump(st, f, indent=2, ensure_ascii=False)
                print("    [DEBUG] debug_stream.json")

            link = stream_url(st)
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


def _exists(path: str) -> bool:
    import os
    return os.path.exists(path)


if __name__ == "__main__":
    main()   
