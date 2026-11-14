#!/usr/bin/env python3
"""
Scrape malaysia-tv.net → playlist.m3u (IPTV)
Versi: Playwright (handle JS-rendered m3u8)
Requirements:
  pip install requests beautifulsoup4 playwright
  playwright install chromium
"""

import re
import time
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
DELAY = 1.0
TIMEOUT = 20_000  # ms per halaman

GROUP_MAP = {
    "g-my": "Malaysian TV",
    "g-in": "International",
    "g-sp": "Sports",
}


def extract_m3u8_static(page_source: str) -> str | None:
    """Coba regex dulu (cepat) sebelum fallback ke browser."""
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
    """Ambil daftar channel dari halaman utama (static, cukup requests)."""
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


async def get_m3u8_browser(page, channel_url: str) -> str | None:
    """
    Load halaman di headless browser, intercept request .m3u8.
    Return URL m3u8 atau None.
    """
    m3u8_url = None

    def on_request(request):
        nonlocal m3u8_url
        if ".m3u8" in request.url and not m3u8_url:
            m3u8_url = request.url

    page.on("request", on_request)

    try:
        await page.goto(channel_url, wait_until="networkidle", timeout=TIMEOUT)
        # Kasih waktu ekstra untuk hls.js load
        await asyncio.sleep(2)
    except Exception:
        pass
    finally:
        page.remove_listener("request", on_request)

    if m3u8_url:
        return m3u8_url

    # Fallback: cek DOM setelah JS render
    try:
        # Cari di <video src="...">, data attributes, atau script inline
        content = await page.content()
        return extract_m3u8_static(content)
    except Exception:
        return None


async def build_playlist(channels: list[dict], output: str = "playlist.m3u"):
    lines = ["#EXTM3U"]
    ok = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for i, ch in enumerate(channels, 1):
            print(f"  [{i}/{len(channels)}] {ch['name']} ...", end=" ")

            # Step 1: coba regex cepat (tanpa browser)
            try:
                r = requests.get(ch["url"], headers=HEADERS, timeout=15)
                m3u8_url = extract_m3u8_static(r.text)
            except Exception:
                m3u8_url = None

            # Step 2: kalau gagal, pakai browser
            if not m3u8_url:
                m3u8_url = await get_m3u8_browser(page, ch["url"])

            if m3u8_url:
                logo_attr = f' tvg-logo="{ch["logo"]}"' if ch["logo"] else ""
                lines.append(
                    f'#EXTINF:-1 tvg-id="{ch["name"]}"'
                    f'{logo_attr} group-title="{ch["group"]}",{ch["name"]}'
                )
                lines.append(m3u8_url)
                ok += 1
                print(f"✓ {m3u8_url[:70]}")
            else:
                print("✗ (tidak ada m3u8)")

            await asyncio.sleep(DELAY)

        await browser.close()

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n✅ Selesai! {ok}/{len(channels)} channel berhasil.")
    print(f"   File: {output}")


if __name__ == "__main__":
    channels = scrape_channels()
    if channels:
        asyncio.run(build_playlist(channels))
    else:
        print("Tidak ada channel ditemukan.")   
