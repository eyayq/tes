import json
import time
import random
import re
from urllib.parse import quote, unquote
from curl_cffi import requests as cffi_requests
from playwright.sync_api import sync_playwright

# ==============================
# KONFIGURASI
# ==============================

BASE = "https://www.sankavollerei.web.id"
API = f"{BASE}/plananimek/api"
REFERER = "https://sankanime.web.id/"
ORIGIN = "https://sankanime.web.id"

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


# ==============================
# PLAYWRIGHT (bypass CF challenge + proxy)
# ==============================

def fetch_playwright(url: str, timeout: int = 60) -> dict:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                proxy=PROXY_PW,  # ← PROXY RESIDENTIAL
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="id-ID",
                extra_http_headers={
                    "Referer": REFERER,
                    "Origin": ORIGIN,
                    "Accept": "application/json, text/plain, */*",
                },
            )
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
                    return json.loads(result["body"])
                except json.JSONDecodeError:
                    print(f"[PLAYWRIGHT] Bukan JSON. Preview: {result['body'][:200]}")
                    return None
            return None

    except Exception as e:
        print(f"[PLAYWRIGHT] Error: {type(e).__name__}: {e}")
    return None


# ==============================
# DIRECT (curl_cffi + proxy)
# ==============================

def fetch_direct(url: str):
    try:
        r = cffi_requests.get(
            url,
            impersonate="chrome",
            headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
                "Accept": "application/json, text/plain, */*",
            },
            proxies=PROXY_CURL,  # ← PROXY RESIDENTIAL
            timeout=20,
        )
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "json" in ct:
            return r.json()
        else:
            print(f"[DIRECT] HTTP {r.status_code} | {url[:70]}")
    except Exception as e:
        print(f"[DIRECT] {type(e).__name__}: {e}")
    return None


# ==============================
# FETCH: DIRECT → PLAYWRIGHT
# ==============================

def fetch(url: str):
    # 1. Direct (cepat)
    data = fetch_direct(url)
    if data:
        return data

    # 2. Playwright (bypass CF)
    print("[PLAYWRIGHT] Bypassing Cloudflare...")
    return fetch_playwright(url)


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

    recent = fetch(f"{API}/recently-updated?page=1")
    if not recent:
        print("[X] Gagal ambil data awal.")
        return

    if isinstance(recent.get("data"), dict):
        anime_list = recent.get("data", {}).get("results", [])
    else:
        anime_list = recent.get("data", [])

    if not anime_list:
        print("[X] List anime kosong.")
        return

    print(f"[+] Total Anime: {len(anime_list)}")

    for anime in anime_list:
        a_id = anime.get("id") if isinstance(anime, dict) else anime
        if not a_id:
            continue

        print(f"\n>>> {a_id}")
        delay()

        info = fetch(f"{API}/info?id={a_id}")
        delay()
        eps_data = fetch(f"{API}/episodes/{a_id}")

        if not info or not eps_data:
            print(f"[-] Gagal: {a_id}")
            continue

        title = info.get("title", "Unknown")
        logo = info.get("image", "")
        ep_list = eps_data.get("data", eps_data) if isinstance(eps_data, dict) else eps_data

        if not isinstance(ep_list, list):
            continue

        print(f"[i] {title} — {len(ep_list)} eps")

        for ep in ep_list:
            ep_id = ep.get("id")
            num = ep.get("number", "?")
            if not ep_id:
                continue

            stream_url = f"{API}/stream?id={a_id}&ep={ep_id}&server=hd-2&type=sub"
            delay(0.5, 2)

            stream = fetch(stream_url)
            if not stream:
                continue

            sources = stream.get("sources", [])
            raw_link = ""
            if isinstance(sources, list) and sources:
                raw_link = sources[0].get("file", "")
            elif isinstance(sources, dict):
                raw_link = sources.get("file", "")

            if not raw_link:
                continue

            final_link = unquote(raw_link.split("proxy?url=")[-1]) if "proxy?url=" in raw_link else raw_link

            m3u_content += f'#EXTINF:-1 tvg-logo="{logo}" group-title="{title}",{title} - Ep {num}\n'
            m3u_content += f"{final_link}\n"
            print(f"    ✓ Ep {num}")

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n[V] Selesai → playlist.m3u8 ({m3u_content.count('#EXTINF')} entries)")


if __name__ == "__main__":
    main()   
