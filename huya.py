
import requests
import json
import re
import time
import base64
from urllib.parse import quote

API_URL = "https://servicebuss.transvision.co.id/global/v4/channel-list?page=1&per_page=200&platform_id=1"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTQ4NDIwMzksImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.BATvz76DFcxG7sozXbD-jK1liuE8ReLjhL6pKBY8M6k"
OUTPUT = "playlist.m3u"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"

cookies = {
    'device_fp': 'U2FsdGVkX1+ao0jXElG0/PjbS3TRy6FpRn3dUBvQMGccx3EnW2WJvAvz9/TN9ZlXpUvAikE1A/oGELB3jJ1VjvW9GvOZFf/ryHB7G9E+fGIt+7+8PSc7EaWxG2RIhgYoJ21p9J6phrJL0xQ6u3BrHjDqFG4rFH15NF3T0s/XsqBGLMb7nqpPQ4nltMhlyBHmOpF+k5+IjfLDbLZHXo7/bER3RtHJv+NMvFVsCiz7maNT0ij6FsZXF0+ExLE3VyJsileLVb0HILmLdz0XDVYW4xLknkFYa1MTyXZfy5yCOmk3yyNJYQs2K8zsrRrfcqL8la29tIbXImAquxnuo9PNPg==',
    'access_token': TOKEN,
    'refresh_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTczNDc2MzksImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwianRpIjoiYmZiMWUzNDctMzMwYy00Y2E2LTg3MTQtNTRiOWZiMTUyMTkzIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.LCuSMTAD8FsSLMzL5eGcGjztkKeKwgZB3Hp0DzKgxLY',
    'bitmovin_analytics_uuid': '4de777be-9165-444c-8913-330c299a03ae',
}

headers_api = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/plain, */*",
    "Origin": ORIGIN,
    "Referer": REFERER,
    "User-Agent": UA,
}

headers_page = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.5",
    "Referer": "https://www.cubmu.com/live-tv",
}


def b64_to_hex(b64_str: str) -> str:
    s = b64_str.strip()
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s).hex()


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
            "clearkey": props.get("sourceDrmClearKey") or {},
            "drm_detail": props.get("drmDetail") or {},
            "device_id": props.get("deviceId", ""),
        }
    except Exception as e:
        print(f"  ⚠️  {e}")
        return {}


# ─── Fetch channel list ───
r = requests.get(API_URL, headers=headers_api)
data = r.json()

if data.get("status") != "success":
    print(f"Error: {data.get('message')}")
    exit(1)

items = data["data"]["items"]
total_channels = 0
failed = 0
clearkey_count = 0
widevine_count = 0

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
            clearkey = info.get("clearkey") or {}
            drm_detail = info.get("drm_detail") or {}

            if not manifest_url:
                print(f"  ❌ {name} (ch {ch_num}) — manifest kosong")
                failed += 1
                continue

            is_clearkey = clearkey and clearkey.get("valid")
            is_widevine = (not is_clearkey) and drm_detail.get("widevine_license_url")

            if is_clearkey:
                clearkey_count += 1
            elif is_widevine:
                widevine_count += 1

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre_name}",{name}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={UA}\n')
            f.write(f'#EXTVLCOPT:http-referer={REFERER}\n')
            f.write('#KODIPROP:inputstream=inputstream.adaptive\n')
            f.write('#KODIPROP:inputstream.adaptive.manifest_type=mpd\n')

            if is_clearkey:
                kid_hex = b64_to_hex(clearkey["kid"])
                key_hex = b64_to_hex(clearkey["k"])
                f.write('#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={kid_hex}:{key_hex}\n')
            elif is_widevine:
                f.write('#KODIPROP:inputstream.adaptive.license_type=com.widevine\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_url={drm_detail["widevine_license_url"]}\n')
                f.write('#KODIPROP:inputstream.adaptive.license_content_type=application/octet-stream\n')

            f.write(f'{manifest_url}\n\n')
            total_channels += 1

            if is_clearkey:
                mark = "🔓"
            elif is_widevine:
                mark = "🔒 W"
            else:
                mark = "🔒"
            print(f"  {mark} {name} (ch {ch_num})")

            time.sleep(0.5)

print(f"\n{'='*55}")
print(f"✅ Total: {total_channels} channel, {failed} gagal")
print(f"   🔓 ClearKey: {clearkey_count}")
print(f"   🔒 Widevine: {widevine_count}")
print(f"   📄 File: {OUTPUT}")
print(f"{'='*55}")   
