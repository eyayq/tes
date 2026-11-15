import requests
import json
import re
import time
import base64
from urllib.parse import quote

# ─── KONFIGURASI ───
PROXY_URL = "https://exsample.d-n-s.name/playlist.mpd"
API_PROXY = "https://exsample.d-n-s.name/index.php"
PROXY_SECRET = "ganti-dengan-secret-panjang"
OUTPUT = "playlist.m3u"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"


def b64_to_hex(b64_str):
    s = b64_str.strip()
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s).hex()


def fetch_channels():
    """Fetch channel list via proxy (IP server, bukan IP Python)."""
    resp = requests.get(f"{API_PROXY}?api=channels&auth={PROXY_SECRET}", timeout=30)
    if resp.status_code != 200:
        print(f"Error: {resp.status_code} — {resp.text[:200]}")
        exit(1)
    return resp.json()


def fetch_channel_detail(slug):
    """Fetch channel detail via proxy."""
    resp = requests.get(f"{API_PROXY}?api=channel&slug={quote(slug, safe='')}&auth={PROXY_SECRET}", timeout=20)
    if resp.status_code != 200:
        return {"manifest": "", "device_id": "", "clearkey": {}, "drm_detail": {}}
    return resp.json()


def push_to_proxy(channels):
    resp = requests.post(
        f"{PROXY_URL}?auth={PROXY_SECRET}",
        json={"channels": channels},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"   ❌ {resp.status_code} — {resp.text[:200]}")
        return {"error": resp.text[:200]}
    try:
        return resp.json()
    except:
        print(f"   ❌ {resp.text[:200]}")
        return {"error": "not json"}


def main():
    # Step 1: Fetch channel list (via proxy → IP server)
    print("📡 ...")
    data = fetch_channels()
    if data.get("status") != "success":
        print(f"Error: {data.get('message')}")
        exit(1)

    items = data["data"]["items"]
    total_channels = sum(len(g['channels']) for g in items)
    print(f"✅ {total_channels} channel")

    # Step 2: Fetch detail per channel (via proxy → IP server)
    print("🔍...")
    all_channels = []
    for i, genre in enumerate(items):
        for j, ch in enumerate(genre["channels"]):
            slug = ch.get("slug_url", f"{ch['channel_id']}-{ch['channel_name'].lower().replace(' ', '-')}")
            try:
                info = fetch_channel_detail(slug)
            except Exception as e:
                print(f"  ⚠️  [{i}-{j}] {ch['channel_name']} — {e}")
                info = {"manifest": "", "device_id": "", "clearkey": {}, "drm_detail": {}}

            all_channels.append({
                "slug": slug,
                "name": ch["channel_name"],
                "channel_number": ch["channel_number"],
                "logo": ch["channel_image"],
                "genre": genre["genre_name"],
                **info,
            })
            time.sleep(0.3)

    # Step 3: Push ke streaming proxy
    print(f"📤 {len(all_channels)}...")
    result = push_to_proxy(all_channels)
    print(f"   Proxy: {result}")

    # Step 4: Generate M3U
    total = 0
    failed = 0
    clearkey_count = 0
    widevine_count = 0

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in all_channels:
            name = ch["name"]
            logo = ch["logo"]
            ch_num = ch["channel_number"]
            genre = ch["genre"]
            slug = ch["slug"]
            manifest = ch["manifest"]
            clearkey = ch.get("clearkey") or {}
            drm_detail = ch.get("drm_detail") or {}

            if not manifest:
                failed += 1
                continue

            # Deteksi DRM type
            is_clearkey = clearkey and clearkey.get("valid")
            is_widevine = (not is_clearkey) and drm_detail.get("widevine_license_url")

            if is_clearkey:
                clearkey_count += 1
            elif is_widevine:
                widevine_count += 1

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="tv nasional prem",{name} [vip tes]\n')
            f.write(f'#EXTVLCOPT:http-user-agent={UA}\n')
            f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
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
                f.write(f'#KODIPROP:inputstream.adaptive.license_url={PROXY_URL}?license\n')
                f.write('#KODIPROP:inputstream.adaptive.license_content_type=application/octet-stream\n')   
    
            f.write(f'{PROXY_URL}?slug={quote(slug, safe="")}\n\n')
            total += 1
            if is_clearkey:
                mark = "🔓"
            elif is_widevine:
                mark = "🔒 W"
            else:
                mark = "🔒"
            print(f"  {mark} {name} (ch {ch_num})")

    print(f"\n{'='*55}")
    print(f"✅ Total: {total} channel, {failed} gagal")
    print(f"   🔓 ClearKey: {clearkey_count}")
    print(f"   🔒 Widevine: {widevine_count}")
    print(f"   📄 File: {OUTPUT}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()   
