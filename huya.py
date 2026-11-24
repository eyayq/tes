import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

BASE_URL = "https://jolightled.com"
PLAYER_DOMAIN = "jlbfpc.jlbfyh.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.5",
}

WIB = timezone(timedelta(hours=7))
OUTPUT = "playlist.m3u"
MAX_WORKERS = 10


def extract_stream_url(iframe_src):
    parsed = urlparse(iframe_src)
    params = parse_qs(parsed.query)
    url = unquote(params.get("url", [""])[0])
    cover = unquote(params.get("cover", [""])[0])
    return url or None, cover or None


def is_stream(url):
    if not url:
        return False
    clean = url.split("?")[0].lower()
    return any(ext in clean for ext in
               [".m3u8", ".flv", ".mpd", ".ts", ".php"])


def scrape_live_page():
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
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        iframe = soup.select_one("iframe[src]")
        if not iframe:
            return None, ""
        stream, cover = extract_stream_url(iframe["src"])
        if not cover:
            img = soup.select_one("img[src*='jalacos']")
            cover = img["src"] if img else ""
        return stream, cover
    except Exception as e:
        print(f"  ⚠ {e}")
        return None, ""


def fetch_channel(ch, t_str, d_str):
    stream, d_logo = get_stream(ch["detail_url"])
    if not stream or not is_stream(stream):
        return None
    return {
        "title": ch["title"],
        "group": ch["category"],
        "logo": ch["logo"] or d_logo,
        "url": stream,
        "time": t_str,
        "date": d_str,
    }


def main():
    print("⏳ Scraping /live ...")
    channels = scrape_live_page()
    print(f"   Ditemukan {len(channels)} channel\n")

    now = datetime.now(WIB)
    t_str = now.strftime("%H:%M")
    d_str = now.strftime("%d-%m-%Y")

    final = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(fetch_channel, ch, t_str, d_str): ch
            for ch in channels if ch["detail_url"]
        }
        for i, fut in enumerate(as_completed(futures), 1):
            ch = futures[fut]
            result = fut.result()
            status = "✓" if result else "✗"
            print(f"[{i}/{len(futures)}] {status} {ch['title']}")
            if result:
                final.append(result)

    final.sort(key=lambda x: (x["date"], x["time"]))

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Generated {now.strftime('%Y-%m-%d %H:%M WIB')}\n\n")
        for c in final:
            logo = f' tvg-logo="{c["logo"]}"' if c["logo"] else ""
            title = f'{c["title"]} [{c["time"]} {c["date"]} WIB]'
            f.write(f'#EXTINF:-1 group-title="{c["group"]}"{logo},{title}\n')
            f.write('#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36\n')
            f.write(f'#EXTVLCOPT:http-referrer=https://{PLAYER_DOMAIN}/\n')
            f.write(c["url"] + "\n")

    print(f"\n✅ Total: {len(final)} stream → {OUTPUT}")


if __name__ == "__main__":
    main()   
