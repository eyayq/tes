import requests
import base64
import time

WORKER_URL = "https://ch.bhns.workers.dev"
OUTPUT = "playlist.m3u"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"


def b64_to_hex(b64_str: str) -> str:
    return base64.b64decode(b64_str).hex()


def main():
    # Step 1: Fetch channel list (cepat, < 3 detik)
    print(f"📡 Fetch channel list: {WORKER_URL}/api/channels")
    resp = requests.get(f"{WORKER_URL}/api/channels", timeout=30)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code} — {resp.text[:200]}")
        exit(1)

    data = resp.json()
    channels = data["channels"]
    print(f"✅ {data['count']} channel")

    total = 0
    failed = 0
    clearkey_count = 0

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for i, ch in enumerate(channels, 1):
            name = ch["name"]
            logo = ch["logo"]
            ch_num = ch["channel_number"]
            genre = ch["genre"]
            slug = ch["slug"]

            # Step 2: Fetch detail per channel (1 request per channel)
            try:
                detail_resp = requests.get(f"{WORKER_URL}/api/channel/{slug}", timeout=15)
                if detail_resp.status_code != 200:
                    print(f"  ❌ [{i}/{len(channels)}] {name} — {detail_resp.status_code}")
                    failed += 1
                    continue
                detail = detail_resp.json()
            except Exception as e:
                print(f"  ❌ [{i}/{len(channels)}] {name} — {e}")
                failed += 1
                continue

            manifest = detail.get("manifest")
            device_id = detail.get("device_id")
            clearkey = detail.get("clearkey")

            if not manifest:
                print(f"  ❌ [{i}/{len(channels)}] {name} — no manifest")
                failed += 1
                continue

            is_clearkey = clearkey and clearkey.get("valid")
            if is_clearkey:
                clearkey_count += 1

            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre}",{name}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={UA}\n')
            f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
            f.write(f'#EXTVLCOPT:http-referer={REFERER}\n')
            f.write(f'#KODIPROP:inputstream.adaptive.user_agent={UA}\n')
            f.write(f'#KODIPROP:inputstream.adaptive.headers=Origin:{ORIGIN};Referer:{REFERER}\n')

            if is_clearkey:
                kid_hex = b64_to_hex(clearkey["kid"])
                key_hex = b64_to_hex(clearkey["k"])
                f.write('#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={kid_hex}:{key_hex}\n')

            f.write(f'{WORKER_URL}/stream/{slug}\n\n')

            total += 1
            mark = "🔓" if is_clearkey else "🔒"
            print(f"  {mark} [{i}/{len(channels)}] {name} (ch {ch_num})")
            time.sleep(0.3)  # rate limit

    print(f"\n{'='*55}")
    print(f"✅ Total: {total} channel, {failed} gagal")
    print(f"   🔓 ClearKey: {clearkey_count}")
    print(f"   📄 File: {OUTPUT}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()   
