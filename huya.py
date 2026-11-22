from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import re, time   

BASE = "https://tructiep-hd.club"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
WIB = timezone(timedelta(hours=7))

def get_matches(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    seen = set()

    for li in soup.find_all("li", class_="item-watch"):
        a = li.find("a", class_=re.compile(r"btn_(xem|mophong)"))
        if not a or not a.get("href"):
            continue

        href = a["href"]
        m = re.match(r"^https?://tructiep-hd\.club/([a-z0-9\-]+\-m\d+)", href)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        league_el = li.find("div", class_="giai-dau")
        league = league_el.get_text(strip=True) if league_el else "Unknown"

        time_el = li.find("div", class_="time")
        time_str = time_el.get_text(strip=True) if time_el else ""

        home_el = li.find("div", class_="team-home")
        home_name = ""
        home_logo = ""
        if home_el:
            h4 = home_el.find("h4", class_="team-name")
            home_name = h4.get_text(strip=True) if h4 else ""
            img = home_el.find("img")
            home_logo = img.get("data-src", "") if img else ""

        away_el = li.find("div", class_="team-away")
        away_name = ""
        if away_el:
            h4 = away_el.find("h4", class_="team-name")
            away_name = h4.get_text(strip=True) if h4 else ""

        is_live = "live" in li.get("class", [])

        matches.append({
            "slug": slug,
            "league": league,
            "time_str": time_str,
            "home": home_name,
            "away": away_name,
            "home_logo": home_logo,
            "is_live": is_live,
            "url": f"{BASE}/{slug}",
        })

    return matches


def to_wib(time_str: str, is_live: bool) -> str:
    if is_live:
        return time_str
    m = re.match(r"(\d{2}:\d{2})-(\d{2}/\d{2})", time_str)
    if m:
        return f"{m.group(1)} WIB"
    return time_str


def get_stream_url(slug: str) -> list[str]:
    url = f"{BASE}/{slug}"
    try:
        # ✅ curl_cffi dengan impersonate chrome
        r = cffi_requests.get(
            url,
            headers={"Referer": BASE + "/"},
            impersonate="chrome",
            timeout=15,
        )
        r.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    streams = []

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "") or iframe.get("data-src", "")
        if src:
            streams.append(src)

    if not streams:
        for s in soup.find_all("script"):
            text = s.string or ""
            for m in re.finditer(r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)', text):
                streams.append(m.group(1))
            for m in re.finditer(r'(?:streamUrl|playerUrl|videoUrl|embedUrl|src)\s*[:=]\s*["\']([^"\']+)["\']', text, re.I):
                val = m.group(1)
                if val.startswith("http"):
                    streams.append(val)

    return streams


def build_m3u(matches: list[dict], streams_map: dict[str, list[str]]) -> str:
    lines = ["#EXTM3U"]

    for m in matches:
        streams = streams_map.get(m["slug"], [])
        if not streams:
            continue

        time_wib = to_wib(m["time_str"], m["is_live"])
        title = f"{m['home']} vs {m['away']} [{time_wib}]"
        logo = m["home_logo"]
        group = m["league"]
        stream_url = streams[0]

        lines.append(
            f'#EXTINF:-1 tvg-id="" tvg-name="{m["home"]} vs {m["away"]}" '
            f'tvg-logo="{logo}" group-title="{group}",{title}'
        )
        lines.append(f'#EXTVLCOPT: http-user-agent={UA}')
        lines.append(f'#EXTVLCOPT: http-referrer={BASE}/')
        lines.append(stream_url)

    return "\n".join(lines) + "\n"


# === MAIN ===
if __name__ == "__main__":
    # Ambil beranda via curl_cffi
    r = cffi_requests.get(BASE, impersonate="chrome", timeout=20)
    r.raise_for_status()
    matches = get_matches(r.text)
    print(f"Total pertandingan: {len(matches)}\n")

    streams_map = {}
    for m in matches:
        streams = get_stream_url(m["slug"])
        streams_map[m["slug"]] = streams
        status = "✓" if streams else "✗"
        print(f"[{status}] {m['home']} vs {m['away']}")
        if streams:
            print(f"     → {streams[0]}")
        time.sleep(1)

    m3u_content = build_m3u(matches, streams_map)
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\n✅ Tersimpan ke playlist.m3u ({len(streams_map)} stream)")   
