import requests
import time
import json

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://www.dens.tv/api/dens3/"
OUTPUT_FILE = "playlist.m3u"
DELAY = 0.5

# HTTP Headers untuk streaming
STREAM_UA = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36'
STREAM_REFERER = 'https://www.dens.tv/'

# Kategori channel
CATEGORIES = {
    "1": "Local",
    "2": "Premium",
    "3": "International",
    "13": "Free Streaming",
}

# ============================================================
# SESSION
# ============================================================
session = requests.Session()

session.cookies.update({
    'dtv_atl': '14400',
    'dtv_adt': '1794511834',
    'dens_session': 'akknvh81aimpc671rhl656i7uj8c0al0',
    'tvCategories': 'W3siaWQiOiIxIiwidGl0bGUiOiJMb2NhbCIsInNsdWciOiJsb2NhbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjIiLCJ0aXRsZSI6IlByZW1pdW0iLCJzbHVnIjoicHJlbWl1bSIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjMiLCJ0aXRsZSI6IkludGVybmF0aW9uYWwiLCJzbHVnIjoiaW50ZXJuYXRpb25hbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjEzIiwidGl0bGUiOiJGcmVlIFN0cmVhbWluZyIsInNsdWciOiJmcmVlLXN0cmVhbWluZyIsInR5cGUiOiJMaXZlIFRWIn1d',
    'dtv_hrls': '713a42fe9b1e51d3f06039055cb60023',
    'dtv_rls': 'eyJyZXBlYXQiOnRydWUsImZyZWVfZGF5cyI6MywiZnJlZV9ob3VycyI6NCwiY2F0Y2h1cF9hY2Nlc3MiOnRydWV9',
    'dtv_msg': 'eyJzaWduX2luIjoiWW91ciBGcmVlIEV4cGVyaWVuY2UgRXhwaXJlZCAtIFBsZWFzZSBTaWduSW4iLCJzdWJzY3JpcHRpb24iOiJZb3VyIEZyZWUgRXhwZXJpZW5jZSBFeHBpcmVkIC0gUGxlYXNlIFN1YmNyaWJlIiwiZXhwaXJlZF9jYXRjaHVwIjoiQ2F0Y2h1cCBFeHBpcmVkIn0%3D',
    'dtv_grf': 'guest_655132461d4c2bd4',
    'dtv_axp': '2026-11-15T04%3A00%3A07%2B07%3A00',
    'dtv_alm': '14400',
    'last_visit': 'https%3A%2F%2Fwww.dens.tv%2F',
})

session.headers.update({
    'User-Agent': STREAM_UA,
    'Accept': '*/*',
    'Referer': 'https://www.dens.tv/',
    'Accept-Language': 'id-ID,id;q=0.7',
})


# ============================================================
# API CALLS
# ============================================================
def fetch_channels_by_category(category_id):
    """Fetch channel list per kategori"""
    url = f"{BASE_URL}tv/TvChannels/listByCategory?id_category={category_id}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_home_sections(page=1, limit=10):
    """Fetch HomeSections (untuk catchup & VOD)"""
    url = f"{BASE_URL}HomeSections?page={page}&limit={limit}&app_type=10"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# M3U BUILDER
# ============================================================
def esc(val):
    """Escape untuk M3U attribute"""
    if not val:
        return ""
    return str(val).replace('"', "'").replace("\n", " ").strip()


def build_m3u(entries):
    lines = ["#EXTM3U"]
    for e in entries:
        if not e.get("stream_url"):
            continue
        group = esc(e.get("group_title", ""))
        name = esc(e.get("tvg_name", ""))
        logo = esc(e.get("tvg_logo", ""))
        stream_url = e["stream_url"]

        lines.append(f'#EXTINF:-1 group-title="{group}" tvg-name="{name}" tvg-logo="{logo}",{name}')
        lines.append(f'#EXTVLCOPT:http-user-agent={STREAM_UA}')
        lines.append(f'#EXTVLCOPT:http-referrer={STREAM_REFERER}')
        lines.append(stream_url)
    return "\n".join(lines) + "\n"


# ============================================================
# MAIN
# ============================================================
def scrape():
    entries = []

    # ----------------------------------------------------------
    # 1. LIVE TV — dari TvChannels/listByCategory
    # ----------------------------------------------------------
    print("[*] Fetching Live TV channels...")
    for cat_id, cat_name in CATEGORIES.items():
        try:
            data = fetch_channels_by_category(cat_id)
            if data.get("success"):
                contents = data["data"]["contents"]
                count = 0
                for item in contents:
                    meta = item.get("meta", {})
                    rules = item.get("rules", {})

                    title = meta.get("title", "")
                    logo = meta.get("images", {}).get("default", "")
                    stream_url = meta.get("videos", {}).get("live", "")

                    if stream_url:
                        entries.append({
                            "group_title": cat_name,
                            "tvg_name": title,
                            "tvg_logo": logo,
                            "stream_url": stream_url,
                        })
                        count += 1

                print(f"    ✓ {cat_name}: {count} channels")
            time.sleep(DELAY)
        except Exception as ex:
            print(f"    [!] {cat_name}: {ex}")

    # ----------------------------------------------------------
    # 2. CATCHUP & VOD — dari HomeSections
    # ----------------------------------------------------------
    print("[*] Fetching Catchup & VOD...")
    try:
        data = fetch_home_sections()
        if data.get("success"):
            sections = data["data"]["contents"]
            for section in sections:
                section_title = section.get("title", "")
                contents = section.get("content", [])

                for item in contents:
                    item_type = item.get("type", "")
                    title = item.get("title", "")
                    logo = item.get("image_landscape", "") or item.get("image_square", "")

                    stream_url = None

                    if item_type == "catchup_schedule":
                        stream_url = item.get("stream_url_web", "")
                    elif item_type == "vod":
                        stream_url = item.get("vod_url_web", "")
                    else:
                        continue

                    if stream_url:
                        entries.append({
                            "group_title": section_title,
                            "tvg_name": title,
                            "tvg_logo": logo,
                            "stream_url": stream_url,
                        })

            print(f"    ✓ Done")
    except Exception as ex:
        print(f"    [!] HomeSections: {ex}")

    # ----------------------------------------------------------
    # 3. WRITE FILE
    # ----------------------------------------------------------
    m3u = build_m3u(entries)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u)

    live_count = sum(1 for e in entries if "op-flashcon-digdayahd" in e.get("stream_url", "") or "op-flashcon-digdayasd" in e.get("stream_url", ""))
    other_count = len(entries) - live_count
    print(f"\n[✓] Selesai! Total: {len(entries)} entri → '{OUTPUT_FILE}'")
    print(f"    - Live TV: {live_count}")
    print(f"    - Catchup/VOD: {other_count}")


if __name__ == "__main__":
    scrape()   
