#!/usr/bin/env python3
"""
HUYA M3U PLAYLIST GENERATOR — FINAL
- live.huya.com/liveHttpUI/getLiveList (iGid=0 = ALL categories)
- curl_cffi (Chrome TLS fingerprint)
- ThreadPoolExecutor (parallel stream fetching)
- tvg-logo + group-title dari listing API langsung
- Chinese → English translation
- Output: playlist.m3u
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
MAX_ROOMS_TOTAL = 500       # total room yang mau diambil
REQUEST_DELAY = 0.3
TIMEOUT = 15
MAX_WORKERS = 18
PAGE_SIZE = 120             # rooms per page (max 120)

_local = threading.local()


def get_session():
    if not hasattr(_local, 'session'):
        _local.session = cffi_requests.Session(impersonate="chrome")
        try:
            _local.session.get("https://m.huya.com/", timeout=TIMEOUT)
        except Exception:
            pass
    return _local.session


# ─── Terjemahan Chinese → English ────────────────────────────────
GAME_TRANSLATION = {
    '英雄联盟': 'League of Legends',
    'CS2': 'CS2',
    '无畏契约': 'Valorant',
    '三角洲行动': 'Delta Force',
    'LOL云顶之弈': 'TFT',
    'lol云顶之弈': 'TFT',
    '炉石传说': 'Hearthstone',
    '穿越火线': 'CrossFire',
    'DOTA2': 'Dota 2',
    'Dota2': 'Dota 2',
    '魔兽世界': 'World of Warcraft',
    '坦克世界': 'World of Tanks',
    '守望先锋': 'Overwatch',
    '天天吃鸡': 'PUBG',
    '主机游戏': 'Console',
    '我的世界': 'Minecraft',
    '失控进化-RUST': 'Rust',
    '失控进化-Rust': 'Rust',
    '植物大战僵尸': 'Plants vs Zombies',
    '彩虹六号': 'Rainbow Six',
    '黑神话悟空': 'Black Myth: Wukong',
    '黑神话：悟空': 'Black Myth: Wukong',
    '逃离塔科夫': 'Escape from Tarkov',
    '恐怖游戏': 'Horror',
    '体育': 'Sports',
    '户外': 'Outdoor',
    '星秀': 'Star Show',
    '一起看': 'Watch Together',
    '原创': 'Original',
    '交友': 'Social',
    '二次元': 'Anime',
    '美食': 'Food',
    '上分': 'Ranking Up',
    '王者荣耀': 'Honor of Kings',
    '和平精英': 'Game for Peace',
    '金铲铲之战': 'TFT Mobile',
    '棋牌休闲': 'Board/Card Games',
    'QQ飞车手游': 'QQ Speed Mobile',
    'COD手游': 'COD Mobile',
    '火影忍者手游': 'Naruto Mobile',
    '蛋仔派对': 'Eggy Party',
    '王者万象棋': 'HoK Chess',
    'CF手游': 'CF Mobile',
    'LOL手游': 'LoL Mobile',
    '皇室战争': 'Clash Royale',
    '原神': 'Genshin Impact',
    '第五人格': 'Identity V',
    'MMORPG': 'MMORPG',
    '射击综合游戏': 'Shooter',
    '英雄联盟手游': 'LoL Mobile',
    '动作游戏': 'Action',
    '地下城与勇士': 'DNF',
    '冒险岛': 'MapleStory',
    '军事游戏': 'Military',
    '综合手游': 'Mobile General',
    '永劫无间': 'Naraka',
    '桌游': 'Board Games',
    '二次元手游': 'Anime Mobile',
    'QQ飞车': 'QQ Speed',
    '炉石战棋': 'Hearthstone BG',
    '魔兽争霸3': 'Warcraft 3',
    '剑灵': 'Blade & Soul',
    '欢乐斗地主': 'Happy Landlord',
    '三国杀': '3 Kingdoms Kill',
    '幻塔': 'Tower of Fantasy',
    '英魂之刃': 'Spirit Blade',
    '颜值': 'Beauty',
    '方舟': 'ARK',
    '怀旧游戏': 'Retro',
    'JJ斗地主': 'JJ Landlord',
    '逆战': 'Counter-Strike',
    '音乐': 'Music',
    '科技': 'Tech',
    '欢乐麻将': 'Happy Mahjong',
    '拳皇15': 'KOF 15',
    '永劫无间手游': 'Naraka Mobile',
    '星际争霸': 'StarCraft',
    '虚拟偶像': 'Virtual Idol',
    '罗布乐思': 'Roblox',
    '旅游': 'Travel',
    '恐鬼症': 'Phasmophobia',
    '迷你世界': 'Mini World',
    '梦三国': 'Dream 3K',
    '天翼决': 'Skyfire',
    '全球使命': 'Global Mission',
    '洛克王国：世界': 'Roco Kingdom',
    '暗区突围': 'Arena Breakout',
    '中国象棋': 'Chinese Chess',
    '天天象棋': 'Daily Chinese Chess',
    '崩坏：星穹铁道': 'Honkai: Star Rail',
    '热血江湖': 'Hot-Blooded Jianghu',
    '死亡之夜': 'Night of Death',
    '台球国度': 'Billiard Kingdom',
    '鸣潮': 'Wuthering Waves',
    'CFHD': 'CFHD',
    '艾尔登法环': 'Elden Ring',
    '极限竞速：地平线': 'Forza Horizon',
    '鹅鸭杀': 'Goose Goose Duck',
    '永恒之塔': 'Aion',
    '暗区突围：无限': 'Arena Breakout: Infinite',
    '海贼王 寻秘世界': 'One Piece',
    'Apex英雄': 'Apex Legends',
    '怪物猎人：荒野': 'Monster Hunter Wilds',
    '育碧游戏': 'Ubisoft',
    '全面战争': 'Total War',
    '体育游戏': 'Sports Games',
    '诛仙世界': 'Zhu Xian World',
    '明日之后': 'LifeAfter',
    '黑色沙漠': 'Black Desert',
    '森林之子': 'Sons of the Forest',
    '天堂': 'Paradise',
    '战争雷霆': 'War Thunder',
    '绝区零': 'Zenless Zone Zero',
    '部落冲突': 'Clash of Clans',
    '矩阵：零日危机': 'Matrix Zero Day',
    '只狼：影逝二度': 'Sekiro',
    '暗黑破坏神': 'Diablo',
    '风云': 'Storm Riders',
    '甜蜜之家': 'Sweet Home',
    '异人之下': 'Under the Skin',
    '最终幻想7：重制版': 'FF7 Remake',
    'NBA2KOL系列': 'NBA 2KOL',
    '盗贼之海': 'Sea of Thieves',
    '帝国时代4': 'Age of Empires 4',
    '刺客信条': "Assassin's Creed",
    '巅峰战舰': 'Naval Artillery',
    '小游戏': 'Mini Games',
    '港诡实录': 'HK Horror',
    '双人成行': 'It Takes Two',
    '过山车之星': 'Planet Coaster',
    'FF14': 'FFXIV',
    '全球使命3': 'Global Mission 3',
    '足球小将': 'Captain Tsubasa',
    '极品飞车：集结': 'NFS: Heat',
    '生死狙击': 'Sniper Strike',
    '命运方舟': 'Lost Ark',
    '俄罗斯钓鱼4': 'Russian Fishing 4',
    '格斗游戏': 'Fighting',
    '决斗之城': 'Duel City',
    '坦克大战': 'Tank Battle',
    '环世界': 'RimWorld',
    '完美世界：诸神之战': 'Perfect World',
    '博德之门3': "Baldur's Gate 3",
    '征途': 'Zhengtu',
    '战锤40K：暗潮': 'WH40K: Darktide',
    '天龙八部2': 'Demi-Gods 2',
    '疾风之刃': 'Blade Storm',
    '斗战神': 'War of Gods',
    '泡泡堂': 'BnB',
    '看门狗：军团': 'Watch Dogs Legion',
    'KARDS': 'KARDS',
    '逃生：试炼': 'Escape: Trial',
    '荒野乱斗': 'Brawl Stars',
    '未来之役': 'Future War',
    '英灵神殿': 'Spirit Temple',
    '奶块': 'Milk Block',
    '最终幻想系列': 'Final Fantasy',
    '重返帝国': 'Return to Empire',
    '战意': "Conqueror's Blade",
    '使命召唤系列': 'Call of Duty',
    '虎牙全明星': 'Huya All-Stars',
    '球球大作战': 'Ball Battle',
    '御龙在天': 'Dragon in Sky',
    '问道': 'Ask the Way',
    '诛仙3': 'Zhu Xian 3',
    '战争冲突': 'War',
    '影之刃零': 'Shadow Blade Zero',
    '英雄联盟电竞经理': 'LoL Esports Manager',
    '冒险岛：枫之传说': 'MapleStory',
    '狼人杀手游': 'Werewolf Mobile',
    '洛克手游': 'Roco Mobile',
    '神武4电脑版': 'Shenwu 4',
    'KPL年度总决赛': 'KPL Finals',
    '斯诺克': 'Snooker',
    'IEM北京2026': 'IEM Beijing 2026',
    '2026英雄联盟全球总决赛': 'LoL Worlds 2026',
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


# ─── Step 1: Ambil SEMUA rooms (iGid=0 = ALL) ────────────────────
def get_all_rooms(max_rooms=MAX_ROOMS_TOTAL):
    """
    Ambil rooms dari live.huya.com — iGid=0 = SEMUA kategori.
    Paginate: iPageNo=1,2,3... sampai max_rooms tercapai.
    Setiap room sudah punya: sNick, lProfileRoom, sGameFullName, sScreenshot, sAvatar180
    """
    s = get_session()
    all_rooms = []
    page = 1

    while len(all_rooms) < max_rooms:
        api = (
            f"https://live.huya.com/liveHttpUI/getLiveList"
            f"?iGid=0&iPageNo={page}&iPageSize={PAGE_SIZE}"
        )
        try:
            r = s.get(api, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception:
            break

        vlist = data.get('vList', [])
        if not vlist:
            break

        for item in vlist:
            room_id = str(item.get('lProfileRoom', ''))
            if not room_id:
                continue

            # Logo: screenshot (thumbnail stream) atau avatar
            logo = item.get('sScreenshot', '') or item.get('sAvatar180', '')

            all_rooms.append({
                'id': room_id,
                'nick': item.get('sNick', ''),
                'introduction': item.get('sIntroduction', ''),
                'screenshot': logo,
                'gameName': item.get('sGameFullName', ''),  # ← group-title
                'viewers': item.get('lTotalCount', 0),
            })

        print(f"  Page {page}: +{len(vlist)} rooms (total: {len(all_rooms)})")
        page += 1
        time.sleep(REQUEST_DELAY)

        if len(vlist) < PAGE_SIZE:
            break  # sudah habis

    return all_rooms[:max_rooms]


# ─── Step 2: Fallback — www.huya.com/cache.php ───────────────────
def get_rooms_fallback(max_rooms=MAX_ROOMS_TOTAL):
    """Fallback jika live.huya.com gagal."""
    s = get_session()
    all_rooms = []

    # Ambil kategori
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    categories = []
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            for item in data.get('data', []):
                gid = item.get('gameId') or item.get('id')
                gname = item.get('gameName') or item.get('name', '')
                if gid and gname:
                    categories.append({'id': int(gid), 'name': gname})
    except Exception:
        pass

    if not categories:
        categories = [
            {'id': 2135, 'name': '英雄联盟'},
            {'id': 1052, 'name': 'CS2'},
            {'id': 1141, 'name': '无畏契约'},
            {'id': 2067, 'name': '体育'},
            {'id': 2068, 'name': '户外'},
            {'id': 2134, 'name': '星秀'},
            {'id': 1058, 'name': '天天吃鸡'},
            {'id': 2136, 'name': '主机游戏'},
            {'id': 1059, 'name': 'DOTA2'},
            {'id': 2137, 'name': '王者荣耀'},
            {'id': 1056, 'name': '和平精英'},
            {'id': 2138, 'name': '金铲铲之战'},
            {'id': 1220, 'name': '三角洲行动'},
        ]

    per_cat = max(10, max_rooms // len(categories))

    for cat in categories:
        if len(all_rooms) >= max_rooms:
            break
        api2 = (
            f"https://www.huya.com/cache.php"
            f"?m=LiveList&do=getLiveListByPage"
            f"&gameId={cat['id']}&tagAll=0&page=1"
        )
        try:
            r = s.get(api2, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        datas = data.get('data', {}).get('datas', [])
        for item in datas[:per_cat]:
            room_id = str(item.get('profileRoom', ''))
            if not room_id:
                continue
            all_rooms.append({
                'id': room_id,
                'nick': item.get('nick', ''),
                'introduction': item.get('introduction', ''),
                'screenshot': item.get('screenshot', ''),
                'gameName': item.get('gameName', '') or cat['name'],
                'viewers': item.get('totalCount', 0),
            })
        time.sleep(REQUEST_DELAY)

    return all_rooms[:max_rooms]


# ─── Step 3: Esports hot matches ─────────────────────────────────
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


# ─── Step 4: Decode liveLineUrl → stream URL ─────────────────────
def decode_live_url(liveline):
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


# ─── Step 5: Ambil stream info per room ──────────────────────────
def get_stream_info(room_id):
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
                        return stream_url.replace('http://', 'https://')
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

                    flv_list = stream.get('flv', {}).get('multiLine', [])
                    if flv_list:
                        for line in reversed(flv_list):
                            url = line.get('url', '')
                            if url:
                                stream_url = url.replace('http://', 'https://')
                                break

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
                        return stream_url
    except Exception:
        pass

    # ═══ METODE 3: www.huya.com ═══
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

            if stream_url:
                return stream_url
    except Exception:
        pass

    return None


# ─── Step 6: Worker ──────────────────────────────────────────────
def fetch_stream_worker(args):
    idx, room = args
    room_id = room['id']
    try:
        stream_url = get_stream_info(room_id)
    except Exception as e:
        return (idx, room, None, str(e))
    return (idx, room, stream_url, None)


# ─── Step 7: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_rooms, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  HUYA M3U PLAYLIST GENERATOR (FINAL)")
    print(f"  {max_rooms} rooms | {max_workers} threads")
    print("=" * 55)

    # Step 1: Ambil semua rooms (iGid=0 = ALL)
    print("\n[1/3] Mengambil rooms (live.huya.com, iGid=0)...")
    all_rooms = get_all_rooms(max_rooms)

    # Fallback jika live.huya.com gagal
    if not all_rooms:
        print("  [WARN] live.huya.com gagal, mencoba fallback...")
        all_rooms = get_rooms_fallback(max_rooms)

    # Tambah esports
    print("\n  [ESPORTS] Mengambil hot matches...")
    esports_rooms = get_hot_matches()
    existing_ids = {r['id'] for r in all_rooms}
    for room in esports_rooms:
        if room['id'] not in existing_ids:
            all_rooms.append(room)
            existing_ids.add(room['id'])
    print(f"  Esports: +{len(esports_rooms)} rooms")

    print(f"\n  Total rooms: {len(all_rooms)}")

    if not all_rooms:
        print("\n  [ERROR] Tidak ada rooms! IP mungkin di-ban.")
        return

    # Step 2: Stream URL (PARALEL)
    print(f"\n[2/3] Mengambil stream URL ({max_workers} threads)...")
    results = [None] * len(all_rooms)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_stream_worker, (i, room)): i
            for i, room in enumerate(all_rooms)
        }

        done_count = 0
        for future in as_completed(futures):
            idx, room, stream_url, error = future.result()
            results[idx] = (room, stream_url, error)
            done_count += 1
            if done_count % 100 == 0:
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

        name = translate_text(room['nick'] or room['introduction'] or f"Huya {room['id']}")
        group_title = translate_text(room.get('gameName', '') or 'Huya')
        logo = room.get('screenshot', '')

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
        description='Huya M3U Playlist Generator (Final)')
    parser.add_argument('--output', '-o', default='playlist.m3u',
                        help='File output M3U (default: playlist.m3u)')
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
