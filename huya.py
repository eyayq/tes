import json
import time
import random
import zlib
import base64
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi_requests

# ==============================
# KONFIGURASI
# ==============================

BASE = "https://www.sankavollerei.web.id"
ID_BASE = f"{BASE}/plananimek/id"
REFERER = "https://sankanime.web.id/"
ORIGIN = "https://sankanime.web.id"
OUTPUT = "playlist.m3u"

GENRES = [23, 33]
PAGES_PER_GENRE = 3
MAX_WORKERS = 5

PROXY_HOST = "23.26.71.145"
PROXY_PORT = "5628"
PROXY_USER = "voifrfqj"
PROXY_PASS = "w50vbiwbunum"

PROXY_URL = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
PROXY_CURL = {"http": PROXY_URL, "https": PROXY_URL}

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"

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
# DECODE
# ==============================

def decode(data):
    if not isinstance(data, dict):
        return data
    enc = data.get("_encsankaa")
    if not enc:
        return data
    b64 = enc.replace('-', '+').replace('_', '/')
    pad = 4 - (len(b64) % 4)
    if pad != 4:
        b64 += '=' * pad
    out = zlib.decompress(base64.b64decode(b64))
    return json.loads(out)


# ==============================
# FETCH
# ==============================

def fetch(url: str):
    try:
        r = cffi_requests.get(
            url,
            impersonate="chrome",
            headers=HEADERS,
            proxies=PROXY_CURL,
            timeout=20,
        )
        if r.status_code == 200:
            return decode(r.json())
        else:
            print(f"  [ERR] HTTP {r.status_code} | {url[:60]}")
    except Exception as e:
        print(f"  [ERR] {type(e).__name__}: {e}")
    return None


# ==============================
# EXTRACT LIST
# ==============================

def extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    # Cek "results"
    results = data.get("results")
    if isinstance(results, dict):
        for key in ["data", "list", "items", "results", "movie"]:
            v = results.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    elif isinstance(results, list) and results and isinstance(results[0], dict):
        return results

    # Cek "data"
    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d
    if isinstance(d, dict):
        # Level 1
        for key in ["movie", "data", "results", "list", "items", "episodes", "episode_list"]:
            v = d.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # Level 2: data.data.movie
        inner = d.get("data")
        if isinstance(inner, dict):
            for key in ["movie", "data", "results", "list", "items", "episodes"]:
                v = inner.get(key)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v

    # Fallback: cari list of dict dengan "id" di level 1-3
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
            return v
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list) and vv and isinstance(vv[0], dict) and "id" in vv[0]:
                    return vv
                if isinstance(vv, dict):
                    for vvv in vv.values():
                        if isinstance(vvv, list) and vvv and isinstance(vvv[0], dict) and "id" in vvv[0]:
                            return vvv
    return []


# ==============================
# GET STREAM URL
# ==============================

def get_stream_url(stream_data):
    if not stream_data:
        return ""
    data = stream_data.get("data", stream_data)
    if isinstance(data, dict):
        sources = data.get("sources", [])
        if isinstance(sources, list) and sources:
            raw = sources[0].get("file", "") if isinstance(sources[0], dict) else ""
            if raw:
                return unwrap(raw)
        elif isinstance(sources, dict):
            raw = sources.get("file", "")
            if raw:
                return unwrap(raw)
        for key in ["url", "file", "stream", "src", "source"]:
            v = data.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return unwrap(v)
        for v in data.values():
            if isinstance(v, str) and v.startswith("http"):
                return unwrap(v)
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, str) and vv.startswith("http"):
                        return unwrap(vv)
    return ""


def unwrap(u: str) -> str:
    if "proxy?url=" in u:
        pos = u.index("proxy?url=")
        return unquote(u[pos + 10:])
    return u


# ==============================
# WORKER: proses 1 anime
# ==============================

def process_anime(anime, index, total):
    a_id = anime.get("id")
    if not a_id:
        return []

    title = anime.get("title") or anime.get("name") or "Unknown"
    logo = anime.get("poster") or anime.get("image_poster") or anime.get("image") or ""
    genre = anime.get("genre") or anime.get("category") or "Anime"
    if isinstance(genre, list):
        genre = genre[0] if genre else "Anime"
    elif isinstance(genre, str) and "," in genre:
        genre = genre.split(",")[0].strip()

    detail = fetch(f"{ID_BASE}/detail/{a_id}?page=0")
    if not detail:
        print(f"  [{index}/{total}] {title} → gagal detail")
        return []

    eps = extract_list(detail)
    if not eps:
        print(f"  [{index}/{total}] {title} → no episodes")
        return []

    entries = []
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("id")
        num = ep.get("number") or ep.get("episode") or "?"
        if not ep_id:
            continue

        stream = fetch(f"{ID_BASE}/stream/{ep_id}")
        if not stream:
            continue

        link = get_stream_url(stream)
        if not link:
            continue

        entries.append((title, logo, genre, num, link))

    print(f"  [{index}/{total}] {title} → {len(entries)} eps ✓")
    return entries


# ==============================
# MAIN
# ==============================

def main():
    m3u_content = "#EXTM3U\n"
    count = 0
    seen = set()
    all_animes = []

    print("[1/3] Kumpulkan anime...")
    for gid in GENRES:
        for p in range(PAGES_PER_GENRE):
            d = fetch(f"{ID_BASE}/genre/{gid}?page={p}&sort=views&genre_in=")
            if not d:
                break
            items = extract_list(d)
            if not items:
                break
            for a in items:
                aid = a.get("id")
                if aid and aid not in seen:
                    seen.add(aid)
                    all_animes.append(a)
            time.sleep(0.2)

    if not all_animes:
        print("[X] Tidak ada anime.")
        return

    print(f"[+] Total: {len(all_animes)} anime, {MAX_WORKERS} threads")

    print("[2/3] Scrape (paralel)...")
    all_entries = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(process_anime, anime, i, len(all_animes)): anime
            for i, anime in enumerate(all_animes, 1)
        }
        for future in as_completed(futures):
            try:
                entries = future.result()
                all_entries.extend(entries)
            except Exception as e:
                print(f"  [ERR] {e}")

    for title, logo, genre, num, link in all_entries:
        m3u_content += f'#EXTINF:-1 tvg-logo="{logo}" group-title="{genre}",{title} - EP {num}\n'
        m3u_content += f"{link}\n"
        count += 1

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n[3/3] ✅ Selesai")
    print(f"    File: {OUTPUT}")
    print(f"    Entries: {count}")
    print(f"    Size: {len(m3u_content):,} bytes")


if __name__ == "__main__":
    main()   
