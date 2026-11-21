import json
import time
import random
import zlib
import base64
from urllib.parse import quote, unquote
from curl_cffi import requests as cffi_requests
from playwright.sync_api import sync_playwright

# ==============================
# KONFIGURASI
# ==============================

BASE = "https://www.sankavollerei.web.id"
API = f"{BASE}/plananimek/api"
ID_BASE = f"{BASE}/plananimek/id"
REFERER = "https://sankanime.web.id/"
ORIGIN = "https://sankanime.web.id"
OUTPUT = "playlist.m3u8"

# Proxy Residential
PROXY_HOST = "23.26.71.145"
PROXY_PORT = "5628"
PROXY_USER = "voifrfqj"
PROXY_PASS = "w50vbiwbunum"

PROXY_URL = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
PROXY_CURL = {"http": PROXY_URL, "https": PROXY_URL}
PROXY_PW = {
    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
    "username": PROXY_USER,
    "password": PROXY_PASS,
}

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
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
# DECODE: _encsankaa → JSON
# ==============================

def decode(data):
    """Decode _encsankaa: base64url → zlib decompress → JSON"""
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
# FETCH DIRECT (curl_cffi + proxy)
# ==============================

def fetch_direct(url: str):
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
            print(f"[DIRECT] HTTP {r.status_code} | {url[:70]}")
    except Exception as e:
        print(f"[DIRECT] {type(e).__name__}: {e}")
    return None


# ==============================
# FETCH PLAYWRIGHT (bypass CF + proxy)
# ==============================

def fetch_playwright(url: str, timeout: int = 60):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                proxy=PROXY_PW,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent=UA,
                viewport={"width": 390, "height": 844},
                locale="id-ID",
                extra_http_headers={
                    "Referer": REFERER,
                    "Origin": ORIGIN,
                    "Accept": "application/json, text/plain, */*",
                },
            )
            # Anti-detect
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id'] });
                window.chrome = { runtime: {} };
            """)
            page = context.new_page()

            result = {}

            def handle_response(response):
                nonlocal result
                if url in response.url or response.url.startswith(url.split("?")[0]):
                    try:
                        result["status"] = response.status
                        result["body"] = response.text()
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)

            # Tunggu CF challenge
            for _ in range(20):
                if page.title() not in ("Just a moment...", "Tunggu sebentar..."):
                    break
                time.sleep(2)

            browser.close()

            if "body" in result:
                try:
                    return decode(json.loads(result["body"]))
                except (json.JSONDecodeError, zlib.error) as e:
                    print(f"[PLAYWRIGHT] Decode error: {e}")
                    print(f"[PLAYWRIGHT] Preview: {result['body'][:200]}")
                    return None
            return None

    except Exception as e:
        print(f"[PLAYWRIGHT] Error: {type(e).__name__}: {e}")
    return None


# ==============================
# FETCH: DIRECT → PLAYWRIGHT
# ==============================

def fetch(url: str):
    data = fetch_direct(url)
    if data:
        return data
    print("[PLAYWRIGHT] Bypassing Cloudflare...")
    return fetch_playwright(url)


# ==============================
# EXTRACT HELPERS
# ==============================

def extract_list(data, keys=None):
    """Cari list of dict di dalam response."""
    if keys is None:
        keys = ["results", "data", "items", "list", "animes", "episodes", "episode_list", "eps"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            v = data.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        # Fallback
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
                return v
    return []


def get_stream_url(stream_data):
    """Ekstrak URL stream dari response."""
    if not stream_data:
        return ""
    data = stream_data.get("data", stream_data)
    if isinstance(data, dict):
        # sources
        sources = data.get("sources", [])
        if isinstance(sources, list) and sources:
            raw = sources[0].get("file", "") if isinstance(sources[0], dict) else ""
            if raw:
                return unwrap(raw)
        elif isinstance(sources, dict):
            raw = sources.get("file", "")
            if raw:
                return unwrap(raw)
        # Direct keys
        for key in ["url", "file", "stream", "src", "source"]:
            v = data.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return unwrap(v)
            if isinstance(v, dict):
                for kk in ["url", "file", "src"]:
                    vv = v.get(kk)
                    if isinstance(vv, str) and vv.startswith("http"):
                        return unwrap(vv)
        # Deep search
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
# DELAY
# ==============================

def delay(min_s=1, max_s=3):
    time.sleep(random.uniform(min_s, max_s))


# ==============================
# MAIN
# ==============================

def main():
    m3u_content = "#EXTM3U\n"
    count = 0

    # --- Ambil daftar anime ---
    print("[1/3] Ambil daftar anime...")
    recent = fetch(f"{API}/recently-updated?page=1")
    if not recent:
        print("[X] Gagal ambil data awal.")
        return

    anime_list = extract_list(recent)
    if not anime_list:
        print("[X] List anime kosong.")
        print(f"[DEBUG] {json.dumps(recent, ensure_ascii=False)[:500]}")
        return

    print(f"[+] Total Anime: {len(anime_list)}")

    # --- Scrape tiap anime ---
    print("[2/3] Scrape...")

    for i, anime in enumerate(anime_list, 1):
        a_id = anime.get("id") if isinstance(anime, dict) else anime
        if not a_id:
            continue

        title = anime.get("title") or anime.get("name") or "Unknown"
        logo = anime.get("image_poster") or anime.get("image") or anime.get("thumbnail") or ""
        genre = anime.get("genre") or anime.get("category") or "Anime"
        if isinstance(genre, list):
            genre = genre[0] if genre else "Anime"
        elif isinstance(genre, str) and "," in genre:
            genre = genre.split(",")[0].strip()

        print(f"  [{i}/{len(anime_list)}] {title}")
        delay(0.5, 1.5)

        # Detail + Episodes
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

            delay(0.3, 1.0)

            # Stream
            stream = fetch(f"{ID_BASE}/stream/{ep_id}")
            if not stream:
                continue

            link = get_stream_url(stream)
            if not link:
                continue

            m3u_content += f'#EXTINF:-1 tvg-logo="{logo}" group-title="{genre}",{title} - EP {num}\n'
            m3u_content += f"{link}\n"
            count += 1
            print(f"    ✓ EP {num}")

    # --- Simpan ---
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n[3/3] ✅ Selesai")
    print(f"    File: {OUTPUT}")
    print(f"    Entries: {count}")
    print(f"    Size: {len(m3u_content):,} bytes")


if __name__ == "__main__":
    main()   
