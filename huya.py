

import json
import uuid
import hashlib
import time
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from curl_cffi import requests as cffi_requests

# ─── CONFIG ─────────────────────────────────────────────
WEB_BASE = "https://exsample.d-n-s.name/px.php?csurl=https://www.camel1.tv"
API_BASE = "https://exsample.d-n-s.name/px.php?csurl=https://api.cameltv.live"
OUTPUT_M3U = "playlist.m3u"
MAX_WORKERS = 200          # ← DITURUNKAN dari 500 (anti rate-limit)
RETRIES = 3               # ← JENIS BARU: retry per match
RETRY_DELAY = 2           # ← JENIS BARU: jeda antar retry (detik)

PROXY_BASE = "https://live123.bhns.workers.dev"
HARDCODE_AUTH = "aad639d3a1b66cb8c384364342600a6b"

APP_CODE = "D04B29D6B957CD44DC5F9894189380B8"
DEVICE_ID = str(uuid.uuid4())
NODE = "camel"
REGION = "XM"

GROUP_TITLE = "live camelia vip [tes]"
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36"
REFERER = "https://www.camel1.tv/"
ORIGIN = "https://www.camel1.tv"

WIB = timezone(timedelta(hours=7))
STATUS_MAP = {1: "NS", 2: "LIVE", 3: "UPC", 4: "FT", 5: "HT"}

SESSION = cffi_requests.Session(impersonate="chrome", timeout=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("camel")


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


def to_proxy_url(cdn_url):
    parsed = urlparse(cdn_url)
    query = parsed.query
    if query:
        query += f"&auth={HARDCODE_AUTH}&d={parsed.netloc}"
    else:
        query = f"auth={HARDCODE_AUTH}&d={parsed.netloc}"
    return f"{PROXY_BASE}/{parsed.path.lstrip('/')}?{query}"


# ─── API ────────────────────────────────────────────────

def anonymous_login():
    url = f"{API_BASE}/account-service/login/anonymous/web?appCode={APP_CODE}"
    h = _headers()
    h["content-type"] = "application/x-www-form-urlencoded"
    r = SESSION.post(url, headers=h, data=json.dumps({"appCode": APP_CODE}))
    data = r.json()
    if data.get("success") or data.get("code") == 0:
        d = data.get("detail", {})
        return d.get("key", ""), d.get("value", "")
    return None, None


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

            match_time = None
            raw_time = m.get("start_time") or m.get("match_time") or m.get("kickoff_time") or ""
            if raw_time:
                try:
                    if isinstance(raw_time, (int, float)):
                        match_time = datetime.fromtimestamp(raw_time, tz=WIB)
                    else:
                        match_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00")).astimezone(WIB)
                except:
                    pass

            matches.append({
                "id": m.get("id", ""),
                "competition": cn,
                "home": h.get("name", "?"),
                "away": a.get("name", "?"),
                "home_logo": h.get("logo") or h.get("logo_url") or h.get("crest") or "",
                "away_logo": a.get("logo") or a.get("logo_url") or a.get("crest") or "",
                "home_score": hs[0] if hs else 0,
                "away_score": as_[0] if as_ else 0,
                "status": STATUS_MAP.get(m.get("status_id", 0), "?"),
                "status_id": m.get("status_id", 0),
                "has_stream": m.get("coverage", {}).get("has_stream", 0),
                "match_time": match_time,
                "match_time_raw": raw_time,
            })
    return matches


# ─── PERBAIKAN UTAMA: get_stream_url lebih robust ───────

def _extract_stream_from_response(data):
    """Ekstrak stream URL dari berbagai kemungkinan struktur response API."""
    # Cek beberapa kemungkinan lokasi data
    detail = data.get("detail", data.get("data", data.get("result", {})))

    # Jika detail sendiri adalah list
    if isinstance(detail, list):
        streams = detail
    elif isinstance(detail, dict):
        # Coba berbagai key
        streams = (detail.get("streams")
                   or detail.get("streamList")
                   or detail.get("list")
                   or [])
        # Kalau detail punya streamUrl langsung
        if not streams:
            if detail.get("streamUrlM3u8"):
                return detail["streamUrlM3u8"].replace("http://", "https://")
            if detail.get("streamUrl"):
                return detail["streamUrl"].replace("http://", "https://")
    else:
        return None

    if not streams:
        return None

    # Cari URL dari setiap item
    for s in streams:
        if not isinstance(s, dict):
            continue
        # Prioritas: streamUrlM3u8 > streamUrl > url > playUrl
        for key in ("streamUrlM3u8", "streamUrl", "url", "playUrl", "m3u8Url", "hlsUrl"):
            val = s.get(key, "")
            if val and val.startswith("http"):
                return val.replace("http://", "https://")
    return None


def get_stream_url(match_id, uid):
    """Coba beberapa endpoint dengan retry."""
    endpoints = [
        f"{API_BASE}/camel-service/ee/sports_live/football/stream?matchId={match_id}",
        f"{API_BASE}/camel-service/ee/sports_live/loadAnchorsByMatchId?matchId={match_id}&sportType=football",
        f"{API_BASE}/camel-service/ee/sports_live/getStreamByMatchId?matchId={match_id}",
    ]

    for attempt in range(RETRIES):
        for ep in endpoints:
            try:
                r = SESSION.get(ep, headers=_headers(uid))
                if r.status_code != 200:
                    continue
                data = r.json()
                if not (data.get("success") or data.get("code") == 0 or data.get("status") == 200):
                    continue

                stream_url = _extract_stream_from_response(data)
                if stream_url:
                    return stream_url

            except Exception as e:
                log.debug(f"  Attempt {attempt+1} {ep}: {e}")
                continue

        # Jeda sebelum retry
        if attempt < RETRIES - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None


def get_token(stream_url, uid):
    stream_name = stream_url.rstrip(".m3u8").split("/")[-1]
    try:
        url = f"{API_BASE}/camel-service/ee/sports_live/token?streamName={stream_name}"
        r = SESSION.get(url, headers=_headers(uid))
        data = r.json()
        if data.get("status") == 200 or data.get("code") == 0:
            d = data.get("data", data.get("detail", {}))
            tx_time = d.get("txTime", "")
            tx_secret = d.get("txSecret", "")
            if tx_time and tx_secret:
                return tx_time, tx_secret
    except:
        pass
    return None, None


def build_final_url(stream_url, tx_time, tx_secret):
    if tx_time and tx_secret:
        tx_secret_url = hashlib.md5(tx_secret.encode()).hexdigest()
        return f"{stream_url}?txSecret={tx_secret_url}&txTime={tx_time}&lat=9000"
    return f"{stream_url}?auth={HARDCODE_AUTH}"


def get_stream_for_match(match, uid):
    match_id = match["id"]
    label = f"{match['home']} vs {match['away']} [{match['status']}]"

    stream_url = get_stream_url(match_id, uid)
    if not stream_url:
        log.info(f"  ❌ No stream: {label} (id={match_id}, has_stream={match['has_stream']})")
        return match, None

    tx_time, tx_secret = get_token(stream_url, uid)
    final_url = build_final_url(stream_url, tx_time, tx_secret)
    proxy_url = to_proxy_url(final_url)

    log.info(f"  ✅ {label}")
    return match, proxy_url


# ─── M3U ────────────────────────────────────────────────

def format_match_time(match):
    mt = match.get("match_time")
    if mt:
        return f"{mt.strftime('%H:%M')} WIB {mt.strftime('%d-%m-%Y')}"
    raw = match.get("match_time_raw", "")
    return str(raw) if raw else "TBD"


def generate_m3u(streams):
    lines = ["#EXTM3U"]
    for s in streams:
        time_str = format_match_time(s)
        title = (f"{s['home']} vs {s['away']} - {s['competition']} "
                 f"[{s['status']}] ({s['home_score']}-{s['away_score']}) {time_str}")
        logo = s.get("home_logo") or s.get("away_logo") or ""
        lines.append(f'#EXTINF:0,tvg-name="{title}" tvg-logo="{logo}" group-title="{GROUP_TITLE}",{title}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(s['stream_url'])
        lines.append("")
    return "\n".join(lines)


# ─── MAIN ───────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  ✓")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}")
    print(f"  ✓")
    print(f"  ✓:{MAX_WORKERS} | ✓:{RETRIES}")
    print("=" * 70)

    print("\n🔑 Login...")
    uid, _ = anonymous_login()
    if not uid:
        print("  ❌ Login gagal.")
        return
    print(f"  uid = {uid}")

    print("\n📡 Fetching...")
    r = SESSION.get(f"{WEB_BASE}/")
    matches = extract_matches_rsc(r.text)
    print(f"  ✅ {len(matches)} matches")

    for m in matches:
        print(f"     [{m['status']}] {m['home']} vs {m['away']} | id={m['id']} | has_stream={m['has_stream']}")

    # ─── PERBAIKAN: filter dengan has_stream + status ───
    # Hanya coba match yang memang punya stream (has_stream=1) ATAU sedang LIVE/UPC
    streamable = [m for m in matches
                  if m["status_id"] in (1, 2, 3, 4)
                  and (m["has_stream"] in (1, True) or m["status_id"] in (2, 3))]
    print(f"  📺 {len(streamable)}")

    if not streamable:
        print("  ⚠ Tidak ada.")
        return

    print(f"\n🔧 ({MAX_WORKERS} {RETRIES} )...")
    results = []
    failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_stream_for_match, m, uid): m for m in streamable}
        for i, fut in enumerate(as_completed(futures)):
            match, stream_url = fut.result()
            label = f"{match['home']} vs {match['away']} [{match['status']}]"
            if stream_url:
                results.append({**match, "stream_url": stream_url})
            else:
                failed.append(match)

    # ─── PERBAIKAN: Second pass untuk yang gagal ───
    if failed:
        print(f"\n🔄 Retry {len(failed)} failed (delay 5s)...")
        time.sleep(5)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS // 2) as pool:
            futures = {pool.submit(get_stream_for_match, m, uid): m for m in failed}
            for fut in as_completed(futures):
                match, stream_url = fut.result()
                if stream_url:
                    results.append({**match, "stream_url": stream_url})
                    failed.remove(match)

    results.sort(key=lambda x: x.get("match_time") or datetime.max.replace(tzinfo=WIB))

    if results:
        m3u = generate_m3u(results)
        with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
            f.write(m3u)
        print(f"\n✅ {OUTPUT_M3U} {len(results)}")
        print(f"   Failed: {len(failed)}")
        print(m3u[:1000])
    else:
        print("\n❌ 0 streams.")


if __name__ == "__main__":
    main()   















