#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
Multi-strategy: curl_cffi (RSC) → Playwright (per-channel stream) → Selenium

Dependencies:
    pip install "curl-cffi<0.14" playwright selenium beautifulsoup4
    playwright install chromium --with-deps

Run:
    xvfb-run python vidio_m3u.py -c 200
"""

import argparse
import json
import re
import sys
import time
import logging
from pathlib import Path

# ==================== IMPORTS ====================
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# ==================== KONFIGURASI ====================
OUTPUT_FILE = "playlist.m3u"
LIVE_URL = "https://m.vidio.com/live"
API_BASE = "https://api.vidio.com"
API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)
IMPERSONATE = "chrome"
DELAY = 1.0
PAGE_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== PARSING RSC ====================
def extract_channels_from_rsc(rsc_text: str) -> list:
    """Parse RSC text untuk menemukan semua livestreaming schedule."""
    channels = {}

    for match in re.finditer(r'"content_type"\s*:\s*"livestreaming_schedule"', rsc_text):
        start = rsc_text.rfind('{', 0, match.start())
        depth = 0
        end = start
        for i in range(start, len(rsc_text)):
            if rsc_text[i] == '{':
                depth += 1
            elif rsc_text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        json_str = rsc_text[start:end]
        try:
            obj = json.loads(json_str)
            meta = obj.get("links", {}).get("self", {}).get("meta", {})
            ls_id = meta.get("livestreaming_id")
            if ls_id and ls_id not in channels:
                web_url = obj.get("web_url", "")
                mobile_url = web_url.replace("https://www.vidio.com", "https://m.vidio.com")
                channels[ls_id] = {
                    "livestreaming_id": ls_id,
                    "schedule_id": meta.get("schedule_id"),
                    "title": obj.get("title", "Unknown"),
                    "cover_url": obj.get("cover_url", ""),
                    "livestreaming_title": obj.get("livestreaming_title", "Unknown"),
                    "stream_url": obj.get("stream_url", ""),
                    "mobile_url": mobile_url,
                }
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue

    return list(channels.values())


def parse_html_for_channels(html: str) -> list:
    """Fallback: parse HTML dengan BeautifulSoup."""
    if not HAS_BS4:
        return []
    channels = {}
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "livestreaming_schedule" in text:
            for ch in extract_channels_from_rsc(text):
                channels[ch["livestreaming_id"]] = ch
    return list(channels.values())


# ==================== STRATEGY 1: curl_cffi (RSC) ====================
def fetch_rsc_curl_cffi() -> list:
    """Fetch RSC data menggunakan curl_cffi."""
    if not HAS_CURL_CFFI:
        log.warning("curl_cffi tidak tersedia")
        return []

    log.info("[Step 1] curl_cffi: Fetch RSC dari %s", LIVE_URL)
    session = cffi_requests.Session(impersonate=IMPERSONATE)

    headers = {
        "RSC": "1",
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.6",
        "Referer": REFERER,
    }
    try:
        resp = session.get(LIVE_URL, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  HTTP %d", resp.status_code)
            return []
        rsc_text = resp.text
        log.info("  RSC: %d bytes", len(rsc_text))
    except Exception as e:
        log.error("  Failed: %s", e)
        return []

    channels = extract_channels_from_rsc(rsc_text)
    if not channels:
        channels = parse_html_for_channels(rsc_text)

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STRATEGY 2: Playwright (per-channel) ====================
def fetch_streams_playwright(channels: list, max_wait: int) -> dict:
    """Navigate ke halaman individual setiap channel, intercept stream URL."""
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright tidak tersedia")
        return {}

    log.info("[Step 2] Playwright: Fetch stream URL per channel (%d channels)", len(channels))
    stream_captures = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 412, "height": 915},
            locale="id-ID",
        )

        def on_response(response):
            if "/stream?initialize=true" in response.url:
                try:
                    data = response.json()
                    hls = data.get("data", {}).get("attributes", {}).get("hls", "")
                    if hls:
                        m = re.search(r"/livestreamings/(\d+)/stream", response.url)
                        if m:
                            ls_id = int(m.group(1))
                            stream_captures[ls_id] = hls
                            log.info("    [CAPTURE] ID %s ✓", ls_id)
                except Exception:
                    pass

        context.on("response", on_response)
        page = context.new_page()

        # Establish session
        log.info("  Loading %s ...", LIVE_URL)
        page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        # Per-channel navigation
        for i, ch in enumerate(channels, 1):
            ls_id = ch["livestreaming_id"]
            page_url = ch.get("mobile_url") or f"https://m.vidio.com/live/{ls_id}"

            if ls_id in stream_captures:
                log.info("  [%d/%d] %s - skip (already captured)", i, len(channels), ch["livestreaming_title"])
                continue

            log.info("  [%d/%d] %s → %s", i, len(channels), ch["livestreaming_title"], page_url)

            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000)

                waited = 0
                while waited < PAGE_TIMEOUT and ls_id not in stream_captures:
                    time.sleep(1)
                    waited += 1

                if ls_id in stream_captures:
                    log.info("    ✓ Got non-DRM stream")
                else:
                    log.warning("    ✗ Timeout %ds", waited)

            except Exception as e:
                log.warning("    Error: %s", e)

            time.sleep(DELAY)

        browser.close()

    log.info("  Total captured: %d/%d", len(stream_captures), len(channels))
    return stream_captures


# ==================== STRATEGY 3: Selenium ====================
def fetch_streams_selenium(channels: list, max_wait: int) -> dict:
    """Fallback: Selenium Chrome headless."""
    if not HAS_SELENIUM:
        log.warning("Selenium tidak tersedia")
        return {}

    log.info("[Step 3] Selenium: Fetch stream URL per channel")
    stream_captures = {}

    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=412,915")
    options.add_argument(f"user-agent={USER_AGENT}")
    options.add_argument("--lang=id-ID")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get(LIVE_URL)
        time.sleep(3)

        for i, ch in enumerate(channels, 1):
            ls_id = ch["livestreaming_id"]
            page_url = ch.get("mobile_url") or f"https://m.vidio.com/live/{ls_id}"

            if ls_id in stream_captures:
                continue

            log.info("  [%d/%d] %s", i, len(channels), ch["livestreaming_title"])

            try:
                driver.get(page_url)
                waited = 0
                while waited < PAGE_TIMEOUT:
                    time.sleep(1)
                    waited += 1
                    # Cek performance entries untuk stream URL
                    entries = driver.execute_script("""
                        return performance.getEntriesByType('resource')
                            .filter(e => e.name.includes('/stream?initialize=true'))
                            .map(e => e.name);
                    """)
                    if entries:
                        log.info("    Stream request detected, extracting...")
                        break
            except Exception as e:
                log.warning("    Error: %s", e)

            time.sleep(DELAY)

    finally:
        driver.quit()

    return stream_captures


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    """Generate file M3U playlist dengan EXTINF + EXTVLCOPT."""
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ls_id = ch["livestreaming_id"]
        title = ch["title"]
        group = ch["livestreaming_title"]
        logo = ch["cover_url"]

        stream_url = stream_urls.get(ls_id) or ch.get("stream_url", "")

        if not stream_url:
            log.warning("  [SKIP] No stream: %s | %s", group, title)
            continue

        # EXTINF: metadata channel
        lines.append(
            f'#EXTINF:-1 tvg-id="{ls_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{group} | {title}'
        )

        # EXTVLCOPT: custom HTTP headers untuk VLC
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')

        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] Playlist: %s (%d channels)", output_file, count)


# ==================== FALLBACK: RSC via Playwright ====================
def _fetch_rsc_playwright(max_wait: int) -> list:
    """Fallback: fetch RSC via Playwright jika curl_cffi gagal."""
    if not HAS_PLAYWRIGHT:
        return []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 412, "height": 915},
            locale="id-ID",
        )
        page = context.new_page()
        page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=60000)

        rsc_text = ""
        start = time.time()
        while time.time() - start < max_wait:
            rsc_text = page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        if (s.textContent && s.textContent.includes('livestreaming_schedule'))
                            return s.textContent;
                    }
                    return '';
                }
            """)
            if "livestreaming_schedule" in rsc_text:
                break
            time.sleep(1)

        browser.close()

    return extract_channels_from_rsc(rsc_text)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-c", "--count", type=int, default=200,
                        help="Max wait seconds total (default: 200)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE,
                        help="Output file (default: playlist.m3u)")
    parser.add_argument("--strategy", type=str, default="auto",
                        choices=["auto", "cffi", "playwright", "selenium"],
                        help="Force strategy (default: auto)")
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  curl_cffi (RSC) → Playwright (stream) → Selenium")
    print("=" * 55)

    channels = []
    stream_urls = {}

    # === STEP 1: Fetch RSC (metadata) ===
    if args.strategy in ("auto", "cffi"):
        channels = fetch_rsc_curl_cffi()

    if not channels and args.strategy in ("auto", "playwright"):
        log.info("  Fallback: fetch RSC via Playwright...")
        channels = _fetch_rsc_playwright(args.count)

    if not channels:
        log.error("Gagal: tidak ada channel ditemukan.")
        sys.exit(1)

    log.info("\n  Channels (%d):", len(channels))
    for ch in channels:
        log.info("    [%s] %s | %s", ch["livestreaming_id"],
                 ch["livestreaming_title"], ch["title"])

    # === STEP 2: Fetch stream URLs ===
    if args.strategy in ("auto", "playwright"):
        stream_urls = fetch_streams_playwright(channels, args.count)

    missing = [ch for ch in channels if ch["livestreaming_id"] not in stream_urls]
    if missing and args.strategy in ("auto", "selenium"):
        log.info("  %d channels masih kosong, coba Selenium...", len(missing))
        extra = fetch_streams_selenium(missing, args.count)
        stream_urls.update(extra)

    # === STEP 3: Generate M3U ===
    log.info("\n[Step 3] Generate M3U...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
