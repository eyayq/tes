import json
import uuid
import hashlib
import time
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from curl_cffi import requests as cffi_requests

# ─── CONFIG ─────────────────────────────────────────────
API_PROXY  = "https://px.bhns.workers.dev/?url=https://www.camel1.tv"   # Proxy API & Website
PROXY_BASE = "https://px.bhns.workers.dev/?url="   # Proxy Upstream (m3u8)
SECRET     = "ganti-dengan-secret-panjang"

OUTPUT_M3U  = "playlist.m3u"
MAX_WORKERS = 5
RETRIES     = 3
RETRY_DELAY = 2

APP_CODE    = "D04B29D6B957CD44DC5F9894189380B8"
GROUP_TITLE = "live camelia vip [tes]"
USER_AGENT  = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36"
REFERER     = "https://www.camel1.tv/"
ORIGIN      = "https://www.camel1.tv"

WIB = timezone(timedelta(hours=7))
STATUS_MAP = {1: "NS", 2: "LIVE", 3: "UPC", 4: "FT", 5: "HT"}

SESSION = cffi_requests.Session(impersonate="chrome", timeout=30)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("camel")


# ─── PROXY HELPERS ──────────────────────────────────────

def _api_proxy(api, **params):
    """Bangun URL untuk proxy API/website (index.php)."""
    qs = f"auth={SECRET}&api={api}"
    for k, v in params.items():
        qs += f"&{k}={quote(str(v), safe='')}"
    return f"{API_PROXY}?{qs}"


def _upstream_proxy(stream_url):
    """Wrap stream URL ke proxy upstream (proxy.php) — m3u8 rewrite."""
    return f"{PROXY_BASE}?u={quote(stream_url, safe='')}"


# ─── API VIA PROXY ──────────────────────────────────────

def anonymous_login():
    r = SESSION.get(_api_proxy("login"))
    if r.status_code != 200:
        return None, None
    data = r.json()
    return data.get("uid"), data.get("token")


def fetch_matches_html():
    r = SESSION.get(_api_proxy("matches"))
    if r.status_code != 200:
        return ""
    return r.text


def get_stream_url(match_id, uid):
    for attempt in range(RETRIES):
        r = SESSION.get(_api_proxy("stream", match_id=match_id, uid=uid))
        if r.status_code == 200:
            data = r.json()
            url = data.get("stream_url")
            if url:
                return url
        if attempt < RETRIES - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def get_token(stream_url, uid):
    stream_name = stream_url.rstrip(".m3u8").split("/")[-1]
    r = SESSION.get(_api_proxy("token", stream_name=stream_name, uid=uid))
    if r.status_code == 200:
        data = r.json()
        return data.get("tx_time"), data.get("tx_secret")
    return None, None


# ─── PARSE RSC ──────────────────────────────────────────

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


# ─── BUILD URL ──────────────────────────────────────────

def build_final_url(stream_url, tx_time, tx_secret):
    """
    1. Tambah txSecret/txTime jika ada
    2. Wrap ke proxy upstream (proxy.php) — m3u8 di-rewrite, segment langsung ke CDN
    """
    if tx_time and tx_secret:
        tx_secret_url = hashlib.md5(tx_secret.encode()).hexdigest()
        full_url = f"{stream_url}?txSecret={tx_secret_url}&txTime={tx_time}&lat=9000"
    else:
        full_url = stream_url

    return _upstream_proxy(full_url)


def get_stream_for_match(match, uid):
    match_id = match["id"]
    label = f"{match['home']} vs {match['away']} [{match['status']}]"

    stream_url = get_stream_url(match_id, uid)
    if not stream_url:
        log.info(f"  ❌ No stream: {label}")
        return match, None

    tx_time, tx_secret = get_token(stream_url, uid)
    final_url = build_final_url(stream_url, tx_time, tx_secret)

    log.info(f"  ✅ {label}")
    return match, final_url


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
    print(f"  Camel TV — M3U Generator")
    print(f"  {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}")
    print(f"  Workers: {MAX_WORKERS} | Retries: {RETRIES}")
    print(f"  API Proxy:  {API_PROXY}")
    print(f"  Upstream:   {PROXY_BASE}")
    print("=" * 70)

    print("\n🔑 Login...")
    uid, _ = anonymous_login()
    if not uid:
        print("  ❌ Login gagal.")
        return
    print(f"  uid = {uid}")

    print("\n📡 Fetching matches...")
    html = fetch_matches_html()
    matches = extract_matches_rsc(html)
    print(f"  ✅ {len(matches)} matches")

    for m in matches:
        print(f"     [{m['status']}] {m['home']} vs {m['away']} | id={m['id']} | has_stream={m['has_stream']}")

    streamable = [m for m in matches
                  if m["status_id"] in (1, 2, 3, 4)
                  and (m["has_stream"] in (1, True) or m["status_id"] in (2, 3))]
    print(f"  📺 {len(streamable)} streamable")

    if not streamable:
        print("  ⚠ Tidak ada.")
        return

    print(f"\n🔧 Fetching streams ({MAX_WORKERS} workers, {RETRIES} retries)...")
    results = []
    failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_stream_for_match, m, uid): m for m in streamable}
        for fut in as_completed(futures):
            match, stream_url = fut.result()
            if stream_url:
                results.append({**match, "stream_url": stream_url})
            else:
                failed.append(match)

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
        print(f"\n✅ {OUTPUT_M3U} — {len(results)} streams")
        print(f"   Failed: {len(failed)}")
        print("\n" + m3u[:1500])
    else:
        print("\n❌ 0 streams.")


if __name__ == "__main__":
    main()   
