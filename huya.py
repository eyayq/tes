import requests
import json

API_URL = "https://servicebuss.transvision.co.id/global/v4/channel-list?page=1&per_page=200&platform_id=1"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTQ1NjY2MDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.aWxlZzjfPxBqWgkgiACFn80ffyVR42at7Fdh1fQcGxU"

DRM_KEY = "https://cubmu.topsecret.workers.dev/license-proxy-widevine/cenc/?specConform=true"
OUTPUT = "playlist.m3u"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.cubmu.com",
    "Referer": "https://www.cubmu.com/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
}

# Fetch channel list
r = requests.get(API_URL, headers=headers)
data = r.json()

if data.get("status") != "success":
    print(f"Error: {data.get('message')}")
    exit(1)

items = data["data"]["items"]
total_channels = 0

with open(OUTPUT, "w") as f:
    f.write("#EXTM3U\n")
    for genre in items:
        genre_name = genre["genre_name"]
        print(f"\n📺 {genre_name}: {len(genre['channels'])} channel")
        for ch in genre["channels"]:
            name = ch["channel_name"]
            logo = ch["channel_image"]
            ch_id = ch["channel_id"]
            ch_num = ch["channel_number"]
            stream_url = f"https://cdnjkt4.transvision.co.id:1000/live/master/2/{ch_id}/manifest.m3u8"

            # Cek DRM type
            drm_type = ""
            if ch.get("channel_cdn_list"):
                for cdn in ch["channel_cdn_list"]:
                    if cdn.get("cdn_global_drm_type") == "MULTIDRM":
                        drm_type = "widevine"
                        break

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre_name}",{name}\n')
            if drm_type == "widevine":
                f.write(f'#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={DRM_KEY}\n')
            f.write(f'{stream_url}\n')
            total_channels += 1
            print(f"  ✅ {name} (ch {ch_num})")

print(f"\n✅ Selesai! {total_channels} channel. File: {OUTPUT}")   
