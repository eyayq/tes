#!/usr/bin/env python3
"""
Auto-scrape xoilacvck.tv → playlist.m3u
Playwright async + asyncio.Semaphore
"""

import re
import sys
import time
import asyncio
import datetime
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://xoilacvck.tv"
IFRAME_REFERER = "https://xl365.domainkqt.cc/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
CONCURRENCY = 100
LIVE_STATUSES = {"2", "3", "4", "5", "6", "51", "52", "53", "54"}
MAX_RETRIES = 2


def fetch_page(url: str, retries: int = MAX_RETRIES) -> str:
    for attempt in range(retries):
        try:
            resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 3
                print(f"    ⚠ Retry {attempt+1}/{retries-1} in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise


def parse_matches(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for item in soup.select(".main-grid-match"):
        fid = item.get("data-fid", "")
        sport = item.get("data-sport", "unknown")
        sport_id = item.get("data-sport-id", "")
        league = item.get("data-league", "")
        status = item.get("data-status", "1")
        data_day = item.get("data-day", "")
        data_runtime = item.get("data-runtime", "")

        link = item.select_one("a.redirectPopup[href*='/truc-tiep/']")
        if not link:
            link = item.select_one("a[href*='/truc-tiep/']:not(.commentator)")
        if not link:
            link = item.select_one("div[href*='/truc-tiep/']")
        if not link:
            continue

        slug_path = link.get("href", "")
        full_url = urljoin(BASE_URL, slug_path)
        slug = slug_path.rstrip("/").split("/")[-1] if slug_path else ""

        league_img = item.select_one(".gmd-comp_logo")
        tvg_logo = league_img.get("src", "") if league_img else ""

        home_names = [p.get_text(strip=True) for p in item.select(".gmd-home_team .team-name-group p")]
        away_names = [p.get_text(strip=True) for p in item.select(".gmd-away_team .team-name-group p")]
        home = "/".join(home_names) if home_names else "Home"
        away = "/".join(away_names) if away_names else "Away"
        title = f"{home} vs {away}"

        time_el = item.select_one(".gmd-e_minutes[data-time]")
        match_time = time_el.get("data-time", "") if time_el else ""

        date_el = item.select_one(".gmd-match-date span")
        match_date = date_el.get_text(strip=True).split(" - ")[-1] if date_el else ""

        if not match_date and data_day and len(data_day) == 8:
            match_date = f"{data_day[6:8]}/{data_day[4:6]}"
        if not match_time and data_runtime:
            dt = datetime.datetime.fromtimestamp(int(data_runtime))
            match_time = dt.strftime("%H:%M")

        is_live = status in LIVE_STATUSES

        matches.append({
            "fid": fid,
            "sport": sport,
            "sport_id": sport_id,
            "league": league,
            "status": status,
            "slug": slug,
            "full_url": full_url,
            "tvg_logo": tvg_logo,
            "title": title,
            "match_time": match_time,
            "match_date": match_date,
            "is_live": is_live,
        })

    return matches


async def get_stream_from_page(page, match_url: str) -> str | None:
    """
    Intercept network requests untuk menangkap stream URL (m3u8/flv/mp4)
    dari cross-origin iframe.
    """
    stream_urls: list[str] = []

    def on_request(request):
        url = request.url
        if any(ext in url.lower() for ext in (".m3u8", ".flv", ".mp4")):
            if url not in stream_urls:
                stream_urls.append(url)

    page.on("request", on_request)

    try:
        await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)

        try:
            await page.wait_for_selector("iframe", timeout=15000)
        except Exception:
            pass

        await page.wait_for_timeout(5000)

        if not stream_urls:
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)

        if stream_urls:
            for url in stream_urls:
                if ".m3u8" in url.lower():
                    return url
            for url in stream_urls:
                if ".flv" in url.lower():
                    return url
            for url in stream_urls:
                if ".mp4" in url.lower():
                    return url
            return stream_urls[0]

    except Exception as e:
        print(f"    ⚠ {e}")
    finally:
        page.remove_listener("request", on_request)

    return None


async def scrape_match(browser, match: dict, semaphore: asyncio.Semaphore) -> tuple:
    async with semaphore:
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="vi-VN",
            extra_http_headers={
                "Referer": IFRAME_REFERER,
                "Origin": IFRAME_REFERER.rstrip("/"),
            },
        )
        page = await context.new_page()
        try:
            stream_url = await get_stream_from_page(page, match["full_url"])
        finally:
            await page.close()
            await context.close()

        if not stream_url:
            stream_url = match["full_url"]
        return (match, stream_url)


def build_m3u_entry(match: dict, stream_url: str) -> str:
    title_parts = []
    if match["is_live"]:
        title_parts.append("[LIVE]")
    title_parts.append(match["title"])
    if match["match_time"] and match["match_date"]:
        title_parts.append(f"{match['match_time']} - {match['match_date']}")
    elif match["match_time"]:
        title_parts.append(match["match_time"])
    elif match["match_date"]:
        title_parts.append(match["match_date"])

    full_title = " ".join(title_parts).replace('"', "'")
    league = match["league"].replace('"', "'")

    lines = []
    lines.append(f'#EXTINF:-1 group-title="{league}" tvg-logo="{match["tvg_logo"]}",{full_title}')
    lines.append(f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}')
    lines.append(f'#EXTVLCOPT:http-referrer={IFRAME_REFERER}')
    lines.append(stream_url)
    return "\n".join(lines)


async def main_async(matches: list):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = {}
    done_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        tasks = [scrape_match(browser, m, semaphore) for m in matches]

        for coro in asyncio.as_completed(tasks):
            try:
                m, stream_url = await coro
                results[m["fid"]] = (m, stream_url)
            except Exception as e:
                print(f"    ✗ Error: {e}")
            done_count += 1
            if done_count % 10 == 0 or done_count == len(matches):
                print(f"  Progress: {done_count}/{len(matches)}")

        await browser.close()

    return results


def main():
    live_only = "--live-only" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        slug = args[0].strip("/")
        if not slug:
            print("⚠ Usage: python script.py [slug] [--live-only]")
            return
        match_url = f"{BASE_URL}/truc-tiep/{slug}/"
        print(f"Scraping: {match_url}")

        async def debug():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="vi-VN",
                    extra_http_headers={
                        "Referer": IFRAME_REFERER,
                        "Origin": IFRAME_REFERER.rstrip("/"),
                    },
                )
                page = await context.new_page()
                stream_url = await get_stream_from_page(page, match_url)
                await browser.close()
            return stream_url

        stream_url = asyncio.run(debug())
        print(f"  Stream: {stream_url or 'NOT FOUND'}")
        return

    # === AUTO MODE ===
    print(f"🔄 Fetching {BASE_URL} ...")
    html = fetch_page(BASE_URL)
    matches = parse_matches(html)

    seen = set()
    unique = []
    for m in matches:
        if m["slug"] and m["slug"] not in seen:
            seen.add(m["slug"])
            unique.append(m)
    matches = unique

    if live_only:
        matches = [m for m in matches if m["is_live"]]

    live_count = sum(1 for m in matches if m["is_live"])
    print(f"📋 Total: {len(matches)} matches ({live_count} live, {len(matches)-live_count} upcoming)")
    print(f"⚡ Concurrency: {CONCURRENCY}x\n")

    if not matches:
        print("⚠ No matches found.")
        return

    results = asyncio.run(main_async(matches))

    entries = []
    for match in matches:
        if match["fid"] in results:
            m, stream_url = results[match["fid"]]
            tag = "🔴" if m["is_live"] else "⏳"
            print(f"  {tag} {m['title'][:50]} → {stream_url[:60]}")
            entries.append(build_m3u_entry(m, stream_url))

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in entries:
            f.write(entry + "\n")

    print(f"\n✅ Done! {len(entries)} entries → playlist.m3u")


if __name__ == "__main__":
    main()   
