#!/usr/bin/env python3
"""Scrape hd1.xemtv.net -> playlist.m3u (ThreadPool)
   Scrape semua Link 1, Link 2, ... dari halaman channel"""

import re
import requests
import urllib3
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings()

BASE_URL = "https://hd1.xemtv.net/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
WIB = timezone(timedelta(hours=7))
SKIP_DOMAINS = {"google", "facebook", "youtube", "cloudflare",
                "adskeeper", "ahrefs", "googlesyndication"}


def get_soup(url, referer=None):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=30, verify=False)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def parse_categories(soup):
    channels = []
    cat_map = {
        "1": "THỜI SỰ", "2": "ĐỊA PHƯƠNG", "3": "GIẢI TRÍ",
        "4": "THIẾU NHI", "5": "FM-RADIO", "6": "QUỐC TẾ",
        "7": "THỂ THAO",
    }
    for tab in soup.select("div.tab-pane"):
        tab_id = tab.get("id", "")
        group = cat_map.get(tab_id, f"Tab {tab_id}")
        for li in tab.select("li.channel"):
            a = li.find("a")
            img = li.find("img")
            if not a:
                continue
            title = img.get("title", "").strip() if img else li.get("rel", "")
            logo_src = img.get("src", "") if img else ""
            channels.append({
                "id": li.get("rel", ""),
                "name": title,
                "logo": urljoin(BASE_URL, logo_src) if logo_src else "",
                "group": group,
                "page": urljoin(BASE_URL, a.get("href", "")),
            })
    return channels


def extract_stream_url(html):
    """Cari URL streaming .m3u8 .mpd .flv .ts"""
    pats = [
        r'https?://[^\s"\'<>]+?\.(?:m3u8|mpd|flv|ts)[^\s"\'<>]*',
    ]
    urls = []
    for p in pats:
        urls.extend(re.findall(p, html))
    valid = []
    for u in urls:
        d = urlparse(u).netloc.lower()
        if not any(s in d for s in SKIP_DOMAINS):
            valid.append(u)
    valid = list(dict.fromkeys(valid))
    prio = {".m3u8": 0, ".mpd": 1, ".flv": 2, ".ts": 3}
    valid.sort(key=lambda u: min(
        (p for e, p in prio.items() if e in u), default=9))
    return valid


def get_player_domain(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def get_embed_pages(soup, page_url):
    """
    Ambil semua embed page:
    1. iframe utama (src="kenh/xxx.php")
    2. Semua Link 1, Link 2, ... (configuratorc)
    Return: list of (label, url)
    """
    pages = []

    # 1. Iframe utama
    iframe = soup.find("iframe", id="Myiframe")
    if not iframe:
        iframe = soup.find("iframe", class_="embed-responsive-item")
    if iframe:
        src = iframe.get("src", "")
        if src:
            pages.append(("Main", urljoin(page_url, src)))

    # 2. Link 1, Link 2, ... (configuratorc)
    for a in soup.find_all("a", onclick=re.compile(r"configuratorc")):
        name = a.get("name", "")
        text = a.get_text(strip=True)
        if name:
            pages.append((text or "Link", urljoin(page_url, name)))

    # Dedup
    seen = set()
    unique = []
    for label, url in pages:
        if url not in seen:
            seen.add(url)
            unique.append((label, url))
    return unique


def scrape_embed_page(embed_url, referer):
    """
    Scrape 1 embed page -> cari stream URL.
    Return: stream_url (m3u8/mpd/flv/ts) atau None
    """
    try:
        soup = get_soup(embed_url, referer=referer)
        raw = str(soup)
    except Exception:
        return None

    streams = extract_stream_url(raw)
    if streams:
        return streams[0]

    # Follow iframe di dalam embed page
    for ifr in soup.find_all("iframe"):
        src = ifr.get("src", "")
        if src:
            sub_url = urljoin(embed_url, src)
            try:
                ss = get_soup(sub_url, referer=embed_url)
                streams = extract_stream_url(str(ss))
                if streams:
                    return streams[0]
            except Exception:
                pass
    return None


def scrape_channel(ch):
    """Scrape 1 channel: semua embed page (Main, Link 1, Link 2, ...)"""
    try:
        soup = get_soup(ch["page"], referer=BASE_URL)
    except Exception as e:
        return ch, f"FAIL ({e})", []

    embed_pages = get_embed_pages(soup, ch["page"])
    if not embed_pages:
        return ch, "NO EMBED", []

    results = []
    for label, embed_url in embed_pages:
        stream = scrape_embed_page(embed_url, ch["page"])
        if stream:
            results.append((label, stream))
            print(f"    {label}: {stream[:60]}...")
        else:
            # Fallback: pakai URL embed PHP sendiri
            results.append((label, embed_url))
            print(f"    {label}: (embed) {embed_url[:60]}...")

    status = f"{len(results)} link"
    return ch, status, results


def build_m3u(channels, out_file="playlist.m3u", max_workers=5):
    ts = datetime.now(WIB).strftime("%d/%m/%Y %H:%M:%S")
    cat_order = {"THỂ THAO": 0, "THỜI SỰ": 1, "ĐỊA PHƯƠNG": 2,
                 "GIẢI TRÍ": 3, "THIẾU NHI": 4, "FM-RADIO": 5,
                 "QUỐC TẾ": 6}
    channels.sort(key=lambda c: (cat_order.get(c["group"], 9), c["name"]))

    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(scrape_channel, ch): ch for ch in channels}
        done = 0
        for fut in as_completed(futs):
            ch, status, streams = fut.result()
            done += 1
            print(f"[{done}/{len(channels)}] {ch['name']}: {status}")
            if streams:
                all_results.append((ch, streams))

    order = {ch["id"]: i for i, ch in enumerate(channels)}
    all_results.sort(key=lambda r: order.get(r[0]["id"], 999))

    lines = ["#EXTM3U"]
    for ch, streams in all_results:
        for label, stream in streams:
            pd = get_player_domain(stream)
            ua = HEADERS["User-Agent"]
            if label == "Main":
                full = f"{ch['name']} [{ts} WIB]"
            else:
                full = f"{ch['name']} {label} [{ts} WIB]"
            lines.append(
                f'#EXTINF:-1 '
                f'tvg-id="{ch["id"]}" '
                f'tvg-name="{ch["name"]}" '
                f'tvg-logo="{ch["logo"]}" '
                f'group-title="{ch["group"]}",'
                f'{full}')
            lines.append(f'#EXTVLCOPT:http-user-agent={ua}')
            lines.append(f'#EXTVLCOPT:http-referrer=https://hd1.xemtv.net/')
            lines.append(f'#EXTVLCOPT:http-origin=https://hd1.xemtv.net')
            lines.append(stream)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    total_streams = sum(len(s) for _, s in all_results)
    print(f"\n✅ {len(all_results)} channel, {total_streams} stream -> {out_file}")


if __name__ == "__main__":
    print("=== Scrape hd1.xemtv.net ===")
    soup = get_soup(BASE_URL)
    channels = parse_categories(soup)
    print(f"Total channel: {len(channels)}\n")
    build_m3u(channels, max_workers=15)   
