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
    "american-football": "NFL",
    "others": "Others", "other-sports": "Others",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("VIPBox")


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

            # ── PAKAI SLUG + LINK_APPENDS ──
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
            "nascar": "nfl", "formula-1": "motorsports",
            "motogp": "motorsports", "college-football": "ncaaf",
            "soccer": "football", "other-sports": "other-sports",
            "college-basketball": "basketball",
            "nba": "basketball", "nfl": "nfl",
            "davis-cup": "tennis", "khl": "hockey",
            "worldcup": "football", "bahrain": "football",
            "qatar": "football", "romania": "football",
            "serbia": "football", "slovenia": "football",
            "bulgaria": "football", "russia": "hockey",
            "czech-republic": "hockey", "finland": "hockey",
            "australia": "basketball", "south-korea": "basketball",
            "italy": "basketball", "television": "nfl",
            "nhl": "hockey",
        }
        key = m.get(icon_cls, icon_cls) if icon_cls else "football"
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── 2. EKSTRAK PARAM VIDEO (OBFUSCATED) ──────────────
    def get_play_params(self, e):
        with self._lock:
            if e["eid"] in self.onair_cache:
                return self.onair_cache[e["eid"]]

        params_list = None
        if e["onair_uri"]:
            params_list = self._extract_all_from_page(e["onair_uri"])
            if params_list:
                with self._lock:
                    self.onair_cache[e["eid"]] = params_list
                return params_list

        params_list = self._extract_all_from_page(e["live_uri"])
        if params_list:
            with self._lock:
                self.onair_cache[e["eid"]] = params_list
        return params_list

    def _extract_all_from_page(self, uri):
        with self._lock:
            if uri in self.uri_cache:
                return self.uri_cache[uri]

        url = f"{BASE_URL}{uri}"
        result = []
        try:
            r = self.s.get(url, timeout=TIMEOUT,
                           headers={"Referer": f"{BASE_URL}/"})
            html = r.text
            log.info(f"    GET {uri} → {r.status_code}, {len(html)}B")

            # ── Coba data-* attributes ──
            all_pldm = re.findall(r'data-pldm="([^"]+)"', html)
            all_play = re.findall(r'data-play="([^"]+)"', html)

            if all_pldm and all_play:
                all_pid = re.findall(r'data-pid="([^"]+)"', html)
                all_player = re.findall(r'data-player="([^"]+)"', html)
                all_gc = re.findall(r'data-gc="([^"]+)"', html)
                max_videos = max(len(all_pldm), len(all_play), 1)
                soup = BeautifulSoup(html, "html.parser")
                cfg = self._extract_site_config(soup) or {}
                for i in range(max_videos):
                    pldm = all_pldm[i] if i < len(all_pldm) else ""
                    play = all_play[i] if i < len(all_play) else ""
                    if not pldm or not play:
                        continue
                    result.append({
                        "pldm": pldm,
                        "play": play,
                        "pid": all_pid[i] if i < len(all_pid) else "6",
                        "player": all_player[i] if i < len(all_player) else "jw",
                        "gc": all_gc[i] if i < len(all_gc) else "motorsports",
                        "csrf": cfg.get("csrf", ""),
                        "csrf_ip": cfg.get("csrf_ip", ""),
                        "sec_expires": cfg.get("sec_expires", ""),
                        "sec_hash": cfg.get("sec_hash", ""),
                    })
                if result:
                    with self._lock:
                        self.uri_cache[uri] = result
                    return result

            # ── Ekstrak window['...'] obfuscated variable ──
            m = re.search(
                r"window\[['\"]([A-Za-z0-9_\-]+)['\"]\]\s*=\s*['\"]([^'\"]+)['\"]",
                html
            )
            if not m:
                log.warning(f"    No player data in {uri}")
                with self._lock:
                    self.uri_cache[uri] = result
                return result

            win_val = m.group(2)
            log.info(f"    window var len={len(win_val)}")

            # ── Decode base64 ──
            try:
                decoded = base64.b64decode(win_val)
            except Exception:
                decoded = win_val.encode()

            # ── Parse decoded content ──
            result = self._parse_decoded_player(decoded, win_val, html)

        except Exception as ex:
            log.debug(f"    GET fail {uri}: {ex}")

        with self._lock:
            self.uri_cache[uri] = result
        return result

    def _parse_decoded_player(self, decoded_bytes, raw_str, html):
        result = []
        text = decoded_bytes.decode("utf-8", errors="ignore")

        # ── Coba JSON ──
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                pldm = data.get("pldm", "") or data.get("domain", "")
                play = data.get("play", "") or data.get("vimg", "")
                if pldm and play:
                    result.append({
                        "pldm": pldm,
                        "play": play,
                        "pid": data.get("pid", "6"),
                        "player": data.get("player", "jw"),
                        "gc": data.get("gc", "motorsports"),
                        "csrf": data.get("csrf", ""),
                        "csrf_ip": data.get("csrf_ip", ""),
                        "sec_expires": data.get("sec_expires", ""),
                        "sec_hash": data.get("sec_hash", ""),
                    })
        except Exception:
            pass

        # ── Coba parse sebagai URL langsung ──
        if not result:
            url_m = re.search(
                r'(https?://[^\s"\'<>]+\.(?:mpd|m3u8|flv)[^\s"\'<>]*)',
                text
            )
            if url_m:
                stream_url = url_m.group(1)
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

        # ── Fallback: regex di raw HTML ──
        if not result:
            url_m = re.search(
                r'(https?://[^\s"\'<>]+\.(?:mpd|m3u8|flv)[^\s"\'<>]*)',
                html
            )
            if url_m:
                stream_url = url_m.group(1)
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

    # ── 3. POST KE PLAYER ────────────────────────────────
    def get_stream_from_player(self, params):
        if params.get("_direct_url"):
            url = params["_direct_url"]
            parsed = urlparse(url)
            domain = parsed.netloc if parsed.netloc else params["pldm"]
            return (url, domain, None)

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
        params_list = self.get_play_params(e)

        # Retry 1x kalau event live tapi params kosong
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
        for params in params_list:
            stream_url, domain, drm_key = \
                self.get_stream_from_player(params)
            e["videos"].append({
                "player_domain": domain or params["pldm"],
                "resolved_url": stream_url or f"{BASE_URL}{e['live_uri']}",
                "drm_key": drm_key,
            })
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

    # ── 6. MAIN ──────────────────────────────────────────
    def run(self):
        log.info("=" * 50)
        log.info("  VIPBox M3U Generator v7.4")
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
