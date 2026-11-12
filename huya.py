import subprocess
import json
import time
import sys

USER_URL = "https://www.dailymotion.com/user/Film_Horor_Indonesia"
OUTPUT = "playlist.m3u"
GROUP = "Film Horor Indonesia"

# Step 1: Get video list via yt-dlp (flat = fast, no download)
result = subprocess.run(
    ["yt-dlp", "--flat-playlist", "-J", USER_URL],
    capture_output=True, text=True
)

if result.returncode != 0:
    print(f"Error: {result.stderr}")
    sys.exit(1)

data = json.loads(result.stdout)
entries = data.get("entries", [])
logo = data.get("thumbnail", "")  # avatar user
print(f"Total: {len(entries)} video\n")

# Step 2: Write M3U
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for i, v in enumerate(entries, 1):
        url = v.get("url") or f"https://www.dailymotion.com/video/{v.get('id')}"
        title = v.get("title", "Unknown")
        vlogo = v.get("thumbnail", logo)
        print(f"[{i}/{len(entries)}] {title}")

        # Get m3u8 stream URL
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
