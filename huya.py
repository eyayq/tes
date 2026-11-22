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
DELAY_BETWEEN_REQUESTS = 1.5
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
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ─── Session ────────────────────────────────────────────────────
def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    log.info("Mengambil cookies dari %s ...", BASE_URL)
    resp = s.get(BASE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    log.info("Cookies: %s", list(s.cookies.keys()))
    return s


# ─── Step 1: Scrape Jadwal per Kategori ────────────────────────
def scrape_schedule(session: requests.Session, sport: str) -> list[dict]:
    url = f"{BASE_URL}/r/{sport}"
    log.info("Scrape jadwal: %s", url)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()

    # Debug: pastikan HTML valid
    if "vi-sch-item" not in resp.text:
        log.warning("  ⚠ HTML tidak mengandung 'vi-sch-item' — kemungkinan encoding issue")
        log.warning("  Content-Type: %s, Length: %d", resp.headers.get("Content-Type"), len(resp.text))
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    matches = []

    for item in soup.select("a.vi-sch-item"):
        href = item.get("href", "")
        if not href:
            continue

        league = item.get("data-league", "").strip()

        time_el = item.select_one("time.vi-sch-item__time")
        time_str = time_el.get("content", "") if time_el else ""

        team_names = [
            t.get_text(strip=True)
            for t in item.select(".vi-sch-item__team-name")
            if t.get_text(strip=True)
        ]

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


# ─── Step 2: Ekstrak URL Stream ─────────────────────────────────
def extract_stream_url(session: requests.Session, match_url: str) -> str | None:
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
        r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*',
        r'streamUrl\s*[:=]\s*["\']([^"\']+)["\']',
        r'src\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        r'"url"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'file\s*:\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        r'"src"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'setSource\(\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
        r'loadSource\(\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
    ]

    for pattern in m3u8_patterns:
        m = re.search(pattern, html)
        if m:
            stream = m.group(1).strip().replace("\\/", "/")
            log.info("    ✓ Stream: %s", stream[:80])
            return stream

    # Pola 2: iframe embed
    iframe = re.search(r'<iframe[^>]+src=["\']([^"\']+)', html)
    if iframe:
        src = iframe.group(1)
        log.info("    → Iframe: %s", src[:60])
        return _resolve_iframe(session, src)

    # Pola 3: HLS path
    m = re.search(r'(https?://[^"\'<>\s]+/hls/[^"\'<>\s]+)', html)
    if m:
        return m.group(1)

    # Pola 4: data attribute
    m = re.search(r'data-src=["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html)
    if m:
        return m.group(1)

    log.warning("    ✗ Tidak menemukan stream URL")
    # Debug: simpan HTML untuk inspeksi manual
    debug_file = f"debug_{Path(match_url).name}.html"
    Path(debug_file).write_text(html, encoding="utf-8")
    log.info("    → HTML disimpan ke %s untuk debugging", debug_file)
    return None


def _resolve_iframe(session: requests.Session, iframe_src: str) -> str | None:
    try:
        full_src = urljoin(BASE_URL, iframe_src)
        resp = session.get(full_src, timeout=TIMEOUT)
        html = resp.text
        m = re.search(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', html)
        if m:
            return m.group(0).replace("\\/", "/")
        m = re.search(r'source\s+src=["\']([^"\']+\.m3u8[^"\']*)', html)
        if m:
            return m.group(1)
    except requests.RequestException:
        pass
    return None


# ─── Step 3: Generate M3U ───────────────────────────────────────
def generate_m3u(entries: list[dict], output: str = OUTPUT_FILE):
    lines = ["#EXTM3U"]
    lines.append(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# Total channels: {sum(1 for e in entries if e.get('stream_url'))}")
    lines.append("")

    for e in entries:
        if not e.get("stream_url"):
            continue
        tvg_group = e.get("league", e.get("sport", "Other"))
        tvg_id = e.get("href", "").strip("/").replace("/", "-")

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{e["title"]}" '
            f'tvg-group="{tvg_group}",{e["title"]} [{tvg_group}]'
        )
        lines.append(e["stream_url"])
        lines.append("")

    Path(output).write_text("\n".join(lines), encoding="utf-8")
    count = sum(1 for e in entries if e.get("stream_url"))
    log.info("✓ Playlist: %s (%d channel)", output, count)


# ─── Main ───────────────────────────────────────────────────────
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

        time.sleep(DELAY_BETWEEN_REQUESTS)

    valid = [e for e in all_entries if e["stream_url"]]
    log.info("Total: %d match, %d dengan stream URL", len(all_entries), len(valid))
    generate_m3u(all_entries)
    log.info("Selesai! Buka %s di player IPTV.", OUTPUT_FILE)


if __name__ == "__main__":
    main()   
