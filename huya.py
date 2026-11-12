#!/usr/bin/env python3

import requests
import re
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "http://202.80.222.20"
CDN_PATH = "/cdn/iptv/Tvod/001"
CHANNEL_PATTERN = "channel2000{:03d}"
ID_START = 1
ID_END = 250
THREADS = 20
TIMEOUT = 5
OUTPUT_FILE = "playlist.m3u"
LOG_FILE = "scan_log.txt"

HEADERS = {
    "User-Agent": "IndiHomeTV/8.7.12 (Linux;Android 15.0.0;) ExoPlayerLib/2.19.1",
    "Referer": "https://www.indihometv.com",
    "Accept": "*/*",
}

# ============================================================
# KNOWN CHANNEL DATABASE
# ============================================================
KNOWN_CHANNELS = {
    1:   {"name": "TVRI", "group": "TV Lokal"},
    9:   {"name": "TRANS TV", "group": "TV Lokal"},
    10:  {"name": "TRANS 7", "group": "TV Lokal"},
    22:  {"name": "SCTV", "group": "TV Lokal"},
    23:  {"name": "ANTV", "group": "TV Lokal"},
    24:  {"name": "INDOSIAR", "group": "TV Lokal"},
    25:  {"name": "MNCTV", "group": "TV Lokal"},
    26:  {"name": "GTV", "group": "TV Lokal"},
    27:  {"name": "METRO TV", "group": "TV Lokal"},
    28:  {"name": "CNN INDONESIA", "group": "TV Lokal"},
    29:  {"name": "TV ONE", "group": "TV Lokal"},
    30:  {"name": "KOMPAS TV", "group": "TV Lokal"},
    31:  {"name": "iNEWS", "group": "TV Lokal"},
    32:  {"name": "NET.", "group": "TV Lokal"},
    34:  {"name": "CHANNEL 34", "group": "TV Lokal"},  # <-- TAMBAHAN
    106: {"name": "MNCTV", "group": "TV Lokal"},
    113: {"name": "GTV", "group": "TV Lokal"},
    163: {"name": "FIGHT SPORTS", "group": "Olahraga"},
}

# ============================================================
# GROUP INFERENCE
# ============================================================
def infer_group(channel_id):
    if channel_id <= 50:
        return "TV Lokal"
    elif channel_id <= 80:
        return "News"
    elif channel_id <= 120:
        return "Movies"
    elif channel_id <= 150:
        return "Olahraga"
    elif channel_id <= 170:
        return "Kids"
    elif channel_id <= 190:
        return "Lifestyle"
    else:
        return "Lainnya"

# ============================================================
# LOGGING
# ============================================================
def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

# ============================================================
# STEP 1: PROBE ENDPOINTS
# ============================================================
def probe_endpoints():
    log("=" * 50)
    log("STEP 1: Probing endpoints...")
    log("=" * 50)

    endpoints = [
        "/playlist.m3u8", "/playlist.m3u", "/channels.m3u8",
        "/channels.m3u", "/all.m3u8", "/index.m3u8", "/master.m3u8",
        "/live/index.m3u8", "/live/master.m3u8",
        "/iptv/playlist.m3u8", "/cdn/iptv/playlist.m3u8",
        "/cdn/iptv/index.m3u8", "/cdn/iptv/Tvod/playlist.m3u8",
        "/cdn/iptv/Tvod/001/playlist.m3u8",
        "/cdn/iptv/Tvod/001/index.m3u8",
        "/cdn/iptv/Tvod/001/master.m3u8",
        "/channels.json", "/api/channels", "/api/channels.json",
        "/api/v1/channels", "/api/playlist", "/api/vod", "/api/live",
        "/cdn/iptv/channels.json", "/cdn/iptv/api/channels",
        "/cdn/iptv/Tvod/001/channels.json",
        "/channels.xml", "/api/channels.xml", "/epg.xml", "/guide.xml",
        "/get.php?type=m3u_plus", "/player_api.php", "/panel_api.php",
        "/status", "/health", "/info", "/version", "/robots.txt",
    ]

    found = {}
    for ep in endpoints:
        url = BASE_URL + ep
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 10:
                content_type = r.headers.get("Content-Type", "")
                log(f"  [HIT] {ep} -> {r.status_code} ({content_type}, {len(r.content)} bytes)")
                found[ep] = {
                    "url": url,
                    "status": r.status_code,
                    "content_type": content_type,
                    "content": r.text[:5000],
                }
            elif r.status_code != 404:
                log(f"  [{r.status_code}] {ep}")
        except requests.exceptions.ConnectionError:
            log(f"  [CONN ERR] {ep}")
            break
        except Exception as e:
            log(f"  [ERR] {ep}: {e}")

    if found:
        with open("probed_endpoints.json", "w") as f:
            json.dump(found, f, indent=2)
        log(f"  -> Saved {len(found)} endpoints")
    else:
        log("  -> No endpoints found")

    return found

# ============================================================
# STEP 2: BRUTE-FORCE
# ============================================================
def check_channel(channel_id):
    cid = CHANNEL_PATTERN.format(channel_id)
    url = f"{BASE_URL}{CDN_PATH}/{cid}/1024.m3u8"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            name_match = re.search(r'#EXT-X-DEFINE.*?NAME="([^"]+)"', r.text)
            stream_name = name_match.group(1) if name_match else None
            return {
                "id": channel_id,
                "cid": cid,
                "url": url,
                "status": "OK",
                "stream_name": stream_name,
                "content_length": len(r.text),
            }
    except:
        pass
    return {"id": channel_id, "cid": cid, "url": url, "status": "FAIL"}

def brute_force_channels():
    log("=" * 50)
    log(f"STEP 2: Brute-forcing channels {ID_START}-{ID_END}...")
    log("=" * 50)

    results = []
    ok_count = 0

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {
            executor.submit(check_channel, i): i
            for i in range(ID_START, ID_END + 1)
        }
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            if res["status"] == "OK":
                ok_count += 1
                log(f"  [FOUND] {res['cid']} -> {res['stream_name'] or 'unknown'}")

    results.sort(key=lambda x: x["id"])
    valid = [r for r in results if r["status"] == "OK"]
    log(f"  -> {ok_count}/{ID_END} channels found")

    with open("brute_force_results.json", "w") as f:
        json.dump(valid, f, indent=2)

    return valid

# ============================================================
# STEP 3: ENRICH
# ============================================================
def enrich_channels(valid_channels):
    log("=" * 50)
    log("STEP 3: Enriching with metadata...")
    log("=" * 50)

    enriched = []
    for ch in valid_channels:
        cid_num = ch["id"]
        info = KNOWN_CHANNELS.get(cid_num, {})

        name = info.get("name") or ch.get("stream_name") or f"Channel {cid_num}"
        group = info.get("group") or infer_group(cid_num)

        enriched.append({
            "id": cid_num,
            "cid": ch["cid"],
            "url": ch["url"],
            "name": name,
            "group": group,
            "tvg_id": f"Channel{cid_num}.id",
        })

    log(f"  -> Enriched {len(enriched)} channels")
    return enriched

# ============================================================
# STEP 4: SCAN M3U8 CONTENT
# ============================================================
def try_discover_from_m3u8(valid_channels):
    log("=" * 50)
    log("STEP 4: Scanning m3u8 content for metadata...")
    log("=" * 50)

    metadata_found = {}
    for ch in valid_channels[:20]:
        try:
            r = requests.get(ch["url"], headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            content = r.text
            defines = re.findall(r'#EXT-X-DEFINE:([^;]+);([^;]+)', content)
            urls = re.findall(r'https?://[^\s"\']+\.m3u8', content)
            names = re.findall(r'NAME="([^"]+)"', content)
            titles = re.findall(r'TITLE="([^"]+)"', content)

            if defines or urls or names or titles:
                metadata_found[ch["cid"]] = {
                    "defines": defines,
                    "urls": urls,
                    "names": names,
                    "titles": titles,
                    "raw_first_500": content[:500],
                }
                log(f"  [META] {ch['cid']}: names={names}, titles={titles}")
        except:
            pass

    if metadata_found:
        with open("m3u8_metadata.json", "w") as f:
            json.dump(metadata_found, f, indent=2)
        log(f"  -> Found metadata in {len(metadata_found)} channels")
    else:
        log("  -> No embedded metadata found")

    return metadata_found

# ============================================================
# STEP 5: GENERATE M3U (TANPA LOGO)
# ============================================================
def generate_m3u(enriched_channels):
    log("=" * 50)
    log(f"STEP 5: Generating {OUTPUT_FILE}...")
    log("=" * 50)

    lines = ['#EXTM3U', '']

    enriched_channels.sort(key=lambda x: (x["group"], x["name"]))

    for ch in enriched_channels:
        # TANPA tvg-logo, hanya tvg-id dan group-title
        attrs = f'tvg-id="{ch["tvg_id"]}"'
        attrs += f' group-title="{ch["group"]}"'

        lines.append(f'#EXTINF:-1 {attrs},{ch["name"]}')
        lines.append(ch["url"])
        lines.append('')

    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(lines))

    log(f"  -> Written {len(enriched_channels)} channels to {OUTPUT_FILE}")

    with open("channels.json", "w") as f:
        json.dump(enriched_channels, f, indent=2)

# ============================================================
# STEP 6: COMMUNITY METADATA
# ============================================================
def fetch_community_metadata():
    log("=" * 50)
    log("STEP 6: Fetching community playlists...")
    log("=" * 50)

    community_urls = [
        "https://gist.githubusercontent.com/rizaldypaparang/531584eb3d0367f4129d8218948b75a3/raw",
    ]

    community_data = {}
    for url in community_urls:
        try:
            log(f"  Fetching: {url[:60]}...")
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                lines = r.text.split("\n")
                for i, line in enumerate(lines):
                    if "202.80.222.20" in line and "channel2000" in line:
                        extinf = ""
                        for j in range(i - 1, max(0, i - 5), -1):
                            if lines[j].startswith("#EXTINF"):
                                extinf = lines[j]
                                break

                        name_m = re.search(r',(.+)$', extinf)
                        group_m = re.search(r'group-title="([^"]+)"', extinf)
                        id_m = re.search(r'channel(\d+)', line)

                        if id_m:
                            cid = int(id_m.group(1))
                            community_data[cid] = {
                                "name": name_m.group(1).strip() if name_m else None,
                                "group": group_m.group(1) if group_m else None,
                                "url": line.strip(),
                            }
                log(f"  -> Parsed {len(community_data)} channels")
                break
        except Exception as e:
            log(f"  [ERR] {url[:50]}: {e}")

    if community_data:
        with open("community_metadata.json", "w") as f:
            json.dump(community_data, f, indent=2)

    return community_data

# ============================================================
# MERGE
# ============================================================
def merge_metadata(enriched, community_data):
    log("=" * 50)
    log("MERGING community metadata...")
    log("=" * 50)

    updated = 0
    for ch in enriched:
        cid = ch["id"]
        if cid in community_data:
            cd = community_data[cid]
            if cd.get("name"):
                ch["name"] = cd["name"]
            if cd.get("group"):
                ch["group"] = cd["group"]
            updated += 1

    log(f"  -> Updated {updated} channels")
    return enriched

# ============================================================
# MAIN
# ============================================================
def main():
    open(LOG_FILE, "w").close()
    log("IndiHome TV Channel Scanner - START")

    probed = probe_endpoints()

    valid = brute_force_channels()
    if not valid:
        log("No channels found! Exiting.")
        sys.exit(1)

    enriched = enrich_channels(valid)

    m3u8_meta = try_discover_from_m3u8(valid)

    generate_m3u(enriched)

    community = fetch_community_metadata()
    if community:
        enriched = merge_metadata(enriched, community)
        generate_m3u(enriched)

    log("=" * 50)
    log("DONE! Output: " + OUTPUT_FILE)
    log("=" * 50)

if __name__ == "__main__":
    main()   
