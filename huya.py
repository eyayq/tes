
import json
import re
import uuid
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from curl_cffi import requests as cffi_requests

WEB_BASE = "https://exsample.d-n-s.name/px.php?csurl=https://www.camel1.tv"
API_BASE = "https://api.cameltv.live"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 500

APP_CODE = "D04B29D6B957CD44DC5F9894189380B8"
DEVICE_ID = str(uuid.uuid4())
NODE = "camel2"
REGION = "XM"

STATUS_MAP = {1: "NS", 2: "LIVE", 3: "UPC", 4: "FT", 5: "HT"}

SESSION = cffi_requests.Session(impersonate="chrome", timeout=20)

# ─── STEP 1: ANONYMOUS LOGIN ───────────────────────────
def anonymous_login():
    url = f"{API_BASE}/account-service/login/anonymous/web?appCode={APP_CODE}"
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.camel1.tv",
        "referer": "https://www.camel1.tv/",
        "deviceid": DEVICE_ID,
        "device": "WEB",
        "appversion": "20.0.0.0",
        "node": NODE,
        "region": REGION,
        "accept-language": "en",
    }
    body = json.dumps({"appCode": APP_CODE})
    try:
        r = SESSION.post(url, headers=headers, data=body)
        data = r.json()
        if data.get("success") or data.get("code") == 0:
            detail = data.get("detail", {})
            uid = detail.get("key", "")
            token = detail.get("value", "")
            print(f"  ✅ uid={uid}")
            return uid, token
        else:
            print(f"  ⚠ {data}")
            return None, None
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None, None

# ─── STEP 2: EKSTRAK MATCH DATA ─────────────────────────
def extract_matches_rsc(html):
    unescaped = html.replace('\\"', '"').replace('\\\\', '\\')
    start = unescaped.find('[{"competition"')
    if start == -1:
        return []
    depth, i, in_str, esc, end = 0, start, False, False, -1
    while i < len(unescaped):
        c = unescaped[i]
        if esc:
            esc = False; i += 1; continue
        if c == '\\' and in_str:
            esc = True; i += 1; continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    end = i; break
        i += 1
    if end == -1:
        return []
    try:
        data = json.loads(unescaped[start:end + 1])
    except:
        return []
    matches = []
    for cb in data:
        cn = cb.get("competition", {}).get("name", "")
        for m in cb.get("match", []):
            h = m.get("home_team", {})
            a = m.get("away_team", {})
            hs = m.get("home_scores", [0] * 7)
            as_ = m.get("away_scores", [0] * 7)
            matches.append({
                "id": m.get("id", ""),
                "competition": cn,
                "home": h.get("name", "?"),
                "away": a.get("name", "?"),
                "home_score": hs[0] if hs else 0,
                "away_score": as_[0] if as_ else 0,
                "status": STATUS_MAP.get(m.get("status_id", 0), "?"),
                "status_id": m.get("status_id", 0),
                "has_stream": m.get("coverage", {}).get("has_stream", 0),
            })
    return matches

# ─── STEP 3: LOAD ANCHORS ───────────────────────────────
def load_anchors(match_id, uid):
    url = f"{API_BASE}/camel-service/ee/sports_live/loadAnchorsByMatchId?matchId={match_id}&sportType=football"
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://www.camel1.tv",
        "referer": "https://www.camel1.tv/",
        "deviceid": DEVICE_ID,
        "device": "WEB",
        "appversion": "20.0.0.0",
        "node": NODE,
        "region": REGION,
        "accept-language": "en",
        "uid": uid,
    }
    try:
        r = SESSION.get(url, headers=headers)
        data = r.json()
        if data.get("success") or data.get("code") == 0:
            streams = data.get("detail", {}).get("streams", [])
            if streams:
                s = streams[0]
                stream_url = s.get("streamUrl", "")
                stream_name = stream_url.rstrip(".m3u8").split("/")[-1] if stream_url else ""
                return {"stream_url": stream_url, "stream_name": stream_name}
        return None
    except Exception as e:
        return None

# ─── STEP 4: GET STREAM TOKEN ───────────────────────────
def get_stream_token(stream_name, uid):
    url = f"{API_BASE}/camel-service/ee/sports_live/token?streamName={stream_name}"
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "accept": "application/json, text/plain, */*",
        "content-type": "multipart/form-data",
        "origin": "https://www.camel1.tv",
        "referer": "https://www.camel1.tv/",
        "deviceid": DEVICE_ID,
        "device": "WEB",
        "appversion": "20.0.0.0",
        "node": NODE,
        "region": REGION,
        "accept-language": "en",
        "uid": uid,
    }
    try:
        r = SESSION.get(url, headers=headers)
        data = r.json()
        if data.get("status") == 200 or data.get("code") == 0:
            d = data.get("data", {})
            return {
                "txTime": d.get("txTime", ""),
                "txSecret": d.get("txSecret", ""),
                "txDevice": d.get("txDevice", ""),
            }
        return None
    except:
        return None

# ─── STEP 5: BUILD FINAL URL ────────────────────────────
def build_final_url(stream_url, token_data):
    if not token_data:
        return stream_url
    tx_time = token_data.get("txTime", "")
    tx_secret = token_data.get("txSecret", "")
    # MD5 hex dari txSecret (32 char)
    tx_secret_url = hashlib.md5(tx_secret.encode()).hexdigest() if tx_secret else ""
    if tx_time and tx_secret_url:
        return f"{stream_url}?txSecret={tx_secret_url}&txTime={tx_time}&lat=9000"
    return stream_url

# ─── STEP 6: FULL PIPELINE PER MATCH ────────────────────
def get_stream_for_match(match, uid):
    match_id = match["id"]
    anchors = load_anchors(match_id, uid)
    if not anchors or not anchors.get("stream_url"):
        return match, None
    stream_url = anchors["stream_url"]
    stream_name = anchors.get("stream_name", "")
    token_data = get_stream_token(stream_name, uid)
    final_url = build_final_url(stream_url, token_data)
    return match, final_url

# ─── GENERATE M3U ───────────────────────────────────────
def generate_m3u(streams):
    lines = ["#EXTM3U"]
    for s in streams:
        title = (f"{s['home']} vs {s['away']} - {s['competition']} "
                 f"[{s['status']}] ({s['home_score']}-{s['away_score']})")
        lines.append(f'#EXTINF:0,tvg-name="{title}",{title}')
        lines.append(s['stream_url'])
    return "\n".join(lines)

# ─── MAIN ───────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  CAMEL LIVE - M3U STREAM SCRAPER (API)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Device: {DEVICE_ID}")
    print("=" * 70)

    # 1. Login
    print("\n🔑 Anonymous login...")
    uid, token = anonymous_login()
    if not uid:
        print("  ❌ Login gagal.")
        return

    # 2. Homepage
    print("\n📡 Fetching homepage...")
    r = SESSION.get(f"{WEB_BASE}/")
    matches = extract_matches_rsc(r.text)
    print(f"  ✅ {len(matches)} matches")

    streamable = [m for m in matches if m.get("has_stream") and m["status_id"] in (2, 4)]
    print(f"  📺 Streamable (LIVE/FT): {len(streamable)}")

    if not streamable:
        print("  ⚠ Tidak ada match dengan stream.")
        return

    # 3. ThreadPool
    print(f"\n🔧 Fetching stream URLs ({MAX_WORKERS} workers)...")
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(get_stream_for_match, m, uid) for m in streamable]
        for i, fut in enumerate(futures):
            match, stream_url = fut.result()
            label = f"{match['home']} vs {match['away']} [{match['status']}]"
            if stream_url:
                print(f"  [{i + 1}/{len(streamable)}] ✅ {label}")
                print(f"       → {stream_url[:130]}")
                results.append({**match, "stream_url": stream_url})
            else:
                print(f"  [{i + 1}/{len(streamable)}] ❌ {label}")

    # 4. Output
    print(f"\n{'═' * 70}")
    if results:
        m3u = generate_m3u(results)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"  ✅ {OUTPUT_M3U} → {len(results)} streams")
        print(f"{'═' * 70}")
        print(f"\n{m3u[:600]}")
    else:
        print(f"  ❌ 0 streams.")
        print(f"{'═' * 70}")

if __name__ == "__main__":
    main()   

