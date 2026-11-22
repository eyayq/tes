#!/usr/bin/env python3
"""
Auto-scrape xoilacvck.tv → playlist.m3u
Playwright async + context-level network interception
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
MAX_RETRIES = 3
DEBUG = True 


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


def is_stream_url(url: str) -> bool:
    url_lower = url.lower()
    if any(ext in url_lower for ext in (".m3u8", ".flv", ".mp4", ".ts")):
        return True
    if url_lower.startswith("wss://") and any(kw in url_lower for kw in ("stream", "flv", "play", "hls")):
        return True
    return False


def pick_best_stream(stream_urls: list[str]) -> str | None:
    """Prioritas: m3u8 > flv > mp4 > ts > lainnya"""
    for priority in (".m3u8", ".flv", ".mp4", ".ts"):
        for url in stream_urls:
            if priority in url.lower():
                return url
    return stream_urls[0] if stream_urls else None


async def get_stream_from_context(context, match_url: str) -> str | None:
    """
    Context-level interception:
    1. Buka match page → ambil semua iframe src
    2. Buka setiap iframe src di page baru
    3. Context listener catch SEMUA request dari SEMUA frame
    """
    stream_urls: list[str] = []
    ws_urls: list[str] = []
    all_requests: list[str] = []  # untuk debug

    # === CONTEXT-LEVEL LISTENERS (catch semua frame) ===
    def on_context_request(request):
        url = request.url
        if DEBUG:
            all_requests.append(f"{request.method} {url[:120]}")
        if is_stream_url(url):
            if url not in stream_urls:
                stream_urls.append(url)

    def on_context_websocket(ws):
        if DEBUG:
            print(f"      [WS] {ws.url[:120]}")
        if ws.url not in ws_urls:
            ws_urls.append(ws.url)

    context.on("request", on_context_request)
    context.on("websocket", on_context_websocket)

    try:
        # === STEP 1: Buka match page, kumpulkan semua iframe src ===
        page1 = await context.new_page()
        iframe_sources: list[str] = []

        try:
            await page1.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page1.wait_for_selector("iframe", timeout=10000)
            except Exception:
                pass
            await page1.wait_for_timeout(3000)

            # Kumpulkan SEMUA iframe src (termasuk nested)
            iframes = await page1.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src") or ""
                if src:
                    full_src = urljoin(match_url, src)
                    if full_src not in iframe_sources:
                        iframe_sources.append(full_src)

            # Juga cek di HTML source
            content = await page1.content()
            for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', content):
                full_src = urljoin(match_url, m.group(1))
                if full_src not in iframe_sources:
                    iframe_sources.append(full_src)

        except Exception as e:
            if DEBUG:
                print(f"    ⚠ Step1: {e}")
        finally:
            await page1.close()

        # === STEP 2: Buka setiap iframe src di page baru ===
        for iframe_src in iframe_sources:
            # Skip non-player iframes
            if any(kw in iframe_src.lower() for kw in ("facebook", "google", "youtube", "ads", "analytics", "chat")):
                continue

            page2 = await context.new_page()
            try:
                await page2.goto(iframe_src, wait_until="domcontentloaded", timeout=20000)
                # Tunggu player load stream
                await page2.wait_for_timeout(8000)

                # Kalau sudah dapat stream, stop
                if stream_urls:
                    break

                # Coba extract dari page content
                try:
                    content = await page2.content()
                    for m in re.finditer(r'(https?://[^\s"\'<>\\]+?\.(?:m3u8|flv|mp4)[^\s"\'<>\\]*)', content):
                        if m.group(1) not in stream_urls:
                            stream_urls.append(m.group(1))
                except Exception:
                    pass

                # Cek nested iframe di player page
                nested_iframes = await page2.query_selector_all("iframe")
                for ni in nested_iframes:
                    src = await ni.get_attribute("src") or ""
                    if src:
                        full_src = urljoin(iframe_src, src)
                        if is_stream_url(full_src) and full_src not in stream_urls:
                            stream_urls.append(full_src)

            except Exception as e:
                if DEBUG:
                    print(f"    ⚠ Step2 ({iframe_src[:60]}): {e}")
            finally:
                await page2.close()

            if stream_urls:
                break

        # === STEP 3: Kalau masih kosong, tunggu lebih lama di match page ===
        if not stream_urls:
            page3 = await context.new_page()
            try:
                await page3.goto(match_url, wait_until="domcontentloaded", timeout=30000)
                await page3.wait_for_timeout(20000)  # tunggu 20 detik

                # Evaluate JS untuk cari stream URL
                try:
                    js_stream = await page3.evaluate("""
                        () => {
                            // Cari di semua frame
                            const frames = [window];
                            for (let i = 0; i < window.frames.length; i++) {
                                try { frames.push(window.frames[i]); } catch(e) {}
                            }
                            for (const f of frames) {
                                try {
                                    const html = f.document.documentElement.innerHTML;
                                    const matches = html.match(/https?:\\/\\/[^\\s"'<>]+?\\.(?:m3u8|flv|mp4)[^\\s"'<>]*/g);
                                    if (matches) return matches;
                                } catch(e) {}
                            }
                            // Cek performance entries
                            const entries = performance.getEntriesByType('resource');
                            const streams = entries
                                .filter(e => e.name.includes('.m3u8') || e.name.includes('.flv'))
                                .map(e => e.name);
                            if (streams.length) return streams;
                            return null;
                        }
                    """)
                    if js_stream:
                        for url in js_stream:
                            if url not in stream_urls:
                                stream_urls.append(url)
                except Exception:
                    pass

            except Exception as e:
                if DEBUG:
                    print(f"    ⚠ Step3: {e}")
            finally:
                await page3.close()

        # === Hasil ===
        if stream_urls:
            return pick_best_stream(stream_urls)
        if ws_urls:
            return ws_urls[0]

        if DEBUG and all_requests:
            print(f"    [DEBUG] All requests ({len(all_requests)}):")
            for r in all_requests[-30:]:
                print(f"      {r}")

    except Exception as e:
        if DEBUG:
            print(f"    ⚠ get_stream: {e}")
    finally:
        context.remove_listener("request", on_context_request)
        context.remove_listener("websocket", on_context_websocket)

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
        try:
            stream_url = await get_stream_from_context(context, match["full_url"])
        finally:
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
    global DEBUG
    live_only = "--live-only" in sys.argv
    debug_flag = "--debug" in sys.argv
    DEBUG = debug_flag
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        slug = args[0].strip("/")
        if not slug:
            print("⚠ Usage: python script.py [slug] [--live-only] [--debug]")
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
                stream_url = await get_stream_from_context(context, match_url)
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
    print(f"⚡ Concurrency: {CONCURRENCY}x")
    if DEBUG:
        print("🐛 DEBUG mode ON")
    print()

    if not matches:
        print("⚠ No matches found.")
        return

    results = asyncio.run(main_async(matches))

    entries = []
    found_count = 0
    for match in matches:
        if match["fid"] in results:
            m, stream_url = results[match["fid"]]
            is_real_stream = stream_url != m["full_url"]
            if is_real_stream:
                found_count += 1
            tag = "🔴" if m["is_live"] else "⏳"
            stream_display = stream_url[:80] if is_real_stream else "NO STREAM"
            print(f"  {tag} {m['title'][:50]} → {stream_display}")
            entries.append(build_m3u_entry(m, stream_url))

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in entries:
            f.write(entry + "\n")

    print(f"\n✅ Done! {len(entries)} entries → playlist.m3u ({found_count} real streams)")


if __name__ == "__main__":
    main()   
