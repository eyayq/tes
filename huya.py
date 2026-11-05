#!/usr/bin/env python3
"""
HUYA M3U PLAYLIST GENERATOR — FULL AUTO
- curl_cffi (TLS fingerprint impersonation → bypass anti-bot)
- ThreadPoolExecutor (parallel stream URL fetching)
- Auto-scrape: semua kategori, rooms, tvg-logo, group-title
"""

import re
import json
import base64
import time
import hashlib
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote

from curl_cffi import requests as cffi_requests

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_ROOMS_PER_CATEGORY = 10
REQUEST_DELAY = 0.2
TIMEOUT = 15
MAX_WORKERS = 8          # jumlah thread paralel
IMPERSONATE = "chrome"   # TLS fingerprint

# Thread-local: setiap thread punya session sendiri (thread-safe)
_local = threading.local()


def get_session():
    """Ambil atau buat session per-thread."""
    if not hasattr(_local, 'session'):
        _local.session = cffi_requests.Session(impersonate=IMPERSONATE)
    return _local.session


# ─── Step 1: Ambil SEMUA kategori ────────────────────────────────
def get_all_categories():
    s = get_session()

    # Metode 1: API
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200 and r.headers.get('Content-Type', '').startswith('application/json'):
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
        r = s.get("https://www.huya.com/", timeout=TIMEOUT)
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
    s = get_session()
    rooms = []
    page = 1

    while len(rooms) < max_rooms:
        api = (
            f"https://www.huya.com/cache.php"
            f"?m=LiveList&do=getLiveListByPage"
            f"&gameId={category_id}&tagAll=0&page={page}"
        )
        try:
            r = s.get(api, timeout=TIMEOUT)
            if r.status_code != 200:
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


# ─── Step 2b: Esports/sports matches ─────────────────────────────
def get_hot_matches():
    s = get_session()
    api = "https://www.huya.com/cache.php?callback=huyaNavHotMatch&do=ajaxGetHotMatch&m=Nav"
    try:
        r = s.get(api, timeout=TIMEOUT)
        text = r.text
        start = text.index('(') + 1
        end = text.rindex(')')
        data = json.loads(text[start:end])
    except Exception as e:
        print(f"  [WARN] huyaNavHotMatch: {e}")
        return []

    rooms = []
    for item in data.get('data', {}).get('matchLives', []):
        room_id = str(item.get('profileRoom', ''))
        if not room_id:
            continue
        rooms.append({
            'id': room_id,
            'nick': item.get('roomName', '') or item.get('introduction', ''),
            'introduction': item.get('introduction', ''),
            'screenshot': '',
            'gameName': item.get('name', ''),
            'viewers': item.get('totalCount', 0),
        })

    for item in data.get('data', {}).get('matchNotices', []):
        if item.get('liveState') != 1:
            continue
        room_id = str(item.get('profileRoom', ''))
        if not room_id:
            continue
        rooms.append({
            'id': room_id,
            'nick': item.get('nick', '') or item.get('title', ''),
            'introduction': item.get('digest', ''),
            'screenshot': item.get('picUrl', ''),
            'gameName': item.get('title', ''),
            'viewers': 0,
        })

    return rooms


# ─── Step 3: Decode liveLineUrl → stream URL ─────────────────────
def decode_live_url(liveline):
    """Decode liveLineUrl (base64) → real stream URL with wsSecret."""
    try:
        i, b = liveline.split('?', 1)
        r = i.split('/')
        s_name = re.sub(r'\.(flv|m3u8)', '', r[-1])

        params = {}
        for kv in b.split('&'):
            if '=' in kv:
                k, v = kv.split('=', 1)
                params[k] = v

        fm = unquote(params.get('fm', ''))
        if not fm:
            return None

        u = base64.b64decode(fm).decode('utf-8')
        hash_prefix = u.split('_')[0]
        wsTime = params.get('wsTime', '')
        f = str(int(time.time() * 1e7))
        t = '0'

        h = '_'.join([hash_prefix, t, s_name, f, wsTime])
        wsSecret = hashlib.md5(h.encode('utf-8')).hexdigest()

        remaining = {k: v for k, v in params.items() if k not in ('fm', 'wsTime')}
        query_parts = [f"wsSecret={wsSecret}", f"wsTime={wsTime}", f"u={t}", f"seqid={f}"]
        for k, v in remaining.items():
            query_parts.append(f"{k}={v}")

        return f"{i}?{'&'.join(query_parts)}"
    except Exception:
        return None


# ─── Step 4: Ambil stream info per room (dipanggil dari thread) ──
def get_stream_info(room_id):
    """
    3 metode (urutan prioritas):
      1. m.huya.com + liveLineUrl (base64) — PALING ANDAL
      2. mp.huya.com API JSON
      3. www.huya.com + stream JSON
    """
    s = get_session()

    # ═══ METODE 1: m.huya.com + liveLineUrl ═══
    try:
        r = s.get(f"https://m.huya.com/{room_id}", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text

            m = re.search(r'ISLIVE\s*=\s*([a-z]+)', html)
            if m and m.group(1) == 'false':
                return None

            m = re.search(r'"liveLineUrl"\s*:\s*"([^"]+)"', html)
            if not m:
                m = re.search(r'liveLineUrl\s*=\s*"([^"]+)"', html)
            if m:
                try:
                    liveline = base64.b64decode(m.group(1)).decode('utf-8')
                except Exception:
                    liveline = ''

                if liveline and 'replay' not in liveline:
                    stream_url = decode_live_url(liveline)
                    if stream_url:
                        stream_url = stream_url.replace('http://', 'https://')

                        name_m = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
                        if not name_m:
                            name_m = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
                        name = name_m.group(1) if name_m else f"Huya {room_id}"

                        logo = ''
                        logo_m = re.search(r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
                        if not logo_m:
                            logo_m = re.search(
                                r'(https?://huyaimg\.msstatic\.com/avatar/[^"\s\')\\]+)', html)
                        if not logo_m:
                            logo_m = re.search(
                                r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
                        logo = logo_m.group(1) if logo_m else ''

                        game_m = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
                        if not game_m:
                            game_m = re.search(r'"sGameName"\s*:\s*"([^"]+)"', html)
                        game = game_m.group(1) if game_m else ''

                        return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}
    except Exception:
        pass

    # ═══ METODE 2: mp.huya.com API JSON ═══
    try:
        r = s.get(
            f"https://mp.huya.com/cache.php?m=Live&do=profileRoom&roomid={room_id}",
            timeout=TIMEOUT
        )
        if r.status_code == 200:
            data = r.json()
            d = data.get('data')
            if d and not isinstance(d, list):
                stream = d.get('stream')
                if stream:
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
                        for gs in glist:
                            if gs.get('sFlvUrl') and gs.get('sStreamName'):
                                stream_url = (
                                    f"{gs['sFlvUrl']}/{gs['sStreamName']}"
                                    f".{gs.get('sFlvUrlSuffix', 'flv')}"
                                    f"?{gs.get('sFlvAntiCode', '')}"
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

    # ═══ METODE 3: www.huya.com + stream JSON ═══
    try:
        r = s.get(f"https://www.huya.com/{room_id}", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text

            m = re.search(r'ISLIVE\s*=\s*([a-z]+)', html)
            if m and m.group(1) == 'false':
                return None

            stream_url = None
            m = re.search(r'"gameStreamInfoList"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
            if m:
                try:
                    for gs in json.loads(m.group(1)):
                        if gs.get('sFlvUrl') and gs.get('sStreamName'):
                            stream_url = (
                                f"{gs['sFlvUrl']}/{gs['sStreamName']}"
                                f".{gs.get('sFlvUrlSuffix', 'flv')}"
                                f"?{gs.get('sFlvAntiCode', '')}"
                            ).replace('http://', 'https://')
                            break
                except json.JSONDecodeError:
                    pass

            if not stream_url:
                m = re.search(r'"?liveLineUrl"?\s*[:=]\s*"([^"]+)"', html)
                if m:
                    try:
                        decoded = base64.b64decode(m.group(1)).decode('utf-8')
                        if 'replay' not in decoded:
                            stream_url = decode_live_url(decoded)
                            if stream_url:
                                stream_url = stream_url.replace('http://', 'https://')
                    except Exception:
                        pass

            if not stream_url:
                return None

            name_m = re.search(r'"nick"\s*:\s*"([^"]+)"', html)
            if not name_m:
                name_m = re.search(r'"nickName"\s*:\s*"([^"]+)"', html)
            name = name_m.group(1) if name_m else f"Huya {room_id}"

            logo = ''
            logo_m = re.search(r'"sAvatar"\s*:\s*"(https?://[^"]+)"', html)
            if not logo_m:
                logo_m = re.search(
                    r'(https?://huyaimg\.msstatic\.com/avatar/[^"\s\')\\]+)', html)
            if not logo_m:
                logo_m = re.search(
                    r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
            logo = logo_m.group(1) if logo_m else ''

            game_m = re.search(r'"gameName"\s*:\s*"([^"]+)"', html)
            if not game_m:
                game_m = re.search(r'"sGameName"\s*:\s*"([^"]+)"', html)
            game = game_m.group(1) if game_m else 'Huya'

            return {'url': stream_url, 'name': name, 'logo': logo, 'game': game}
    except Exception:
        pass

    return None


# ─── Step 5: Worker untuk ThreadPool ─────────────────────────────
def fetch_stream_worker(args):
    """Worker function — dipanggil dari ThreadPoolExecutor."""
    idx, room, cat_name = args
    room_id = room['id']
    label = room['nick'] or room['introduction'] or room_id

    try:
        info = get_stream_info(room_id)
    except Exception as e:
        return (idx, room, cat_name, None, str(e))

    return (idx, room, cat_name, info, None)


# ─── Step 6: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_per_cat=MAX_ROOMS_PER_CATEGORY,
                      categories_filter=None, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  HUYA M3U PLAYLIST GENERATOR (curl_cffi + ThreadPool)")
    print("=" * 55)

    # Step 1: Kategori
    print("\n[1/4] Mengambil semua kategori...")
    categories = get_all_categories()
    print(f"  Total kategori: {len(categories)}")

    if categories_filter:
        categories = [c for c in categories if c['id'] in categories_filter]
        print(f"  (Filter: {len(categories)} kategori)")

    # Step 2: Rooms
    print(f"\n[2/4] Mengambil rooms (max {max_per_cat}/kategori)...")
    all_rooms = []

    for i, cat in enumerate(categories):
        rooms = get_rooms(cat['id'], max_per_cat)
        for room in rooms:
            all_rooms.append((room, cat['name']))
        print(f"  {cat['name']:<20} → {len(rooms)} rooms")
        time.sleep(REQUEST_DELAY)

    # Step 2b: Esports
    print("\n  [ESPORTS] Mengambil hot matches...")
    esports_rooms = get_hot_matches()
    for room in esports_rooms:
        all_rooms.append((room, 'Esports/Sports'))
    print(f"  Esports/Sports         → {len(esports_rooms)} rooms")

    print(f"\n  Total rooms: {len(all_rooms)}")

    # Step 3: Stream URL (PARALEL dengan ThreadPool)
    print(f"\n[3/4] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(all_rooms)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_stream_worker, (i, room, cat)): i
            for i, (room, cat) in enumerate(all_rooms)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, room, cat_name, info, error = future.result()
            results[idx] = (room, cat_name, info, error)
            done_count += 1

            if done_count % 20 == 0:
                print(f"  Progress: {done_count}/{len(all_rooms)}")

    # Step 4: Build playlist
    print(f"\n[4/4] Membangun playlist...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for item in results:
        if item is None:
            continue
        room, cat_name, info, error = item

        if error:
            error_count += 1
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
        description='Huya M3U Playlist Generator (curl_cffi + ThreadPool)')
    parser.add_argument('--output', '-o', default='playlist.m3u',
                        help='File output M3U (default: playlist.m3u)')
    parser.add_argument('--max-per-cat', '-m', type=int,
                        default=MAX_ROOMS_PER_CATEGORY,
                        help='Maks room per kategori (default: 10)')
    parser.add_argument('--categories', '-c', nargs='*', type=int,
                        help='Filter kategori ID (contoh: -c 2135 1052)')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 8)')
    parser.add_argument('--delay', '-d', type=float, default=REQUEST_DELAY,
                        help='Jeda antar request listing (default: 0.2)')
    args = parser.parse_args()

    REQUEST_DELAY = args.delay
    generate_playlist(
        output_file=args.output,
        max_per_cat=args.max_per_cat,
        categories_filter=set(args.categories) if args.categories else None,
        max_workers=args.workers,
    )   
