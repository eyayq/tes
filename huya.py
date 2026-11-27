#!/usr/bin/env python3
"""
VIPBox M3U Generator v8.0
Mode debug: python vipbox.py --debug
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

# ── DEBUG MODE ──
DEBUG = "--debug" in sys.argv
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
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9",
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
    "finland": "Hockey", "russia": "Hockey", "czech-republic": "Hockey",
    "tennis": "Tennis", "davis-cup": "Tennis",
    "golf": "Golf", "football": "Football", "soccer": "Football",
    "worldcup": "Football", "bahrain": "Football", "qatar": "Football",
    "romania": "Football", "serbia": "Football", "slovenia": "Football",
    "bulgaria": "Football",
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
    """Simpan konten ke file debug."""
    if not DEBUG:
        return
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    path = os.path.join(DEBUG_DIR, safe_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if isinstance(content, str) else str(content))
    log.debug(f"  [DEBUG] Saved: {path}")


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


def extract_all_json_blocks(text, var_pattern):
    """FIX: Ekstrak SEMUA blok JSON yang match pattern (bukan cuma 1)."""
    results = []
    for m in re.finditer(var_pattern, text):
        brace_start = text.find("{", m.start())
        if brace_start == -1:
            continue
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
                            results.append(json.loads(text[brace_start:i+1]))
                        except json.JSONDecodeError:
                            pass
                        break
    return results


class VIPBox:
    def __init__(self):
        self.s = cffi_requests.Session(impersonate=IMPERSONATE)
        self.s.headers.update(HEADERS)
        self.play_cache = {}
        self.uri_cache = {}
        self.onair_cache = {}
        self._lock = threading.Lock()

    # ── 1. FETCH SCHEDULE ────────────────────────────────
    def fetch_category(self, slug, name):
        url = f"{BASE_URL}/{slug}-schedule"
        log.info(f"  GET {url}")
        try:
            r = self.s.get(url, timeout=TIMEOUT)
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

        post_url = f"{BASE_URL}/schedule/{sched_data}/"
        post_hdrs = {
            "fetch-token": token,
            "Referer": url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        }
        try:
            r2 = self.s.post(post_url, headers=post_hdrs, timeout=TIMEOUT)
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
            r = self.s.get(url, timeout=TIMEOUT)
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

        post_url = f"{BASE_URL}/schedule/{sched_data}/"
        post_hdrs = {
            "fetch-token": token,
            "Referer": url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        }
        try:
            r2 = self.s.post(post_url, headers=post_hdrs, timeout=TIMEOUT)
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
                ev_links = [{"player": "?", "lang": "", "hd": 0}]

            slug = slugs.get(ev_id, ev_id)
            append_gc = link_appends.get(ev_id, ev["gc"])
            group = GC_TO_GROUP.get(append_gc, name)

            for i, lk in enumerate(ev_links, 1):
                live_uri = f"/live/{append_gc}/{slug}-{i}"
                results.append({
                    "name": ev["title"],
                    "group": group,
                    "logo": ev["logo"],
                    "time": ev["time"],
                    "player": lk.get("player", "?"),
                    "lang": lk.get("lang", ""),
                    "hd": lk.get("hd", 0),
                    "live_uri": live_uri,
                    "onair_uri": ev["onair_uri"],
                    "eid": ev_id,
                    "link_no": i,
                    "gc": append_gc,
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
        for d in soup.find_all("details",
                               attrs={"vt-data-events-row": True}):
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
                    if c not in ("align-bottom", "me-2", "vip-box-icon"):
                        icon_cls = c.strip()
                        break

            events.append({
                "title": title,
                "id": eid,
                "time": etime,
                "onair_uri": onair_uri,
                "gc": gc,
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
            "davis-cup": "tennis", "khl": "hockey",
            "nhl": "hockey",
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
        """Gabungkan hasil dari onair_uri DAN live_uri."""
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
                self.onair_cache[e["eid"]] = all_params
        return all_params

    def _extract_all_from_page(self, uri):
        """Kumpulkan SEMUA sumber player dari halaman."""
        with self._lock:
            if uri in self.uri_cache:
                return self.uri_cache[uri]

        url = f"{BASE_URL}{uri}"
        result = []
        try:
            r = self.s.get(url, timeout=TIMEOUT,
                           headers={"Referer": f"{BASE_URL}/"})
            html = r.text
            debug_save(f"player_{uri.strip('/').replace('/', '_')}.html", html)
            log.info(f"    GET {uri} → {r.status_code}, {len(html)}B")
            soup = BeautifulSoup(html, "html.parser")
            cfg = self._extract_site_config(soup) or {}

            # ── Sumber 1: data-* attributes (per-element) ──
            result.extend(self._extract_data_attrs(soup, cfg))

            # ── Sumber 2: window['...'] obfuscated (SEMUA) ──
            result.extend(self._extract_window_vars(html, cfg))

            # ── Sumber 3: iframe src ──
            result.extend(self._extract_iframe_players(soup, cfg))

            # ── Sumber 4: URL stream langsung di HTML ──
            result.extend(self._extract_direct_urls(html))

            # ── Sumber 5: data-src, data-url, data-href attributes ──
            result.extend(self._extract_data_url_attrs(soup, cfg))

            # ── Sumber 6: fetch/XHR URL patterns ──
            result.extend(self._extract_fetch_urls(html, cfg))

        except Exception as ex:
            log.debug(f"    GET fail {uri}: {ex}")

        # Dedup
        seen = set()
        unique = []
        for p in result:
            key = (p.get("pldm", ""), p.get("play", ""))
            if key and key not in seen:
                seen.add(key)
                unique.append(p)

        log.info(f"    Extracted {len(unique)} player params from {uri}")
        if not unique:
            log.warning(f"    ⚠️ NO PLAYER DATA FOUND in {uri}")
            if DEBUG:
                # Tampilkan semua script tags untuk debugging
                for sc in soup.find_all("script"):
                    t = sc.string or sc.get_text()
                    if t and len(t.strip()) > 10:
                        log.debug(f"    [SCRIPT] {t[:200]}...")

        with self._lock:
            self.uri_cache[uri] = unique
        return unique

    def _extract_data_attrs(self, soup, cfg):
        """Parse per-element untuk pairing yang benar."""
        result = []
        for el in soup.find_all(attrs={"data-pldm": True}):
            pldm = el.get("data-pldm", "")
            play = el.get("data-play", "")
            if not pldm or not play:
                continue
            result.append({
                "pldm": pldm,
                "play": play,
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
        """Ambil SEMUA window['...'] = '...' vars."""
        result = []
        # Pattern 1: window['xxx'] = 'base64string'
        all_matches = re.findall(
            r"window\[['\"]([A-Za-z0-9_\-]+)['\"]\]\s*=\s*['\"]([^'\"]+)['\"]",
            html
        )
        # Pattern 2: window.xxx = 'base64string'
        all_matches2 = re.findall(
            r"window\.([A-Za-z0-9_\-]+)\s*=\s*['\"]([^'\"]{20,})['\"]",
            html
        )
        # Pattern 3: var xxx = 'base64string' (long strings)
        all_matches3 = re.findall(
            r"(?:var|let|const)\s+([A-Za-z0-9_\-]+)\s*=\s*['\"]([^'\"]{50,})['\"]",
            html
        )

        seen_vars = set()
        for var_name, win_val in all_matches + all_matches2 + all_matches3:
            if var_name in seen_vars:
                continue
            seen_vars.add(var_name)

            if len(win_val) < 20:
                continue

            try:
                decoded = base64.b64decode(win_val)
                text = decoded.decode("utf-8", errors="ignore")
            except Exception:
                text = win_val

            parsed = self._parse_decoded_player(text, html, cfg)
            result.extend(parsed)

        return result

    def _extract_iframe_players(self, soup, cfg):
        """Ekstrak player dari iframe."""
        result = []
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "") or iframe.get("data-src", "")
            if not src:
                continue
            parsed = urlparse(src if "://" in src else f"https://{src}")
            if parsed.netloc and "vipbox" not in parsed.netloc:
                result.append({
                    "pldm": parsed.netloc,
                    "play": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                    "pid": "6",
                    "player": "iframe",
                    "gc": "motorsports",
                    "csrf": cfg.get("csrf", ""),
                    "csrf_ip": cfg.get("csrf_ip", ""),
                    "sec_expires": cfg.get("sec_expires", ""),
                    "sec_hash": cfg.get("sec_hash", ""),
                })
        return result

    def _extract_direct_urls(self, html):
        """Cari URL stream langsung di HTML."""
        result = []
        # Cari semua URL yang terlihat seperti stream
        patterns = [
            r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv|ts)[^\s"\'<>\\]*)',
            r'(https?://[^\s"\'<>\\]*/live/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/hls/[^\s"\'<>\\]+)',
            r'(https?://[^\s"\'<>\\]*/stream/[^\s"\'<>\\]+)',
        ]
        seen = set()
        for pat in patterns:
            for m in re.findall(pat, html):
                if m in seen:
                    continue
                seen.add(m)
                parsed = urlparse(m)
                domain = parsed.netloc if parsed.netloc else "vipbox.ga"
                result.append({
                    "pldm": domain,
                    "play": m,
                    "pid": "6",
                    "player": "direct",
                    "gc": "motorsports",
                    "csrf": "",
                    "csrf_ip": "",
                    "sec_expires": "",
                    "sec_hash": "",
                    "_direct_url": m,
                })
        return result

    def _extract_data_url_attrs(self, soup, cfg):
        """Cari data-src, data-url, data-href, data-stream attributes."""
        result = []
        url_attrs = ["data-src", "data-url", "data-href", "data-stream",
                     "data-video", "data-file"]
        for attr in url_attrs:
            for el in soup.find_all(attrs={attr: True}):
                url = el.get(attr, "")
                if not url or not url.startswith("http"):
                    continue
                parsed = urlparse(url)
                if parsed.netloc:
                    result.append({
                        "pldm": parsed.netloc,
                        "play": url,
                        "pid": "6",
                        "player": "data_attr",
                        "gc": "motorsports",
                        "csrf": cfg.get("csrf", ""),
                        "csrf_ip": cfg.get("csrf_ip", ""),
                        "sec_expires": cfg.get("sec_expires", ""),
                        "sec_hash": cfg.get("sec_hash", ""),
                        "_direct_url": url,
                    })
        return result

    def _extract_fetch_urls(self, html, cfg):
        """Cari URL dari fetch()/axios patterns di JavaScript."""
        result = []
        # Cari fetch('url') atau fetch("url")
        fetch_urls = re.findall(
            r"""(?:fetch|axios\.(?:get|post))\(\s*['"]([^'"]+)['"]""",
            html
        )
        for url in fetch_urls:
            if any(ext in url for ext in (".m3u8", ".mpd", "/live/", "/hls/")):
                parsed = urlparse(url if "://" in url else f"https://{url}")
                domain = parsed.netloc if parsed.netloc else "vipbox.ga"
                result.append({
                    "pldm": domain,
                    "play": url,
                    "pid": "6",
                    "player": "fetch",
                    "gc": "motorsports",
                    "csrf": cfg.get("csrf", ""),
                    "csrf_ip": cfg.get("csrf_ip", ""),
                    "sec_expires": cfg.get("sec_expires", ""),
                    "sec_hash": cfg.get("sec_hash", ""),
                    "_direct_url": url,
                })
        return result

    def _parse_decoded_player(self, text, html, cfg):
        """Parse decoded player data - handle dict, list, nested."""
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
                if "players" in data and isinstance(data["players"], list):
                    for item in data["players"]:
                        if isinstance(item, dict):
                            p = self._dict_to_params(item, cfg)
                            if p:
                                result.append(p)
                elif "sources" in data and isinstance(data["sources"], list):
                    for item in data["sources"]:
                        if isinstance(item, dict):
                            p = self._dict_to_params(item, cfg)
                            if p:
                                result.append(p)
                else:
                    p = self._dict_to_params(data, cfg)
                    if p:
                        result.append(p)
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: cari URL stream di text
        if not result:
            urls = re.findall(
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|mpd|flv)[^\s"\'<>\\]*)',
                text
            )
            for stream_url in urls:
                parsed = urlparse(stream_url)
                domain = parsed.netloc if parsed.netloc else "vipbox.ga"
                result.append({
                    "pldm": domain,
                    "play": stream_url,
                    "pid": "6",
                    "player": "direct",
                    "gc": "motorsports",
                    "csrf": "",
                    "csrf_ip": "",
                    "sec_expires": "",
                    "sec_hash": "",
                    "_direct_url": stream_url,
                })

        return result

    def _dict_to_params(self, data, cfg):
        """Konversi dict player config ke params."""
        pldm = (data.get("pldm", "") or data.get("domain", "") or
                data.get("server", "") or data.get("host", ""))
        play = (data.get("play", "") or data.get("vimg", "") or
                data.get("source", "") or data.get("file", "") or
                data.get("url", ""))
        if not pldm or not play:
            return None
        return {
            "pldm": pldm,
            "play": play,
            "pid": str(data.get("pid", "6")),
            "player": data.get("player", "jw"),
            "gc": data.get("gc", "motorsports"),
            "csrf": data.get("csrf", cfg.get("csrf", "")),
            "csrf_ip": data.get("csrf_ip", cfg.get("csrf_ip", "")),
            "sec_expires": data.get("sec_expires", cfg.get("sec_expires", "")),
            "sec_hash": data.get("sec_hash", cfg.get("sec_hash", "")),
        }

    # ── 3. POST KE PLAYER ────────────────────────────────
    def get_stream_from_player(self, params):
        """Return list of (url, domain, drm_key)."""
        if params.get("_direct_url"):
            url = params["_direct_url"]
            parsed = urlparse(url)
            domain = parsed.netloc if parsed.netloc else params["pldm"]
            return [(url, domain, None)]

        pldm = params["pldm"]
        gc = params["gc"]
        cache_key = (pldm, params["play"])

        with self._lock:
            if cache_key in self.play_cache:
                return self.play_cache[cache_key]

        payload_dict = {
            "pid": params["pid"],
            "vimg": params["play"],
            "player": params["player"],
            "csrf": params["csrf"],
            "csrf_ip": params["csrf_ip"],
            "expires": params["sec_expires"],
            "sec_hash": params["sec_hash"],
        }
        payload_b64 = base64.b64encode(
            json.dumps(payload_dict, separators=(",", ":")).encode()
        ).decode()

        # FIX: Coba beberapa endpoint patterns
        post_urls = [
            f"https://{pldm}/vimpl/{gc}",
            f"https://{pldm}/vimpl",
            f"https://{pldm}/player/{gc}",
            f"https://{pldm}/api/play",
        ]

        stream_urls = []
        domain = pldm
        drm_key = None

        for post_url in post_urls:
            if stream_urls:
                break

            for attempt in range(MAX_RETRIES):
                try:
                    r = self.s.post(
                        post_url,
                        data={"payload": payload_b64},
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
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
                    debug_save(
                        f"post_{pldm}_{gc}_attempt{attempt+1}.html",
                        f"URL: {post_url}\nStatus: {r.status_code}\n\n{html}"
                    )
                    log.debug(f"    POST {post_url} → {r.status_code}, "
                              f"{len(html)}B")

                    # ── Ekstrak dari STUDY_CONFIG ──
                    cfg = extract_json_block(html, "STUDY_CONFIG")
                    if not cfg:
                        # FIX: Coba nama config lain
                        for alt_name in ("PLAYER_CONFIG", "playerConfig",
                                         "JWPLAYER_CONFIG", "jwConfig",
                                         "videoConfig", "VIDEO_CONFIG"):
                            cfg = extract_json_block(html, alt_name)
                            if cfg:
                                break

                    if cfg:
                        stream_urls.extend(
                            self._extract_streams_from_config(cfg, pldm)
                        )
                        drm_key = self._extract_drm(cfg)

                    # ── Fallback: regex di HTML ──
                    if not stream_urls:
                        stream_urls.extend(
                            self._extract_streams_from_html(html, pldm)
                        )

                    if stream_urls:
                        break
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(1)

                except Exception as ex:
                    log.debug(f"    POST fail {post_url} "
                              f"(attempt {attempt+1}): {ex}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(1)

        # ── FIX: Jika masih kosong, coba GET langsung ke pldm ──
        if not stream_urls:
            stream_urls = self._try_direct_get(pldm, params)

        with self._lock:
            self.play_cache[cache_key] = stream_urls
        return stream_urls

    def _extract_streams_from_config(self, cfg, pldm):
        """Ekstrak semua stream URL dari config object."""
        results = []
        source = cfg.get("source", cfg.get("sources", cfg.get("file", "")))

        # source bisa dict, list, atau string
        if isinstance(source, str) and source.startswith("http"):
            parsed = urlparse(source)
            d = parsed.netloc if parsed.netloc else pldm
            results.append((source, d, None))
        elif isinstance(source, dict):
            for key in ("hls", "dash", "url", "file", "src", "mpd", "m3u8"):
                if key in source and source[key]:
                    su = source[key]
                    if isinstance(su, str) and su.startswith("http"):
                        parsed = urlparse(su)
                        d = parsed.netloc if parsed.netloc else pldm
                        results.append((su, d, None))
        elif isinstance(source, list):
            for item in source:
                if isinstance(item, str) and item.startswith("http"):
                    parsed = urlparse(item)
                    d = parsed.netloc if parsed.netloc else pldm
                    results.append((item, d, None))
                elif isinstance(item, dict):
                    for key in ("src", "file", "url", "hls", "dash"):
                        if key in item and item[key]:
                            su = item[key]
                            if isinstance(su, str) and su.startswith("http"):
                                parsed = urlparse(su)
                                d = parsed.netloc if parsed.netloc else pldm
                                results.append((su, d, None))

        # Cek juga di level atas config
        for key in ("hls", "dash", "url", "file", "src", "stream_url"):
            if key in cfg and isinstance(cfg[key], str) and cfg[key].startswith("http"):
                su = cfg[key]
                parsed = urlparse(su)
                d = parsed.netloc if parsed.netloc else pldm
                results.append((su, d, None))

        return results

    def _extract_drm(self, cfg):
        """Ekstrak DRM key dari config."""
        source = cfg.get("source", {})
        if isinstance(source, dict):
            drm = source.get("drm", cfg.get("drm", {}))
            if drm:
                ck = drm.get("clearkey", [])
                if ck and isinstance(ck, list):
                    return f"{ck[0]['kid']}:{ck[0]['key']}"
        return None

    def _extract_streams_from_html(self, html, pldm):
        """Regex fallback untuk cari stream URL di HTML."""
        results = []
        patterns = [
            r'"(https?://[^"]+\.(?:m3u8|mpd|flv)[^"]*)"',
            r"'(https?://[^']+\.(?:m3u8|mpd|flv)[^']*)'",
            r'"(https?://[^"]*/live/[^"]+)"',
            r"'(https?://[^']*/live/[^']+)'",
            r'"(https?://[^"]*/hls/[^"]+)"',
            r'"(https?://[^"]*/stream/[^"]+)"',
            r'src="(https?://[^"]+)"',
        ]
        seen = set()
        for pat in patterns:
            for m in re.findall(pat, html):
                if m in seen:
                    continue
                seen.add(m)
                parsed = urlparse(m)
                d = parsed.netloc if parsed.netloc else pldm
                results.append((m, d, None))
        return results

    def _try_direct_get(self, pldm, params):
        """Coba GET langsung ke player domain."""
        results = []
        # Coba beberapa path
        paths = [
            f"/{params['gc']}",
            f"/live/{params['gc']}",
            f"/embed/{params['gc']}",
            "/",
        ]
        for path in paths:
            try:
                url = f"https://{pldm}{path}"
                r = self.s.get(url, timeout=10,
                               headers={"Referer": f"{BASE_URL}/"})
                if r.status_code == 200 and len(r.text) > 100:
                    debug_save(f"direct_get_{pldm}_{path.strip('/')}.html",
                               r.text)
                    results = self._extract_streams_from_html(r.text, pldm)
                    if results:
                        break
            except Exception:
                continue
        return results

    # ── 4. RESOLVE ENTRY ─────────────────────────────────
    def resolve_entry(self, e):
        params_list = self.get_play_params(e)

        if not params_list and e.get("time") and \
                e["time"] <= datetime.now(WIB):
            log.info(f"    Retry {e['name']} (5s delay)")
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
        for params in params_list:
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

        if not e["videos"]:
            e["videos"] = [{
                "player_domain": "vipbox.ga",
                "resolved_url": f"{BASE_URL}{e['live_uri']}",
                "drm_key": None,
            }]
        return e

    # ── 5. GENERATE M3U ──────────────────────────────────
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
                    full_name = (f"{status} | {base_name} | "
                                 f"Video {vi} | {ts} WIB")
                else:
                    full_name = f"{status} | {base_name} | {ts} WIB"

                domain = v.get("player_domain", "vipbox.ga")
                origin = f"https://{domain}"
                referrer = f"{origin}/"
                final_url = v.get("resolved_url",
                                  f"{BASE_URL}{e['live_uri']}")

                if not_started or "vipbox.ga" in final_url:
                    final_url = fallback_url
                else:
                    resolved_count += 1
                total_videos += 1

                lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
                lines.append(f"#EXTVLCOPT:http-origin={origin}")
                lines.append(f"#EXTVLCOPT:http-referrer={referrer}")
                lines.append(
                    "#KODIPROP:inputstream.adaptive"
                    ".manifest_type=dash"
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

        log.info(f"\n  ✅ {OUTPUT_FILE} → {total_videos} stream")
        log.info(f"     Resolved: {resolved_count}/{total_videos}")
        log.info(f"     Fallback: {total_videos - resolved_count}/{total_videos}")

    # ── 6. DEBUG: Cek 1 channel saja ─────────────────────
    def debug_single(self):
        """Mode debug: ambil 1 channel 24/7 dan telusuri full chain."""
        log.info("=" * 50)
        log.info("  DEBUG MODE: Telusuri 1 channel")
        log.info("=" * 50)

        # Ambil livenow
        url = f"{BASE_URL}/livenow-schedule"
        r = self.s.get(url, timeout=TIMEOUT)
        debug_save("debug_livenow.html", r.text)
        soup = BeautifulSoup(r.text, "html.parser")
        cfg = self._extract_site_config(soup)
        if not cfg:
            log.error("No siteConfig found!")
            return

        log.info(f"  siteConfig keys: {list(cfg.keys())}")
        sched_data = cfg.get("scheduleData", "")
        token = cfg.get("fetck_token", "")

        post_url = f"{BASE_URL}/schedule/{sched_data}/"
        r2 = self.s.post(post_url, headers={
            "fetch-token": token,
            "Referer": url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        }, timeout=TIMEOUT)
        debug_save("debug_livenow_post.json", r2.text)
        data = r2.json()
        log.info(f"  POST response keys: {list(data.keys())}")

        events = self._parse_events(soup)
        if not events:
            log.error("No events parsed!")
            return

        # Ambil event pertama
        ev = events[0]
        log.info(f"\n  Event: {ev['title']}")
        log.info(f"  ID: {ev['id']}")
        log.info(f"  onair_uri: {ev['onair_uri']}")
        log.info(f"  gc: {ev['gc']}")

        # Build entry
        links_map = data.get("links", {})
        slugs = data.get("slugs", {})
        link_appends = data.get("linkAppends", {})
        ev_id = ev["id"]
        slug = slugs.get(ev_id, ev_id)
        append_gc = link_appends.get(ev_id, ev["gc"])
        live_uri = f"/live/{append_gc}/{slug}-1"

        entry = {
            "name": ev["title"],
            "group": "Debug",
            "logo": ev["logo"],
            "time": ev["time"],
            "player": "?",
            "lang": "",
            "hd": 0,
            "live_uri": live_uri,
            "onair_uri": ev["onair_uri"],
            "eid": ev_id,
            "link_no": 1,
            "gc": append_gc,
        }

        # Extract params
        log.info(f"\n  --- Extracting from onair_uri: {ev['onair_uri']} ---")
        params = self.get_play_params(entry)
        log.info(f"  Params found: {len(params)}")
        for i, p in enumerate(params):
            log.info(f"    [{i}] pldm={p['pldm']}, play={p['play'][:80]}")

        if params:
            log.info(f"\n  --- Resolving stream from player ---")
            for i, p in enumerate(params):
                results = self.get_stream_from_player(p)
                log.info(f"    [{i}] → {len(results)} stream URLs:")
                for url, domain, drm in results:
                    log.info(f"       {url[:100]}")

        log.info(f"\n  Debug output di: {os.path.abspath(DEBUG_DIR)}/")
        log.info("  Cek file HTML untuk lihat struktur player page.")

    # ── 7. MAIN ──────────────────────────────────────────
    def run(self):
        if DEBUG:
            self.debug_single()
            return

        log.info("=" * 50)
        log.info("  VIPBox M3U Generator v8.0")
        log.info(f"  Target : {BASE_URL}")
        log.info(f"  Output : {OUTPUT_FILE}")
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

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
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
                        "resolved_url": f"{BASE_URL}{e['live_uri']}",
                        "drm_key": None,
                    }]
                    log.warning(f"    fail: {ex}")

        self.write_m3u(all_entries)


if __name__ == "__main__":
    VIPBox().run()   
