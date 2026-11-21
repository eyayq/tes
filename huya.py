from curl_cffi import requests as cffi_requests
import json, zlib, base64

PROXY_URL = "http://voifrfqj:w50vbiwbunum@23.26.71.145:5628"
PROXY_CURL = {"http": PROXY_URL, "https": PROXY_URL}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Origin": "https://sankanime.web.id",
    "Referer": "https://sankanime.web.id/",
}

url = "https://www.sankavollerei.web.id/plananimek/id/genre/23?page=0&sort=views&genre_in="

# Test 1: Direct (tanpa proxy)
print("=== DIRECT ===")
try:
    r = cffi_requests.get(url, impersonate="chrome", headers=HEADERS, timeout=20)
    print(f"Status: {r.status_code}")
    print(f"Body: {r.text[:200]}")
except Exception as e:
    print(f"Error: {e}")

print()

# Test 2: Proxy
print("=== PROXY ===")
try:
    r = cffi_requests.get(url, impersonate="chrome", headers=HEADERS, proxies=PROXY_CURL, timeout=20)
    print(f"Status: {r.status_code}")
    print(f"Body: {r.text[:200]}")
except Exception as e:
    print(f"Error: {e}")   
