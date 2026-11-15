#!/usr/bin/env python3
import re
import json
import asyncio
import requests
from urllib.parse import urlparse, parse_qs, unquote, quote
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ─────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────
BASE_URL   = "https://daddylive.app"
PROXY_BASE = "https://exsample.d-n-s.name"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}
CONCURRENCY = 20
TIMEOUT     = 25_000
OUTPUT      = "playlist.m3u"

JSON_KEYS = [f"tv{i}" for i in range(1, 20)]
MAX_EMBEDS_PER_EVENT = 3

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/159.0.0.0 Safari/537.36")

SKIP_HEADERS = {"accept-encoding", "host", "content-length", "connection"}
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────
def strip_emoji(text: str) -> str:
    """Hapus emoji & symbol non-ASCII."""
    return ''.join(
        c for c in text
        if ord(c) < 0x1F000 or c in ' \n\t'
    ).strip()


def extract_category(event_name: str) -> str:
    if " :" in event_name:
        return strip_emoji(event_name.split(" :")[0].strip())
    return "Other"


def extract_clean_name(event_name: str) -> str:
    if " :" in event_name:
        return strip_emoji(event_name.split(" :")[1].strip())
    return strip_emoji(event_name.strip())


def is_tiktok_cdn(m3u8_url: str) -> bool:
    """Quick check: apakah m3u8 ini pakai TikTok CDN segments."""
    try:
        r = requests.get(m3u8_url, headers={"User-Agent": BROWSER_UA}, timeout=10)
        return "tiktokcdn" in r.text
    except Exception:
        return False


def extract_m3u8_from_wrapper(url: str) -> str | None:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("url", "src", "file", "source", "stream", "playlist"):
        if key in qs:
            val = unquote(qs[key][0])
            if ".m3u8" in val:
                if ".html" in val or "?url=" in val or "?src=" in val:
                    return extract_m3u8_from_wrapper(val)
                return val
    m = re.search(r'(https?://[^&"\']+\.m3u8[^&"\']*)', unquote(url))
    if m:
        return m.group(1)
    return None


# ─────────────────────────────────────────────
# STEP 1: FETCH JSON
# ─────────────────────────────────────────────
def fetch_all_json() -> dict:
    print("→ Fetching JSON sources...")
    all_data = {}
    for key in JSON_KEYS:
        url = f"{BASE_URL}/player/{key}.json"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.ok:
                text = r.text.strip()
                if not text or not text.startswith("{"):
                    print(f"  ✗ {key}: empty/non-JSON")
                    continue
                data = json.loads(text)
                if data:
                    all_data[key] = data
                    n = count_events(data)
                    print(f"  ✓ {key}: {n} events")
        except json.JSONDecodeError:
            print(f"  ✗ {key}: invalid JSON")
        except Exception as e:
            print(f"  ✗ {key}: {e}")
    return all_data


def count_events(data) -> int:
    """Hitung event — handle Format A & B."""
    n = 0
    for _key, value in data.items():
        if isinstance(value, dict):
            # Format A: {topCat: {subCat: [events]}}
            for _sub, items in value.items():
                if isinstance(items, list):
                    n += len(items)
        elif isinstance(value, list):
            # Format B: {dateKey: [{category, events: [...]}]}
            for block in value:
                if isinstance(block, dict) and isinstance(block.get("events"), list):
                    n += len(block["events"])
    return n


# ─────────────────────────────────────────────
# STEP 2: PARSE EVENTS (Format A & B)
# ─────────────────────────────────────────────
def parse_all_events(all_data: dict) -> list[dict]:
    events = []
    seen = set()

    for src_key, data in all_data.items():
        for top_key, value in data.items():

            # ── Format A: {topCat: {subCat: [eventObjects]}} ──
            if isinstance(value, dict):
                for sub_cat, items in value.items():
                    if not isinstance(items, list):
                        continue
                    for ev in items:
                        if not isinstance(ev, dict):
                            continue
                        raw_name = (ev.get("event") or "").strip()
                        if not raw_name:
                            continue
                        event_name = strip_emoji(raw_name)

                        dedup_key = f"{event_name}|{src_key}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        embeds = []
                        for ch in (ev.get("channels") or []):
                            if not isinstance(ch, dict):
                                continue
                            for k in sorted(ch.keys()):
                                if re.match(r"^embed_\d+$", k) and ch[k]:
                                    embeds.append(ch[k])

                        if not embeds:
                            continue

                        events.append({
                            "name": extract_clean_name(raw_name),
                            "full_name": event_name,
                            "category": extract_category(raw_name),
                            "time": ev.get("time", "Live"),
                            "source": src_key,
                            "embeds": embeds,
                        })

            # ── Format B: {dateKey: [{category, events: [...]}]} ──
            elif isinstance(value, list):
                for block in value:
                    if not isinstance(block, dict):
                        continue
                    raw_cat = (block.get("Category") or block.get("category") or "Other").strip()
                    category = strip_emoji(raw_cat)

                    for ev in (block.get("events") or []):
                        if not isinstance(ev, dict):
                            continue
                        raw_name = (ev.get("event") or "").strip()
                        if not raw_name:
                            continue
                        event_name = strip_emoji(raw_name)

                        dedup_key = f"{event_name}|{src_key}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        embeds = []
                        for ch in (ev.get("channels") or []):
                            if not isinstance(ch, dict):
                                continue
                            for k in sorted(ch.keys()):
                                if re.match(r"^embed_\d+$", k) and ch[k]:
                                    embeds.append(ch[k])

                        if not embeds:
                            continue

                        events.append({
                            "name": event_name,
                            "full_name": event_name,
                            "category": category,
                            "time": ev.get("time", "Live"),
                            "source": src_key,
                            "embeds": embeds,
                        })

    print(f"  ✓ Total: {len(events)} events\n")
    return events


# ─────────────────────────────────────────────
# STEP 3: RESOLVE M3U8 + INTERCEPT HEADERS
# ─────────────────────────────────────────────
async def resolve_m3u8(page, embed_url: str) -> dict | None:
    m3u8_url = None
    m3u8_headers = {}

    def on_request(request):
        nonlocal m3u8_url, m3u8_headers
        if m3u8_url:
            return
        url = request.url
        parsed = urlparse(url)
        path = parsed.path

        if path.endswith(".m3u8"):
            m3u8_url = url
            m3u8_headers = dict(request.headers)
            return

        if ".m3u8" in url and not path.endswith(".m3u8"):
            extracted = extract_m3u8_from_wrapper(url)
            if extracted:
                m3u8_url = extracted
                m3u8_headers = dict(request.headers)
                return

    page.on("request", on_request)
    try:
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=TIMEOUT)
        try:
            await page.wait_for_request(
                lambda r: ".m3u8" in r.url,
                timeout=10_000
            )
        except Exception:
            pass
        await asyncio.sleep(3)
    except Exception:
        pass
    finally:
        page.remove_listener("request", on_request)

    if m3u8_url:
        return {"m3u8": m3u8_url, "headers": m3u8_headers}

    # Fallback 1: <video> element
    try:
        video_src = await page.evaluate("""
            () => {
                const v = document.querySelector('video');
                if (v && v.src) return v.src;
                if (v && v.currentSrc) return v.currentSrc;
                return null;
            }
        """)
        if video_src and ".m3u8" in video_src:
            return {"m3u8": video_src, "headers": {}}
    except Exception:
        pass

    # Fallback 2: JS variables
    try:
        js_m3u8 = await page.evaluate("""
            () => {
                if (window.streamUrl) return window.streamUrl;
                if (window.videoUrl) return window.videoUrl;
                if (window.playerUrl) return window.playerUrl;
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const text = s.textContent || '';
                    const m = text.match(/["']([^"']*\\.m3u8[^"']*)["']/);
                    if (m) return m[1];
                }
                return null;
            }
        """)
        if js_m3u8 and ".m3u8" in js_m3u8:
            if js_m3u8.startswith("//"):
                js_m3u8 = "https:" + js_m3u8
            return {"m3u8": js_m3u8, "headers": {}}
    except Exception:
        pass

    # Fallback 3: regex di HTML
    try:
        content = await page.content()
        for m in re.finditer(r'["\']([^"\']*?\.m3u8[^"\']*)["\']', content):
            candidate = m.group(1).strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            p = urlparse(candidate)
            if p.path.endswith(".m3u8") or not p.path.endswith(".html"):
                return {"m3u8": candidate, "headers": {}}
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────
# STEP 4: BUILD PLAYLIST
# ─────────────────────────────────────────────
def write_header_lines(lines: list[str], headers: dict):
    for key, value in headers.items():
        lk = key.lower()
        if lk in SKIP_HEADERS:
            continue
        if not isinstance(value, str):
            value = str(value)
        if lk == "referer":
            lines.append(f'#EXTVLCOPT:http-referrer={value}')
        elif lk == "user-agent":
            lines.append(f'#EXTVLCOPT:http-user-agent={value}')
        else:
            lines.append(f'#EXTVLCOPT:http-headers={key}: {value}')


def build_proxy_url(m3u8_url: str, headers: dict) -> str:
    if not isinstance(m3u8_url, str):
        m3u8_url = str(m3u8_url)
    referrer = headers.get("referer", "")
    origin   = headers.get("origin", "")
    params   = f"url={quote(m3u8_url, safe='')}"
    if referrer:
        params += f"&referer={quote(referrer, safe='')}"
    if origin:
        params += f"&origin={quote(origin, safe='')}"
    return f"{PROXY_BASE}/m3u8.php?{params}"


async def build_playlist(events: list[dict]):
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = []
    failed = []
    skipped_tiktok = []

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = await browser.new_context(
            user_agent=BROWSER_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        pages = []
        for _ in range(CONCURRENCY):
            page = await context.new_page()
            pages.append(page)

        async def process(ev: dict, idx: int):
            nonlocal results, failed, skipped_tiktok
            async with semaphore:
                page = pages[idx % CONCURRENCY]
                resolved = None

                for embed_url in ev["embeds"][:MAX_EMBEDS_PER_EVENT]:
                    try:
                        await page.goto("about:blank", timeout=5000)
                    except Exception:
                        pass

                    resolved = await resolve_m3u8(page, embed_url)
                    if resolved:
                        # ← Filter: skip TikTok CDN
                        if is_tiktok_cdn(resolved["m3u8"]):
                            resolved = None
                            continue
                        break

                if resolved and isinstance(resolved.get("m3u8"), str):
                    results.append({**ev, **resolved})
                    print(f"  [{idx+1}/{len(events)}] ✓ {ev['name']}")
                elif skipped_tiktok_count(ev, idx):
                    skipped_tiktok.append(ev["name"])
                    print(f"  [{idx+1}/{len(events)}] ⚠ {ev['name']} (TikTok CDN)")
                else:
                    failed.append(ev["name"])
                    print(f"  [{idx+1}/{len(events)}] ✗ {ev['name']}")

        tasks = [process(ev, i) for i, ev in enumerate(events)]
        await asyncio.gather(*tasks)
        await browser.close()

    # ── Tulis playlist ──
    lines = ["#EXTM3U"]
    for ev in results:
        headers = ev.get("headers", {})
        if not isinstance(headers, dict):
            headers = {}

        try:
            proxy_url = build_proxy_url(ev["m3u8"], headers)
        except Exception as e:
            print(f"  [!] Skip {ev['name']}: {e}")
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ev["name"]}"'
            f' group-title="{ev["category"]}",{ev["full_name"]}'
        )
        write_header_lines(lines, headers)
        lines.append(proxy_url)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # ── Ringkasan ──
    print(f"\n{'='*50}")
    print(f"  ✅ Berhasil   : {len(results)}/{len(events)}")
    if skipped_tiktok:
        print(f"  ⚠  TikTok CDN : {len(skipped_tiktok)} (skipped)")
    if failed:
        print(f"  ❌ Gagal      : {len(failed)}")
        for name in failed[:10]:
            print(f"     - {name}")
        if len(failed) > 10:
            print(f"     ... dan {len(failed)-10} lainnya")
    print(f"  📄 File        : {OUTPUT}")
    print(f"  🌐 Proxy       : {PROXY_BASE}")
    print(f"{'='*50}")


def skipped_tiktok_count(ev, idx) -> bool:
    """Placeholder — diisi oleh process() via closure."""
    return False


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

    asyncio.run(build_playlist(events))   
