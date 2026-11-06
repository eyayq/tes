#!/usr/bin/env python3
"""
HUYA M3U PLAYLIST GENERATOR — FINAL COMPLETE
- HYBRID: requests (listing) + curl_cffi (scrape)
- Warm-up session (cookies) untuk bypass anti-bot
- Hardcoded 150+ kategori sebagai fallback
- ThreadPoolExecutor (parallel stream fetching)
- tvg-logo + group-title otomatis
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

import requests as std_requests
from curl_cffi import requests as cffi_requests

# ─── Konfigurasi ─────────────────────────────────────────────────
MAX_ROOMS_PER_CATEGORY = 10
REQUEST_DELAY = 5
TIMEOUT = 15
MAX_WORKERS = 8

_local = threading.local()


# ─── Session (Hybrid + Warm-up) ─────────────────────────────────
def get_listing_session():
    """Untuk API listing — requests + warm-up cookies."""
    if not hasattr(_local, 'listing_session'):
        s = std_requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.huya.com/",
            "X-Requested-With": "XMLHttpRequest",
        })
        # Warm-up: ambil cookies dari homepage
        try:
            s.get("https://www.huya.com/", timeout=TIMEOUT, allow_redirects=True)
        except Exception:
            pass
        _local.listing_session = s
    return _local.listing_session


def get_scrape_session():
    """Untuk scrape room page — curl_cffi + warm-up."""
    if not hasattr(_local, 'scrape_session'):
        s = cffi_requests.Session(impersonate="chrome")
        # Warm-up: ambil cookies dari homepage
        try:
            s.get("https://www.huya.com/", timeout=TIMEOUT)
        except Exception:
            pass
        _local.scrape_session = s
    return _local.scrape_session


# ─── Hardcoded ALL Categories (150+) ────────────────────────────
ALL_CATEGORIES = [
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
    {'id': 1053, 'name': 'Apex英雄'},
    {'id': 1120, 'name': '我的世界'},
    {'id': 1057, 'name': '失控进化-RUST'},
    {'id': 1055, 'name': '炉石传说'},
    {'id': 1054, 'name': '穿越火线'},
    {'id': 1060, 'name': '魔兽世界'},
    {'id': 1061, 'name': '坦克世界'},
    {'id': 1062, 'name': '守望先锋'},
    {'id': 1063, 'name': '地下城与勇士'},
    {'id': 1064, 'name': '冒险岛'},
    {'id': 1065, 'name': '剑灵'},
    {'id': 1066, 'name': '永恒之塔'},
    {'id': 1067, 'name': '黑色沙漠'},
    {'id': 1068, 'name': '天堂'},
    {'id': 1069, 'name': 'FF14'},
    {'id': 1070, 'name': '命运方舟'},
    {'id': 1071, 'name': '博德之门3'},
    {'id': 1072, 'name': '艾尔登法环'},
    {'id': 1073, 'name': '只狼：影逝二度'},
    {'id': 1074, 'name': '黑神话悟空'},
    {'id': 1075, 'name': '逃离塔科夫'},
    {'id': 1076, 'name': '彩虹六号'},
    {'id': 1077, 'name': '战争雷霆'},
    {'id': 1078, 'name': '星际争霸'},
    {'id': 1079, 'name': '魔兽争霸3'},
    {'id': 1080, 'name': '拳皇15'},
    {'id': 1081, 'name': '格斗游戏'},
    {'id': 1082, 'name': '恐怖游戏'},
    {'id': 1083, 'name': '小游戏'},
    {'id': 1084, 'name': '桌游'},
    {'id': 1085, 'name': '音乐'},
    {'id': 1086, 'name': '美食'},
    {'id': 1087, 'name': '旅游'},
    {'id': 1088, 'name': '科技'},
    {'id': 1089, 'name': '二次元'},
    {'id': 1090, 'name': '原创'},
    {'id': 1091, 'name': '交友'},
    {'id': 1092, 'name': '颜值'},
    {'id': 1093, 'name': '虚拟偶像'},
    {'id': 1094, 'name': '一起看'},
    {'id': 1095, 'name': '上分'},
    {'id': 1096, 'name': '怀旧游戏'},
    {'id': 1097, 'name': '军事游戏'},
    {'id': 1098, 'name': '动作游戏'},
    {'id': 1099, 'name': 'MMORPG'},
    {'id': 1100, 'name': '射击综合游戏'},
    {'id': 1101, 'name': '综合手游'},
    {'id': 1102, 'name': '二次元手游'},
    {'id': 1103, 'name': '棋牌休闲'},
    {'id': 1104, 'name': 'CF手游'},
    {'id': 1105, 'name': 'LOL手游'},
    {'id': 1106, 'name': '原神'},
    {'id': 1107, 'name': '第五人格'},
    {'id': 1108, 'name': '蛋仔派对'},
    {'id': 1109, 'name': '崩坏：星穹铁道'},
    {'id': 1110, 'name': '鸣潮'},
    {'id': 1111, 'name': '绝区零'},
    {'id': 1112, 'name': '暗区突围'},
    {'id': 1113, 'name': '永劫无间'},
    {'id': 1114, 'name': '幻塔'},
    {'id': 1115, 'name': '明日之后'},
    {'id': 1116, 'name': '球球大作战'},
    {'id': 1117, 'name': '荒野乱斗'},
    {'id': 1118, 'name': '皇室战争'},
    {'id': 1119, 'name': '部落冲突'},
    {'id': 1121, 'name': '罗布乐思'},
    {'id': 1122, 'name': '迷你世界'},
    {'id': 1123, 'name': '植物大战僵尸'},
    {'id': 1124, 'name': '鹅鸭杀'},
    {'id': 1125, 'name': '恐鬼症'},
    {'id': 1126, 'name': '森林之子'},
    {'id': 1127, 'name': '方舟'},
    {'id': 1128, 'name': '全面战争'},
    {'id': 1129, 'name': '帝国时代4'},
    {'id': 1130, 'name': '极限竞速：地平线'},
    {'id': 1131, 'name': '刺客信条'},
    {'id': 1132, 'name': '看门狗：军团'},
    {'id': 1133, 'name': '最终幻想系列'},
    {'id': 1134, 'name': 'NBA2KOL系列'},
    {'id': 1135, 'name': '体育游戏'},
    {'id': 1136, 'name': '中国象棋'},
    {'id': 1137, 'name': '欢乐斗地主'},
    {'id': 1138, 'name': 'JJ斗地主'},
    {'id': 1139, 'name': '欢乐麻将'},
    {'id': 1140, 'name': '三国杀'},
    {'id': 1142, 'name': 'QQ飞车'},
    {'id': 1143, 'name': 'QQ飞车手游'},
    {'id': 1144, 'name': 'LOL云顶之弈'},
    {'id': 1145, 'name': '炉石战棋'},
    {'id': 1146, 'name': '彩虹岛Online'},
    {'id': 1147, 'name': '英魂之刃'},
    {'id': 1148, 'name': '梦三国'},
    {'id': 1149, 'name': '天翼决'},
    {'id': 1150, 'name': '全球使命'},
    {'id': 1151, 'name': '洛克王国：世界'},
    {'id': 1152, 'name': '热血江湖'},
    {'id': 1153, 'name': '天龙八部2'},
    {'id': 1154, 'name': '完美世界：诸神之战'},
    {'id': 1155, 'name': '诛仙世界'},
    {'id': 1156, 'name': '征途'},
    {'id': 1157, 'name': '御龙在天'},
    {'id': 1158, 'name': '疾风之刃'},
    {'id': 1159, 'name': '斗战神'},
    {'id': 1160, 'name': '泡泡堂'},
    {'id': 1161, 'name': '生死狙击'},
    {'id': 1162, 'name': '逆战'},
    {'id': 1163, 'name': 'CFHD'},
    {'id': 1164, 'name': 'COD手游'},
    {'id': 1165, 'name': '火影忍者手游'},
    {'id': 1166, 'name': '王者万象棋'},
    {'id': 1167, 'name': '摸了个鱼'},
    {'id': 1168, 'name': '台球国度'},
    {'id': 1169, 'name': '指尖四川麻将'},
    {'id': 1170, 'name': '天天象棋'},
    {'id': 1171, 'name': '海底大作战'},
    {'id': 1172, 'name': '俄罗斯钓鱼4'},
    {'id': 1173, 'name': '环世界'},
    {'id': 1174, 'name': '过山车之星'},
    {'id': 1175, 'name': '双人成行'},
    {'id': 1176, 'name': '港诡实录'},
    {'id': 1177, 'name': '死亡之夜'},
    {'id': 1178, 'name': '甜蜜之家'},
    {'id': 1179, 'name': '异人之下'},
    {'id': 1180, 'name': '风云'},
    {'id': 1181, 'name': '盗贼之海'},
    {'id': 1182, 'name': '巅峰战舰'},
    {'id': 1183, 'name': '战意'},
    {'id': 1184, 'name': '使命召唤系列'},
    {'id': 1185, 'name': '虎牙全明星'},
    {'id': 1186, 'name': '矩阵：零日危机'},
    {'id': 1187, 'name': '怪物猎人：荒野'},
    {'id': 1188, 'name': '育碧游戏'},
    {'id': 1189, 'name': '足球小将'},
    {'id': 1190, 'name': '极品飞车：集结'},
    {'id': 1191, 'name': '魔法门之英雄无敌系列'},
    {'id': 1192, 'name': '决斗之城'},
    {'id': 1193, 'name': '坦克大战'},
    {'id': 1194, 'name': '归家异途'},
    {'id': 1195, 'name': '全球使命3'},
    {'id': 1196, 'name': 'KARDS'},
    {'id': 1197, 'name': '逃生：试炼'},
    {'id': 1198, 'name': '命运扳机'},
    {'id': 1199, 'name': '未来之役'},
    {'id': 1200, 'name': '英灵神殿'},
    {'id': 1201, 'name': '奶块'},
    {'id': 1202, 'name': '重返帝国'},
    {'id': 1203, 'name': '战锤40K：暗潮'},
    {'id': 1204, 'name': '最终幻想：起源'},
    {'id': 1205, 'name': '洛克手游'},
    {'id': 1206, 'name': '神武4电脑版'},
    {'id': 1207, 'name': '暗黑破坏神'},
    {'id': 1208, 'name': '最终幻想7：重制版'},
    {'id': 1209, 'name': '影之刃零'},
    {'id': 1210, 'name': '英雄联盟手游'},
    {'id': 1211, 'name': '英雄联盟电竞经理'},
    {'id': 1212, 'name': '冒险岛：枫之传说'},
    {'id': 1213, 'name': '暗区突围：无限'},
    {'id': 1214, 'name': '海贼王 寻秘世界'},
    {'id': 1215, 'name': '永劫无间手游'},
    {'id': 1216, 'name': '狼人杀手游'},
    {'id': 1217, 'name': '问道'},
    {'id': 1218, 'name': '诛仙3'},
    {'id': 1219, 'name': '战争冲突'},
    {'id': 1220, 'name': '三角洲行动'},
]


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
    'DOTA1': 'Dota 1',
    '坦克世界': 'World of Tanks',
    '守望先锋': 'Overwatch',
    '天天吃鸡': 'PUBG',
    '主机游戏': 'Console',
    '我的世界': 'Minecraft',
    '失控进化-RUST': 'Rust',
    '失控进化-Rust': 'Rust',
    '植物大战僵尸': 'Plants vs Zombies',
    '部落：上升': 'Clash of Clans',
    '彩虹六号': 'Rainbow Six',
    '黑神话悟空': 'Black Myth: Wukong',
    '黑神话：悟空': 'Black Myth: Wukong',
    '逃离塔科夫': 'Escape from Tarkov',
    '恐怖游戏': 'Horror',
    '互动点播': 'Interactive',
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
    '棋牌综合': 'Board/Card Games',
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
    '影之刃零': 'Shadow Blade Zero',
    'MMORPG': 'MMORPG',
    '射击综合游戏': 'Shooter',
    '英雄联盟手游': 'LoL Mobile',
    '动作游戏': 'Action',
    '地下城与勇士': 'DNF',
    '冒险岛': 'MapleStory',
    '军事游戏': 'Military',
    '综合手游': 'Mobile General',
    '永劫无间': 'Naraka',
    '传奇': 'Legend',
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
    '问道': 'Ask the Way',
    '狼人杀手游': 'Werewolf Mobile',
    '科技': 'Tech',
    '欢乐麻将': 'Happy Mahjong',
    '诛仙3': 'Zhu Xian 3',
    '战争冲突': 'War',
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
    '海底大作战': 'Sea Battle',
    '骑马与砍杀系列': 'Mount & Blade',
    '极限竞速：地平线': 'Forza Horizon',
    '鹅鸭杀': 'Goose Goose Duck',
    '永恒之塔': 'Aion',
    '暗区突围：无限': 'Arena Breakout: Infinite',
    '海贼王 寻秘世界': 'One Piece',
    'Apex英雄': 'Apex Legends',
    '怪物猎人：荒野': 'Monster Hunter Wilds',
    '育碧游戏': 'Ubisoft',
    '全面战争': 'Total War',
    '指尖四川麻将': 'Sichuan Mahjong',
    '体育游戏': 'Sports Games',
    '诛仙世界': 'Zhu Xian World',
    '明日之后': 'LifeAfter',
    '黑色沙漠': 'Black Desert',
    '森林之子': 'Sons of the Forest',
    '天堂': 'Paradise',
    '英雄杀': 'Hero Kill',
    '战争雷霆': 'War Thunder',
    '绝区零': 'Zenless Zone Zero',
    '星空': 'Starfield',
    '鬼谷八荒': 'Ghost Bladestrike',
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
    '归家异途': 'Home',
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
    '最终幻想：起源': 'FF: Origin',
    '天龙八部2': 'Demi-Gods 2',
    '疾风之刃': 'Blade Storm',
    '斗战神': 'War of Gods',
    '泡泡堂': 'BnB',
    '看门狗：军团': 'Watch Dogs Legion',
    '创世战车': 'Crossout',
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
    # Esports events
    'KPL年度总决赛': 'KPL Finals',
    '斯诺克': 'Snooker',
    'IEM北京2026': 'IEM Beijing 2026',
    '2026英雄联盟全球总决赛': 'LoL Worlds 2026',
    '2026QQ飞车手游S联赛秋季赛': 'QQ Speed S League',
    '2026NBPL秋季赛': 'NBPL Fall 2026',
    '第四届东亚电竞锦标赛': 'ECEA',
    '2026虎牙斗象杯': 'Huya Douxiang Cup',
    '2026CFML秋季赛': 'CFML Fall 2026',
    '2026王者荣耀中王杯': 'HoK Mid-King Cup',
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


# ─── Step 1: Ambil SEMUA kategori ────────────────────────────────
def get_all_categories():
    s = get_listing_session()
    cs = get_scrape_session()

    # Metode 1: Scrape /g dengan curl_cffi
    try:
        r = cs.get("https://www.huya.com/g", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text
            seen = set()
            categories = []
            for m in re.finditer(r'href="/g/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', html):
                gid = int(m.group(1))
                gname = m.group(2).strip()
                if gid not in seen and gname and len(gname) > 1:
                    seen.add(gid)
                    categories.append({'id': gid, 'name': gname})
            if len(categories) > 10:
                return categories
    except Exception:
        pass

    # Metode 2: Scrape /g dengan requests
    try:
        r = s.get("https://www.huya.com/g", timeout=TIMEOUT)
        if r.status_code == 200:
            html = r.text
            seen = set()
            categories = []
            for m in re.finditer(r'href="/g/(\d+)"[^>]*>\s*([^<]+?)\s*</a>', html):
                gid = int(m.group(1))
                gname = m.group(2).strip()
                if gid not in seen and gname and len(gname) > 1:
                    seen.add(gid)
                    categories.append({'id': gid, 'name': gname})
            if len(categories) > 10:
                return categories
    except Exception:
        pass

    # Metode 3: API getLiveGameList
    api = "https://www.huya.com/cache.php?m=LiveList&do=getLiveGameList"
    try:
        r = s.get(api, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            categories = []
            for item in data.get('data', []):
                gid = item.get('gameId') or item.get('id')
                gname = item.get('gameName') or item.get('name', '')
                if gid and gname:
                    categories.append({'id': int(gid), 'name': gname})
            if len(categories) > 10:
                return categories
    except Exception:
        pass

    # Metode 4: Hardcoded ALL
    return ALL_CATEGORIES


# ─── Step 2: Ambil rooms per kategori ────────────────────────────
def get_rooms(category_id, max_rooms=MAX_ROOMS_PER_CATEGORY):
    s = get_listing_session()
    cs = get_scrape_session()
    rooms = []
    page = 1

    while len(rooms) < max_rooms:
        api = (
            f"https://www.huya.com/cache.php"
            f"?m=LiveList&do=getLiveListByPage"
            f"&gameId={category_id}&tagAll=0&page={page}"
        )

        # Coba requests dulu
        data = None
        try:
            r = s.get(api, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
        except Exception:
            pass

        # Fallback: curl_cffi
        if not data or not data.get('data', {}).get('datas'):
            try:
                r = cs.get(api, timeout=TIMEOUT)
                if r.status_code == 200:
                    data = r.json()
            except Exception:
                break

        if not data:
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


# ─── Step 2b: Esports/sports hot matches ─────────────────────────
def get_hot_matches():
    s = get_listing_session()
    cs = get_scrape_session()
    api = "https://www.huya.com/cache.php?callback=huyaNavHotMatch&do=ajaxGetHotMatch&m=Nav"

    data = None
    # Coba requests
    try:
        r = s.get(api, timeout=TIMEOUT)
        text = r.text
        if '(' in text and ')' in text:
            start = text.index('(') + 1
            end = text.rindex(')')
            data = json.loads(text[start:end])
    except Exception:
        pass

    # Fallback: curl_cffi
    if not data:
        try:
            r = cs.get(api, timeout=TIMEOUT)
            text = r.text
            if '(' in text and ')' in text:
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


# ─── Step 4: Ambil stream info per room (curl_cffi) ──────────────
def get_stream_info(room_id):
    s = get_scrape_session()

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
    idx, room, cat_name = args
    room_id = room['id']
    try:
        info = get_stream_info(room_id)
    except Exception as e:
        return (idx, room, cat_name, None, str(e))
    return (idx, room, cat_name, info, None)


# ─── Step 6: Generate playlist ───────────────────────────────────
def generate_playlist(output_file, max_per_cat=MAX_ROOMS_PER_CATEGORY,
                      categories_filter=None, max_workers=MAX_WORKERS):
    print("=" * 55)
    print("  HUYA M3U PLAYLIST GENERATOR (FINAL)")
    print("  Hybrid + Warm-up + Hardcoded Fallback")
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
        if (i + 1) % 20 == 0 or i == len(categories) - 1:
            print(f"  Progress: {i+1}/{len(categories)} | total rooms: {len(all_rooms)}")
        time.sleep(REQUEST_DELAY)

    # Step 2b: Esports
    print("\n  [ESPORTS] Mengambil hot matches...")
    esports_rooms = get_hot_matches()
    for room in esports_rooms:
        all_rooms.append((room, 'Esports/Sports'))
    print(f"  Esports/Sports         → {len(esports_rooms)} rooms")

    print(f"\n  Total rooms: {len(all_rooms)}")

    if not all_rooms:
        print("\n  [ERROR] Tidak ada rooms ditemukan!")
        print("  Kemungkinan IP di-block. Coba:")
        print("    1. Restart modem (ganti IP)")
        print("    2. Jalankan di GitHub Actions (IP berbeda)")
        print("    3. Tunggu beberapa jam")
        return

    # Step 3: Stream URL (PARALEL)
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
            if done_count % 50 == 0:
                print(f"  Progress: {done_count}/{len(all_rooms)}")

    # Step 4: Build playlist + translate
    print(f"\n[4/4] Membangun playlist + translate...")
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

        name = translate_text(info['name'])
        group_title = translate_text(
            room.get('gameName', '') or info.get('game', '') or cat_name
        )
        logo = info.get('logo', '') or room.get('screenshot', '')

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
        description='Huya M3U Playlist Generator (Final — Hybrid)')
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
    parser.add_argument('--no-translate', action='store_true',
                        help='Nonaktifkan terjemahan (lebih cepat)')
    args = parser.parse_args()

    if args.no_translate:
        translate_text = lambda x: x or ''

    REQUEST_DELAY = args.delay
    generate_playlist(
        output_file=args.output,
        max_per_cat=args.max_per_cat,
        categories_filter=set(args.categories) if args.categories else None,
        max_workers=args.workers,
    )   
