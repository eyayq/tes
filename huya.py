#!/usr/bin/env python3
"""CEK ROOM DOUYU — test cepat 1 room ID"""

import time
import hashlib
import uuid
import sys

from curl_cffi import requests as cffi_requests

ROOM_ID = sys.argv[1] if len(sys.argv) > 1 else "36252"
TIMEOUT = 15

s = cffi_requests.Session(impersonate="chrome")
try:
    s.get("https://www.douyu.com/", timeout=TIMEOUT)
except Exception:
    pass


def build_stream_url(rtmp_url, rtmp_live):
    if not rtmp_url or not rtmp_live:
        return None
    if '.m3u8' in rtmp_live:
        return None
    if rtmp_url.startswith('rtmp://'):
        rtmp_url = rtmp_url.replace('rtmp://', 'http://')
    return f"{rtmp_url}/{rtmp_live}"


def method1():
    print("[Method 1] coapi (MD5 sign)...")
    tt = str(int(time.time()))
    sign_content = (
        f"lapi/live/thirdPart/getPlay/{ROOM_ID}"
        f"?aid=pcclient&rate=0&time={tt}9TUk5fjjUjg9qIMH3sdnh"
    )
    sign = hashlib.md5(sign_content.encode('ascii')).hexdigest()
    api = (
        f"https://coapi.douyucdn.cn/lapi/live/thirdPart/getPlay/{ROOM_ID}"
        f"?rate=0&cdn=ws-h5&auth={sign}"
    )
    try:
        r = s.get(api, timeout=TIMEOUT)
        print(f"  Status: {r.status_code}")
        data = r.json()
        if data.get('error') == 0:
            d = data.get('data', {})
            url = build_stream_url(d.get('rtmp_url', ''), d.get('rtmp_live', ''))
            if url:
                print(f"  ✅ SUCCESS: {url}")
                return url
        print(f"  ❌ error={data.get('error')} msg={data.get('msg')}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return None


def method2():
    print("[Method 2] getH5PlayV1 (encryption)...")
    did = uuid.uuid4().hex
    ts = str(int(time.time()))

    enc_api = f"https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={did}"
    try:
        r = s.get(enc_api, timeout=TIMEOUT)
        data = r.json()
        if data.get('error') != 0:
            print(f"  ❌ enc error={data.get('error')}")
            return None
        enc_data = data.get('data', {})
    except Exception as e:
        print(f"  ❌ Exception: {e}")
        return None

    key = enc_data.get('key', '')
    rand_str = enc_data.get('rand_str', '')
    enc_time = int(enc_data.get('enc_time', 0))
    enc_data_str = enc_data.get('enc_data', '')

    if not key or not rand_str:
        print("  ❌ No key/rand_str")
        return None

    f = rand_str
    for _ in range(enc_time):
        f = hashlib.md5((f + key).encode('utf-8')).hexdigest()
    auth = hashlib.md5((f + key + ROOM_ID + ts).encode('utf-8')).hexdigest()

    api = f"https://www.douyu.com/lapi/live/getH5PlayV1/{ROOM_ID}"
    form_data = {
        'enc_data': enc_data_str,
        'tt': ts,
        'did': did,
        'auth': auth,
        'cdn': 'ws-h5',
        'rate': '0',
        'ver': 'Douyu_new',
        'iar': '0',
        'ive': '0',
        'prev': '1',
        'bs': '2',
    }
    try:
        r = s.post(api, data=form_data, timeout=TIMEOUT)
        print(f"  Status: {r.status_code}")
        data = r.json()
        if data.get('error') == 0:
            d = data.get('data', {})
            url = build_stream_url(d.get('rtmp_url', ''), d.get('rtmp_live', ''))
            if url:
                print(f"  ✅ SUCCESS: {url}")
                return url
        print(f"  ❌ error={data.get('error')} msg={data.get('msg')}")
    except Exception as e:
        print(f"  ❌ Exception: {e}")
    return None


# ─── Run ─────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  CEK ROOM DOUYU: {ROOM_ID}")
print(f"{'='*55}\n")

url = method1()
if not url:
    url = method2()

print(f"\n{'='*55}")
if url:
    print(f"  HASIL: {url}")
    # Test apakah URL bisa diakses
    print("\n[TEST] Mengakses URL stream...")
    try:
        r = s.get(url, timeout=TIMEOUT, stream=True)
        print(f"  HTTP {r.status_code} | Content-Type: {r.headers.get('Content-Type','?')}")
        if r.status_code == 200:
            print("  ✅ Stream aktif & bisa diakses!")
        else:
            print(f"  ❌ Stream tidak bisa diakses (HTTP {r.status_code})")
        r.close()
    except Exception as e:
        print(f"  ❌ Gagal akses: {e}")
else:
    print("  ❌ Gagal ambil URL dari semua method.")
print(f"{'='*55}\n")   
