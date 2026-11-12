import requests
import json
import re
import time

API_URL = "https://servicebuss.transvision.co.id/global/v4/channel-list?page=1&per_page=200&platform_id=1"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTQ1NjY2MDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.aWxlZzjfPxBqWgkgiACFn80ffyVR42at7Fdh1fQcGxU"
OUTPUT = "transvision.m3u"

# ─── Cookies (WAJIB agar device_id konsisten) ───
cookies = {
    'device_fp': 'U2FsdGVkX1+zdgtg0njhkkToFtWQAolfZL67uwGwUr0C1txwf6/imhUvjDuwLSVMu3vB6Ozru0fL/TdQZHjtNKMGQlJlsFC8iTJKWND8Pp/eX32QLev6okQFDAYBodWVh0u/7cJj+3MFkiisSI+MhRk2FUNJxSE76fta6TX9v0slsGidZrLcp4qzPznkqzHEn+FS0Z8xmthQk7Ku/kF2bvwr0lkAH8NJuzo35YwKntIKYd50aHK2aL08re1ZqcAHbIUynKn5QTt4NTBLRrcqOje2uO3qvSTwnzvCt9c05PbJiwcMGk6r2dMb5OibvaNOkEd+iKafNNCQFV9+ux7j1g==',
    'access_token': TOKEN,
    'refresh_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTcwNzIyMDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwianRpIjoiOTNjMTA4ODEtYjRlZS00ZDE1LTkyMTQtZDljZGVlMDUzNmRkIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.n_czjsLezTMxsqEaOyhvdQwGyjrxvOU3JOsucUqSwAM',
    'bitmovin_analytics_uuid': '4de777be-9165-444c-8913-330c299a03ae',
}

headers_api = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.cubmu.com",
    "Referer": "https://www.cubmu.com/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
}

headers_page = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.5",
    "Referer": "https://www.cubmu.com/live-tv",
}

# ─── Fetch channel list ───
r = requests.get(API_URL, headers=headers_api)
data = r.json()

if data.get("status") != "success":
    print(f"Error: {data.get('message')}")
    exit(1)

items = data["data"]["items"]
total_channels = 0
failed = 0

def get_next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        return {}
    return json.loads(m.group(1))

def get_stream_info(slug: str) -> dict:
    url = f"https://www.cubmu.com/watch/live-tv/{slug}"
    try:
        resp = requests.get(url, headers=headers_page, cookies=cookies, timeout=15)
        nd = get_next_data(resp.text)
        props = nd.get("props", {}).get("pageProps", {})
        return {
            "manifest": props.get("manifest", ""),
            "clearkey": props.get("sourceDrmClearKey", {}),
            "drm_detail": props.get("drmDetail", {}),
            "device_id": props.get("deviceId", ""),
        }
    except Exception as e:
        print(f"  ⚠️  {e}")
        return {}

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for genre in items:
        genre_name = genre["genre_name"]
        print(f"\n📺 {genre_name}: {len(genre['channels'])} channel")
        for ch in genre["channels"]:
            name = ch["channel_name"]
            logo = ch["channel_image"]
            ch_num = ch["channel_number"]
            slug = ch.get("slug_url", f"{ch['channel_id']}-{name.lower().replace(' ', '-')}")

            info = get_stream_info(slug)
            manifest_url = info.get("manifest", "")
            clearkey = info.get("clearkey", {})

            if not manifest_url:
                print(f"  ❌ {name} (ch {ch_num}) — manifest kosong")
                failed += 1
                continue

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre_name}",{name}\n')

            # CLEARKEY
            if clearkey and clearkey.get("valid"):
                kid = clearkey["kid"]
                key = clearkey["k"]
                f.write('#KODIPROP:inputstream.adaptive.license_type=clearkey\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={kid}:{key}\n')

            f.write(f'{manifest_url}\n')
            total_channels += 1
            print(f"  ✅ {name} (ch {ch_num})")

            time.sleep(0.5)

print(f"\n✅ Selesai! {total_channels} channel, {failed} gagal. File: {OUTPUT}")   
