import requests, re, time

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "origin": "https://jack01eo.m8ow6pride8nly2qearly.cfd",
    "referer": "https://jack01eo.m8ow6pride8nly2qearly.cfd/",
}

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"
AES_IV  = b"8017d3a8f1400d2f"

s = requests.Session()
s.headers.update(HEADERS)

resp = s.get(
    f"{DATA_URL}/api/stream/detail?streamId=932428&siteType=2001"
    f"&continent=AS&country=ID&digit=trd&matchId=2266971&sportType=6",
    timeout=15
)
content = resp.content
print(f"Raw size: {len(content)} bytes, %16 = {len(content) % 16}")
print(f"rb-session: {resp.headers.get('rb-session', 'MISSING')[:80]}")

if len(content) % 16 == 0:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    try:
        pt = unpad(cipher.decrypt(content), 16)
        print(f"\n✓ AES DECRYPT SUCCESS ({len(pt)} bytes):")
        print(pt.decode('utf-8', errors='replace')[:500])
    except Exception as e:
        print(f"\n✗ AES decrypt failed: {e}")
        # Try without unpad
        raw = cipher.decrypt(content)
        print(f"  Raw decrypt (no unpad): {raw[:200]!r}")
else:
    print("Not a multiple of 16 — not AES. First 200 bytes:")
    print(content[:200])   
