import asyncio
import json
import re
import base64
from datetime import datetime
from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright

SITE_URL = "https://gostreameast.one"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 5
MAX_STREAMS = 20

SESSION = cffi_requests.Session(impersonate="chrome", timeout=20)

# ─── DECODE __lxc ───────────────────────────────────────
def decode_lxc(encoded_str):
    try:
        padding = 4 - len(encoded_str) % 4
        if padding != 4:
            encoded_str += "=" * padding
        return base64.b64decode(encoded_str).decode("utf-8", errors="ignore")
    except:
        try:
            s = encoded_str.replace("-", "+").replace("_", "/")
            padding = 4 - len(s) % 4
            if padding != 4:
                s += "=" * padding
            return base64.b64decode(s).decode("utf-8", errors="ignore")
        except:
            return encoded_str

# ─── FETCH HOMEPAGE (curl_cffi) ─────────────────────────
def fetch_homepage():
    print("📡 Fetching homepage (curl_cffi)...")
    r = SESSION.get(SITE_URL)
    if r.status_code != 200:
        print(f"  ⚠ HTTP {r.status_code}")
        return [], ""
    html = r.text
    print(f"  Size: {len(html)} bytes")
    lxc_match = re.search(r'window\.__lxc\s*=\s*(\[[^\]]+\])', html)
    if not lxc_match:
        return [], html
    lxc_raw = lxc_match.group(1)
    lxc_values = re.findall(r'"([^"]+)"', lxc_raw)
    api_urls = [decode_lxc(v) for v in lxc_values]
    print(f"  ✅ __lxc: {len(api_urls)} URLs")
    return api_urls, html

# ─── FETCH API DATA ─────────────────────────────────────
def fetch_api_data(api_urls):
    if not api_urls:
        return None, None, None, None
    results = []
    for url in api_urls:
        try:
            r = SESSION.get(url, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": SITE_URL,
                "Origin": SITE_URL,
            })
            text = r.text
            if 'k1' in text[:20]:
                results.append(None)
            else:
                try:
                    results.append(json.loads(text))
                except:
                    results.append(None)
        except:
            results.append(None)
    while len(results) < 4:
        results.append(None)
    return results[0], results[1], results[2], results[3]

# ─── PLAYWRIGHT: EXTRACT MATCH DATA ─────────────────────
async def playwright_extract():
    print("\n🎬 Playwright (extract match data)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        await page.goto(SITE_URL, wait_until="networkidle", timeout=30000)

        try:
            await page.wait_for_function(
                "window.seAllMatches && window.seAllMatches.length > 0",
                timeout=20000
            )
        except:
            await page.wait_for_timeout(10000)

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

# ─── INTERCEPT STREAMS ──────────────────────────────────
async def intercept_streams(us_domain, build, targets):
    streams = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        async def get_stream(match, idx):
            page_id = match.get("page", "us")
            match_param = f"m={match['id']}" if page_id == "soccer" else f"id={match['id']}"
            watch_url = (
                f"https://{us_domain}/us/{page_id}.html"
                f"?p={page_id}&d={us_domain}&{match_param}"
                f"&v={build}&ref=gostreameast.one"
            )
            pg = await context.new_page()
            stream_urls = []

            def on_response(response):
                url = response.url
                if ".m3u8" in url or "hls" in url.lower():
                    if not any(x in url.lower() for x in ["facebook", "google", "sensors"]):
                        stream_urls.append(url)

            pg.on("response", on_response)
            try:
                await pg.goto(watch_url, wait_until="domcontentloaded", timeout=20000)
                await pg.wait_for_timeout(5000)
                for sel in ["video", "[class*='player']", "[class*='play']", "button"]:
                    try:
                        el = await pg.query_selector(sel)
                        if el:
                            await el.click(timeout=1000)
                            break
                    except:
                        continue
                await pg.wait_for_timeout(3000)
                if not stream_urls:
                    content = await pg.content()
                    stream_urls.extend(re.findall(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', content))
            except:
                pass
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
            tasks = [get_stream(m, batch_start + i) for i, m in enumerate(batch)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    streams.append(r)
        await browser.close()
    return streams

# ─── GENERATE M3U ───────────────────────────────────────
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
                dt = datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
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

# ─── MAIN ───────────────────────────────────────────────
async def main():
    print("=" * 70)
    print("  STREAMEAST - M3U STREAM SCRAPER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # Stage 1: curl_cffi
    api_urls, html = fetch_homepage()
    m_data, c_data, d_data, l_data = fetch_api_data(api_urls)

    all_matches = []
    us_domain = ""
    build = "20260917a"

    if d_data and isinstance(d_data, dict):
        us_domain = str(d_data.get("us_live_domain", "")).lower()
        if d_data.get("matches"):
            for m in d_data["matches"]:
                if m.get("active", 1) != 0:
                    all_matches.append({
                        "id": m.get("id", ""),
                        "name": f"{m.get('home',{}).get('name','')} vs {m.get('away',{}).get('name','')}",
                        "tag": "SOC",
                        "color": "#16a34a",
                        "is_live": m.get("status") == 1,
                        "page": "soccer",
                        "category": m.get("league", {}).get("name", ""),
                        "begin_at": f"{m.get('date','')}T{m.get('time','00:00')}:00Z",
                    })

    if m_data and isinstance(m_data, dict) and m_data.get("matches"):
        for m in m_data["matches"]:
            all_matches.append({
                "id": m.get("id", ""),
                "name": m.get("title", ""),
                "tag": (m.get("sport", "?") or "?")[:4].upper(),
                "color": "#E10601",
                "is_live": bool(m.get("live")),
                "page": "us",
                "category": m.get("sport", ""),
                "begin_at": m.get("starts_at", ""),
            })

    # Stage 2: Playwright fallback
    if not all_matches or not us_domain:
        print("\n⚠ curl_cffi tidak cukup. Playwright fallback...")
        pw_data = await playwright_extract()
        if pw_data:
            us_domain = pw_data.get("usDomain", "")
            build = pw_data.get("build", build)
            all_matches = (pw_data.get("matches", []) +
                          pw_data.get("soccer", []) +
                          pw_data.get("latin", []))
            print(f"  ✅ usDomain: {us_domain}, Matches: {len(all_matches)}")

    if not us_domain and html:
        m = re.search(r'https://([^/"]+?)/us/', html)
        if m:
            us_domain = m.group(1)

    if not us_domain:
        print("\n  ❌ Tidak bisa dapatkan usDomain.")
        return

    live_matches = [m for m in all_matches if m.get("is_live")]
    print(f"\n  🔴 Live: {len(live_matches)} / {len(all_matches)} total")

    if not live_matches:
        print("  ⚠ Tidak ada match LIVE. Exit.")
        return

    # Stage 3: Intercept
    targets = live_matches[:MAX_STREAMS]
    print(f"\n🎬 Intercepting {len(targets)} streams...")
    streams = await intercept_streams(us_domain, build, targets)

    # Output
    print(f"\n{'═' * 70}")
    if streams:
        m3u = generate_m3u(streams)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(streams)} streams")
        print(f"{'═' * 70}")
        print(f"\n{m3u[:800]}")
    else:
        print(f"  ❌ 0 streams.")
        print(f"{'═' * 70}")

if __name__ == "__main__":
    asyncio.run(main())   
