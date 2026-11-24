#!/usr/bin/env python3
"""
SportPlus M3U Playlist Generator
Scrape semua kategori & pertandingan, buat playlist.m3u
dengan tvg-logo, group-title, title, name, jam/tanggal WIB,
EXVLCOPT user-agent & referrer dari domain player.
"""

import requests
import json
import time
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── KONFIGURASI ───────────────────────────────────────────
API_BASE = "https://apisplus.live"
WEB_PLAYER = "https://en97.sportplus.watch"
OUTPUT_FILE = "playlist.m3u"
MAX_WORKERS = 8
TIMEOUT = 15
WIB = timezone(timedelta(hours=7))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": WEB_PLAYER,
    "Referer": WEB_PLAYER + "/",
    "Accept-Language": "id-ID,id;q=0.5",
}

# ─── HELPERS ───────────────────────────────────────────────
def to_wib(dt_str: str) -> datetime:
    """Parse ISO datetime (+0300) → WIB."""
    dt = datetime.fromisoformat(dt_str)
    return dt.astimezone(WIB)


def extract_domain(url: str) -> str:
    """Ekstrak domain dari URL stream (player domain, bukan web player)."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def get_player_domain(stream_url: str) -> str:
    """
    Dapatkan domain player dari URL stream.
    Jika URL relative, coba deteksi dari context.
    """
    if stream_url.startswith("http"):
        return extract_domain(stream_url)
    return WEB_PLAYER  # fallback


def escape_m3u(value: str) -> str:
    """Escape karakter khusus untuk atribut M3U."""
    return (
        value.replace('"', "&quot;")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def is_stream_url(url: str) -> bool:
    """Cek apakah URL adalah stream (m3u8, flv, mpd, ts, php)."""
    if not url:
        return False
    lower = url.lower()
    return any(ext in lower for ext in [".m3u8", ".flv", ".mpd", ".ts", ".php"])


# ─── FETCH FUNCTIONS ───────────────────────────────────────
def fetch_sidebar(session: requests.Session) -> list:
    """Ambil semua sport & tournament dari sidebar."""
    url = f"{API_BASE}/v3/en/sidebar.json"
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_matches(
    session: requests.Session,
    sport_id: int,
    tournament_id: int,
    tournament_alias: str,
    sport_alias: str,
) -> list:
    """Ambil daftar match untuk satu tournament."""
    url = (
        f"{API_BASE}/v3.1/matches/index"
        f"?fw=3.1&lang=en&lcfg=en&offset=25200"
        f"&other_matches=1&sport_id={sport_id}"
        f"&cur_tournament_id={tournament_id}"
    )
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("items", [])


def fetch_match_view(
    session: requests.Session,
    match_id: int,
    sport_alias: str,
    tournament_alias: str,
) -> dict:
    """Ambil detail match termasuk stream URLs."""
    url = (
        f"{API_BASE}/v3.1/matches/view"
        f"?id={match_id}"
        f"&sport_id={sport_alias}"
        f"&tournament_id={tournament_alias}"
        f"&lang=en&lcfg=en&fw=3.1&has_regions=1"
    )
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("item", {})


# ─── SCRAPE & PARSE ────────────────────────────────────────
def scrape_all() -> list:
    """
    Scrape semua kategori, tournament, match, dan stream.
    Return list of dicts:
    {
        'name': str,
        'title': str,
        'group_title': str,
        'tvg_logo': str,
        'stream_url': str,
        'start_wib': datetime,
        'player_domain': str,
    }
    """
    session = requests.Session()
    entries = []

    # 1. Ambil sidebar
    print("[1/3] Mengambil sidebar (kategori & tournament)...")
    sidebar = fetch_sidebar(session)

    for sport in sidebar:
        sport_id = sport["id"]
        sport_name = sport["name"]
        sport_alias = sport.get("alias", str(sport_id))

        tournaments = sport.get("tournaments", [])
        if not tournaments:
            continue

        print(f"  Sport: {sport_name} ({len(tournaments)} tournament)")

        for t in tournaments:
            t_id = t["id"]
            t_name = t["name"]
            t_alias = t.get("alias", str(t_id))
            if isinstance(t_alias, int):
                t_alias = str(t_alias)

            # 2. Ambil matches
            try:
                matches = fetch_matches(
                    session, sport_id, t_id, t_alias, sport_alias
                )
            except Exception as e:
                print(f"    [WARN] {t_name}: {e}")
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

                # 3. Ambil match view untuk stream
                try:
                    view = fetch_match_view(
                        session, mid, sport_alias, t_alias
                    )
                except Exception as e:
                    # Fallback: tanpa stream detail
                    view = {}

                # Kumpulkan semua stream URL
                stream_urls = []

                # Dari field 'ls' (m3u8 langsung)
                for ls_url in view.get("ls", []):
                    if is_stream_url(ls_url):
                        stream_urls.append(ls_url)

                # Dari field 'streams' → resolve relative URL
                for s in view.get("streams", []):
                    s_name = s.get("name", "")
                    s_logo = s.get("logo5", "")
                    s_url = s.get("url", "")

                    # Resolve relative URL
                    if s_url and not s_url.startswith("http"):
                        full_url = urljoin(WEB_PLAYER, s_url)
                    else:
                        full_url = s_url

                    if full_url and is_stream_url(full_url):
                        stream_urls.append(full_url)

                    # Jika URL stream tidak ada, coba dari ls
                    # (ls sudah di atas)

                # Jika tidak ada stream URL dari view, skip
                if not stream_urls:
                    # Coba buat entry tanpa stream (placeholder)
                    continue

                # Buat entry untuk setiap stream URL
                for i, s_url in enumerate(stream_urls):
                    player_domain = get_player_domain(s_url)

                    # Logo: dari stream logo jika ada, else kosong
                    if "streams" in view and i < len(view.get("streams", [])):
                        logo = view["streams"][i].get("logo5", "")
                        if logo:
                            logo = urljoin(WEB_PLAYER, logo)
                    else:
                        logo = ""

                    # Nama channel
                    if "streams" in view and i < len(view.get("streams", [])):
                        chan_name = view["streams"][i].get("name", f"Stream {i+1}")
                    else:
                        chan_name = f"Stream {i+1}"

                    # Format waktu WIB
                    time_str = start_wib.strftime("%H:%M")
                    date_str = start_wib.strftime("%d-%m-%Y")

                    # Title: "Home — Away"
                    home = m.get("home", {}).get("name", "")
                    away = m.get("away", {}).get("name", "")
                    title = f"{home} vs {away}" if home and away else m_name

                    # Name: "Title [Channel] (HH:MM DD-MM-YYYY)"
                    name = f"{title} [{chan_name}] ({time_str} {date_str})"

                    # Group: "Sport - Tournament"
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

                time.sleep(0.1)  # rate limit

    return entries


# ─── GENERATE M3U ──────────────────────────────────────────
def generate_m3u(entries: list, output: str):
    """Generate file playlist.m3u, diurutkan berdasarkan waktu."""
    # Urutkan: live dulu (by time), lalu not_started (by date+time)
    entries.sort(key=lambda e: (
        0 if e["status"] == "live" else 1,
        e["start_wib"],
    ))

    with open(output, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for e in entries:
            tvg_logo = escape_m3u(e["tvg_logo"]) if e["tvg_logo"] else ""
            group = escape_m3u(e["group_title"])
            title = escape_m3u(e["title"])
            name = escape_m3u(e["name"])

            attrs = (
                f'tvg-logo="{tvg_logo}" '
                f'group-title="{group}" '
                f'title="{title}" '
                f'name="{name}"'
            )

            f.write(f"#EXTINF:-1,{attrs}\n{name}\n")
            f.write(f"{e['stream_url']}\n")

            # EXVLCOPT: user-agent & referrer dari domain player
            player_domain = e["player_domain"]
            ua = (
                "Mozilla/5.0 (Linux; Android 10; K) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/157.0.0.0 Mobile Safari/537.36"
            )
            f.write(
                f'#EXTVLCOPT:http-user-agent="{ua}"\n'
            )
            f.write(
                f'#EXTVLCOPT:http-referrer="{player_domain}/"\n'
            )
            f.write(f"\n")

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
