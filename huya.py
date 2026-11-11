import requests
import time
import os

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://www.dens.tv/api/dens3/"
OUTPUT_FILE = "playlist.m3u"
DELAY_BETWEEN_REQUESTS = 0.5  # detik, anti rate-limit

# ============================================================
# SESSION & AUTH
# ============================================================
session = requests.Session()

session.cookies.update({
    'dtv_atl': '14400',
    'dtv_adt': '1794511834',
    'dens_session': 'at93n8eacjl18m5o74aobqbv5mlgvi55',
    'tvCategories': 'W3siaWQiOiIxIiwidGl0bGUiOiJMb2NhbCIsInNsdWciOiJsb2NhbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjIiLCJ0aXRsZSI6IlByZW1pdW0iLCJzbHVnIjoicHJlbWl1bSIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjMiLCJ0aXRsZSI6IkludGVybmF0aW9uYWwiLCJzbHVnIjoiaW50ZXJuYXRpb25hbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjEzIiwidGl0bGUiOiJGcmVlIFN0cmVhbWluZyIsInNsdWciOiJmcmVlLXN0cmVhbWluZyIsInR5cGUiOiJMaXZlIFRWIn1d',
    'dtv_hrls': '713a42fe9b1e51d3f06039055cb60023',
    'dtv_rls': 'eyJyZXBlYXQiOnRydWUsImZyZWVfZGF5cyI6MywiZnJlZV9ob3VycyI6NCwiY2F0Y2h1cF9hY2Nlc3MiOnRydWV9',
    'dtv_msg': 'eyJzaWduX2luIjoiWW91ciBGcmVlIEV4cGVyaWVuY2UgRXhwaXJlZCAtIFBsZWFzZSBTaWduSW4iLCJzdWJzY3JpcHRpb24iOiJZb3VyIEZyZWUgRXhwZXJpZW5jZSBFeHBpcmVkIC0gUGxlYXNlIFN1YmNyaWJlIiwiZXhwaXJlZF9jYXRjaHVwIjoiQ2F0Y2h1cCBFeHBpcmVkIn0%3D',
    'dtv_grf': 'guest_7afc09895ef25f8e',
    'dtv_axp': '2026-11-15T03%3A45%3A23%2B07%3A00',
    'dtv_alm': '14400',
    'last_visit': 'https%3A%2F%2Fwww.dens.tv%2F',
})

session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36',
    'Accept': '*/*',
    'Referer': 'https://www.dens.tv/',
    'Accept-Language': 'id-ID,id;q=0.7',
})


# ============================================================
# API CALLS
# ============================================================
def fetch_home_sections(page=1, limit=10):
    """Fetch HomeSections API"""
    url = f"{BASE_URL}HomeSections?page={page}&limit={limit}&app_type=10"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_channel_stream(channel_id):
    """
    Fetch stream URL untuk channel live.
    Sesuaikan endpoint jika perlu (coba beberapa kemungkinan).
    """
    # Coba endpoint channel detail
    endpoints = [
        f"Channel/detail?id={channel_id}&app_type=10",
        f"Channel/getDetail?id={channel_id}&app_type=10",
        f"channel/detail/{channel_id}",
    ]

    for ep in endpoints:
        try:
            url = BASE_URL + ep
            resp = session.get(url)
            if resp.status_code == 200:
                data = resp.json()
                # Cari stream URL di response
                if data.get("success"):
                    d = data.get("data", {})
                    # Cek beberapa kemungkinan field
                    for key in ["playurl", "play_url", "stream_url", "stream_url_web", "url"]:
                        if key in d:
                            return d[key]
                    # Cek nested
                    if "channel" in d:
                        for key in ["playurl", "play_url", "stream_url", "stream_url_web"]:
                            if key in d["channel"]:
                                return d["channel"][key]
        except Exception:
            continue
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return None


def fetch_all_channels():
    """Fetch daftar semua channel + stream URL"""
    url = f"{BASE_URL}Channel/list?page=1&limit=100&app_type=10"
    try:
        resp = session.get(url)
        if resp.status_code == 200:
            data = resp.json()
            return data
    except Exception as e:
        print(f"[WARN] Gagal fetch channel list: {e}")
    return None


# ============================================================
# M3U GENERATOR
# ============================================================
def escape_m3u(value):
    """Escape karakter yang tidak valid di M3U attribute"""
    if not value:
        return ""
    return value.replace('"', "'").replace("\n", " ").strip()


def build_m3u(entries):
    """
    entries: list of dict {
        'group_title': str,
        'tvg_name': str,
        'tvg_logo': str,
        'stream_url': str,
    }
    """
    lines = ["#EXTM3U"]

    for e in entries:
        group = escape_m3u(e.get("group_title", ""))
        name = escape_m3u(e.get("tvg_name", ""))
        logo = escape_m3u(e.get("tvg_logo", ""))
        url = e.get("stream_url", "")

        if not url:
            continue

        # #EXTINF:-1 group-title="..." tvg-name="..." tvg-logo="...",Display Name
        extinf = f'#EXTINF:-1 group-title="{group}" tvg-name="{name}" tvg-logo="{logo}",{name}'
        lines.append(extinf)
        lines.append(url)

    return "\n".join(lines) + "\n"


# ============================================================
# MAIN SCRAPE LOGIC
# ============================================================
def scrape():
    print("[*] Fetching HomeSections...")
    data = fetch_home_sections()

    if not data.get("success"):
        print("[ERROR] API gagal:", data.get("message"))
        return

    sections = data["data"]["contents"]
    entries = []

    # Cache channel stream URLs (hindari duplicate request)
    channel_stream_cache = {}

    # Pre-fetch channel list untuk stream URL
    print("[*] Fetching channel list untuk stream URL...")
    channel_list_data = fetch_all_channels()
    if channel_list_data and channel_list_data.get("success"):
        channels = channel_list_data.get("data", {}).get("channels", [])
        for ch in channels:
            ch_id = str(ch.get("id", ""))
            # Simpan stream URL jika ada
            for key in ["playurl", "play_url", "stream_url", "stream_url_web"]:
                if key in ch and ch[key]:
                    channel_stream_cache[ch_id] = ch[key]
                    break
        print(f"    ✓ {len(channel_stream_cache)} channel stream URL ditemukan")

    # Proses setiap section
    for section in sections:
        section_title = section.get("title", "Unknown")
        section_type = section.get("name_type", "")
        contents = section.get("content", [])

        print(f"[*] Section: {section_title} ({len(contents)} items)")

        for item in contents:
            item_type = item.get("type", "")
            title = item.get("title", "")
            logo = item.get("image_square", "") or item.get("image_landscape", "")
            group_title = section_title

            stream_url = None

            if item_type == "live_tv":
                # Live channel - perlu fetch stream URL
                ch_id = str(item.get("id", ""))
                if ch_id in channel_stream_cache:
                    stream_url = channel_stream_cache[ch_id]
                else:
                    # Coba fetch per channel
                    print(f"    [~] Fetching stream untuk: {title}")
                    stream_url = fetch_channel_stream(ch_id)
                    if stream_url:
                        channel_stream_cache[ch_id] = stream_url
                    else:
                        print(f"    [!] Stream URL tidak ditemukan untuk: {title}")

            elif item_type == "catchup_schedule":
                stream_url = item.get("stream_url_web", "")

            elif item_type == "vod":
                stream_url = item.get("vod_url_web", "")

            else:
                continue  # skip banner_ads, dll.

            if stream_url:
                entries.append({
                    "group_title": group_title,
                    "tvg_name": title,
                    "tvg_logo": logo,
                    "stream_url": stream_url,
                })

    # Generate M3U
    m3u_content = build_m3u(entries)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n[✓] Selesai! {len(entries)} entri ditulis ke '{OUTPUT_FILE}'")
    print(f"    - Live TV: {sum(1 for s in sections for i in s.get('content',[]) if i.get('type')=='live_tv')}")
    print(f"    - Catchup: {sum(1 for s in sections for i in s.get('content',[]) if i.get('type')=='catchup_schedule')}")
    print(f"    - VOD:     {sum(1 for s in sections for i in s.get('content',[]) if i.get('type')=='vod')}")


if __name__ == "__main__":
    scrape()   
