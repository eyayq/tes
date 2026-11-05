#!/usr/bin/env python3
"""
Huya M3U Playlist Generator — FULL AUTO SCRAPE
1. Scrape semua kategori dari halaman utama
2. Scrape semua room per kategori (via API internal)
3. Scrape stream URL + tvg-logo + group-title dari halaman room
4. Output: playlist.m3u
"""

import re
import json
import time
import argparse
import requests
from pathlib import Path

sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.140 Mobile Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.huya.com/",
})

# ─── Konfigurasi ───────────────────────────────────────────────
MAX_ROOMS_PER_CATEGORY = 10   # batasi room per kategori (hemat request)
REQUEST_DELAY = 0.5           # jeda antar request (detik)
TIMEOUT = 15


# ─── Step 1: Scrape kategori dari halaman utama ────────────────
def scrape_categories():
    """Ambil semua game category ID dari halaman utama Huya."""
    url = "https://www.huya.com/"
    r = sess.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text

    categories = []

    # Metode 1: Ekstrak dari embedded JSON (gameList / gameCateList)
    # Huya embeds: var gameList = [{gameId: 2135, gameName: "LOL", ...}, ...]
    m = re.search(r'gameList\s*[:=]\s*(\[.*?\])\s*[,;]', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            for item in data:
                gid = item.get('gameId') or item.get('id')
                gname = item.get('gameName') or item.get('name', '')
                if gid and gname:
                    categories.append({'id': int(gid), 'name': gname})
        except json.JSONDecodeError:
            pass

    # Metode 2: Fallback — regex link /g/{id}
    if not categories:
        seen = set()
        for m in re.finditer(r'/g/(\d+)"[^>]*>\s*([^<]+)', html):
            gid = int(m.group(1))
            gname = m.group(2).strip()
            if gid not in seen and gname:
                seen.add(gid)
                categories.append({'id': gid, 'name': gname})

    # Metode 3: Fallback — cache.php API untuk daftar game
    if not categories:
        try:
            api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
            r = sess.get(api, timeout=TIMEOUT)
            data = r.json()
            for item in data.get('data', []):
                gid = item.get('gameId') or item.get('id')
                gname = item.get('gameName') or item.get('name', '')
                if gid and gname:
                    categories.append({'id': int(gid), 'name': gname})
        except Exception:
            pass

    if not categories:
        # Hardcoded fallback (kategori populer)
        categories = [
            {'id': 2135, 'name': 'LOL'},
            {'id': 1052, 'name': 'CS2'},
            {'id': 1059, 'name': 'Dota2'},
            {'id': 1141, 'name': 'Valorant'},
            {'id': 1058, 'name': 'PUBG'},
            {'id': 1145, 'name': 'Apex'},
            {'id': 1056, 'name': 'OW2'},
            {'id': 1120, 'name': 'Fortnite'},
        ]

    print(f"Kategori ditemukan: {len(categories)}")
    return categories


# ─── Step 2: Scrape rooms per kategori (API internal) ──────────
def scrape_rooms(category_id, max_rooms=MAX_ROOMS_PER_CATEGORY):
    """Ambil daftar room live dari API Huya per kategori."""
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
        except Exception as e:
            print(f"    [WARN] API page {page}: {e}")
            break

        datas = data.get('data', {}).get('datas', [])
        if not datas:
            break

        for item in datas:
            room = {
                'id': str(item.get('profileRoom', '')),
                'nick': item.get('nick', ''),
                'introduction': item.get('introduction', ''),
                'screenshot': item.get('screenshot', ''),
            }
            if room['id']:
                rooms.append(room)
            if len(rooms) >= max_rooms:
                break

        page += 1
        time.sleep(REQUEST_DELAY)

    return rooms


# ─── Step 3: Scrape stream URL + logo dari halaman room ────────
def get_stream_info(room_id):
    """Ekstrak stream URL, nama, logo dari halaman room Huya."""
    url = f"https://www.huya.com/{room_id}"
    try:
        r = sess.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception:
        return None

    html = r.text

    # Cek apakah live
    if 'ISLIVE' in html:
        m = re.search(r'ISLIVE\s*=\s*([a-z]+)', html)
        if m and m.group(1) == 'false':
            return None

    # --- Ekstrak stream URL (struktur baru: stream.data[0].gameStreamInfoList) ---
    stream_url = None

    # Metode 1: gameStreamInfoList di data[0] (struktur 2025+)
    m = re.search(r'"stream"\s*:\s*\{[^}]*"data"\s*:\s*\[\s*\{', html)
    if m:
        # Ambil JSON block dari stream
        start = m.start()
        # Cari gameStreamInfoList
        m2 = re.search(r'"gameStreamInfoList"\s*:\s*(\[.*?\])\s*[,}]', html[start:], re.DOTALL)
        if m2:
            try:
                stream_list = json.loads(m2.group(1))
                for s in stream_list:
                    if s.get('sFlvUrl') and s.get('sStreamName'):
                        stream_url = (
                            f"{s['sFlvUrl']}/{s['sStreamName']}"
                            f".{s.get('sFlvUrlSuffix', 'flv')}"
                            f"?{s.get('sFlvAntiCode', '')}"
                        )
                        break
            except json.JSONDecodeError:
                pass

    # Metode 2: gameStreamInfoList di level atas (struktur lama)
    if not stream_url:
        m = re.search(r'"gameStreamInfoList"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
        if m:
            try:
                stream_list = json.loads(m.group(1))
                for s in stream_list:
                    if s.get('sFlvUrl') and s.get('sStreamName'):
                        stream_url = (
                            f"{s['sFlvUrl']}/{s['sStreamName']}"
                            f".{s.get('sFlvUrlSuffix', 'flv')}"
                            f"?{s.get('sFlvAntiCode', '')}"
                        )
                        break
            except json.JSONDecodeError:
                pass

    # Metode 3: HLS URL (m3u8)
    if not stream_url:
        m = re.search(r'"sHlsUrl"\s*:\s*"(https?://[^"]+)"[^}]*"sStreamName"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"sStreamName"\s*:\s*"([^"]+)"[^}]*"sHlsUrl"\s*:\s*"(https?://[^"]+)"', html)
            if m:
                m = (m.group(2), m.group(1))
        if m:
            hls_url, stream_name = m.group(1), m.group(2)
            m_suffix = re.search(r'"sHlsUrlSuffix"\s*:\s*"([^"]+)"', html)
            suffix = m_suffix.group(1) if m_suffix else 'm3u8'
            stream_url = f"{hls_url}/{stream_name}.{suffix}"

    if not stream_url:
        return None

    stream_url = stream_url.replace('http://', 'https://')

    # --- Ekstrak nama streamer ---
    name = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
    if not name:
        name = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
    name = name.group(1) if name else f"Huya {room_id}"

    # --- Ekstrak logo (tvg-logo) ---
    logo = ''

    # 1. og:image
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', html)
    if m:
        logo = m.group(1)

    # 2. sAvatar / avatar
    if not logo:
        m = re.search(r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
        if m:
            logo = m.group(1)

    # 3. huyaimg avatar pattern
    if not logo:
        m = re.search(r'(https?://huyaimg\.msstatic\.com/avatar/[^"\s\')\\]+)', html)
        if m:
            logo = m.group(1)

    # 4. sScreenshot (dari API listing)
    if not logo:
        m = re.search(r'"screenshot"\s*:\s*"(https?://[^"]+)"', html)
        if m:
            logo = m.group(1)

    # --- Ekstrak game (group-title) ---
    game = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
    if not game:
        game = re.search(r'"game"\s*:\s*"([^"]+)"', html)
    if not game:
        game = re.search(r'"sGameName"\s*:\s*"([^"]+)"', html)
    game = game.group(1) if game else 'Huya'

    return {
        'url': stream_url,
        'name': name,
        'logo': logo,
        'game': game,
    }


# ─── Step 4: Generate playlist ─────────────────────────────────
def generate_playlist(output_file, categories_filter=None, max_per_cat=MAX_ROOMS_PER_CATEGORY):
    print("=" * 50)
    print("HUYA M3U PLAYLIST GENERATOR (FULL AUTO)")
    print("=" * 50)

    # Step 1: Ambil kategori
    print("\n[1/3] Scraping kategori...")
    categories = scrape_categories()

    if categories_filter:
        categories = [c for c in categories if c['id'] in categories_filter]
        print(f"  Filter: {len(categories)} kategori")

    # Step 2: Ambil rooms per kategori
    print(f"\n[2/3] Scraping rooms (max {max_per_cat}/kategori)...")
    all_rooms = []  # list of (room_dict, category_name)

    for cat in categories:
        rooms = scrape_rooms(cat['id'], max_per_cat)
        live_count = 0
        for room in rooms:
            all_rooms.append((room, cat['name']))
        print(f"  {cat['name']:<15} → {len(rooms)} rooms")
        time.sleep(REQUEST_DELAY)

    print(f"\n  Total rooms untuk di-scrape: {len(all_rooms)}")

    # Step 3: Scrape stream info per room
    print(f"\n[3/3] Scraping stream info + logo...")
    lines = ['#EXTM3U']
    live_count = 0

    for i, (room, cat_name) in enumerate(all_rooms):
        room_id = room['id']
        label = room['nick'] or room_id

        try:
            info = get_stream_info(room_id)
        except Exception as e:
            print(f"  [{i+1}/{len(all_rooms)}] {label}: ERROR {e}")
            continue

        if not info:
            print(f"  [{i+1}/{len(all_rooms)}] {label}: OFFLINE")
            continue

        name = info['name']
        logo = info['logo']
        group_title = cat_name or info['game']

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{group_title}"'
        extinf += f',{name}'

        lines.append(extinf)
        lines.append(info['url'])
        live_count += 1
        print(f"  [{i+1}/{len(all_rooms)}] ✓ {name} | {group_title} | logo: {'✓' if logo else '✗'}")

        time.sleep(REQUEST_DELAY)

    # Tulis output
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 50}")
    print(f"SELESAI: {live_count} channel live → {output_file}")
    print(f"{'=' * 50}")


# ─── Main ──────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Huya M3U Playlist Generator (Full Auto)')
    parser.add_argument('--output', '-o', default='playlist.m3u', help='File output M3U')
    parser.add_argument('--categories', '-c', nargs='*', type=int,
                        help='Filter kategori ID (contoh: -c 2135 1052)')
    parser.add_argument('--max-per-cat', '-m', type=int, default=MAX_ROOMS_PER_CATEGORY,
                        help='Maks room per kategori (default: 10)')
    args = parser.parse_args()

    generate_playlist(
        output_file=args.output,
        categories_filter=set(args.categories) if args.categories else None,
        max_per_cat=args.max_per_cat,
    )   
