#!/usr/bin/env python3
"""
VIPBox M3U Playlist Generator v3 (FIXED)
"""

import requests
import re
import json
import logging
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ═══════════════════════════════════════════════════════
BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 25
MAX_WORKERS = 4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="158", "Not.A/Brand";v="24", "Brave";v="158"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
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


class VIPBox:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.play_cache = {}
        self.uri_cache = {}

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
        cfg = self._extract_config(soup)
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

    def _extract_config(self, soup):
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                m = re.search(
                    r"const\s+siteConfig\s*=\s*(\{.+\})\s*;",
                    t, re.DOTALL
                )
                if m:
                    try:
                        return json.loads(m.group(1))
                    except:
                        pass
        return None

    def _parse_events(self, soup, time_cls):
        events = []
        for d in soup.find_all("details", attrs={"vt-data-events-row": True}):
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
                        icon_cls = c
                        break

            events.append({
                "title": title,
                "id": eid,
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

    # ── 2. EKSTRAK data-play DARI HALAMAN ────────────────
    def extract_play_params(self, uri):
        """GET /live/... → data-play, data-pldm, data-gc"""
        if uri in self.uri_cache:
            return self.uri_cache[uri]

        url = f"{BASE_URL}{uri}"
        result = None
        try:
            r = self.s.get(url, timeout=TIMEOUT,
                           headers={"Referer": f"{BASE_URL}/"})
            html = r.text

            pldm = re.search(r'data-pldm="([^"]+)"', html)
            if not pldm:
                pldm = re.search(r"data-pldm='([^']+)'", html)

            play = re.search(r'data-play="([^"]+)"', html)
            if not play:
                play = re.search(r"data-play='([^']+)'", html)

            gc = re.search(r'data-gc="([^"]+)"', html)
            if not gc:
                gc = re.search(r"data-gc='([^']+)'", html)

            if pldm and play:
                result = {
                    "pldm": pldm.group(1),
                    "play": play.group(1),
                    "gc": gc.group(1) if gc else "motorsports",
                }

        except Exception as e:
            log.debug(f"    extract fail {uri}: {e}")

        self.uri_cache[uri] = result
        return result

    # ── 3. POST KE PLAYER → STREAM URL ───────────────────
    def get_stream_from_player(self, pldm, data_play, gc):
        """POST {pldm}/vimpl/{gc} → STUDY_CONFIG → mpd/m3u8"""
        cache_key = (pldm, data_play)
        if cache_key in self.play_cache:
            return self.play_cache[cache_key]

        post_url = f"https://{pldm}/vimpl/{gc}"
        stream_url = None
        domain = pldm
        drm_key = None

        try:
            r = self.s.post(
                post_url,
                data={"payload": data_play},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": BASE_URL,
                    "Referer": f"{BASE_URL}/",
                    "User-Agent": HEADERS["User-Agent"],
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "iframe",
                },
                timeout=TIMEOUT,
            )
            html = r.text

            # Parse STUDY_CONFIG
            m = re.search(
                r'window\.STUDY_CONFIG\s*=\s*(\{.+\})\s*;',
                html, re.DOTALL
            )
            if m:
                try:
                    cfg = json.loads(m.group(1))
                    source = cfg.get("source", {})

                    if "hls" in source:
                        stream_url = source["hls"]
                    elif "dash" in source:
                        stream_url = source["dash"]
                    elif "url" in source:
                        stream_url = source["url"]

                    # DRM clearkey
                    drm = source.get("drm", {})
                    if drm:
                        ck = drm.get("clearkey", [])
                        if ck:
                            drm_key = f"{ck[0]['kid']}:{ck[0]['key']}"

                    if stream_url:
                        parsed = urlparse(stream_url)
                        if parsed.netloc:
                            domain = parsed.netloc

                except json.JSONDecodeError:
                    pass

            # Fallback regex
            if not stream_url:
                m2 = re.search(
                    r'"(https?://[^"]+\.(?:m3u8|mpd|flv))"', html
                )
                if m2:
                    stream_url = m2.group(1)
                    parsed = urlparse(stream_url)
                    if parsed.netloc:
                        domain = parsed.netloc

        except Exception as e:
            log.debug(f"    player POST fail {pldm}: {e}")

        result = (stream_url, domain, drm_key)
        self.play_cache[cache_key] = result
        return result

    # ── 4. RESOLVE ENTRY ─────────────────────────────────
    def resolve_entry(self, e):
        params = self.extract_play_params(e["live_uri"])
        if not params and e["onair_uri"]:
            params = self.extract_play_params(e["onair_uri"])

        if not params:
            e["player_domain"] = "vipbox.ga"
            e["resolved_url"] = f"{BASE_URL}{e['live_uri']}"
            e["drm_key"] = None
            return e

        pldm = params["pldm"]
        data_play = params["play"]
        gc = params.get("gc", e["gc"])

        stream_url, domain, drm_key = self.get_stream_from_player(
            pldm, data_play, gc
        )

        e["player_domain"] = domain or pldm
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
        ua = HEADERS["User-Agent"]

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
            referrer = f"https://{domain}/"

            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            lines.append(f"#EXTVLCOPT:http-referrer={referrer}")

            # DRM clearkey (jika ada)
            drm = e.get("drm_key")
            if drm:
                lines.append(
                    f"#EXTVLCOPT:input-drm-clearkey={drm}"
                )

            lines.append(
                f'#EXTINF:-1 '
                f'tvg-id="{e["group"].lower()}" '
                f'tvg-name="{full_name}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",{full_name}'
            )
            lines.append(e.get("resolved_url", f"{BASE_URL}{e['live_uri']}"))

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"\n  ✅ {OUTPUT_FILE} → {len(unique)} channel")

    # ── 6. MAIN ──────────────────────────────────────────
    def run(self):
        log.info("=" * 50)
        log.info("  VIPBox M3U Generator v3")
        log.info(f"  Target : {BASE_URL}")
        log.info(f"  Output : {OUTPUT_FILE}")
        log.info("=" * 50)

        all_entries = []
        for slug, name in CATEGORIES:
            entries = self.fetch_category(slug, name)
            log.info(f"  {name}: {len(entries)} streams")
            all_entries.extend(entries)
            time.sleep(0.3)

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
                    e["player_domain"] = "vipbox.ga"
                    e["resolved_url"] = f"{BASE_URL}{e['live_uri']}"
                    e["drm_key"] = None
                    log.warning(f"    fail: {ex}")

        self.write_m3u(all_entries)


if __name__ == "__main__":
    VIPBox().run()   
