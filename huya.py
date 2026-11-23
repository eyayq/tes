#!/usr/bin/env python3
"""
STREAMEAST M3U SCRAPER — v5
- SEMUA kategori (bukan hanya live)
- SEMUA stream per match (Live 1, Live 2, Live 3...)
"""

import asyncio
import re
from datetime import datetime, timezone
from playwright.async_api import async_playwright

SITE_URL = "https://gostreameast.one"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 10
MAX_STREAMS = 50
WAIT_EMBED = 6
WAIT_STREAM = 8
WAIT_TAB = 2  # detik antar klik tab


def se_ts():
    d = datetime.now(timezone.utc)
    return f"{d.year%100:02d}{d.month:02d}{d.day:02d}{d.hour:02d}{d.minute:02d}"


# ─── STAGE 1: Parse DOM (SEMUA match) ────────────────────────────
async def extract_matches():
    print("📡 Stage 1: Parse DOM (semua kategori)...")
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
            "Object.defineProperty(navigator,'webdriver',"
            "{get:()=>undefined});"
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
                    const tag = row.querySelector('.tag')
                        ?.textContent?.trim() || '';
                    const name = row.querySelector('.name')
                        ?.textContent?.trim() || '';
                    const isLive = row.classList.contains('is-live');
                    if (!link?.href) continue;
                    const url = link.href;
                    const dm = url.match(
                        /^https:\\/\\/([^/]+)\\/us\\//);
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


# ─── STAGE 2: Ekstrak SEMUA embed_url + intercept .m3u8 ──────────
async def intercept_streams(us_domain, build, targets):
    all_streams = []
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
            "Object.defineProperty(navigator,'webdriver',"
            "{get:()=>undefined});"
        )

        async def get_all_embeds(match):
            """Buka watch page → klik semua tab → kumpulkan embed_url"""
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
            embeds = []

            try:
                await pg.goto(watch_url, wait_until="domcontentloaded",
                              timeout=30000)
                # Tunggu iframe pertama muncul
                try:
                    await pg.wait_for_selector("#liveIframe", timeout=15000)
                except:
                    await pg.wait_for_timeout(8000)

                # Hitung jumlah tab
                tab_count = await pg.evaluate("""
                    () => document.querySelectorAll('.se-tab-btn')
                          .length
                """)

                # Baca embed dari tab aktif (default = tab 0)
                embeds.append(await pg.evaluate("""
                    () => {
                        const f = document.getElementById('liveIframe');
                        if (!f?.src) return '';
                        const q = new URL(f.src, location.origin)
                            .searchParams.get('url');
                        return q ? decodeURIComponent(q) : '';
                    }
                """))

                # Klik setiap tab berikutnya
                for i in range(1, tab_count):
                    try:
                        await pg.evaluate(f"""
                            () => {{
                                const btns = document
                                    .querySelectorAll('.se-tab-btn');
                                if (btns[{i}]) btns[{i}].click();
                            }}
                        """)
                        await pg.wait_for_timeout(WAIT_TAB)
                        embed = await pg.evaluate("""
                            () => {
                                const f = document
                                    .getElementById('liveIframe');
                                if (!f?.src) return '';
                                const q = new URL(f.src, location.origin)
                                    .searchParams.get('url');
                                return q ? decodeURIComponent(q) : '';
                            }
                        """)
                        if embed and embed not in embeds:
                            embeds.append(embed)
                    except:
                        continue

            except Exception as e:
                print(f"    ⚠ {match['name']}: {e}")
            finally:
                await pg.close()

            return [e for e in embeds if e]

        async def get_m3u8_from_embed(embed_url):
            """Buka embed_url → intercept .m3u8"""
            pg = await context.new_page()
            stream_urls = []

            def on_req(request):
                u = request.url
                if any(x in u for x in
                       [".m3u8", "hls", "playlist", "manifest", ".mpd"]):
                    if not any(x in u.lower() for x in
                               ["facebook", "google", "sensors",
                                "analytics", "histats"]):
                        if u not in stream_urls:
                            stream_urls.append(u)

            def on_resp(response):
                u = response.url
                if any(x in u for x in
                       [".m3u8", "hls", "playlist", "manifest", ".mpd"]):
                    if not any(x in u.lower() for x in
                               ["facebook", "google", "sensors",
                                "analytics", "histats"]):
                        if u not in stream_urls:
                            stream_urls.append(u)

            pg.on("request", on_req)
            pg.on("response", on_resp)

            try:
                await pg.goto(embed_url, wait_until="domcontentloaded",
                              timeout=30000)
                await pg.wait_for_timeout(WAIT_EMBED)

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

                # Cek video / hls
                extra = await pg.evaluate("""
                    () => {
                        const urls = [];
                        const v = document.querySelector('video');
                        if (v) {
                            if (v._hls?.url) urls.push(v._hls.url);
                            if (v.src) urls.push(v.src);
                        }
                        if (window.hls?.url) urls.push(window.hls.url);
                        const ifr = document.querySelector('iframe');
                        if (ifr?.src) urls.push(ifr.src);
                        return urls;
                    }
                """)
                for u in extra:
                    if u and u not in stream_urls:
                        stream_urls.append(u)

                if not stream_urls:
                    content = await pg.content()
                    stream_urls.extend(re.findall(
                        r'(https?://[^"\'\\s]+\\.(?:m3u8|mpd)[^"\'\\s]*)',
                        content
                    ))

            except:
                pass
            finally:
                await pg.close()

            return stream_urls[0] if stream_urls else None

        # ── Proses batch ──
        for batch_start in range(0, len(targets), MAX_WORKERS):
            batch = targets[batch_start:batch_start + MAX_WORKERS]
            tasks = [get_all_embeds(m) for m in batch]
            results = await asyncio.gather(
                *tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    continue
                match = batch[i]
                embeds = result if isinstance(result, list) else []
                if not embeds:
                    print(f"  [{batch_start+i+1}/{len(targets)}] "
                          f"❌ {match['name']} (no embeds)")
                    continue

                print(f"  [{batch_start+i+1}/{len(targets)}] "
                      f"{match['name']} → {len(embeds)} streams")

                # Intercept .m3u8 dari tiap embed
                m3u8_tasks = [get_m3u8_from_embed(e) for e in embeds]
                m3u8_results = await asyncio.gather(
                    *m3u8_tasks, return_exceptions=True)

                for j, m3u8 in enumerate(m3u8_results):
                    if m3u8:
                        all_streams.append({
                            **match,
                            "stream_url": m3u8,
                            "stream_num": j + 1,
                        })

        await browser.close()
    return all_streams


# ─── GENERATE M3U ────────────────────────────────────────────────
def generate_m3u(streams):
    lines = ["#EXTM3U"]
    for s in streams:
        name = s.get("name", "Unknown")
        tag = s.get("tag", "")
        is_live = s.get("is_live", False)
        num = s.get("stream_num", 1)
        group = "LIVE" if is_live else "UPCOMING"
        logo = "https://gostreameast.one/assets/images/favicon-32x32.png"

        display = f"{name} [{tag}] — Live {num}"
        extinf = (
            f'#EXTINF:0,'
            f'tvg-name="{name} [{tag}] Live {num}",'
            f'tvg-logo="{logo}",'
            f'group-title="{group}",'
            f'tvg-id="{tag.lower()}-live{num}",'
            f'{display}'
        )
        lines.append(extinf)
        lines.append(s["stream_url"])
    return "\n".join(lines)


# ─── MAIN ────────────────────────────────────────────────────────
async def main():
    print("=" * 55)
    print("  STREAMEAST M3U SCRAPER v5 (ALL + multi-stream)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    data = await extract_matches()
    us_domain = data.get("usDomain", "")
    build = data.get("build", "")
    all_matches = data.get("matches", [])

    print(f"\n  usDomain: {us_domain}")
    print(f"  Build:    {build}")
    print(f"  Total:    {len(all_matches)}")

    if not us_domain:
        print("  ❌ usDomain kosong.")
        return

    # ✅ SEMUA match (bukan hanya live)
    # Urutkan: live dulu, sisanya setelah
    live = [m for m in all_matches if m.get("is_live")]
    upcoming = [m for m in all_matches if not m.get("is_live")]
    targets = (live + upcoming)[:MAX_STREAMS]

    print(f"  🔴 Live: {len(live)} | ⏳ Upcoming: {len(upcoming)}")
    print(f"  📡 Target: {len(targets)}")

    if not targets:
        print("  ⚠ Tidak ada match.")
        return

    print(f"\n🎬 Scraping {len(targets)} matches "
          f"(semua stream per match)...")
    streams = await intercept_streams(us_domain, build, targets)

    print(f"\n{'═' * 55}")
    if streams:
        m3u = generate_m3u(streams)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(streams)} streams")
        print(f"{'═' * 55}")
        print(f"\n{m3u[:600]}")
    else:
        print(f"  ❌ 0 streams.")
        print(f"{'═' * 55}")


if __name__ == "__main__":
    asyncio.run(main())   
