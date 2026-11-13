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

API_KEY = (
    "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/"
    "kbNqmxScg7wv0Qx/FgU4yLE2Kf52t4QUCXu80YeQUkJESbfJS3ACIfGz8LFiEqCE8UkYYMHiVl12alJ+/"
    "lhROE="
)

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

# ==================== USER (FRESH 13 Nov 2026) ====================
USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
AHOY_VISIT = "94286b7e-197b-4581-b481-ee5685207fac"
ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30s"
    "ImV4cCI6MTc5NDYyNDU0NX0.bUPeP6j3OWV0mZypBSzgz53X7-ei_FZkHyMfoYdgNDo"
)
REMEMBER_TOKEN = (
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFExTnpRd056QXVPVGc1TlRrMk5DSmQi"
    "LCJleHAiOiIyMDI4LTExLTEzVDEyOjQ3OjUwLjk4OVoiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--acafa3b630bc3e51b589dbb5e5c8d9cb506e2511"
)
VIDIO_SESSION = (
    "t3A%2BcN8aBooxL7fKPY9G0VGh%2F%2FDPylvngN993PNlyJ1VpHd6rjAYSxaHrfyd1aNPuVRgAGWUqiERMHy0ReTwweK3vy%2BoAQ2AoPhlo237fo5OVtOUMHJkg38rmf7yHoBBa9xTU94UsZyUOQALInQYcEM1weXO78AdgKBJ%2Bgdq1qs9dD9CnSq9QccxTBjSCWW2deIypsFR4Y3o6uVcNN8umO6cwXuQUExGpYDfvGFqmrET5swPnwJ8S3BMU72Kz4WSSkEqGqD6Yn5%2B581o8st2oqxFI7ZHqw%3D%3D"
    "--ymVcR2hmdQWB8sOR--tEd0VilyCIQozUgUKNh2pQ%3D%3D"
)
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
    h = {
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-visitor-id": AHOY_VISITOR,
        "x-user-id": USER_ID,
        "x-api-key": API_KEY,
        "x-api-app-info": "js/m.vidio.com",
        "x-user-login": "true",
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

    session.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    session.cookies.set("ahoy_visit", AHOY_VISIT, domain=".vidio.com", path="/")
    session.cookies.set("plenty_id", USER_ID, domain=".vidio.com", path="/")
    session.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("access_token", ACCESS_TOKEN, domain=".vidio.com", path="/")
    session.cookies.set("_vidio_session", VIDIO_SESSION, domain=".vidio.com", path="/")
    session.cookies.set("country_id", "ID", domain=".vidio.com", path="/")
    session.cookies.set("shva", "1", domain=".vidio.com", path="/")

    try:
        resp = session.get(SITE_URL, timeout=30)
        log.info("  GET m.vidio.com → %d", resp.status_code)
    except Exception as e:
        log.warning("  Error visit site: %s", e)

    at = session.cookies.get("access_token")
    if at:
        log.info("  ✓ access_token: %s...", at[:40])
    else:
        log.error("  ✗ access_token kosong!")
        return ""

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


# ==================== STEP 3: FETCH ALL CHANNELS ====================
def fetch_all_channels(session, luws: str) -> list:
    url = f"{API_BASE}/categories/live/sections"
    params = {"page[number]": 1, "page[size]": 10, "included": "contents"}
    h = api_headers(luws)

    log.info("Fetching all channels via /categories/live/sections ...")
    channels = {}
    page = 1

    while True:
        params["page[number]"] = page
        try:
            resp = session.get(url, params=params, headers=h, timeout=30)
            if resp.status_code != 200:
                log.warning("  Page %d: HTTP %d", page, resp.status_code)
                log.warning("  Body: %s", resp.text[:500])
                break
        except Exception as e:
            log.error("  Page %d error: %s", page, e)
            break

        data = resp.json()
        sections = data.get("data", [])
        if isinstance(sections, dict):
            sections = [sections]
        if not isinstance(sections, list):
            break

        # ★ Build index dari included (JSON:API format)
        included = data.get("included", [])
        included_map = {}
        for item in included:
            if isinstance(item, dict):
                item_id = item.get("id")
                if item_id:
                    included_map[str(item_id)] = item

        for section in sections:
            if not isinstance(section, dict):
                continue

            attrs = section.get("attributes", {})
            section_title = attrs.get("title", "Lainnya")

            # ★ Cara 1: dari attributes.segments
            segments = attrs.get("segments", [])
            # ★ Cara 2: dari relationships.contents.data
            rel = section.get("relationships", {})
            rel_contents = []
            if isinstance(rel, dict):
                for key in ("contents", "items", "segments"):
                    if key in rel and isinstance(rel[key], dict):
                        rel_contents = rel[key].get("data", [])
                        break

            # Gabungkan semua sumber konten
            all_items = []

            # Dari segments (bisa langsung punya data atau reference)
            for seg in segments:
                if isinstance(seg, dict):
                    # Jika segment langsung punya content_type → pakai langsung
                    if "content_type" in seg:
                        all_items.append(seg)
                    # Jika segment punya "items" atau "contents" di dalamnya
                    elif "items" in seg and isinstance(seg["items"], list):
                        all_items.extend(seg["items"])
                    elif "contents" in seg and isinstance(seg["contents"], list):
                        all_items.extend(seg["contents"])

            # Dari relationships → resolve via included_map
            for ref in rel_contents:
                if isinstance(ref, dict):
                    ref_id = str(ref.get("id", ""))
                    if ref_id in included_map:
                        all_items.append(included_map[ref_id])
                    # Jika ref langsung punya data
                    elif "content_type" in ref:
                        all_items.append(ref)

            # Debug page 1
            if page == 1:
                log.info("  [DEBUG] segments: %d, rel_contents: %d, all_items: %d",
                         len(segments), len(rel_contents), len(all_items))
                if all_items:
                    log.info("  [DEBUG] all_items[0] keys: %s", list(all_items[0].keys())[:10])
                    log.info("  [DEBUG] all_items[0]: %s", json.dumps(all_items[0])[:400])

            # Parse items
            for item in all_items:
                if not isinstance(item, dict):
                    continue

                # Cek content_type di level item
                content_type = item.get("content_type", "")

                # Jika content_type ada di attributes (JSON:API)
                if not content_type and isinstance(item.get("attributes"), dict):
                    content_type = item["attributes"].get("content_type", "")
                    item = {**item, **item.get("attributes", {})}

                if content_type != "livestreaming":
                    continue

                ch_id = item.get("content_id") or item.get("id")
                if not ch_id or str(ch_id) in channels:
                    continue

                channel_name = item.get("alt_title") or item.get("title", "Unknown")
                program_name = item.get("title", "")
                logo = item.get("cover_url") or item.get("image_url", "")
                stream_url = item.get("stream_url", "")

                channels[str(ch_id)] = {
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
def generate_m3u(channels: list, stream_urls: dict, output_file: str, premium_ids: set = None):
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

        tvg_id = f"{ch_id}"
        if premium_ids and ch_id in premium_ids:
            tvg_id = f"{ch_id}_premium"

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
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
    parser = argparse.ArgumentParser(description="Vidio Live TV M3U Generator")
    parser.add_argument("-o", "--output", type=str, default=OUTPUT_FILE)
    parser.add_argument("--drm-only", action="store_true")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 55)
    print("  Vidio Live TV → M3U (Scrape All Channels)")
    print("=" * 55)

    session = cffi_requests.Session(impersonate=IMPERSONATE)

    luws = bootstrap_session(session)
    if not luws:
        sys.exit(1)

    if not verify_login(session):
        log.error("Login gagal!")
        sys.exit(1)

    channels = fetch_all_channels(session, luws)
    if not channels:
        log.error("Tidak ada channel. Lihat [DEBUG] di atas.")
        sys.exit(1)

    groups = {}
    for ch in channels:
        groups.setdefault(ch["group"], []).append(ch["title"])
    log.info("\n  Groups:")
    for g, names in groups.items():
        log.info("    [%s] %d: %s", g, len(names), ", ".join(names[:5]) + ("..." if len(names) > 5 else ""))

    stream_urls = {}
    premium_ids = set()

    if not args.drm_only:
        need_fetch = [ch for ch in channels if not ch.get("stream_url")]
        log.info("\nFetching non-DRM URLs for %d channels ...", len(need_fetch))

        failed = []
        for i, ch in enumerate(need_fetch, 1):
            hls = fetch_stream_url(session, luws, ch["id"])
            if hls:
                stream_urls[ch["id"]] = hls
                log.info("  [%d/%d] %s ✓", i, len(need_fetch), ch["title"])
            else:
                failed.append(ch)
                premium_ids.add(ch["id"])
                log.warning("  [%d/%d] %s ✗ (premium)", i, len(need_fetch), ch["title"])
            time.sleep(DELAY)

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
                    premium_ids.discard(ch["id"])
                    log.info("  [R%d] %s ✓", attempt, ch["title"])
                else:
                    still.append(ch)
                time.sleep(DELAY)
            failed = still

        log.info("\n  Non-DRM: %d/%d | Premium: %d", len(stream_urls), len(need_fetch), len(premium_ids))

    log.info("\nGenerating M3U ...")
    generate_m3u(channels, stream_urls, args.output, premium_ids)


if __name__ == "__main__":
    main()   
