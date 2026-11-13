import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
import logging
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Error: pip install 'curl-cffi<0.14'")
    sys.exit(1)

# ==================== KONFIGURASI ====================
API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
OUTPUT_FILE = "playlist.m3u"
IMPERSONATE = "chrome"
DELAY = 0.3

# Fallback DRM pattern (jika stream_url kosong di data section)
STREAM_DRM_PATTERN = (
    "https://etslive-v3-vidio-com-tokenized.akamaized.net"
    "/stream/{id}/file/drm/hls/master.m3u8"
)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Mobile Safari/537.36"
)
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

# [FIX 1] x-api-key yang hilang
X_API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5"
    "/kbNqmxScg7wv0Qx/FgU4yLE2Kf52t4QUCXu80YeQUkJESbfJS3ACIfGz8LFiEqCE8UkYYMHiVl12alJ+/lhROE="
)

# ==================== USER ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"

# [FIX 4] Token baru
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFExTnpVek1EZ3VPRGc1TVRFNElsMD0i"
    "LCJleHAiOiIyMDI4LTExLTEzVDEzOjA4OjI4Ljg4OVoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--7d30ce3e8ef000910b9b05e6c368e03015aaefd1"
)

# [FIX 2] access_token dari cookie
ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30s"
    "ImV4cCI6MTc5NDYyNDU0NX0.bUPeP6j3OWV0mZypBSzgz53X7-ei_FZkHyMfoYdgNDo"
)

# [FIX 3] _vidio_session dari cookie
VIDIO_SESSION = (
    "j2K5OJoP8BXLkI9vZpq6N1hFzSS%2BtqGsQxBwG2DIN7wk6U04oZlsQ5l%2BqLqDj%2FC87hZPPvU2kbOquCYkAS0AnKhHW4PH8K8m90ayqRLBIMRbfyvD369ajaNobkvBXVh9S5n8"
    "%2FAal6okcTC%2BwCLpHcBIixLwbw9cVw5HDLWr%2BOPDjU6IBAAmiOgS8PTTOBfBcBKMaXrP9X8%2FPqI4%2FNsu"
    "%2Ftyrs%2FXmJkStZaYNhDKJke5s52r7eZdBeOMbpezrsaS55WvEB7LRcaIbEI9NAF9xkqMtWeL2q3JCouOMtlanZP0"
    "%2BRHhkYxU2W4T3Q13w%2F5jf6VuweVAdSgS7lyJcdJlM2fKJgqa18ioVoP1hlox1n4hk2JNCTCaLwDoZV%2FXSOy1sCrqglkZDLhMIa0FLQHJHojHR9"
    "--VHG1%2BLXHP%2FmMKHLh--BApQtLuesYlXXLLMx4PU5g%3D%3D"
)

# [FIX 3] ahoy_visit
AHOY_VISIT = "94286b7e-197b-4581-b481-ee5685207fac"

# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vidio-m3u")


# ==================== X-SIGNATURE ====================
def generate_x_signature() -> tuple:
    t = str(time.time())
    secret_key = f"V1d10D3v:{t}"
    signature = hmac.new(
        secret_key.encode(),
        t.encode(),
        hashlib.sha256
    ).hexdigest()
    return t, signature


# ==================== API HEADERS ====================
def api_headers(luws: str = "") -> dict:
    """Hanya header API kustom. Biarkan impersonate handle browser headers."""
    h = {
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR,
        "x-user-id": USER_ID,
        "x-api-key": X_API_KEY,          # [FIX 1] TAMBAH x-api-key
        "origin": ORIGIN,
        "referer": REFERER,
        "accept": "application/json",
    }
    if luws:
        h["luws"] = luws
    return h


# ==================== STEP 1: BOOTSTRAP SESSION ====================
def bootstrap_session(session) -> str:
    log.info("Bootstrap session ...")

    # [FIX 2 & 3] Set SEMUA cookie yang diperlukan
    session.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    session.cookies.set("ahoy_visit", AHOY_VISIT, domain=".vidio.com", path="/")
    session.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("country_id", "ID", domain=".vidio.com", path="/")
    session.cookies.set("access_token", ACCESS_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("_vidio_session", VIDIO_SESSION, domain=".vidio.com", path="/")
    session.cookies.set("plenty_id", USER_ID, domain=".vidio.com", path="/")
    session.cookies.set("shva", "1", domain=".vidio.com", path="/")

    try:
        resp = session.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", resp.status_code)
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    at = session.cookies.get("access_token")
    if at:
        log.info("  ✓ access_token: %s...", at[:30])

    luws = session.cookies.get("luws", "")
    if not luws:
        luws = f"{str(uuid.uuid4()).upper()}_{USER_ID}"
    log.info("  ✓ luws: %s", luws)
    return luws


# ==================== STEP 2: VERIFY LOGIN ====================
def verify_login(session) -> bool:
    h = api_headers()
    h.update({"x-auth-prefer": "AccessToken"})
    try:
        resp = session.get(f"{SITE_URL}/interactions.json", headers=h, timeout=15)
        if resp.status_code == 200:
            user = resp.json().get("current_user", {})
            if user:
                log.info("  ✓ Login: %s (id=%s)", user.get("display_name"), user.get("user_id"))
                log.info("  ✓ Sub: %s", "Premium" if user.get("has_active_subscription") else "Free")
                return True
    except Exception as e:
        log.warning("  Verify gagal: %s", e)
    return False


# ==================== STEP 3: FETCH ALL CHANNELS (SCRAPE) ====================
def fetch_all_channels(session, luws: str) -> list:
    """
    GET /categories/live/sections → semua section & channel.
    Extract semua item dengan content_type == "livestreaming".
    Deduplicate by content_id.
    """
    url = f"{API_BASE}/categories/live/sections"
    params = {"page[number]": 1, "page[size]": 10, "included": "contents"}
    h = api_headers(luws)

    log.info("Fetching all channels via /categories/live/sections ...")
    channels = {}  # dedup by content_id
    page = 1

    while True:
        params["page[number]"] = page
        try:
            resp = session.get(url, params=params, headers=h, timeout=30)
            if resp.status_code != 200:
                log.warning("  Page %d: HTTP %d", page, resp.status_code)
                break
        except Exception as e:
            log.error("  Page %d error: %s", page, e)
            break

        data = resp.json()
        sections = data.get("data", [])
        if isinstance(sections, dict):
            sections = [sections]

        for section in sections:
            attrs = section.get("attributes", {})
            section_title = attrs.get("title", "Lainnya")
            contents = attrs.get("contents", [])

            for item in contents:
                content_type = item.get("content_type", "")
                if content_type != "livestreaming":
                    continue

                ch_id = item.get("content_id")
                if not ch_id or ch_id in channels:
                    continue

                channel_name = item.get("alt_title") or item.get("title", "Unknown")
                program_name = item.get("title", "")
                logo = item.get("cover_url", "")
                stream_url = item.get("stream_url", "")

                channels[ch_id] = {
                    "id": int(ch_id),
                    "title": channel_name,
                    "program": program_name,
                    "logo": logo,
                    "group": section_title,
                    "stream_url": stream_url,
                }

        links = data.get("links", {})
        if links.get("next"):
            page += 1
            time.sleep(DELAY)
        else:
            break

    result = list(channels.values())
    log.info("  Found %d unique channels", len(result))
    return result


# ==================== STEP 4: FETCH NON-DRM STREAM URL ====================
def fetch_stream_url(session, luws: str, ch_id: int) -> str:
    """GET /livestreamings/{id}/stream?initialize=true → non-DRM HLS URL."""
    url = f"{API_BASE}/livestreamings/{ch_id}/stream?initialize=true"
    x_client, x_signature = generate_x_signature()

    h = api_headers(luws)
    h.update({
        "x-user-email": USER_EMAIL,
        "x-client": x_client,
        "x-signature": x_signature,
    })

    try:
        resp = session.get(url, headers=h, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            hls = data.get("data", {}).get("attributes", {}).get("hls", "")
            if hls:
                return hls
    except Exception as e:
        log.debug("  Exception: %s", e)
    return ""


# ==================== M3U GENERATOR ====================
def generate_m3u(channels: list, stream_urls: dict, output_file: str):
    lines = ["#EXTM3U"]
    count = 0

    for ch in channels:
        ch_id = ch["id"]
        title = ch["title"]
        logo = ch.get("logo", "")
        group = ch.get("group", "Lainnya")

        stream_url = (
            stream_urls.get(ch_id)
            or ch.get("stream_url", "")
            or STREAM_DRM_PATTERN.format(id=ch_id)
        )

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{title}'
        )
        lines.append(stream_url)
        count += 1

    Path(output_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("[✓] %s (%d channels)", output_file, count)


# ==================== MAIN ====================
def main():
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator (Scrape All)")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--drm-only", action="store_true",
                        help="Skip non-DRM, langsung pakai DRM URL dari section")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U (Scrape All Channels)")
    print("  Source: /categories/live/sections")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    # Step 1: Bootstrap
    luws = bootstrap_session(session)

    # Step 2: Verify login
    if not verify_login(session):
        log.error("Login gagal! Check REMEMBER_TOKEN / ACCESS_TOKEN.")
        sys.exit(1)

    # Step 3: Fetch ALL channels
    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel ditemukan.")
        sys.exit(1)

    # Show summary by group
    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups:")
    for g, names in groups.items():
        log.info("    [%s] %d channels: %s", g, len(names), ", ".join(names[:5]) + ("..." if len(names) > 5 else ""))

    # Step 4: Fetch non-DRM stream URLs
    stream_urls = {}
    if not args.drm_only:
        need_fetch = [ch for ch in channels if not ch.get("stream_url")]
        log.info("\nFetching non-DRM URLs for %d channels (no stream_url in data) ...", len(need_fetch))

        failed = []
        for i, ch in enumerate(need_fetch, 1):
            hls = fetch_stream_url(session, luws, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [%d/%d] %s ✓", i, len(need_fetch), ch["title"])
            else:
                failed.append(ch)
                log.warning("  [%d/%d] %s ✗", i, len(need_fetch), ch["title"])
            time.sleep(DELAY)

        # Retry
        for attempt in range(1, args.retries + 1):
            if not failed:
                break
            log.info("  Retry %d/%d (%d) ...", attempt, args.retries, len(failed))
            time.sleep(5)
            still = []
            for ch in failed:
                hls = fetch_stream_url(session, luws, ch["id"])
                if hls:
                    stream_urls[ch["id"]] = hls
                    log.info("  [R%d] %s ✓", attempt, ch["title"])
                else:
                    still.append(ch)
                time.sleep(DELAY)
            failed = still

        log.info("\n  Non-DRM: %d/%d", len(stream_urls), len(need_fetch))

    # Step 5: Generate M3U
    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output)


if __name__ == "__main__":
    main()   
