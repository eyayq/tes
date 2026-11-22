#!/usr/bin/env python3
"""
SportFusion IPTV Playlist Generator
Workflow:
  1. GET / → ambil cookies
  2. GET /r/{sport} → parse jadwal (match list)
  3. GET /u/{league}/{slug} → ekstrak URL stream (m3u8)
  4. Tulis playlist.m3u
"""

import re
import time
import logging
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ─── Konfigurasi ───────────────────────────────────────────────
BASE_URL = "https://sportsfusion.im"
OUTPUT_FILE = "playlist.m3u"
DELAY_BETWEEN_REQUESTS = 1.5  # detik, anti rate-limit
TIMEOUT = 15

SPORTS = [
    "football", "basketball", "american-football", "baseball",
    "ice-hockey", "boxing", "mma", "golf", "rugby",
    "motorsports", "darts", "snooker", "tennis",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ─── Session ────────────────────────────────────────────────────
def create_session() -> requests.Session:
    """Buat session dan ambil cookies awal dari halaman home."""
    s = requests.Session()
    s.headers.update(HEADERS)
    log.info("Mengambil cookies dari %s ...", BASE_URL)
    resp = s.get(BASE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    log.info("Cookies: %s", list(s.cookies.keys()))
    return s


# ─── Step 1: Scrape Jadwal per Kategori ────────────────────────
def scrape_schedule(session: requests.Session, sport: str) -> list[dict]:
    """
    Parse halaman /r/{sport} → daftar match.
    Return: [{title, league, time, url, href}, ...]
    """
    url = f"{BASE_URL}/r/{sport}"
    log.info("Scrape jadwal: %s", url)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    matches = []
    for item in soup.select("a.vi-sch-item"):
        href = item.get("href", "")
        if not href:
            continue

        # League dari data attribute
        league = item.get("data-league", "").strip()

        # Time
        time_el = item.select_one("time.vi-sch-item__time")
        time_str = time_el.get("content", "") if time_el else ""

        # Nama tim
        team_names = [
            t.get_text(strip=True)
            for t in item.select(".vi-sch-item__team-name")
            if t.get_text(strip=True)
        ]

        # Title: "Team1 vs Team2" atau dari title attribute
        if len(team_names) >= 2:
            title = f"{team_names[0]} vs {team_names[1]}"
        else:
            title = item.get("title", "").replace(" Stream", "")

        full_url = urljoin(BASE_URL, href)
        matches.append({
            "title": title,
            "league": league,
            "time": time_str,
            "url": full_url,
            "href": href,
            "sport": sport,
        })

    log.info("  → %d match ditemukan", len(matches))
    return matches


# ─── Step 2: Ekstrak URL Stream dari Halaman Match ─────────────
def extract_stream_url(session: requests.Session, match_url: str) -> str | None:
    """
    Kunjungi halaman /u/{league}/{slug} dan cari URL stream.
    Mencari pola:
      - .m3u8 dalam HTML/JS
      - source src=
      - var streamUrl / player config
    """
    log.info("  Ekstrak stream: %s", match_url)
    try:
        resp = session.get(match_url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("  Gagal fetch: %s", e)
        return None

    html = resp.text

    # Pola 1: URL .m3u8 langsung
    m3u8_patterns = [
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'streamUrl\s*[:=]\s*["\']([^"\']+)["\']',
        r'src\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        r'"url"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
    ]

    for pattern in m3u8_patterns:
        m = re.search(pattern, html)
        if m:
            stream = m.group(1).strip()
            log.info("    ✓ Stream: %s", stream[:80] + "...")
            return stream

    # Pola 2: iframe embed (player eksternal)
    iframe = re.search(r'<iframe[^>]+src=["\']([^"\']+)', html)
    if iframe:
        src = iframe.group(1)
        log.info("    → Iframe embed: %s (perlu di-resolve)", src[:60])
        # Coba resolve iframe
        return _resolve_iframe(session, src)

    # Pola 3: HLS dalam JS object
    hls_pattern = r'(https?://[^"\'<>\s]+/hls/[^"\'<>\s]+)'
    m = re.search(hls_pattern, html)
    if m:
        return m.group(1)

    log.warning("    ✗ Tidak menemukan stream URL")
    return None


def _resolve_iframe(session: requests.Session, iframe_src: str) -> str | None:
    """Coba resolve iframe ke URL stream."""
    try:
        resp = session.get(iframe_src, timeout=TIMEOUT)
        html = resp.text
        m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
        if m:
            return m.group(0)
        # Coba pola umum player
        m = re.search(r'source\s+src=["\']([^"\']+\.m3u8[^"\']*)', html)
        if m:
            return m.group(1)
    except requests.RequestException:
        pass
    return None


# ─── Step 3: Generate M3U ───────────────────────────────────────
def generate_m3u(entries: list[dict], output: str = OUTPUT_FILE):
    """Tulis file playlist.m3u."""
    lines = ["#EXTM3U"]
    lines.append(f'#EXTM3U url-tvg="http://xmltv.sportsfusion.im/guide.xml"')
    lines.append(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# Total channels: {len(entries)}")
    lines.append("")

    for e in entries:
        if not e.get("stream_url"):
            continue
        # Group by league untuk tvg-group
        tvg_group = e.get("league", e.get("sport", "Other"))
        # tvg-id: slug dari href
        tvg_id = e.get("href", "").strip("/").replace("/", "-")

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{e["title"]}" '
            f'tvg-group="{tvg_group}",{e["title"]} [{tvg_group}]'
        )
        lines.append(e["stream_url"])
        lines.append("")

    Path(output).write_text("\n".join(lines), encoding="utf-8")
    log.info("✓ Playlist tersimpan: %s (%d channel)", output,
             sum(1 for e in entries if e.get("stream_url")))


# ─── Main Workflow ──────────────────────────────────────────────
def main():
    session = create_session()
    all_entries: list[dict] = []

    for sport in SPORTS:
        matches = scrape_schedule(session, sport)
        for match in matches:
            stream = extract_stream_url(session, match["url"])
            match["stream_url"] = stream
            all_entries.append(match)
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Filter yang punya stream
    valid = [e for e in all_entries if e["stream_url"]]
    log.info("Total: %d match, %d dengan stream URL", len(all_entries), len(valid))

    generate_m3u(all_entries)
    log.info("Selesai! Buka %s di player IPTV kamu.", OUTPUT_FILE)


if __name__ == "__main__":
    main()   
