import subprocess
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

USER_URL = "https://www.dailymotion.com/user/Film_Horor_Indonesia"
OUTPUT = "playlist.m3u"
GROUP = "Film Horor Indonesia"
MAX_WORKERS = 5

# Step 1: Get video URLs (fast, flat)
result = subprocess.run(
    ["yt-dlp", "--flat-playlist", "-J", USER_URL],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"Error: {result.stderr}")
    sys.exit(1)

data = json.loads(result.stdout)
entries = data.get("entries", [])
video_urls = []
for e in entries:
    url = e.get("url") or f"https://www.dailymotion.com/video/{e.get('id', '')}"
    if url and url.startswith("http"):
        video_urls.append(url)

print(f"Total: {len(video_urls)} video\n")

# Step 2: Get full metadata + m3u8 (paralel)
def fetch_video(url):
    result = subprocess.run(
        ["yt-dlp", "-J", "-f", "bv*", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return {"url": url, "title": None, "thumbnail": None, "m3u8": None}
    try:
        d = json.loads(result.stdout)
        return {
            "url": url,
            "title": d.get("title", "Unknown"),
            "thumbnail": d.get("thumbnail", ""),
            "m3u8": d.get("url", "")
        }
    except json.JSONDecodeError:
        return {"url": url, "title": None, "thumbnail": None, "m3u8": None}

results = [None] * len(video_urls)
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(fetch_video, url): i for i, url in enumerate(video_urls)}
    done = 0
    for future in as_completed(futures):
        i = futures[future]
        results[i] = future.result()
        done += 1
        r = results[i]
        status = "✅" if r["m3u8"] else "⚠️"
        print(f"[{done}/{len(video_urls)}] {status} {r['title']}")

# Step 3: Write M3U
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for r in results:
        if r and r["m3u8"]:
            title = r["title"] or "Unknown"
            vlogo = r["thumbnail"] or ""
            f.write(f'#EXTINF:-1 tvg-name="{title}" tvg-logo="{vlogo}" group-title="{GROUP}",{title}\n')
            f.write(f'{r["m3u8"]}\n')

print(f"\n✅ Selesai! File: {OUTPUT}")   
