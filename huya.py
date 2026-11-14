#!/usr/bin/env python3
"""
Scrape malaysia-tv.net → playlist.m3u (IPTV)
Requirements: pip install requests beautifulsoup4
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://malaysia-tv.net/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}
DELAY = 1.0

# Pemetaan grid class → group-title
GROUP_MAP = {
    "g-my": "Malaysian TV",
    "g-in": "International",
    "g-sp": "Sports",
}


def get_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  [!] Gagal fetch {url}: {e}")
        return None


def extract_m3u8(page_source: str) -> str | None:
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
    soup = get_soup(BASE_URL)
    if not soup:
        return []

    channels = []
    seen = set()

    # Iterasi tiap grid (g-my, g-in, g-sp)
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


def build_playlist(channels: list[dict], output: str = "playlist.m3u"):
    lines = ["#EXTM3U"]
    ok = 0

    for i, ch in enumerate(channels, 1):
        print(f"  [{i}/{len(channels)}] {ch['name']} ...", end=" ")
        try:
            r = requests.get(ch["url"], headers=HEADERS, timeout=15)
            m3u8_url = extract_m3u8(r.text)
        except Exception:
            m3u8_url = None

        if m3u8_url:
            logo_attr = f' tvg-logo="{ch["logo"]}"' if ch["logo"] else ""
            lines.append(
                f'#EXTINF:-1 tvg-id="{ch["name"]}"'
                f'{logo_attr} group-title="{ch["group"]}",{ch["name"]}'
            )
            lines.append(m3u8_url)
            ok += 1
            print(f"✓")
        else:
            print("✗ (tidak ada m3u8)")

        time.sleep(DELAY)

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n✅ Selesai! {ok}/{len(channels)} channel berhasil.")
    print(f"   File: {output}")


if __name__ == "__main__":
    channels = scrape_channels()
    if channels:
        build_playlist(channels)
    else:
        print("Tidak ada channel ditemukan. Cek struktur HTML situs.")   
