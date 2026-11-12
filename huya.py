import requests
import subprocess
import time

USER = "Film_Horor_Indonesia"
OUTPUT = "playlist.m3u"
GROUP = "Film Horor Indonesia"  # group-title

# Step 1: Get user ID + avatar (untuk tvg-logo)
r = requests.get("https://api.dailymotion.com/user",
    params={"screen_name": USER, "fields": "id,name,thumbnail_url"})
data = r.json()
user_id = data["id"]
logo = data.get("thumbnail_url", "")
print(f"User: {data['name']} (ID: {user_id})")
print(f"Logo: {logo}")

# Step 2: Collect all videos
video_ids = []
start = 0
while True:
    r = requests.get("https://api.dailymotion.com/videos",
        params={"owner": user_id, "fields": "id,title,thumbnail_url", "limit": 20, "start": start})
    batch = r.json().get("list", [])
    if not batch:
        break
    video_ids.extend(batch)
    start += 20
    if start >= r.json().get("total", 0):
        break

print(f"Total: {len(video_ids)} video\n")

# Step 3: Write M3U with full attributes
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for i, v in enumerate(video_ids, 1):
        url = f"https://www.dailymotion.com/video/{v['id']}"
        title = v["title"]
        vlogo = v.get("thumbnail_url", logo)  # thumbnail video, fallback ke logo user
        print(f"[{i}/{len(video_ids)}] {title}")

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
