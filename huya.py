#!/usr/bin/env python3
"""
SportFusion IPTV Playlist Generator v3
- Parallel (async playwright + semaphore)
- tvg-logo dari team logo
- Waktu UTC → WIB (UTC+7)
- #EXTVLCOPT: user-agent, referrer, origin (dari iframe URL)
"""

import asyncio
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ─── Konfigurasi ───────────────────────────────────────────────
BASE_URL = "https://sportsfusion.im"
OUTPUT_FILE = "playlist.m3u"
TIMEOUT = 20
PLAYER_WAIT_MS = 8000
MAX_CONCURRENT = 5  # jumlah page parallel
WIB = timezone(timedelta(hours=7))  # UTC+7

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


# ─── Util ──────────────────────────────────────────────────────
def utc_to_wib(iso_str: str) -> str:
    """Konversi ISO UTC string → format WIB 'YYYY-MM-DD HH:MM'."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_wib = dt.astimezone(WIB)
        return dt_wib.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str


def get_origin(url: str) -> str:
    """Extract origin (scheme://host) dari URL."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def get_referrer(url: str) -> str:
    """Buat referrer dari URL."""
    if url.endswith("/"):
        return url
    return url + "/"


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
        log.warning("  ⚠ HTML tidak valid")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    matches = []

    for item in soup.select("a.vi-sch-item"):
        href = item.get("href", "")
        if not href:
            continue

        league = item.get("data-league", "").strip()

        time_el = item.select_one("time.vi-sch-item__time")
        time_utc = time_el.get("content", "") if time_el else ""
        time_wib = utc_to_wib(time_utc)

        team_names = [
            t.get_text(strip=True)
            for t in item.select(".vi-sch-item__team-name")
            if t.get_text(strip=True)
        ]

        title = (f"{team_names[0]} vs {team_names[1]}" if len(team_names) >= 2
                 else item.get("title", "").replace(" Stream", ""))

        # Logo tim pertama (skip default/football)
        logo = ""
        for img in item.select(".vi-sch-item__team img"):
            src = img.get("src", "")
            if src and "default.svg" not in src and "football.svg" not in src:
                logo = urljoin(BASE_URL, src)
                break
        if not logo:
            start_img = item.select_one(".vi-sch-item__start img")
            if start_img:
                src = start_img.get("src", "")
                if src and "default.svg" not in src:
                    logo = urljoin(BASE_URL, src)

        matches.append({
            "title": title,
            "league": league,
            "time_utc": time_utc,
            "time_wib": time_wib,
            "url": urljoin(BASE_URL, href),
            "href": href,
            "sport": sport,
            "logo": logo,
        })

    log.info("  → %d match", len(matches))
    return matches


# ─── Step 2: Ekstrak Stream via Playwright (Async) ─────────────
async def extract_stream_url(
    browser: Browser,
    match: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    async with semaphore:
        log.info("  [PW] %s", match["url"])
        captured_urls: list[str] = []
        iframe_url: str | None = None

        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 390, "height": 844},
            locale="en-US",
        )
        page = await context.new_page()

        def on_response(response):
            url = response.url
            if ".m3u8" in url or "/hls/" in url or "/live/" in url:
                if url not in captured_urls:
                    captured_urls.append(url)
                    log.info("    🎯 M3U8: %s", url[:100])

        page.on("response", on_response)

        try:
            await page.goto(
                match["url"],
                timeout=TIMEOUT * 1000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(PLAYER_WAIT_MS)

            # Deteksi iframe
            iframe_src = await page.evaluate("""
                () => {
                    const iframe = document.querySelector('iframe');
                    return iframe ? iframe.src : null;
                }
            """)
            if iframe_src:
                iframe_url = urljoin(match["url"], iframe_src)
                log.info("    📎 Iframe: %s", iframe_url[:100])

            # Video element
            video_src = await page.evaluate("""
                () => {
                    const v = document.querySelector('video');
                    return v ? (v.currentSrc || v.src) : null;
                }
            """)
            if video_src and video_src not in captured_urls:
                captured_urls.append(video_src)

        except Exception as e:
            log.warning("    Error: %s", e)
        finally:
            await context.close()

        stream_url = None
        if captured_urls:
            stream_url = max(captured_urls, key=len)
            log.info("    ✓ Stream: %s", stream_url[:100])

        # Referrer & origin dari iframe (bukan page)
        if iframe_url:
            referrer = get_referrer(iframe_url)
            origin = get_origin(iframe_url)
        else:
            referrer = get_referrer(match["url"])
            origin = get_origin(match["url"])

        return {
            "stream_url": stream_url,
            "iframe_url": iframe_url,
            "referrer": referrer,
            "origin": origin,
        }


# ─── Step 3: Generate M3U ───────────────────────────────────────
def generate_m3u(entries: list[dict], output: str = OUTPUT_FILE):
    lines = ["#EXTM3U"]
    lines.append(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} WIB")
    lines.append(f"# Total: {sum(1 for e in entries if e.get('stream_url'))} channels")
    lines.append("")

    for e in entries:
        if not e.get("stream_url"):
            continue

        tvg_group = e.get("league", e.get("sport", "Other"))
        tvg_id = e.get("href", "").strip("/").replace("/", "-")
        tvg_logo = e.get("logo", "")
        time_wib = e.get("time_wib", "")

        # EXTLVLCOPT: user-agent, referrer, origin
        referrer = e.get("referrer", "")
        origin = e.get("origin", "")

        # Channel name: [WIB time] Title [League]
        channel_name = f"[{time_wib}] {e['title']} [{tvg_group}]" if time_wib \
            else f"{e['title']} [{tvg_group}]"

        # #EXTINF
        logo_attr = f' tvg-logo="{tvg_logo}"' if tvg_logo else ""
        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{e["title"]}" '
            f'tvg-group="{tvg_group}"{logo_attr},{channel_name}'
        )

        # #EXTVLCOPT
        lines.append(f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}')
        if referrer:
            lines.append(f'#EXTVLCOPT:http-referrer={origin}/')
        if origin:
            lines.append(f'#EXTVLCOPT:http-origin={origin}')

        lines.append(e["stream_url"])
        lines.append("")

    Path(output).write_text("\n".join(lines), encoding="utf-8")
    count = sum(1 for e in entries if e.get("stream_url"))
    log.info("✓ %s → %d channel", output, count)


# ─── Main (Async) ───────────────────────────────────────────────
async def main():
    # 1. Session untuk schedule (blocking, jalan di thread)
    session = create_session()

    # Kumpulkan semua match dulu
    all_matches: list[dict] = []
    for sport in SPORTS:
        matches = scrape_schedule(session, sport)
        all_matches.extend(matches)
        time.sleep(0.5)

    log.info("Total match: %d → mulai ekstrak stream (parallel %d)...",
             len(all_matches), MAX_CONCURRENT)

    # 2. Playwright async + semaphore
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )

        # Buat task parallel
        async def process_match(match: dict):
            result = await extract_stream_url(browser, match, semaphore)
            match["stream_url"] = result["stream_url"]
            match["iframe_url"] = result["iframe_url"]
            match["referrer"] = result["referrer"]
            match["origin"] = result["origin"]
            return match

        tasks = [process_match(m) for m in all_matches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    # Handle exceptions
    all_entries: list[dict] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log.warning("  ✗ Error match %d: %s", i, r)
            all_entries.append(all_matches[i])
        else:
            all_entries.append(r)

    valid = [e for e in all_entries if e.get("stream_url")]
    log.info("Selesai: %d match, %d dengan stream URL", len(all_entries), len(valid))

    # 3. Generate M3U
    generate_m3u(all_entries)
    log.info("Buka %s di player IPTV (VLC, IPTV Smarters, dll).", OUTPUT_FILE)


if __name__ == "__main__":
    asyncio.run(main())   
