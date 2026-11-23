#!/usr/bin/env python3
"""
STREAMEAST M3U SCRAPER — v4
- Watch page → #liveIframe → embed_url → actual .m3u8
- 2-level iframe intercept
"""

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, unquote
from playwright.async_api import async_playwright

SITE_URL = "https://gostreameast.one"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 3
MAX_STREAMS = 50
WAIT_EMBED = 8   # detik tunggu embed load
WAIT_STREAM = 10  # detik tunggu stream di level 2


def se_ts():
    d = datetime.now(timezone.utc)
    return f"{d.year%100:02d}{d.month:02d}{d.day:02d}{d.hour:02d}{d.minute:02d}"


def ts_api():
    """Timestamp format API: YYYYMMDDHHmm"""
    d = datetime.now(timezone.utc)
    return (f"{d.year}{d.month:02d}{d.day:02d}"
            f"{d.hour:02d}{d.minute:02d}")


# ─── STAGE 1: Parse DOM (match list) ─────────────────────────────
async def extract_matches():
    print("📡 Stage 1: Parse DOM...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await context.new_page()
        await page.goto(SITE_URL, wait_until="domcontentloaded",
                        timeout=30000)
        try:
            await page.wait_for_selector(".row", timeout=20000)
        except:
            await page.wait_for_timeout(5000)

        data = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('li.row');
                const matches = [];
                let usDomain = '', build = '';
                for (const row of rows) {
                    const link = row.querySelector('a.row-link');
                    const tag = row.querySelector('.tag')?.textContent?.trim()||'';
                    const name = row.querySelector('.name')?.textContent?.trim()||'';
                    const isLive = row.classList.contains('is-live');
                    if (!link?.href) continue;
                    const url = link.href;
                    const dm = url.match(/^https:\\/\\/([^/]+)\\/us\\//);
                    if (dm && !usDomain) usDomain = dm[1];
                    const bm = url.match(/[?&]v=([^&]+)/);
                    if (bm && !build) build = bm[1];
                    const params = new URL(url).searchParams;
                    matches.push({
                        id: params.get('id')||params.get('m')||'',
                        name, tag, is_live: isLive,
                        page: params.get('p')||'us',
                        watch_url: url,
                    });
                }
                return { usDomain, build, matches };
            }
        """)
        await browser.close()
        return data


# ─── STAGE 2: 2-level stream intercept ───────────────────────────
async def intercept_streams(us_domain, build, targets):
    streams = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-first-run", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        async def get_stream(match, idx):
            watch_url = match.get("watch_url", "")
            if not watch_url:
                page_id = match.get("page", "us")
                mp = (f"m={match['id']}" if page_id == "soccer"
                      else f"id={match['id']}")
                watch_url = (
                    f"https://{us_domain}/us/{page_id}.html"
                    f"?p={page_id}&d={us_domain}&{mp}"
                    f"&v={build}&ref=gostreameast.one&t={se_ts()}"
                )

            pg = await context.new_page()
            embed_url = ""
            stream_urls = []

            # ── LEVEL 1: watch page ──
            try:
                await pg.goto(watch_url, wait_until="domcontentloaded",
                              timeout=30000)

                # Tunggu #liveIframe muncul (JS sudah decrypt API + render)
                try:
                    await pg.wait_for_selector("#liveIframe", timeout=15000)
                except:
                    await pg.wait_for_timeout(8000)

                # Ekstrak embed_url dari iframe src
                embed_url = await pg.evaluate("""
                    () => {
                        const iframe = document.getElementById('liveIframe');
                        if (!iframe?.src) return '';
                        // src = "/frame.html?url=ENCODED_URL"
                        const qs = new URL(iframe.src, location.origin)
                                     .searchParams.get('url');
                        return qs ? decodeURIComponent(qs) : '';
                    }
                """)

            except Exception as e:
                print(f"    ⚠ L1 {match['name']}: {e}")
            finally:
                await pg.close()

            if not embed_url:
                print(f"  [{idx+1}/{len(targets)}] ❌ {match['name']} "
                      f"(no embed_url)")
                return None

            # ── LEVEL 2: buka embed_url, intercept .m3u8 ──
            pg2 = await context.new_page()
            stream_urls = []

            def on_req(request):
                u = request.url
                if any(x in u for x in [".m3u8", "hls", "playlist",
                                        "manifest", ".mpd"]):
                    if not any(x in u.lower() for x in
                               ["facebook", "google", "sensors",
                                "analytics", "histats"]):
                        if u not in stream_urls:
                            stream_urls.append(u)

            def on_resp(response):
                u = response.url
                if any(x in u for x in [".m3u8", "hls", "playlist",
                                        "manifest", ".mpd"]):
                    if not any(x in u.lower() for x in
                               ["facebook", "google", "sensors",
                                "analytics", "histats"]):
                        if u not in stream_urls:
                            stream_urls.append(u)

            pg2.on("request", on_req)
            pg2.on("response", on_resp)

            try:
                await pg2.goto(embed_url, wait_until="domcontentloaded",
                               timeout=30000)
                await pg2.wait_for_timeout(WAIT_EMBED)

                # Klik play kalau ada
                for sel in ["video", "[class*='player']",
                            "[class*='play']", "button",
                            ".vjs-big-play-button"]:
                    try:
                        el = await pg2.query_selector(sel)
                        if el:
                            await el.click(timeout=1000)
                            break
                    except:
                        continue

                await pg2.wait_for_timeout(WAIT_STREAM)

                # Cek video.src / hls.js
                extra = await pg2.evaluate("""
                    () => {
                        const urls = [];
                        const v = document.querySelector('video');
                        if (v) {
                            if (v._hls?.url) urls.push(v._hls.url);
                            if (v.src) urls.push(v.src);
                        }
                        if (window.hls?.url) urls.push(window.hls.url);
                        // Cek iframe nested (level 3)
                        const ifr = document.querySelector('iframe');
                        if (ifr?.src) urls.push(ifr.src);
                        return urls;
                    }
                """)
                for u in extra:
                    if u and u not in stream_urls:
                        stream_urls.append(u)

                # Fallback: regex di HTML
                if not stream_urls:
                    content = await pg2.content()
                    stream_urls.extend(
                        re.findall(
                            r'(https?://[^"\'\\s]+\\.(?:m3u8|mpd)[^"\'\\s]*)',
                            content
                        )
                    )

            except Exception as e:
                print(f"    ⚠ L2 {match['name']}: {e}")
            finally:
                await pg2.close()

            label = f"{match['name']} [{match.get('tag','')}]"
            if stream_urls:
                print(f"  [{idx+1}/{len(targets)}] ✅ {label}")
                return {**match, "stream_url": stream_urls[0],
                        "embed_url": embed_url}
            else:
                print(f"  [{idx+1}/{len(targets)}] ❌ {label} "
                      f"(embed={embed_url[:60]}...)")
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
    print("=" * 55)
    print("  STREAMEAST M3U SCRAPER v4 (2-level)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    data = await extract_matches()
    us_domain = data.get("usDomain", "")
    build = data.get("build", "")
    all_matches = data.get("matches", [])

    print(f"\n  usDomain: {us_domain}")
    print(f"  Build:    {build}")
    print(f"  Matches:  {len(all_matches)}")

    if not us_domain:
        print("  ❌ usDomain kosong.")
        return

    live = [m for m in all_matches if m.get("is_live")]
    print(f"  🔴 Live: {len(live)} / {len(all_matches)}")

    if not live:
        print("  ⚠ Tidak ada LIVE.")
        return

    targets = live[:MAX_STREAMS]
    print(f"\n🎬 Intercepting {len(targets)} streams "
          f"(L1=embed, L2=stream, wait={WAIT_EMBED}+{WAIT_STREAM}s)...")
    streams = await intercept_streams(us_domain, build, targets)

    print(f"\n{'═' * 55}")
    if streams:
        m3u = generate_m3u(streams)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(streams)} streams")
        print(f"{'═' * 55}")
        print(f"\n{m3u[:500]}")
    else:
        print(f"  ❌ 0 streams.")
        print(f"{'═' * 55}")


if __name__ == "__main__":
    asyncio.run(main())   
