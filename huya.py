

#!/usr/bin/env python3

from curl_cffi import requests as cffi_requests
import re
import json
import base64
import logging
import time
import threading
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 30
MAX_WORKERS = 4
IMPERSONATE = "chrome"

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("VIPBox")


def extract_json_block(text, var_name):
    """Extract JSON object assigned to a JS variable using brace counting."""
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

        soup = BeautifulSoup(r.text, "html.parser")
        cfg = self._extract_site_config(soup)
        if not cfg:
            log.warning("    No siteConfig")
            return []

        sched_data = cfg.get("scheduleData", "")
        token = cfg.get("fetck_token", "")
        time_cls = cfg.get("timeCls", "vt_m0k0o3u5d1")

        post_url = f"{BASE_URL}/schedule/{sched_data}/"
        post_hdrs = {
            "fetch-token": token,
            "Referer": url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        }
        try:
            r2 = self.s.post(post_url, headers=post_hdrs, timeout=TIMEOUT)
            data = r2.json()
        except Exception as e:
            log.error(f"    POST FAIL: {e}")
            data = {"links": {}, "linkAppends": {}, "slugs": {}}

        events = self._parse_events(soup, time_cls)
        log.info(f"    {len(events)} events")

        results = []
        appends = data.get("linkAppends", {})
        links_map = data.get("links", {})
        slugs_map = data.get("slugs", {})

        for ev in events:
            eid = ev["id"]
            append = appends.get(eid, slug)
            ev_links = links_map.get(eid, [])
            if not ev_links:
                ev_links = [{"player": "?", "lang": "", "hd": 0}]
            ev_slug = slugs_map.get(eid, "")

            for i, lk in enumerate(ev_links, 1):
                live_uri = f"/live/{append}/{eid}-{i}"
                onair_uri = f"/onair/{append}/{ev_slug}" if ev_slug else ""
                results.append({
                    "name": ev["title"],
                    "group": name,
                    "logo": ev["logo"],
                    "time": ev["time"],
                    "player": lk.get("player", "?"),
                    "lang": lk.get("lang", ""),
                    "hd": lk.get("hd", 0),
                    "live_uri": live_uri,
                    "onair_uri": onair_uri,
                    "eid": eid,
                    "link_no": i,
                    "gc": append,
                })
        return results

    def _extract_site_config(self, soup):
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                return extract_json_block(t, "siteConfig")
        return None

    def _parse_events(self, soup, time_cls):
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
            t_el = d.find("time")
            etime = None
            if t_el and t_el.get("content"):
                try:
                    etime = datetime.fromisoformat(t_el["content"])
                except:
                    pass
            icon = d.find("i", class_=re.compile(r"vip-box-icon"))
            icon_cls = ""
            if icon:
                for c in icon.get("class", []):
                    if c not in ("align-bottom", "me-2", "vip-box-icon"):
                        icon_cls = c.strip()
                        break
            events.append({
                "title": title, "id": eid,
                "time": etime,
                "logo": self._icon_to_logo(icon_cls),
            })
        return events

    def _icon_to_logo(self, icon_cls):
        m = {
            "nascar": "nfl", "formula-1": "motorsports",
            "motogp": "motorsports", "college-football": "ncaaf",
            "soccer": "football", "other-sports": "other-sports",
        }
        key = m.get(icon_cls, icon_cls) if icon_cls else "football"
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── 2. EKSTRAK PARAMS DARI HALAMAN ONAIR ─────────────
    def get_play_params(self, e):
        with self._lock:
            if e["eid"] in self.onair_cache:
                return self.onair_cache[e["eid"]]

        params = None
        if e["onair_uri"]:
            params = self._extract_from_page(e["onair_uri"])
            if params:
                with self._lock:
                    self.onair_cache[e["eid"]] = params
                return params

        params = self._extract_from_page(e["live_uri"])
        if params:
            with self._lock:
                self.onair_cache[e["eid"]] = params
        return params

    def _extract_from_page(self, uri):
        with self._lock:
            if uri in self.uri_cache:
                return self.uri_cache[uri]

        url = f"{BASE_URL}{uri}"
        result = None
        try:
            r = self.s.get(url, timeout=TIMEOUT,
                           headers={"Referer": f"{BASE_URL}/"})
            html = r.text

            pldm = re.search(r'data-pldm="([^"]+)"', html)
            play = re.search(r'data-play="([^"]+)"', html)
            pid = re.search(r'data-pid="([^"]+)"', html)
            player = re.search(r'data-player="([^"]+)"', html)
            gc = re.search(r'data-gc="([^"]+)"', html)

            if pldm and play:
                soup = BeautifulSoup(html, "html.parser")
                cfg = self._extract_site_config(soup) or {}

                result = {
                    "pldm": pldm.group(1),
                    "play": play.group(1),
                    "pid": pid.group(1) if pid else "6",
                    "player": player.group(1) if player else "jw",
                    "gc": gc.group(1) if gc else "motorsports",
                    "csrf": cfg.get("csrf", ""),
                    "csrf_ip": cfg.get("csrf_ip", ""),
                    "sec_expires": cfg.get("sec_expires", ""),
                    "sec_hash": cfg.get("sec_hash", ""),
                }
        except Exception as ex:
            log.debug(f"    GET fail {uri}: {ex}")

        with self._lock:
            self.uri_cache[uri] = result
        return result

    # ── 3. POST KE PLAYER ────────────────────────────────
    def get_stream_from_player(self, params):
        pldm = params["pldm"]
        gc = params["gc"]
        cache_key = (pldm, params["play"])

        with self._lock:
            if cache_key in self.play_cache:
                return self.play_cache[cache_key]

        # Rakit payload JSON lalu base64-encode
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

        post_url = f"https://{pldm}/vimpl/{gc}"
        stream_url = None
        domain = pldm
        drm_key = None

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
            )
            html = r.text
            log.debug(f"    POST {post_url} → {r.status_code}, "
                      f"{len(html)}B")

            if "STUDY_CONFIG" not in html:
                log.warning(
                    f"    No STUDY_CONFIG! "
                    f"First 200: {html[:200]}"
                )

            # Parse STUDY_CONFIG
            cfg = extract_json_block(html, "STUDY_CONFIG")
            if cfg:
                source = cfg.get("source", {})
                if "hls" in source:
                    stream_url = source["hls"]
                elif "dash" in source:
                    stream_url = source["dash"]
                elif "url" in source:
                    stream_url = source["url"]

                drm = source.get("drm", {})
                if drm:
                    ck = drm.get("clearkey", [])
                    if ck:
                        drm_key = f"{ck[0]['kid']}:{ck[0]['key']}"

                if stream_url:
                    parsed = urlparse(stream_url)
                    if parsed.netloc:
                        domain = parsed.netloc

            # Fallback regex
            if not stream_url:
                pats = [
                    r'"(https?://[^"]+\.(?:m3u8|mpd|flv)[^"]*)"',
                    r'"(https?://[^"]*/live/[^"]+)"',
                ]
                for p in pats:
                    m2 = re.search(p, html)
                    if m2:
                        stream_url = m2.group(1)
                        parsed = urlparse(stream_url)
                        if parsed.netloc:
                            domain = parsed.netloc
                        break

        except Exception as ex:
            log.warning(f"    POST fail {pldm}: {ex}")

        result = (stream_url, domain, drm_key)
        with self._lock:
            self.play_cache[cache_key] = result
        return result

    # ── 4. RESOLVE ENTRY ─────────────────────────────────
    def resolve_entry(self, e):
        params = self.get_play_params(e)

        if not params:
            e["player_domain"] = "vipbox.ga"
            e["resolved_url"] = f"{BASE_URL}{e['live_uri']}"
            e["drm_key"] = None
            return e

        stream_url, domain, drm_key = self.get_stream_from_player(params)

        e["player_domain"] = domain or params["pldm"]
        e["resolved_url"] = stream_url or f"{BASE_URL}{e['live_uri']}"
        e["drm_key"] = drm_key
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
        FALLBACK_URL = "https://0.now1.tv/CC///CC.php"
        ua = HEADERS["User-Agent"]
        resolved_count = 0

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
            else:
                ts = "LIVE 24/7"

            full_name = f"{base_name} | {ts} WIB"
            domain = e.get("player_domain", "vipbox.ga")
            origin = f"https://{domain}"
            referrer = f"https://{domain}/"
            final_url = e.get("resolved_url", f"{BASE_URL}{e['live_uri']}")

            if "vipbox.ga/live/" not in final_url:
                resolved_count += 1

            drm = e.get("drm_key", "")

            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            lines.append(f"#EXTVLCOPT:http-origin={origin}")
            lines.append(f"#EXTVLCOPT:http-referrer={referrer}")
            lines.append(
                "#KODIPROP:inputstream.adaptive.manifest_type=dash"
            )
            lines.append(
                "#KODIPROP:inputstream.adaptive.license_type=org.w3.clearkey"
            )
            lines.append(
                f"#KODIPROP:inputstream.adaptive.license_key={drm}"
            )
            lines.append(
                f'#EXTINF:-1 '
                f'tvg-id="{e["group"].lower()}" '
                f'tvg-name="{full_name}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",{full_name}'
            )
            lines.append(final_url)
            lines.append(FALLBACK_URL)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"\n  ✅ {OUTPUT_FILE} → {len(unique)} channel")
        log.info(f"     Resolved: {resolved_count}/{len(unique)}")   
