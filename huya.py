"""
debug_stream.py — Trace lengkap: dari mana URL streaming dibuat & bagaimana auth dihasilkan.
Jalankan: python debug_stream.py
"""

import json
import uuid
import hashlib
import time
import logging
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from curl_cffi import requests as cffi_requests

# ─── CONFIG (sama dengan script asli) ───────────────────
WEB_BASE = "https://www.camel1.tv"
API_BASE = "https://api.cameltv.live"
PROXY_BASE = "https://live123.bhns.workers.dev"
HARDCODE_AUTH = "aad639d3a1b66cb8c384364342600a6b"
APP_CODE = "D04B29D6B957CD44DC5F9894189380B8"
DEVICE_ID = str(uuid.uuid4())
NODE = "camel"
REGION = "XM"
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36"
REFERER = "https://www.camel1.tv/"
ORIGIN = "https://www.camel1.tv"
WIB = timezone(timedelta(hours=7))
STATUS_MAP = {1: "NS", 2: "LIVE", 3: "UPC", 4: "FT", 5: "HT"}

SESSION = cffi_requests.Session(impersonate="chrome", timeout=30)

# ─── DEBUG LOGGER ───────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
dbg = logging.getLogger("DEBUG")

# ─── HELPERS ────────────────────────────────────────────
def _hr(title):
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")

def _step(n, desc):
    print(f"\n  ┌─ STEP {n}: {desc}")
    print(f"  │")

def _val(label, value):
    print(f"  │  {label}: {value}")

def _raw_json(data, indent=4):
    """Print JSON terpotong agar tidak terlalu panjang."""
    s = json.dumps(data, indent=indent, ensure_ascii=False)
    if len(s) > 1500:
        s = s[:1500] + "\n  │  ... (truncated)"
    for line in s.split("\n"):
        print(f"  │  {line}")

def _headers(uid=None):
    h = {
        "user-agent": USER_AGENT,
        "accept": "application/json, text/plain, */*",
        "origin": ORIGIN,
        "referer": REFERER,
        "deviceid": DEVICE_ID,
        "device": "WEB",
        "appversion": "20.0.0.0",
        "node": NODE,
        "region": REGION,
        "accept-language": "en",
    }
    if uid:
        h["uid"] = uid
    return h


# ─── STEP 1: LOGIN ─────────────────────────────────────
def debug_login():
    _hr("STEP 1: ANONYMOUS LOGIN — mendapatkan uid")
    url = f"{API_BASE}/account-service/login/anonymous/web?appCode={APP_CODE}"
    h = _headers()
    h["content-type"] = "application/x-www-form-urlencoded"
    body = json.dumps({"appCode": APP_CODE})

    _step(1, f"POST {url}")
    _val("Headers:", json.dumps(h, indent=2))
    _val("Body:", body)

    r = SESSION.post(url, headers=h, data=body)
    _val("Status Code:", r.status_code)
    _val("Response Headers:", dict(r.headers))
    _val("Response Body:")
    _raw_json(r.json())

    data = r.json()
    if data.get("success") or data.get("code") == 0:
        d = data.get("detail", {})
        uid = d.get("key", "")
        val = d.get("value", "")
        _val("✅ uid (key):", uid)
        _val("   value:", val)
        return uid
    _val("❌ Login gagal", data)
    return None


# ─── STEP 2: FETCH MATCHES ─────────────────────────────
def debug_fetch_matches():
    _hr("STEP 2: FETCH MATCHES dari HTML (RSC extraction)")
    url = f"{WEB_BASE}/"
    _step(2, f"GET {url}")

    r = SESSION.get(url)
    _val("Status Code:", r.status_code)
    _val("HTML length:", len(r.text))

    # Cari segment RSC
    unescaped = r.text.replace('\\"', '"').replace('\\\\', '\\')
    start = unescaped.find('[{"competition"')
    _val("RSC segment found at index:", start)

    if start == -1:
        _val("❌ RSC segment tidak ditemukan!", "")
        return []

    # Ekstrak bracket (simplified untuk debug)
    depth, i, in_str, esc, end = 0, start, False, False, -1
    while i < len(unescaped):
        c = unescaped[i]
        if esc: esc = False; i += 1; continue
        if c == '\\' and in_str: esc = True; i += 1; continue
        if c == '"': in_str = not in_str
        elif not in_str:
            if c == '[': depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0: end = i; break
        i += 1

    if end == -1:
        _val("❌ Bracket tidak seimbang!", "")
        return []

    raw_json_str = unescaped[start:end + 1]
    _val("Raw JSON length:", len(raw_json_str))
    _val("Raw JSON (first 800 chars):", raw_json_str[:800])

    data = json.loads(raw_json_str)
    matches = []
    for cb in data:
        cn = cb.get("competition", {}).get("name", "")
        for m in cb.get("match", []):
            h = m.get("home_team", {})
            a = m.get("away_team", {})
            matches.append({
                "id": m.get("id", ""),
                "competition": cn,
                "home": h.get("name", "?"),
                "away": a.get("name", "?"),
                "status_id": m.get("status_id", 0),
                "status": STATUS_MAP.get(m.get("status_id", 0), "?"),
                "has_stream": m.get("coverage", {}).get("has_stream", 0),
            })

    _val("Total matches:", len(matches))
    for m in matches[:10]:
        print(f"  │    [{m['status']}] {m['home']} vs {m['away']} | id={m['id']} | has_stream={m['has_stream']}")
    return matches


# ─── STEP 3: GET STREAM URL ────────────────────────────
def debug_get_stream_url(match_id, uid):
    _hr("STEP 3: GET STREAM URL — dari mana URL streaming berasal")
    _step(3, f"Mencari stream untuk match_id={match_id}")

    endpoints = [
        f"{API_BASE}/camel-service/ee/sports_live/football/stream?matchId={match_id}",
        f"{API_BASE}/camel-service/ee/sports_live/loadAnchorsByMatchId?matchId={match_id}&sportType=football",
        f"{API_BASE}/camel-service/ee/sports_live/getStreamByMatchId?matchId={match_id}",
    ]

    for idx, ep in enumerate(endpoints, 1):
        print(f"\n  │  ┌─ Endpoint {idx}/{len(endpoints)}:")
        print(f"  │  │  GET {ep}")
        print(f"  │  │")

        try:
            r = SESSION.get(ep, headers=_headers(uid))
            print(f"  │  │  Status: {r.status_code}")

            if r.status_code != 200:
                print(f"  │  │  ⏭ Skip (non-200)")
                continue

            data = r.json()
            print(f"  │  │  Response JSON:")
            _raw_json(data)

            # Cek success
            success = data.get("success") or data.get("code") == 0 or data.get("status") == 200
            if not success:
                print(f"  │  │  ⏭ Skip (not success)")
                continue

            # Ekstrak stream URL
            detail = data.get("detail", data.get("data", data.get("result", {})))
            stream_url = _extract_debug(detail)

            if stream_url:
                print(f"  │  │  ✅ STREAM URL DITEMUKAN:")
                print(f"  │  │  ┌─────────────────────────────────────────────")
                print(f"  │  │  │  {stream_url}")
                print(f"  │  │  └─────────────────────────────────────────────")
                return stream_url

        except Exception as e:
            print(f"  │  │  ❌ Error: {e}")

    _val("❌ Tidak ada stream URL ditemukan untuk match_id:", match_id)
    return None


def _extract_debug(detail):
    """Ekstrak stream URL dengan logging detail."""
    if isinstance(detail, list):
        streams = detail
    elif isinstance(detail, dict):
        streams = (detail.get("streams")
                   or detail.get("streamList")
                   or detail.get("list")
                   or [])
        if not streams:
            for key in ("streamUrlM3u8", "streamUrl"):
                if detail.get(key):
                    print(f"  │  │  │  → Langsung dari detail.{key}")
                    return detail[key].replace("http://", "https://")
    else:
        return None

    for s in streams:
        if not isinstance(s, dict):
            continue
        for key in ("streamUrlM3u8", "streamUrl", "url", "playUrl", "m3u8Url", "hlsUrl"):
            val = s.get(key, "")
            if val and val.startswith("http"):
                print(f"  │  │  │  → Dari stream[{key}]")
                return val.replace("http://", "https://")
    return None


# ─── STEP 4: GET TOKEN (AUTH) ──────────────────────────
def debug_get_token(stream_url, uid):
    _hr("STEP 4: GET TOKEN — di mana AUTH (txSecret) dibuat")
    _step(4, f"Mengambil token untuk stream")

    # Ekstrak stream_name
    stream_name = stream_url.rstrip(".m3u8").split("/")[-1]
    _val("stream_url (input):", stream_url)
    _val("stream_name (ekstrak):", stream_name)
    _val("   (logika: stream_url.rstrip('.m3u8').split('/')[-1])")

    url = f"{API_BASE}/camel-service/ee/sports_live/token?streamName={stream_name}"
    print(f"\n  │  GET {url}")
    print(f"  │")

    try:
        r = SESSION.get(url, headers=_headers(uid))
        _val("Status Code:", r.status_code)
        _val("Response JSON:")
        _raw_json(r.json())

        data = r.json()
        if data.get("status") == 200 or data.get("code") == 0:
            d = data.get("data", data.get("detail", {}))
            tx_time = d.get("txTime", "")
            tx_secret = d.get("txSecret", "")
            _val("✅ txTime:", tx_time)
            _val("   txSecret (RAW dari API):", tx_secret)
            return tx_time, tx_secret
    except Exception as e:
        _val("❌ Error:", str(e))

    _val("⚠ Token tidak ditemukan, akan fallback ke HARDCODE_AUTH", "")
    return None, None


# ─── STEP 5: BUILD FINAL URL ───────────────────────────
def debug_build_final_url(stream_url, tx_time, tx_secret):
    _hr("STEP 5: BUILD FINAL URL — bagaimana auth di-encode ke URL")
    _step(5, "Membangun final URL")

    _val("stream_url:", stream_url)
    _val("tx_time:", tx_time)
    _val("tx_secret (raw):", tx_secret)

    if tx_time and tx_secret:
        # INI DI MANA AUTH DIBUAT
        tx_secret_url = hashlib.md5(tx_secret.encode()).hexdigest()
        print(f"\n  │  ┌─ 🔐 AUTH CREATION:")
        print(f"  │  │  tx_secret (raw)  = {tx_secret}")
        print(f"  │  │  md5(tx_secret)   = {tx_secret_url}")
        print(f"  │  │  txTime           = {tx_time}")
        print(f"  │  │  lat              = 9000")
        print(f"  │  └─────────────────────────────────────────────")

        final_url = f"{stream_url}?txSecret={tx_secret_url}&txTime={tx_time}&lat=9000"
    else:
        print(f"\n  │  ┌─ 🔐 AUTH FALLBACK (HARDCODE):")
        print(f"  │  │  auth = {HARDCODE_AUTH}")
        print(f"  │  └─────────────────────────────────────────────")
        final_url = f"{stream_url}?auth={HARDCODE_AUTH}"

    _val("FINAL URL:", final_url)
    return final_url


# ─── STEP 6: PROXY URL ─────────────────────────────────
def debug_to_proxy_url(final_url):
    _hr("STEP 6: PROXY URL — wrapping melalui proxy + auth tambahan")
    _step(6, "Mengubah final URL menjadi proxy URL")

    _val("final_url (input):", final_url)

    parsed = urlparse(final_url)
    _val("parsed.path:", parsed.path)
    _val("parsed.netloc:", parsed.netloc)
    _val("parsed.query (existing):", parsed.query)

    query = parsed.query
    if query:
        new_query = f"{query}&auth={HARDCODE_AUTH}&d={parsed.netloc}"
    else:
        new_query = f"auth={HARDCODE_AUTH}&d={parsed.netloc}"

    proxy_url = f"{PROXY_BASE}/{parsed.path.lstrip('/')}?{new_query}"

    print(f"\n  │  ┌─ 🔐 PROXY AUTH ADDED:")
    print(f"  │  │  PROXY_BASE = {PROXY_BASE}")
    print(f"  │  │  + auth={HARDCODE_AUTH}")
    print(f"  │  │  + d={parsed.netloc}")
    print(f"  │  └─────────────────────────────────────────────")

    _val("PROXY URL (output):", proxy_url)
    return proxy_url


# ─── STEP 7: VALIDATE ──────────────────────────────────
def debug_validate(proxy_url):
    _hr("STEP 7: VALIDATE — cek apakah URL bisa diakses")
    _step(7, f"GET {proxy_url[:80]}...")

    try:
        r = SESSION.get(proxy_url, headers={
            "user-agent": USER_AGENT,
            "referer": REFERER,
        }, timeout=10)
        _val("Status Code:", r.status_code)
        _val("Content-Type:", r.headers.get("content-type", "?"))
        _val("Content-Length:", len(r.content))
        _val("First 500 bytes:")
        content = r.content[:500].decode("utf-8", errors="replace")
        for line in content.split("\n")[:15]:
            print(f"  │    {line}")
    except Exception as e:
        _val("❌ Validation error:", str(e))


# ─── MAIN ──────────────────────────────────────────────
def main():
    print("\n" + "█" * 70)
    print("  🔬 DEBUG: Trace URL Streaming & Auth Creation")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S WIB')}")
    print("█" * 70)

    # 1. Login
    uid = debug_login()
    if not uid:
        print("\n  💀 Tidak bisa lanjut tanpa uid.")
        return

    # 2. Fetch matches
    matches = debug_fetch_matches()
    if not matches:
        print("\n  💀 Tidak ada match.")
        return

    # Pilih match pertama yang has_stream=1 atau LIVE
    target = None
    for m in matches:
        if m["has_stream"] in (1, True) or m["status_id"] in (2, 3):
            target = m
            break
    if not target:
        target = matches[0]
        print(f"\n  ⚠ Tidak ada match dengan stream, pakai: {target['home']} vs {target['away']}")

    print(f"\n  🎯 Target: {target['home']} vs {target['away']} (id={target['id']})")

    # 3. Get stream URL
    stream_url = debug_get_stream_url(target["id"], uid)
    if not stream_url:
        print("\n  💀 Tidak ada stream URL. Hentikan.")
        return

    # 4. Get token
    tx_time, tx_secret = debug_get_token(stream_url, uid)

    # 5. Build final URL
    final_url = debug_build_final_url(stream_url, tx_time, tx_secret)

    # 6. Proxy URL
    proxy_url = debug_to_proxy_url(final_url)

    # 7. Validate
    debug_validate(proxy_url)

    # ─── RINGKASAN ──────────────────────────────────────
    _hr("📋 RINGKASAN CHAIN URL")
    print(f"""
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. API Stream Endpoint                                         │
  │    → stream_url = {stream_url[:60]}...│
  ├─────────────────────────────────────────────────────────────────┤
  │ 2. API Token Endpoint                                          │
  │    → txTime   = {tx_time or 'N/A'}│
  │    → txSecret = {tx_secret or 'N/A'}│
  ├─────────────────────────────────────────────────────────────────┤
  │ 3. AUTH CONSTRUCTION                                           │
  │    → md5(txSecret) = {hashlib.md5(tx_secret.encode()).hexdigest() if tx_secret else 'N/A'}│
  │    → final_url   = stream_url?txSecret=...&txTime=...&lat=9000│
  ├─────────────────────────────────────────────────────────────────┤
  │ 4. PROXY WRAPPING                                              │
  │    → proxy_url   = PROXY_BASE/path?query&auth={HARDCODE_AUTH[:8]}...&d=netloc│
  ├─────────────────────────────────────────────────────────────────┤
  │ 5. FINAL (di M3U)                                              │
  │    → {proxy_url[:60]}...│
  └─────────────────────────────────────────────────────────────────┘
""")


if __name__ == "__main__":
    main()   
