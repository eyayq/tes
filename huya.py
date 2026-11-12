import requests
import json
import re
import time
import base64
from urllib.parse import urlparse, unquote, quote

API_URL = "https://servicebuss.transvision.co.id/global/v4/channel-list?page=1&per_page=200&platform_id=1"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZXZpY2VfaWQiOiJ3ZWJfYnJvd3NlciIsImVtYWlsIjoibWFzdGVyX2FjY291bnRAdHJhbnN2aXNpb24uY28uaWQiLCJleHAiOjE3OTQ1NjY2MDAsImhvdGVsX2lkIjpudWxsLCJob3RlbF9uYW1lIjpudWxsLCJpc19iMmIiOmZhbHNlLCJpc19kZXZpY2VfdW5wYWlyZWQiOmZhbHNlLCJpc190diI6ZmFsc2UsImlzcF9pZCI6IjYiLCJpc3BfbmFtZSI6IlRyYW5zdmlzaW9uIiwicGFja2FnZV9saXN0IjpudWxsLCJwaG9uZSI6IjA4MTIwMDAxOTIwMSIsInJvbGVfaWQiOiI5IiwidXNlcl9pZCI6OTl9.aWxlZzjfPxBqWgkgiACFn80ffyVR42at7Fdh1fQcGxU"
OUTPUT = "playlist.m3u"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"

WIDEVINE_LICENSE_URL = "https://cubmu.ceriatv.workers.dev/"

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

# ─── Helpers ───
def b64_to_hex(b64_str: str) -> str:
    return base64.b64decode(b64_str).hex()

def b64_decode_safe(b64_str: str) -> str:
    """Decode base64 dengan auto-padding. Fallback ke string asli kalau gagal."""
    try:
        padding = 4 - len(b64_str) % 4
        if padding != 0:
            b64_str += "=" * padding
        return base64.b64decode(b64_str).decode("utf-8")
    except Exception:
        return b64_str

def decode_manifest_url(manifest_url: str) -> dict:
    parsed = urlparse(manifest_url)
    result = {
        "cdn_host": parsed.netloc,
        "stream_id": "",
        "channel_path_raw": "",
        "channel_path_decoded": "",
        "signature_raw": "",
        "signature_decoded": "",
        "time": "",
        "device_id": "",
        "full_decoded_url": manifest_url,
    }

    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 4:
        result["stream_id"] = path_parts[0]
        result["channel_path_raw"] = path_parts[2]
        result["channel_path_decoded"] = b64_decode_safe(path_parts[2])

    query = parsed.query
    params = dict(re.findall(r'([^&=]+)=([^&]*)', query))
    result["signature_raw"] = params.get("signature", "")
    result["time"] = params.get("time", "")
    result["device_id"] = params.get("device_id", "")
    result["signature_decoded"] = b64_decode_safe(result["signature_raw"])

    # Build URL dengan path decoded (URL-encoded)
    if result["channel_path_decoded"] != result["channel_path_raw"]:
        decoded_path = parsed.path.replace(
            result["channel_path_raw"],
            quote(result["channel_path_decoded"], safe="")
        )
        result["full_decoded_url"] = f"{parsed.scheme}://{parsed.netloc}{decoded_path}?{query}"

    return result

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
            "drm_type": props.get("drmType", ""),
        }
    except Exception as e:
        print(f"  ⚠️  {e}")
        return {}

# ─── DETEKSI DRM ───
def detect_drm(clearkey: dict, drm_detail: dict, drm_type: str) -> dict:
    result = {"type": "none", "kid": "", "key": "", "license_url": ""}

    if drm_type:
        dt = drm_type.lower()
        if "clearkey" in dt or "clear_key" in dt:
            result["type"] = "clearkey"
        elif "widevine" in dt:
            result["type"] = "widevine"

    if clearkey and clearkey.get("valid"):
        result["type"] = "clearkey"
        result["kid"] = b64_to_hex(clearkey["kid"])
        result["key"] = b64_to_hex(clearkey["k"])
        return result

    if drm_detail:
        if isinstance(drm_detail, dict):
            for field in ["license_url", "licenseServer", "license_server", "url", "endpoint"]:
                if drm_detail.get(field):
                    result["type"] = "widevine"
                    result["license_url"] = drm_detail[field]
                    return result
            for field in ["kid", "KID", "key_id"]:
                if drm_detail.get(field):
                    result["type"] = "widevine"
                    result["kid"] = drm_detail[field]
                    break
        elif isinstance(drm_detail, str) and drm_detail:
            result["type"] = "widevine"
            result["license_url"] = drm_detail

    return result

def write_drm_props(f, drm_info: dict):
    if drm_info["type"] == "clearkey":
        f.write('#KODIPROP:inputstream=inputstream.adaptive\n')
        f.write('#KODIPROP:inputstream.adaptive.manifest_type=dash\n')
        f.write('#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey\n')
        f.write(f'#KODIPROP:inputstream.adaptive.license_key={drm_info["kid"]}:{drm_info["key"]}\n')
    elif drm_info["type"] == "widevine":
        f.write('#KODIPROP:inputstream=inputstream.adaptive\n')
        f.write('#KODIPROP:inputstream.adaptive.manifest_type=dash\n')
        f.write('#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha\n')
        license_url = drm_info["license_url"] or WIDEVINE_LICENSE_URL
        f.write(f'#KODIPROP:inputstream.adaptive.license_key={license_url}\n')

# ─── Main ───
r = requests.get(API_URL, headers=headers_api)
data = r.json()

if data.get("status") != "success":
    print(f"Error: {data.get('message')}")
    exit(1)

items = data["data"]["items"]
total_channels = 0
failed = 0
stats = {"clearkey": 0, "widevine": 0, "none": 0}
decoded_urls = []

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
            drm_detail = info.get("drm_detail", {})
            drm_type = info.get("drm_type", "")

            if not manifest_url:
                print(f"  ❌ {name} (ch {ch_num}) — manifest kosong")
                failed += 1
                continue

            decoded = decode_manifest_url(manifest_url)
            decoded_urls.append({
                "channel": name,
                "cdn": decoded["cdn_host"],
                "channel_path_raw": decoded["channel_path_raw"],
                "channel_path_decoded": decoded["channel_path_decoded"],
                "signature_raw": decoded["signature_raw"],
                "signature_decoded": decoded["signature_decoded"],
                "time": decoded["time"],
                "device_id": decoded["device_id"],
            })

            drm_info = detect_drm(clearkey, drm_detail, drm_type)
            stats[drm_info["type"]] += 1
            icon = {"clearkey": "🔑", "widevine": "🛡️", "none": "📡"}[drm_info["type"]]

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre_name}",{name}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={UA}\n')
            f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
            f.write(f'#EXTVLCOPT:http-referer={REFERER}\n')

            write_drm_props(f, drm_info)
            f.write(f'{decoded["full_decoded_url"]}\n')  # ← URL DECODED
            total_channels += 1

            print(f"  {icon} {name} (ch {ch_num}) [{drm_info['type'].upper()}]")
            print(f"       path: {decoded['channel_path_raw']} → {decoded['channel_path_decoded']}")
            print(f"       sig:  {decoded['signature_decoded'][:16]}... | time={decoded['time']}")

            time.sleep(0.5)

with open("decoded_urls.json", "w", encoding="utf-8") as f:
    json.dump(decoded_urls, f, indent=2, ensure_ascii=False)

print(f"\n✅ Selesai! {total_channels} channel, {failed} gagal.")
print(f"   File: {OUTPUT}")
print(f"   Decode: decoded_urls.json")
print(f"   🔑 ClearKey : {stats['clearkey']}")
print(f"   🛡️  Widevine : {stats['widevine']}")
print(f"   📡 No DRM   : {stats['none']}")   
