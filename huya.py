import json
import time
import random
import zlib
import base64
from urllib.parse import unquote
from curl_cffi import requests as cffi_requests

# ==============================
# KONFIGURASI
# ==============================

BASE = "https://www.sankavollerei.web.id"
API_BASE = f"{BASE}/plananimek/api"
ID_BASE = f"{BASE}/plananimek/id"
REFERER = "https://sankanime.web.id/"
ORIGIN = "https://sankanime.web.id"
OUTPUT = "playlist.m3u"

MAX_ANIME = 30

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
# FETCH (serial + retry + Playwright fallback)
# ==============================

def fetch(url: str, retries=2):
    for attempt in range(retries):
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
            elif r.status_code in (429, 403):
                print(f"  [HTTP {r.status_code}] → Playwright fallback...")
                d = fetch_playwright(url)
                if d:
                    return d
                wait = (attempt + 1) * 15
                print(f"  [403/429] PW gagal, tunggu {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [ERR] HTTP {r.status_code} | {url[:60]}")
                return None
        except Exception as e:
            print(f"  [ERR] {type(e).__name__}: {e}")
            time.sleep(3)
    return None


def fetch_playwright(url: str):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                proxy={
                    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
                    "username": PROXY_USER,
                    "password": PROXY_PASS,
                },
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(user_agent=UA, locale="id-ID")
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['id-ID','id']});
                window.chrome = { runtime: {} };
            """)
            page = ctx.new_page()
            result = {}

            def on_resp(resp):
                if url in resp.url:
                    try:
                        result["body"] = resp.text()
                    except Exception:
                        pass

            page.on("response", on_resp)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            for _ in range(15):
                if page.title() not in ("Just a moment...", "Tunggu sebentar..."):
                    break
                time.sleep(2)
            time.sleep(2)

            browser.close()

            if "body" in result:
                return decode(json.loads(result["body"]))
    except Exception as e:
        print(f"  [PW] {type(e).__name__}: {e}")
    return None


# ==============================
# EXTRACT LIST
# ==============================

def extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    results = data.get("results")
    if isinstance(results, dict):
        for key in ["data", "list", "items", "results", "movie"]:
            v = results.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    elif isinstance(results, list) and results and isinstance(results[0], dict):
        return results

    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d
    if isinstance(d, dict):
        for key in ["movie", "data", "results", "list", "items", "episodes", "episode_list"]:
            v = d.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        inner = d.get("data")
        if isinstance(inner, dict):
            for key in ["movie", "data", "results", "list", "items", "episodes"]:
                v = inner.get(key)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v

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
# MAIN
# ==============================

def main():
    m3u_content = "#EXTM3U\n"
    count = 0
    seen = set()
    all_animes = []

    # --- Step 1: Ambil recently updated ---
    print("[1/3] Ambil recently updated...")
    d = fetch(f"{API_BASE}/recently-updated")
    if not d:
        print("[X] Gagal ambil data.")
        return

    items = extract_list(d)
    if not items:
        print("[X] List kosong.")
        print(f"[DEBUG] {json.dumps(d, ensure_ascii=False)[:500]}")
        return

    for a in items:
        aid = a.get("id")
        if aid and aid not in seen:
            seen.add(aid)
            all_animes.append(a)

    print(f"    {len(items)} anime ditemukan")

    if len(all_animes) > MAX_ANIME:
        all_animes = all_animes[:MAX_ANIME]
        print(f"[i] Dibatasi ke {MAX_ANIME} anime")

    print(f"[+] Total: {len(all_animes)} anime")

    # --- Step 2: Scrape ---
    print("[2/3] Scrape...")

    for i, anime in enumerate(all_animes, 1):
        a_id = anime.get("id")
        if not a_id:
            continue

        title = anime.get("title") or anime.get("name") or "Unknown"
        logo = anime.get("poster") or anime.get("image_poster") or anime.get("image") or ""
        genre = anime.get("genre") or anime.get("category") or "Anime"
        if isinstance(genre, list):
            genre = genre[0] if genre else "Anime"
        elif isinstance(genre, str) and "," in genre:
            genre = genre.split(",")[0].strip()

        print(f"  [{i}/{len(all_animes)}] {title}")
        time.sleep(0.8)

        detail = fetch(f"{ID_BASE}/detail/{a_id}?page=0")
        if not detail:
            continue

        eps = extract_list(detail)
        if not eps:
            continue

        print(f"    {len(eps)} eps")

        for ep in eps:
            if not isinstance(ep, dict):
                continue
            ep_id = ep.get("id")
            num = ep.get("number") or ep.get("episode") or "?"
            if not ep_id:
                continue

            time.sleep(0.8)

            stream = fetch(f"{ID_BASE}/stream/{ep_id}")
            if not stream:
                continue

            link = get_stream_url(stream)
            if not link:
                continue

            m3u_content += f'#EXTINF:-1 tvg-logo="{logo}" group-title="{genre}",{title} - EP {num}\n'
            m3u_content += f"{link}\n"
            count += 1
            print(f"    \u2713 EP {num}")

    # --- Step 3: Simpan ---
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n[3/3] \u2705 Selesai")
    print(f"    File: {OUTPUT}")
    print(f"    Entries: {count}")
    print(f"    Size: {len(m3u_content):,} bytes")


if __name__ == "__main__":
    main()   
