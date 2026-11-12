#!/usr/bin/env python3

import argparse
import base64
import datetime
import json
import re
import sys
import urllib.request

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
    "sec-ch-ua": '"Microsoft Edge";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def load_cookies(path):
    """Load cookies from a file, auto-detecting JSON, Netscape, or header string format."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read().strip()

    # Try JSON first
    if text.startswith('['):
        try:
            cookies = json.loads(text)
            parts = []
            for c in cookies:
                parts.append("%s=%s" % (c['name'], c['value']))
            return '; '.join(parts)
        except (json.JSONDecodeError, KeyError):
            pass

    # Try Netscape format
    lines = text.splitlines()
    if lines and lines[0].startswith('# Netscape'):
        parts = []
        for line in lines:
            if line.startswith('#') or not line.strip():
                continue
            fields = line.split('\t')
            if len(fields) >= 7:
                parts.append("%s=%s" % (fields[5], fields[6]))
        return '; '.join(parts)

    # Assume raw header string
    return text


def fetch(url, cookies=None, extra_headers=None):
    """Fetch a URL and return the response body as a string."""
    headers = dict(DEFAULT_HEADERS)
    if cookies:
        headers["Cookie"] = cookies
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def extract_next_data(html):
    """Extract and parse the __NEXT_DATA__ JSON from the HTML."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
        html, re.DOTALL
    )
    if not match:
        raise ValueError("No __NEXT_DATA__ script tag found in HTML")
    return json.loads(match.group(1))


def find_player_js_url(html):
    """Find the live-tv player JS chunk URL from the HTML."""
    match = re.search(
        r'<script\s+src="(/_next/static/chunks/pages/play/live-tv-[^"]+)"',
        html
    )
    if not match:
        raise ValueError("No live-tv player JS chunk found in HTML")
    return "https://www.cubmu.com" + match.group(1)


def extract_drm_key(js_text):
    """Extract drmDecryptKey value from the player JS."""
    match = re.search(r'drmDecryptKey\s*:\s*"([^"]+)"', js_text)
    if not match:
        raise ValueError("drmDecryptKey not found in player JS")
    return match.group(1)


def safe_b64decode(s):
    """Base64 decode with URL-safe variant support."""
    padded = s.replace('-', '+').replace('_', '/')
    missing = len(padded) % 4
    if missing:
        padded += '=' * (4 - missing)
    return base64.b64decode(padded)


def main():
    parser = argparse.ArgumentParser(
        description="Decrypt CubMu ClearKey DRM from live URL."
    )
    parser.add_argument("url", help="CubMu live-tv page URL")
    parser.add_argument("--cookie", "-c", metavar="FILE",
                        help="Cookie file (JSON, Netscape, or header string)")
    args = parser.parse_args()

    cookies = load_cookies(args.cookie) if args.cookie else None

    # --- Fetch the live-tv HTML page ---
    print("Fetching page: %s" % args.url)
    html = fetch(args.url, cookies=cookies)
    page_data = extract_next_data(html)
    page = page_data['props']['pageProps']

    # --- Find and fetch the player JS chunk ---
    js_url = find_player_js_url(html)
    print("Fetching player JS: %s" % js_url)
    js_text = fetch(js_url, extra_headers={"Referer": args.url})

    # --- Extract drmDecryptKey from player JS ---
    drm_key = extract_drm_key(js_text)

    # --- Extract manifest + clearKey from page data ---
    manifest_url = page.get('manifest', '')
    ck = page.get('clearKey', {})
    ck_data_b64 = ck.get('data', '')
    ck_iv_b64 = ck.get('iv', '')

    if not ck_data_b64 or not ck_iv_b64:
        print("Error: No clearKey data or IV found in page.")
        sys.exit(1)

    dk_raw = drm_key.encode('utf-8')
    ck_data = safe_b64decode(ck_data_b64)
    iv = safe_b64decode(ck_iv_b64)

    # --- Output ---
    print()
    print("[DRM Decrypt Key]")
    print("  String:  %s" % drm_key)
    print("  Hex:     %s" % dk_raw.hex())
    print("  Length:  %d bytes (%d bits)" % (len(dk_raw), len(dk_raw) * 8))
    print()

    print("=" * 70)
    print("DECRYPTING clearKey.data")
    print("=" * 70)
    print()
    print("Method: AES-256-CBC")
    print("Key:    drmDecryptKey as raw string bytes (%d bytes)" % len(dk_raw))
    print("IV:     clearKey.iv (%s)" % iv.hex())
    print()

    # --- Decrypt ---
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        print("Error: 'cryptography' library not available. Install with:")
        print("  pip install cryptography")
        sys.exit(1)

    try:
        cipher = Cipher(algorithms.AES(dk_raw), modes.CBC(iv))
        dec = cipher.decryptor()
        pt = dec.update(ck_data) + dec.finalize()

        # Remove PKCS7 padding
        pad_len = pt[-1]
        if 1 <= pad_len <= 16 and all(b == pad_len for b in pt[-pad_len:]):
            pt = pt[:-pad_len]

        print("Decrypted ClearKey License (%d bytes):" % len(pt))
        print()

        license_json = json.loads(pt.decode('utf-8'))
        print(json.dumps(license_json, indent=2))
        print()

        for i, k_entry in enumerate(license_json.get('keys', [])):
            print("--- Content Key #%d ---" % (i + 1))
            print("  kid (Key ID): %s" % k_entry.get('kid', 'N/A'))
            print("  k   (Key):    %s" % k_entry.get('k', 'N/A'))
            print("  kty (Type):   %s" % k_entry.get('kty', 'N/A'))
            if 'k' in k_entry:
                raw_key = safe_b64decode(k_entry['k'])
                print("  k decoded:    %s (%d bytes)" % (raw_key.hex(), len(raw_key)))
            if 'kid' in k_entry:
                raw_kid = safe_b64decode(k_entry['kid'])
                print("  kid decoded:  %s (%d bytes)" % (raw_kid.hex(), len(raw_kid)))
            print()

        if 'exp' in license_json:
            exp_ts = license_json['exp']
            try:
                exp_dt = datetime.datetime.fromtimestamp(exp_ts)
                print("Expires: %s (timestamp: %d)" % (exp_dt.isoformat(), exp_ts))
            except Exception:
                print("Expires: timestamp %d" % exp_ts)
        if 'type' in license_json:
            print("Type: %s" % license_json['type'])

    except Exception as e:
        print("AES-256-CBC decryption failed: %s" % e)
        sys.exit(1)

    print()
    print("Done.")
    print()
    print("Manifest: %s" % manifest_url)
    print()

    # --- kid:key pair ---
    raw_key = safe_b64decode(license_json['keys'][0]['k'])
    raw_kid = safe_b64decode(license_json['keys'][0]['kid'])
    print("%s:%s" % (raw_kid.hex(), raw_key.hex()))


if __name__ == '__main__':
    main()
