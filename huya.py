import subprocess
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

USERS = [
    {"url": "https://www.dailymotion.com/user/kuramanime", "group": "Kuramanime"},
    {"url": "https://www.dailymotion.com/user/k-dramaa", "group": "Korean Drama"},
    {"url": "https://www.dailymotion.com/user/Film_Horor_Indonesia", "group": "Film Horor Indonesia"},
    {"url": "https://www.dailymotion.com/user/sugondofilm.55", "group": "Film Horor Indonesia 2"},
]

OUTPUT = "playlist.m3u"
MAX_WORKERS = 7
RETRIES = 2

def fetch_video(url):
    for attempt in range(RETRIES):
        result = subprocess.run(
            ["yt-dlp", "-J", "-f", "b", url],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            try:
                d = json.loads(result.stdout)
                return {
                    "title": d.get("title", "Unknown"),
                    "thumbnail": d.get("thumbnail", ""),
                    "m3u8": d.get("url", "")
                }
            except json.JSONDecodeError:
                pass
        if attempt < RETRIES - 1:
            wait = (attempt + 1) * 5
            print(f"  Retry {attempt+1} ({wait}s): {url}")
            time.sleep(wait)
    return {"title": None, "thumbnail": None, "m3u8": None}

# Collect all entries
all_entries = []
for user in USERS:
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-J", user["url"]],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"⚠️  Error: {user['url']}")
        continue
    data = json.loads(result.stdout)
    entries = data.get("entries", [])
    count = 0
    for e in entries:
        url = e.get("url") or f"https://www.dailymotion.com/video/{e.get('id', '')}"
        if url and url.startswith("http"):
            all_entries.append({"url": url, "group": user["group"]})
            count += 1
    print(f"✅ {user['group']}: {count} video")

print(f"\nTotal: {len(all_entries)} video\n")

# Fetch all (paralel)
def fetch_with_group(entry):
    r = fetch_video(entry["url"])
    r["group"] = entry["group"]
    return r

results = [None] * len(all_entries)
with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(fetch_with_group, e): i for i, e in enumerate(all_entries)}
    done = 0
    for future in as_completed(futures):
        i = futures[future]
        results[i] = future.result()
        done += 1
        r = results[i]
        status = "✅" if r["m3u8"] else "⚠️"
        print(f"[{done}/{len(all_entries)}] {status} {r['title']}")

# Write M3U
with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for r in results:
        if r and r["m3u8"]:
            title = r["title"] or "Unknown"
            vlogo = r["thumbnail"] or ""
            f.write(f'#EXTINF:-1 tvg-name="{title}" tvg-logo="{vlogo}" group-title="{r["group"]}",{title}\n')
            f.write(f'{r["m3u8"]}\n')

ok = sum(1 for r in results if r and r["m3u8"])
fail = len(results) - ok
print(f"\n✅ Selesai! {ok} sukses, {fail} gagal. File: {OUTPUT}")   
