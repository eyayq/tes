#!/usr/bin/env python3
"""
Scrape hd1.xemtv.net -> playlist.m3u
Fitur:
- Scrape semua kategori & channel
- Ambil URL streaming (m3u8/flv/mpd/ts/php)
- tvg-logo, group-title, tvg-name, name
- Jam & tanggal WIB di akhir name
- Sortir berdasarkan jam/tanggal
- EXVLCOPT user-agent & referrer dari domain player
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

BASE_URL = "https://hd1.xemtv.net/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
WIB = timezone(timedelta(hours=7))
STREAM_EXT = (".m3u8", ".flv", ".mpd", ".ts", ".php")


def get_soup(url, referer=None):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=30, verify=False)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def parse_categories(soup):
    """Ekstrak semua kategori dan channel dari halaman utama."""
    channels = []
    tabs = soup.select("div.tab-pane")
    for tab in tabs:
        tab_id = tab.get("id", "")
        # Nama kategori dari <strong> pertama di dalam tab
        strong = tab.find("strong")
        # Ambil kata pertama sebagai group-title
        if strong:
            raw = strong.get_text(strip=True)
            # Ambil bagian sebelum ":" atau 2 kata pertama
            group = raw.split(":")[0].split(" - ")[0].strip()
            if len(group) > 40:
                group = group[:40]
        else:
            group = f"Tab {tab_id}"

        # Peta tab_id -> nama kategori
        cat_map = {
            "1": "THỜI SỰ", "2": "ĐỊA PHƯƠNG", "3": "GIẢI TRÍ",
            "4": "THIẾU NHI", "5": "FM-RADIO", "6": "QUỐC TẾ",
            "7": "THỂ THAO",
        }
        group = cat_map.get(tab_id, group)

        for li in tab.select("li.channel"):
            rel = li.get("rel", "")
            a = li.find("a")
            img = li.find("img")
            if not a:
                continue
            href = a.get("href", "")
            title = img.get("title", "") if img else rel
            logo_src = img.get("src", "") if img else ""
            logo = urljoin(BASE_URL, logo_src) if logo_src else ""
            page_url = urljoin(BASE_URL, href)
            channels.append({
                "id": rel,
                "name": title.strip(),
                "logo": logo,
                "group": group,
                "page": page_url,
            })
    return channels


def extract_stream_url(page_html, referer):
    """
    Cari URL streaming (m3u8/flv/mpd/ts) dari HTML halaman channel.
    Cek: var source, video src, iframe src, script content.
    """
    patterns = [
        r'https?://[^\s"\'<>]+?\.(?:m3u8|flv|mpd|ts)[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+?\.php\?[^\s"\'<>]*',
    ]
    urls = []
    for pat in patterns:
        found = re.findall(pat, page_html)
        urls.extend(found)

    # Filter: buang URL yang jelas bukan stream (ads, analytics, dll)
    skip_domains = {"google", "facebook", "youtube", "cloudflare",
                    "adskeeper", "ahrefs", "googlesyndication"}
    valid = []
    for u in urls:
        domain = urlparse(u).netloc.lower()
        if any(s in domain for s in skip_domains):
            continue
        valid.append(u)

    # Dedup, prioritaskan m3u8 > mpd > flv > ts > php
    priority = {".m3u8": 0, ".mpd": 1, ".flv": 2, ".ts": 3, ".php": 4}
    valid = list(dict.fromkeys(valid))
    valid.sort(key=lambda u: min(
        (priority.get(e, 9) for e in STREAM_EXT if e in u),
        default=9
    ))
    return valid


def get_player_domain(stream_url):
    """Ambil domain dari URL stream (bukan domain web player)."""
    p = urlparse(stream_url)
    return f"{p.scheme}://{p.netloc}"


def build_m3u(channels, out_file="playlist.m3u"):
    now_wib = datetime.now(WIB)
    ts_str = now_wib.strftime("%d/%m/%Y %H:%M:%S")

    # Urutkan: THỂ THAO dulu (ada jadwal), lalu kategori lain
    cat_order = {"THỂ THAO": 0, "THỜI SỰ": 1, "ĐỊA PHƯƠNG": 2,
                 "GIẢI TRÍ": 3, "THIẾU NHI": 4, "FM-RADIO": 5,
                 "QUỐC TẾ": 6}
    channels.sort(key=lambda c: (cat_order.get(c["group"], 9), c["name"]))

    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        print(f"  Scrape: {ch['name']} ...", end=" ")
        try:
            soup = get_soup(ch["page"], referer=BASE_URL)
            raw_html = str(soup)
        except Exception as e:
            print(f"FAIL ({e})")
            continue

        streams = extract_stream_url(raw_html, ch["page"])

        if not streams:
            # Coba cek iframe / script src untuk player php
            iframes = soup.find_all("iframe")
            for ifr in iframes:
                src = ifr.get("src", "")
                if src and (".php" in src or ".html" in src):
                    player_url = urljoin(ch["page"], src)
                    try:
                        psoup = get_soup(player_url, referer=ch["page"])
                        streams = extract_stream_url(str(psoup), player_url)
                    except Exception:
                        pass
                    if streams:
                        break

        if not streams:
            print("NO STREAM")
            continue

        # Ambil stream pertama (paling prioritas)
        stream = streams[0]
        player_domain = get_player_domain(stream)
        # User-agent & referrer dari domain player
        ua = HEADERS["User-Agent"]
        referrer = f"{player_domain}/"

        # Name dengan timestamp WIB di akhir
        full_name = f"{ch['name']} [{ts_str} WIB]"

        line = (
            f'#EXTINF:-1,'
            f'tvg-id="{ch["id"]}",'
            f'tvg-name="{ch["name"]}",'
            f'tvg-logo="{ch["logo"]}",'
            f'group-title="{ch["group"]}" '
            f'{full_name}'
        )
        lines.append(line)
        lines.append(f'{stream},'
                     f'#EXTVLCOPT:http-user-agent={ua}')
        lines.append(f'#EXTVLCOPT:http-referrer={referrer}')
        count += 1
        print(f"OK -> {stream[:60]}...")
        time.sleep(0.5)  # rate limit

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n✅ Selesai! {count} channel -> {out_file}")


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings()

    print("=== Scrape hd1.xemtv.net ===")
    soup = get_soup(BASE_URL)
    channels = parse_categories(soup)
    print(f"Total channel ditemukan: {len(channels)}\n")

    build_m3u(channels)   
