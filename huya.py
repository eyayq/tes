#!/usr/bin/env python3
"""Scrape stream URLs dari vipbox.ga/live-schedule + Playwright fallback"""

from curl_cffi import requests as cffi_requests
import re, json, base64, logging, time, threading, sys
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://vipbox.ga"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 30
MAX_WORKERS = 8
PW_TIMEOUT = 30000
PW_WAIT_MS = 15000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "id-ID,id;q=0.6",
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("Scrape")


def extract_json_block(text, var_name):
    idx = text.find(var_name)
    if idx == -1: return None
    bs = text.find("{", idx)
    if bs == -1: return None
    depth, in_str, esc = 0, False, False
    for i in range(bs, len(text)):
        c = text[i]
        if esc: esc = False; continue
        if c == '\\': esc = True; continue
        if c == '"' and not in_str: in_str = True
        elif c == '"' and in_str: in_str = False
        elif not in_str:
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try: return json.loads(text[bs:i+1])
                    except: break
    return None


def is_base64(s):
    if len(s) < 20 or len(s) % 4 > 2: return False
    try:
        base64.b64decode(s, validate=True); return True
    except: return False


# ═══════════════════════════════════════════════════════════
# PLAYWRIGHT INTERCEPTOR
# ═══════════════════════════════════════════════════════════
class PlaywrightInterceptor:
    def __init__(self):
        self._local = threading.local()
        self._browsers = []
        self._lock = threading.Lock()

    def _ensure(self):
        if hasattr(self._local, 'browser'): return
        with self._lock:
            if hasattr(self._local, 'browser'): return
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-web-security",
                ]
            )
            self._local.pw = pw
            self._local.browser = browser
            self._browsers.append((pw, browser))
            log.info(f"  [PW] Browser init")

    def intercept(self, url, referer=None, wait_ms=PW_WAIT_MS):
        self._ensure()
        browser = self._local.browser
        captured = []

        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={"Referer": referer or f"{BASE_URL}/"},
            viewport={"width": 390, "height": 844},
        )
        page = ctx.new_page()

        def on_response(response):
            try:
                ru = response.url
                if re.search(r'\.(m3u8|mpd|flv|ts)(\?|$)', ru):
                    if "vipbox.ga" not in ru:
                        captured.append(ru)
                elif re.search(r'/(live|hls|stream|vod|play|video)/', ru):
                    if "vipbox.ga" not in ru:
                        captured.append(ru)
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct or "html" in ct:
                    try: body = response.text()
                    except: return
                    for u in re.findall(
                        r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
                        r'(?:[^\s"\'<>\\]*)?)', body
                    ):
                        if "vipbox.ga" not in u:
                            captured.append(u)
            except: pass

        page.on("response", on_response)

        try:
            log.info(f"    [PW] {url}")
            page.goto(url, wait_until="domcontentloaded",
                      timeout=PW_TIMEOUT)
            elapsed = 0
            while elapsed < wait_ms:
                page.wait_for_timeout(500)
                elapsed += 500
                if captured:
                    page.wait_for_timeout(2000)
                    break
            # Scan final HTML
            html = page.content()
            for u in re.findall(
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
                r'(?:[^\s"\'<>\\]*)?)', html
            ):
                if "vipbox.ga" not in u and u not in captured:
                    captured.append(u)
        except Exception as ex:
            log.warning(f"    [PW] Error: {ex}")
        finally:
            ctx.close()

        seen = set()
        unique = [u for u in captured if u not in seen and not seen.add(u)]
        log.info(f"    [PW] Captured {len(unique)} URLs")
        return unique

    def close(self):
        with self._lock:
            for pw, browser in self._browsers:
                try: browser.close()
                except: pass
                try: pw.stop()
                except: pass
            self._browsers.clear()


# ═══════════════════════════════════════════════════════════
# MAIN SCRAPER
# ═══════════════════════════════════════════════════════════
class Scraper:
    def __init__(self):
        self.s = cffi_requests.Session(impersonate="chrome")
        self.s.headers.update(HEADERS)
        self.pw = PlaywrightInterceptor()

    def __del__(self):
        try: self.pw.close()
        except: pass

    # ── 1. Fetch schedule ────────────────────────────────
    def fetch_schedule(self):
        url = f"{BASE_URL}/live-schedule"
        log.info(f"GET {url}")
        r = self.s.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cfg = None
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                cfg = extract_json_block(t, "siteConfig")
                break
        if not cfg:
            log.error("No siteConfig!"); return [], {}

        sd = cfg.get("scheduleData", "")
        tk = cfg.get("fetck_token", "")
        r2 = self.s.post(
            f"{BASE_URL}/schedule/{sd}/",
            headers={"fetch-token": tk, "Referer": url,
                     "Origin": BASE_URL,
                     "Content-Type": "application/json"},
            timeout=TIMEOUT
        )
        data = r2.json()
        events = self._parse_events(soup)
        log.info(f"  {len(events)} events")
        return events, data

    def _parse_events(self, soup):
        events = []
        for d in soup.find_all(
            "details", attrs={"vt-data-events-row": True}
        ):
            a = d.find("a")
            if not a: continue
            title = a.get("title", "").strip()
            eid = a.get("data-bs-target", "").lstrip("#")
            if not eid: continue
            href = a.get("href", "")
            gc = "others"
            if href.startswith("/onair/"):
                parts = href.strip("/").split("/")
                if len(parts) >= 2: gc = parts[1]
            t_el = d.find("time")
            etime = None
            if t_el and t_el.get("content"):
                try: etime = datetime.fromisoformat(t_el["content"])
                except: pass
            events.append({
                "title": title, "id": eid, "time": etime,
                "onair_uri": href, "gc": gc,
            })
        return events

    # ── 2. Resolve stream per event ──────────────────────
    def resolve_event(self, ev, data):
        eid = ev["id"]
        links = data.get("links", {}).get(eid, [])
        if not links:
            links = data.get("players", {}).get(eid, []) or []
        slug = data.get("slugs", {}).get(eid, eid)
        gc = data.get("linkAppends", {}).get(eid, ev["gc"])

        results = []
        for i, lk in enumerate(links, 1):
            live_uri = f"/live/{gc}/{slug}-{i}"
            page_url = f"{BASE_URL}{live_uri}"

            stream = None
            # Try HTTP first
            try:
                r = self.s.get(page_url, timeout=TIMEOUT,
                               headers={"Referer": f"{BASE_URL}/"})
                stream = self._extract_stream(r.text)
            except Exception as e:
                log.debug(f"  HTTP fail: {e}")

            # Fallback: Playwright
            if not stream:
                log.info(f"    [PW] Fallback: {live_uri}")
                pw_urls = self.pw.intercept(
                    page_url, referer=f"{BASE_URL}/"
                )
                if pw_urls:
                    stream = pw_urls[0]

            results.append({
                "name": ev["title"],
                "time": ev["time"].astimezone(WIB).strftime("%H:%M")
                        if ev["time"] else "24/7",
                "link_no": i,
                "player": lk.get("player", "?"),
                "lang": lk.get("lang", ""),
                "hd": lk.get("hd", 0),
                "stream_url": stream,
                "page_url": page_url,
            })
        return results

    # ── 3. Extract stream from HTML ──────────────────────
    def _extract_stream(self, html):
        # Direct URLs
        for m in re.findall(
            r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
            r'(?:[^\s"\'<>\\]*)?)', html
        ):
            if "vipbox.ga" not in m:
                return m

        # data-pldm + data-play → POST
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(attrs={"data-pldm": True}):
            pldm = el.get("data-pldm", "")
            play = el.get("data-play", "")
            if not pldm or not play: continue
            cfg = self._get_cfg(html) or {}
            stream = self._post_player(
                pldm, play,
                el.get("data-pid", "6"),
                el.get("data-player", "jw"),
                el.get("data-gc", "motorsports"),
                cfg
            )
            if stream: return stream

        # window vars (base64)
        for name, val in self._window_vars(html):
            if len(val) < 20 or not is_base64(val): continue
            try:
                dec = base64.b64decode(val).decode("utf-8", "ignore")
            except: continue
            try:
                obj = json.loads(dec)
                urls = self._urls_in_obj(obj)
                if urls: return urls[0]
            except: pass
            found = re.findall(
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
                r'(?:[^\s"\'<>\\]*)?)', dec
            )
            if found: return found[0]

        # Path-based
        for m in re.findall(
            r'(https?://[^\s"\'<>\\]+/(?:stream|live|hls'
            r'|play|video|vod)/[^\s"\'<>\\]+)', html
        ):
            if "vipbox.ga" not in m:
                return m
        return None

    def _get_cfg(self, html):
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                return extract_json_block(t, "siteConfig")
        return None

    def _post_player(self, pldm, play, pid, player, gc, cfg):
        payload = {
            "pid": pid, "vimg": play, "player": player,
            "csrf": cfg.get("csrf", ""),
            "csrf_ip": cfg.get("csrf_ip", ""),
            "expires": cfg.get("sec_expires", ""),
            "sec_hash": cfg.get("sec_hash", ""),
        }
        b64 = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        for post_url in (f"https://{pldm}/vimpl/{gc}",
                         f"https://{pldm}/vimpl"):
            try:
                r = self.s.post(
                    post_url, data={"payload": b64},
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                        "Origin": BASE_URL,
                        "Referer": f"{BASE_URL}/",
                    },
                    timeout=TIMEOUT, allow_redirects=True,
                )
                stream = self._extract_stream(r.text)
                if stream: return stream
            except: continue
        return None

    def _window_vars(self, html):
        m = re.findall(
            r"window\.([A-Za-z0-9_\-]+)\s*=\s*"
            r"['\"]([^'\"]{20,})['\"]", html)
        m += re.findall(
            r"window\[['\"]([A-Za-z0-9_\-]+)['\"]\]\s*=\s*"
            r"['\"]((?:[^'\"\\]|\\.)*+)['\"]", html, re.DOTALL)
        return [(n, v.strip()) for n, v in m]

    def _urls_in_obj(self, obj):
        urls = []
        if isinstance(obj, str):
            if obj.startswith("http") and re.search(
                r'\.(m3u8|mpd|flv|ts)', obj):
                urls.append(obj)
        elif isinstance(obj, dict):
            for k in ("file","src","url","hls","dash",
                      "source","stream","manifest","m3u8"):
                if k in obj:
                    urls.extend(self._urls_in_obj(obj[k]))
            if "sources" in obj:
                urls.extend(self._urls_in_obj(obj["sources"]))
        elif isinstance(obj, list):
            for i in obj:
                urls.extend(self._urls_in_obj(i))
        return urls

    # ── 4. Run ───────────────────────────────────────────
    def run(self):
        log.info("=" * 50)
        log.info("  VIPBox Live Schedule + Playwright")
        log.info(f"  Target: {BASE_URL}/live-schedule")
        log.info("=" * 50)

        events, data = self.fetch_schedule()
        if not events:
            log.error("No events"); return

        now = datetime.now(WIB)
        live = [e for e in events if e["time"] and e["time"] <= now]
        if not live:
            live = events[:10]
        log.info(f"  Live: {len(live)} events")

        all_streams = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {
                pool.submit(self.resolve_event, ev, data): ev
                for ev in live
            }
            for fut in as_completed(futs):
                try:
                    all_streams.extend(fut.result())
                except Exception as e:
                    log.warning(f"  Error: {e}")

        # Output
        resolved = sum(1 for s in all_streams if s["stream_url"])
        log.info(f"\n  Total: {len(all_streams)}, "
                 f"Resolved: {resolved}")
        print(f"\n{'='*60}")
        for s in sorted(all_streams,
                        key=lambda x: (x["time"], x["name"])):
            hd = " [HD]" if s["hd"] else ""
            lang = f" [{s['lang']}]" if s["lang"] else ""
            icon = "✅" if s["stream_url"] else "❌"
            print(f"\n{icon} {s['time']}  {s['name']}{hd}{lang}")
            print(f"   Page:   {s['page_url']}")
            if s["stream_url"]:
                print(f"   Stream: {s['stream_url']}")
        print(f"\n{'='*60}")


if __name__ == "__main__":
    Scraper().run()   
