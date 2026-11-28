import requests
import re
import json
from datetime import datetime

TARGET_URL = "https://vipbox.ga"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}

def find_stream_urls(html):
    """Ekstrak semua URL streaming dari HTML."""
    patterns = [
        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.mpd[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.webm[^\s"\'<>]*',
        r'https?://[^\s"\'<>]+\.ts[^\s"\'<>]*',
    ]
    urls = set()
    for p in patterns:
        urls.update(re.findall(p, html))
    # Cek iframe src
    for src in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html):
        urls.add(src)
    return sorted(urls)

def scrape():
    print(f"Scraping: {TARGET_URL}")
    resp = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    urls = find_stream_urls(resp.text)
    result = {
        "source": TARGET_URL,
        "scraped_at": datetime.utcnow().isoformat(),
        "urls": urls
    }

    with open("playlist.m3u", "w") as f:
        json.dump(result, f, indent=2)

    print(f"✅ Ditemukan {len(urls)} URL streaming:")
    for u in urls:
        print(f"  {u}")

if __name__ == "__main__":
    scrape()   
