#!/usr/bin/env python3
"""
VIPBox M3U Playlist Generator
Scrape semua kategori, ekstrak stream URL (m3u8/flv/mpd),
generate playlist.m3u dengan tvg-logo, group-title,
waktu WIB, dan EXVLCOPT (UA + referrer dari domain iframe player).
"""

import requests
import re
import json
import base64
import time
import logging
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin

# ─── KONFIGURASI ───────────────────────────────────────────
BASE_URL = "https://vipbox.ga"
STATIC_URL = "https://static.vipbox.ga"
OUTPUT_FILE = "playlist.m3u"
MAX_WORKERS = 5
TIMEOUT = 15
WIB = timezone(timedelta(hours=7))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/158.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="158", "Not.A/Brand";v="24", "Brave";v="158"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
}

CATEGORIES = [
    ("nfl", "NFL"),
    ("ncaaf", "NCAAF"),
    ("basketball", "Basketball"),
    ("hockey", "Hockey"),
    ("tennis", "Tennis"),
    ("golf", "Golf"),
    ("football", "Football"),
    ("rugby", "Rugby"),
    ("afl", "AFL"),
    ("darts", "Darts"),
    ("mma", "MMA"),
    ("wwe", "WWE"),
    ("boxing", "Boxing"),
    ("fighting", "Fighting"),
    ("motorsports", "Motorsports"),
    ("others", "Others"),
]

# Peta ikon CSS class → URL logo
ICON_MAP = {
    "nascar": "nfl",
    "formula-1": "motorsports",
    "motogp": "motorsports",
    "nfl": "nfl",
    "college-football": "ncaaf",
    "basketball": "basketball",
    "hockey": "hockey",
    "tennis": "tennis",
    "golf": "golf",
    "soccer": "football",
    "rugby": "rugby",
    "afl": "afl",
    "darts": "darts",
    "mma": "mma",
    "wwe": "wwe",
    "boxing": "boxing",
    "fighting": "fighting",
    "other-sports": "other-sports",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("VIPBox")


class VIPBoxScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._player_domain_cache = {}
        self._stream_url_cache = {}

    # ── 1. Ambil data schedule per kategori ────────────────
    def fetch_category(self, cat_slug, cat_name):
        """GET halaman schedule → ekstrak siteConfig → POST /schedule/"""
        url = f"{BASE_URL}/{cat_slug}-schedule"
        log.info(f"Scrape: {cat_name} ({url})")
        try:
            resp = self.session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"  Gagal GET {url}: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Ekstrak siteConfig
        config = self._extract_site_config(soup)
        if not config:
            log.warning(f"  siteConfig tidak ditemukan di {url}")
            return []

        schedule_data = config.get("scheduleData", "")
        fetch_token = config.get("fetck_token", "")
        time_cls = config.get("timeCls", "vt_m0k0o3u5d1")

        # Parse event dari HTML
        events_html = self._parse_events_html(soup, time_cls)
        if not events_html:
            log.info(f"  Tidak ada event di {cat_name}")
            return []

        # POST untuk ambil links JSON
        post_url = f"{BASE_URL}/schedule/{schedule_data}/"
        post_headers = {
            "fetch-token": fetch_token,
            "Referer": url,
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        }
        try:
            resp2 = self.session.post(
                post_url, headers=post_headers, timeout=TIMEOUT
            )
            data = resp2.json()
        except Exception as e:
            log.error(f"  Gagal POST {post_url}: {e}")
            data = {"links": {}, "linkAppends": {}, "slugs": {}}

        # Gabungkan
        return self._merge_events(
            events_html, data, cat_slug, cat_name
        )

    def _extract_site_config(self, soup):
        """Ekstrak JSON siteConfig dari <script>"""
        for script in soup.find_all("script"):
            text = script.string or script.get_text()
            if "siteConfig" in text:
                m = re.search(
                    r"const\s+siteConfig\s*=\s*(\{.*?\})\s*;",
                    text, re.DOTALL,
                )
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
        return None

    def _parse_events_html(self, soup, time_cls):
        """Parse <details vt-data-events-row> untuk nama, waktu, ID"""
        events = []
        for d in soup.find_all("details", attrs={"vt-data-events-row": True}):
            summary = d.find("summary")
            if not summary:
                continue
            a = summary.find("a")
            if not a:
                continue

            title = a.get("title", "").strip()
            href = a.get("href", "")
            target = a.get("data-bs-target", "").lstrip("#")

            # Waktu dari <time content="...">
            time_el = summary.find("time", class_=time_cls)
            event_time = None
            if time_el:
                content = time_el.get("content", "")
                try:
                    event_time = datetime.fromisoformat(content)
                except ValueError:
                    pass

            # Ikon untuk logo
            icon_el = summary.find("i", class_=re.compile(r"vip-box-icon"))
            icon_cls = ""
            if icon_el:
                for cls in icon_el.get("class", []):
                    if cls != "align-bottom" and cls != "me-2" \
                       and cls != "vip-box-icon":
                        icon_cls = cls
                        break

            events.append({
                "title": title,
                "event_id": target,
                "time": event_time,
                "icon": icon_cls,
                "href": href,
            })
        return events

    def _merge_events(self, events_html, data, cat_slug, cat_name):
        """Gabungkan data HTML + JSON → daftar stream"""
        slugs = data.get("slugs", {})
        link_appends = data.get("linkAppends", {})
        links = data.get("links", {})
        results = []

        for ev in events_html:
            eid = ev["event_id"]
            append = link_appends.get(eid, cat_slug)
            ev_links = links.get(eid, [])

            # Tentukan logo
            logo = self._get_logo(ev["icon"], cat_slug)

            # Jika tidak ada link spesifik, buat 1 entry
            if not ev_links:
                ev_links = [{"player": "Unknown", "lang": "", "hd": 0}]

            for i, link in enumerate(ev_links, 1):
                stream_path = f"/live/{append}/{eid}-{i}"
                stream_url = f"{BASE_URL}{stream_path}"

                # Format nama + waktu WIB
                time_str = ""
                if ev["time"]:
                    wib_time = ev["time"].astimezone(WIB)
                    time_str = wib_time.strftime("%d-%m-%Y %H:%M")
                else:
                    time_str = "LIVE 24/7"

                name = ev["title"]
                lang = link.get("lang", "")
                hd = link.get("hd", 0)
                player = link.get("player", "")

                # Susun nama channel
                # [HH:MM DD-MM-YYYY] Nama Event [Lang] [HD]
                parts = [name]
                if lang:
                    parts.append(f"[{lang}]")
                if hd:
                    parts.append("[HD]")
                channel_name = " ".join(parts)
                # Tambah waktu di akhir
                full_name = f"{channel_name} | {time_str} WIB"

                results.append({
                    "name": full_name,
                    "group": cat_name,
                    "logo": logo,
                    "stream_url": stream_url,
                    "player": player,
                    "time": ev["time"],
                    "time_str": time_str,
                    "lang": lang,
                    "hd": hd,
                })

        return results

    def _get_logo(self, icon_cls, cat_slug):
        """Dapatkan URL tvg-logo"""
        key = icon_cls if icon_cls else cat_slug
        # Normalisasi
        key_map = {
            "nascar": "nfl",
            "formula-1": "motorsports",
            "motogp": "motorsports",
            "college-football": "ncaaf",
            "soccer": "football",
            "other-sports": "other-sports",
        }
        key = key_map.get(key, key)
        return f"{STATIC_URL}/img/home/{key}.webp"

    # ── 2. Deteksi domain iframe player ────────────────────
    def get_player_domain(self, stream_url):
        """
        Kunjungi halaman stream di vipbox.ga,
        cari <iframe> → ekstrak domain player.
        """
        if stream_url in self._player_domain_cache:
            return self._player_domain_cache[stream_url]

        domain = "vipbox.ga"
        try:
            resp = self.session.get(
                stream_url,
                headers={"Referer": BASE_URL},
                timeout=TIMEOUT,
            )
            # Cari iframe
            m = re.search(
                r'<iframe[^>]+src=["\']([^"\']+)["\']',
                resp.text,
            )
            if m:
                iframe_src = m.group(1)
                if iframe_src.startswith("//"):
                    iframe_src = "https:" + iframe_src
                domain = urlparse(iframe_src).netloc or "vipbox.ga"

            # Alternatif: cari di JS
            if domain == "vipbox.ga":
                m2 = re.search(
                    r'(?:player|iframe|embed)[^=]*=["\']'
                    r'(https?://[^/"\']+)',
                    resp.text, re.IGNORECASE,
                )
                if m2:
                    domain = m2.group(1).replace("https://", "").replace("http://", "")

        except Exception as e:
            log.debug(f"  Gagal deteksi player {stream_url}: {e}")

        self._player_domain_cache[stream_url] = domain
        return domain

    # ── 3. Ekstrak URL stream (m3u8/flv/mpd) ──────────────
    def extract_stream_urls(self, stream_url, player_domain):
        """
        Kunjungi domain player, cari URL m3u8/flv/mpd.
        """
        cache_key = f"{player_domain}:{stream_url}"
        if cache_key in self._stream_url_cache:
            return self._stream_url_cache[cache_key]

        urls = []
        player_url = f"https://{player_domain}"

        try:
            resp = self.session.get(
                player_url,
                headers={
                    "Referer": f"https://{player_domain}/",
                    "User-Agent": HEADERS["User-Agent"],
                },
                timeout=TIMEOUT,
            )
            text = resp.text

            # Cari m3u8 / flv / mpd
            pattern = r'(https?://[^\s"\'<>\\]+\.(?:m3u8|flv|mpd)(?:\?[^"\s\'<>\\]*)?)'
            found = re.findall(pattern, text)
            urls = list(dict.fromkeys(found))  # dedup, preserve order

            # Kalau belum ketemu, coba cari di JSON/JS
            if not urls:
                # Pola umum: "url":"...m3u8" atau src: '...m3u8'
                pattern2 = r'["\']([^"\']+\.(?:m3u8|flv|mpd))["\']'
                found2 = re.findall(pattern2, text)
                for u in found2:
                    if u.startswith("http"):
                        urls.append(u)
                    elif u.startswith("/"):
                        urls.append(f"https://{player_domain}{u}")

        except Exception as e:
            log.debug(f"  Gagal ekstrak stream {player_url}: {e}")

        self._stream_url_cache[cache_key] = urls
        return urls

    # ── 4. Proses semua kategori ───────────────────────────
    def scrape_all(self):
        all_entries = []

        for cat_slug, cat_name in CATEGORIES:
            entries = self.fetch_category(cat_slug, cat_name)
            log.info(f"  → {len(entries)} stream dari {cat_name}")
            all_entries.extend(entries)
            time.sleep(0.5)  # rate limit

        # Urutkan: event ber-waktu dulu (ascending), LIVE 24/7 di akhir
        def sort_key(e):
            if e["time"]:
                return (0, e["time"])
            return (1, datetime.min.replace(tzinfo=timezone.utc))

        all_entries.sort(key=sort_key)
        return all_entries

    # ── 5. Generate M3U ────────────────────────────────────
    def generate_m3u(self, entries, output_file=OUTPUT_FILE):
        """
        Generate playlist.m3u dengan:
        - tvg-logo, group-title, tvg-name
        - #EXTVLCOPT: http-user-agent + http-referrer
          (dari domain iframe player, BUKAN vipbox.ga)
        - URL stream m3u8/flv/mpd jika ditemukan,
          kalau tidak → URL wrapper vipbox.ga
        """
        lines = ["#EXTM3U"]
        lines.append(
            '#EXTM3U url-tvg="" tvg-shift="0"'
        )

        # Batch: deteksi player domain + ekstrak stream URL
        log.info("Mendeteksi player domain & stream URL...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for i, e in enumerate(entries):
                fut = pool.submit(
                    self._resolve_stream, e
                )
                futures[fut] = i

            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    entries[idx].update(fut.result())
                except Exception as ex:
                    log.warning(f"  Error resolve #{idx}: {ex}")
                    entries[idx]["resolved_url"] = entries[idx]["stream_url"]
                    entries[idx]["player_domain"] = "vipbox.ga"

        # Urutkan ulang setelah resolve
        def sort_key(e):
            if e.get("time"):
                return (0, e["time"])
            return (1, datetime.min.replace(tzinfo=timezone.utc))

        entries.sort(key=sort_key)

        for e in entries:
            name = e["name"]
            group = e["group"]
            logo = e["logo"]
            player_domain = e.get("player_domain", "vipbox.ga")
            resolved_url = e.get("resolved_url", e["stream_url"])

            # EXVLCOPT: UA + Referrer dari domain PLAYER (bukan vipbox.ga)
            referrer = f"https://{player_domain}/"
            ua = HEADERS["User-Agent"]

            lines.append("#EXTVLCOPT:http-user-agent=" + ua)
            lines.append("#EXTVLCOPT:http-referrer=" + referrer)
            lines.append(
                f'#EXTINF:-1 tvg-id="{group.lower()}" '
                f'tvg-name="{name}" '
                f'tvg-logo="{logo}" '
                f'group-title="{group}",{name}'
            )
            lines.append(resolved_url)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        log.info(f"✅ Playlist tersimpan: {output_file}")
        log.info(f"   Total: {len(entries)} channel")

    def _resolve_stream(self, entry):
        """Thread-safe: deteksi domain player + ekstrak URL stream"""
        stream_url = entry["stream_url"]
        player_domain = self.get_player_domain(stream_url)
        stream_urls = self.extract_stream_urls(stream_url, player_domain)

        # Pilih URL terbaik: m3u8 > mpd > flv
        best_url = stream_url  # fallback ke wrapper vipbox.ga
        if stream_urls:
            for ext in ("m3u8", "mpd", "flv"):
                for u in stream_urls:
                    if ext in u:
                        best_url = u
                        break
                if best_url != stream_url:
                    break

        return {
            "player_domain": player_domain,
            "resolved_url": best_url,
            "stream_urls": stream_urls,
        }

    # ── MAIN ───────────────────────────────────────────────
    def run(self):
        log.info("=" * 50)
        log.info("VIPBox M3U Playlist Generator")
        log.info(f"Target: {BASE_URL}")
        log.info(f"Kategori: {len(CATEGORIES)}")
        log.info("=" * 50)

        entries = self.scrape_all()
        if not entries:
            log.error("Tidak ada data ditemukan!")
            return

        log.info(f"\nTotal {len(entries)} stream ditemukan.\n")
        self.generate_m3u(entries)


if __name__ == "__main__":
    scraper = VIPBoxScraper()
    scraper.run()   
