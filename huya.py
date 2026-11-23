#!/usr/bin/env python3
"""
STREAMEAST M3U SCRAPER — v3 (DOM parsing)
- Parse .row elements dari DOM (bukan closure variable)
- Ekstrak usDomain + build dari href
- Anti-detection args
"""

import asyncio
import re
from datetime import datetime, timezone
from playwright.async_api import async_playwright

SITE_URL = "https://gostreameast.one"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 3
MAX_STREAMS = 50
WAIT_STREAM = 12


def se_ts():
    d = datetime.now(timezone.utc)
    return f"{d.year%100:02d}{d.month:02d}{d.day:02d}{d.hour:02d}{d.minute:02d}"


# ─── STAGE 1: Parse DOM rendered ─────────────────────────────────
async def extract_matches():
    print("📡 Stage 1: Parse DOM (Playwright)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        # Patch webdriver flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        page = await context.new_page()
        await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=30000)

        # ✅ TUNGGU .row elements muncul di DOM
        try:
            await page.wait_for_selector(".row", timeout=20000)
        except:
            print("  ⚠ .row tidak muncul dalam 20s")
            # Coba scroll / tunggu lebih
            await page.wait_for_timeout(5000)

        # ✅ PARSE DOM — bukan closure variable
        data = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('li.row');
                const matches = [];
                let usDomain = '';
                let build = '';

                for (const row of rows) {
                    const link = row.querySelector('a.row-link');
                    const tag = row.querySelector('.tag')?.textContent?.trim() || '';
                    const name = row.querySelector('.name')?.textContent?.trim() || '';
                    const isLive = row.classList.contains('is-live');

                    if (!link || !link.href) continue;

                    // Ekstrak usDomain + build dari href
                    const url = link.href;
                    const domainMatch = url.match(/^https:\\/\\/([^/]+)\\/us\\//);
                    if (domainMatch && !usDomain) usDomain = domainMatch[1];
                    const buildMatch = url.match(/[?&]v=([^&]+)/);
                    if (buildMatch && !build) build = buildMatch[1];

                    // Ekstrak page + id dari query params
                    const params = new URL(url).searchParams;
                    const pageId = params.get('p') || 'us';
                    const matchId = params.get('id') || params.get('m') || '';

                    matches.push({
                        id: matchId,
                        name: name,
                        tag: tag,
                        is_live: isLive,
                        page: pageId,
                        watch_url: url,  // ✅ simpan URL lengkap
                    });
                }
                return { usDomain, build, matches };
            }
        """)
        await browser.close()
        return data


# ─── STAGE 2: Intercept stream ───────────────────────────────────
async def intercept_streams(us_domain, build, targets):
    streams = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        async def get_stream(match, idx):
            # ✅ Pakai watch_url langsung dari DOM (sudah lengkap)
            watch_url = match.get("watch_url", "")
            if not watch_url:
                # Fallback: build manual
                page_id = match.get("page", "us")
                mp = (f"m={match['id']}" if page_id == "soccer"
                      else f"id={match['id']}")
                watch_url = (
                    f"https://{us_domain}/us/{page_id}.html"
                    f"?p={page_id}&d={us_domain}&{mp}"
                    f"&v={build}&ref=gostreameast.one&t={se_ts()}"
                )

            pg = await context.new_page()
            stream_urls = []

            def on_request(request):
                url = request.url
                if any(x in url for x in [".m3u8", "hls", "playlist", "manifest"]):
                    if not any(x in url.lower() for x in
                               ["facebook", "google", "sensors", "analytics"]):
                        if url not in stream_urls:
                            stream_urls.append(url)

            def on_response(response):
                url = response.url
                if any(x in url for x in [".m3u8", "hls", "playlist", "manifest"]):
                    if not any(x in url.lower() for x in
                               ["facebook", "google", "sensors", "analytics"]):
                        if url not in stream_urls:
                            stream_urls.append(url)

            pg.on("request", on_request)
            pg.on("response", on_response)

            try:
                await pg.goto(watch_url, wait_until="domcontentloaded",
                              timeout=30000)
                await pg.wait_for_timeout(3000)

                # Klik play
                for sel in ["video", "[class*='player']",
                            "[class*='play']", "button"]:
                    try:
                        el = await pg.query_selector(sel)
                        if el:
                            await el.click(timeout=1000)
                            break
                    except:
                        continue

                await pg.wait_for_timeout(WAIT_STREAM)

                # Cek video.src + hls.js
                extra = await pg.evaluate("""
                    () => {
                        const urls = [];
                        const v = document.querySelector('video');
                        if (v) {
                            if (v._hls?.url) urls.push(v._hls.url);
                            if (v.src) urls.push(v.src);
                        }
                        if (window.hls?.url) urls.push(window.hls.url);
                        const el = document.querySelector('[data-src]');
                        if (el) urls.push(el.dataset.src);
                        return urls;
                    }
                """)
                for u in extra:
                    if u and u not in stream_urls:
                        stream_urls.append(u)

                # Fallback: regex di HTML
                if not stream_urls:
                    content = await pg.content()
                    stream_urls.extend(
                        re.findall(
                            r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)',
                            content
                        )
                    )

            except Exception as e:
                print(f"    ⚠ {match['name']}: {e}")
            finally:
                await pg.close()

            label = f"{match['name']} [{match.get('tag','')}]"
            if stream_urls:
                print(f"  [{idx+1}/{len(targets)}] ✅ {label}")
                return {**match, "stream_url": stream_urls[0]}
            else:
                print(f"  [{idx+1}/{len(targets)}] ❌ {label}")
                return None

        for batch_start in range(0, len(targets), MAX_WORKERS):
            batch = targets[batch_start:batch_start + MAX_WORKERS]
            tasks = [
                get_stream(m, batch_start + i)
                for i, m in enumerate(batch)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    streams.append(r)

        await browser.close()
    return streams


# ─── GENERATE M3U ────────────────────────────────────────────────
def generate_m3u(streams):
    lines = ["#EXTM3U"]
    for s in streams:
        name = s.get("name", "Unknown")
        tag = s.get("tag", "")
        is_live = s.get("is_live", False)
        group = "LIVE" if is_live else "UPCOMING"
        logo = "https://gostreameast.one/assets/images/favicon-32x32.png"

        extinf = (
            f'#EXTINF:0,'
            f'tvg-name="{name} [{tag}]",'
            f'tvg-logo="{logo}",'
            f'group-title="{group}",'
            f'tvg-id="{tag.lower()}",'
            f'{name} [{tag}]'
        )
        lines.append(extinf)
        lines.append(s["stream_url"])
    return "\n".join(lines)


# ─── MAIN ────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  STREAMEAST - M3U SCRAPER v3 (DOM)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    data = await extract_matches()
    us_domain = data.get("usDomain", "")
    build = data.get("build", "")
    all_matches = data.get("matches", [])

    print(f"\n  usDomain: {us_domain}")
    print(f"  Build:    {build}")
    print(f"  Matches:  {len(all_matches)}")

    if not us_domain:
        print("  ❌ usDomain kosong. Coba headless=False")
        return

    live = [m for m in all_matches if m.get("is_live")]
    print(f"  🔴 Live: {len(live)} / {len(all_matches)}")

    if not live:
        print("  ⚠ Tidak ada LIVE.")
        return

    targets = live[:MAX_STREAMS]
    print(f"\n🎬 Intercepting {len(targets)} streams...")
    streams = await intercept_streams(us_domain, build, targets)

    print(f"\n{'═' * 60}")
    if streams:
        m3u = generate_m3u(streams)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(streams)} streams")
    else:
        print("  ❌ 0 streams.")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    asyncio.run(main())   
