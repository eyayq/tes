#!/usr/bin/env python3
"""
VIPBox M3U Generator v9.5
+ Thread-safe session (per-thread)
+ Race condition fix (processing guard)
+ Playwright JS execution via page.route() mock
+ Better URL extraction
"""

from curl_cffi import requests as cffi_requests
import re
import json
import base64
import logging
import time
import threading
import os
import sys
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ── CONFIG ───────────────────────────────────────────────
DEBUG = "--debug" in sys.argv
FORCE_PW = "--pw" in sys.argv
DEBUG_DIR = "debug_output"
if DEBUG:
    os.makedirs(DEBUG_DIR, exist_ok=True)

BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 30
MAX_WORKERS = 4
IMPERSONATE = "chrome"
MAX_RETRIES = 2
REQUEST_DELAY = 0.5
PW_TIMEOUT = 20000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "id-ID,id;q=0.6",
}

CATEGORIES = [
    ("nfl", "NFL"), ("ncaaf", "NCAAF"), ("basketball", "Basketball"),
    ("hockey", "Hockey"), ("tennis", "Tennis"), ("golf", "Golf"),
    ("football", "Football"), ("rugby", "Rugby"), ("afl", "AFL"),
    ("darts", "Darts"), ("mma", "MMA"), ("wwe", "WWE"),
    ("boxing", "Boxing"), ("fighting", "Fighting"),
    ("motorsports", "Motorsports"), ("others", "Others"),
]

GC_TO_GROUP = {
    "nfl": "NFL", "ncaaf": "NCAAF", "basketball": "Basketball",
    "nba": "Basketball", "college-basketball": "Basketball",
    "hockey": "Hockey", "khl": "Hockey", "nhl": "Hockey",
    "finland": "Hockey", "russia": "Hockey",
    "czech-republic": "Hockey",
    "tennis": "Tennis", "davis-cup": "Tennis",
    "golf": "Golf", "football": "Football", "soccer": "Football",
    "worldcup": "Football", "bahrain": "Football", "qatar": "Football",
    "romania": "Football", "serbia": "Football",
    "slovenia": "Football", "bulgaria": "Football",
    "rugby": "Rugby", "afl": "AFL",
    "darts": "Darts", "mma": "MMA", "boxing": "Boxing",
    "fighting": "Fighting", "motorsports": "Motorsports",
    "formula-1": "Motorsports", "motogp": "Motorsports",
    "nascar": "Motorsports",
    "american-football": "NFL",
    "wwe": "WWE",
    "cycling": "Others", "handball": "Others", "snooker": "Others",
    "volleyball": "Others", "sports": "Others",
    "others": "Others", "other-sports": "Others",
}

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("VIPBox")


def debug_save(name, content):
    if not DEBUG:
        return
    safe = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    path = os.path.join(DEBUG_DIR, safe)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if isinstance(content, str) else str(content))


def is_base64(s):
    if len(s) < 20:
        return False
    if len(s) % 4 > 2:
        return False
    try:
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False


def extract_json_block(text, var_name):
    idx = text.find(var_name)
    if idx == -1:
        return None
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(brace_start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == '\\':
            esc = True
            continue
        if c == '"' and not in_str:
            in_str = True
        elif c == '"' and in_str:
            in_str = False
        elif not in_str:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i+1])
                    except json.JSONDecodeError:
                        break
    return None


# ═══════════════════════════════════════════════════════════
# PLAYWRIGHT INTERCEPTOR (v9.5: JS execution via route mock)
# ═══════════════════════════════════════════════════════════
class PlaywrightInterceptor:
    def __init__(self):
        self._local = threading.local()
        self._all_browsers = []
        self._lock = threading.Lock()

    def _ensure_browser(self):
        if hasattr(self._local, 'browser'):
            return
        with self._lock:
            if hasattr(self._local, 'browser'):
                return
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
            self._all_browsers.append((pw, browser))
            log.info(
                f"  [PW] Browser init "
                f"(thread={threading.current_thread().name})"
            )

    def _get_browser(self):
        self._ensure_browser()
        return self._local.browser

    def close(self):
        with self._lock:
            for pw, browser in self._all_browsers:
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass
            self._all_browsers.clear()

    def intercept_post(self, post_url, payload_b64,
                       referer=None, post_html=None):
        """
        v9.5: Mock POST response via page.route(),
        lalu page.goto() agar JS execute dengan origin benar.
        """
        self._ensure_browser()
        browser = self._get_browser()
        captured = []

        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={
                "Referer": referer or f"{BASE_URL}/",
                "Origin": BASE_URL,
            },
            viewport={"width": 390, "height": 844},
        )
        page = context.new_page()

        def on_response(response):
            try:
                ru = response.url
                if re.search(r'\.(m3u8|mpd|flv|ts)(\?|$)', ru):
                    if "vipbox.ga" not in ru:
                        captured.append(ru)
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct:
                    body = response.text()
                    for u in re.findall(
                        r'(https?://[^\s"\'<>\\]+\.(?:'
                        r'm3u8|mpd|flv|ts)(?:[^\s"\'<>\\]*)?)',
                        body
                    ):
                        if "vipbox.ga" not in u:
                            captured.append(u)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            # ── Step 1: Dapatkan HTML POST response ──
            if not post_html:
                resp = context.request.post(
                    post_url,
                    data=f"payload={payload_b64}",
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                        "Origin": BASE_URL,
                        "Referer": referer or f"{BASE_URL}/",
                    },
                    timeout=PW_TIMEOUT,
                )
                post_html = resp.text()
                log.debug(
                    f"  [PW] POST {post_url} → "
                    f"{resp.status}, {len(post_html)}B"
                )

            # ── Step 2: Mock route — intercept POST & fulfill ──
            def handle_route(route):
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=post_html,
                )

            page.route(post_url, handle_route)

            # ── Step 3: Navigate ke POST URL (JS akan jalan) ──
            page.goto(post_url, wait_until="domcontentloaded",
                      timeout=PW_TIMEOUT)

            # ── Step 4: Tunggu JS decrypt + player request ──
            for _ in range(30):
                page.wait_for_timeout(500)
                if captured:
                    break

            # ── Step 5: Cek page content (JS inject URL) ──
            try:
                final_html = page.content()
                for u in re.findall(
                    r'(https?://[^\s"\'<>\\]+\.(?:'
                    r'm3u8|mpd|flv|ts)(?:[^\s"\'<>\\]*)?)',
                    final_html
                ):
                    if "vipbox.ga" not in u and u not in captured:
                        captured.append(u)
                # URL tanpa extension
                for u in re.findall(
                    r'(https?://[^\s"\'<>\\]+/(?:stream|live'
                    r'|hls|play|video|vod)/[^\s"\'<>\\]+)',
                    final_html
                ):
                    if "vipbox.ga" not in u and u not in captured:
                        captured.append(u)
            except Exception:
                pass

        except Exception as ex:
            log.debug(f"  [PW] POST Error: {ex}")
        finally:
            context.close()

        seen = set()
        return [u for u in captured
                if u not in seen and not seen.add(u)]

    def intercept_page(self, url, referer=None, wait_ms=5000):
        self._ensure_browser()
        browser = self._get_browser()
        captured = []
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={
                "Referer": referer or f"{BASE_URL}/",
                "Origin": BASE_URL,
            },
            viewport={"width": 390, "height": 844},
        )
        page = context.new_page()

        def on_response(response):
            try:
                ru = response.url
                if re.search(
                    r'\.(m3u8|mpd|flv|ts)(\?|$)', ru
                ) or re.search(r'/(live|hls|stream|vod)/', ru):
                    if "vipbox.ga" not in ru:
                        captured.append(ru)
                ct = response.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct \
                        or "html" in ct:
                    body = response.text()
                    for u in re.findall(
                        r'(https?://[^\s"\'<>\\]+\.(?:'
                        r'm3u8|mpd|flv|ts)(?:[^\s"\'<>\\]*)?)',
                        body
                    ):
                        if "vipbox.ga" not in u:
                            captured.append(u)
                    if "#EXTM3U" in body[:500]:
                        for u in re.findall(
                            r'(https?://[^\s"\'<>\\]+\.ts'
                            r'[^\s"\'<>\\]*)', body
                        ):
                            captured.append(u)
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=PW_TIMEOUT)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            for u in re.findall(
                r'(https?://[^\s"\'<>\\]+\.(?:'
                r'm3u8|mpd|flv|ts)(?:[^\s"\'<>\\]*)?)', html
            ):
                if "vipbox.ga" not in u:
                    captured.append(u)
        except Exception as ex:
            log.debug(f"  [PW] Page Error: {ex}")
        finally:
            context.close()

        seen = set()
        return [u for u in captured
                if u not in seen and not seen.add(u)]

    def _extract_stream_urls(self, text):
        urls = []
        try:
            data = json.loads(text)
            urls.extend(self._urls_from_jw(data))
        except (json.JSONDecodeError, TypeError):
            pass
        for m in re.findall(
            r'["\']?(?:file|src|url|hls|dash|source|manifest)'
            r'["\']?\s*[:=]\s*["\'](https?://[^"\']+)["\']',
            text, re.IGNORECASE
        ):
            if m not in urls:
                urls.append(m)
        for m in re.findall(
            r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
            r'(?:[^\s"\'<>\\]*)?)', text
        ):
            if m not in urls:
                urls.append(m)
        for m in re.findall(
            r'(https?://[^\s"\'<>\\]+/(?:stream|live|hls'
            r'|play|video|vod)/[^\s"\'<>\\]+)', text
        ):
            if m not in urls:
                urls.append(m)
        return urls

    def _urls_from_jw(self, data):
        urls = []
        def _c(obj):
            if isinstance(obj, str):
                if obj.startswith("http") and re.search(
                    r'\.(m3u8|mpd|flv|ts)', obj
                ):
                    urls.append(obj)
            elif isinstance(obj, dict):
                for k in ("file", "src", "url", "hls", "dash",
                          "source", "stream", "manifest", "m3u8"):
                    if k in obj:
                        _c(obj[k])
                if "sources" in obj:
                    _c(obj["sources"])
                if "playlist" in obj:
                    _c(obj["playlist"])
            elif isinstance(obj, list):
                for i in obj:
                    _c(i)
        _c(data)
        return urls


# ═══════════════════════════════════════════════════════════
# MAIN CLASS
# ═══════════════════════════════════════════════════════════
class VIPBox:
    def __init__(self):
        self._local = threading.local()
        self.play_cache = {}
        self.uri_cache = {}
        self.onair_cache = {}
        self._lock = threading.Lock()
        self._processing = set()
        self.pw = PlaywrightInterceptor()

    def _get_session(self):
        if not hasattr(self._local, 's'):
            self._local.s = cffi_requests.Session(
                impersonate=IMPERSONATE
            )
            self._local.s.headers.update(HEADERS)
        return self._local.s

    def __del__(self):
        try:
            self.pw.close()
        except Exception:
            pass

    # ── 1. FETCH SCHEDULE ────────────────────────────────
    def fetch_category(self, slug, name):
        url = f"{BASE_URL}/{slug}-schedule"
        log.info(f"  GET {url}")
        try:
            r = self._get_session().get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log.error(f"    FAIL: {e}")
            return []

        debug_save(f"schedule_{slug}.html", r.text)
        soup = BeautifulSoup(r.text, "html.parser")
        cfg = self._extract_site_config(soup)
        if not cfg:
            log.warning("    No siteConfig")
            return []

        sched_data = cfg.get("scheduleData", "")
        token = cfg.get("fetck_token", "")
        try:
            r2 = self._get_session().post(
                f"{BASE_URL}/schedule/{sched_data}/",
                headers={
                    "fetch-token": token, "Referer": url,
                    "Origin": BASE_URL,
                    "Content-Type": "application/json",
                }, timeout=TIMEOUT
            )
            debug_save(f"schedule_{slug}_post.json", r2.text)
            data = r2.json()
        except Exception as e:
            log.error(f"    POST FAIL: {e}")
            data = {"links": {}}

        events = self._parse_events(soup)
        log.info(f"    {len(events)} events")
        return self._build_results(events, data, name)

    def fetch_livenow(self):
        url = f"{BASE_URL}/livenow-schedule"
        log.info(f"  GET {url}")
        try:
            r = self._get_session().get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log.error(f"    FAIL: {e}")
            return []

        debug_save("schedule_livenow.html", r.text)
        soup = BeautifulSoup(r.text, "html.parser")
        cfg = self._extract_site_config(soup)
        if not cfg:
            log.warning("    No siteConfig")
            return []

        sched_data = cfg.get("scheduleData", "")
        token = cfg.get("fetck_token", "")
        try:
            r2 = self._get_session().post(
                f"{BASE_URL}/schedule/{sched_data}/",
                headers={
                    "fetch-token": token, "Referer": url,
                    "Origin": BASE_URL,
                    "Content-Type": "application/json",
                }, timeout=TIMEOUT
            )
            debug_save("schedule_livenow_post.json", r2.text)
            data = r2.json()
        except Exception as e:
            log.error(f"    POST FAIL: {e}")
            data = {"links": {}}

        events = self._parse_events(soup)
        log.info(f"    {len(events)} events (livenow)")
        return self._build_results(events, data, "Others")

    def _build_results(self, events, data, name):
        results = []
        links_map = data.get("links", {})
        slugs = data.get("slugs", {})
        link_appends = data.get("linkAppends", {})

        for ev in events:
            ev_id = ev["id"]
            ev_links = links_map.get(ev_id, [])

            if not ev_links:
                players = data.get("players", {}).get(ev_id, [])
                if players:
                    ev_links = players
                else:
                    ev_links = [{"player": "?", "lang": "", "hd": 0}]

            slug = slugs.get(ev_id, ev_id)
            append_gc = link_appends.get(ev_id, ev["gc"])
            group = GC_TO_GROUP.get(append_gc, name)

            for i, lk in enumerate(ev_links, 1):
                live_uri = f"/live/{append_gc}/{slug}-{i}"
                results.append({
                    "name": ev["title"], "group": group,
                    "logo": ev["logo"], "time": ev["time"],
                    "player": lk.get("player", "?"),
                    "lang": lk.get("lang", ""), "hd": lk.get("hd", 0),
                    "live_uri": live_uri,
                    "onair_uri": ev["onair_uri"],
                    "eid": ev_id, "link_no": i, "gc": append_gc,
                })

        return results

    def _extract_site_config(self, soup):
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                return extract_json_block(t, "siteConfig")
        return None

    def _parse_events(self, soup):
        events = []
        for d in soup.find_all(
            "details", attrs={"vt-data-events-row": True}
        ):
            a = d.find("a")
            if not a:
                continue
            title = a.get("title", "").strip()
            eid = a.get("data-bs-target", "").lstrip("#")
            if not eid:
                continue
            href = a.get("href", "")
            onair_uri = href
            gc = "others"
            if href.startswith("/onair/"):
                parts = href.strip("/").split("/")
                if len(parts) >= 2:
                    gc = parts[1]

            t_el = d.find("time")
            etime = None
            if t_el and t_el.get("content"):
                try:
                    etime = datetime.fromisoformat(t_el["content"])
                except Exception:
                    pass

            icon = d.find("i", class_=re.compile(r"vip-box-icon"))
            icon_cls = ""
            if icon:
                for c in icon.get("class", []):
                    if c not in (
                        "align-bottom", "me-2", "vip-box-icon"
                    ):
                        icon_cls = c.strip()
                        break

            events.append({
                "title": title, "id": eid, "time": etime,
                "onair_uri": onair_uri, "gc": gc,
                "logo": self._icon_to_logo(icon_cls),
            })
        return events

    def _icon_to_logo(self, icon_cls):
        m = {
            "nascar": "motorsports", "formula-1": "motorsports",
            "motogp": "motorsports", "college-football": "ncaaf",
            "soccer": "football", "other-sports": "other-sports",
            "college-basketball": "basketball",
            "nba": "basketball", "nfl": "nfl",
            "davis-cup": "tennis", "khl": "hockey", "nhl": "hockey",
            "worldcup": "football", "bahrain": "football",
            "qatar": "football", "romania": "football",
            "serbia": "football", "slovenia": "football",
            "bulgaria": "football", "russia": "hockey",
            "czech-republic": "hockey", "finland": "hockey",
            "australia": "basketball", "south-korea": "basketball",
            "italy": "basketball", "television": "nfl",
        }
        key = m.get(icon_cls, icon_cls) if icon_cls else "football"
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── 2. EKSTRAK PARAM VIDEO ───────────────────────────
    def get_play_params(self, e):
        with self._lock:
            if e["eid"] in self.onair_cache:
                return self.onair_cache[e["eid"]]

        all_params = []
        if e["onair_uri"]:
            params = self._extract_all_from_page(e["onair_uri"])
            if params:
                all_params.extend(params)

        params_live = self._extract_all_from_page(e["live_uri"])
        if params_live:
            existing = {(p["pldm"], p["play"]) for p in all_params}
            for p in params_live:
                key = (p["pldm"], p["play"])
                if key not in existing:
                    all_params.append(p)
                    existing.add(key)

        if all_params:
            with self._lock:
                self.onair_cache[e["eid"]] = list(all_params)
        return all_params

    def _extract_all_from_page(self, uri):
        with self._lock:
            if uri in self.uri_cache:
                return self.uri_cache[uri]

        url = f"{BASE_URL}{uri}"
        result = []
        try:
            r = self._get_session().get(
                url, timeout=TIMEOUT,
                headers={"Referer": f"{BASE_URL}/"}
            )
            html = r.text
            debug_save(
                f"player_{uri.strip('/').replace('/', '_')}.html",
                html
            )
            log.info(f"    GET {uri} → {r.status_code}, {len(html)}B")
            soup = BeautifulSoup(html, "html.parser")
            cfg = self._extract_site_config(soup) or {}

            result.extend(self._extract_data_attrs(soup, cfg))
            result.extend(self._extract_window_vars(html, cfg))
            result.extend(self._extract_iframe_players(soup, cfg))
            result.extend(self._extract_direct_urls(html))
            result.extend(self._extract_data_url_attrs(soup, cfg))

        except Exception as ex:
            log.debug(f"    GET fail {uri}: {ex}")

        seen = set()
        unique = []
        for p in result:
            key = (p.get("pldm", ""), p.get("play", ""))
            if key and key not in seen:
                seen.add(key)
                unique.append(p)

        log.info(f"    Extracted {len(unique)} params from {uri}")
        if not unique:
            log.warning(f"    NO PLAYER DATA in {uri}")

        with self._lock:
            self.uri_cache[uri] = unique
        return unique

    def _extract_data_attrs(self, soup, cfg):
        result = []
        for el in soup.find_all(attrs={"data-pldm": True}):
            pldm = el.get("data-pldm", "")
            play = el.get("data-play", "")
            if not pldm or not play:
                continue
            result.append({
                "pldm": pldm, "play": play,
                "pid": el.get("data-pid", "6"),
                "player": el.get("data-player", "jw"),
                "gc": el.get("data-gc", "motorsports"),
                "csrf": cfg.get("csrf", ""),
                "csrf_ip": cfg.get("csrf_ip", ""),
                "sec_expires": cfg.get("sec_expires", ""),
                "sec_hash": cfg.get("sec_hash", ""),
            })
        return result

    def _extract_window_vars(self, html, cfg):
        result = []
        matches = self._find_window_vars(html)
        seen_vars = set()
        for var_name, val in matches:
            if var_name in seen_vars or len(val) < 20:
                continue
            seen_vars.add(var_name)
            if not is_base64(val):
                continue
            try:
                decoded = base64.b64decode(val)
                text = decoded.decode("utf-8", errors="ignore")
            except Exception:
                continue
            result.extend(self._parse_decoded_player(text, html, cfg))
        return result

    def _find_window_vars(self, html):
        matches = []
        matches += re.findall(
            r"window\.([A-Za-z0-9_\-]+)\s*=\s*"
            r"['\"]([^'\"]{20,})['\"]", html
        )
        multi = re.findall(
            r"window\[['\"]([A-Za-z0-9_\-]+)['\"]\]\s*=\s*"
            r"['\"]((?:[^'\"\\]|\\.)*+)['\"]",
            html, re.DOTALL
        )
        for name, val in multi:
            val = val.strip()
            if len(val) >= 20:
                matches.append((name, val))
        for m in re.findall(
            r"(?:var|let|const)\s+([A-Za-z0-9_\-]+)\s*=\s*"
            r"['\"]([^'\"]{50,})['\"]", html
        ):
            name, val = m
            if is_base64(val):
                matches.append((name, val))
        return matches

    def _extract_iframe_players(self, soup, cfg):
        result = []
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "") or iframe.get("data-src", "")
            if not src:
                continue
            full = src if "://" in src else f"https://{src}"
            parsed = urlparse(full)
            if parsed.netloc and "vipbox" not in parsed.netloc:
                result.append({
                    "pldm": parsed.netloc, "play": full,
                    "pid": "6", "player": "iframe",
                    "gc": "motorsports",
                    "csrf": cfg.get("csrf", ""),
                    "csrf_ip": cfg.get("csrf_ip", ""),
                    "sec_expires": cfg.get("sec_expires", ""),
                    "sec_hash": cfg.get("sec_hash", ""),
                })
        return result

    def _extract_direct_urls(self, html):
        result = []
        patterns = [
            r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
            r'(?:[^\s"\'<>\\]*)?)',
            r'(https?://[^\s"\'<>\\]*/live/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/hls/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/stream/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/vod/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/play/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/video/[^\s"\'<>\\]+)',
        ]
        seen = set()
        for pat in patterns:
            for m in re.findall(pat, html):
                if "vipbox.ga" in m or m in seen:
                    continue
                seen.add(m)
                parsed = urlparse(m)
                result.append({
                    "pldm": parsed.netloc or "vipbox.ga",
                    "play": m, "pid": "6", "player": "direct",
                    "gc": "motorsports",
                    "csrf": "", "csrf_ip": "",
                    "sec_expires": "", "sec_hash": "",
                    "_direct_url": m,
                })
        return result

    def _extract_data_url_attrs(self, soup, cfg):
        result = []
        for attr in ("data-src", "data-url", "data-href",
                     "data-stream", "data-video", "data-file"):
            for el in soup.find_all(attrs={attr: True}):
                url = el.get(attr, "")
                if not url or not url.startswith("http"):
                    continue
                if "vipbox.ga" in url:
                    continue
                parsed = urlparse(url)
                if parsed.netloc:
                    result.append({
                        "pldm": parsed.netloc, "play": url,
                        "pid": "6", "player": "data_attr",
                        "gc": "motorsports",
                        "csrf": cfg.get("csrf", ""),
                        "csrf_ip": cfg.get("csrf_ip", ""),
                        "sec_expires": cfg.get("sec_expires", ""),
                        "sec_hash": cfg.get("sec_hash", ""),
                        "_direct_url": url,
                    })
        return result

    def _parse_decoded_player(self, text, html, cfg):
        result = []
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        p = self._dict_to_params(item, cfg)
                        if p:
                            result.append(p)
            elif isinstance(data, dict):
                for key in ("players", "sources", "streams"):
                    if key in data and isinstance(data[key], list):
                        for item in data[key]:
                            if isinstance(item, dict):
                                p = self._dict_to_params(item, cfg)
                                if p:
                                    result.append(p)
                        break
                else:
                    p = self._dict_to_params(data, cfg)
                    if p:
                        result.append(p)
        except (json.JSONDecodeError, TypeError):
            pass

        if not result:
            urls = re.findall(
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)'
                r'(?:[^\s"\'<>\\]*)?)', text
            )
            urls += re.findall(
                r'(https?://[^\s"\'<>\\]+/(?:stream|live|hls'
                r'|play|video|vod)/[^\s"\'<>\\]+)', text
            )
            for u in urls:
                if "vipbox.ga" in u:
                    continue
                parsed = urlparse(u)
                result.append({
                    "pldm": parsed.netloc or "vipbox.ga",
                    "play": u, "pid": "6", "player": "direct",
                    "gc": "motorsports",
                    "csrf": "", "csrf_ip": "",
                    "sec_expires": "", "sec_hash": "",
                    "_direct_url": u,
                })
        return result

    def _dict_to_params(self, data, cfg):
        pldm = (data.get("pldm", "") or data.get("domain", "") or
                data.get("server", "") or data.get("host", ""))
        play = (data.get("play", "") or data.get("vimg", "") or
                data.get("source", "") or data.get("file", "") or
                data.get("url", ""))
        if not pldm or not play:
            return None
        return {
            "pldm": pldm, "play": play,
            "pid": str(data.get("pid", "6")),
            "player": data.get("player", "jw"),
            "gc": data.get("gc", "motorsports"),
            "csrf": data.get("csrf", cfg.get("csrf", "")),
            "csrf_ip": data.get("csrf_ip", cfg.get("csrf_ip", "")),
            "sec_expires":
                data.get("sec_expires", cfg.get("sec_expires", "")),
            "sec_hash": data.get("sec_hash", cfg.get("sec_hash", "")),
        }

    # ── 3. POST KE PLAYER ────────────────────────────────
    def get_stream_from_player(self, params):
        if params.get("_direct_url"):
            url = params["_direct_url"]
            parsed = urlparse(url)
            return [(url, parsed.netloc or params["pldm"], None)]

        pldm = params["pldm"]
        gc = params["gc"]
        cache_key = (pldm, params["play"])

        with self._lock:
            if cache_key in self.play_cache:
                return list(self.play_cache[cache_key])
            if cache_key in self._processing:
                is_worker = False
            else:
                self._processing.add(cache_key)
                is_worker = True

        if not is_worker:
            for _ in range(60):
                time.sleep(0.5)
                with self._lock:
                    if cache_key in self.play_cache:
                        return list(self.play_cache[cache_key])
                    if cache_key not in self._processing:
                        break
            with self._lock:
                if cache_key in self.play_cache:
                    return list(self.play_cache[cache_key])
            return []

        payload_dict = {
            "pid": params["pid"], "vimg": params["play"],
            "player": params["player"], "csrf": params["csrf"],
            "csrf_ip": params["csrf_ip"],
            "expires": params["sec_expires"],
            "sec_hash": params["sec_hash"],
        }
        payload_b64 = base64.b64encode(
            json.dumps(payload_dict, separators=(",", ":")).encode()
        ).decode()

        post_urls = [
            f"https://{pldm}/vimpl/{gc}",
            f"https://{pldm}/vimpl",
        ]

        stream_urls = []
        last_post_html = None

        try:
            for post_url in post_urls:
                if stream_urls:
                    break
                for attempt in range(MAX_RETRIES):
                    try:
                        r = self._get_session().post(
                            post_url,
                            data={"payload": payload_b64},
                            headers={
                                "Content-Type":
                                    "application/x-www-form-urlencoded",
                                "Origin": BASE_URL,
                                "Referer": f"{BASE_URL}/",
                                "Sec-Fetch-Site": "cross-site",
                                "Sec-Fetch-Mode": "navigate",
                                "Sec-Fetch-Dest": "iframe",
                            },
                            timeout=TIMEOUT,
                            allow_redirects=True,
                        )
                        html = r.text
                        last_post_html = html
                        debug_save(
                            f"post_{pldm}_{gc}_a{attempt+1}.html",
                            f"URL: {post_url}\n"
                            f"Status: {r.status_code}\n\n{html}"
                        )
                        log.debug(
                            f"    POST {post_url} → "
                            f"{r.status_code}, {len(html)}B"
                        )

                        stream_urls.extend(
                            self._extract_window_var_stream(
                                html, pldm
                            )
                        )

                        if not stream_urls:
                            cfg = self._find_player_config(html)
                            if cfg:
                                stream_urls.extend(
                                    self._extract_streams_from_config(
                                        cfg, pldm
                                    )
                                )

                        if not stream_urls:
                            stream_urls.extend(
                                self._extract_streams_from_html(
                                    html, pldm
                                )
                            )

                        if stream_urls:
                            break
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(1)
                    except Exception as ex:
                        log.debug(
                            f"    POST fail {post_url} "
                            f"a{attempt+1}: {ex}"
                        )
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(1)

            # ── Playwright fallback (v9.5: JS execution) ──
            if not stream_urls:
                log.info(f"    [PW] Fallback for {pldm}")
                for post_url in post_urls:
                    pw_results = self.pw.intercept_post(
                        post_url, payload_b64,
                        referer=f"{BASE_URL}/",
                        post_html=last_post_html,
                    )
                    for u in pw_results:
                        parsed = urlparse(u)
                        stream_urls.append(
                            (u, parsed.netloc or pldm, None)
                        )
                    if stream_urls:
                        break

            if not stream_urls:
                stream_urls = self._try_direct_get(pldm, params)

        finally:
            with self._lock:
                self.play_cache[cache_key] = tuple(stream_urls)
                self._processing.discard(cache_key)

        return list(stream_urls)

    # ── Ekstrak window['...'] dari POST response ──
    def _extract_window_var_stream(self, html, pldm):
        results = []
        matches = self._find_window_vars(html)

        for var_name, val in matches:
            if len(val) < 20:
                continue
            if not is_base64(val):
                continue

            log.info(
                f"    [WIN] window['{var_name}'] len={len(val)}"
            )

            try:
                decoded = base64.b64decode(val)
                text = decoded.decode("utf-8", errors="ignore")
            except Exception:
                continue

            debug_save(f"post_decoded_{var_name}.txt", text)
            log.info(f"    [WIN] Decoded: {text[:300]}")

            # Parse: JSON
            try:
                data = json.loads(text)
                urls = self._extract_urls_from_jw_config(data)
                for u in urls:
                    parsed = urlparse(u)
                    domain = parsed.netloc or pldm
                    results.append((u, domain, None))
            except (json.JSONDecodeError, TypeError):
                pass

            # Parse: JS snippet
            if not results:
                js_urls = re.findall(
                    r'["\']?(?:file|src|url|hls|dash|source)'
                    r'["\']?\s*[:=]\s*'
                    r'["\'](https?://[^"\']+)["\']',
                    text, re.IGNORECASE
                )
                for u in js_urls:
                    if "vipbox.ga" not in u:
                        parsed = urlparse(u)
                        results.append(
                            (u, parsed.netloc or pldm, None)
                        )

            # Parse: direct URL (dengan extension)
            if not results:
                found = re.findall(
                    r'(https?://[^\s"\'<>\\]+\.(?:'
                    r'm3u8|mpd|flv|ts)(?:[^\s"\'<>\\]*)?)',
                    text
                )
                for u in found:
                    if "vipbox.ga" not in u:
                        parsed = urlparse(u)
                        results.append(
                            (u, parsed.netloc or pldm, None)
                        )

            # Parse: URL tanpa extension
            if not results:
                found = re.findall(
                    r'(https?://[^\s"\'<>\\]+/(?:stream|live|hls'
                    r'|play|video|vod)/[^\s"\'<>\\]+)',
                    text
                )
                for u in found:
                    if "vipbox.ga" not in u:
                        parsed = urlparse(u)
                        results.append(
                            (u, parsed.netloc or pldm, None)
                        )

        return results

    def _extract_urls_from_jw_config(self, data):
        urls = []
        def _c(obj):
            if isinstance(obj, str):
                if obj.startswith("http") and re.search(
                    r'\.(m3u8|mpd|flv|ts)', obj
                ):
                    urls.append(obj)
            elif isinstance(obj, dict):
                for k in ("file", "src", "url", "hls", "dash",
                          "source", "stream", "manifest", "m3u8"):
                    if k in obj:
                        _c(obj[k])
                if "sources" in obj:
                    _c(obj["sources"])
                if "playlist" in obj:
                    _c(obj["playlist"])
            elif isinstance(obj, list):
                for i in obj:
                    _c(i)
        _c(data)
        return urls

    def _find_player_config(self, html):
        for name in ("STUDY_CONFIG", "PLAYER_CONFIG", "playerConfig",
                     "JWPLAYER_CONFIG", "jwConfig", "videoConfig",
                     "VIDEO_CONFIG"):
            cfg = extract_json_block(html, name)
            if cfg:
                return cfg
        return None

    def _extract_streams_from_config(self, cfg, pldm):
        results = []
        source = (cfg.get("source", "") or
                  cfg.get("sources", "") or
                  cfg.get("file", ""))

        if isinstance(source, str) and source.startswith("http"):
            parsed = urlparse(source)
            results.append(
                (source, parsed.netloc or pldm, self._get_drm(cfg))
            )
        elif isinstance(source, dict):
            for key in ("hls", "dash", "url", "file", "src",
                        "mpd", "m3u8"):
                if key in source and source[key]:
                    su = source[key]
                    if isinstance(su, str) and su.startswith("http"):
                        parsed = urlparse(su)
                        results.append(
                            (su, parsed.netloc or pldm,
                             self._get_drm(cfg))
                        )
        elif isinstance(source, list):
            for item in source:
                if isinstance(item, str) and item.startswith("http"):
                    parsed = urlparse(item)
                    results.append(
                        (item, parsed.netloc or pldm,
                         self._get_drm(cfg))
                    )
                elif isinstance(item, dict):
                    for key in ("src", "file", "url", "hls", "dash"):
                        if key in item and item[key]:
                            su = item[key]
                            if isinstance(su, str) \
                                    and su.startswith("http"):
                                parsed = urlparse(su)
                                results.append(
                                    (su, parsed.netloc or pldm,
                                     self._get_drm(cfg))
                                )

        for key in ("hls", "dash", "url", "file", "src", "stream_url"):
            if key in cfg and isinstance(cfg[key], str) \
                    and cfg[key].startswith("http"):
                su = cfg[key]
                parsed = urlparse(su)
                results.append(
                    (su, parsed.netloc or pldm, self._get_drm(cfg))
                )
        return results

    def _get_drm(self, cfg):
        source = cfg.get("source", {})
        if isinstance(source, dict):
            drm = source.get("drm", cfg.get("drm", {}))
            if drm:
                ck = drm.get("clearkey", [])
                if ck and isinstance(ck, list):
                    return f"{ck[0]['kid']}:{ck[0]['key']}"
        return None

    def _extract_streams_from_html(self, html, pldm):
        results = []
        patterns = [
            r'"(https?://[^"]+\.(?:m3u8|mpd|flv|ts)[^"]*)"',
            r"'(https?://[^']+\.(?:m3u8|mpd|flv|ts)[^']*)'",
            r'"(https?://[^"]*/live/[^"]+)"',
            r"'(https?://[^']*/live/[^']+)'",
            r'"(https?://[^"]*/hls/[^"]+)"',
            r'"(https?://[^"]*/stream/[^"]+)"',
            r'"(https?://[^"]*/vod/[^"]+)"',
            r'"(https?://[^"]*/play/[^"]+)"',
            r'"(https?://[^"]*/video/[^"]+)"',
        ]
        seen = set()
        for pat in patterns:
            for m in re.findall(pat, html):
                if "vipbox.ga" in m or m in seen:
                    continue
                seen.add(m)
                parsed = urlparse(m)
                results.append((m, parsed.netloc or pldm, None))
        return results

    def _try_direct_get(self, pldm, params):
        results = []
        for path in (f"/{params['gc']}", f"/live/{params['gc']}",
                     f"/embed/{params['gc']}", "/"):
            try:
                r = self._get_session().get(
                    f"https://{pldm}{path}", timeout=10,
                    headers={"Referer": f"{BASE_URL}/"}
                )
                if r.status_code == 200 and len(r.text) > 100:
                    results.extend(
                        self._extract_window_var_stream(
                            r.text, pldm
                        )
                    )
                    if not results:
                        results = self._extract_streams_from_html(
                            r.text, pldm
                        )
                    if results:
                        break
            except Exception:
                continue

        if not results:
            log.info(f"    [PW] Direct GET for {pldm}")
            pw_urls = self.pw.intercept_page(
                f"https://{pldm}/{params['gc']}",
                referer=f"{BASE_URL}/", wait_ms=5000
            )
            for u in pw_urls:
                parsed = urlparse(u)
                results.append((u, parsed.netloc or pldm, None))
        return results

    # ── 4. PLAYWRIGHT PAGE INTERCEPT ─────────────────────
    def _pw_extract_page(self, uri):
        url = f"{BASE_URL}{uri}"
        log.info(f"    [PW] Intercepting {uri}")
        urls = self.pw.intercept_page(
            url, referer=f"{BASE_URL}/", wait_ms=5000
        )
        results = []
        for u in urls:
            parsed = urlparse(u)
            results.append({
                "pldm": parsed.netloc or "vipbox.ga",
                "play": u, "pid": "6", "player": "pw_intercept",
                "gc": "motorsports",
                "csrf": "", "csrf_ip": "",
                "sec_expires": "", "sec_hash": "",
                "_direct_url": u,
            })
        return results

    # ── 5. RESOLVE ENTRY ─────────────────────────────────
    def resolve_entry(self, e):
        params_list = self.get_play_params(e)

        if not params_list and (FORCE_PW or DEBUG):
            log.info(
                f"    [PW] Trying Playwright for {e['live_uri']}"
            )
            if e["onair_uri"]:
                params_list = self._pw_extract_page(e["onair_uri"])
            if not params_list:
                params_list = self._pw_extract_page(e["live_uri"])

        if not params_list and e.get("time") and \
                e["time"] <= datetime.now(WIB):
            log.info(f"    Retry {e['name']} (5s)")
            time.sleep(5)
            with self._lock:
                self.onair_cache.pop(e["eid"], None)
            params_list = self.get_play_params(e)

        if not params_list:
            e["videos"] = [{
                "player_domain": "vipbox.ga",
                "resolved_url": f"{BASE_URL}{e['live_uri']}",
                "drm_key": None,
            }]
            return e

        e["videos"] = []
        seen_urls = set()
        for idx, params in enumerate(params_list):
            stream_results = self.get_stream_from_player(params)
            for stream_url, domain, drm_key in stream_results:
                if not stream_url or stream_url in seen_urls:
                    continue
                seen_urls.add(stream_url)
                e["videos"].append({
                    "player_domain": domain or params["pldm"],
                    "resolved_url": stream_url,
                    "drm_key": drm_key,
                })
            time.sleep(REQUEST_DELAY)

        if not e["videos"]:
            e["videos"] = [{
                "player_domain": "vipbox.ga",
                "resolved_url": f"{BASE_URL}{e['live_uri']}",
                "drm_key": None,
            }]
        return e

    # ── 6. GENERATE M3U ──────────────────────────────────
    def write_m3u(self, entries):
        seen = set()
        unique = []
        for e in entries:
            key = (e["eid"], e["link_no"], e["group"])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        def sk(e):
            if e["time"]:
                return (0, e["time"])
            return (1, datetime.min.replace(tzinfo=timezone.utc))
        unique.sort(key=sk)

        lines = ["#EXTM3U"]
        ua = HEADERS["User-Agent"]
        fallback_url = "https://0.now1.tv/CC///CC.php"
        resolved_count = 0
        total_videos = 0

        for e in unique:
            parts = [e["name"]]
            if e["lang"]:
                parts.append(f"[{e['lang']}]")
            if e["hd"]:
                parts.append("[HD]")
            base_name = " ".join(parts)

            if e["time"]:
                wib = e["time"].astimezone(WIB)
                ts = wib.strftime("%d-%m-%Y %H:%M")
                now_wib = datetime.now(WIB)
                if e["time"] <= now_wib:
                    status = "LIVE"
                    not_started = False
                else:
                    status = "UPCOMING"
                    not_started = True
            else:
                ts = "LIVE 24/7"
                status = "LIVE"
                not_started = False

            videos = e.get("videos", [{
                "player_domain": "vipbox.ga",
                "resolved_url": f"{BASE_URL}{e['live_uri']}",
                "drm_key": None,
            }])
            multi = len(videos) > 1

            for vi, v in enumerate(videos, 1):
                if multi:
                    full_name = (
                        f"{status} | {base_name} | "
                        f"Video {vi} | {ts} WIB"
                    )
                else:
                    full_name = f"{status} | {base_name} | {ts} WIB"

                domain = v.get("player_domain", "vipbox.ga")
                origin = f"https://{domain}"
                referrer = f"{origin}/"
                final_url = v.get(
                    "resolved_url", f"{BASE_URL}{e['live_uri']}"
                )

                if not_started or "vipbox.ga" in final_url:
                    final_url = fallback_url
                else:
                    resolved_count += 1
                total_videos += 1

                lines.append(
                    f"#EXTVLCOPT:http-user-agent={ua}"
                )
                lines.append(f"#EXTVLCOPT:http-origin={origin}")
                lines.append(
                    f"#EXTVLCOPT:http-referrer={referrer}"
                )

                url_lower = final_url.lower()
                if ".mpd" in url_lower:
                    lines.append(
                        "#KODIPROP:inputstream.adaptive"
                        ".manifest_type=dash"
                    )
                elif ".m3u8" in url_lower:
                    lines.append(
                        "#KODIPROP:inputstream.adaptive"
                        ".manifest_type=hls"
                    )

                drm = v.get("drm_key")
                if drm:
                    lines.append(
                        "#KODIPROP:inputstream.adaptive"
                        ".license_type=org.w3.clearkey"
                    )
                    lines.append(
                        "#KODIPROP:inputstream.adaptive"
                        f".license_key={drm}"
                    )

                lines.append(
                    f'#EXTINF:-1 '
                    f'tvg-id="{e["group"].lower()}" '
                    f'tvg-name="{full_name}" '
                    f'tvg-logo="{e["logo"]}" '
                    f'group-title="{e["group"]}",{full_name}'
                )
                lines.append(final_url)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"\n  {OUTPUT_FILE} → {total_videos} stream")
        log.info(f"     Resolved: {resolved_count}/{total_videos}")
        log.info(
            f"     Fallback: "
            f"{total_videos - resolved_count}/{total_videos}"
        )

    # ── 7. DEBUG MODE ────────────────────────────────────
    def debug_single(self):
        log.info("=" * 50)
        log.info("  DEBUG: Football + 24/7")
        log.info("=" * 50)

        try:
            url = f"{BASE_URL}/football-schedule"
            r = self._get_session().get(url, timeout=TIMEOUT)
            r.raise_for_status()
            debug_save("debug_football.html", r.text)
            soup = BeautifulSoup(r.text, "html.parser")
            cfg = self._extract_site_config(soup)
            if not cfg:
                log.error("No siteConfig!")
                self.pw.close()
                return

            sched_data = cfg.get("scheduleData", "")
            token = cfg.get("fetck_token", "")
            r2 = self._get_session().post(
                f"{BASE_URL}/schedule/{sched_data}/",
                headers={
                    "fetch-token": token, "Referer": url,
                    "Origin": BASE_URL,
                    "Content-Type": "application/json",
                }, timeout=TIMEOUT
            )
            debug_save("debug_football_post.json", r2.text)
            data = r2.json()
            events = self._parse_events(soup)
            if not events:
                log.error("No events!")
                self.pw.close()
                return

            now_wib = datetime.now(WIB)
            live = [
                e for e in events
                if e["time"] and e["time"] <= now_wib
            ]
            ev = (live or events)[0]

            log.info(f"\n  [FOOTBALL] {ev['title']}")
            log.info(f"  ID: {ev['id']}")
            log.info(f"  onair: {ev['onair_uri']}")
            log.info(f"  time: {ev['time']}")

            slugs = data.get("slugs", {})
            appends = data.get("linkAppends", {})
            slug = slugs.get(ev["id"], ev["id"])
            gc = appends.get(ev["id"], ev["gc"])
            live_uri = f"/live/{gc}/{slug}-1"

            entry = {
                "name": ev["title"], "group": "Football",
                "logo": ev["logo"], "time": ev["time"],
                "player": "?", "lang": "", "hd": 0,
                "live_uri": live_uri,
                "onair_uri": ev["onair_uri"],
                "eid": ev["id"], "link_no": 1, "gc": gc,
            }

            log.info(f"\n  --- Extract params ---")
            params = self.get_play_params(entry)
            log.info(f"  Found: {len(params)}")
            for i, p in enumerate(params):
                log.info(f"    [{i}] {p['pldm']} | {p['play'][:60]}")

            if params:
                log.info(f"\n  --- Resolve stream ---")
                for i, p in enumerate(params):
                    results = self.get_stream_from_player(p)
                    log.info(f"    [{i}] → {len(results)} URLs:")
                    for u, d, drm in results:
                        log.info(f"       {u[:100]}")
            else:
                log.info(f"\n  --- [PW] Playwright ---")
                pw_params = self._pw_extract_page(ev["onair_uri"])
                log.info(f"  PW: {len(pw_params)}")
                for i, p in enumerate(pw_params):
                    log.info(f"    [{i}] {p['pldm']} | {p['play'][:60]}")
                    results = self.get_stream_from_player(p)
                    log.info(f"       → {len(results)} URLs:")
                    for u, d, drm in results:
                        log.info(f"         {u[:100]}")

            log.info(f"\n  Output: {os.path.abspath(DEBUG_DIR)}/")

        except Exception as e:
            log.error(f"  Debug error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.pw.close()

    # ── 8. MAIN ──────────────────────────────────────────
    def run(self):
        if DEBUG:
            self.debug_single()
            return

        log.info("=" * 50)
        log.info("  VIPBox M3U Generator v9.5")
        log.info(f"  Target : {BASE_URL}")
        log.info(f"  Output : {OUTPUT_FILE}")
        log.info(
            f"  Playwright: {'ON' if FORCE_PW else 'fallback'}"
        )
        log.info("=" * 50)

        all_entries = []
        for slug, name in CATEGORIES:
            entries = self.fetch_category(slug, name)
            log.info(f"  {name}: {len(entries)} streams")
            all_entries.extend(entries)
            time.sleep(0.3)

        livenow = self.fetch_livenow()
        log.info(f"  Livenow: {len(livenow)} streams")
        all_entries.extend(livenow)

        log.info(f"\n  Total: {len(all_entries)} entries")
        log.info("  Resolve stream URL...")

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as pool:
            futures = {
                pool.submit(self.resolve_entry, e): e
                for e in all_entries
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 25 == 0:
                    log.info(f"    {done}/{len(all_entries)}")
                try:
                    fut.result()
                except Exception as ex:
                    e = futures[fut]
                    e["videos"] = [{
                        "player_domain": "vipbox.ga",
                        "resolved_url":
                            f"{BASE_URL}{e['live_uri']}",
                        "drm_key": None,
                    }]
                    log.warning(f"    fail: {ex}")

        self.write_m3u(all_entries)
        self.pw.close()


if __name__ == "__main__":
    VIPBox().run()   
