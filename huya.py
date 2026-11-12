import requests
import base64
import json
import sys

WORKER_URL = "https://ch.bhns.workers.dev"  # ← ganti
OUTPUT = "playlist.m3u"

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
ORIGIN = "https://www.cubmu.com"
REFERER = "https://www.cubmu.com/"


def b64_to_hex(b64_str: str) -> str:
    return base64.b64decode(b64_str).hex()


def main():
    print(f"📡 Fetch dari Worker: {WORKER_URL}/api/channels")
    resp = requests.get(f"{WORKER_URL}/api/channels", timeout=120)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code} — {resp.text}")
        exit(1)

    data = resp.json()
    channels = data["channels"]
    print(f"✅ {data['count']} channel diterima")

    total = 0
    failed = 0
    clearkey_count = 0
    device_ids = set()

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for ch in channels:
            name = ch["name"]
            logo = ch["logo"]
            ch_num = ch["channel_number"]
            genre = ch["genre"]
            slug = ch["slug"]
            manifest = ch["manifest"]
            device_id = ch["device_id"]
            clearkey = ch["clearkey"]

            if not manifest:
                print(f"  ❌ {name} (ch {ch_num}) — no manifest")
                failed += 1
                continue

            device_ids.add(device_id)
            is_clearkey = clearkey and clearkey.get("valid")
            if is_clearkey:
                clearkey_count += 1

            # M3U entry
            f.write(f'#EXTINF:0 tvg-name="{name}" tvg-id="{ch_num}" tvg-logo="{logo}" group-title="{genre}",{name}\n')

            # VLC headers
            f.write(f'#EXTVLCOPT:http-user-agent={UA}\n')
            f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
            f.write(f'#EXTVLCOPT:http-referer={REFERER}\n')

            # Kodi headers
            f.write(f'#KODIPROP:inputstream.adaptive.user_agent={UA}\n')
            f.write(f'#KODIPROP:inputstream.adaptive.headers=Origin:{ORIGIN};Referer:{REFERER}\n')

            # ClearKey (hex)
            if is_clearkey:
                kid_hex = b64_to_hex(clearkey["kid"])
                key_hex = b64_to_hex(clearkey["k"])
                f.write('#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey\n')
                f.write(f'#KODIPROP:inputstream.adaptive.license_key={kid_hex}:{key_hex}\n')

            # URL via Worker (302)
            f.write(f'{WORKER_URL}/stream/{slug}\n\n')

            total += 1
            mark = "🔓" if is_clearkey else "🔒"
            print(f"  {mark} {name} (ch {ch_num}) [{device_id[:12]}...]")

    # ─── Summary ───
    print(f"\n{'='*55}")
    print(f"✅ Total: {total} channel, {failed} gagal")
    print(f"   🔓 ClearKey: {clearkey_count}")
    print(f"   🆔 Device ID: {', '.join(d[:16] + '...' for d in device_ids) if device_ids else 'N/A'}")
    print(f"   📄 File: {OUTPUT}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()   
