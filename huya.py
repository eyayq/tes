#!/usr/bin/env python3
import argparse, hashlib, hmac, json, sys, time, uuid, logging
from pathlib import Path
from urllib.parse import quote

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("pip install 'curl-cffi<0.14'"); sys.exit(1)

API_BASE = "https://api.vidio.com"
SITE_URL = "https://m.vidio.com"
WORKER = "https://ch.bhns.workers.dev"
API_KEY = "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w="
UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"
REFERER = "https://m.vidio.com/"
ORIGIN = "https://m.vidio.com"

USER_ID = "186957857"
USER_EMAIL = "bhianesee05@gmail.com"
AHOY_VISITOR = "3489c20a-d32e-468a-bd1b-8f7d150fa132"
REMEMBER_TOKEN = ("eyJfcmFpbHMiOnsibWVzc2FnZSI6Ilcxc3hPRFk1TlRjNE5UZGRMQ0lrTW1Fa01UQWthRU0xVnpKbGNVSXlhakJTYmtaMmIwUmFjVlJ2VHlJc0lqRTNPVFF6T1RneU5ERXVNekkyTkRFM05DSmQi"
    "LCJleHAiOiIyMDI4LTExLTExVDExOjU3OjIxLjMyNloiLCJwdXIiOiJjb29raWUucmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D"
    "--2c508b1dd9d15adf9f96cfcbfdbcfd3ef41d76c5")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("vidio")

# ==================== BOOTSTRAP → DAPAT COOKIES ====================
def bootstrap(s):
    s.cookies.set("ahoy_visitor", AHOY_VISITOR, domain=".vidio.com", path="/")
    s.cookies.set("remember_user_token", REMEMBER_TOKEN, domain=".vidio.com", path="/")
    s.cookies.set("country_id", "ID", domain=".vidio.com", path="/")
    s.get(SITE_URL, timeout=30)

    # Ambil cookies yang dibutuhkan
    cookies = {
        "at": s.cookies.get("access_token", ""),
        "vs": s.cookies.get("_vidio_session", ""),
        "av": s.cookies.get("ahoy_visit", ""),
        "pid": s.cookies.get("plenty_id", USER_ID),
    }
    luws = s.cookies.get("luws", "") or f"{str(uuid.uuid4()).upper()}_{USER_ID}"

    log.info("Cookies: at=%s... vs=%s... av=%s", cookies["at"][:20], cookies["vs"][:20], cookies["av"][:20])
    return luws, cookies

# ==================== COOKIE PARAM STRING ====================
def cookie_params(cookies):
    """Build query string untuk worker: &at=...&vs=...&av=...&pid=..."""
    parts = []
    if cookies["at"]: parts.append(f"at={quote(cookies['at'], safe='')}")
    if cookies["vs"]: parts.append(f"vs={quote(cookies['vs'], safe='')}")
    if cookies["av"]: parts.append(f"av={quote(cookies['av'], safe='')}")
    parts.append(f"pid={cookies['pid']}")
    return "&".join(parts)

# ==================== FETCH CHANNELS ====================
def fetch_channels(s, luws):
    h = {"x-api-key":API_KEY,"x-api-platform":"web-mobile","x-secure-level":"2",
         "x-visitor-id":AHOY_VISITOR,"x-user-id":USER_ID,"x-user-login":"true",
         "x-api-app-info":"js/m.vidio.com","luws":luws,
         "origin":ORIGIN,"referer":REFERER,"accept":"application/json","accept-language":"id"}
    ch = {}
    try:
        r = s.get(f"{API_BASE}/livestreamings", params={"stream_type":"tv_stream"}, headers=h, timeout=30)
        if r.status_code == 200:
            for item in r.json().get("data",[]):
                a = item.get("attributes",{})
                cid = item.get("id")
                if not cid: continue
                try: cid = int(cid)
                except: continue
                logo = ""
                sq = a.get("square_image")
                if isinstance(sq,dict): logo = sq.get("url","")
                elif isinstance(sq,str): logo = sq
                if not logo: logo = a.get("image_landscape_url","") or a.get("cover_url","")
                ch[cid] = {"id":cid,"title":a.get("title","?"),"logo":logo,"group":"Lainnya"}
            log.info("/livestreamings: %d", len(ch))
    except Exception as e: log.error("%s", e)

    # Sections
    secs = []
    page = 1
    while True:
        try:
            r = s.get(f"{API_BASE}/categories/live/sections",
                      params={"page[number]":page,"page[size]":10,"included":"contents"}, headers=h, timeout=30)
            if r.status_code != 200: break
        except: break
        d = r.json()
        ss = d.get("data",[])
        if isinstance(ss,dict): ss=[ss]
        for sec in ss:
            t = sec.get("attributes",{}).get("title","Lainnya")
            self_u = sec.get("links",{}).get("self","")
            if not self_u: self_u = f"{API_BASE}/sections/{sec.get('id')}-{t.lower().replace(' ','-')}"
            secs.append({"title":t,"url":self_u})
        for it in d.get("included",[]):
            if it.get("type")!="content": continue
            _parse(it,"Lainnya",ch)
        if d.get("links",{}).get("next"): page+=1; time.sleep(0.3)
        else: break

    for sec in secs:
        u = sec["url"] + ("&" if "?" in sec["url"] else "?") + "content_size=50"
        try:
            r = s.get(u, headers=h, timeout=30)
            if r.status_code != 200: continue
        except: continue
        d = r.json()
        items = []
        if isinstance(d,dict):
            a = d.get("attributes",{})
            if isinstance(a,dict) and a.get("contents"): items.extend(a["contents"])
            items.extend(d.get("included",[]))
            dd = d.get("data",[])
            if isinstance(dd,list): items.extend(dd)
            elif isinstance(dd,dict): items.append(dd)
        n=0
        for it in items:
            if _parse(it, sec["title"], ch): n+=1
        log.info("[%s] +%d", sec["title"], n)
        time.sleep(0.3)

    result = list(ch.values())
    log.info("Total: %d", len(result))
    return result

def _parse(it, grp, ch):
    a = it.get("attributes",{})
    if a.get("content_type","")!="livestreaming":
        if "livestreaming" not in it.get("type","").lower(): return False
    cid = a.get("content_id")
    if not cid:
        p = str(it.get("id","")).split("-")
        try: cid=int(p[-1])
        except:
            try: cid=int(it.get("id",""))
            except: return False
    try: cid=int(cid)
    except: return False
    if cid in ch:
        if grp!="Lainnya": ch[cid]["group"]=grp
        return False
    t = a.get("alt_title") or a.get("title","?")
    logo = a.get("cover_url","")
    if not logo:
        sq=a.get("square_image")
        if isinstance(sq,dict): logo=sq.get("url","")
        elif isinstance(sq,str): logo=sq
    ch[cid]={"id":cid,"title":t,"logo":logo,"group":grp}
    return True

# ==================== GENERATE M3U ====================
def gen_m3u(channels, cookies, out):
    cp = cookie_params(cookies)  # "at=...&vs=...&av=...&pid=..."
    lines = ["#EXTM3U"]
    for c in channels:
        cid = c["id"]; t = c["title"]; logo = c.get("logo",""); grp = c.get("group","Lainnya")
        stream = f"{WORKER}/ch.m3u8?id={cid}&type=hls&{cp}"
        license_url = f"{WORKER}/dh.mpd?id={cid}"
        lines.append(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{t}" tvg-logo="{logo}" group-title="{grp}",{t}')
        lines.append(f'#EXTVLCOPT:http-user-agent={UA}')
        lines.append(f'#EXTVLCOPT:http-referrer={REFERER}')
        lines.append(f'#EXTVLCOPT:http-origin={ORIGIN}')
        lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
        lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_url}")
        lines.append(stream)
    Path(out).write_text("\n".join(lines)+"\n", encoding="utf-8")
    log.info("[✓] %s (%d ch)", out, len(channels))

# ==================== MAIN ====================
def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", default="playlist.m3u")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()
    if a.debug: logging.getLogger().setLevel(logging.DEBUG)

    print("="*55)
    print("  Vidio Live TV → M3U (Proxy Worker + Cookies)")
    print(f"  HLS:     {WORKER}/ch.m3u8?id=&type=hls&at=...")
    print(f"  DASH:    {WORKER}/dh.mpd?id=&type=dash&at=...")
    print(f"  License: {WORKER}/dh.mpd?id= (POST)")
    print("="*55)

    s = cffi_requests.Session(impersonate="chrome")
    luws, cookies = bootstrap(s)

    # Verify
    try:
        r = s.get(f"{SITE_URL}/interactions.json",
                  headers={"x-api-platform":"web-mobile","x-secure-level":"2","x-auth-prefer":"AccessToken","origin":ORIGIN,"referer":REFERER}, timeout=15)
        u = r.json().get("current_user",{})
        if u: log.info("Login: %s", u.get("display_name"))
        else: log.error("Login gagal!"); sys.exit(1)
    except: log.error("Verify gagal"); sys.exit(1)

    ch = fetch_channels(s, luws)
    if not ch: log.error("0 channel"); sys.exit(1)

    groups = {}
    for c in ch: groups.setdefault(c["group"],[]).append(c["title"])
    log.info("\nGroups (%d):", len(groups))
    for g,names in sorted(groups.items()):
        log.info("  [%s] %d: %s", g, len(names), ", ".join(names[:4])+("..." if len(names)>4 else ""))

    gen_m3u(ch, cookies, a.o)

if __name__ == "__main__": main()   
