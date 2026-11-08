import requests, re, time

DATA_URL = "https://apis-data10.tcpwe108stya.ru"  # ← pakai yang benar: apis-data10.tcpwe138stya.ru
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

# Stream ID yang BENAR dari dump: 932434
resp = s.get(
    f"{DATA_URL}/api/stream/detail?streamId=932434&siteType=2001"
    f"&continent=AS&country=ID&digit=trd&matchId=2266973&sportType=6",
    timeout=15
)

content = resp.content
print(f"Status: {resp.status_code}")
print(f"Size: {len(content)} bytes, %16 = {len(content) % 16}")
print(f"rb-session: {resp.headers.get('rb-session', 'MISSING')[:100]}")
print(f"First 300: {content[:300]!r}")

if len(content) >= 16 and len(content) % 16 == 0:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    try:
        pt = unpad(cipher.decrypt(content), 16)
        print(f"\n✓ AES OK: {pt[:500]!r}")
        print(f"  Text: {pt.decode('utf-8', errors='replace')[:500]}")
    except Exception as e:
        print(f"\n✗ AES: {e}")
        raw = cipher.decrypt(content)
        print(f"  No-unpad: {raw[:300]!r}")
else:
    # Maybe it's protobuf with AES field inside
    print("\nNot full-AES. Checking as protobuf...")
    # Look for AES-sized fields
    import re as re2
    # Just print all printable strings
    strs = re2.findall(rb'[\x20-\x7e]{10,}', content)
    for st in strs[:20]:
        print(f"  {st.decode()[:150]}")   
