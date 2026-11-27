#!/usr/bin/env python3
"""
VIPBox M3U Playlist Generator
Scrape semua kategori → ekstrak stream m3u8/flv/mpd
→ generate playlist.m3u (WIB, dedup, EXVLCOPT)
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
# KONFIGURASI
# ═══════════════════════════════════════════════════════
BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 20
MAX_WORKERS = 4

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


# ═══════════════════════════════════════════════════════
# CLASS UTAMA
# ═══════════════════════════════════════════════════════
class VIPBox:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        # Cache: player_name → (domain, [stream_urls])
        self.player_cache = {}
        # Cache: stream_url → domain
        self.url_domain_cache = {}
        # Cache: domain → [stream_urls]
        self.domain_streams_cache = {}

    # ── 1. FETCH KATEGORI ────────────────────────────────
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

        # POST untuk ambil JSON links
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

        # Parse events dari HTML
        events = self._parse_events(soup, time_cls)
        log.info(f"    {len(events)} events ditemukan")

        # Merge HTML + JSON
        results = []
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
                    except json.JSONDecodeError:
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
            t_el = d.find("time")
            etime = None
            if t_el and t_el.get("content"):
                try:
                    etime = datetime.fromisoformat(t_el["content"])
                except ValueError:
                    pass

            # Logo
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
        mapping = {
            "nascar": "nfl",
            "formula-1": "motorsports",
            "motogp": "motorsports",
            "college-football": "ncaaf",
            "soccer": "football",
            "other-sports": "other-sports",
        }
        key = mapping.get(icon_cls, icon_cls) if icon_cls else "football"
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── 2. DETEKSI PLAYER DOMAIN ─────────────────────────
    def get_player_domain(self, stream_url):
        """
        Fetch halaman /live/... → cari <iframe src> → return domain.
        Cache per stream_url.
        """
        if stream_url in self.url_domain_cache:
            return self.url_domain_cache[stream_url]

        domain = None
        try:
            r = self.s.get(
                stream_url,
                timeout=TIMEOUT,
                headers={"Referer": f"{BASE_URL}/"},
            )
            html = r.text

            # Pattern 1: <iframe src="...">
            m = re.search(
                r'<iframe[^>]+src=["\']([^"\']+)["\']', html
            )

            # Pattern 2: JS variable
            if not m:
                m = re.search(
                    r'(?:iframe|player|embed|video)[\s_]*'
                    r'(?:src|url|href|srcUrl)?\s*[:=]\s*["\']'
                    r'(https?://[^"\']+)',
                    html, re.I
                )

            # Pattern 3: data-src, data-url
            if not m:
                m = re.search(
                    r'data-(?:src|url|embed)["\']\s*=\s*["\'](https?://[^"\']+)',
                    html, re.I
                )

            # Pattern 4: window.location / redirect
            if not m:
                m = re.search(
                    r'(?:location|redirect|url)\s*[=:]\s*["\'](https?://[^/"\'"]+)',
                    html, re.I
                )

            if m:
                src = m.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                domain = urlparse(src).netloc

        except Exception as e:
            log.debug(f"    player detect fail: {e}")

        if not domain:
            domain = "vipbox.ga"

        self.url_domain_cache[stream_url] = domain
        return domain

    # ── 3. EKSTRAK STREAM URL DARI DOMAIN PLAYER ─────────
    def get_stream_urls(self, domain):
        """Fetch domain player → regex m3u8/flv/mpd"""
        if domain in self.domain_streams_cache:
            return self.domain_streams_cache[domain]

        urls = []
        try:
            r = self.s.get(
                f"https://{domain}/",
                headers={
                    "Referer": f"https://{domain}/",
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept": "*/*",
                },
                timeout=TIMEOUT,
            )
            text = r.text

            patterns = [
                # Full URL dengan extension
                r'(https?://[^\s"\'<>\\]+\.(?:m3u8|flv|mpd)(?:\?[^\s"\'<>\\]*)?)',
                # URL di dalam quotes
                r'["\']((?:https?:)?//[^"\']+\.(?:m3u8|flv|mpd))["\']',
                # Path relatif
                r'["\'](/[^"\']+\.(?:m3u8|flv|mpd))["\']',
                # source src=
                r'src=["\']([^"\']+\.(?:m3u8|flv|mpd))["\']',
            ]

            for pat in patterns:
                found = re.findall(pat, text)
                for u in found:
                    if u.startswith("/"):
                        u = f"https://{domain}{u}"
                    elif u.startswith("//"):
                        u = "https:" + u
                    elif not u.startswith("http"):
                        u = f"https://{domain}/{u}"
                    if u not in urls:
                        urls.append(u)
                if urls:
                    break

        except Exception as e:
            log.debug(f"    stream extract fail {domain}: {e}")

        self.domain_streams_cache[domain] = urls
        return urls

    # ── 4. RESOLVE ENTRY ─────────────────────────────────
    def resolve_entry(self, e):
        """Dapatkan domain player + stream URL terbaik"""
        pname = e["player"]

        if pname in self.player_cache:
            domain, stream_urls = self.player_cache[pname]
        else:
            domain = self.get_player_domain(e["stream_url"])
            stream_urls = self.get_stream_urls(domain)
            self.player_cache[pname] = (domain, stream_urls)

        # Pilih best: m3u8 > mpd > flv
        best = e["stream_url"]
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

    # ── 5. GENERATE M3U ──────────────────────────────────
    def write_m3u(self, entries):
        # Dedup: (eid, link_no, group)
        seen = set()
        unique = []
        for e in entries:
            key = (e["eid"], e["link_no"], e["group"])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        # Sort: ber-waktu asc, tanpa waktu di akhir
        def sort_key(e):
            if e["time"]:
                return (0, e["time"])
            return (1, datetime.min.replace(tzinfo=timezone.utc))

        unique.sort(key=sort_key)

        lines = ["#EXTM3U"]
        ua = HEADERS["User-Agent"]

        for e in unique:
            # Nama channel
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
            domain = e.get("player_domain", "vipbox.ga")
            referrer = f"https://{domain}/"

            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            lines.append(f"#EXTVLCOPT:http-referrer={referrer}")
            lines.append(
                f'#EXTINF:-1 '
                f'tvg-id="{e["group"].lower()}" '
                f'tvg-name="{full_name}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",{full_name}'
            )
            lines.append(e.get("resolved_url", e["stream_url"]))

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"✅ {OUTPUT_FILE} → {len(unique)} channel")

    # ── 6. MAIN ──────────────────────────────────────────
    def run(self):
        log.info("=" * 50)
        log.info("  VIPBox M3U Playlist Generator")
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
        log.info(f"  Resolve player domain + stream URL...")

        # Resolve (threaded)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(self.resolve_entry, e): e
                for e in all_entries
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 20 == 0:
                    log.info(f"    Progress: {done}/{len(all_entries)}")
                try:
                    fut.result()
                except Exception as ex:
                    e = futures[fut]
                    e["player_domain"] = "vipbox.ga"
                    e["resolved_url"] = e["stream_url"]
                    log.warning(f"    resolve fail: {ex}")

        self.write_m3u(all_entries)


# ═══════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    VIPBox().run()   
