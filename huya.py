import requests, re, base64, binascii
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad

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

resp = s.get(
    f"{DATA_URL}/api/stream/detail?streamId=932434&siteType=2001"
    f"&continent=AS&country=ID&digit=trd&matchId=2266973&sportType=6",
    timeout=15
)
content = resp.content
rb_session = resp.headers.get("rb-session", "")
print(f"rb-session: {rb_session}")
print(f"Size: {len(content)}")

# Parse protobuf to get f10.f2.f4
def read_varint(data, i):
    result, shift = 0, 0
    while i < len(data) and shift < 35:
        b = data[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80): return result, i
        shift += 7
    return None, i

def parse_msg(data, start=0, end=None):
    if end is None: end = len(data)
    fields, i = [], start
    while i < end:
        tag, i = read_varint(data, i)
        if tag is None or tag == 0 or i >= end: break
        fnum, wt = tag >> 3, tag & 0x07
        if wt == 0:
            val, i = read_varint(data, i)
            if val is None: break
            fields.append((fnum, 0, val))
        elif wt == 2:
            length, i = read_varint(data, i)
            if length is None or i + length > end: break
            fields.append((fnum, 2, data[i:i+length]))
            i += length
        elif wt == 1:
            if i + 8 > end: break
            fields.append((fnum, 1, data[i:i+8])); i += 8
        elif wt == 5:
            if i + 4 > end: break
            fields.append((fnum, 5, data[i:i+4])); i += 4
        else: break
    return fields

# Navigate: root → f10 → f2 → f4
top = parse_msg(content)
f10 = next((v for f, w, v in top if f == 10 and w == 2), None)
if not f10:
    print("f10 not found"); exit()

f2 = next((v for f, w, v in parse_msg(f10) if f == 2 and w == 2), None)
if not f2:
    print("f2 not found"); exit()

f4 = next((v for f, w, v in parse_msg(f2) if f == 4 and w == 2), None)
if not f4:
    print("f4 not found"); exit()

print(f"\nf4: {len(f4)} bytes")
print(f"  repr: {f4!r}")
print(f"  %16 = {len(f4) % 16}")
print(f"  All printable ASCII: {all(32 <= b < 127 for b in f4)}")

# ===== TRY ALL DECRYPTION METHODS =====
print("\n" + "="*60)

# Method 1: Direct AES-CBC decrypt
print("\n[1] Direct AES-CBC decrypt:")
try:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    raw = cipher.decrypt(f4)
    print(f"  Raw: {raw[:100]!r}")
    try:
        pt = unpad(raw, 16)
        print(f"  ✓ Unpadded: {pt!r}")
    except Exception as e:
        print(f"  ✗ Unpad failed: {e}")
        print(f"  Last byte (pad?): 0x{raw[-1]:02x}")
except Exception as e:
    print(f"  ✗ {e}")

# Method 2: Base64 decode → AES
print("\n[2] Base64 decode → AES-CBC:")
for b64_variant in [
    lambda d: base64.b64decode(d + b'=' * ((4 - len(d) % 4) % 4)),
    lambda d: base64.urlsafe_b64decode(d + b'=' * ((4 - len(d) % 4) % 4)),
]:
    try:
        decoded = b64_variant(f4)
        print(f"  Decoded: {len(decoded)} bytes, %16={len(decoded)%16}")
        if len(decoded) % 16 == 0 and len(decoded) >= 16:
            cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
            raw = cipher.decrypt(decoded)
            try:
                pt = unpad(raw, 16)
                print(f"  ✓ AES OK: {pt!r}")
            except:
                print(f"  Raw: {raw[:100]!r}")
    except Exception as e:
        print(f"  ✗ {e}")

# Method 3: ROT47 → then check if it's base64 or AES
print("\n[3] ROT47(f4) → check:")
def rot47(s):
    if isinstance(s, bytes): s = s.decode('ascii', errors='ignore')
    return ''.join(chr((ord(c)-33+47)%87+33) if 33 <= ord(c) <= 119 else c for c in s)

r47 = rot47(f4)
print(f"  ROT47 result: {r47[:80]}")
# Check if ROT47 result is valid base64
b64_chars = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
if all(c in b64_chars for c in r47):
    print(f"  ✓ ROT47 result IS valid base64!")
    try:
        decoded = base64.b64decode(r47)
        print(f"  Decoded: {len(decoded)} bytes, %16={len(decoded)%16}")
        if len(decoded) % 16 == 0:
            cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
            raw = cipher.decrypt(decoded)
            try:
                pt = unpad(raw, 16)
                print(f"  ✓ AES OK: {pt!r}")
            except:
                print(f"  Raw: {raw[:100]!r}")
    except Exception as e:
        print(f"  ✗ b64 decode: {e}")
else:
    non_b64 = [c for c in r47 if c not in b64_chars]
    print(f"  Not base64. Non-b64 chars: {set(non_b64)}")

# Method 4: XOR with key then AES
print("\n[4] XOR with AES_KEY → AES:")
try:
    xored = bytes(f4[i] ^ AES_KEY[i % len(AES_KEY)] for i in range(len(f4)))
    print(f"  Xored first 20: {xored[:20]!r}")
    if len(xored) % 16 == 0:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        raw = cipher.decrypt(xored)
        try:
            pt = unpad(raw, 16)
            print(f"  ✓ AES OK: {pt!r}")
        except:
            print(f"  Raw: {raw[:100]!r}")
except Exception as e:
    print(f"  ✗ {e}")

# Method 5: Maybe it's not AES — try as direct URL with various decodings
print("\n[5] Direct interpretations:")
print(f"  As-is: {f4.decode('ascii', errors='replace')[:100]}")
# Check if it looks like a URL with chars shifted
for shift in range(1, 5):
    shifted = bytes((b + shift) % 128 for b in f4)
    try:
        text = shifted.decode('ascii')
        if 'http' in text:
            print(f"  Shift +{shift}: {text[:100]}")
    except:
        pass

# Method 6: Hex decode
print("\n[6] Hex decode:")
try:
    decoded = binascii.unhexlify(f4)
    print(f"  Decoded: {len(decoded)} bytes, %16={len(decoded)%16}")
    if len(decoded) % 16 == 0:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        raw = cipher.decrypt(decoded)
        try:
            pt = unpad(raw, 16)
            print(f"  ✓ AES OK: {pt!r}")
        except:
            print(f"  Raw: {raw[:100]!r}")
except Exception as e:
    print(f"  ✗ Not valid hex: {e}")

# Method 7: AES-ECB (no IV)
print("\n[7] AES-256-ECB (no IV):")
try:
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    raw = cipher.decrypt(f4)
    print(f"  Raw: {raw[:100]!r}")
    try:
        pt = unpad(raw, 16)
        print(f"  ✓ Unpadded: {pt!r}")
    except:
        print(f"  Last byte: 0x{raw[-1]:02x}")
except Exception as e:
    print(f"  ✗ {e}")

# Method 8: AES with hex-decoded key/IV
print("\n[8] AES with hex-decoded key/IV:")
try:
    key_hex = bytes.fromhex("a7981cc9eb2f4d19dcfea57b101ecd89")
    iv_hex = bytes.fromhex("8017d3a8f1400d2f")
    print(f"  Key: {len(key_hex)}B, IV: {len(iv_hex)}B")
    if len(key_hex) == 16 and len(iv_hex) == 8:
        print(f"  Key=128bit, IV=8B (invalid for CBC)")
    elif len(key_hex) == 32:
        cipher = AES.new(key_hex, AES.MODE_CBC, iv_hex.ljust(16, b'\x00'))
        raw = cipher.decrypt(f4)
        try:
            pt = unpad(raw, 16)
            print(f"  ✓ AES OK: {pt!r}")
        except:
            print(f"  Raw: {raw[:100]!r}")
except Exception as e:
    print(f"  ✗ {e}")

print("\n" + "="*60)
print("DONE — share which method (if any) produced readable output")   
