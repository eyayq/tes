

import json
import re
import time
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
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
DEBUG = True
PW_TIMEOUT = 30000
M3U8_WAIT = 7

MAX_WORKERS = 200
REQUEST_DELAY = 0.5

UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36"

HEADERS_API = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.livesports808.to",
    "Referer": "https://www.livesports808.to/",
}
HEADERS_HTML = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
        for key in ["matches", "data", "today", "list"]:
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
    if s in ("live", "1", "2", "3"):
        return "live"
    elif s in ("ft", "aet", "pen", "cancelled", "abandoned"):
        return "finished"
    return "upcoming"

# ─── MATCH TIME (return datetime object for sorting) ────
def parse_match_time(m: dict, detail: dict) -> datetime:
    """
    Return a datetime object (WIB) for sorting.
    If time unknown, return datetime.max (sorts last).
    """
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
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(str(raw_time), fmt).replace(tzinfo=timezone.utc)
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

def generate_m3u(entries: list) -> str:
    lines = ["#EXTM3U"]
    for e in entries:
        logo = e.get("logo", "")
        group = e.get("group", "Live")
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1{logo_attr} group-title="{group}",{e["name"]}')
        h = e.get("headers", {})
        if h.get("user_agent"):
            lines.append(f'#EXTVLCOPT:http-user-agent={h["user_agent"]}')
        if h.get("referrer"):
            lines.append(f'#EXTVLCOPT:http-user-agent={h["user_agent"]}')
        if h.get("referrer"):
            lines.append(f'#EXTVLCOPT:http-referrer={h["referrer"]}')
        if h.get("referrer"):
            lines.append(f'#EXTVLCOPT:http-origin={h["origin"]}')
        lines.append(e["url"])
    return "\n".join(lines) + "\n"

# ─── ASYNC RESOLVE ──────────────────────────────────────
async def resolve_stream_async(page, php_url: str) -> tuple:
    m3u8_url = None
    m3u8_headers = {}
    found = asyncio.Event()

    def on_request(request):
        nonlocal m3u8_url, m3u8_headers
        if ".m3u8" in request.url:
            m3u8_url = request.url
            h = request.headers
            m3u8_headers = {
                "user_agent": h.get("user-agent", UA),
                "referrer": h.get("referer", ""),
                "origin": h.get("origin", ""),
            }
            found.set()

    def on_response(response):
        nonlocal m3u8_url
        if ".m3u8" in response.url and response.status == 200:
            if not m3u8_url:
                m3u8_url = response.url
            found.set()

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        await page.goto(php_url, wait_until="domcontentloaded", timeout=PW_TIMEOUT)
        await asyncio.wait_for(found.wait(), timeout=M3U8_WAIT)
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

    if m3u8_url:
        if not m3u8_headers:
            parsed = urlparse(m3u8_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            m3u8_headers = {"user_agent": UA, "referrer": origin + "/", "origin": origin}
        return (m3u8_url, m3u8_headers)
    return ("", {})

async def process_match_async(page, task: dict) -> list:
    entries = []
    for url in task["urls"]:
        m3u8_url, headers = await resolve_stream_async(page, url)
        if m3u8_url:
            entries.append({
                "name": task["name"],
                "url": m3u8_url,
                "logo": task["logo"],
                "group": task["group"],
                "headers": headers,
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
                status = "✅" if entries else "⚠"
                print(f"  {status} [{completed[0]}/{total}] {task['name'][:60]} ({len(entries)} streams)")
                return entries
            except Exception as e:
                completed[0] += 1
                print(f"  ❌ [{completed[0]}/{total}] {task['name'][:60]} — {e}")
                return []

    pages = []
    for _ in range(MAX_WORKERS):
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 412, "height": 915},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="id-ID",
            extra_http_headers={
                "sec-ch-ua": '"Chromium";v="158", "Not.A/Brand";v="24", "Google Chrome";v="158"',
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
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    now_wib = datetime.now(WIB)

    print("=" * 50)
    print(f"  ⚽ M3U Generator — {today}")
    print(f"  Engine: curl_cffi + Playwright async (mobile)")
    print(f"  Groups: Live + Upcoming (sorted by time)")
    print(f"  Workers: {MAX_WORKERS} | Delay: {REQUEST_DELAY}s")
    print("=" * 50)

    print(f"\n[1] Fetching matches . . .")
    matches = fetch_matches(today)
    if not matches:
        print("  ❌ No data")
        return
    print(f"  ✅ {len(matches)} total matches")

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

    to_process = [(m, urls, group) for m, urls, group in live_matches + upcoming_matches if urls]

    if not to_process:
        print(f"\n  ⏳ Tidak ada match dengan stream URL saat ini.")
        return

    # Prepare tasks (sync API calls)
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

        # ★ Parse time as datetime for sorting
        match_dt = parse_match_time(m, detail)
        time_str = format_match_time(match_dt)
        if time_str:
            name += f" | {time_str}"

        logo = extract_logo(m, detail)
        tasks.append({
            "m": m, "urls": urls, "group": group,
            "detail": detail, "name": name, "logo": logo,
            "match_dt": match_dt,  # ★ for sorting
        })

    # ★ SORT: chronological by match time (live = past, upcoming = future)
    #   Live matches: sorted by start time (most recent first → descending)
    #   Upcoming matches: sorted by kickoff time (nearest first → ascending)
    #   Combined: live (descending by start) then upcoming (ascending by kickoff)
    live_tasks = [t for t in tasks if t["group"] == "Live"]
    upcoming_tasks = [t for t in tasks if t["group"] == "Upcoming"]

    live_tasks.sort(key=lambda t: t["match_dt"], reverse=True)    # newest first
    upcoming_tasks.sort(key=lambda t: t["match_dt"])               # nearest first

    tasks = live_tasks + upcoming_tasks

    print(f"  ✅ {len(tasks)} tasks ready (sorted by time)")

    # Print sorted order
    for i, t in enumerate(tasks, 1):
        time_str = format_match_time(t["match_dt"])
        print(f"    {i:2d}. [{t['group']:8s}] {time_str:20s} {t['name'][:50]}")

    # ─── ASYNC PARALLEL RESOLUTION ──────────────────────
    print(f"\n[3] Resolving streams ({MAX_WORKERS} workers) . . .")

    async def async_main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            entries = await run_resolvers(browser, tasks)
            await browser.close()
            return entries

    all_entries = asyncio.run(async_main())

    # ─── WRITE OUTPUT ───────────────────────────────────
    print(f"\n[4] Writing {OUTPUT_M3U} . . .")
    m3u = generate_m3u(all_entries)
    with open(OUTPUT_M3U, "w") as f:
        f.write(m3u)

    live_count = sum(1 for e in all_entries if e["group"] == "Live")
    upcoming_count = sum(1 for e in all_entries if e["group"] == "Upcoming")
    logo_count = sum(1 for e in all_entries if e.get("logo"))
    hdr_count = sum(1 for e in all_entries if e.get("headers"))

    print(f"  ✅ {len(all_entries)} streams")
    print(f"     • Live: {live_count}")
    print(f"     • Upcoming: {upcoming_count}")
    print(f"     • With logo: {logo_count}/{len(all_entries)}")
    print(f"     • With EXTVLCOPT: {hdr_count}/{len(all_entries)}")

    with open("a/jesson.json", "w") as f:
        json.dump({
            "date": today,
            "time": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB"),
            "streams": len(all_entries),
            "live": live_count,
            "upcoming": upcoming_count,
            "entries": all_entries,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n  ⏱ {time.time()-start:.1f}s")

if __name__ == "__main__":
    main()   










    
