import json
import re
import time
import asyncio
import base64
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, quote
from collections import Counter
from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright

# ─── CONFIG ─────────────────────────────────────────────
OUTPUT_M3U = "playlist.m3u"
WIB = timezone(timedelta(hours=7))

API_BASE = "https://api.footypaper.top/api/soccer"
API_SLIM = f"{API_BASE}/today/slim"
API_EVENT = f"{API_BASE}/events"

TIMEOUT = 20
USE_DETAIL = True
DEBUG = False
PW_TIMEOUT = 30000
M3U8_WAIT = 15

MAX_WORKERS = 150
REQUEST_DELAY = 0

# ★ PROXY (Cloudflare Worker — query param)
PROXY_BASE = "https://pl-gratosv---al.bhns.workers.dev"
PLAYER_UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36"

HEADERS_API = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.livesports808.to",
    "Referer": "https://www.livesports808.to/",
}

# ─── API (sync) ─────────────────────────────────────────
def api_get(url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            r = cffi_requests.get(url, headers=HEADERS_API, impersonate="chrome", timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                time.sleep(attempt * 2)
        except Exception as e:
            if attempt < retries:
                time.sleep(attempt)
            else:
                print(f"    ⚠ {e}")
    return None

def fetch_matches(date: str = None) -> list:
    if not date:
        date = datetime.now(WIB).strftime("%Y-%m-%d")
    data = api_get(f"{API_SLIM}?date={date}")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("matches", "data", "today", "list"):
            if key in data and isinstance(data[key], list):
                return data[key]
        if date in data and isinstance(data[date], list):
            return data[date]
    return []

def fetch_event(match_id: str) -> dict:
    data = api_get(f"{API_EVENT}/{match_id}", retries=2)
    return data if isinstance(data, dict) else {}

# ─── CLASSIFY ───────────────────────────────────────────
def get_match_status(m: dict) -> str:
    s = str(m.get("s", "")).lower()
    if s in ("live", "1", "2", "3", "1h", "2h", "ht", "in", "playing", "et", "pen", "bt"):
        return "live"
    elif s in ("ft", "aet", "cancelled", "abandoned", "postp", "canc", "walkover"):
        return "finished"
    return "upcoming"

# ─── MATCH TIME ─────────────────────────────────────────
def parse_match_time(m: dict, detail: dict) -> datetime:
    raw_time = None
    if detail:
        for key in ("kickoff_time", "start_time", "time", "date_time", "match_time", "kickoff", "start"):
            val = detail.get(key)
            if val:
                raw_time = val
                break
    if not raw_time:
        for key in ("t", "time", "kickoff", "start", "date_time", "match_time"):
            val = m.get(key)
            if val:
                raw_time = val
                break

    dt = None
    if raw_time:
        raw_str = str(raw_time)
        tz_match = re.search(r'([+-]\d{2}:\d{2})$', raw_str)
        if tz_match:
            tz_str = tz_match.group(1)
            tz_hours = int(tz_str[:3])
            tz_minutes = int(tz_str[3:]) * (1 if tz_str[1] == '+' else -1)
            tz_offset = timezone(timedelta(hours=tz_hours, minutes=tz_minutes))
            raw_str = raw_str[:tz_match.start()]
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(raw_str, fmt).replace(tzinfo=tz_offset)
                    break
                except ValueError:
                    continue
        if not dt:
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(raw_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
        if not dt:
            try:
                ts = int(raw_time)
                if ts > 1000000000:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        if not dt:
            for fmt in ("%H:%M", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(str(raw_time), fmt).replace(tzinfo=WIB)
                    break
                except ValueError:
                    continue

    if not dt:
        match_id = m.get("id", "")
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', match_id)
        if date_match:
            try:
                dt = datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=WIB)
            except ValueError:
                pass

    if dt and dt.tzinfo != WIB:
        dt = dt.astimezone(WIB)
    return dt if dt else datetime.max.replace(tzinfo=WIB)

def format_match_time(dt: datetime) -> str:
    if dt == datetime.max.replace(tzinfo=WIB):
        return ""
    return dt.strftime("%H:%M WIB %d/%m/%Y")

# ─── LOGO ───────────────────────────────────────────────
def extract_logo(m: dict, detail: dict) -> str:
    if detail:
        for key in ("home_team_logo", "logo", "team_logo", "home_logo"):
            val = detail.get(key, "")
            if val and isinstance(val, str) and val.startswith("http"):
                return val
        for team_key in ("home_team", "teams"):
            team = detail.get(team_key)
            if isinstance(team, dict):
                for key in ("logo", "crest", "icon"):
                    val = team.get(key, "")
                    if val and isinstance(val, str) and val.startswith("http"):
                        return val
            elif isinstance(team, list) and team:
                for key in ("logo", "crest", "icon"):
                    val = team[0].get(key, "")
                    if val and isinstance(val, str) and val.startswith("http"):
                        return val
    for key in ("hl", "logo", "home_logo", "team_logo"):
        val = m.get(key, "")
        if val and isinstance(val, str):
            if val.startswith("http"):
                return val
            if val.startswith("/"):
                return f"https://api.footypaper.top{val}"
    return ""

# ─── HELPERS ────────────────────────────────────────────
def format_name_slim(m: dict) -> str:
    name = f"{m.get('ht','?')} vs {m.get('at','?')}"
    if m.get("c"):
        name += f" [{m['c']}]"
    hs, as_ = m.get("hs"), m.get("as")
    if hs is not None and as_ is not None and hs != "" and as_ != "":
        name += f" ({hs}-{as_})"
    return name

def format_name_detail(d: dict) -> str:
    name = f"{d.get('home_team','?')} vs {d.get('away_team','?')}"
    if d.get("competition"):
        name += f" [{d['competition']}]"
    hs, as_ = d.get("home_score"), d.get("away_score")
    if hs is not None and as_ is not None:
        name += f" ({hs}-{as_})"
    if d.get("match_minute"):
        name += f" [{d['match_minute']}']"
    return name

def normalize_urls(urls) -> list:
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list):
        return []
    return [u for u in urls if isinstance(u, str) and u]

# ─── PROXY WRAP (query param) ───────────────────────────
def wrap_proxy(url: str, match_id: str = "", origin: str = "") -> str:
    """
    Format: https://pl-gratosv-al.bhns.workers.dev/live?id=<encoded_url>&mid=xxx&origin=xxx
    """
    params = f"id={quote(url, safe='')}"
    if match_id:
        params += f"&mid={match_id}"
    if origin:
        params += f"&origin={quote(origin, safe='')}"
    return f"{PROXY_BASE}/live.m3u8?{params}"

# ─── GENERATE M3U ───────────────────────────────────────
def generate_m3u(entries: list) -> str:
    lines = ["#EXTM3U"]
    for e in entries:
        logo = e.get("logo", "")
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1{logo_attr} group-title="{e["group"]}",{e["name"]}')
        lines.append(e["proxy_url"])
    return "\n".join(lines) + "\n"

# ─── ASYNC RESOLVE ──────────────────────────────────────
async def resolve_stream_async(page, php_url: str) -> tuple:
    """Return (m3u8_url, player_origin)."""
    m3u8_url = None
    player_origin = ""
    found = asyncio.Event()

    def on_request(request):
        nonlocal m3u8_url, player_origin
        if ".m3u8" in request.url:
            m3u8_url = request.url
            headers = request.headers
            player_origin = headers.get("origin", "")
            if not player_origin:
                referer = headers.get("referer", "")
                if referer:
                    parsed = urlparse(referer)
                    player_origin = f"{parsed.scheme}://{parsed.netloc}"
            found.set()

    def on_response(response):
        nonlocal m3u8_url, player_origin
        if ".m3u8" in response.url and response.status == 200:
            if not m3u8_url:
                m3u8_url = response.url
            if not player_origin:
                try:
                    req = response.request
                    headers = req.headers
                    player_origin = headers.get("origin", "")
                    if not player_origin:
                        referer = headers.get("referer", "")
                        if referer:
                            parsed = urlparse(referer)
                            player_origin = f"{parsed.scheme}://{parsed.netloc}"
                except Exception:
                    pass
            found.set()

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        resp = await page.goto(php_url, wait_until="networkidle", timeout=PW_TIMEOUT)
        if DEBUG:
            status = resp.status if resp else "None"
            print(f"    [debug] goto → status={status}, url={page.url[:80]}")
        await asyncio.wait_for(found.wait(), timeout=M3U8_WAIT)
    except asyncio.TimeoutError:
        if DEBUG:
            print(f"    [debug] TIMEOUT m3u8 — page.url={page.url[:100]}")
    except Exception as e:
        if DEBUG:
            print(f"    [debug] ERROR {type(e).__name__}: {e}")
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    if not player_origin:
        try:
            for frame in page.frames:
                if frame != page.main_frame:
                    parsed = urlparse(frame.url)
                    if parsed.netloc:
                        player_origin = f"{parsed.scheme}://{parsed.netloc}"
                        break
        except Exception:
            pass

    if not m3u8_url:
        try:
            content = await page.content()
            match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', content)
            if match:
                m3u8_url = match.group(1)
                if DEBUG:
                    print(f"    [debug] m3u8 found via DOM fallback: {m3u8_url[:80]}")
        except Exception:
            pass

    if DEBUG:
        print(f"    [debug] m3u8={m3u8_url[:60] if m3u8_url else 'None'} | origin={player_origin}")

    return (m3u8_url or "", player_origin)

async def process_match_async(page, task: dict) -> list:
    entries = []
    for url in task["urls"]:
        m3u8_url, player_origin = await resolve_stream_async(page, url)
        if m3u8_url:
            proxy_url = wrap_proxy(m3u8_url, task["match_id"], player_origin)
            entries.append({
                "name": task["name"],
                "url": m3u8_url,
                "proxy_url": proxy_url,
                "logo": task["logo"],
                "group": task["group"],
                "match_id": task["match_id"],
                "player_origin": player_origin,
            })
        await asyncio.sleep(REQUEST_DELAY)
    return entries

# ─── PARALLEL RUNNER ────────────────────────────────────
async def run_resolvers(browser, tasks: list) -> list:
    semaphore = asyncio.Semaphore(MAX_WORKERS)
    all_entries = []
    completed = [0]
    total = len(tasks)

    async def limited_process(page, task):
        async with semaphore:
            try:
                entries = await process_match_async(page, task)
                completed[0] += 1
                status = "✅" if entries else "⏳"
                print(f"  {status} [{completed[0]}/{total}] {task['name'][:60]} ({len(entries)} streams)")
                return entries
            except Exception as e:
                completed[0] += 1
                print(f"  ❌ [{completed[0]}/{total}] {task['name'][:60]} — {e}")
                return []

    pages = []
    for _ in range(MAX_WORKERS):
        context = await browser.new_context(
            user_agent=PLAYER_UA,
            viewport={"width": 412, "height": 915},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="id-ID",
            extra_http_headers={
                "sec-ch-ua": '"Not:A-Brand";v="8", "Chromium";v="156", "Brave";v="156"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
            },
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['id-ID','id','en-US','en']});
            Object.defineProperty(navigator, 'platform', {get: () => 'Linux armv81'});
            window.chrome = { runtime: {} };
        """)
        pages.append(await context.new_page())

    async def worker(task_idx, task):
        page = pages[task_idx % MAX_WORKERS]
        return await limited_process(page, task)

    coroutines = [worker(i, task) for i, task in enumerate(tasks)]
    results = await asyncio.gather(*coroutines)

    for r in results:
        all_entries.extend(r)

    for page in pages:
        await page.context.close()

    return all_entries

# ─── MAIN ───────────────────────────────────────────────
def main():
    start = time.time()
    now = datetime.now(WIB)
    today_wib = now.strftime("%Y-%m-%d")
    today_utc = now.astimezone(timezone.utc).strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"  ⚽ M3U Generator — {now.strftime('%Y-%m-%d %H:%M WIB')}")
    print(f"  Engine: curl_cffi + Playwright async (mobile)")
    print(f"  Mode:   Worker query-param proxy")
    print(f"  Workers: {MAX_WORKERS} | Delay: {REQUEST_DELAY}s | M3U8_WAIT: {M3U8_WAIT}s")
    print(f"  Date: WIB={today_wib} | UTC={today_utc}")
    print(f"  Proxy: {PROXY_BASE}/live.m3u8?id=...")
    print("=" * 50)

    # [1] Fetch matches
    print(f"\n[1] Fetching matches . . .")
    matches = fetch_matches(today_wib)
    if today_utc != today_wib:
        matches_utc = fetch_matches(today_utc)
        seen_ids = {m.get("id") for m in matches}
        for m in matches_utc:
            if m.get("id") not in seen_ids:
                matches.append(m)
                seen_ids.add(m.get("id"))
        print(f"  [info] Merged UTC ({today_utc}) + WIB ({today_wib})")

    if not matches:
        print("  ❌ No data")
        return
    print(f"  ✅ {len(matches)} total matches")

    status_values = Counter(str(m.get("s", "")).lower() for m in matches)
    if DEBUG:
        print(f"  [debug] Status values: {dict(status_values)}")

    live_matches = []
    upcoming_matches = []
    for m in matches:
        status = get_match_status(m)
        urls = normalize_urls(m.get("u", []))
        if status == "live":
            live_matches.append((m, urls, "Live"))
        elif status == "upcoming":
            upcoming_matches.append((m, urls, "Upcoming"))

    print(f"  → {len(live_matches)} live, {len(upcoming_matches)} upcoming")

    to_process = live_matches + upcoming_matches

    if not to_process:
        print(f"\n  ⏳ Tidak ada match saat ini.")
        return

    # [2] Prepare tasks
    print(f"\n[2] Preparing {len(to_process)} matches . . .")
    tasks = []
    for m, urls, group in to_process:
        match_id = m.get("id", "")
        detail = {}
        if USE_DETAIL and match_id:
            detail = fetch_event(match_id)
            if detail:
                detail_urls = detail.get("urls")
                if isinstance(detail_urls, list) and detail_urls:
                    urls = normalize_urls(detail_urls)

        if detail:
            name = format_name_detail(detail)
        else:
            name = format_name_slim(m)

        match_dt = parse_match_time(m, detail)

        if group == "Upcoming" and match_dt != datetime.max.replace(tzinfo=WIB):
            match_dt_utc = match_dt.astimezone(timezone.utc)
            if match_dt_utc.date() != datetime.now(timezone.utc).date():
                continue

        time_str = format_match_time(match_dt)
        if time_str:
            name += f" | {time_str}"

        logo = extract_logo(m, detail)
        tasks.append({
            "m": m, "urls": urls, "group": group,
            "detail": detail, "name": name, "logo": logo,
            "match_dt": match_dt,
            "match_id": match_id,
        })

    live_tasks = [t for t in tasks if t["group"] == "Live"]
    upcoming_tasks = [t for t in tasks if t["group"] == "Upcoming"]
    live_tasks.sort(key=lambda t: t["match_dt"], reverse=True)
    upcoming_tasks.sort(key=lambda t: t["match_dt"])
    tasks = live_tasks + upcoming_tasks

    tasks_to_resolve = [t for t in tasks if t["urls"]]
    tasks_no_url = [t for t in tasks if not t["urls"]]

    print(f"  ✅ {len(tasks)} total | {len(tasks_to_resolve)} to resolve | {len(tasks_no_url)} no URL")
    for i, t in enumerate(tasks, 1):
        time_str = format_match_time(t["match_dt"])
        url_mark = "🔗" if t["urls"] else "⏳"
        print(f"    {i:3d}. [{t['group']:8s}] {time_str:20s} {url_mark} {t['name'][:50]}")

    # [3] Resolve streams
    if tasks_to_resolve:
        print(f"\n[3] Resolving {len(tasks_to_resolve)} streams ({MAX_WORKERS} workers) . . .")

        async def async_main():
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
                )
                entries = await run_resolvers(browser, tasks_to_resolve)
                await browser.close()
                return entries

        all_entries = asyncio.run(async_main())
    else:
        print(f"\n[3] No streams to resolve.")
        all_entries = []

    # [4] Write output
    print(f"\n[4] Writing {OUTPUT_M3U} . . .")

    playable = [e for e in all_entries if e["proxy_url"]]
    waiting = len(tasks_no_url)

    m3u = generate_m3u(playable)
    with open(OUTPUT_M3U, "w") as f:
        f.write(m3u)

    live_count = sum(1 for e in playable if e["group"] == "Live")
    upcoming_count = sum(1 for e in playable if e["group"] == "Upcoming")
    logo_count = sum(1 for e in playable if e.get("logo"))

    print(f"  ✅ {len(playable)} streams in {OUTPUT_M3U}")
    print(f"     • Live: {live_count}")
    print(f"     • Upcoming: {upcoming_count}")
    print(f"     • With logo: {logo_count}/{len(playable)}")
    print(f"     • Waiting (no URL yet): {waiting}")
    print(f"     • Proxy: {PROXY_BASE}/live.m3u8?id=...")

    if playable:
        print(f"\n  📺 Sample:")
        print(f"     {playable[0]['proxy_url'][:120]}...")

    with open("result.json", "w") as f:
        json.dump({
            "date": today_wib,
            "time": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
            "streams": len(playable),
            "waiting": waiting,
            "live": live_count,
            "upcoming": upcoming_count,
            "entries": playable,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n  ⏱ {time.time() - start:.1f}s")

if __name__ == "__main__":
    main()   
