#!/usr/bin/env python3
"""VIPBox M3U Generator - Fixed"""

import requests
import re
import json
import logging
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 20

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("VIPBox")


class VIPBox:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        # Cache: player_name → (domain, ua, referrer)
        self.player_cache = {}
        # Cache: stream_url → list of m3u8/flv/mpd
        self.stream_cache = {}

    # ── Fetch schedule per kategori ─────────────────────
    def fetch_category(self, slug, name):
        url = f"{BASE_URL}/{slug}-schedule"
        log.info(f"GET {url}")
        try:
            r = self.s.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log.error(f"  FAIL: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        # siteConfig
        cfg = self._get_config(soup)
        if not cfg:
            log.warning("  No siteConfig")
            return []

        sched_data = cfg.get("scheduleData", "")
        token = cfg.get("fetck_token", "")
        time_cls = cfg.get("timeCls", "")

        # POST schedule
        post_url = f"{BASE_URL}/schedule/{sched_data}/"
        hdrs = {"fetch-token": token, "Referer": url, "Origin": BASE_URL}
        try:
            r2 = self.s.post(post_url, headers=hdrs, timeout=TIMEOUT)
            data = r2.json()
        except Exception as e:
            log.error(f"  POST FAIL: {e}")
            data = {"links": {}, "linkAppends": {}, "slugs": {}}

        # Parse events dari HTML
        events = self._parse_events(soup, time_cls)

        # Merge
        results = []
        slugs_map = data.get("slugs", {})
        appends = data.get("linkAppends", {})
        links_map = data.get("links", {})

        for ev in events:
            eid = ev["id"]
            append = appends.get(eid, slug)
            ev_links = links_map.get(eid, [])

            if not ev_links:
                ev_links = [{"player": "?", "lang": "", "hd": 0}]

            for i, lk in enumerate(ev_links, 1):
                stream_url = f"{BASE_URL}/live/{append}/{eid}-{i}"
                results.append({
                    "name": ev["title"],
                    "group": name,
                    "logo": ev["logo"],
                    "time": ev["time"],
                    "player": lk.get("player", "?"),
                    "lang": lk.get("lang", ""),
                    "hd": lk.get("hd", 0),
                    "stream_url": stream_url,
                    "eid": eid,
                    "link_no": i,
                    "append": append,
                })
        return results

    def _get_config(self, soup):
        for sc in soup.find_all("script"):
            t = sc.string or ""
            if "siteConfig" in t:
                m = re.search(r"const\s+siteConfig\s*=\s*(\{.+\})\s*;", t, re.DOTALL)
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

            # Waktu
            t = d.find("time")
            etime = None
            if t and t.get("content"):
                try:
                    etime = datetime.fromisoformat(t["content"])
                except:
                    pass

            # Logo dari icon class
            icon = d.find("i", class_=re.compile(r"vip-box-icon"))
            icon_cls = ""
            if icon:
                for c in icon.get("class", []):
                    if c not in ("align-bottom", "me-2", "vip-box-icon"):
                        icon_cls = c
                        break

            logo = self._icon_to_logo(icon_cls)
            events.append({"title": title, "id": eid, "time": etime, "logo": logo})
        return events

    def _icon_to_logo(self, icon_cls):
        m = {
            "nascar": "nfl", "formula-1": "motorsports",
            "motogp": "motorsports", "college-football": "ncaaf",
            "soccer": "football", "other-sports": "other-sports",
        }
        key = m.get(icon_cls, icon_cls) if icon_cls else "football"
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── Deteksi player domain dari halaman /live/ ───────
    def get_player_info(self, stream_url):
        """
        Fetch halaman /live/... → cari <iframe> → return (domain, player_name)
        Cache per player_name agar tidak fetch ulang.
        """
        # Coba cache dulu berdasarkan URL
        if stream_url in self._player_url_cache:
            return self._player_url_cache[stream_url]

        domain = None
        try:
            r = self.s.get(stream_url, timeout=TIMEOUT,
                           headers={"Referer": BASE_URL})
            html = r.text

            # Cari iframe (berbagai pattern)
            # Pattern 1: <iframe src="...">
            m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
            if not m:
                # Pattern 2: iframe di dalam JS/JSON
                m = re.search(r'["\']iframe["\']\s*:\s*["\']([^"\']+)["\']', html)
            if not m:
                # Pattern 3: embed/player URL di JS
                m = re.search(
                    r'(?:src|url|embed|player)[\s]*[=:]\s*["\'](https?://[^"\']+)["\']',
                    html, re.I)

            if m:
                src = m.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                domain = urlparse(src).netloc

            # Pattern 4: cari di meta/og
            if not domain:
                m = re.search(r'<meta[^>]+(?:og:|twitter:)?(?:url|image)[^>]+content=["\'](https?://[^/"\']+)', html)
                if m:
                    domain = m.group(1).split("//")[1].split("/")[0]

        except Exception as e:
            log.debug(f"  player detect fail {stream_url}: {e}")

        if not domain:
            domain = "vipbox.ga"  # fallback

        self._player_url_cache[stream_url] = domain
        return domain

    def _player_url_cache(self):
        if not hasattr(self, '_pc'):
            self._pc = {}
        return self._pc

    # ── Ekstrak stream URL dari domain player ───────────
    def get_stream_urls(self, player_domain, referrer):
        """Fetch domain player → regex m3u8/flv/mpd"""
        if player_domain in self._stream_dom_cache:
            return self._stream_dom_cache[player_domain]

        urls = []
        try:
            r = self.s.get(
                f"https://{player_domain}/",
                headers={"Referer": referrer, "User-Agent": HEADERS["User-Agent"]},
                timeout=TIMEOUT,
            )
            text = r.text

            # Regex m3u8/flv/mpd (full URL atau path)
            pats = [
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|flv|mpd)(?:\?[^\s"\'<>\\]*)?)',
                r'["\']((?:https?:)?//[^"\']+\.(?:m3u8|flv|mpd))["\']',
                r'["\'](/[^"\']+\.(?:m3u8|flv|mpd))["\']',
            ]
            for p in pats:
                found = re.findall(p, text)
                for u in found:
                    if u.startswith("/"):
                        u = f"https://{player_domain}{u}"
                    if u.startswith("//"):
                        u = "https:" + u
                    if u not in urls:
                        urls.append(u)
                if urls:
                    break

        except Exception as e:
            log.debug(f"  stream extract fail {player_domain}: {e}")

        self._stream_dom_cache[player_domain] = urls
        return urls

    # ── Resolve 1 entry ─────────────────────────────────
    def resolve_entry(self, e):
        """Dapatkan player domain + stream URL untuk 1 entry"""
        # Cek cache per player name
        pname = e["player"]
        if pname in self.player_cache:
            domain, stream_urls = self.player_cache[pname]
        else:
            domain = self.get_player_info(e["stream_url"])
            stream_urls = self.get_stream_urls(domain, f"https://{domain}/")
            self.player_cache[pname] = (domain, stream_urls)

        # Pilih best URL: m3u8 > mpd > flv
        best = e["stream_url"]  # fallback
        for ext in ("m3u8", "mpd", "flv"):
            for u in stream_urls:
                if ext in u:
                    best = u
                    break
            if best != e["stream_url"]:
                break

        e["player_domain"] = domain
        e["resolved_url"] = best
        return e

    # ── Generate M3U ────────────────────────────────────
    def write_m3u(self, entries):
        # Dedup: key = (eid, link_no, group)
        seen = set()
        unique = []
        for e in entries:
            key = (e["eid"], e["link_no"], e["group"])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        # Urutkan: ber-waktu dulu (asc), tanpa waktu di akhir
        def sk(e):
            return (0, e["time"]) if e["time"] else (1, datetime.min.replace(tzinfo=timezone.utc))
        unique.sort(key=sk)

        lines = ["#EXTM3U"]

        for e in unique:
            # Format nama
            parts = [e["name"]]
            if e["lang"]:
                parts.append(f"[{e['lang']}]")
            if e["hd"]:
                parts.append("[HD]")
            base_name = " ".join(parts)

            # Waktu WIB
            if e["time"]:
                wib = e["time"].astimezone(WIB)
                ts = wib.strftime("%d-%m-%Y %H:%M")
            else:
                ts = "LIVE 24/7"

            full_name = f"{base_name} | {ts} WIB"

            # EXVLCOPT
            domain = e.get("player_domain", "vipbox.ga")
            referrer = f"https://{domain}/"
            ua = HEADERS["User-Agent"]

            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            lines.append(f"#EXTVLCOPT:http-referrer={referrer}")
            lines.append(
                f'#EXTINF:-1 tvg-id="{e["group"].lower()}" '
                f'tvg-name="{full_name}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",{full_name}'
            )
            lines.append(e.get("resolved_url", e["stream_url"]))

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"✅ {OUTPUT_FILE} → {len(unique)} channel")

    # ── Main ────────────────────────────────────────────
    def run(self):
        log.info("=" * 40)
        log.info("VIPBox M3U Generator")
        log.info("=" * 40)

        all_entries = []
        for slug, name in CATEGORIES:
            entries = self.fetch_category(slug, name)
            log.info(f"  {name}: {len(entries)} streams")
            all_entries.extend(entries)
            time.sleep(0.3)

        log.info(f"\nTotal: {len(all_entries)} → resolve player domain...")

        # Resolve dengan thread pool
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self.resolve_entry, e): e for e in all_entries}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as ex:
                    e = futures[fut]
                    e["player_domain"] = "vipbox.ga"
                    e["resolved_url"] = e["stream_url"]
                    log.warning(f"  resolve fail: {ex}")

        self.write_m3u(all_entries)


if __name__ == "__main__":
    VIPBox().run()   
