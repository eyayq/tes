#!/usr/bin/env python3
"""
Huya M3U Playlist Generator — FULL AUTO (API-based, no HTML scraping)

Alur:
  1. GET semua kategori dari getLiveGameList
  2. GET rooms per kategori dari getLiveListByPage
  3. GET stream URL per room dari mp.huya.com/cache.php
  4. tvg-logo dari field 'screenshot' di listing API
  5. group-title dari field 'gameName' di listing API
  6. Output: playlist.m3u
"""

import json
import time
import argparse
import requests

sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.huya.com/",
})

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_ROOMS_PER_CATEGORY = 15    # batasi room per kategori (hemat waktu)
REQUEST_DELAY = 0.1           # jeda antar request (detik)
TIMEOUT = 15


# ─── Step 1: Ambil SEMUA kategori ────────────────────────────────
def get_all_categories():
    """
    Ambil semua game category dari Huya API.
    Endpoint: /cache.php?m=LiveList&do=getLiveGameList
    Response: {"data": [{"gameId": 2135, "gameName": "LOL", ...}, ...]}
    """
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    try:
        r = sess.get(api, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [WARN] getLiveGameList gagal: {e}")
        # Fallback: scrape dari halaman utama
        return _fallback_categories()

    categories = []
    for item in data.get('data', []):
        gid = item.get('gameId') or item.get('id')
        gname = item.get('gameName') or item.get('name', '')
        if gid and gname:
            categories.append({'id': int(gid), 'name': gname})

    if not categories:
        categories = _fallback_categories()

    return categories


def _fallback_categories():
    """Fallback jika API utama gagal — scrape dari halaman utama."""
    try:
        r = sess.get("https://www.huya.com/", timeout=TIMEOUT)
        html = r.text
        seen = set()
        categories = []
        for m in __import__('re').finditer(r'/g/(\d+)"[^>]*>\s*([^<]+)', html):
            gid = int(m.group(1))
            gname = m.group(2).strip()
            if gid not in seen and gname:
                seen.add(gid)
                categories.append({'id': gid, 'name': gname})
        if categories:
            return categories
    except Exception:
        pass
    # Hardcoded minimal
    return [
        {'id': 2135, 'name': 'LOL'},
        {'id': 1052, 'name': 'CS2'},
        {'id': 1059, 'name': 'Dota2'},
        {'id': 1141, 'name': 'Valorant'},
        {'id': 1058, 'name': 'PUBG'},
        {'id': 1056, 'name': 'OW2'},
        {'id': 1120, 'name': 'Fortnite'},
        {'id': 1053, 'name': 'Apex'},
        {'id': 2067, 'name': 'Sports'},
    ]


# ─── Step 2: Ambil rooms per kategori ────────────────────────────
def get_rooms(category_id, max_rooms=MAX_ROOMS_PER_CATEGORY):
    """
    Ambil daftar room live dari API per kategori.
    Endpoint: /cache.php?m=LiveList&do=getLiveListByPage&gameId={id}&page={n}
    Response fields per room:
      - profileRoom: room ID
      - nick: nama streamer
      - introduction: judul stream
      - screenshot: URL gambar (dipakai sebagai tvg-logo)
      - gameName: nama game (group-title)
      - totalCount: jumlah penonton
    """
    rooms = []
    page = 1

    while len(rooms) < max_rooms:
        api = (
            f"https://www.huya.com/cache.php"
            f"?m=LiveList&do=getLiveListByPage"
            f"&gameId={category_id}&tagAll=0&page={page}"
        )
        try:
            r = sess.get(api, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception:
            break

        datas = data.get('data', {}).get('datas', [])
        if not datas:
            break

        for item in datas:
            room_id = item.get('profileRoom', '')
            if not room_id:
                continue
            rooms.append({
                'id': str(room_id),
                'nick': item.get('nick', ''),
                'introduction': item.get('introduction', ''),
                'screenshot': item.get('screenshot', ''),  # ← tvg-logo
                'gameName': item.get('gameName', ''),       # ← group-title
                'viewers': item.get('totalCount', 0),
            })
            if len(rooms) >= max_rooms:
                break

        page += 1
        time.sleep(REQUEST_DELAY)

    return rooms


# ─── Step 3: Ambil stream URL per room (API JSON) ───────────────
def get_stream_url(room_id):
    """
    Ambil stream URL dari API terstruktur Huya.
    Endpoint: mp.huya.com/cache.php?m=Live&do=profileRoom&roomid={rid}

    Response JSON structure:
      data.stream.baseSteamInfoList[0].sStreamName
      data.stream.baseSteamInfoList[0].sHlsAntiCode
      data.stream.hls.multiLine[0].cdnType
      data.stream.flv.multiLine[n].url
      data.profileInfo.uid
      data.profileInfo.sAvatar
    """
    api = f"https://mp.huya.com/cache.php?m=Live&do=profileRoom&roomid={room_id}"
    try:
        r = sess.get(api, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    d = data.get('data')
    if not d or isinstance(d, list):
        return None  # offline / room tidak ada

    stream = d.get('stream')
    if not stream:
        return None

    # Cek apakah live
    live_status = d.get('liveStatus', '')
    if live_status and live_status != 'on':
        return None

    # --- Metode 1: FLV URL langsung dari API ---
    flv_list = stream.get('flv', {}).get('multiLine', [])
    if flv_list:
        # Pilih yang bitrate tertinggi (biasanya index terakhir)
        for line in reversed(flv_list):
            url = line.get('url', '')
            if url:
                return url.replace('http://', 'https://')

    # --- Metode 2: Build dari baseSteamInfoList + hls ---
    base_list = stream.get('baseSteamInfoList', [])
    if base_list:
        base = base_list[0]
        stream_name = base.get('sStreamName', '')
        anti_code = base.get('sHlsAntiCode', '') or base.get('sFlvAntiCode', '')

        if stream_name and anti_code:
            # Tentukan CDN
            hls_lines = stream.get('hls', {}).get('multiLine', [])
            cdn_type = hls_lines[0].get('cdnType', 'huyalive') if hls_lines else 'huyalive'
            cdn_type = cdn_type.lower()

            # Build URL
            url = f"https://{cdn_type}.hls.huya.com/src/{stream_name}.m3u8?{anti_code}"
            return url

    # --- Metode 3: sFlvUrl dari gameStreamInfoList (struktur alternatif) ---
    game_list = stream.get('gameStreamInfoList', [])
    if game_list:
        for s in game_list:
            flv_url = s.get('sFlvUrl', '')
            s_name = s.get('sStreamName', '')
            suffix = s.get('sFlvUrlSuffix', 'flv')
            anti = s.get('sFlvAntiCode', '')
            if flv_url and s_name:
                return f"{flv_url}/{s_name}.{suffix}?{anti}".replace('http://', 'https://')

    return None


# ─── Step 4: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_per_cat=MAX_ROOMS_PER_CATEGORY,
                      categories_filter=None):
    print("=" * 55)
    print("  HUYA M3U PLAYLIST GENERATOR (FULL AUTO — API-based)")
    print("=" * 55)

    # Step 1: Ambil semua kategori
    print("\n[1/3] Mengambil semua kategori...")
    categories = get_all_categories()
    print(f"  Total kategori: {len(categories)}")

    if categories_filter:
        categories = [c for c in categories if c['id'] in categories_filter]
        print(f"  (Filter: {len(categories)} kategori)")

    # Step 2: Ambil rooms per kategori
    print(f"\n[2/3] Mengambil rooms (max {max_per_cat}/kategori)...")
    all_rooms = []  # list of (room_dict, category_name)

    for i, cat in enumerate(categories):
        rooms = get_rooms(cat['id'], max_per_cat)
        for room in rooms:
            all_rooms.append((room, cat['name']))
        if (i + 1) % 20 == 0 or i == len(categories) - 1:
            print(f"  Progress: {i+1}/{len(categories)} kategori | "
                  f"total rooms: {len(all_rooms)}")
        time.sleep(REQUEST_DELAY)

    print(f"\n  Total rooms: {len(all_rooms)}")

    # Step 3: Ambil stream URL per room
    print(f"\n[3/3] Mengambil stream URL...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0

    for i, (room, cat_name) in enumerate(all_rooms):
        room_id = room['id']
        label = room['nick'] or room['introduction'] or room_id

        stream_url = get_stream_url(room_id)
        if not stream_url:
            offline_count += 1
            continue

        # Nama channel: nick atau introduction
        name = room['nick'] or room['introduction'] or f"Huya {room_id}"

        # tvg-logo: screenshot dari listing API
        logo = room.get('screenshot', '')

        # group-title: gameName dari listing API, fallback ke kategori
        group_title = room.get('gameName', '') or cat_name

        # Build EXTINF
        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{group_title}"'
        extinf += f',{name}'

        lines.append(extinf)
        lines.append(stream_url)
        live_count += 1

        # Progress setiap 20 room
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(all_rooms)}] live: {live_count} | offline: {offline_count}")

        time.sleep(REQUEST_DELAY)

    # Tulis output
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline")
    print(f"  Output: {output_file}")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Huya M3U Playlist Generator (Full Auto — API-based)')
    parser.add_argument('--output', '-o', default='playlist.m3u',
                        help='File output M3U (default: playlist.m3u)')
    parser.add_argument('--max-per-cat', '-m', type=int,
                        default=MAX_ROOMS_PER_CATEGORY,
                        help='Maks room per kategori (default: 5)')
    parser.add_argument('--categories', '-c', nargs='*', type=int,
                        help='Filter kategori ID (contoh: -c 2135 1052)')
    parser.add_argument('--delay', '-d', type=float, default=REQUEST_DELAY,
                        help='Jeda antar request dalam detik (default: 0.3)')
    args = parser.parse_args()

    REQUEST_DELAY = args.delay
    generate_playlist(
        output_file=args.output,
        max_per_cat=args.max_per_cat,
        categories_filter=set(args.categories) if args.categories else None,
    )   
