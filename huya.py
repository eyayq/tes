import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
AES_KEY = b"a7981cc9eb2f4d19dcfea57b101ecd89"
AES_IV  = b"8017d3a8f1400d2f"

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "origin": "https://jack01eo.m8ow6pride8nly2qearly.cfd",
    "referer": "https://jack01eo.m8ow6pride8nly2qearly.cfd/",
}

s = requests.Session()
s.headers.update(HEADERS)

# Pakai streamId dari fetch Anda
resp = s.get(
    f"{DATA_URL}/api/stream/detail?streamId=932428&siteType=2001"
    f"&continent=AS&country=ID&digit=trd&matchId=2266971&sportType=6",
    timeout=15
)

content = resp.content
print(f"Status: {resp.status_code}")
print(f"Size: {len(content)} bytes")
print(f"% 16 = {len(content) % 16}")
print(f"rb-session: {resp.headers.get('rb-session', 'MISSING')[:100]}")
print(f"Content-Encoding: {resp.headers.get('content-encoding', 'none')}")
print()

if len(content) >= 16 and len(content) % 16 == 0:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    try:
        pt = unpad(cipher.decrypt(content), 16)
        print(f"✓ AES DECRYPT SUCCESS ({len(pt)} bytes)")
        print(f"  Plaintext: {pt[:500]!r}")
        # Try decode
        text = pt.decode('utf-8', errors='replace')
        print(f"  As text: {text[:500]}")
    except Exception as e:
        print(f"✗ unpad failed: {e}")
        raw = cipher.decrypt(content)
        print(f"  Raw (no unpad): {raw[:300]!r}")
        # Maybe it's protobuf after decrypt
        print(f"  First byte: 0x{raw[0]:02x} (protobuf tag?)")
else:
    print("✗ Not multiple of 16 — not AES-CBC")
    print(f"  First 200: {content[:200]!r}")   
