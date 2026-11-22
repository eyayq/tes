#!/usr/bin/env python3
"""
Auto-scrape xoilacvck.tv → playlist.m3u
Selenium + ThreadPoolExecutor
"""

import re
import time
import datetime

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


BASE_URL = "https://xoilacvck.tv"
IFRAME_REFERER = "https://xl365.domainkqt.cc/"
MAX_WORKERS = 250

LIVE_STATUSES = {"2", "3", "4", "5", "6", "51", "52", "53", "54"}


# =========================================================
# SETUP DRIVER
# =========================================================
def setup_driver():

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--blink-settings=imagesEnabled=false")

    options.page_load_strategy = "eager"

    options.add_argument(
        "--user-agent=Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/125.0.0.0 "
        "Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)

    return driver


# =========================================================
# EXTRACT STREAMS FROM HTML
# =========================================================
def extract_streams_from_html(html):

    streams = set()

    patterns = [
        r'https?:\/\/[^"\'\\]+\.m3u8[^"\'\\]*',
        r'https?:\/\/[^"\'\\]+\.mpd[^"\'\\]*',
        r'https?:\/\/[^"\'\\]+\.flv[^"\'\\]*',
        r'"file"\s*:\s*"([^"]+)"',
        r'"src"\s*:\s*"([^"]+)"',
        r'"source"\s*:\s*"([^"]+)"',
        r'"hls"\s*:\s*"([^"]+)"',
        r'"stream_url"\s*:\s*"([^"]+)"',
        r'"random_stream_url"\s*:\s*"([^"]+)"',
        r'"video_url"\s*:\s*"([^"]+)"',
        r'"play_url"\s*:\s*"([^"]+)"',
        r'file:\s*"([^"]+)"',
        r'source:\s*"([^"]+)"',
        r'stream_url\s*[:=]\s*["\'](https?://[^"\']+)',
        r'["\'](https?:\/\/[^"\']+)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.I)
        for m in matches:
            m = m.replace("\\/", "/")
            if any(x in m.lower() for x in [".m3u8", ".mpd", ".flv"]):
                streams.add(m)

    return streams


# =========================================================
# GET STREAMS
# =========================================================
def get_streams(driver):

    streams = set()

    try:
        time.sleep(5)

        html = driver.page_source
        streams.update(extract_streams_from_html(html))

        # PERFORMANCE RESOURCE
        resources = driver.execute_script("""
            const resources = performance.getEntriesByType('resource');
            return resources.map(r => r.name);
        """)

        for url in resources:
            if any(x in url.lower() for x in [".m3u8", ".mpd", ".flv"]):
                streams.add(url)

        # IFRAMES
        iframes = driver.find_elements("tag name", "iframe")

        for iframe in iframes:
            try:
                src = iframe.get_attribute("src")

                if src:
                    if "${" in src or "%7B" in src:
                        continue

                    if any(x in src.lower() for x in [".m3u8", ".mpd", ".flv"]):
                        streams.add(src)

                    driver.switch_to.frame(iframe)
                    time.sleep(3)
                    iframe_html = driver.page_source
                    streams.update(extract_streams_from_html(iframe_html))

                    try:
                        iframe_resources = driver.execute_script("""
                            const resources = performance.getEntriesByType('resource');
                            return resources.map(r => r.name);
                        """)
                        for url in iframe_resources:
                            if any(x in url.lower() for x in [".m3u8", ".mpd", ".flv"]):
                                streams.add(url)
                    except:
                        pass

                    driver.switch_to.default_content()

            except:
                try:
                    driver.switch_to.default_content()
                except:
                    pass

        # INLINE SCRIPTS
        scripts = driver.find_elements("tag name", "script")
        for script in scripts:
            try:
                content = script.get_attribute("innerHTML")
                if content:
                    streams.update(extract_streams_from_html(content))
            except:
                pass

        # WINDOW VARIABLES
        try:
            window_vars = driver.execute_script("""
                const results = [];
                const keys = ['streamUrl', 'stream_url', 'videoUrl', 'video_url',
                              'hlsUrl', 'hls_url', 'flvUrl', 'flv_url',
                              'playUrl', 'play_url', 'random_stream_url',
                              'playerUrl', 'player_url'];
                for (const key of keys) {
                    if (window[key] && typeof window[key] === 'string') {
                        results.push(window[key]);
                    }
                }
                return results;
            """)
            for url in window_vars:
                if url and any(x in url.lower() for x in [".m3u8", ".mpd", ".flv"]):
                    streams.add(url)
        except:
            pass

    except Exception as e:
        print(f"STREAM ERROR: {e}")

    return list(streams)


# =========================================================
# GET MATCH LINKS
# =========================================================
def get_match_links():

    driver = setup_driver()
    results = []

    try:
        driver.get(BASE_URL)
        time.sleep(5)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        for item in soup.select(".main-grid-match"):
            fid = item.get("data-fid", "")
            sport = item.get("data-sport", "unknown")
            sport_id = item.get("data-sport-id", "")
            league = item.get("data-league", "")
            status = item.get("data-status", "1")
            data_day = item.get("data-day", "")
            data_runtime = item.get("data-runtime", "")

            link = item.select_one("a.redirectPopup[href*='/truc-tiep/']")
            if not link:
                link = item.select_one("a[href*='/truc-tiep/']:not(.commentator)")
            if not link:
                link = item.select_one("div[href*='/truc-tiep/']")
            if not link:
                continue

            slug_path = link.get("href", "")
            full_url = urljoin(BASE_URL, slug_path)

            league_img = item.select_one(".gmd-comp_logo")
            tvg_logo = league_img.get("src", "") if league_img else ""

            home_names = [p.get_text(strip=True) for p in item.select(".gmd-home_team .team-name-group p")]
            away_names = [p.get_text(strip=True) for p in item.select(".gmd-away_team .team-name-group p")]
            home = "/".join(home_names) if home_names else "Home"
            away = "/".join(away_names) if away_names else "Away"
            title = f"{home} vs {away}"

            time_el = item.select_one(".gmd-e_minutes[data-time]")
            match_time = time_el.get("data-time", "") if time_el else ""

            date_el = item.select_one(".gmd-match-date span")
            match_date = date_el.get_text(strip=True).split(" - ")[-1] if date_el else ""

            if not match_date and data_day and len(data_day) == 8:
                match_date = f"{data_day[6:8]}/{data_day[4:6]}"
            if not match_time and data_runtime:
                dt = datetime.datetime.fromtimestamp(int(data_runtime))
                match_time = dt.strftime("%H:%M")

            is_live = status in LIVE_STATUSES

            results.append({
                "fid": fid,
                "sport": sport,
                "sport_id": sport_id,
                "league": league,
                "status": status,
                "title": title,
                "url": full_url,
                "tvg_logo": tvg_logo,
                "match_time": match_time,
                "match_date": match_date,
                "is_live": is_live,
            })

    except Exception as e:
        print(f"MATCH ERROR: {e}")

    finally:
        driver.quit()

    return results


# =========================================================
# SCRAPE MATCH
# =========================================================
def scrape_match(match):

    driver = setup_driver()
    title = match["title"]
    url = match["url"]
    league = match["league"]
    tvg_logo = match["tvg_logo"]
    match_time = match["match_time"]
    match_date = match["match_date"]
    is_live = match["is_live"]

    tag = "🔴" if is_live else "⏳"
    print(f"{tag} CHECK -> {title}")

    try:
        driver.get(url)
        time.sleep(4)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        # IMPROVE TITLE
        h1 = soup.find("h1")
        if h1:
            better = h1.get_text(" ", strip=True)
            if len(better) > 5:
                title = better

        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            og_title = og["content"].strip()
            if len(og_title) > 5:
                title = og_title

        title = re.sub(r'\s+', ' ', title)
        title = title.replace(",", "")
        title = title.strip()
        title = re.sub(r'\s*[\|\-]\s*xoilac.*$', '', title, flags=re.I)

        # LOGO
        logo = tvg_logo or "https://viiip.kitashinsaku.com/0.php"
        if not logo:
            imgs = soup.find_all("img", src=True)
            for img in imgs:
                src = img["src"]
                if any(x in src.lower() for x in ["logo", "team", "club"]):
                    if not src.startswith("http"):
                        src = urljoin(BASE_URL, src)
                    logo = src
                    break

        # STREAMS
        streams = get_streams(driver)

        if not streams:
            print(f"  NO STREAM")
            return []

        results = []
        for stream in streams:
            print(f"  FOUND STREAM: {stream}")
            results.append({
                "name": title,
                "logo": logo,
                "url": stream,
                "league": league,
                "match_time": match_time,
                "match_date": match_date,
                "is_live": is_live,
                "match_url": url,
            })

        return results

    except Exception as e:
        print(f"DETAIL ERROR: {e}")
        return []

    finally:
        driver.quit()


# =========================================================
# WRITE M3U
# =========================================================
def write_m3u(data):

    if not data:
        print("NO DATA")
        return

    content = "#EXTM3U\n\n"
    used = set()

    for item in data:
        if item["url"] in used:
            continue
        used.add(item["url"])

        name = item["name"]
        if item["is_live"]:
            name = f"[LIVE] {name}"
        if item["match_time"] and item["match_date"]:
            name += f" ({item['match_time']} - {item['match_date']})"
        elif item["match_time"]:
            name += f" ({item['match_time']})"
        elif item["match_date"]:
            name += f" ({item['match_date']})"

        league = item.get("league", "XOILAC LIVE").replace('"', "'")

        content += (
            f'#EXTINF:-1 '
            f'tvg-logo="{item["logo"]}" '
            f'group-title="{league}",'
            f' {name}\n'
        )

        content += (
            '#EXTVLCOPT:http-user-agent='
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 '
            '(KHTML, like Gecko) '
            'Chrome/125.0.0.0 '
            'Safari/537.36\n'
        )

        content += (
            f'#EXTVLCOPT:http-referrer={IFRAME_REFERER}\n'
        )

        content += (
            f'#EXTVLCOPT:http-origin={IFRAME_REFERER.rstrip("/")}\n'
        )

        content += item["url"] + "\n\n"

    output = "playlist.m3u"
    with open(output, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"DONE: {len(data)} entries → {output}")


# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":

    all_data = []

    matches = get_match_links()

    live_count = sum(1 for m in matches if m["is_live"])
    print(f"MATCHES: {len(matches)} ({live_count} live, {len(matches)-live_count} upcoming)")
    print(f"WORKERS: {MAX_WORKERS}\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        futures = [
            executor.submit(scrape_match, match)
            for match in matches
        ]

        for future in as_completed(futures):
            try:
                data = future.result()
                if data:
                    all_data.extend(data)
            except Exception as e:
                print(f"THREAD ERROR: {e}")

    write_m3u(all_data)   
