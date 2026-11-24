import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timezone, timedelta
import time

BASE_URL = "https://jolightled.com"
PLAYER_DOMAIN = "jlbfpc.jlbfyh.com"  # domain player (bukan web)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.5",
}

WIB = timezone(timedelta(hours=7))
OUTPUT = "playlist.m3u"


def extract_stream_url(iframe_src):
    """Ambil URL stream asli dari parameter url di iframe src"""
    parsed = urlparse(iframe_src)
    params = parse_qs(parsed.query)
    url = params.get("url", [None])[0]
    return unquote(url) if url else None


def is_stream(url):
    """Cek apakah URL valid (m3u8, flv, mpd, ts, php)"""
    if not url:
        return False
    clean = url.split("?")[0].lower()
    return any(ext in clean for ext in
               [".m3u8", ".flv", ".mpd", ".ts", ".php"])


def scrape_live_page():
    """Scrape halaman /live untuk semua channel"""
    resp = requests.get(f"{BASE_URL}/live", headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    channels = []
    for card in soup.select(".channel-card"):
        category = card.get("data-category", "Lainnya")
        h2 = card.select_one("h2")
        title = h2.get_text(strip=True) if h2 else "Unknown"

        img = card.select_one("img[src]")
        logo = img["src"] if img else ""

        link = card.select_one("a.btn")
        detail = (f"{BASE_URL}{link['href']}"
                  if link and "href" in link.attrs else "")

        channels.append({
            "title": title,
            "category": category,
            "logo": logo,
            "detail_url": detail,
        })
    return channels


def get_stream(detail_url):
    """Buka halaman detail, ambil stream URL dari iframe"""
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        iframe = soup.select_one("iframe[src]")
        if not iframe:
            return None, ""
        stream = extract_stream_url(iframe["src"])
        img = soup.select_one("img[src*='jalacos']")
        logo = img["src"] if img else ""
        return stream, logo
    except Exception as e:
        print(f"  ⚠ {e}")
        return None, ""


def main():
    print("⏳ Scraping /live ...")
    channels = scrape_live_page()
    print(f"   Ditemukan {len(channels)} channel\n")

    now = datetime.now(WIB)
    t_str = now.strftime("%H:%M")
    d_str = now.strftime("%d-%m-%Y")

    final = []
    for i, ch in enumerate(channels, 1):
        print(f"[{i}/{len(channels)}] {ch['title']}")
        if not ch["detail_url"]:
            continue

        stream, d_logo = get_stream(ch["detail_url"])
        if not stream or not is_stream(stream):
            print("   ⚠ Tidak ada stream valid")
            continue

        final.append({
            "title": ch["title"],
            "group": ch["category"],
            "logo": ch["logo"] or d_logo,
            "url": stream,
            "time": t_str,
            "date": d_str,
        })
        time.sleep(0.5)

    # Urutkan sesuai tanggal & jam
    final.sort(key=lambda x: (x["date"], x["time"]))

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Generated {now.strftime('%Y-%m-%d %H:%M WIB')}\n\n")
        for c in final:
            logo = f' tvg-logo="{c["logo"]}"' if c["logo"] else ""
            title = f'{c["title"]} [{c["time"]} {c["date"]} WIB]'
            f.write(f'#EXTINF:-1 group-title="{c["group"]}"{logo},{title}\n')
            f.write(f'#EXTVLCOPT:http-user-agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"\n')
            f.write(f'#EXTVLCOPT:http-referrer="https://{PLAYER_DOMAIN}/"\n')
            f.write(c["url"] + "\n")

    print(f"\n✅ Total: {len(final)} stream → {OUTPUT}")


if __name__ == "__main__":
    main()   
