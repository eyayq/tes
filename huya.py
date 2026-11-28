#!/usr/bin/env python3
"""
Test: GET .ts → 302 → m3u8
python test_ts.py
"""

from curl_cffi import requests
from urllib.parse import urlparse

# ── ISI DARI HASIL POST ────────────────────────────────
PLDM = "dervlin.me"
TS_URL = (
    "https://s-a3.mhys.ink/pavel/"
    "1az0rawos0robu4ozigi_1i5uvovojopaku606um0/"
    "1795904976/"
    "sv2.f4ff37086f6b4e41e1ccef0476f01f557e4b9ef4ea2e67e59efcedcc5a0cbec6/"
    "0ccae93ecc4e6502db140bf7fc7ff377/"
    "manifest.ts?u_id=stlev-91d9e2834fd49835932cdba7088e56bf313bc4ab3976a2935def0bb072048eab"
)
# ────────────────────────────────────────────────────────

UA = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/158.0.0.0 Mobile Safari/537.36"
)

def resolve_redirect(url, pldm):
    """GET .ts → 302 → absolute m3u8 URL"""
    s = requests.Session(impersonate="chrome")
    s.headers.update({"User-Agent": UA})

    hdrs = {
        "Origin": f"https://{pldm}",
        "Referer": f"https://{pldm}/",
        "Accept": "*/*",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }

    print(f"GET {url[:70]}...")
    print(f"  Origin:  https://{pldm}")
    print(f"  Referer: https://{pldm}/")
    print()

    r = s.get(url, headers=hdrs, timeout=15,
              allow_redirects=False)

    print(f"Status: {r.status_code}")

    if r.status_code in (301, 302, 307, 308):
        loc = r.headers["Location"]
        print(f"Location: {loc[:100]}")

        # Resolve relative → absolute
        if loc.startswith("/"):
            parsed = urlparse(url)
            final = f"{parsed.scheme}://{parsed.netloc}{loc}"
        else:
            final = loc

        print(f"\n✓ M3U8: {final}")
        return final

    elif r.status_code == 200:
        ct = r.headers.get("content-type", "")
        print(f"200, CT={ct}")
        print(f"Body: {r.text[:200]}")
        return url

    else:
        print(f"FAIL: {r.status_code}")
        print(f"Body: {r.text[:200]}")
        return None


if __name__ == "__main__":
    result = resolve_redirect(TS_URL, PLDM)
    if result:
        print(f"\n\n=== FINAL URL untuk M3U ===")
        print(result)   
