

import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
MAX_WORKERS = 5  # thread pool
RETRY_DELAY = 3.0
RETRY_WAIT = 5

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

# Channel internasional (untuk group-title)
INTERNATIONAL = {
    "Arirang", "ABC Australia", "Euronews", "CNA",
    "France24", "MUSICA", "U-Channel TV",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== X-SIGNATURE ====================
def generate_x_signature() -> tuple:
    t_float = time.time()
    t_int = int(t_float)
    x_client = f"{t_float}"
    secret_key = f"V1d10D3v:{t_int}"
    signature = hmac.new(
        secret_key.encode(),
        x_client.encode(),
        hashlib.sha256
    ).hexdigest()
    return x_client, signature


# ==================== STEP 1: AUTO LUWS + VISITOR ====================
def get_luws_and_visitor() -> tuple:
    """
    Auto ambil luws + ahoy_visitor via Playwright.
    JS di m.vidio.com set luws via document.cookie.
    """
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright tidak tersedia, pakai random")
        return (
            f"{str(uuid.uuid4()).upper()}_",
            str(uuid.uuid4()),
        )

    log.info("Mengambil luws + visitor-id via Playwright ...")
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
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=30000)

        # Tunggu JS set cookie luws
        luws = ""
        visitor_id = ""
        for _ in range(15):
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            luws = cookies.get("luws", "")
            visitor_id = cookies.get("ahoy_visitor", "")
            if luws and visitor_id:
                break
            time.sleep(1)

        browser.close()

    if luws and visitor_id:
        log.info("  luws: %s", luws)
        log.info("  visitor-id: %s", visitor_id)
    else:
        log.warning("  Gagal ambil dari browser, pakai random")
        luws = luws or f"{str(uuid.uuid4()).upper()}_"
        visitor_id = visitor_id or str(uuid.uuid4())

    return luws, visitor_id


# ==================== STEP 2: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str, visitor_id: str) -> list:
    url = f"{API_BASE}/livestreamings"
    params = {"stream_type": "tv_stream"}
    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": visitor_id,
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "false",
        "luws": luws,
        "Accept": "application/json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
    }

    log.info("Fetching all channels ...")
    try:
        resp = session.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            log.warning("  HTTP %d", resp.status_code)
            return []
    except Exception as e:
        log.error("  Error: %s", e)
        return []

    data = resp.json()
    channels = []

    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        ch_id = item.get("id")
        if not ch_id:
            continue

        title = attrs.get("title", "Unknown")

        # Logo: square_image (120x120) → fallback landscape
        square = attrs.get("square_image", {})
        landscape = attrs.get("image_landscape_url", "")
        logo = square.get("url", "") or landscape

        # Group-title: TV Nasional / TV Internasional
        group = "TV Internasional" if title in INTERNATIONAL else "TV Nasional"

        channels.append({
            "id": int(ch_id),
            "title": title,
            "logo": logo,
            "group": group,
            "is_live": "ongoing_schedule" in item.get("relationships", {}),
        })

    log.info("  Found %d channels", len(channels))
    return channels


# ==================== STEP 3: FETCH STREAM URL ====================
def fetch_stream_url(session, luws: str, visitor_id: str, ch_id: int) -> str:
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    headers = {
        "x-api-key": API_KEY,
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": visitor_id,
        "luws": luws,
        "x-client": x_client,
        "x-signature": x_signature,
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Origin": ORIGIN,
        "Accept": "*/*",
    }

    try:
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("attributes", {}).get("hls", "")
    except Exception:
        pass

    return ""


# ==================== THREAD POOL ====================
def fetch_all_streams(channels: list, luws: str, visitor_id: str, max_workers: int) -> tuple:
    """Fetch semua stream URL secara parallel."""
    stream_urls = {}
    failed = []

    def _fetch(ch):
        ch_id = ch["id"]
        hls = fetch_stream_url(
            cffi_requests.Session(impersonate=IMPERSONATE),
            luws, visitor_id, ch_id
        )
        return ch, hls

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, ch): ch for ch in channels}
        done = 0
        for future in as_completed(futures):
            ch, hls = future.result()
            done += 1
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [%d/%d] %s ✓", done, len(channels), ch["title"])
            else:
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗", done, len(channels), ch["title"])

    return stream_urls, failed


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        group = ch["group"]
        logo = ch["logo"]
        title = ch["title"]
        stream_url = stream_urls.get(ch_id, "")

        if not stream_url:
            log.warning("  [SKIP] No stream: %s", title)
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help="Thread pool workers (default: 5)")
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    print("=" * 55)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("  Auto luws + ThreadPool")
    print("=" * 55)

    # Step 1: Auto ambil luws + visitor-id
    luws, visitor_id = get_luws_and_visitor()

    # Step 2: Fetch channels
    session = cffi_requests.Session(impersonate=IMPERSONATE)
    channels = fetch_all_channels(session, luws, visitor_id)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    live_count = sum(1 for c in channels if c.get("is_live"))
    log.info("\n  Total: %d channels (%d live, %d off)",
             len(channels), live_count, len(channels) - live_count)

    for ch in channels:
        status = "●" if ch.get("is_live") else "○"
        log.info("    [%s] %s  %s", status, ch["id"], ch["title"])

    # Step 3: Fetch stream URLs (parallel)
    log.info("\nFetching stream URLs (%d workers) ...", args.workers)
    start = time.time()
    stream_urls, failed = fetch_all_streams(channels, luws, visitor_id, args.workers)
    elapsed = time.time() - start
    log.info("  Done in %.1fs", elapsed)

    # Step 4: Retry failed
    for attempt in range(1, args.retries + 1):
        if not failed:
            break
        log.info("\nRetry %d/%d (%d channels) ...", attempt, args.retries, len(failed))
        time.sleep(RETRY_WAIT)
        _, failed = fetch_all_streams(failed, luws, visitor_id, args.workers)
        # Merge results
        for ch in failed:
            pass  # failed list sudah di-update

        # Re-fetch yang masih gagal
        still_failed = []
        for ch in failed:
            hls = fetch_stream_url(session, luws, visitor_id, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [RETRY %d] %s ✓", attempt, ch["title"])
            else:
                still_failed.append(ch)
        failed = still_failed

    # Summary
    log.info("\n  ─────────────────────────────────")
    log.info("  Total : %d", len(channels))
    log.info("  OK    : %d", len(stream_urls))
    log.info("  Fail  : %d", len(failed))
    log.info("  Time  : %.1fs", time.time() - start)
    log.info("  ─────────────────────────────────")

    if failed:
        log.info("  Tanpa stream:")
        for ch in failed:
            log.info("    - [%s] %s", ch["id"], ch["title"])

    # Step 5: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
