
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
MAX_ROOMS_TOTAL = 2000
REQUEST_DELAY = 0
TIMEOUT = 15
MAX_WORKERS = 28
PER_CATEGORY = 50

_local = threading.local()


def get_session():
    if not hasattr(_local, 'session'):
        _local.session = cffi_requests.Session(impersonate="chrome")
        try:
            _local.session.get("https://www.douyu.com/", timeout=TIMEOUT)
        except Exception:
            pass
    return _local.session


# ─── Terjemahan Chinese → English ────────────────────────────────
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


# ─── Parse response (handle list atau dict) ─────────────────────
def parse_response(data, key='rooms'):
    data_field = data.get('data', [])
    if isinstance(data_field, list):
        return data_field
    elif isinstance(data_field, dict):
        return data_field.get(key, [])
    return []


# ─── Step 0: Scrape SEMUA kategori (3222) ───────────────────────
def get_all_categories():
    s = get_session()
    api = "https://open.douyucdn.cn/api/RoomApi/game"

    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    if data.get('error') != 0:
        return []

    games = parse_response(data, key='games')
    if not games:
        games = data.get('data', [])
        if not isinstance(games, list):
            return []

    categories = []
    for item in games:
        short = item.get('short_name', '')
        name = item.get('game_name', '')
        if short and name:
            categories.append({'short': short.lower(), 'name': name})

    return categories


# ─── Step 1: Ambil rooms (adaptive, semua 3222 kategori) ────────
def get_all_rooms(max_rooms, categories):
    s = get_session()
    all_rooms = []
    seen_ids = set()
    consecutive_empty = 0

    for i, cat in enumerate(categories):
        if len(all_rooms) >= max_rooms:
            break

        api = (
            f"https://open.douyucdn.cn/api/RoomApi/live/{cat['short']}"
            f"?limit={min(PER_CATEGORY, 30)}&page=1"
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

        rooms = parse_response(data)
        new_count = 0
        for item in rooms:
            room_id = str(item.get('room_id', ''))
            if not room_id or room_id in seen_ids:
                continue
            seen_ids.add(room_id)

            all_rooms.append({
                'id': room_id,
                'nick': item.get('nickname', ''),
                'introduction': item.get('room_name', ''),
                'screenshot': item.get('room_src', ''),
                'gameName': cat['name'],
                'viewers': item.get('online', 0),
            })
            new_count += 1

        if new_count == 0:
            consecutive_empty += 1
            if consecutive_empty >= 100:
                print(f"  [INFO] 100 kategori kosong berturut-turut — stop.")
                break
        else:
            consecutive_empty = 0

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(categories)} | total rooms: {len(all_rooms)}")

        time.sleep(REQUEST_DELAY)

    return all_rooms[:max_rooms]


# ─── Step 2: Stream URL — Method 1 (coapi, MD5 sign) ────────────
def get_stream_url_method1(room_id):
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
                if isinstance(d, dict):
                    rtmp_url = d.get('rtmp_url', '')
                    rtmp_live = d.get('rtmp_live', '')
                    if rtmp_url and rtmp_live:
                        return f"{rtmp_url}/{rtmp_live}"
    except Exception:
        pass

    return None


# ─── Step 3: Stream URL — Method 2 (getH5PlayV1) ────────────────
def get_stream_url_method2(room_id):
    s = get_session()
    did = uuid.uuid4().hex
    ts = str(int(time.time()))

    enc_api = f"https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={did}"
    try:
        r = s.get(enc_api, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get('error') != 0:
            return None
        enc_data = data.get('data', {})
        if not isinstance(enc_data, dict):
            return None
    except Exception:
        return None

    key = enc_data.get('key', '')
    rand_str = enc_data.get('rand_str', '')
    enc_time = int(enc_data.get('enc_time', 0))
    enc_data_str = enc_data.get('enc_data', '')

    if not key or not rand_str:
        return None

    f = rand_str
    for _ in range(enc_time):
        f = hashlib.md5((f + key).encode('utf-8')).hexdigest()
    auth = hashlib.md5((f + key + str(room_id) + ts).encode('utf-8')).hexdigest()

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
                if isinstance(d, dict):
                    rtmp_url = d.get('rtmp_url', '')
                    rtmp_live = d.get('rtmp_live', '')
                    if rtmp_url and rtmp_live:
                        return f"{rtmp_url}/{rtmp_live}"
    except Exception:
        pass

    return None


# ─── Step 4: Stream URL — Method 3 (hlsH5Preview) ───────────────
def get_stream_url_method3(room_id):
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
                if isinstance(d, dict):
                    rtmp_live = d.get('rtmp_live', '')
                    if rtmp_live:
                        key_match = re.search(r'(\d+r\w+)', rtmp_live)
                        if key_match:
                            return f"http://vplay1a.douyucdn.cn/live/{key_match.group(1)}.flv?uuid={did}"
    except Exception:
        pass

    return None


# ─── Step 5: Get stream URL (try all methods) ───────────────────
def get_stream_url(room_id):
    url = get_stream_url_method1(room_id)
    if url:
        return url
    url = get_stream_url_method2(room_id)
    if url:
        return url
    url = get_stream_url_method3(room_id)
    if url:
        return url
    return None


# ─── Step 6: Worker ──────────────────────────────────────────────
def fetch_worker(args):
    idx, room = args
    room_id = room['id']
    try:
        stream_url = get_stream_url(room_id)
    except Exception as e:
        return (idx, room, None, str(e))
    return (idx, room, stream_url, None)


# ─── Step 7: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_rooms, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  DOUYU M3U PLAYLIST GENERATOR (FINAL)")
    print(f"  {max_rooms} rooms | {max_workers} threads | {PER_CATEGORY}/kategori")
    print("=" * 55)

    # Step 0: Scrape semua kategori
    print("\n[0/3] Scraping semua kategori...")
    categories = get_all_categories()
    print(f"  Kategori ditemukan: {len(categories)}")

    if not categories:
        print("  [ERROR] Tidak bisa ambil kategori!")
        return

    # Step 1: Ambil rooms (adaptive)
    print(f"\n[1/3] Mengambil rooms (adaptive, max {max_rooms})...")
    all_rooms = get_all_rooms(max_rooms, categories)

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

        game_name = room.get('gameName', '')
        logo = room.get('screenshot', '')

        name = translate_text(room['nick'] or room['introduction'] or f"Douyu {room['id']}")
        group_title = translate_text(game_name) if game_name else 'Douyu'

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
        description='Douyu M3U Playlist Generator (Final)')
    parser.add_argument('--output', '-o', default='playlist_douyu.m3u',
                        help='File output M3U (default: playlist_douyu.m3u)')
    parser.add_argument('--rooms', '-r', type=int, default=MAX_ROOMS_TOTAL,
                        help=f'Total room (default: {MAX_ROOMS_TOTAL})')
    parser.add_argument('--workers', '-w', type=int, default=MAX_WORKERS,
                        help='Jumlah thread paralel (default: 18)')
    parser.add_argument('--delay', '-d', type=float, default=REQUEST_DELAY,
                        help='Jeda antar request (default: 0.2)')
    parser.add_argument('--per-cat', '-p', type=int, default=PER_CATEGORY,
                        help=f'Room per kategori (default: {PER_CATEGORY})')
    parser.add_argument('--no-translate', action='store_true',
                        help='Nonaktifkan terjemahan (lebih cepat)')
    args = parser.parse_args()

    if args.no_translate:
        translate_text = lambda x: x or ''

    REQUEST_DELAY = args.delay
    PER_CATEGORY = args.per_cat
    generate_playlist(
        output_file=args.output,
        max_rooms=args.rooms,
        max_workers=args.workers,
    )   
