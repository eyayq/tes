import requests
import re
import time

DATA_URL = "https://apis-data10.tcpwe138stya.ru"
LANGUAGE = 34

HEADERS = {
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "origin": "https://jack01eo.m8ow6pride8nly2qearly.cfd",
    "referer": "https://jack01eo.m8ow6pride8nly2qearly.cfd/",
}

s = requests.Session()
s.headers.update(HEADERS)


def read_varint(data, i):
    result = 0
    shift = 0
    while i < len(data) and shift < 35:
        b = data[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
    return None, i


def parse_msg(data, start=0, end=None):
    if end is None:
        end = len(data)
    fields = []
    i = start
    while i < end:
        tag, i = read_varint(data, i)
        if tag is None or tag == 0 or i >= end:
            break
        fnum = tag >> 3
        wt = tag & 0x07
        if wt == 0:
            val, i = read_varint(data, i)
            if val is None: break
            fields.append((fnum, 0, val))
        elif wt == 1:
            if i + 8 > end: break
            fields.append((fnum, 1, data[i:i+8]))
            i += 8
        elif wt == 2:
            length, i = read_varint(data, i)
            if length is None or length < 0 or i + length > end: break
            fields.append((fnum, 2, data[i:i+length]))
            i += length
        elif wt == 5:
            if i + 4 > end: break
            fields.append((fnum, 5, data[i:i+4]))
            i += 4
        else:
            break
    return fields


def dump(data, indent=0, max_depth=6):
    if indent > max_depth:
        return
    p = "  " * indent
    fields = parse_msg(data)
    if not fields:
        print(f"{p}(empty or not protobuf, {len(data)}B): {data[:60]!r}")
        return
    for fnum, wt, val in fields:
        wname = {0:"varint",1:"64bit",2:"bytes",5:"32bit"}.get(wt, f"wt{wt}")
        if wt == 0:
            print(f"{p}f{fnum} ({wname}): {val}")
        elif wt == 2:
            try:
                t = val.decode('utf-8')
                if all(32 <= ord(c) < 127 or c in '\n\t' for c in t) and len(val) < 300:
                    print(f"{p}f{fnum} (str): {t!r}")
                    continue
            except:
                pass
            print(f"{p}f{fnum} (bytes, {len(val)}B):")
            sub = parse_msg(val)
            if sub:
                dump(val, indent+1, max_depth)
            else:
                print(f"{p}  raw: {val[:80]!r}")
        else:
            print(f"{p}f{fnum} ({wname}): {val!r}")


# Step 1: Signing key
resp = s.get(f"{DATA_URL}/api/common/bs?code=100&sportType=0&stream=true", timeout=15)
m = re.search(rb'[a-f0-9]{32}', resp.content)
md5 = m.group().decode()
print(f"Key: {md5}\n")

# Step 2: Get matches (protobuf)
time.sleep(2)
resp = s.get(f"{DATA_URL}/sfver{md5}/api/match/live?sportType=0&language={LANGUAGE}&stream=true", timeout=15)
content = resp.content

# Extract first match with "vs"
# Find all sub-messages that contain "vs"
top = parse_msg(content)
# Look for the big field (matches container)
container = None
for fnum, wt, val in top:
    if wt == 2 and len(val) > 1000:
        container = val
        break

if not container:
    print("No container found in live matches")
    exit()

inner = parse_msg(container)
# Find first match that has a "vs" name
first_match_data = None
first_match_id = None
for fnum, wt, val in inner:
    if wt == 2 and len(val) > 20:
        if b" vs " in val or b" v " in val:
            first_match_data = val
            # Extract match id (field 1, varint, > 1000)
            mf = parse_msg(val)
            for n, w, v in mf:
                if w == 0 and n == 1 and v > 1000:
                    first_match_id = v
                    break
            break

if not first_match_data:
    print("No match with 'vs' found")
    exit()

name_m = re.search(rb'[\x20-\x7e]{2,}\s+vs\s+[\x20-\x7e]{2,}', first_match_data)
name = name_m.group().decode() if name_m else "Unknown"
sport_type = 0
mf = parse_msg(first_match_data)
for n, w, v in mf:
    if w == 0 and n == 2 and 0 < v < 100:
        sport_type = v
        break

print(f"Match: {name}")
print(f"matchId: {first_match_id}, sportType: {sport_type}\n")

# Step 3: Match detail — DUMP FULL PROTOBUF
print("=" * 60)
print("MATCH DETAIL PROTOBUF DUMP")
print("=" * 60)
time.sleep(2)
resp = s.get(
    f"{DATA_URL}/sfver{md5}/api/match/detail?matchId={first_match_id}"
    f"&sportType={sport_type}&language={LANGUAGE}&stream=true",
    timeout=15
)
detail = resp.content
print(f"Size: {len(detail)} bytes")
print(f"Content-Type: {resp.headers.get('content-type')}")
print()
dump(detail)

# Also: look for ALL varint values that could be stream IDs
# Stream IDs are typically 6-8 digits
print("\n" + "=" * 60)
print("ALL VARINT VALUES (potential stream IDs)")
print("=" * 60)

def find_all_varints(data, path="root", depth=0):
    if depth > 8:
        return
    fields = parse_msg(data)
    for fnum, wt, val in fields:
        if wt == 0:
            if 1000 < val < 999999999:
                print(f"  {path}.f{fnum} = {val}")
        elif wt == 2:
            find_all_varints(val, f"{path}.f{fnum}", depth+1)

find_all_varints(detail)

# Look for any string containing "stream"
print("\n" + "=" * 60)
print("STRINGS CONTAINING 'stream' (case-insensitive)")
print("=" * 60)
strings = re.findall(rb'[\x20-\x7e]{5,}', detail)
for s_str in strings:
    if b'stream' in s_str.lower() or b'Stream' in s_str:
        print(f"  {s_str.decode()[:150]}")

print("\n=== DONE ===")   
