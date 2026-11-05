#!/usr/bin/env python3
"""
HUYA M3U PLAYLIST GENERATOR — FULL AUTO (FIXED)
Metode stream URL:
  1. m.huya.com + liveLineUrl (base64) — PALING ANDAL
  2. mp.huya.com API JSON — fallback
  3. www.huya.com + stream JSON — fallback terakhir
"""

import re
import json
import base64
import time
import hashlib
import argparse
import requests
from urllib.parse import parse_qsl, unquote, urlencode
from pathlib import Path

sess = requests.Session()
sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/75.0.3770.100 Mobile Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.huya.com/",
})

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_ROOMS_PER_CATEGORY = 10
REQUEST_DELAY = 0.3
TIMEOUT = 15


# ─── Step 1: Ambil SEMUA kategori ────────────────────────────────
def get_all_categories():
    # Metode 1: API
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    try:
        r = sess.get(api, timeout=TIMEOUT)
        if r.headers.get('Content-Type', '').startswith('application/json'):
            data = r.json()
            categories = []
            for item in data.get('data', []):
                gid = item.get('gameId') or item.get('id')
                gname = item.get('gameName') or item.get('name', '')
                if gid and gname:
                    categories.append({'id': int(gid), 'name': gname})
            if categories:
                return categories
    except Exception:
        pass

    # Metode 2: Scrape HTML
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

    # Hardcoded fallback
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
            if not r.headers.get('Content-Type', '').startswith('application/json'):
                break
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


# ─── Step 3: Decode liveLineUrl → stream URL ─────────────────────
def decode_live_url(liveline):
    """
    Decode liveLineUrl (base64) → real stream URL.
    Algoritma:
      1. Parse query params dari liveline
      2. Decode 'fm' (base64) → hash_prefix
      3. Generate wsSecret = md5(hash_prefix + '_' + t + '_' + stream_name + '_' + f + '_' + wsTime)
      4. Build final URL
    """
    i, b = liveline.split('?', 1)
    r = i.split('/')
    s = re.sub(r'\.(flv|m3u8)', '', r[-1])  # stream name

    params = {}
    for kv in b.split('&'):
        if '=' in kv:
            k, v = kv.split('=', 1)
            params[k] = v

    fm = unquote(params.get('fm', ''))
    if not fm:
        return None

    try:
        u = base64.b64decode(fm).decode('utf-8')
    except Exception:
        return None

    hash_prefix = u.split('_')[0]
    wsTime = params.get('wsTime', '')
    f = str(int(time.time() * 1e7))  # timestamp in 100ns
    t = '0'

    # Generate wsSecret
    h = '_'.join([hash_prefix, t, s, f, wsTime])
    wsSecret = hashlib.md5(h.encode('utf-8')).hexdigest()

    # Build URL
    remaining = {k: v for k, v in params.items() if k not in ('fm', 'wsTime')}
    query_parts = [f"wsSecret={wsSecret}", f"wsTime={wsTime}", f"u={t}", f"seqid={f}"]
    for k, v in remaining.items():
        query_parts.append(f"{k}={v}")

    url = f"{i}?{'&'.join(query_parts)}"
    return url


# ─── Step 4: Ambil stream info per room ──────────────────────────
def get_stream_info(room_id):
    """
    3 metode (urutan prioritas):
      1. m.huya.com + liveLineUrl (base64) — PALING ANDAL
      2. mp.huya.com API JSON
      3. www.huya.com + stream JSON
    """

    # ═══════════════════════════════════════════════════════════
    # METODE 1: m.huya.com + liveLineUrl (PALING ANDAL)
    # ═══════════════════════════════════════════════════════════
    try:
        r = sess.get(f"https://m.huya.com/{room_id}", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text

            # Cek offline
            m = re.search(r'ISLIVE\s*=\s*([a-z]+)', html)
            if m and m.group(1) == 'false':
                return None

            # Ekstrak liveLineUrl
            m = re.search(r'"liveLineUrl"\s*:\s*"([^"]+)"', html)
            if not m:
                m = re.search(r'liveLineUrl\s*=\s*"([^"]+)"', html)
            if m:
                liveLineUrl = m.group(1)
                try:
                    liveline = base64.b64decode(liveLineUrl).decode('utf-8')
                except Exception:
                    liveline = ''

                if liveline:
                    if 'replay' in liveline:
                        return None

                    stream_url = decode_live_url(liveline)
                    if stream_url:
                        stream_url = stream_url.replace('http://', 'https://')

                        # Ekstrak nama
                        name_m = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
                        if not name_m:
                            name_m = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
                        name = name_m.group(1) if name_m else f"Huya {room_id}"

                        # Ekstrak logo
                        logo = ''
                        logo_m = re.search(r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
                        if not logo_m:
                            logo_m = re.search(
                                r'(https?://huyaimg\.msstatic\.com/avatar/[^"\s\')\\]+)', html)
                        if not logo_m:
                            logo_m = re.search(
                                r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
                        logo = logo_m.group(1) if logo_m else ''

                        # Ekstrak game
                        game_m = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
                        if not game_m:
                            game_m = re.search(r'"sGameName"\s*:\s*"([^"]+)"', html)
                        game = game_m.group(1) if game_m else ''

                        return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # METODE 2: mp.huya.com API JSON
    # ═══════════════════════════════════════════════════════════
    try:
        r = sess.get(
            f"https://mp.huya.com/cache.php?m=Live&do=profileRoom&roomid={room_id}",
            timeout=TIMEOUT
        )
        if r.headers.get('Content-Type', '').startswith('application/json'):
            data = r.json()
            d = data.get('data')
            # Jika data adalah list → offline/tidak ada
            if d and not isinstance(d, list):
                stream = d.get('stream')
                if stream:
                    # Cek live status (case-insensitive)
                    live_status = str(d.get('liveStatus', '')).lower()
                    if live_status and live_status not in ('on', 'live'):
                        return None

                    stream_url = None

                    # FLV multiLine
                    flv_list = stream.get('flv', {}).get('multiLine', [])
                    if flv_list:
                        for line in reversed(flv_list):
                            url = line.get('url', '')
                            if url:
                                stream_url = url.replace('http://', 'https://')
                                break

                    # baseSteamInfoList
                    if not stream_url:
                        base_list = stream.get('baseSteamInfoList', [])
                        if base_list:
                            base = base_list[0]
                            sname = base.get('sStreamName', '')
                            anti = base.get('sFlvAntiCode', '') or base.get('sHlsAntiCode', '')
                            if sname and anti:
                                hls_lines = stream.get('hls', {}).get('multiLine', [])
                                cdn = (hls_lines[0].get('cdnType', 'huyalive')
                                       if hls_lines else 'huyalive').lower()
                                stream_url = f"https://{cdn}.hls.huya.com/src/{sname}.m3u8?{anti}"

                    # gameStreamInfoList
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
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # METODE 3: www.huya.com + stream JSON
    # ═══════════════════════════════════════════════════════════
    try:
        r = sess.get(f"https://www.huya.com/{room_id}", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text

            # Ekstrak stream JSON: stream: {...}\n...};
            m = re.search(r'stream\s*:\s*(\{.+?\})\s*\n.*?};', html, re.DOTALL)
            if not m:
                m = re.search(r'"stream"\s*:\s*(\{.+?"gameStreamInfoList".+?\})\s*[,}]', html, re.DOTALL)
            if m:
                try:
                    stream_data = json.loads(m.group(1))
                    data_list = stream_data.get('data', [])
                    if data_list:
                        glist = data_list[0].get('gameStreamInfoList', [])
                        for s in glist:
                            if s.get('sFlvUrl') and s.get('sStreamName'):
                                stream_url = (
                                    f"{s['sFlvUrl']}/{s['sStreamName']}"
                                    f".{s.get('sFlvUrlSuffix', 'flv')}"
                                    f"?{s.get('sFlvAntiCode', '')}"
                                ).replace('http://', 'https://')

                                name_m = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
                                name = name_m.group(1) if name_m else f"Huya {room_id}"

                                logo_m = re.search(
                                    r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
                                logo = logo_m.group(1) if logo_m else ''

                                game_m = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
                                game = game_m.group(1) if game_m else ''

                                return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass

    return None


# ─── Step 5: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_per_cat=MAX_ROOMS_PER_CATEGORY,
                      categories_filter=None):
    print("=" * 55)
    print("  HUYA M3U PLAYLIST GENERATOR (FULL AUTO — FIXED)")
    print("=" * 55)

    # Step 1: Kategori
    print("\n[1/3] Mengambil semua kategori...")
    categories = get_all_categories()
    print(f"  Total kategori: {len(categories)}")

    if categories_filter:
        categories = [c for c in categories if c['id'] in categories_filter]
        print(f"  (Filter: {len(categories)} kategori)")

    # Step 2: Rooms
    print(f"\n[2/3] Mengambil rooms (max {max_per_cat}/kategori)...")
    all_rooms = []

    for i, cat in enumerate(categories):
        rooms = get_rooms(cat['id'], max_per_cat)
        for room in rooms:
            all_rooms.append((room, cat['name']))
        print(f"  {cat['name']:<20} → {len(rooms)} rooms")
        time.sleep(REQUEST_DELAY)

    print(f"\n  Total rooms: {len(all_rooms)}")

    # Step 3: Stream URL
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
            print(f"  [{i+1}/{len(all_rooms)}] "
                  f"live: {live_count} | offline: {offline_count} | error: {error_count}")

        time.sleep(REQUEST_DELAY)

    # Tulis output
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline | {error_count} error")
    print(f"  Output: {output_file}")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Huya M3U Playlist Generator (Full Auto — Fixed)')
    parser.add_argument('--output', '-o', default='playlist.m3u',
                        help='File output M3U (default: playlist.m3u)')
    parser.add_argument('--max-per-cat', '-m', type=int,
                        default=MAX_ROOMS_PER_CATEGORY,
                        help='Maks room per kategori (default: 10)')
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
