#!/usr/bin/env python3
"""
Vidio Live TV M3U Playlist Generator
Scrape m.vidio.com/live untuk membuat playlist.m3u
dengan tvg-logo, group-title, dan title.
"""

import requests
import re
import json
import time
import sys

# ==================== KONFIGURASI ====================
API_KEY = "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
BASE_API = "https://api.vidio.com"
LIVE_PAGE = "https://m.vidio.com/live"
OUTPUT_FILE = "playlist.m3u"
DELAY_BETWEEN_REQUESTS = 0.5  # detik, agar tidak terlalu agresif

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.6",
    "Referer": "https://m.vidio.com/",
    "Origin": "https://m.vidio.com",
}

API_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "x-api-key": API_KEY,
    "x-api-platform": "web-mobile",
    "x-secure-level": "2",
    "Accept": "*/*",
    "Referer": "https://m.vidio.com/",
    "Origin": "https://m.vidio.com",
}
# =====================================================


def fetch_live_page():
    """Fetch halaman /live dan parse RSC response untuk mendapatkan data livestreaming."""
    headers = {
        **HEADERS,
        "RSC": "1",
        "Next-Router-State-Tree": '["",{"children":["__PAGE__",{},null,null,0]},null,null,16]',
    }
    print("[*] Fetching https://m.vidio.com/live ...")
    resp = requests.get(LIVE_PAGE, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_rsc_livestreamings(rsc_text):
    """
    Parse RSC text untuk menemukan semua item dengan content_type = 'livestreaming_schedule'.
    Mengembalikan list dict dengan: title, cover_url, livestreaming_title, livestreaming_id
    """
    channels = {}  # deduplicate by livestreaming_id

    # Cari semua JSON object yang mengandung "livestreaming_schedule"
    # RSC format: baris-baris text, beberapa berisi JSON
    # Kita cari pattern JSON object yang memiliki field yang kita butuhkan

    # Approach: cari semua substring yang mirip JSON object dengan "livestreaming_title"
    pattern = re.compile(
        r'\{[^{}]*"content_type"\s*:\s*"livestreaming_schedule"[^{}]*\}'
    )

    # Karena JSON bisa nested, kita pakai approach berbeda:
    # Cari semua "livestreaming_id":\d+ dan ambil context di sekitarnya

    # Method yang lebih robust: cari semua blok yang mengandung "livestreaming_schedule"
    # dan extract field-field yang dibutuhkan

    # Split by common RSC delimiters and look for JSON-like structures
    # Actually, the RSC response has JSON objects embedded. Let's find them.

    # Simpler approach: find all occurrences of the pattern
    # {"type":"com.vidio.kmm.fluidsection.content.Landscape","id":"...","content_id":...,"content_type":"livestreaming_schedule","title":"...","cover_url":"...","stream_url":"...","livestreaming_title":"...","links":{"self":{"href":"...","meta":{"livestreaming_id":...,"schedule_id":...}}}}

    # Use a more flexible regex
    entries = []
    for match in re.finditer(
        r'"content_type"\s*:\s*"livestreaming_schedule"',
        rsc_text
    ):
        # Get a window around this match to extract the full JSON object
        start = rsc_text.rfind('{', 0, match.start())
        # Find the matching closing brace
        depth = 0
        end = start
        for i in range(start, len(rsc_text)):
            if rsc_text[i] == '{':
                depth += 1
            elif rsc_text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        json_str = rsc_text[start:end]
        try:
            obj = json.loads(json_str)
            title = obj.get("title", "Unknown")
            cover_url = obj.get("cover_url", "")
            ls_title = obj.get("livestreaming_title", "Unknown")
            links = obj.get("links", {})
            self_link = links.get("self", {})
            meta = self_link.get("meta", {})
            ls_id = meta.get("livestreaming_id")
            schedule_id = meta.get("schedule_id")

            if ls_id and ls_id not in channels:
                channels[ls_id] = {
                    "livestreaming_id": ls_id,
                    "schedule_id": schedule_id,
                    "title": title,
                    "cover_url": cover_url,
                    "livestreaming_title": ls_title,
                }
        except (json.JSONDecodeError, AttributeError):
            continue

    return list(channels.values())


def get_stream_url(livestreaming_id):
    """Fetch HLS stream URL dari API Vidio."""
    url = f"{BASE_API}/livestreamings/{livestreaming_id}/stream?initialize=true"
    try:
        resp = requests.get(url, headers=API_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        hls_url = data.get("data", {}).get("attributes", {}).get("hls")
        return hls_url
    except Exception as e:
        print(f"    [!] Error fetching stream for ID {livestreaming_id}: {e}")
        return None


def generate_m3u(channels, stream_urls, output_file):
    """Generate file M3U playlist."""
    lines = ["#EXTM3U"]

    for ch in channels:
        ls_id = ch["livestreaming_id"]
        title = ch["title"]
        group = ch["livestreaming_title"]
        logo = ch["cover_url"]
        stream_url = stream_urls.get(ls_id, "")

        if not stream_url:
            # Jika tidak ada stream URL, skip atau gunakan placeholder
            print(f"    [!] No stream URL for {group} - {title}, skipping")
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{ls_id}" '
            f'tvg-name="{group}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}",'
            f'{group} | {title}'
        )
        lines.append(stream_url)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n[✓] Playlist berhasil disimpan ke: {output_file}")
    print(f"    Total channel: {len(stream_urls)}")


def main():
    print("=" * 50)
    print("  Vidio Live TV → M3U Playlist Generator")
    print("=" * 50)

    # Step 1: Fetch data live page
    rsc_text = fetch_live_page()
    print(f"    Response size: {len(rsc_text)} bytes")

    # Step 2: Parse data livestreaming
    channels = parse_rsc_livestreamings(rsc_text)
    print(f"\n[*] Found {len(channels)} unique channels:")
    for ch in channels:
        print(f"    - [{ch['livestreaming_id']}] {ch['livestreaming_title']} | {ch['title']}")

    if not channels:
        print("\n[!] Tidak ada channel ditemukan. Coba cek headers/cookies.")
        sys.exit(1)

    # Step 3: Fetch stream URL untuk setiap channel
    print(f"\n[*] Fetching stream URLs ({len(channels)} channels)...")
    stream_urls = {}
    for i, ch in enumerate(channels, 1):
        ls_id = ch["livestreaming_id"]
        print(f"    [{i}/{len(channels)}] {ch['livestreaming_title']} (ID: {ls_id}) ...", end=" ")
        hls_url = get_stream_url(ls_id)
        if hls_url:
            stream_urls[ls_id] = hls_url
            print("OK")
        else:
            print("FAILED")
        time.sleep(DELAY_BETWEEN_REQUESTS)

    # Step 4: Generate M3U
    print()
    generate_m3u(channels, stream_urls, OUTPUT_FILE)


if __name__ == "__main__":
    main()   
