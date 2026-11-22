#!/usr/bin/env python3
"""
SportFusion IPTV Playlist Generator v2
Workflow:
  1. requests: GET / → cookies
  2. requests: GET /r/{sport} → parse jadwal
  3. playwright: GET /u/{league}/{slug} → intercept .m3u8 (JS obfuscated)
  4. Tulis playlist.m3u
"""

import re
import time
import logging
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser

# ─── Konfigurasi ───────────────────────────────────────────────
BASE_URL = "https://sportsfusion.im"
OUTPUT_FILE = "playlist.m3u"
DELAY_BETWEEN_REQUESTS = 1.0
TIMEOUT = 20
PLAYER_WAIT_MS = 8000  # tunggu player load (ms)

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


# ─── Session (requests) ────────────────────────────────────────
def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    log.info("Mengambil cookies dari %s ...", BASE_URL)
    resp = s.get(BASE_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    log.info("Cookies: %s", list(s.cookies.keys()))
    return s


# ─── Step 1: Scrape Jadwal ─────────────────────────────────────
def scrape_schedule(session: requests.Session, sport: str) -> list[dict]:
    url = f"{BASE_URL}/r/{sport}"
    log.info("Scrape jadwal: %s", url)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()

    if "vi-sch-item" not in resp.text:
        log.warning("  ⚠ HTML tidak valid (encoding?)")
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

        title = (f"{team_names[0]} vs {team_names[1]}" if len(team_names) >= 2
                 else item.get("title", "").replace(" Stream", ""))

        matches.append({
            "title": title,
            "league": league,
            "time": time_str,
            "url": urljoin(BASE_URL, href),
            "href": href,
            "sport": sport,
        })

    log.info("  → %d match", len(matches))
    return matches


# ─── Step 2: Ekstrak Stream via Playwright ─────────────────────
def create_browser(pw) -> Browser:
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
    )
    return browser


def extract_stream_url(browser: Browser, match_url: str) -> str | None:
    """
    Buka halaman player, intercept semua request .m3u8 / hls.
    JS obfuscated akan decode dan load stream → kita tangkap di network.
    """
    log.info("  [Playwright] %s", match_url)
    captured_urls: list[str] = []

    context = browser.new_context(
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 390, "height": 844},
        locale="en-US",
    )
    page = context.new_page()

    # Intercept semua response
    def on_response(response):
        url = response.url
        if ".m3u8" in url or "/hls/" in url or "/live/" in url:
            if url not in captured_urls:
                captured_urls.append(url)
                log.info("    🎯 Intercept: %s", url[:100])

    page.on("response", on_response)

    try:
        page.goto(match_url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
        # Tunggu player load & JS decode selesai
        page.wait_for_timeout(PLAYER_WAIT_MS)

        # Coba juga: cek kalau ada video element yang sudah punya src
        video_src = page.evaluate("""
            () => {
                const v = document.querySelector('video');
                return v ? (v.currentSrc || v.src) : null;
            }
        """)
        if video_src and video_src not in captured_urls:
            captured_urls.append(video_src)
            log.info("    🎯 Video src: %s", video_src[:100])

    except Exception as e:
        log.warning("    Error: %s", e)
    finally:
        context.close()

    if captured_urls:
        # Pilih URL terbaik (paling panjang / paling spesifik)
        best = max(captured_urls, key=len)
        log.info("    ✓ Stream: %s", best[:100])
        return best

    log.warning("    ✗ Tidak ada stream ter-capture")
    return None


# ─── Step 3: Generate M3U ───────────────────────────────────────
def generate_m3u(entries: list[dict], output: str = OUTPUT_FILE):
    lines = ["#EXTM3U"]
    lines.append(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# Total: {sum(1 for e in entries if e.get('stream_url'))} channels")
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
    log.info("✓ %s → %d channel", output, count)


# ─── Main ───────────────────────────────────────────────────────
def main():
    # 1. Session untuk schedule
    session = create_session()

    # 2. Browser untuk player
    with sync_playwright() as pw:
        browser = create_browser(pw)
        all_entries: list[dict] = []

        for sport in SPORTS:
            matches = scrape_schedule(session, sport)
            for match in matches:
                stream = extract_stream_url(browser, match["url"])
                match["stream_url"] = stream
                all_entries.append(match)
                time.sleep(DELAY_BETWEEN_REQUESTS)

            time.sleep(DELAY_BETWEEN_REQUESTS)

        browser.close()

    valid = [e for e in all_entries if e["stream_url"]]
    log.info("Total: %d match, %d dengan stream", len(all_entries), len(valid))
    generate_m3u(all_entries)
    log.info("Selesai! Buka %s di player IPTV.", OUTPUT_FILE)


if __name__ == "__main__":
    main()   
