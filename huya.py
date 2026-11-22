#!/usr/bin/env python3
"""
Auto-scrape xoilacvck.tv → playlist.m3u
Playwright async + asyncio.Semaphore (5x/10x concurrent)
"""

import re
import sys
import asyncio
import datetime
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://xoilacvck.tv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
CONCURRENCY = 250  # ← ubah ke 5 atau 10
LIVE_STATUSES = {"2", "3", "4", "5", "6", "51", "52", "53", "54"}


def fetch_page(url: str) -> str:
    resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_matches(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for item in soup.select(".main-grid-match"):
        fid = item.get("data-fid", "")
        sport = item.get("data-sport", "unknown")
        league = item.get("data-league", "")
        status = item.get("data-status", "1")
        data_day = item.get("data-day", "")
        data_runtime = item.get("data-runtime", "")

        link = item.select_one("a[href*='/truc-tiep/']")
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
        if item.select_one(".icon-live, .live-badge, [class*='live']"):
            is_live = True

        matches.append({
            "fid": fid,
            "sport": sport,
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
    try:
        await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("iframe", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        iframes = await page.query_selector_all("iframe")
        for iframe in iframes:
            src = await iframe.get_attribute("src") or ""
            if not src:
                continue
            full_src = urljoin(match_url, src)

            if ".m3u8" in full_src:
                return full_src

            if any(kw in full_src.lower() for kw in ("stream", "player", "embed", "hls", "video", "watch")):
                try:
                    await page.goto(full_src, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)
                    content = await page.content()
                    m = re.search(r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', content)
                    if m:
                        return m.group(1)
                except Exception:
                    pass
                return full_src

        content = await page.content()
        m = re.search(r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', content)
        if m:
            return m.group(1)

        for script in await page.query_selector_all("script"):
            text = await script.inner_text()
            m = re.search(r'"(?:src|url|file|source|stream)"\s*:\s*"([^"]+\.m3u8[^"]*)"', text)
            if m:
                return m.group(1)

    except Exception as e:
        print(f"    ⚠ {e}")

    return None


async def scrape_match(browser, match: dict, semaphore: asyncio.Semaphore) -> tuple:
    async with semaphore:
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="vi-VN",
        )
        page = await context.new_page()
        stream_url = await get_stream_from_page(page, match["full_url"])
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

    lines = []
    lines.append(f'#EXTINF:-1 group-title="{match["league"]}" tvg-logo="{match["tvg_logo"]}",{full_title}')
    lines.append(f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}')
    lines.append(f'#EXTVLCOPT:http-referrer={match["full_url"]}')
    lines.append(stream_url)
    return "\n".join(lines)


async def main_async(matches: list):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = {}
    done_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        tasks = [scrape_match(browser, m, semaphore) for m in matches]

        for coro in asyncio.as_completed(tasks):
            try:
                m, stream_url = await coro
                results[m["fid"]] = (m, stream_url)
            except Exception as e:
                pass
            done_count += 1
            if done_count % 10 == 0 or done_count == len(matches):
                print(f"  Progress: {done_count}/{len(matches)}")

        await browser.close()

    return results


def main():
    if len(sys.argv) > 1:
        slug = sys.argv[1].strip("/")
        if not slug:
            print("⚠ Usage: python script.py <slug>")
            return
        match_url = f"{BASE_URL}/truc-tiep/{slug}/"
        print(f"Scraping: {match_url}")

        async def debug():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=HEADERS["User-Agent"], locale="vi-VN")
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

    live_count = sum(1 for m in matches if m["is_live"])
    print(f"📋 Total: {len(matches)} matches ({live_count} live, {len(matches)-live_count} upcoming)")
    print(f"⚡ Concurrency: {CONCURRENCY}x\n")

    if not matches:
        print("⚠ No matches found.")
        return

    results = asyncio.run(main_async(matches))

    # Write playlist.m3u (original order)
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
