import requests
import subprocess
import time
import sys

USER = "Film_Horor_Indonesia"
OUTPUT = "playlist.m3u"
GROUP = "Film Horor Indonesia"

# Step 1: Get user ID + logo
r = requests.get("https://api.dailymotion.com/users",
    params={"screenname": USER, "fields": "id,name,thumbnail_url"})
data = r.json()

if not data.get("list"):
    print(f"User '{USER}' tidak ditemukan!")
    sys.exit(1)

user = data["list"][0]
user_id = user["id"]
logo = user.get("thumbnail_url", "")
print(f"User: {user['name']} (ID: {user_id})")

# Step 2: Collect all videos
videos = []
start = 0
while True:
    r = requests.get("https://api.dailymotion.com/videos",
        params={"owner": user_id, "fields": "id,title,thumbnail_url", "limit": 20, "start": start})
    data = r.json()
    batch = data.get("list", [])
    if not batch:
        break
    videos.extend(batch)
    start += 20
    if start >= data.get("total", 0):
        break

print(f"Total: {len(videos)} video\n")

# Step 3: Write M3U
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for i, v in enumerate(videos, 1):
        url = f"https://www.dailymotion.com/video/{v['id']}"
        title = v["title"]
        vlogo = v.get("thumbnail_url", logo)
        print(f"[{i}/{len(videos)}] {title}")

        result = subprocess.run(
            ["yt-dlp", "-g", "-f", "bv*", url],
            capture_output=True, text=True
        )
        m3u8 = result.stdout.strip()

        if m3u8:
            f.write(f'#EXTINF:-1 tvg-name="{title}" tvg-logo="{vlogo}" group-title="{GROUP}",{title}\n')
            f.write(f'{m3u8}\n')
        else:
            print(f"  ⚠️  Gagal: {url}")

        time.sleep(1)

print(f"\n✅ Selesai! File: {OUTPUT}")   
