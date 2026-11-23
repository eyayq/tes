#!/usr/bin/env python3
"""
STREAMEAST M3U SCRAPER — FIXED
- Playwright handles ALL site JS (decode __lxc, AES decrypt, API calls)
- Intercept via request + video.src + hls.js state
- Watch URL includes &t= cache-buster
"""

import asyncio
import re
import json
from datetime import datetime, timezone
from playwright.async_api import async_playwright

SITE_URL = "https://gostreameast.one"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 3
MAX_STREAMS = 50
WAIT_STREAM = 12  # detik tunggu stream load


def se_ts():
    """Cache-buster: YYmmddHHii (UTC)"""
    d = datetime.now(timezone.utc)
    return f"{d.year%100:02d}{d.month:02d}{d.day:02d}{d.hour:02d}{d.minute:02d}"


# ─── STAGE 1: Ekstrak match data dari site JS ────────────────────
async def extract_matches():
    print("📡 Stage 1: Ekstrak match data (Playwright)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        await page.goto(SITE_URL, wait_until="networkidle", timeout=30000)

        # Tunggu sampai site JS selesai load semua data
        try:
            await page.wait_for_function(
                "window.seAllMatches && window.seAllMatches.length > 0",
                timeout=20000
            )
        except:
            await page.wait_for_timeout(10000)

        # Ekstrak SEMUA dari variable JS site
        data = await page.evaluate("""
            () => ({
                usDomain: window.seUsDomain || '',
                build: window.SE_BUILD || '',
                matches: (window.seAllMatches || []).map(m => ({
                    id: m.id,
                    name: m.name,
                    tag: m.tag,
                    color: m.color,
                    is_live: m.is_live,
                    page: m.page || 'us',
                    category: m.category?.name || '',
                    begin_at: m.begin_at || '',
                })),
                soccer: (window.seSoccer || []).map(m => ({
                    id: m.id,
                    name: m.name,
                    tag: m.tag,
                    color: m.color,
                    is_live: m.is_live,
                    page: 'soccer',
                    category: m.category?.name || '',
                    begin_at: m.begin_at || '',
                })),
                latin: (window.seLatin || []).map(m => ({
                    id: m.id,
                    name: m.name,
                    tag: m.tag,
                    color: m.color,
                    is_live: m.is_live,
                    page: 'latin',
                    category: m.category?.name || '',
                    begin_at: m.begin_at || '',
                })),
            })
        """)
        await browser.close()
        return data


# ─── STAGE 2: Intercept stream URL ───────────────────────────────
async def intercept_streams(us_domain, build, targets):
    streams = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        async def get_stream(match, idx):
            page_id = match.get("page", "us")
            match_param = (
                f"m={match['id']}" if page_id == "soccer"
                else f"id={match['id']}"
            )
            # ✅ TAMBAHKAN &t= CACHE-BUSTER
            watch_url = (
                f"https://{us_domain}/us/{page_id}.html"
                f"?p={page_id}&d={us_domain}&{match_param}"
                f"&v={build}&ref=gostreameast.one"
                f"&t={se_ts()}"
            )

            pg = await context.new_page()
            stream_urls = []

            # ✅ INTERCEPT VIA REQUEST (bukan cuma response)
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

                # Klik play button jika ada
                for sel in [
                    "video", "[class*='player']", "[class*='play']",
                    "button", ".play-btn", "[data-action='play']"
                ]:
                    try:
                        el = await pg.query_selector(sel)
                        if el:
                            await el.click(timeout=1000)
                            break
                    except:
                        continue

                # ✅ TUNGGU LEBIH LAMA (stream load)
                await pg.wait_for_timeout(WAIT_STREAM)

                # ✅ CEK video.src langsung
                video_src = await pg.evaluate("""
                    () => {
                        const v = document.querySelector('video');
                        if (!v) return '';
                        // hls.js: cek via Hls instance
                        if (v._hls && v._hls.url) return v._hls.url;
                        return v.src || '';
                    }
                """)
                if video_src and video_src not in stream_urls:
                    stream_urls.append(video_src)

                # ✅ CEK hls.js internal state
                hls_url = await pg.evaluate("""
                    () => {
                        // Cari Hls instance di DOM
                        const v = document.querySelector('video');
                        if (v && v._hls) {
                            try {
                                return v._hls.url || '';
                            } catch(e) {}
                        }
                        // Cek window-level
                        if (window.hls && window.hls.url) return window.hls.url;
                        // Cek data attribute
                        const el = document.querySelector('[data-src]');
                        if (el) return el.getAttribute('data-src') || '';
                        return '';
                    }
                """)
                if hls_url and hls_url not in stream_urls:
                    stream_urls.append(hls_url)

                # ✅ Fallback: regex di HTML source
                if not stream_urls:
                    content = await pg.content()
                    found = re.findall(
                        r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)', content
                    )
                    stream_urls.extend(found)

                # ✅ Fallback 2: regex di JS variables
                if not stream_urls:
                    js_urls = await pg.evaluate("""
                        () => {
                            const html = document.documentElement.innerHTML;
                            const matches = html.match(
                                /https?:\\/\\/[\\w\\-./]+\\.m3u8[^"']*/g
                            );
                            return matches || [];
                        }
                    """)
                    stream_urls.extend(js_urls)

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

        # Batch processing
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
        begin_at = s.get("begin_at", "")

        group = "LIVE" if is_live else "UPCOMING"

        match_time = ""
        if begin_at:
            try:
                dt = datetime.fromisoformat(
                    begin_at.replace("Z", "+00:00"))
                match_time = dt.strftime("%H:%M")
            except:
                pass

        logo_url = "https://gostreameast.one/assets/images/favicon-32x32.png"
        display_title = f"{name} [{tag}]"
        if match_time:
            display_title += f" ({match_time})"

        extinf = (
            f'#EXTINF:0,'
            f'tvg-name="{name} [{tag}]",'
            f'tvg-logo="{logo_url}",'
            f'group-title="{group}",'
            f'tvg-id="{tag.lower()}",'
            f'{display_title}'
        )
        lines.append(extinf)
        lines.append(s["stream_url"])

    return "\n".join(lines)


# ─── MAIN ────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  STREAMEAST - M3U STREAM SCRAPER (FIXED)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Stage 1: Playwright extract (site JS handles decode + AES)
    pw_data = await extract_matches()

    us_domain = pw_data.get("usDomain", "")
    build = pw_data.get("build", "20260917a")
    all_matches = (
        pw_data.get("matches", []) +
        pw_data.get("soccer", []) +
        pw_data.get("latin", [])
    )

    print(f"\n  usDomain: {us_domain}")
    print(f"  Build:    {build}")
    print(f"  Matches:  {len(all_matches)}")

    if not us_domain:
        print("  ❌ Tidak bisa dapatkan usDomain.")
        return

    # Filter live only
    live_matches = [m for m in all_matches if m.get("is_live")]
    print(f"  🔴 Live: {len(live_matches)} / {len(all_matches)}")

    if not live_matches:
        print("  ⚠ Tidak ada match LIVE.")
        return

    # Stage 2: Intercept
    targets = live_matches[:MAX_STREAMS]
    print(f"\n🎬 Intercepting {len(targets)} streams "
          f"(wait={WAIT_STREAM}s each)...")
    streams = await intercept_streams(us_domain, build, targets)

    # Output
    print(f"\n{'═' * 60}")
    if streams:
        m3u = generate_m3u(streams)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(streams)} streams")
        print(f"{'═' * 60}")
        print(f"\n{m3u[:600]}")
    else:
        print(f"  ❌ 0 streams.")
        print(f"{'═' * 60}")


if __name__ == "__main__":
    asyncio.run(main())   
