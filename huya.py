#!/usr/bin/env python3
"""
DOUYU M3U PLAYLIST GENERATOR
- Open API (open.douyucdn.cn) untuk listing — TANPA AUTH
- coapi.douyucdn.cn untuk stream URL (MD5 sign)
- Fallback: getH5PlayV1 (encryption-based)
- curl_cffi (Chrome TLS fingerprint)
- ThreadPoolExecutor (parallel)
- tvg-logo + group-title otomatis
- Chinese → English translation
- Output: playlist.m3u
"""

import re
import json
import time
import hashlib
import threading
import argparse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_ROOMS_TOTAL = 50
REQUEST_DELAY = 0.3
TIMEOUT = 15
MAX_WORKERS = 18
PAGE_SIZE = 30  # open API max 30/page

_local = threading.local()


def get_session():
    if not hasattr(_local, 'session'):
        _local.session = cffi_requests.Session(impersonate="chrome")
        try:
            _local.session.get("https://www.douyu.com/", timeout=TIMEOUT)
        except Exception:
            pass
    return _local.session


# ─── Terjemahan ──────────────────────────────────────────────────
GAME_TRANSLATION = {
    '英雄联盟': 'League of Legends',
    'CS2': 'CS2',
    '无畏契约': 'Valorant',
    'DOTA2': 'Dota 2',
    'Dota2': 'Dota 2',
    '王者荣耀': 'Honor of Kings',
    '和平精英': 'Game for Peace',
    '绝地求生': 'PUBG',
    '炉石传说': 'Hearthstone',
    '魔兽世界': 'World of Warcraft',
    '坦克世界': 'World of Tanks',
    '守望先锋': 'Overwatch',
    '穿越火线': 'CrossFire',
    '地下城与勇士': 'DNF',
    '冒险岛': 'MapleStory',
    '剑灵': 'Blade & Soul',
    '永恒之塔': 'Aion',
    '黑色沙漠': 'Black Desert',
    '天堂': 'Paradise',
    'FF14': 'FFXIV',
    '命运方舟': 'Lost Ark',
    '我的世界': 'Minecraft',
    '主机游戏': 'Console',
    '体育': 'Sports',
    '户外': 'Outdoor',
    '星秀': 'Star Show',
    '二次元': 'Anime',
    '音乐': 'Music',
    '美食': 'Food',
    '交友': 'Social',
    '原创': 'Original',
    '颜值': 'Beauty',
    '一起看': 'Watch Together',
    '上分': 'Ranking Up',
    '怀旧游戏': 'Retro',
    '军事游戏': 'Military',
    '动作游戏': 'Action',
    'MMORPG': 'MMORPG',
    '射击综合游戏': 'Shooter',
    '综合手游': 'Mobile General',
    '二次元手游': 'Anime Mobile',
    '棋牌休闲': 'Board/Card Games',
    'QQ飞车': 'QQ Speed',
    'QQ飞车手游': 'QQ Speed Mobile',
    'CF手游': 'CF Mobile',
    'LOL手游': 'LoL Mobile',
    '原神': 'Genshin Impact',
    '第五人格': 'Identity V',
    '蛋仔派对': 'Eggy Party',
    '崩坏：星穹铁道': 'Honkai: Star Rail',
    '鸣潮': 'Wuthering Waves',
    '绝区零': 'Zenless Zone Zero',
    '暗区突围': 'Arena Breakout',
    '永劫无间': 'Naraka',
    '幻塔': 'Tower of Fantasy',
    '明日之后': 'LifeAfter',
    '球球大作战': 'Ball Battle',
    '荒野乱斗': 'Brawl Stars',
    '皇室战争': 'Clash Royale',
    '部落冲突': 'Clash of Clans',
    '罗布乐思': 'Roblox',
    '迷你世界': 'Mini World',
    '植物大战僵尸': 'Plants vs Zombies',
    '鹅鸭杀': 'Goose Goose Duck',
    '恐鬼症': 'Phasmophobia',
    '森林之子': 'Sons of the Forest',
    '方舟': 'ARK',
    '全面战争': 'Total War',
    '帝国时代4': 'Age of Empires 4',
    '极限竞速：地平线': 'Forza Horizon',
    '刺客信条': "Assassin's Creed",
    '看门狗：军团': 'Watch Dogs Legion',
    '最终幻想系列': 'Final Fantasy',
    '体育游戏': 'Sports Games',
    '中国象棋': 'Chinese Chess',
    '欢乐斗地主': 'Happy Landlord',
    'JJ斗地主': 'JJ Landlord',
    '欢乐麻将': 'Happy Mahjong',
    '三国杀': '3 Kingdoms Kill',
    '拳皇15': 'KOF 15',
    '星际争霸': 'StarCraft',
    '魔兽争霸3': 'Warcraft 3',
    '战争雷霆': 'War Thunder',
    '艾尔登法环': 'Elden Ring',
    '只狼：影逝二度': 'Sekiro',
    '黑神话悟空': 'Black Myth: Wukong',
    '逃离塔科夫': 'Escape from Tarkov',
    '彩虹六号': 'Rainbow Six',
    '恐怖游戏': 'Horror',
    '格斗游戏': 'Fighting',
    '小游戏': 'Mini Games',
    '桌游': 'Board Games',
    '旅游': 'Travel',
    '科技': 'Tech',
    '虚拟偶像': 'Virtual Idol',
    'Apex英雄': 'Apex Legends',
    '怪物猎人：荒野': 'Monster Hunter Wilds',
    '博德之门3': "Baldur's Gate 3",
    '盗贼之海': 'Sea of Thieves',
    '环世界': 'RimWorld',
    '双人成行': 'It Takes Two',
    '生死狙击': 'Sniper Strike',
    '逆战': 'Counter-Strike',
    'CFHD': 'CFHD',
    'COD手游': 'COD Mobile',
    '火影忍者手游': 'Naruto Mobile',
    '王者万象棋': 'HoK Chess',
    '金铲铲之战': 'TFT Mobile',
    'LOL云顶之弈': 'TFT',
    'lol云顶之弈': 'TFT',
    '炉石战棋': 'Hearthstone BG',
    '三角洲行动': 'Delta Force',
    '天天吃鸡': 'PUBG Mobile',
    '失控进化-RUST': 'Rust',
    '失控进化-Rust': 'Rust',
    'KPL年度总决赛': 'KPL Finals',
    '斯诺克': 'Snooker',
    '2026S16': 'LPL S16',
}

_translation_cache = {}


def translate_text(text):
    if not text:
        return text
    if text in _translation_cache:
        return _translation_cache[text]
    if text in GAME_TRANSLATION:
        _translation_cache[text] = GAME_TRANSLATION[text]
        return GAME_TRANSLATION[text]
    if re.search(r'[\u4e00-\u9fff]', text):
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source='zh-CN', target='en').translate(text)
            _translation_cache[text] = result
            return result
        except Exception:
            pass
    _translation_cache[text] = text
    return text


# ─── Step 1: Ambil semua rooms (Open API — TANPA AUTH) ──────────
def get_all_rooms(max_rooms=MAX_ROOMS_TOTAL):
    """
    Open API: open.douyucdn.cn/api/RoomApi/live
    - Tidak perlu auth, cookie, atau sign
    - Max 30 rooms/page
    - Fields: room_id, room_src, room_name, owner_uid, online, hn, nickname, url
    """
    s = get_session()
    all_rooms = []
    page = 1

    while len(all_rooms) < max_rooms:
        api = (
            f"https://open.douyucdn.cn/api/RoomApi/live"
            f"?limit={PAGE_SIZE}&page={page}"
        )
        try:
            r = s.get(api, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception:
            break

        if data.get('error') != 0:
            break

        rooms = data.get('data', {}).get('rooms', [])
        if not rooms:
            break

        for item in rooms:
            room_id = str(item.get('room_id', ''))
            if not room_id:
                continue

            all_rooms.append({
                'id': room_id,
                'nick': item.get('nickname', ''),
                'introduction': item.get('room_name', ''),
                'screenshot': item.get('room_src', ''),
                'gameName': '',  # akan diisi dari room info
                'viewers': item.get('online', 0),
                'url': item.get('url', ''),
            })

        print(f"  Page {page}: +{len(rooms)} rooms (total: {len(all_rooms)})")
        page += 1
        time.sleep(REQUEST_DELAY)

        if len(rooms) < PAGE_SIZE:
            break

    return all_rooms[:max_rooms]


# ─── Step 2: Fallback — per kategori ─────────────────────────────
def get_rooms_by_category(max_rooms=MAX_ROOMS_TOTAL):
    """Fallback: ambil per kategori dari open API."""
    s = get_session()
    all_rooms = []

    # Ambil daftar kategori
    api = "https://open.douyucdn.cn/api/RoomApi/game"
    categories = []
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get('error') == 0:
                for item in data.get('data', {}).get('games', []):
                    cid = item.get('cate_id')
                    gname = item.get('game_name', '')
                    if cid and gname:
                        categories.append({'id': int(cid), 'name': gname})
    except Exception:
        pass

    if not categories:
        print("  [WARN] Tidak bisa ambil daftar kategori")
        return []

    # Ambil top 30 kategori (paling populer)
    categories = categories[:30]
    per_cat = max(10, max_rooms // len(categories))

    for cat in categories:
        if len(all_rooms) >= max_rooms:
            break
        api = (
            f"https://open.douyucdn.cn/api/RoomApi/live/{cat['id']}"
            f"?limit={min(per_cat, 30)}&page=1"
        )
        try:
            r = s.get(api, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        if data.get('error') != 0:
            continue

        rooms = data.get('data', {}).get('rooms', [])
        for item in rooms:
            room_id = str(item.get('room_id', ''))
            if not room_id:
                continue
            all_rooms.append({
                'id': room_id,
                'nick': item.get('nickname', ''),
                'introduction': item.get('room_name', ''),
                'screenshot': item.get('room_src', ''),
                'gameName': cat['name'],
                'viewers': item.get('online', 0),
                'url': item.get('url', ''),
            })
        time.sleep(REQUEST_DELAY)

    return all_rooms[:max_rooms]


# ─── Step 3: Stream URL — Method 1 (coapi, MD5 sign) ────────────
def get_stream_url_method1(room_id):
    """
    coapi.douyucdn.cn — old API, simple MD5 sign, no encryption.
    signContent = 'lapi/live/thirdPart/getPlay/{rid}?aid=pcclient&rate=0&time={tt}9TUk5fjjUjg9qIMH3sdnh'
    sign = md5(signContent)
    """
    s = get_session()
    tt = str(int(time.time()))

    sign_content = (
        f"lapi/live/thirdPart/getPlay/{room_id}"
        f"?aid=pcclient&rate=0&time={tt}9TUk5fjjUjg9qIMH3sdnh"
    )
    sign = hashlib.md5(sign_content.encode('ascii')).hexdigest()

    api = (
        f"https://coapi.douyucdn.cn/lapi/live/thirdPart/getPlay/{room_id}"
        f"?rate=0&auth={sign}"
    )
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get('error') == 0:
                d = data.get('data', {})
                rtmp_url = d.get('rtmp_url', '')
                rtmp_live = d.get('rtmp_live', '')
                if rtmp_url and rtmp_live:
                    return f"{rtmp_url}/{rtmp_live}"
    except Exception:
        pass

    return None


# ─── Step 4: Stream URL — Method 2 (getH5PlayV1, encryption) ────
def get_stream_url_method2(room_id):
    """
    New method (2026):
    1. GET /wgapi/livenc/liveweb/websec/getEncryption?did={did}
    2. f = rand_str; for _ in range(enc_time): f = md5(f + key)
       auth = md5(f + key + str(rid) + str(ts))
    3. POST /lapi/live/getH5PlayV1/{rid}
    """
    s = get_session()
    did = uuid.uuid4().hex
    ts = str(int(time.time()))

    # Step 1: Get encryption key
    enc_api = f"https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={did}"
    try:
        r = s.get(enc_api, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get('error') != 0:
            return None
        enc_data = data.get('data', {})
    except Exception:
        return None

    key = enc_data.get('key', '')
    rand_str = enc_data.get('rand_str', '')
    enc_time = int(enc_data.get('enc_time', 0))
    enc_data_str = enc_data.get('enc_data', '')

    if not key or not rand_str:
        return None

    # Step 2: Compute auth
    f = rand_str
    for _ in range(enc_time):
        f = hashlib.md5((f + key).encode('utf-8')).hexdigest()
    auth = hashlib.md5((f + key + str(room_id) + ts).encode('utf-8')).hexdigest()

    # Step 3: POST getH5PlayV1
    api = f"https://www.douyu.com/lapi/live/getH5PlayV1/{room_id}"
    form_data = {
        'enc_data': enc_data_str,
        'tt': ts,
        'did': did,
        'auth': auth,
        'cdn': 'hw-h5',
        'rate': '0',
        'ver': 'Douyu_new',
        'iar': '0',
        'ive': '0',
        'prev': '1',
        'bs': '2',
    }
    try:
        r = s.post(api, data=form_data, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get('error') == 0:
                d = data.get('data', {})
                rtmp_url = d.get('rtmp_url', '')
                rtmp_live = d.get('rtmp_live', '')
                if rtmp_url and rtmp_live:
                    return f"{rtmp_url}/{rtmp_live}"
    except Exception:
        pass

    return None


# ─── Step 5: Stream URL — Method 3 (hlsH5Preview) ───────────────
def get_stream_url_method3(room_id):
    """
    playweb.douyucdn.cn — preview endpoint (lower quality but simpler).
    """
    s = get_session()
    did = uuid.uuid4().hex
    t13 = str(int(time.time()))
    auth = hashlib.md5((str(room_id) + t13).encode('utf-8')).hexdigest()

    api = f"https://playweb.douyucdn.cn/lapi/live/hlsH5Preview/{room_id}"
    headers = {
        'rid': str(room_id),
        'time': t13,
        'auth': auth,
    }
    form_data = {
        'rid': str(room_id),
        'did': did,
    }
    try:
        r = s.post(api, headers=headers, data=form_data, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get('error') == 0:
                d = data.get('data', {})
                rtmp_live = d.get('rtmp_live', '')
                if rtmp_live:
                    # Extract stream key
                    key_match = re.search(r'(\d+r\w+)', rtmp_live)
                    if key_match:
                        return f"http://vplay1a.douyucdn.cn/live/{key_match.group(1)}.flv?uuid={did}"
    except Exception:
        pass

    return None


# ─── Step 6: Get stream URL (try all methods) ───────────────────
def get_stream_url(room_id):
    """Try 3 methods in order."""
    # Method 1: coapi (simplest)
    url = get_stream_url_method1(room_id)
    if url:
        return url

    # Method 2: getH5PlayV1 (encryption)
    url = get_stream_url_method2(room_id)
    if url:
        return url

    # Method 3: hlsH5Preview (fallback)
    url = get_stream_url_method3(room_id)
    if url:
        return url

    return None


# ─── Step 7: Get room info (game name, avatar) ──────────────────
def get_room_info(room_id):
    """Get room details from open API."""
    s = get_session()
    api = f"https://open.douyucdn.cn/api/RoomApi/room/{room_id}"
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get('error') == 0:
                d = data.get('data', {})
                return {
                    'gameName': d.get('game_name', ''),
                    'avatar': d.get('avatar', ''),
                    'cover': d.get('cover', ''),
                    'room_name': d.get('room_name', ''),
                    'nickname': d.get('nickname', ''),
                    'online': d.get('online', 0),
                }
    except Exception:
        pass
    return None


# ─── Step 8: Worker ──────────────────────────────────────────────
def fetch_worker(args):
    idx, room = args
    room_id = room['id']
    try:
        stream_url = get_stream_url(room_id)
    except Exception as e:
        return (idx, room, None, str(e))
    return (idx, room, stream_url, None)


# ─── Step 9: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_rooms, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  DOUYU M3U PLAYLIST GENERATOR")
    print(f"  {max_rooms} rooms | {max_workers} threads")
    print("=" * 55)

    # Step 1: Ambil semua rooms
    print("\n[1/3] Mengambil rooms (open API)...")
    all_rooms = get_all_rooms(max_rooms)

    if not all_rooms:
        print("  [WARN] Open API gagal, mencoba per kategori...")
        all_rooms = get_rooms_by_category(max_rooms)

    print(f"\n  Total rooms: {len(all_rooms)}")

    if not all_rooms:
        print("\n  [ERROR] Tidak ada rooms! IP mungkin di-ban.")
        return

    # Step 2: Stream URL (PARALEL)
    print(f"\n[2/3] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(all_rooms)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_worker, (i, room)): i
            for i, room in enumerate(all_rooms)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, room, stream_url, error = future.result()
            results[idx] = (room, stream_url, error)
            done_count += 1
            if done_count % 50 == 0:
                print(f"  Progress: {done_count}/{len(all_rooms)}")

    # Step 3: Build playlist + translate
    print(f"\n[3/3] Membangun playlist + translate...")
    lines = ['#EXTM3U']
    live_count = 0
    offline_count = 0
    error_count = 0

    for item in results:
        if item is None:
            continue
        room, stream_url, error = item

        if error:
            error_count += 1
            continue
        if not stream_url:
            offline_count += 1
            continue

        # Get room info untuk game name & avatar (jika belum ada)
        game_name = room.get('gameName', '')
        logo = room.get('screenshot', '')

        if not game_name or not logo:
            info = get_room_info(room['id'])
            if info:
                if not game_name:
                    game_name = info.get('gameName', '')
                if not logo:
                    logo = info.get('cover', '') or info.get('avatar', '')

        name = translate_text(room['nick'] or room['introduction'] or f"Douyu {room['id']}")
        group_title = translate_text(game_name or 'Douyu')

        extinf = f'#EXTINF:-1'
        if logo:
            extinf += f' tvg-logo="{logo}"'
        extinf += f' group-title="{group_title}"'
        extinf += f',{name}'

        lines.append(extinf)
        lines.append(stream_url)
        live_count += 1

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"\n{'=' * 55}")
    print(f"  SELESAI: {live_count} live | {offline_count} offline | {error_count} error")
    print(f"  Output: {output_file}")
    print(f"{'=' * 55}")


# ─── Main ────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Douyu M3U Playlist Generator')
    parser.add_argument('--output', '-o', default='playlist_douyu.m3u',
                        help='File output M3U (default: playlist_douyu.m3u)')
    parser.add_argument('--rooms', '-r', type=int, default=MAX_ROOMS_TOTAL,
                        help=f'Total room (default: {MAX_ROOMS_TOTAL})')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 18)')
    parser.add_argument('--delay', '-d', type=float, default=REQUEST_DELAY,
                        help='Jeda antar request (default: 0.3)')
    parser.add_argument('--no-translate', action='store_true',
                        help='Nonaktifkan terjemahan (lebih cepat)')
    args = parser.parse_args()

    if args.no_translate:
        translate_text = lambda x: x or ''

    REQUEST_DELAY = args.delay
    generate_playlist(
        output_file=args.output,
        max_rooms=args.rooms,
        max_workers=args.workers,
    )   
