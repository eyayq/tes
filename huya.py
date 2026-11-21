import json, time, zlib, base64
from urllib.parse import unquote
from curl_cffi import requests as cffi_requests

BASE = "https://www.sankavollerei.web.id"
ID_BASE = f"{BASE}/plananimek/id"
PROXY_URL = "http://voifrfqj:w50vbiwbunum@23.26.71.145:5628"
PROXY_CURL = {"http": PROXY_URL, "https": PROXY_URL}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://sankanime.web.id",
    "Referer": "https://sankanime.web.id/",
}

def decode(data):
    if not isinstance(data, dict):
        return data
    enc = data.get("_encsankaa")
    if not enc:
        return data
    b64 = enc.replace('-', '+').replace('_', '/')
    pad = 4 - (len(b64) % 4)
    if pad != 4:
        b64 += '=' * pad
    out = zlib.decompress(base64.b64decode(b64))
    return json.loads(out)

# Test
url = f"{ID_BASE}/genre/23?page=0&sort=views&genre_in="
r = cffi_requests.get(url, impersonate="chrome", headers=HEADERS, proxies=PROXY_CURL, timeout=20)
print(f"Status: {r.status_code}")

raw = r.json()
print(f"Raw keys: {list(raw.keys())}")

decoded = decode(raw)
print(f"Decoded keys: {list(decoded.keys()) if isinstance(decoded, dict) else type(decoded)}")
print(f"Preview: {json.dumps(decoded, ensure_ascii=False)[:500]}")   
