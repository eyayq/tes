#!/usr/bin/env python3
"""
Scrape malaysia-tv.net → playlist.m3u (IPTV)
Versi: Playwright + concurrent pages
Requirements:
  pip install requests beautifulsoup4 playwright
  playwright install chromium --with-deps
"""

import re
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BASE_URL = "https://malaysia-tv.net/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}
CONCURRENCY = 7  # jumlah page concurrent (naikkan kalau RAM cukup)
TIMEOUT = 20_000

GROUP_MAP = {
    "g-my": "Malaysian TV",
    "g-in": "International",
    "g-sp": "Sports",
}


def extract_m3u8_static(page_source: str) -> str | None:
    patterns = [
        r'["\']([^"\']*?\.m3u8[^"\']*)["\']',
        r'(?:src|data-src|data-source|file)\s*[:=]\s*["\']([^"\']*?\.m3u8[^"\']*)["\']',
        r'loadSource\s*\(\s*["\']([^"\']*?\.m3u8[^"\']*)["\']',
        r'(https?://[^"\'\s\\]*?\.m3u8[^"\'\s\\]*)',
    ]
    for pat in patterns:
        m = re.search(pat, page_source, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            return url
    return None


def scrape_channels() -> list[dict]:
    print("→ Mengambil daftar channel dari halaman utama...")
    r = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    channels = []
    seen = set()

    for grid_class, group_title in GROUP_MAP.items():
        grid = soup.select_one(f".grid.{grid_class}")
        if not grid:
            continue
        for a in grid.select("a.tl"):
            href = a.get("href", "")
            img = a.find("img")
            if not href or not img:
                continue
            name = img.get("alt", "").strip()
            logo = img.get("src", "") or img.get("data-src", "")
            if not name or href in seen:
                continue
            seen.add(href)
            channels.append({
                "name": name,
                "url": urljoin(BASE_URL, href),
                "logo": urljoin(BASE_URL, logo) if logo else "",
                "group": group_title,
            })

    print(f"  ✓ Ditemukan {len(channels)} channel")
    return channels


async def fetch_channel(page, ch: dict, semaphore: asyncio.Semaphore) -> dict | None:
    """Fetch satu channel (concurrent)."""
    async with semaphore:
        # Step 1: regex cepat
        try:
            r = requests.get(ch["url"], headers=HEADERS, timeout=15)
            m3u8_url = extract_m3u8_static(r.text)
        except Exception:
            m3u8_url = None

        # Step 2: browser kalau regex gagal
        if not m3u8_url:
            m3u8_url = await get_m3u8_browser(page, ch["url"])

        if m3u8_url:
            return {**ch, "m3u8": m3u8_url}
        return None


async def get_m3u8_browser(page, channel_url: str) -> str | None:
    m3u8_url = None

    def on_request(request):
        nonlocal m3u8_url
        if ".m3u8" in request.url and not m3u8_url:
            m3u8_url = request.url

    page.on("request", on_request)
    try:
        await page.goto(channel_url, wait_until="networkidle", timeout=TIMEOUT)
        await asyncio.sleep(2)
    except Exception:
        pass
    finally:
        page.remove_listener("request", on_request)

    if m3u8_url:
        return m3u8_url

    try:
        content = await page.content()
        return extract_m3u8_static(content)
    except Exception:
        return None


async def build_playlist(channels: list[dict], output: str = "playlist.m3u"):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results: list[dict | None] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        pages = [await browser.new_page() for _ in range(CONCURRENCY)]

        tasks = []
        for i, ch in enumerate(channels):
            page = pages[i % CONCURRENCY]
            tasks.append(fetch_channel(page, ch, semaphore))

        done = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            done += 1
            if result:
                results.append(result)
                print(f"  [{done}/{len(channels)}] ✓ {result['name']}")
            else:
                print(f"  [{done}/{len(channels)}] ✗")

        await browser.close()

    # Tulis playlist
    lines = ["#EXTM3U"]
    for ch in results:
        logo_attr = f' tvg-logo="{ch["logo"]}"' if ch["logo"] else ""
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["name"]}"'
            f'{logo_attr} group-title="{ch["group"]}",{ch["name"]}'
        )
        # ← Tambahkan ini:
        lines.append(f'#EXTVLCOPT:http-origin=https://malaysia-tv.net')
        lines.append(f'#EXTVLCOPT:http-referrer={ch["url"]}')
        lines.append(f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}')
        lines.append(ch["m3u8"])

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n✅ Selesai! {len(results)}/{len(channels)} channel berhasil.")
    print(f"   File: {output}")   
