import requests
import time
import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://www.dens.tv/api/dens3/"
OUTPUT_FILE = "playlist.m3u"
DELAY = 0.3
MAX_WORKERS = 8

# HTTP Headers untuk streaming
STREAM_UA = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36'
STREAM_REFERER = 'https://www.dens.tv/'

# Tanggal untuk EPG (default: hari ini)
EPG_DATE = date.today().strftime("%Y-%m-%d")

# Kategori channel
CATEGORIES = {
    "1": "Local",
    "2": "Premium",
    "3": "International",
    "13": "Free Streaming",
}

# Genre VOD (keyword_id, slug, name)
VOD_GENRES = [
    ("5", "drama", "Drama"),
    ("7", "horror-thriller", "Horror & Thriller"),
    ("8", "action", "Action"),
    ("56", "comedy", "Comedy"),
    ("4559", "others", "Others"),
    ("1118", "sports-and-hobbies", "Sports and Hobbies"),
    ("5754", "cooking-show", "Cooking Show"),
    ("5756", "music", "Music"),
    ("5764", "lifestyle-travels", "Lifestyle & Travels"),
    ("5501", "cerita-indonesia", "Cerita Indonesia"),
]

# ============================================================
# SESSION
# ============================================================
session = requests.Session()

session.cookies.update({
    'dtv_atl': '14325',
    'dtv_adt': '1794511834',
    'dens_session': 'o393l30kndtbn6oirvfb2vqolpeoa2m1',
    'tvCategories': 'W3siaWQiOiIxIiwidGl0bGUiOiJMb2NhbCIsInNsdWciOiJsb2NhbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjIiLCJ0aXRsZSI6IlByZW1pdW0iLCJzbHVnIjoicHJlbWl1bSIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjMiLCJ0aXRsZSI6IkludGVybmF0aW9uYWwiLCJzbHVnIjoiaW50ZXJuYXRpb25hbCIsInR5cGUiOiJMaXZlIFRWIn0seyJpZCI6IjEzIiwidGl0bGUiOiJGcmVlIFN0cmVhbWluZyIsInNsdWciOiJmcmVlLXN0cmVhbWluZyIsInR5cGUiOiJMaXZlIFRWIn1d',
    'dtv_hrls': '713a42fe9b1e51d3f06039055cb60023',
    'dtv_rls': 'eyJyZXBlYXQiOnRydWUsImZyZWVfZGF5cyI6MywiZnJlZV9ob3VycyI6NCwiY2F0Y2h1cF9hY2Nlc3MiOnRydWV9',
    'dtv_msg': 'eyJzaWduX2luIjoiWW91ciBGcmVlIEV4cGVyaWVuY2UgRXhwaXJlZCAtIFBsZWFzZSBTaWduSW4iLCJzdWJzY3JpcHRpb24iOiJZb3VyIEZyZWUgRXhwZXJpZW5jZSBFeHBpcmVkIC0gUGxlYXNlIFN1YmNyaWJlIiwiZXhwaXJlZF9jYXRjaHVwIjoiQ2F0Y2h1cCBFeHBpcmVkIn0%3D',
    'dtv_grf': 'guest_563d281270327285',
    'dtv_axp': '2026-11-15T05%3A37%3A30%2B07%3A00',
    'dtv_alm': '14400',
    'last_visit': 'https%3A%2F%2Fwww.dens.tv%2F',
})

session.headers.update({
    'User-Agent': STREAM_UA,
    'Accept': '*/*',
    'Referer': 'https://www.dens.tv/',
    'Accept-Language': 'id-ID,id;q=0.7',
})


# ============================================================
# API CALLS
# ============================================================
def fetch_channels_by_category(category_id):
    """Fetch channel list per kategori"""
    url = f"{BASE_URL}tv/TvChannels/listByCategory?id_category={category_id}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_home_sections(page=1, limit=10):
    """Fetch HomeSections (untuk catchup & VOD)"""
    url = f"{BASE_URL}HomeSections?page={page}&limit={limit}&app_type=10"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_epg_by_date(channel_id, ep_date):
    """Fetch EPG untuk channel tertentu"""
    url = f"{BASE_URL}tv/TvChannels/listEpgByDate?date={ep_date}&id_channel={channel_id}&app_type=10"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_movie_related(keyword_id, slug, page=1, limit=50):
    """Fetch movie related by genre (paginated)"""
    url = f"https://www.dens.tv/movie/related/{keyword_id}/{slug}?page={page}&limit={limit}&json=true"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# M3U BUILDER
# ============================================================
def esc(val):
    """Escape untuk M3U attribute"""
    if not val:
        return ""
    return str(val).replace('"', "'").replace("\n", " ").strip()


def build_m3u(entries):
    lines = ["#EXTM3U"]
    for e in entries:
        if not e.get("stream_url"):
            continue
        group = esc(e.get("group_title", ""))
        name = esc(e.get("tvg_name", ""))
        logo = esc(e.get("tvg_logo", ""))
        stream_url = e["stream_url"]

        lines.append(f'#EXTINF:-1 group-title="{group}" tvg-name="{name}" tvg-logo="{logo}",{name}')
        lines.append(f'#EXTVLCOPT:http-user-agent={STREAM_UA}')
        lines.append(f'#EXTVLCOPT:http-referrer={STREAM_REFERER}')
        lines.append(stream_url)
    return "\n".join(lines) + "\n"


# ============================================================
# WORKER FUNCTIONS (untuk ThreadPool)
# ============================================================
def worker_fetch_channel_category(cat_id, cat_name):
    """Worker: fetch channel per kategori"""
    try:
        data = fetch_channels_by_category(cat_id)
        if data.get("success"):
            return cat_name, data["data"]["contents"]
    except Exception as ex:
        print(f"    [!] {cat_name}: {ex}")
    return cat_name, []


def worker_fetch_epg(ch_info):
    """Worker: fetch EPG per channel"""
    ch_id = ch_info["id"]
    try:
        data = fetch_epg_by_date(ch_id, EPG_DATE)
        if data.get("success") and data.get("data"):
            return ch_info, data["data"]
    except Exception:
        pass
    return ch_info, []


def worker_fetch_genre_first_page(genre):
    """Worker: fetch page 1 untuk genre tertentu (untuk tahu total pages)"""
    kid, slug, name = genre
    try:
        data = fetch_movie_related(kid, slug, page=1, limit=50)
        if data.get("success"):
            return genre, data["data"]
    except Exception as ex:
        print(f"    [!] Genre {name}: {ex}")
    return genre, None


def worker_fetch_genre_page(args):
    """Worker: fetch halaman tertentu untuk genre"""
    kid, slug, name, page = args
    try:
        data = fetch_movie_related(kid, slug, page=page, limit=50)
        if data.get("success"):
            return (kid, slug, name), data["data"]
    except Exception as ex:
        print(f"    [!] Genre {name} page {page}: {ex}")
    return (kid, slug, name), None


# ============================================================
# PROCESS MOVIE DATA
# ============================================================
# Genre yang punya group-title sendiri
PRIORITY_GENRES = {"Cerita Indonesia", "Drama", "Horror & Thriller"}

def _process_movie_list(movies, genre_name, entries, seen_ids):
    """Proses list movie, tambahkan ke entries (dedup by movie_id)"""
    # Tentukan group-title
    if genre_name in PRIORITY_GENRES:
        group = f"VOD - {genre_name}"
    else:
        group = "VOD - Other"

    for item in movies:
        movie_id = item.get("movie_id", "")
        title = item.get("title", "")
        play_url = item.get("extra", {}).get("stream", {}).get("play_url", "")
        logo = item.get("url_handle", {}).get("img_land_large", "") or item.get("url_handle", {}).get("img_port_large", "")

        if play_url and movie_id not in seen_ids:
            seen_ids.add(movie_id)
            entries.append({
                "group_title": group,
                "tvg_name": title,
                "tvg_logo": logo,
                "stream_url": play_url,
            })   


# ============================================================
# MAIN
# ============================================================
def scrape():
    entries = []
    all_channels = []
    seen_movie_ids = set()

    # ----------------------------------------------------------
    # 1. LIVE TV — dari TvChannels/listByCategory (PARALLEL)
    # ----------------------------------------------------------
    print("[*] Fetching Live TV channels (parallel)...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(worker_fetch_channel_category, cid, cname): (cid, cname)
            for cid, cname in CATEGORIES.items()
        }
        for future in as_completed(futures):
            cat_name, contents = future.result()
            count = 0
            for item in contents:
                meta = item.get("meta", {})
                rules = item.get("rules", {})

                title = meta.get("title", "")
                logo = meta.get("images", {}).get("default", "")
                stream_url = meta.get("videos", {}).get("live", "")
                ch_id = meta.get("id", "")

                if stream_url:
                    entries.append({
                        "group_title": cat_name,
                        "tvg_name": title,
                        "tvg_logo": logo,
                        "stream_url": stream_url,
                    })
                    count += 1

                all_channels.append({
                    "id": ch_id,
                    "title": title,
                    "logo": logo,
                    "category": cat_name,
                    "catchup_enable": rules.get("catchup", {}).get("enable", 0),
                })

            print(f"    \u2713 {cat_name}: {count} channels")

    # ----------------------------------------------------------
    # 2. EPG — dari TvChannels/listEpgByDate (PARALLEL)
    # ----------------------------------------------------------
    print(f"[*] Fetching EPG date: {EPG_DATE} (parallel)...")
    epg_channels = [ch for ch in all_channels if ch["catchup_enable"]]
    epg_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(worker_fetch_epg, ch): ch
            for ch in epg_channels
        }
        for future in as_completed(futures):
            ch_info, programs = future.result()
            for prog in programs:
                title = prog.get("title", "")
                play_url = prog.get("play", "")
                start_end = prog.get("start_end", "")

                if play_url:
                    entries.append({
                        "group_title": "epg even",
                        "tvg_name": f"{title} [{start_end}]",
                        "tvg_logo": ch_info["logo"],
                        "stream_url": play_url,
                    })
                    epg_count += 1

    print(f"    \u2713 {epg_count} EPG programs from {len(epg_channels)} channels")

    # ----------------------------------------------------------
    # 3. CATCHUP & VOD — dari HomeSections
    # ----------------------------------------------------------
    print("[*] Fetching Catchup & VOD (HomeSections)...")
    try:
        data = fetch_home_sections()
        if data.get("success"):
            sections = data["data"]["contents"]
            for section in sections:
                section_title = section.get("title", "")
                contents = section.get("content", [])

                for item in contents:
                    item_type = item.get("type", "")
                    title = item.get("title", "")
                    logo = item.get("image_landscape", "") or item.get("image_square", "")

                    stream_url = None
                    if item_type == "catchup_schedule":
                        stream_url = item.get("stream_url_web", "")
                    elif item_type == "vod":
                        stream_url = item.get("vod_url_web", "")
                    else:
                        continue

                    if stream_url:
                        entries.append({
                            "group_title": section_title,
                            "tvg_name": title,
                            "tvg_logo": logo,
                            "stream_url": stream_url,
                        })
            print(f"    \u2713 Done")
    except Exception as ex:
        print(f"    [!] HomeSections: {ex}")

    # ----------------------------------------------------------
    # 4. VOD BY GENRE — dari movie/related (PARALLEL + PAGINATED)
    # ----------------------------------------------------------
    print("[*] Fetching VOD by genre (movie/related, parallel + paginated)...")
    vod_count = 0

    # Phase 1: Fetch page 1 semua genre (parallel) untuk tahu total
    genre_page1_data = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(worker_fetch_genre_first_page, g): g
            for g in VOD_GENRES
        }
        for future in as_completed(futures):
            genre, data = future.result()
            if data:
                genre_page1_data[genre] = data

    # Process page 1 data
    for genre, data in genre_page1_data.items():
        kid, slug, name = genre
        movies = data.get("movies", [])
        total = data.get("totalrecord", 0)
        _process_movie_list(movies, name, entries, seen_movie_ids)
        print(f"    \u2713 {name}: {len(movies)}/{total} items")

    # Phase 2: Fetch remaining pages (parallel)
    remaining_tasks = []
    for genre, data in genre_page1_data.items():
        kid, slug, name = genre
        total = data.get("totalrecord", 0)
        if total > 50:
            total_pages = (total + 49) // 50
            for p in range(2, total_pages + 1):
                remaining_tasks.append((kid, slug, name, p))

    if remaining_tasks:
        print(f"    Fetching {len(remaining_tasks)} additional pages...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(worker_fetch_genre_page, task): task
                for task in remaining_tasks
            }
            for future in as_completed(futures):
                genre_key, data = future.result()
                if data:
                    kid, slug, name = genre_key
                    movies = data.get("movies", [])
                    _process_movie_list(movies, name, entries, seen_movie_ids)

    vod_count = sum(1 for e in entries if "VOD -" in e.get("group_title", ""))
    print(f"    \u2713 Total VOD: {vod_count} items")

    return entries


# ============================================================
# WRITE OUTPUT
# ============================================================
def write_output(entries):
    m3u = build_m3u(entries)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u)

    live_count = sum(1 for e in entries if "op-flashcon-digdayahd" in e.get("stream_url", "") or "op-flashcon-digdayasd" in e.get("stream_url", ""))
    epg_total = sum(1 for e in entries if " - EPG" in e.get("group_title", ""))
    vod_count = sum(1 for e in entries if "VOD -" in e.get("group_title", ""))
    other_count = len(entries) - live_count - epg_total - vod_count

    print(f"\n[\u2713] Selesai! Total: {len(entries)} entri \u2192 '{OUTPUT_FILE}'")
    print(f"    - Live TV: {live_count}")
    print(f"    - EPG/Catchup: {epg_total}")
    print(f"    - VOD (by genre): {vod_count}")
    print(f"    - Other: {other_count}")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    start = time.time()
    entries = scrape()
    write_output(entries)
    elapsed = time.time() - start
    print(f"    Time: {elapsed:.1f}s")   
