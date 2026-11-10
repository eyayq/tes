#!/usr/bin/env python3
"""
Vidio Live TV → M3U Playlist Generator
========================================
Multi-strategy: curl_cffi (TLS impersonation) → Playwright → Selenium

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
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# ==================== KONFIGURASI ====================
OUTPUT_FILE = "playlist.m3u"
LIVE_URL = "https://m.vidio.com/live"
API_BASE = "https://api.vidio.com"
API_KEY = "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
IMPERSONATE = "chrome"  # TLS fingerprint impersonation
DELAY = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== PARSING ====================
def extract_channels_from_rsc(rsc_text: str) -> list[dict]:
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
                channels[ls_id] = {
                    "livestreaming_id": ls_id,
                    "schedule_id": meta.get("schedule_id"),
                    "title": obj.get("title", "Unknown"),
                    "cover_url": obj.get("cover_url", ""),
                    "livestreaming_title": obj.get("livestreaming_title", "Unknown"),
                    "stream_url": obj.get("stream_url", ""),
                }
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue

    return list(channels.values())


def parse_html_for_channels(html: str) -> list[dict]:
    """Fallback: parse HTML dengan BeautifulSoup jika RSC gagal."""
    if not HAS_BS4:
        return []

    channels = {}
    soup = BeautifulSoup(html, "html.parser")

    # Cari semua script yang mengandung data livestreaming
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "livestreaming_schedule" in text:
            found = extract_channels_from_rsc(text)
            for ch in found:
                channels[ch["livestreaming_id"]] = ch

    return list(channels.values())


# ==================== STRATEGY 1: curl_cffi ====================
def fetch_with_curl_cffi() -> tuple[list[dict], dict[int, str]]:
    """Fetch RSC + stream URLs menggunakan curl_cffi (TLS impersonation)."""
    if not HAS_CURL_CFFI:
        log.warning("curl_cffi tidak tersedia, skip strategy 1")
        return [], {}

    log.info("[Strategy 1] curl_cffi impersonate=%s", IMPERSONATE)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Fetch /live RSC
    log.info("  Fetching %s ...", LIVE_URL)
    rsc_headers = {
        "RSC": "1",
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.6",
        "Referer": "https://m.vidio.com/",
    }
    try:
        resp = session.get(LIVE_URL, headers=rsc_headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  RSC fetch: HTTP %d", resp.status_code)
            return [], {}
        rsc_text = resp.text
        log.info("  RSC response: %d bytes", len(rsc_text))
    except Exception as e:
        log.error("  RSC fetch failed: %s", e)
        return [], {}

    # Parse channels
    channels = extract_channels_from_rsc(rsc_text)
    if not channels:
        # Fallback: try BeautifulSoup
        channels = parse_html_for_channels(rsc_text)

    if not channels:
        log.warning("  Tidak ada channel ditemukan di RSC")
        return [], {}

    log.info("  Found %d channels", len(channels))

    # Step 2: Fetch stream URL untuk setiap channel
    stream_urls: dict[int, str] = {}
    api_headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "Accept": "*/*",
        "Referer": "https://m.vidio.com/",
        "Origin": "https://m.vidio.com",
        "Accept-Language": "id-ID,id;q=0.6",
    }

    for i, ch in enumerate(channels, 1):
        ls_id = ch["livestreaming_id"]
        url = f"{API_BASE}/livestreamings/{ls_id}/stream?initialize=true"
        try:
            resp = session.get(url, headers=api_headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                hls = data.get("data", {}).get("attributes", {}).get("hls", "")
                if hls:
                    stream_urls[ls_id] = hls
                    log.info("  [%d/%d] %s ✓", i, len(channels), ch["livestreaming_title"])
                else:
                    log.warning("  [%d/%d] %s - no hls in response", i, len(channels), ch["livestreaming_title"])
            else:
                log.warning("  [%d/%d] %s - HTTP %d", i, len(channels), ch["livestreaming_title"], resp.status_code)
        except Exception as e:
            log.warning("  [%d/%d] %s - %s", i, len(channels), ch["livestreaming_title"], e)

        time.sleep(DELAY)

    return channels, stream_urls


# ==================== STRATEGY 2: Playwright ====================
def fetch_with_playwright(max_wait: int) -> tuple[list[dict], dict[int, str]]:
    """Fetch via Playwright headless browser (intercept network)."""
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright tidak tersedia, skip strategy 2")
        return [], {}

    log.info("[Strategy 2] Playwright Chromium")
    stream_captures: dict[int, str] = {}

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
            user_agent=(
                "Mozilla/5.0 (Linux; Android 10; K) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/157.0.0.0 Mobile Safari/537.36"
            ),
            viewport={"width": 412, "height": 915},
            locale="id-ID",
        )

        # Intercept stream API responses
        def on_response(response):
            if "/stream?initialize=true" in response.url:
                try:
                    data = response.json()
                    hls = data.get("data", {}).get("attributes", {}).get("hls", "")
                    if hls:
                        m = re.search(r"/livestreamings/(\d+)/stream", response.url)
                        if m:
                            stream_captures[int(m.group(1))] = hls
                            log.info("  [CAPTURE] Stream ID %s ✓", m.group(1))
                except Exception:
                    pass

        context.on("response", on_response)
        page = context.new_page()

        log.info("  Navigating to %s ...", LIVE_URL)
        page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=60000)

        # Wait for RSC data
        rsc_text = ""
        start = time.time()
        while time.time() - start < max_wait:
            rsc_text = page.evaluate("""
                () => {
                    try {
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            if (s.textContent && s.textContent.includes('livestreaming_schedule')) {
                                return s.textContent;
                            }
                        }
                    } catch(e) {}
                    return '';
                }
            """)
            if "livestreaming_schedule" in rsc_text:
                log.info("  RSC data found after %.1fs", time.time() - start)
                break
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(1)
        else:
            log.warning("  Timeout menunggu RSC data")

        # Extra wait for stream captures
        time.sleep(5)

        channels = extract_channels_from_rsc(rsc_text)
        browser.close()

    return channels, stream_captures


# ==================== STRATEGY 3: Selenium ====================
def fetch_with_selenium(max_wait: int) -> tuple[list[dict], dict[int, str]]:
    """Fetch via Selenium (last resort)."""
    if not HAS_SELENIUM:
        log.warning("Selenium tidak tersedia, skip strategy 3")
        return [], {}

    log.info("[Strategy 3] Selenium Chrome")
    stream_captures: dict[int, str] = {}

    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=412,915")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/157.0.0.0 Mobile Safari/537.36"
    )
    options.add_argument("--lang=id-ID")

    driver = webdriver.Chrome(options=options)

    try:
        log.info("  Navigating to %s ...", LIVE_URL)
        driver.get(LIVE_URL)

        # Wait for RSC content
        rsc_text = ""
        start = time.time()
        while time.time() - start < max_wait:
            content = driver.page_source
            if "livestreaming_schedule" in content:
                rsc_text = content
                log.info("  RSC data found after %.1fs", time.time() - start)
                break
            driver.execute_script("window.scrollBy(0, 500)")
            time.sleep(1)
        else:
            log.warning("  Timeout menunggu RSC data")

        channels = extract_channels_from_rsc(rsc_text)

        # Try to get stream URLs via JS fetch
        for ch in channels:
            ls_id = ch["livestreaming_id"]
            try:
                hls = driver.execute_script("""
                    return new Promise((resolve) => {
                        fetch('/livestreamings/%d/stream?initialize=true', {
                            headers: {
                                'x-api-key': '%s',
                                'x-api-platform': 'web-mobile',
                                'x-secure-level': '2'
                            }
                        })
                        .then(r => r.json())
                        .then(d => resolve(d.data?.attributes?.hls || ''))
                        .catch(() => resolve(''));
                    });
                """ % (ls_id, API_KEY))
                if hls:
                    stream_captures[ls_id] = hls
                    log.info("  [%s] Stream ✓", ch["livestreaming_title"])
                time.sleep(DELAY)
            except Exception:
                pass

    finally:
        driver.quit()

    return channels, stream_captures


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list[dict], stream_urls: dict[int, str], output_file: str):
    """Generate file M3U playlist."""
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ls_id = ch["livestreaming_id"]
        title = ch["title"]
        group = ch["livestreaming_title"]
        logo = ch["cover_url"]

        # Prioritaskan non-DRM (dari capture), fallback ke RSC
        stream_url = stream_urls.get(ls_id) or ch.get("stream_url", "")

        if not stream_url:
            log.warning("  [SKIP] No stream: %s | %s", group, title)
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ls_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{group} | {title}'
        )
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] Playlist: %s (%d channels)", output_file, count)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-c", "--count", type=int, default=200,
                        help="Max wait seconds (default: 200)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE,
                        help="Output file (default: playlist.m3u)")
    parser.add_argument("--strategy", type=str, default="auto",
                        choices=["auto", "cffi", "playwright", "selenium"],
                        help="Force strategy (default: auto)")
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Strategies: curl_cffi → Playwright → Selenium")
    print("=" * 55)

    channels: list[dict] = []
    stream_urls: dict[int, str] = {}

    # --- AUTO: try each strategy in order ---
    if args.strategy in ("auto", "cffi"):
        channels, stream_urls = fetch_with_curl_cffi()
        if channels and stream_urls:
            log.info("Strategy 1 (curl_cffi) berhasil!")
            generate_m3u(channels, stream_urls, args.output)
            return

    if args.strategy in ("auto", "playwright"):
        log.info("Fallback ke Playwright...")
        channels_pw, stream_urls_pw = fetch_with_playwright(args.count)
        if channels_pw:
            channels = channels_pw
            stream_urls = {**stream_urls, **stream_urls_pw}
            if stream_urls:
                log.info("Strategy 2 (Playwright) berhasil!")
                generate_m3u(channels, stream_urls, args.output)
                return

    if args.strategy in ("auto", "selenium"):
        log.info("Fallback ke Selenium...")
        channels_se, stream_urls_se = fetch_with_selenium(args.count)
        if channels_se:
            channels = channels_se
            stream_urls = {**stream_urls, **stream_urls_se}
            if stream_urls:
                log.info("Strategy 3 (Selenium) berhasil!")
                generate_m3u(channels, stream_urls, args.output)
                return

    # --- Final fallback: use whatever we have (even DRM-only) ---
    if channels:
        log.warning("Tidak ada non-DRM stream, menggunakan URL dari RSC (mungkin DRM)")
        generate_m3u(channels, stream_urls, args.output)
    else:
        log.error("Gagal: tidak ada channel ditemukan dari semua strategy.")
        sys.exit(1)


if __name__ == "__main__":
    main()   
