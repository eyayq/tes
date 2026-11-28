import requests
import re
import json
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE = "https://vipbox.ga"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/158.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.7",
}

# ─── STEP 1: Schedule (tidak diubah) ───────────────────────

def get_schedule_data():
    resp = requests.get(f"{BASE}/livenow-schedule", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    m = re.search(r'const siteConfig = ({.*?});', resp.text, re.DOTALL)
    if not m:
        raise RuntimeError("siteConfig tidak ditemukan")
    config = json.loads(m.group(1))
    return config["fetck_token"], config["scheduleData"]

def get_events(token, schedule):
    url = f"{BASE}/schedule/{schedule}/"
    headers = {
        "fetch-token": token,
        "Content-Type": "application/json",
        "User-Agent": HEADERS["User-Agent"],
        "Origin": BASE,
        "Referer": f"{BASE}/livenow-schedule",
        "Accept": "*/*",
    }
    resp = requests.post(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

# ─── STEP 2: Ekstrak .m3u8 dari event page (BARU) ─────────

def get_m3u8_url(page, event_url):
    """
    Load event page di Playwright, intercept request .m3u8
    dari CDN (owledge.cc / domain player).
    """
    m3u8_url = None

    def on_response(response):
        nonlocal m3u8_url
        if ".m3u8" in response.url and "master" not in response.url:
            m3u8_url = response.url

    page.on("response", on_response)
    page.goto(event_url, wait_until="networkidle", timeout=30000)

    # Tunggu max 15 detik untuk m3u8 muncul
    for _ in range(15):
        if m3u8_url:
            break
        time.sleep(1)

    page.remove_listener("response", on_response)
    return m3u8_url

# ─── STEP 3: Build playlist (tidak diubah, tambah m3u8) ──

def build_m3u(events_data, html, m3u8_map):
    slugs = events_data["slugs"]
    links = events_data["links"]
    appends = events_data["linkAppends"]
    link_uri_tpl = events_data["linkUri"]

    event_names = dict(re.findall(
        r'data-bs-target="#(\d+)"[^>]*title="([^"]+)"', html
    ))

    lines = ["#EXTM3U"]

    for event_id, link_list in links.items():
        slug = slugs.get(event_id, "")
        sport = appends.get(event_id, "sports")
        name = event_names.get(event_id, f"Event {event_id}")

        # Cek apakah ada m3u8 langsung
        m3u8 = m3u8_map.get(event_id)

        if m3u8:
            # Satu entry per event dengan URL m3u8 langsung
            lines.append(f"#EXTINF:-1,{name} [HD]")
            lines.append(m3u8)
        else:
            # Fallback: pakai URL halaman /live/...
            for i, link in enumerate(link_list, 1):
                uri = link_uri_tpl
                uri = uri.replace("|PARAM2|", sport)
                uri = uri.replace("|PARAM|", slug)
                uri = uri.replace("|LINK_NO|", str(i))
                stream_url = BASE + uri

                player = link.get("player", "")
                hd = "HD" if link.get("hd") else "SD"
                lang = link.get("lang", "")
                label = f"{name} [{player} {hd}"
                if lang:
                    label += f" {lang}"
                label += "]"

                lines.append(f"#EXTINF:-1,{label}")
                lines.append(stream_url)

    return "\n".join(lines)

# ─── MAIN ──────────────────────────────────────────────────

def main():
    print("1/4 Fetch schedule...")
    token, schedule = get_schedule_data()
    print(f"   Token: {token[:20]}...")

    print("2/4 Fetch events...")
    data = get_events(token, schedule)
    html = requests.get(f"{BASE}/livenow-schedule", headers=HEADERS).text
    print(f"   Ditemukan {len(data['links'])} event")

    # Siapkan map event_id → event_url
    slugs = data["slugs"]
    appends = data["linkAppends"]
    event_urls = {}
    for eid, slug in slugs.items():
        sport = appends.get(eid, "sports")
        event_urls[eid] = f"{BASE}/onair/{sport}/{slug}"

    print("3/4 Fetch .m3u8 (Playwright)...")
    m3u8_map = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="id-ID",
        )
        page = ctx.new_page()

        # Limit: max 20 event per run (bisa diubah)
        for eid, url in list(event_urls.items())[:20]:
            try:
                m3u8 = get_m3u8_url(page, url)
                if m3u8:
                    m3u8_map[eid] = m3u8
                    print(f"   ✅ {slugs[eid]}")
                else:
                    print(f"   ❌ {slugs[eid]} (no m3u8)")
            except Exception as e:
                print(f"   ⚠️  {slugs[eid]}: {e}")
            time.sleep(2)  # anti-rate-limit

        browser.close()

    print(f"   Berhasil: {len(m3u8_map)}/{len(event_urls)}")

    print("4/4 Generate playlist.m3u...")
    m3u = build_m3u(data, html, m3u8_map)

    with open("playlist.m3u", "w") as f:
        f.write(m3u)

    count = m3u.count("#EXTINF")
    print(f"✅ playlist.m3u → {count} stream")

if __name__ == "__main__":
    main()   
