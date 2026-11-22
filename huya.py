#!/usr/bin/env python3
"""
Debug: temukan API endpoint yang mengembalikan stream URL
"""

import re
import sys
import asyncio
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


async def debug_match(match_url: str):
    print(f"🔍 Debugging: {match_url}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="vi-VN",
            extra_http_headers={
                "Referer": IFRAME_REFERER,
                "Origin": IFRAME_REFERER.rstrip("/"),
            },
        )
        page = await context.new_page()

        # === LOG SEMUA XHR/FETCH REQUEST + RESPONSE ===
        async def on_response(response):
            url = response.url
            req = response.request
            resource_type = req.resource_type

            # Hanya log XHR/Fetch (API calls)
            if resource_type in ("xhr", "fetch"):
                print(f"\n{'='*60}")
                print(f"📡 XHR/FETCH: {req.method} {url}")
                print(f"   Status: {response.status}")
                print(f"   Headers: {dict(response.headers)}")
                try:
                    body = await response.text()
                    print(f"   Body: {body[:1000]}")
                except Exception as e:
                    print(f"   Body: [error: {e}]")
                print(f"{'='*60}")

            # Log semua request yang mengandung 'stream' atau 'player' atau 'api'
            elif any(kw in url.lower() for kw in ("stream", "player", "api", "embed", "xl365", "domainkqt")):
                print(f"\n📌 INTERESTING: {resource_type} {req.method} {url}")
                print(f"   Status: {response.status}")

        context.on("response", on_response)

        # === LOG CONSOLE MESSAGES ===
        page.on("console", lambda msg: print(f"🖥️  [CONSOLE {msg.type}] {msg.text[:200]}"))

        # === LOG PAGE ERRORS ===
        page.on("pageerror", lambda err: print(f"❌ [PAGE ERROR] {err}"))

        # === LOAD PAGE ===
        print("⏳ Loading page...")
        await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)

        # Tunggu 30 detik untuk semua JS execution
        print("⏳ Waiting 30s for JS to execute...")
        await page.wait_for_timeout(30000)

        # === LOG IFRAME STATE ===
        print(f"\n{'='*60}")
        print("📋 IFRAME STATE AFTER 30s:")
        iframes = await page.query_selector_all("iframe")
        for i, iframe in enumerate(iframes):
            src = await iframe.get_attribute("src") or ""
            print(f"   iframe[{i}]: {src[:150]}")
        print(f"{'='*60}")

        # === LOG ALL SCRIPTS (cari API endpoint) ===
        print(f"\n📜 Searching scripts for API endpoints...")
        scripts = await page.query_selector_all("script")
        for script in scripts:
            text = await script.inner_text()
            # Cari pattern API
            for pattern in [
                r'(?:fetch|axios|ajax|XMLHttpRequest)[^\n]*',
                r'["\'](/api/[^"\']+|https?://[^"\']*api[^"\']*)["\']',
                r'random_stream_url[^\n]*',
                r'stream_url[^\n]*',
                r'player_url[^\n]*',
                r'xl365[^\n]*',
                r'domainkqt[^\n]*',
            ]:
                matches = re.findall(pattern, text)
                if matches:
                    for m in matches[:5]:
                        print(f"   🎯 {m[:150]}")

        # === LOG WINDOW VARS ===
        print(f"\n🌐 Window variables:")
        try:
            vars_result = await page.evaluate("""
                () => {
                    const interesting = {};
                    for (const key of Object.keys(window)) {
                        if (key.toLowerCase().includes('stream') ||
                            key.toLowerCase().includes('player') ||
                            key.toLowerCase().includes('video') ||
                            key.toLowerCase().includes('url') ||
                            key.toLowerCase().includes('embed')) {
                            const val = window[key];
                            if (typeof val === 'string' && val.length < 500) {
                                interesting[key] = val;
                            } else if (typeof val === 'object' && val !== null) {
                                interesting[key] = JSON.stringify(val).slice(0, 200);
                            }
                        }
                    }
                    return interesting;
                }
            """)
            for k, v in vars_result.items():
                print(f"   window.{k} = {v[:150]}")
        except Exception as e:
            print(f"   Error: {e}")

        # === LOG PERFORMANCE ENTRIES ===
        print(f"\n⚡ Performance entries (resource):")
        try:
            entries = await page.evaluate("""
                () => performance.getEntriesByType('resource')
                    .filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
                    .map(e => e.name)
            """)
            for e in entries:
                print(f"   {e[:150]}")
        except Exception as e:
            print(f"   Error: {e}")

        await browser.close()


def main():
    if len(sys.argv) < 2:
        # Ambil 1 match live dari homepage
        print("🔄 Fetching homepage untuk cari match live...")
        resp = cffi_requests.get(BASE_URL, headers=HEADERS, impersonate="chrome", timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select(".main-grid-match"):
            status = item.get("data-status", "1")
            if status in {"2", "3", "4", "5", "6", "51", "52", "53", "54"}:
                link = item.select_one("a.redirectPopup[href*='/truc-tiep/']")
                if link:
                    slug_path = link.get("href", "")
                    match_url = urljoin(BASE_URL, slug_path)
                    print(f"🔴 Found live match: {match_url}\n")
                    asyncio.run(debug_match(match_url))
                    return

        print("⚠ No live match found. Usage: python debug.py <slug>")
        return

    slug = sys.argv[1].strip("/")
    match_url = f"{BASE_URL}/truc-tiep/{slug}/"
    asyncio.run(debug_match(match_url))


if __name__ == "__main__":
    main()   
