#!/usr/bin/env python3
"""
Test: GET .ts → 302 → m3u8
Jalankan: python test_ts.py
"""

from curl_cffi import requests
import sys

# ── ISI MANUAL dari hasil POST ──────────────────────────
# Copy dari log debug atau debug_output/
PLDM = "fallafar.me"
TS_URL = (
    "https://s-c5.ragice.autos/pavel/"
    "kixeme20dofo1esuzoti_f06o30s0girak08owiyi/"
    "1795900750/"
    "sv2.0d166098c0be738bb8c753358a57326a65ade7e6fd6526e44d801a3e0fd92a3d/"
    "2a6ab53189a3be178690107951679603/"
    "manifest.ts?u_id=stlev-23a886239ad1279569fcf6a953a1971002ed7122d08cecd390ef8140a9ef0561"
)
CSRF_TOKEN = (
    "c3YyLmV5SmhkWFJvWDNOb1lYSmtJam9pUWlKOS5ZVE5pTWpNNU1ESXpObVZtTk"
    "RobFpESTFOamRtWlRBd1pXRmxPR05pWkRnLjE3OTU4OTM0NTEuOGRlMGQxY2Nl"
    "MTJiYTI4MDIxMzQxMTBjOGIyYmJlMTY2MGRkNTRjYjYzNDYyNDRlMzRlYTg2Mz"
    "RjOWUzYjE5Nw=="
)
# ────────────────────────────────────────────────────────

UA = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/158.0.0.0 Mobile Safari/537.36"
)

def main():
    s = requests.Session(impersonate="chrome")
    s.headers.update({
        "User-Agent": UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "*/*;q=0.8"
        ),
    })

    print(f"Domain : {PLDM}")
    print(f"URL    : {TS_URL[:80]}...")
    print(f"Token  : {CSRF_TOKEN[:40]}...")
    print()

    # ── Pre-GET untuk cookie ──
    pre = f"https://{PLDM}/"
    print(f"[1] GET {pre}")
    try:
        r0 = s.get(pre, timeout=10,
                    headers={"Referer": f"https://vipbox.ga/"})
        print(f"    → {r0.status_code}, cookies={dict(r0.cookies)}")
    except Exception as e:
        print(f"    → FAIL: {e}")

    print()

    # ── GET .ts dengan auth ──
    hdrs = {
        "X-CSRF-Auth": CSRF_TOKEN,
        "Origin": f"https://{PLDM}",
        "Referer": f"https://{PLDM}/",
    }

    # Test tanpa follow redirect dulu
    print(f"[2] GET {TS_URL[:60]}... (no redirect)")
    print(f"    Headers: {hdrs}")
    r1 = s.get(TS_URL, headers=hdrs,
               timeout=10, allow_redirects=False)
    print(f"    Status : {r1.status_code}")
    print(f"    Location: {r1.headers.get('Location', 'N/A')}")
    print(f"    Content-Type: {r1.headers.get('content-type', 'N/A')}")
    print()

    if r1.status_code in (301, 302, 307, 308):
        loc = r1.headers["Location"]
        print(f"[3] REDIRECT OK → {loc[:80]}")

        # Follow redirect
        print(f"[4] GET {loc[:60]}... (final)")
        r2 = s.get(loc, timeout=10, stream=True,
                   headers={"Origin": f"https://{PLDM}",
                            "Referer": f"https://{PLDM}/"})
        print(f"    Status : {r2.status_code}")
        print(f"    CT     : {r2.headers.get('content-type', 'N/A')}")
        print(f"    Size   : {r2.headers.get('content-length', 'streaming')}")

        # Baca beberapa baris pertama
        first_lines = []
        for line in r2.iter_lines():
            first_lines.append(line.decode("utf-8", errors="ignore"))
            if len(first_lines) >= 5:
                break
        r2.close()
        print(f"    First lines:")
        for l in first_lines:
            print(f"      {l[:100]}")

        print()
        print("✓ SUKSES — URL m3u8:")
        print(f"  {loc}")

    elif r1.status_code == 200:
        ct = r1.headers.get("content-type", "")
        print(f"[3] 200 (no redirect), CT={ct}")
        body = r1.text[:500]
        print(f"    Body: {body[:200]}")
        if "m3u8" in body:
            import re
            m = re.search(r'(https?://\S+\.m3u8\S*)', body)
            if m:
                print(f"    Found m3u8: {m.group(1)}")

    elif r1.status_code == 403:
        print("[3] 403 — Auth ditolak")
        print("    Cek: token expired? cookie hilang?")

    else:
        print(f"[3] Unexpected: {r1.status_code}")
        print(f"    Body: {r1.text[:200]}")


if __name__ == "__main__":
    main()   
