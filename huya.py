#!/usr/bin/env python3
"""
SportPlus M3U Playlist Generator (FIXED)
"""

import requests
import json
import time
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin

# ─── KONFIGURASI ───────────────────────────────────────────
API_BASE = "https://apisplus.live"
WEB_PLAYER = "https://en97.sportplus.watch"
OUTPUT_FILE = "playlist.m3u"
TIMEOUT = 15
WIB = timezone(timedelta(hours=7))
DELAY_MIN = 0.5   # delay minimum antar request (detik)
DELAY_MAX = 1.5   # delay maksimum
RETRIES = 3       # jumlah retry untuk 429/5xx
BACKOFF = 2       # backoff base (detik)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": WEB_PLAYER,
    "Referer": WEB_PLAYER + "/",
    "Accept-Language": "id-ID,id;q=0.5",
}

# ─── HELPERS ───────────────────────────────────────────────
def to_wib(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str)
    return dt.astimezone(WIB)


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
    """Return list, handle None/missing."""
    v = obj.get(key) if obj else None
    return v if isinstance(v, list) else []


def polite_sleep():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# ─── HTTP dengan RETRY ────────────────────────────────────
def http_get(session: requests.Session, url: str) -> dict | list | None:
    """GET dengan retry + backoff untuk 429/5xx."""
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
            if "429" in str(e) or "5" in str(e)[:3]:
                wait = BACKOFF ** (attempt + 1) + random.uniform(0, 1)
                print(f"    [ERR] {e} → retry {attempt+1}/{RETRIES}")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            print(f"    [ERR] {e}")
            return None
    return None  # habis retry


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


# ─── SCRAPE ────────────────────────────────────────────────
def scrape_all() -> list:
    session = requests.Session()
    entries = []
    total_t = 0
    done_t = 0

    # Hitung total tournament (skip "top" collection)
    sidebar = fetch_sidebar(session)
    for sport in sidebar:
        if sport.get("type") == "collection":
            continue  # lewati "TOP streams"
        total_t += len(sport.get("tournaments", []))

    print(f"  Total: {total_t} tournament")

    for sport in sidebar:
        # SKIP collection (TOP streams)
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

            matches = fetch_matches(session, sport_id, t_id)
            if not matches:
                continue

            for m in matches:
                mid = m["id"]
                m_name = m.get("name", f"Match {mid}")
                start_str = m.get("start", "")
                status = m.get("status", "")

                if not start_str:
                    continue
                try:
                    start_wib = to_wib(start_str)
                except Exception:
                    continue

                polite_sleep()
                view = fetch_match_view(session, mid, sport_alias, t_alias)

                # Kumpulkan stream URLs
                stream_urls = []

                # FIX: safe_list handle None
                for ls_url in safe_list(view, "ls"):
                    if is_stream_url(ls_url):
                        stream_urls.append(ls_url)

                # Dari streams[]
                streams_list = safe_list(view, "streams")
                for i, s in enumerate(streams_list):
                    s_url = s.get("url", "")
                    if s_url and not s_url.startswith("http"):
                        s_url = urljoin(WEB_PLAYER, s_url)
                    if s_url and is_stream_url(s_url):
                        stream_urls.append(s_url)

                if not stream_urls:
                    continue

                for i, s_url in enumerate(stream_urls):
                    player_domain = get_player_domain(s_url)

                    # Logo
                    logo = ""
                    if i < len(streams_list):
                        logo_path = streams_list[i].get("logo5", "")
                        if logo_path:
                            logo = urljoin(WEB_PLAYER, logo_path)
                    elif not logo:
                        logo = ""

                    # Channel name
                    if i < len(streams_list):
                        chan_name = streams_list[i].get("name", f"Stream {i+1}")
                    else:
                        chan_name = f"Stream {i+1}"

                    # Waktu
                    time_str = start_wib.strftime("%H:%M")
                    date_str = start_wib.strftime("%d-%m-%Y")

                    # Title
                    home = m.get("home", {}).get("name", "")
                    away = m.get("away", {}).get("name", "")
                    title = f"{home} vs {away}" if home and away else m_name

                    # Name: title + channel + waktu di AKHIR
                    name = f"{title} [{chan_name}] ({time_str} {date_str})"

                    group = f"{sport_name} - {t_name}"

                    entries.append({
                        "name": name,
                        "title": title,
                        "group_title": group,
                        "tvg_logo": logo,
                        "stream_url": s_url,
                        "start_wib": start_wib,
                        "player_domain": player_domain,
                        "status": status,
                    })

    return entries


# ─── GENERATE M3U ──────────────────────────────────────────
def generate_m3u(entries: list, output: str):
    # Urutkan: live dulu, lalu by waktu
    entries.sort(key=lambda e: (
        0 if e["status"] == "live" else 1,
        e["start_wib"],
    ))

    ua = ("Mozilla/5.0 (Linux; Android 10; K) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/157.0.0.0 Mobile Safari/537.36")

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
            f.write(f'#EXTVLCOPT:http-user-agent="{ua}"\n')
            f.write(f'#EXTVLCOPT:http-referrer="{e["player_domain"]}/"\n')
            f.write("\n")

    print(f"\n[OK] {output} → {len(entries)} entry")


# ─── MAIN ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print(" SportPlus M3U Playlist Generator")
    print("=" * 50)

    entries = scrape_all()
    if entries:
        generate_m3u(entries, OUTPUT_FILE)
    else:
        print("\n[WARN] Tidak ada stream ditemukan.")   
