import subprocess
import json
import sys
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

USER_URL = "https://www.dailymotion.com/user/Film_Horor_Indonesia"
OUTPUT = "playlist.m3u"
GROUP = "Film Horor Indonesia"
MAX_WORKERS = 5

def extract_vid(entry):
    """Ekstrak video ID dari entry flat-playlist"""
    vid = entry.get("id", "")
    if not vid:
        url = entry.get("url", "")
        if "/video/" in url:
            vid = url.split("/video/")[1].split("?")[0].strip("/")
    # Pastikan format x + angka (ID Dailymotion)
    if not vid or not vid.startswith("x"):
        return None
    return vid

# Step 1: Get video IDs
result = subprocess.run(
    ["yt-dlp", "--flat-playlist", "-J", USER_URL],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"Error: {result.stderr}")
    sys.exit(1)

data = json.loads(result.stdout)
entries = data.get("entries", [])

video_ids = []
for e in entries:
    vid = extract_vid(e)
    if vid:
        video_ids.append(vid)

print(f"Total: {len(video_ids)} video (dari {len(entries)} entries)")

# Step 2: Batch-fetch titles & thumbnails
meta = {}
BATCH = 50
for i in range(0, len(video_ids), BATCH):
    chunk = video_ids[i:i+BATCH]
    r = requests.get("https://api.dailymotion.com/videos",
        params={"ids": ",".join(chunk), "fields": "id,title,thumbnail_url"})
    for v in r.json().get("list", []):
        meta[v["id"]] = {"title": v["title"], "thumbnail": v.get("thumbnail_url", "")}
    print(f"  Metadata: {min(i+BATCH, len(video_ids))}/{len(video_ids)}")

# Cek berapa yang tidak ketemu
not_found = [vid for vid in video_ids if vid not in meta]
if not_found:
    print(f"  ⚠️  {len(not_found)} video tidak ditemukan di API")

# Step 3: Get m3u8 URLs (paralel)
def get_m3u8(vid):
    url = f"https://www.dailymotion.com/video/{vid}"
    result = subprocess.run(
        ["yt-dlp", "-g", "-f", "bv*", url],
        capture_output=True, text=True
    )
    return vid, result.stdout.strip() if result.returncode == 0 else None

m3u8_map = {}
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(get_m3u8, vid): vid for vid in video_ids}
    done = 0
    for future in as_completed(futures):
        vid, m3u8 = future.result()
        m3u8_map[vid] = m3u8
        done += 1
        title = meta.get(vid, {}).get("title", "Unknown")
        print(f"[{done}/{len(video_ids)}] {'✅' if m3u8 else '⚠️'} {title}")

# Step 4: Write M3U
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for vid in video_ids:
        m = meta.get(vid, {})
        m3u8 = m3u8_map.get(vid)
        title = m.get("title", "Unknown")
        vlogo = m.get("thumbnail", "")
        if m3u8:
            f.write(f'#EXTINF:-1 tvg-name="{title}" tvg-logo="{vlogo}" group-title="{GROUP}",{title}\n')
            f.write(f'{m3u8}\n')

print(f"\n✅ Selesai! File: {OUTPUT}")   
