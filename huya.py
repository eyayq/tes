#!/usr/bin/env python3
"""VIPBox Live Schedule Scraper (minimal)"""

from curl_cffi import requests as cffi_requests
import re, json, logging, time, sys
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://vipbox.ga"
WIB = timezone(timedelta(hours=7))
TIMEOUT = 30
MAX_WORKERS = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/158.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "id-ID,id;q=0.6",
}

CATEGORIES = [
    ("nfl","NFL"),("ncaaf","NCAAF"),("basketball","Basketball"),
    ("hockey","Hockey"),("tennis","Tennis"),("golf","Golf"),
    ("football","Football"),("rugby","Rugby"),("afl","AFL"),
    ("darts","Darts"),("mma","MMA"),("wwe","WWE"),
    ("boxing","Boxing"),("fighting","Fighting"),
    ("motorsports","Motorsports"),("others","Others"),
]

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("Schedule")

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

class Scraper:
    def __init__(self):
        self.s = cffi_requests.Session(impersonate="chrome")
        self.s.headers.update(HEADERS)

    def _get_cfg(self, html):
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script"):
            t = sc.string or sc.get_text()
            if "siteConfig" in t:
                return extract_json_block(t, "siteConfig"), soup
        return None, soup

    def _parse_events(self, soup):
        events = []
        for d in soup.find_all("details", attrs={"vt-data-events-row": True}):
            a = d.find("a")
            if not a: continue
            title = a.get("title","").strip()
            eid = a.get("data-bs-target","").lstrip("#")
            if not eid: continue
            href = a.get("href","")
            gc = "others"
            if href.startswith("/onair/"):
                parts = href.strip("/").split("/")
                if len(parts) >= 2: gc = parts[1]
            t_el = d.find("time")
            etime = None
            if t_el and t_el.get("content"):
                try: etime = datetime.fromisoformat(t_el["content"])
                except: pass
            events.append({"title":title,"id":eid,"time":etime,
                          "onair_uri":href,"gc":gc})
        return events

    def fetch(self, slug, name):
        url = f"{BASE_URL}/{slug}-schedule"
        try:
            r = self.s.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log.error(f"  FAIL {slug}: {e}")
            return []
        cfg, soup = self._get_cfg(r.text)
        if not cfg: return []
        sd, tk = cfg.get("scheduleData",""), cfg.get("fetck_token","")
        try:
            r2 = self.s.post(f"{BASE_URL}/schedule/{sd}/",
                headers={"fetch-token":tk,"Referer":url,
                         "Origin":BASE_URL,
                         "Content-Type":"application/json"},
                timeout=TIMEOUT)
            data = r2.json()
        except:
            data = {"links":{},"slugs":{},"linkAppends":{}}
        events = self._parse_events(soup)
        results = []
        for ev in events:
            eid = ev["id"]
            links = data.get("links",{}).get(eid,[])
            if not links:
                players = data.get("players",{}).get(eid,[])
                links = players or [{"player":"?","lang":"","hd":0}]
            slug_ = data.get("slugs",{}).get(eid,eid)
            gc = data.get("linkAppends",{}).get(eid,ev["gc"])
            for i,lk in enumerate(links,1):
                results.append({
                    "name":ev["title"],"group":name,
                    "time":ev["time"],"gc":gc,
                    "player":lk.get("player","?"),
                    "lang":lk.get("lang",""),
                    "hd":lk.get("hd",0),
                    "live_uri":f"/live/{gc}/{slug_}-{i}",
                    "onair_uri":ev["onair_uri"],
                })
        return results

    def fetch_livenow(self):
        url = f"{BASE_URL}/livenow-schedule"
        try:
            r = self.s.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            log.error(f"  FAIL livenow: {e}")
            return []
        cfg, soup = self._get_cfg(r.text)
        if not cfg: return []
        sd, tk = cfg.get("scheduleData",""), cfg.get("fetck_token","")
        try:
            r2 = self.s.post(f"{BASE_URL}/schedule/{sd}/",
                headers={"fetch-token":tk,"Referer":url,
                         "Origin":BASE_URL,
                         "Content-Type":"application/json"},
                timeout=TIMEOUT)
            data = r2.json()
        except:
            data = {"links":{},"slugs":{},"linkAppends":{}}
        events = self._parse_events(soup)
        results = []
        for ev in events:
            eid = ev["id"]
            links = data.get("links",{}).get(eid,[])
            if not links:
                links = data.get("players",{}).get(eid,[]) or \
                        [{"player":"?","lang":"","hd":0}]
            slug_ = data.get("slugs",{}).get(eid,eid)
            gc = data.get("linkAppends",{}).get(eid,ev["gc"])
            for i,lk in enumerate(links,1):
                results.append({
                    "name":ev["title"],"group":"24/7",
                    "time":ev["time"],"gc":gc,
                    "player":lk.get("player","?"),
                    "lang":lk.get("lang",""),
                    "hd":lk.get("hd",0),
                    "live_uri":f"/live/{gc}/{slug_}-{i}",
                    "onair_uri":ev["onair_uri"],
                })
        return results

    def run(self):
        log.info("Scraping schedule...")
        all_entries = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(self.fetch,s,n):n
                    for s,n in CATEGORIES}
            for f in as_completed(futs):
                n = futs[f]
                try:
                    r = f.result()
                    log.info(f"  {n}: {len(r)}")
                    all_entries.extend(r)
                except Exception as e:
                    log.error(f"  {n} error: {e}")
        ln = self.fetch_livenow()
        log.info(f"  24/7: {len(ln)}")
        all_entries.extend(ln)

        # Filter: hanya LIVE (sudah mulai)
        now = datetime.now(WIB)
        live = [e for e in all_entries
                if e["time"] and e["time"] <= now]
        upcoming = [e for e in all_entries
                    if e["time"] and e["time"] > now]

        log.info(f"\n  TOTAL: {len(all_entries)}")
        log.info(f"  LIVE:   {len(live)}")
        log.info(f"  UPCOMING: {len(upcoming)}")

        # Output
        print(f"\n{'='*60}")
        print(f"  LIVE NOW ({len(live)})")
        print(f"{'='*60}")
        for e in sorted(live, key=lambda x: x["time"] or now):
            ts = e["time"].astimezone(WIB).strftime("%H:%M")
            hd = " [HD]" if e["hd"] else ""
            lang = f" [{e['lang']}]" if e["lang"] else ""
            print(f"  {ts}  {e['name']}{hd}{lang}  "
                  f"({e['group']})")

        if upcoming:
            print(f"\n{'='*60}")
            print(f"  UPCOMING ({len(upcoming)})")
            print(f"{'='*60}")
            for e in sorted(upcoming, key=lambda x: x["time"])[:20]:
                ts = e["time"].astimezone(WIB).strftime("%d/%m %H:%M")
                print(f"  {ts}  {e['name']}  ({e['group']})")
            if len(upcoming) > 20:
                print(f"  ... +{len(upcoming)-20} more")

        return all_entries

if __name__ == "__main__":
    Scraper().run()   
