#!/usr/bin/env python3
"""
HUYA M3U PLAYLIST GENERATOR — FULL AUTO (API-based)
Semua kategori, semua room, tvg-logo, group-title — 100% otomatis.
"""

import re
import json
import base64
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
MAX_ROOMS_PER_CATEGORY = 15
REQUEST_DELAY = 0
TIMEOUT = 15


# ─── Step 1: Ambil SEMUA kategori ────────────────────────────────
def get_all_categories():
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    try:
        r = sess.get(api, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        categories = []
        for item in data.get('data', []):
            gid = item.get('gameId') or item.get('id')
            gname = item.get('gameName') or item.get('name', '')
            if gid and gname:
                categories.append({'id': int(gid), 'name': gname})
        if categories:
            return categories
    except Exception as e:
        print(f"  [WARN] getLiveGameList: {e}")

    # Fallback: scrape dari halaman utama
    try:
        r = sess.get("https://www.huya.com/", timeout=TIMEOUT)
        html = r.text
        seen = set()
        categories = []
        for m in re.finditer(r'/g/(\d+)"[^>]*>\s*([^<]+)', html):
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
                'screenshot': item.get('screenshot', ''),
                'gameName': item.get('gameName', ''),
                'viewers': item.get('totalCount', 0),
            })
            if len(rooms) >= max_rooms:
                break

        page += 1
        time.sleep(REQUEST_DELAY)

    return rooms


# ─── Step 3: Ambil stream URL per room ───────────────────────────
def get_stream_info(room_id):
    """
    Metode utama: API JSON (mp.huya.com)
    Fallback:   HTML scrape (www.huya.com)
    """

    # ═══════════════════════════════════════════════════════════
    # METODE UTAMA: API JSON
    # ═══════════════════════════════════════════════════════════
    api = f"https://mp.huya.com/cache.php?m=Live&do=profileRoom&roomid={room_id}"
    try:
        r = sess.get(api, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        data = None

    if data:
        d = data.get('data')
        if d and not isinstance(d, list):
            stream = d.get('stream')
            if stream:
                live_status = d.get('liveStatus', '')
                if live_status and live_status != 'on':
                    return None

                stream_url = None

                # 1. FLV multiLine
                flv_list = stream.get('flv', {}).get('multiLine', [])
                if flv_list:
                    for line in reversed(flv_list):
                        url = line.get('url', '')
                        if url:
                            stream_url = url.replace('http://', 'https://')
                            break

                # 2. Build dari baseSteamInfoList
                if not stream_url:
                    base_list = stream.get('baseSteamInfoList', [])
                    if base_list:
                        base = base_list[0]
                        sname = base.get('sStreamName', '')
                        anti = base.get('sFlvAntiCode', '') or base.get('sHlsAntiCode', '')
                        if sname and anti:
                            hls_lines = stream.get('hls', {}).get('multiLine', [])
                            cdn = hls_lines[0].get('cdnType', 'huyalive').lower() if hls_lines else 'huyalive'
                            stream_url = f"https://{cdn}.hls.huya.com/src/{sname}.m3u8?{anti}"

                # 3. gameStreamInfoList
                if not stream_url:
                    glist = stream.get('gameStreamInfoList', [])
                    for s in glist:
                        if s.get('sFlvUrl') and s.get('sStreamName'):
                            stream_url = (
                                f"{s['sFlvUrl']}/{s['sStreamName']}"
                                f".{s.get('sFlvUrlSuffix', 'flv')}"
                                f"?{s.get('sFlvAntiCode', '')}"
                            ).replace('http://', 'https://')
                            break

                if stream_url:
                    profile = d.get('profileInfo', {})
                    name = (profile.get('nick', '')
                            or profile.get('sNick', '')
                            or f"Huya {room_id}")
                    logo = profile.get('sAvatar', '') or profile.get('avatar', '')
                    game = d.get('gameName', '') or d.get('game', '')

                    return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}

    # ═══════════════════════════════════════════════════════════
    # FALLBACK: HTML SCRAPE
    # ═══════════════════════════════════════════════════════════
    url = f"https://www.huya.com/{room_id}"
    try:
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception:
        return None

    html = r.text

    # Cek offline
    m = re.search(r'ISLIVE\s*=\s*([a-z]+)', html)
    if m and m.group(1) == 'false':
        return None

    stream_url = None

    # gameStreamInfoList
    m = re.search(r'"gameStreamInfoList"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
    if m:
        try:
            for s in json.loads(m.group(1)):
                if s.get('sFlvUrl') and s.get('sStreamName'):
                    stream_url = (
                        f"{s['sFlvUrl']}/{s['sStreamName']}"
                        f".{s.get('sFlvUrlSuffix', 'flv')}"
                        f"?{s.get('sFlvAntiCode', '')}"
                    ).replace('http://', 'https://')
                    break
        except json.JSONDecodeError:
            pass

    # liveLineUrl (base64)
    if not stream_url:
        m = re.search(r'"?liveLineUrl"?\s*[:=]\s*"([^"]+)"', html)
        if m:
            try:
                decoded = base64.b64decode(m.group(1)).decode('utf-8')
                if 'replay' not in decoded:
                    stream_url = decoded.replace('http://', 'https://')
            except Exception:
                pass

    if not stream_url:
        return None

    # Nama
    m = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
    if not m:
        m = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
    name = m.group(1) if m else f"Huya {room_id}"

    # Logo
    logo = ''
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    if not m:
        m = re.search(r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
    if not m:
        m = re.search(r'(https?://huyaimg\.msstatic\.com/avatar/[^"\s\')\\]+)', html)
    logo = m.group(1) if m else ''

    # Game
    m = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
    if not m:
        m = re.search(r'"sGameName"\s*:\s*"([^"]+)"', html)
    game = m.group(1) if m else 'Huya'

    return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}


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
    all_rooms = []

    for i, cat in enumerate(categories):
        rooms = get_rooms(cat['id'], max_per_cat)
        for room in rooms:
            all_rooms.append((room, cat['name']))
        print(f"  {cat['name']:<20} → {len(rooms)} rooms")
        time.sleep(REQUEST_DELAY)

    print(f"\n  Total rooms: {len(all_rooms)}")

    # Step 3: Ambil stream URL per room
    print(f"\n[3/3] Mengambil stream URL + logo...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for i, (room, cat_name) in enumerate(all_rooms):
        room_id = room['id']
        label = room['nick'] or room['introduction'] or room_id

        try:
            info = get_stream_info(room_id)
        except Exception as e:
            error_count += 1
            print(f"  [{i+1}/{len(all_rooms)}] {label}: ERROR {e}")
            continue

        if not info:
            offline_count += 1
            continue

        name = info['name']
        logo = info.get('logo', '') or room.get('screenshot', '')
        group_title = room.get('gameName', '') or info.get('game', '') or cat_name

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{group_title}"'
        extinf += f',{name}'

        lines.append(extinf)
        lines.append(info['url'])
        live_count += 1

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(all_rooms)}] live: {live_count} | "
                  f"offline: {offline_count} | error: {error_count}")

        time.sleep(REQUEST_DELAY)

    # Tulis output
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline | "
          f"{error_count} error")
    print(f"  Output: {output_file}")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Huya M3U Playlist Generator (Full Auto)')
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
