#!/usr/bin/env python3
"""
Scrape daddylive.app → playlist.m3u (IPTV)
Source: /player/tv*.json → events → channels → embed_N
Options: direct embed URL OR Playwright (extract m3u8)
Requirements:
  pip install requests beautifulsoup4 playwright
  playwright install chromium --with-deps
"""

import re
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────
BASE_URL = "https://daddylive.app"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}
CONCURRENCY = 5
TIMEOUT = 20_000
OUTPUT = "playlist.m3u"

# Semua endpoint JSON
JSON_KEYS = [f"tv{i}" for i in range(1, 20)]

# Headers untuk stream (opsional, via #EXTVLCOPT)
STREAM_HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "origin": BASE_URL,
    "referer": BASE_URL + "/",
}

# Mode: "embed" = pakai URL embed langsung (cepat, tanpa browser)
#       "m3u8"  = resolve ke m3u8 via Playwright (lambat, lebih akurat)
MODE = "embed"  # ← ubah ke "m3u8" kalau mau
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# STEP 1: FETCH SEMUA JSON
# ─────────────────────────────────────────────
def fetch_all_json() -> dict:
    """Fetch semua /player/tv*.json dan kembalikan dict gabungan."""
    print("→ Fetching JSON sources...")
    all_data = {}
    for key in JSON_KEYS:
        url = f"{BASE_URL}/player/{key}.json"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.ok:
                data = r.json()
                if data:
                    all_data[key] = data
                    print(f"  ✓ {key}: {count_events(data)} events")
        except Exception as e:
            print(f"  ✗ {key}: {e}")
    return all_data


def count_events(data) -> int:
    """Hitung total event di dalam JSON."""
    n = 0
    for _top_cat, sub_cats in data.items():
        if not isinstance(sub_cats, dict):
            continue
        for _sub_cat, blocks in sub_cats.items():
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if isinstance(block, dict) and isinstance(block.get("events"), list):
                    n += len(block["events"])
    return n


# ─────────────────────────────────────────────
# STEP 2: PARSE EVENTS → CHANNELS → EMBED URLs
# ─────────────────────────────────────────────
def extract_embed_urls(ch: dict) -> list[str]:
    """Ambil semua embed_N dari channel object."""
    urls = []
    for k in sorted(ch.keys()):
        if re.match(r"^embed_\d+$", k) and ch[k]:
            urls.append(ch[k])
    return urls


def parse_all_events(all_data: dict) -> list[dict]:
    """
    Parse semua JSON → list of:
    {
        "name": "Team A vs Team B",
        "category": "Soccer",
        "time": "14:00",
        "source": "tv2",
        "embeds": ["https://...embed/xxx", ...],
    }
    """
    events = []
    seen = set()

    for src_key, data in all_data.items():
        for top_cat, sub_cats in data.items():
            if not isinstance(sub_cats, dict):
                continue
            for sub_cat, blocks in sub_cats.items():
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    category = (block.get("category") or block.get("Category") or sub_cat).strip()
                    for ev in (block.get("events") or []):
                        if not isinstance(ev, dict):
                            continue
                        event_name = (ev.get("event") or "").strip()
                        if not event_name:
                            continue

                        # Dedup berdasarkan nama event + source
                        dedup_key = f"{event_name}|{src_key}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        # Kumpulkan semua embed URLs dari semua channels
                        embeds = []
                        for ch in (ev.get("channels") or []):
                            if not isinstance(ch, dict):
                                continue
                            embeds.extend(extract_embed_urls(ch))

                        if not embeds:
                            continue

                        events.append({
                            "name": event_name,
                            "category": category,
                            "time": ev.get("time", ""),
                            "source": src_key,
                            "embeds": embeds,
                        })

    print(f"  ✓ Total: {len(events)} events dengan stream\n")
    return events


# ─────────────────────────────────────────────
# STEP 3: RESOLVE M3U8 (Playwright) — optional
# ─────────────────────────────────────────────
async def resolve_m3u8(page, embed_url: str) -> str | None:
    """
    Kunjungi halaman embed, intercept request .m3u8 / .ts
    Return: m3u8 URL atau None
    """
    m3u8_url = None

    def on_request(request):
        nonlocal m3u8_url
        url = request.url
        if (".m3u8" in url or ".ts" in url) and not m3u8_url:
            if ".m3u8" in url:
                m3u8_url = url

    page.on("request", on_request)
    try:
        await page.goto(embed_url, wait_until="networkidle", timeout=TIMEOUT)
        await asyncio.sleep(3)
    except Exception:
        pass
    finally:
        page.remove_listener("request", on_request)

    if m3u8_url:
        return m3u8_url

    # Fallback: regex di DOM
    try:
        content = await page.content()
        m = re.search(r'["\']([^"\']*?\.m3u8[^"\']*)["\']', content)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# STEP 4: BUILD PLAYLIST
# ─────────────────────────────────────────────
def build_extvlcopt_lines(event: dict, stream_url: str) -> list[str]:
    """Generate baris #EXTVLCOPT."""
    lines = []
    lines.append(f'#EXTVLCOPT:http-referrer={BASE_URL}/')
    lines.append(f'#EXTVLCOPT:http-user-agent={STREAM_HEADERS["user-agent"]}')
    lines.append(f'#EXTVLCOPT:http-headers=Origin: {BASE_URL}')
    return lines


async def build_playlist(events: list[dict]):
    lines = ["#EXTM3U"]
    ok = 0

    if MODE == "embed":
        # ── MODE EMBED: langsung pakai URL embed (cepat) ──
        for i, ev in enumerate(events, 1):
            # Pakai embed pertama sebagai stream utama
            stream_url = ev["embeds"][0]
            logo = f"{BASE_URL}/images/hdtv.png"

            lines.append(
                f'#EXTINF:-1 tvg-id="{ev["name"]}"'
                f' tvg-logo="{logo}"'
                f' group-title="{ev["category"]}",{ev["name"]}'
            )
            lines.extend(build_extvlcopt_lines(ev, stream_url))
            lines.append(stream_url)
            ok += 1
            print(f"  [{i}/{len(events)}] ✓ {ev['name']} ({len(ev['embeds'])} embeds)")

    else:
        # ── MODE M3U8: resolve via Playwright (lambat) ──
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            pages = [await browser.new_page() for _ in range(CONCURRENCY)]

            async def process(ev, idx):
                nonlocal ok
                async with semaphore:
                    page = pages[idx % CONCURRENCY]
                    # Coba embed pertama, kalau gagal coba yang lain
                    m3u8 = None
                    for embed_url in ev["embeds"]:
                        m3u8 = await resolve_m3u8(page, embed_url)
                        if m3u8:
                            break

                    if m3u8:
                        logo = f"{BASE_URL}/images/hdtv.png"
                        lines.append(
                            f'#EXTINF:-1 tvg-id="{ev["name"]}"'
                            f' tvg-logo="{logo}"'
                            f' group-title="{ev["category"]}",{ev["name"]}'
                        )
                        lines.extend(build_extvlcopt_lines(ev, m3u8))
                        lines.append(m3u8)
                        ok += 1
                        print(f"  [{idx+1}/{len(events)}] ✓ {ev['name']}")
                    else:
                        print(f"  [{idx+1}/{len(events)}] ✗ {ev['name']}")

            tasks = [process(ev, i) for i, ev in enumerate(events)]
            await asyncio.gather(*tasks)
            await browser.close()

    # ── Tulis file ──
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n{'='*50}")
    print(f"  ✅ Berhasil : {ok}/{len(events)}")
    print(f"  📄 File     : {OUTPUT}")
    print(f"  📡 Mode     : {MODE}")
    print(f"{'='*50}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    all_data = fetch_all_json()
    if not all_data:
        print("❌ Tidak ada data. Cek koneksi / domain.")
        exit(1)

    events = parse_all_events(all_data)
    if not events:
        print("❌ Tidak ada event dengan embed URL.")
        exit(1)

    print(f"→ Mode: {MODE.upper()}")
    asyncio.run(build_playlist(events))   
