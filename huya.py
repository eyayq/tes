#!/usr/bin/env python3
"""
SportPlus M3U Playlist Generator - LIVE ONLY + PHP PROXY
"""

from curl_cffi import requests as cr
import time
import random
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── KONFIGURASI ───────────────────────────────────────────
API_BASE = "https://apisplus.live"
WEB_PLAYER = "https://en97.sportplus.watch"
CDN = "https://cdn.ovbstdn.com"
OUTPUT_FILE = "playlist.m3u"
TIMEOUT = 15
WIB = timezone(timedelta(hours=7))
MAX_WORKERS = 80
DEBUG = True

PROXY = "http://svr00000:bhns0000@64.137.96.74:6641"
PROXY_BASE = "https://exsample.d-n-s.name/us.m3u8"  # ← GANTI

DELAY_MIN = 0
DELAY_MAX = 0
RETRIES = 1
BACKOFF = 1
CB_THRESHOLD = 1
CB_COOLDOWN = 1

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/157.0.0.0 Mobile Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Origin": WEB_PLAYER,
    "Referer": WEB_PLAYER + "/",
    "Accept-Language": "id-ID,id;q=0.6",
}

UA_FOR_VLC = ("Mozilla/5.0 (Linux; Android 10; K) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/157.0.0.0 Mobile Safari/537.36")

_consecutive_429 = 0
_cb_lock = threading.Lock()
_dbg_lock = threading.Lock()


def dbg(msg):
    if DEBUG:
        with _dbg_lock:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] {msg}")


# ─── HELPERS ───────────────────────────────────────────────
def to_wib(dt_str):
    return datetime.fromisoformat(dt_str).astimezone(WIB)


def get_player_domain(url):
    if url and url.startswith("http"):
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    return WEB_PLAYER


def escape_m3u(v):
    return v.replace('"', "&quot;").replace("&", "&amp;")


def is_stream_url(url):
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
    if DELAY_MIN > 0 or DELAY_MAX > 0:
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# ─── HTTP (curl_cffi) ─────────────────────────────────────
def http_get(url):
    global _consecutive_429

    for attempt in range(RETRIES):
        with _cb_lock:
            if _consecutive_429 >= CB_THRESHOLD:
                dbg(f"[CB] 429 x{_consecutive_429} → cooldown {CB_COOLDOWN}s")
                time.sleep(CB_COOLDOWN)
                _consecutive_429 = 0

        try:
            dbg(f"GET {url[:90]}...")
            t0 = time.time()

            r = cr.get(
                url,
                headers=HEADERS,
                impersonate="chrome",
                proxy=PROXY,
                timeout=TIMEOUT,
            )

            elapsed = time.time() - t0
            body_len = len(r.text) if r.text else 0

            if r.status_code == 429:
                with _cb_lock:
                    _consecutive_429 += 1
                wait = BACKOFF ** (attempt + 1) + random.uniform(0, 2)
                dbg(f"  ← 429 ({elapsed:.1f}s) → retry "
                    f"{attempt+1}/{RETRIES} tunggu {wait:.0f}s")
                time.sleep(wait)
                continue

            dbg(f"  ← {r.status_code} ({elapsed:.1f}s, {body_len}B)")

            if r.status_code != 200:
                dbg(f"  ⚠ non-200, body: {r.text[:100]}")
                return None

            with _cb_lock:
                _consecutive_429 = 0

            data = r.json()
            if data is None:
                dbg(f"  ⚠ r.json()=None, body: {r.text[:100]}")
                return None

            return data

        except Exception as e:
            if attempt < RETRIES - 1:
                wait = BACKOFF ** (attempt + 1)
                dbg(f"  [ERR] {e} → retry {attempt+1}/{RETRIES}")
                time.sleep(wait)
                continue
            dbg(f"  [ERR] {e} (final)")
            return None
    return None


# ─── FETCH ─────────────────────────────────────────────────
def fetch_sidebar():
    dbg("→ fetch_sidebar()")
    data = http_get(f"{API_BASE}/v3/en/sidebar.json")
    if isinstance(data, list):
        dbg(f"  ← {len(data)} sport")
        for s in data:
            n = len(s.get("tournaments", []))
            typ = s.get("type", "sport")
            dbg(f"    {s['name']} ({n} t, {typ})")
        return data
    dbg("  ← FAILED")
    return []


def fetch_matches(sport_id, t_id):
    url = (f"{API_BASE}/v3.1/matches/index"
           f"?fw=3.1&lang=en&lcfg=en&offset=25200"
           f"&other_matches=1&sport_id={sport_id}"
           f"&cur_tournament_id={t_id}")
    dbg(f"→ fetch_matches(sport={sport_id}, t={t_id})")
    data = http_get(url)
    if isinstance(data, dict):
        items = data.get("items", [])
        live = sum(1 for i in items if i.get("status") == "live")
        dbg(f"  ← {len(items)} match ({live} live)")
        return items
    dbg("  ← FAILED")
    return []


def fetch_match_view(match_id, sport_alias, t_alias):
    url = (f"{API_BASE}/v3.1/matches/view"
           f"?id={match_id}&sport_id={sport_alias}"
           f"&tournament_id={t_alias}"
           f"&lang=en&lcfg=en&fw=3.1&has_regions=1")
    dbg(f"→ fetch_view(id={match_id})")
    data = http_get(url)
    if isinstance(data, dict):
        item = data.get("item", {})
        ls = item.get("ls")
        has_chan = item.get("has_chan", 0)
        dbg(f"  ← ls={'✅'+str(len(ls)) if ls else 'null'} "
            f"has_chan={has_chan}")
        return item
    dbg("  ← FAILED (None)")
    return {}


# ─── WORKER ────────────────────────────────────────────────
def process_match(m, sport_alias, t_alias, sport_name, t_name):
    mid = m["id"]
    m_name = m.get("name", f"Match {mid}")
    start_str = m.get("start", "")
    status = m.get("status", "")

    if status != "live":
        return []

    dbg(f"  ⚙ match {mid}: {m_name[:40]} [{status}]")

    if not start_str:
        dbg(f"    ⚠ no start time")
        return []
    try:
        start_wib = to_wib(start_str)
    except Exception as e:
        dbg(f"    ⚠ parse time: {e}")
        return []

    view = fetch_match_view(mid, sport_alias, t_alias)
    if not view:
        dbg(f"    ⚠ view empty, skip")
        return []

    stream_urls = []
    for ls_url in safe_list(view, "ls"):
        if is_stream_url(ls_url):
            stream_urls.append(ls_url)

    if not stream_urls:
        dbg(f"    ⚠ no stream URL (ls={view.get('ls')})")
        return []

    dbg(f"    ✅ {len(stream_urls)} stream")

    home_pk = m.get("home", {}).get("pk_id")
    tvg_logo = (f"{CDN}/onexbet/pk{home_pk}.png"
                if home_pk else "")

    time_str = start_wib.strftime("%H:%M")
    date_str = start_wib.strftime("%d-%m-%Y")
    home = m.get("home", {}).get("name", "")
    away = m.get("away", {}).get("name", "")
    title = f"{home} vs {away}" if home and away else m_name
    group = f"{sport_name} - {t_name}"

    results = []
    for i, s_url in enumerate(stream_urls):
        mirror = f" M{i+1}" if len(stream_urls) > 1 else ""
        name = f"{title}{mirror} ({time_str} {date_str})"

        # ← BUNGKUS DENGAN PHP PROXY
        proxied_url = f"{PROXY_BASE}?url={quote(s_url, safe='')}"

        results.append({
            "name": name,
            "title": title,
            "group_title": group,
            "tvg_logo": tvg_logo,
            "stream_url": proxied_url,
            "start_wib": start_wib,
            "player_domain": PROXY_BASE,
            "status": status,
        })

    dbg(f"    → {len(results)} entry dibuat")
    return results


# ─── SCRAPE ────────────────────────────────────────────────
def scrape_all():
    entries = []
    total_t = 0
    done_t = 0
    t_start = time.time()

    print("[1/3] Sidebar...")
    sidebar = fetch_sidebar()

    for sport in sidebar:
        if sport.get("type") == "collection":
            continue
        total_t += len(sport.get("tournaments", []))

    print(f"  Total: {total_t} tournament")
    print(f"[2/3] Scrape LIVE (threadpool={MAX_WORKERS})...\n")

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

            elapsed = time.time() - t_start
            print(f"\n  [{done_t}/{total_t}] "
                  f"{sport_name} → {t_name} "
                  f"({elapsed:.0f}s, {len(entries)} stream)")

            polite_sleep()
            matches = fetch_matches(sport_id, t_id)
            if not matches:
                continue

            live_matches = [m for m in matches
                            if m.get("status") == "live"]
            dbg(f"  matches: {len(matches)} total, "
                f"{len(live_matches)} live")

            if not live_matches:
                continue

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    pool.submit(
                        process_match, m,
                        sport_alias, t_alias,
                        sport_name, t_name
                    ): m["id"]
                    for m in live_matches
                }
                for fut in as_completed(futures):
                    try:
                        res = fut.result()
                        if res:
                            entries.extend(res)
                    except Exception as e:
                        dbg(f"  [WARN] match "
                            f"{futures[fut]}: {e}")

    return entries


# ─── GENERATE M3U ──────────────────────────────────────────
def generate_m3u(entries, output):
    entries.sort(key=lambda e: e["start_wib"])

    dbg(f"→ generate_m3u({len(entries)} entries)")

    with open(output, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries):
            attrs = (
                f'tvg-logo="{escape_m3u(e["tvg_logo"])}" '
                f'group-title="{escape_m3u(e["group_title"])}", '
                f'name="{escape_m3u(e["name"])}"'
            )
            f.write(f"#EXTINF:-1 {attrs}\n")
            f.write(f"{e['name']}\n")
            f.write(f'#EXTVLCOPT:http-user-agent="{UA_FOR_VLC}"\n')
            f.write(f'#EXTVLCOPT:http-referrer="https://en97.sportplus.watch/"\n')
            f.write(f'#EXTVLCOPT:http-origin="https://en97.sportplus.watch"\n')
            f.write(f"{e['stream_url']}\n")
            f.write("\n")

    groups = set(e["group_title"] for e in entries)
    print(f"\n[3/3] [OK] {output}")
    print(f"  Total: {len(entries)} entry (semua live)")
    print(f"  Groups: {len(groups)}")
    print(f"  File size: "
          f"{sum(1 for _ in open(output))} lines")


# ─── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print(" SportPlus M3U Generator - LIVE ONLY + PROXY")
    print(f" curl_cffi + proxy + threadpool {MAX_WORKERS}")
    print(f" PHP Proxy: {PROXY_BASE}")
    print(f" DEBUG: {DEBUG}")
    print("=" * 50)

    t0 = time.time()
    entries = scrape_all()
    elapsed = time.time() - t0

    if entries:
        generate_m3u(entries, OUTPUT_FILE)
    else:
        print("\n[WARN] Tidak ada stream live saat ini.")

    print(f"\n⏱ Total time: {elapsed:.0f}s "
          f"({elapsed/60:.1f}m)")   
