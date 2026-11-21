#!/usr/bin/env python3
"""scrape.py — Generate playlist.m3u dari Sanka Vollerei (Playwright + Proxy)"""

import json
import time
import random
import asyncio
import zlib
import base64
import urllib.parse
from playwright.async_api import async_playwright

# ==============================
# KONFIGURASI
# ==============================
ID_BASE    = "https://www.sankavollerei.web.id/plananimek/id"
ORIGIN     = "https://sankanime.web.id"
OUTPUT     = "playlist.m3u"
PAGES      = 3
GENRE_ID   = 23

PROXY = {
    "server":   "http://23.26.71.145:5628",
    "username": "voifrfqj",
    "password": "w50vbiwbunum",
}

# ==============================
# DECODE
# ==============================
def decode(data: dict) -> dict:
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
# WAIT FOR CLOUDFLARE
# ==============================
async def wait_for_cf(page, timeout=30):
    """Tunggu sampai Cloudflare challenge selesai."""
    start = time.time()
    while time.time() - start < timeout:
        title = await page.title()
        if "Tunggu" not in title and "Checking" not in title and "Verifying" not in title:
            print(f"    ✅ CF passed! Title: {title}")
            return True
        await asyncio.sleep(2)
    print(f"    ⚠️ CF timeout. Title: {title}")
    return False

# ==============================
# FETCH VIA context.request (bukan page.evaluate)
# ==============================
async def api_fetch(context, url: str) -> dict | None:
    """Pakai Playwright APIRequestContext → share cookie dengan browser."""
    try:
        resp = await context.request.get(
            url,
            headers={
                "Accept": "*/*",
                "Accept-Language": "id-ID,id;q=0.9",
                "Origin": ORIGIN,
                "Referer": f"{ORIGIN}/",
            },
            timeout=20000,
        )
        if resp.status != 200:
            print(f"    [DEBUG] HTTP {resp.status} | {url[:70]}")
            return None
        return await resp.json()
    except Exception as e:
        print(f"    [DEBUG] {type(e).__name__}: {e}")
        return None

# ==============================
# COLLECT ANIME
# ==============================
async def collect_animes(context, genre_id: int, pages: int) -> list[dict]:
    animes, seen = [], set()
    for p in range(pages):
        print(f"  Halaman {p+1}/{pages}...")
        raw = await api_fetch(context, f"{ID_BASE}/genre/{genre_id}?page={p}&sort=views&genre_in=")
        if raw is None:
            break
        d = decode(raw)
        items = d.get("data", d)
        if isinstance(items, dict):
            items = items.get("data", items.get("list", items.get("results", [])))
        if not isinstance(items, list) or not items:
            if p == 0:
                with open("debug_genre.json", "w") as f:
                    json.dump(d, f, indent=2, ensure_ascii=False)
                print("    [DEBUG] debug_genre.json")
            break
        for a in items:
            if not isinstance(a, dict):
                continue
            aid = a.get("id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            animes.append(a)
        await asyncio.sleep(random.uniform(1, 2))
    return animes

# ==============================
# GET DETAIL
# ==============================
async def get_detail(context, anime_id: int) -> dict | None:
    raw = await api_fetch(context, f"{ID_BASE}/detail/{anime_id}?page=0")
    return decode(raw) if raw else None

# ==============================
# GET STREAM
# ==============================
async def get_stream(context, episode_id: int) -> str:
    raw = await api_fetch(context, f"{ID_BASE}/stream/{episode_id}")
    if not raw:
        return ""
    d = decode(raw)
    data = d.get("data", d)
    if isinstance(data, dict):
        url = (data.get("url") or data.get("file") or data.get("stream")
               or data.get("src") or data.get("source") or "")
        if isinstance(url, dict):
            url = url.get("url") or url.get("file") or url.get("src") or ""
        if isinstance(url, str) and url.startswith("http"):
            return unwrap(url)
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
        return urllib.parse.unquote(u[pos + 10:])
    return u

# ==============================
# EXTRACT EPISODES
# ==============================
def extract_episodes(detail: dict) -> list:
    data = detail.get("data", detail)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["episodes", "episode_list", "list", "data", "eps"]:
            v = data.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0]:
                return v
    return []

# ==============================
# MAIN
# ==============================
async def main():
    m3u = "#EXTM3U\n"
    count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # ← PENTING: headless=False biar CF tidak detect
            proxy=PROXY,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844},
            locale="id-ID",
        )

        # Anti-detect: hapus navigator.webdriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # Load site → tunggu CF challenge
        print("[0/3] Buka browser via proxy...")
        await page.goto(f"{ORIGIN}/", wait_until="domcontentloaded", timeout=60000)
        cf_ok = await wait_for_cf(page, timeout=30)
        if not cf_ok:
            print("❌ Cloudflare tidak pass. Coba jalankan ulang atau ganti proxy.")
            await browser.close()
            return

        # Tunggu network idle
        await page.wait_for_load_state("networkidle", timeout=15000)

        print("[1/3] Ambil daftar anime (genre 23)...")
        animes = await collect_animes(context, GENRE_ID, PAGES)
        if not animes:
            print("❌ Tidak ada anime ditemukan")
            await browser.close()
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
            await asyncio.sleep(random.uniform(0.5, 1.5))

            detail = await get_detail(context, a_id)
            if detail is None:
                continue

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

                await asyncio.sleep(random.uniform(0.3, 1.0))
                link = await get_stream(context, ep_id)
                if not link:
                    continue

                m3u += f'#EXTINF:-1'
                if logo:
                    m3u += f' tvg-logo="{logo}"'
                m3u += f' group-title="{genre}"'
                m3u += f',{title} - EP {num}\n'
                m3u += f'{link}\n'
                count += 1

        await browser.close()

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u)

    print(f"[3/3] ✅ Selesai")
    print(f"    File: {OUTPUT}")
    print(f"    Entries: {count}")
    print(f"    Size: {len(m3u):,} bytes")


if __name__ == "__main__":
    asyncio.run(main())   
