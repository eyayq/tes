import requests
import json
import re
import time
import base64

PROXY_URL = "https://exsample.d-n-s.name/index.php"
PROXY_SECRET = "ganti-dengan-secret-panjang"
OUTPUT = "playlist.m3u"

API_URL = "https://servicebuss.transvision.co.id/global/v4/channel-list?page=1&per_page=200&platform_id=1"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTQ1NjY2MDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.aWxlZzjfPxBqWgkgiACFn80ffyVR42at7Fdh1fQcGxU"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"

cookies = {
    'device_fp': 'U2FsdGVkX1+zdgtg0njhkkToFtWQAolfZL67uwGwUr0C1txwf6/imhUvjDuwLSVMu3vB6Ozru0fL/TdQZHjtNKMGQlJlsFC8iTJKWND8Pp/eX32QLev6okQFDAYBodWVh0u/7cJj+3MFkiisSI+MhRk2FUNJxSE76fta6TX9v0slsGidZrLcp4qzPznkqzHEn+FS0Z8xmthQk7Ku/kF2bvwr0lkAH8NJuzo35YwKntIKYd50aHK2aL08re1ZqcAHbIUynKn5QTt4NTBLRrcqOje2uO3qvSTwnzvCt9c05PbJiwcMGk6r2dMb5OibvaNOkEd+iKafNNCQFV9+ux7j1g==',
    'access_token': TOKEN,
    'refresh_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTcwNzIyMDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwianRpIjoiOTNjMTA4ODEtYjRlZS00ZDE1LTkyMTQtZDljZGVlMDUzNmRkIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.n_czjsLezTMxsqEaOyhvdQwGyjrxvOU3JOsucUqSwAM',
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


def b64_to_hex(b64_str):
    return base64.b64decode(b64_str).hex()


def get_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        return {}
    return json.loads(m.group(1))


def scrape_channel(slug):
    url = f"https://www.cubmu.com/watch/live-tv/{slug}"
    resp = requests.get(url, headers=headers_page, cookies=cookies, timeout=15)
    nd = get_next_data(resp.text)
    props = nd.get("props", {}).get("pageProps", {})
    return {
        "manifest": props.get("manifest", ""),
        "device_id": props.get("deviceId", ""),
        "clearkey": props.get("sourceDrmClearKey", {}),
    }


def push_to_proxy(channels):
    resp = requests.post(
        PROXY_URL,
        json={"channels": channels},
        headers={"Authorization": f"Bearer {PROXY_SECRET}"},
        timeout=30,
    )
    return resp.json()


def main():
    print("📡 Fetch channel list...")
    r = requests.get(API_URL, headers=headers_api)
    data = r.json()
    if data.get("status") != "success":
        print(f"Error: {data.get('message')}")
        exit(1)

    items = data["data"]["items"]
    all_channels = []

    print(f"🔍 Scrape channel detail...")
    for genre in items:
        for ch in genre["channels"]:
            slug = ch.get("slug_url", f"{ch['channel_id']}-{ch['channel_name'].lower().replace(' ', '-')}")
            info = scrape_channel(slug)
            all_channels.append({
                "slug": slug,
                "name": ch["channel_name"],
                "channel_number": ch["channel_number"],
                "logo": ch["channel_image"],
                "genre": genre["genre_name"],
                **info,
            })
            time.sleep(0.3)

    print(f"📤 Push {len(all_channels)} channel ke proxy...")
    result = push_to_proxy(all_channels)
    print(f"   Proxy: {result}")

    total = 0
    failed = 0
    clearkey_count = 0

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in all_channels:
            name = ch["name"]
            logo = ch["logo"]
            ch_num = ch["channel_number"]
            genre = ch["genre"]
            slug = ch["slug"]
            manifest = ch["manifest"]
            clearkey = ch["clearkey"]

            if not manifest:
                failed += 1
                continue

            is_clearkey = clearkey and clearkey.get("valid")
            if is_clearkey:
                clearkey_count += 1

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre}",{name}\n')
            f.write(f'#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey\n')
            if is_clearkey:
                kid_hex = b64_to_hex(clearkey["kid"])
                key_hex = b64_to_hex(clearkey["k"])
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={kid_hex}:{key_hex}\n')

            # URL: full proxy (bukan 302)
            f.write(f'{PROXY_URL}?slug={slug}\n\n')
            total += 1
            mark = "🔓" if is_clearkey else "🔒"
            print(f"  {mark} {name} (ch {ch_num})")

    print(f"\n{'='*55}")
    print(f"✅ Total: {total} channel, {failed} gagal")
    print(f"   🔓 ClearKey: {clearkey_count}")
    print(f"   📄 File: {OUTPUT}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()   
