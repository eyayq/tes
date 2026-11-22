#!/usr/bin/env python3
"""
Scrape streaming URLs from xoilacvck.tv iframes → playlist.m3u
Uses curl_cffi (TLS fingerprint bypass) + ThreadPoolExecutor (concurrent)
"""

import re
import sys
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

BASE_URL = "https://xoilacvck.tv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
MAX_WORKERS = 250  # concurrent threads


def fetch_page(url: str) -> str:
    resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
    resp.raise_for_status()
    return resp.text


def get_all_sports(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    sports = []
    for item in soup.select(".main-grid-match"):
        sport = item.get("data-sport")
        if sport and sport not in sports:
            sports.append(sport)
    return sports


def parse_matches(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for item in soup.select(".main-grid-match"):
        fid = item.get("data-fid", "")
        sport = item.get("data-sport", "unknown")
        league = item.get("data-league", "")
        status = item.get("data-status", "1")
        data_day = item.get("data-day", "")
        data_runtime = item.get("data-runtime", "")

        link = item.select_one("a[href*='/truc-tiep/']")
        if not link:
            link = item.select_one("div[href*='/truc-tiep/']")
        if not link:
            continue
        slug_path = link.get("href", "")
        full_url = urljoin(BASE_URL, slug_path)
        slug = slug_path.rstrip("/").split("/")[-1] if slug_path else ""

        league_img = item.select_one(".gmd-comp_logo")
        tvg_logo = league_img.get("src", "") if league_img else ""

        home_names = [p.get_text(strip=True) for p in item.select(".gmd-home_team .team-name-group p")]
        away_names = [p.get_text(strip=True) for p in item.select(".gmd-away_team .team-name-group p")]
        home = "/".join(home_names) if home_names else "Home"
        away = "/".join(away_names) if away_names else "Away"
        title = f"{home} vs {away}"

        time_el = item.select_one(".gmd-e_minutes[data-time]")
        match_time = time_el.get("data-time", "") if time_el else ""
        date_el = item.select_one(".gmd-match-date span")
        match_date = date_el.get_text(strip=True).split(" - ")[-1] if date_el else ""

        if not match_date and data_day and len(data_day) == 8:
            match_date = f"{data_day[6:8]}/{data_day[4:6]}"
        if not match_time and data_runtime:
            dt = datetime.datetime.fromtimestamp(int(data_runtime))
            match_time = dt.strftime("%H:%M")

        is_live = status in {"2", "3", "4", "5", "6", "51", "52", "53", "54"}
        if item.select_one(".icon-live, .live-badge, [class*='live']"):
            is_live = True

        matches.append({
            "fid": fid,
            "sport": sport,
            "league": league,
            "status": status,
            "slug": slug,
            "full_url": full_url,
            "tvg_logo": tvg_logo,
            "title": title,
            "match_time": match_time,
            "match_date": match_date,
            "is_live": is_live,
        })

    return matches


def get_stream_from_iframe(match_url: str) -> str | None:
    try:
        html = fetch_page(match_url)
    except Exception as e:
        print(f"    ⚠ Fetch error: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    iframes = soup.select("iframe")

    for iframe in iframes:
        src = iframe.get("src", "")
        if not src:
            continue
        full_src = urljoin(match_url, src)

        if ".m3u8" in full_src:
            return full_src

        if any(kw in full_src.lower() for kw in ("stream", "player", "embed", "hls", "video", "watch")):
            try:
                iframe_html = fetch_page(full_src)
                m = re.search(r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', iframe_html)
                if m:
                    return m.group(1)
            except Exception:
                pass
            return full_src

    m = re.search(r'(https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*)', html)
    if m:
        return m.group(1)

    for script in soup.select("script"):
        text = script.string or script.get_text()
        m = re.search(r'"(?:src|url|file|source|stream)"\s*:\s*"([^"]+\.m3u8[^"]*)"', text)
        if m:
            return m.group(1)

    return None


def build_m3u_entry(match: dict, stream_url: str) -> str:
    title_parts = []
    if match["is_live"]:
        title_parts.append("[LIVE]")
    title_parts.append(match["title"])
    if match["match_time"] and match["match_date"]:
        title_parts.append(f"{match['match_time']} - {match['match_date']}")
    elif match["match_time"]:
        title_parts.append(match["match_time"])
    elif match["match_date"]:
        title_parts.append(match["match_date"])

    full_title = " ".join(title_parts).replace('"', "'")

    lines = []
    lines.append(f'#EXTINF:-1 group-title="{match["league"]}" tvg-logo="{match["tvg_logo"]}",{full_title}')
    lines.append(f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}')
    lines.append(f'#EXTVLCOPT:http-referrer={match["full_url"]}')
    lines.append(stream_url)
    return "\n".join(lines)


def scrape_match(match: dict) -> tuple:
    """Worker function for ThreadPoolExecutor."""
    stream_url = get_stream_from_iframe(match["full_url"])
    if not stream_url:
        stream_url = match["full_url"]
    return (match, stream_url)


def main():
    if len(sys.argv) > 1:
        slug = sys.argv[1].strip("/")
        match_url = f"{BASE_URL}/truc-tiep/{slug}/"
        print(f"Scraping single match: {match_url}")
        stream_url = get_stream_from_iframe(match_url)
        if stream_url:
            print(f"  Stream: {stream_url}")
        else:
            print("  ⚠ No stream found")
        return

    print(f"Fetching: {BASE_URL}")
    html = fetch_page(BASE_URL)
    sports = get_all_sports(html)
    print(f"Sports found: {sports}")

    matches = parse_matches(html)
    # Deduplicate
    seen = set()
    unique_matches = []
    for m in matches:
        if m["slug"] not in seen:
            seen.add(m["slug"])
            unique_matches.append(m)
    matches = unique_matches

    print(f"Total matches: {len(matches)}\n")

    all_entries = []
    results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scrape_match, m): m for m in matches}
        for i, future in enumerate(as_completed(futures), 1):
            match = futures[future]
            try:
                m, stream_url = future.result()
            except Exception as e:
                print(f"[{i}/{len(matches)}] ⚠ Error: {e}")
                continue

            status_tag = "🔴 LIVE" if m["is_live"] else "⏳"
            print(f"[{i}/{len(matches)}] {status_tag} {m['title']} ({m['match_time']} {m['match_date']})")
            print(f"    → {stream_url[:80]}")

            results[m["fid"]] = (m, stream_url)

    # Build entries in original order
    for match in matches:
        if match["fid"] in results:
            m, stream_url = results[match["fid"]]
            all_entries.append(build_m3u_entry(m, stream_url))

    output_file = "playlist.m3u"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in all_entries:
            f.write(entry + "\n")

    print(f"\n✅ Done! {len(all_entries)} entries → {output_file}")


if __name__ == "__main__":
    main()   
