#!/usr/bin/env python3
"""
SportPlus M3U Playlist Generator - THREADPOOL 2
"""

import requests
import time
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── KONFIGURASI ───────────────────────────────────────────
API_BASE = "https://apisplus.live"
WEB_PLAYER = "https://en97.sportplus.watch"
CDN = "https://cdn.ovbstdn.com"
OUTPUT_FILE = "playlist.m3u"
TIMEOUT = 15
WIB = timezone(timedelta(hours=7))
MAX_WORKERS = 70  # threadpool size

DELAY_MIN = 0.1
DELAY_MAX = 0.1
RETRIES = 1
BACKOFF = 1

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/157.0.0.0 Mobile Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Origin": WEB_PLAYER,
    "Referer": WEB_PLAYER + "/",
    "Accept-Language": "id-ID,id;q=0.5",
}

UA_FOR_VLC = ("Mozilla/5.0 (Linux; Android 10; K) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/157.0.0.0 Mobile Safari/537.36")


# ─── HELPERS ───────────────────────────────────────────────
def to_wib(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str).astimezone(WIB)


def get_player_domain(url: str) -> str:
    if url and url.startswith("http"):
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    return WEB_PLAYER


def escape_m3u(v: str) -> str:
    return v.replace('"', "&quot;").replace("&", "&amp;")


def is_stream_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(ext in u for ext in [".m3u8", ".flv", ".mpd", ".ts", ".php"])


def safe_list(obj, key):
    if not obj:
        return []
    v = obj.get(key)
    return v if isinstance(v, list) else []


def polite_sleep():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# ─── HTTP dengan RETRY ────────────────────────────────────
def http_get(session: requests.Session, url: str):
    for attempt in range(RETRIES):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 429:
                wait = BACKOFF ** (attempt + 1) + random.uniform(0, 1)
                print(f"    [429] retry {attempt+1}/{RETRIES} "
                      f"tunggu {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if attempt < RETRIES - 1:
                wait = BACKOFF ** (attempt + 1) + random.uniform(0, 1)
                print(f"    [ERR] {e} → retry {attempt+1}/{RETRIES}")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            print(f"    [ERR] {e}")
            return None
    return None


# ─── FETCH ─────────────────────────────────────────────────
def fetch_sidebar(session) -> list:
    data = http_get(session, f"{API_BASE}/v3/en/sidebar.json")
    return data if isinstance(data, list) else []


def fetch_matches(session, sport_id, t_id) -> list:
    url = (f"{API_BASE}/v3.1/matches/index"
           f"?fw=3.1&lang=en&lcfg=en&offset=25200"
           f"&other_matches=1&sport_id={sport_id}"
           f"&cur_tournament_id={t_id}")
    data = http_get(session, url)
    return data.get("items", []) if isinstance(data, dict) else []


def fetch_match_view(session, match_id, sport_alias, t_alias) -> dict:
    url = (f"{API_BASE}/v3.1/matches/view"
           f"?id={match_id}&sport_id={sport_alias}"
           f"&tournament_id={t_alias}"
           f"&lang=en&lcfg=en&fw=3.1&has_regions=1")
    data = http_get(session, url)
    return data.get("item", {}) if isinstance(data, dict) else {}


# ─── WORKER: process 1 match (dipanggil threadpool) ───────
def process_match(session, m, sport_alias, t_alias,
                  sport_name, t_name) -> list:
    """Fetch view + build entries untuk 1 match. Dipanggil paralel."""
    mid = m["id"]
    m_name = m.get("name", f"Match {mid}")
    start_str = m.get("start", "")
    status = m.get("status", "")

    if not start_str:
        return []
    try:
        start_wib = to_wib(start_str)
    except Exception:
        return []

    view = fetch_match_view(session, mid, sport_alias, t_alias)

    # Hanya dari ls[]
    stream_urls = []
    for ls_url in safe_list(view, "ls"):
        if is_stream_url(ls_url):
            stream_urls.append(ls_url)

    if not stream_urls:
        return []

    # Logo tim
    home_pk = m.get("home", {}).get("pk_id")
    tvg_logo = (f"{CDN}/onexbet/pk{home_pk}.png"
                if home_pk else "")

    # Nama & waktu
    time_str = start_wib.strftime("%H:%M")
    date_str = start_wib.strftime("%d-%m-%Y")
    home = m.get("home", {}).get("name", "")
    away = m.get("away", {}).get("name", "")
    title = f"{home} vs {away}" if home and away else m_name
    group = f"{sport_name} - {t_name}"

    results = []
    for i, s_url in enumerate(stream_urls):
        player_domain = get_player_domain(s_url)
        mirror = f" M{i+1}" if len(stream_urls) > 1 else ""
        name = f"{title}{mirror} ({time_str} {date_str})"
        results.append({
            "name": name,
            "title": title,
            "group_title": group,
            "tvg_logo": tvg_logo,
            "stream_url": s_url,
            "start_wib": start_wib,
            "player_domain": player_domain,
            "status": status,
        })
    return results


# ─── SCRAPE ────────────────────────────────────────────────
def scrape_all() -> list:
    # Session terpisah per thread (requests.Session NOT thread-safe)
    session_local = threading.local()

    def get_session():
        if not hasattr(session_local, 's'):
            session_local.s = requests.Session()
        return session_local.s

    entries = []
    total_t = 0
    done_t = 0

    print("[1/3] Mengambil sidebar...")
    main_session = requests.Session()
    sidebar = fetch_sidebar(main_session)

    for sport in sidebar:
        if sport.get("type") == "collection":
            continue
        total_t += len(sport.get("tournaments", []))

    print(f"  Total: {total_t} tournament")
    print(f"[2/3] Scrape (threadpool={MAX_WORKERS})...\n")

    for sport in sidebar:
        if sport.get("type") == "collection":
            continue

        sport_id = sport["id"]
        sport_name = sport["name"]
        sport_alias = sport.get("alias", str(sport_id))

        for t in sport.get("tournaments", []):
            done_t += 1
            t_id = t["id"]
            t_name = t["name"]
            t_alias = t.get("alias", str(t_id))
            if isinstance(t_alias, int):
                t_alias = str(t_alias)

            print(f"  [{done_t}/{total_t}] {sport_name} → {t_name}")
            polite_sleep()

            matches = fetch_matches(main_session, sport_id, t_id)
            if not matches:
                continue

            # ── PARALEL: fetch view untuk semua match ──
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    pool.submit(
                        process_match,
                        get_session(), m,
                        sport_alias, t_alias,
                        sport_name, t_name
                    ): m["id"]
                    for m in matches
                }
                for fut in as_completed(futures):
                    try:
                        res = fut.result()
                        if res:
                            entries.extend(res)
                    except Exception as e:
                        print(f"    [WARN] match "
                              f"{futures[fut]}: {e}")

    return entries


# ─── GENERATE M3U ──────────────────────────────────────────
def generate_m3u(entries: list, output: str):
    entries.sort(key=lambda e: (
        0 if e["status"] == "live" else 1,
        e["start_wib"],
    ))

    with open(output, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for e in entries:
            attrs = (
                f'tvg-logo="{escape_m3u(e["tvg_logo"])}" '
                f'group-title="{escape_m3u(e["group_title"])}" '
                f'title="{escape_m3u(e["title"])}" '
                f'name="{escape_m3u(e["name"])}"'
            )
            f.write(f"#EXTINF:-1,{attrs}\n")
            f.write(f"{e['name']}\n")
            f.write(f"{e['stream_url']}\n")
            f.write(f'#EXTVLCOPT:http-user-agent="{UA_FOR_VLC}"\n')
            f.write(
                f'#EXTVLCOPT:http-referrer='
                f'"{e["player_domain"]}/"\n'
            )
            f.write("\n")

    print(f"\n[3/3] [OK] {output} → {len(entries)} entry")


# ─── IMPORT threading di atas ─────────────────────────────
import threading

# ─── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print(" SportPlus M3U Playlist Generator")
    print(f" ThreadPool: {MAX_WORKERS}")
    print("=" * 50)

    entries = scrape_all()
    if entries:
        generate_m3u(entries, OUTPUT_FILE)
    else:
        print("\n[WARN] Tidak ada stream ditemukan.")   
