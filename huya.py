#!/usr/bin/env python3
"""
jangan di p
"""

import requests
import re
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "http://202.80.222.20"
CDN_PATH = "/cdn/iptv/Tvod/001"
CHANNEL_PATTERN = "channel2000{:03d}"
ID_START = 1
ID_END = 250  # adjust as needed
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
# KNOWN CHANNEL DATABASE (cross-reference for logo & group)
# From community playlists & iptv-org
# ============================================================
KNOWN_CHANNELS = {
    1:   {"name": "TVRI", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/TVRI.jpg"},
    9:   {"name": "TRANS TV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/TransTV.jpg"},
    10:  {"name": "TRANS 7", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/Trans7.jpg"},
    22:  {"name": "SCTV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/SCTV.jpg"},
    23:  {"name": "ANTV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/ANTV.jpg"},
    24:  {"name": "INDOSIAR", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/Indosiar.jpg"},
    25:  {"name": "MNC TV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/MNCTV.jpg"},
    26:  {"name": "GTV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/GTV.jpg"},
    27:  {"name": "METRO TV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/MetroTV.jpg"},
    28:  {"name": "CNN INDONESIA", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/CNNIndonesia.jpg"},
    29:  {"name": "TV ONE", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/TVOne.jpg"},
    30:  {"name": "KOMPAS TV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/KompasTV.jpg"},
    31:  {"name": "iNEWS", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/INews.jpg"},
    32:  {"name": "NET.", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/NetTV.jpg"},
    106: {"name": "MNCTV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/MNCTV.jpg"},
    113: {"name": "GTV", "group": "TV Lokal", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/GTV.jpg"},
    163: {"name": "FIGHT SPORTS", "group": "Olahraga", "logo": "https://raw.githubusercontent.com/IPTV-Digital/Channel/main/Logo%20IPTV/FightSports.jpg"},
}

# Group inference by ID range (heuristic)
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
    """Try common playlist/API endpoints on the server."""
    log("=" * 50)
    log("STEP 1: Probing endpoints...")
    log("=" * 50)

    endpoints = [
        # Playlist patterns
        "/playlist.m3u8",
        "/playlist.m3u",
        "/channels.m3u8",
        "/channels.m3u",
        "/all.m3u8",
        "/index.m3u8",
        "/master.m3u8",
        "/live/index.m3u8",
        "/live/master.m3u8",
        "/iptv/playlist.m3u8",
        "/cdn/iptv/playlist.m3u8",
        "/cdn/iptv/index.m3u8",
        "/cdn/iptv/Tvod/playlist.m3u8",
        "/cdn/iptv/Tvod/001/playlist.m3u8",
        "/cdn/iptv/Tvod/001/index.m3u8",
        "/cdn/iptv/Tvod/001/master.m3u8",
        # JSON patterns
        "/channels.json",
        "/api/channels",
        "/api/channels.json",
        "/api/v1/channels",
        "/api/playlist",
        "/api/vod",
        "/api/live",
        "/cdn/iptv/channels.json",
        "/cdn/iptv/api/channels",
        "/cdn/iptv/Tvod/001/channels.json",
        "/cdn/iptv/Tvod/channels.json",
        "/iptv/channels.json",
        "/iptv/api/channels",
        # XML patterns
        "/channels.xml",
        "/api/channels.xml",
        "/epg.xml",
        "/guide.xml",
        "/tv_guide.xml",
        # Xtream-like
        "/get.php?type=m3u_plus",
        "/player_api.php",
        "/panel_api.php",
        "/c/",
        # Misc
        "/status",
        "/health",
        "/info",
        "/version",
        "/robots.txt",
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
                    "content": r.text[:5000],  # store first 5KB
                }
            elif r.status_code != 404:
                log(f"  [{r.status_code}] {ep}")
        except requests.exceptions.ConnectionError:
            log(f"  [CONN ERR] {ep}")
            break
        except Exception as e:
            log(f"  [ERR] {ep}: {e}")

    # Save raw findings
    if found:
        with open("probed_endpoints.json", "w") as f:
            json.dump(found, f, indent=2)
        log(f"  -> Saved {len(found)} endpoints to probed_endpoints.json")
    else:
        log("  -> No endpoints found (expected for this CDN)")

    return found

# ============================================================
# STEP 2: BRUTE-FORCE CHANNEL IDS
# ============================================================
def check_channel(channel_id):
    """Check if a channel ID exists by fetching its m3u8."""
    cid = CHANNEL_PATTERN.format(channel_id)
    url = f"{BASE_URL}{CDN_PATH}/{cid}/1024.m3u8"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            # Try to extract stream info
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

    # Sort by ID
    results.sort(key=lambda x: x["id"])
    valid = [r for r in results if r["status"] == "OK"]
    log(f"  -> {ok_count}/{ID_END} channels found")

    with open("brute_force_results.json", "w") as f:
        json.dump(valid, f, indent=2)

    return valid

# ============================================================
# STEP 3: ENRICH WITH METADATA
# ============================================================
def enrich_channels(valid_channels):
    """Add tvg-logo, group-title, tvg-id from known DB + heuristics."""
    log("=" * 50)
    log("STEP 3: Enriching with metadata...")
    log("=" * 50)

    enriched = []
    for ch in valid_channels:
        cid_num = ch["id"]
        info = KNOWN_CHANNELS.get(cid_num, {})

        name = info.get("name") or ch.get("stream_name") or f"Channel {cid_num}"
        group = info.get("group") or infer_group(cid_num)
        logo = info.get("logo", "")

        enriched.append({
            "id": cid_num,
            "cid": ch["cid"],
            "url": ch["url"],
            "name": name,
            "group": group,
            "logo": logo,
            "tvg_id": f"Channel{cid_num}.id",
        })

    log(f"  -> Enriched {len(enriched)} channels")
    return enriched

# ============================================================
# STEP 4: TRY TO DISCOVER PLAYLIST FROM M3U8 CONTENT
# ============================================================
def try_discover_from_m3u8(valid_channels):
    """Parse m3u8 content for any embedded metadata or playlist refs."""
    log("=" * 50)
    log("STEP 4: Scanning m3u8 content for metadata...")
    log("=" * 50)

    metadata_found = {}
    for ch in valid_channels[:20]:  # limit to 20 for speed
        try:
            r = requests.get(ch["url"], headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            content = r.text

            # Look for EXT-X-DEFINE
            defines = re.findall(r'#EXT-X-DEFINE:([^;]+);([^;]+)', content)
            # Look for any URL references
            urls = re.findall(r'https?://[^\s"\']+\.m3u8', content)
            # Look for name/title
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

        except Exception as e:
            pass

    if metadata_found:
        with open("m3u8_metadata.json", "w") as f:
            json.dump(metadata_found, f, indent=2)
        log(f"  -> Found metadata in {len(metadata_found)} channels")
    else:
        log("  -> No embedded metadata found in m3u8 files")

    return metadata_found

# ============================================================
# STEP 5: GENERATE M3U OUTPUT
# ============================================================
def generate_m3u(enriched_channels):
    log("=" * 50)
    log(f"STEP 5: Generating {OUTPUT_FILE}...")
    log("=" * 50)

    lines = ['#EXTM3U', '']

    # Sort by group then name
    enriched_channels.sort(key=lambda x: (x["group"], x["name"]))

    for ch in enriched_channels:
        attrs = f'tvg-id="{ch["tvg_id"]}"'
        if ch["logo"]:
            attrs += f' tvg-logo="{ch["logo"]}"'
        attrs += f' group-title="{ch["group"]}"'

        lines.append(f'#EXTINF:-1 {attrs},{ch["name"]}')
        lines.append(ch["url"])
        lines.append('')

    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(lines))

    log(f"  -> Written {len(enriched_channels)} channels to {OUTPUT_FILE}")

    # Also output JSON
    with open("channels.json", "w") as f:
        json.dump(enriched_channels, f, indent=2)
    log(f"  -> Also saved channels.json")

# ============================================================
# STEP 6: TRY COMMUNITY PLAYLISTS (fallback for metadata)
# ============================================================
def fetch_community_metadata():
    """Try to fetch community playlists for cross-referencing."""
    log("=" * 50)
    log("STEP 6: Fetching community playlists for metadata...")
    log("=" * 50)

    community_urls = [
        "https://gist.githubusercontent.com/R03nDL03n1/e3f461a6e39f8bdc3e9bf96df638fd44/raw/SmartTVPlaylist.m3u8",
        "https://gist.githubusercontent.com/rizaldypaparang/531584eb3d0367f4129d8218948b75a3/raw",
    ]

    community_data = {}
    for url in community_urls:
        try:
            log(f"  Fetching: {url[:60]}...")
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                # Parse M3U for channels matching our server
                lines = r.text.split("\n")
                for i, line in enumerate(lines):
                    if "202.80.222.20" in line and "channel2000" in line:
                        # Find the EXTINF line above
                        extinf = ""
                        for j in range(i - 1, max(0, i - 5), -1):
                            if lines[j].startswith("#EXTINF"):
                                extinf = lines[j]
                                break

                        # Extract metadata
                        name_m = re.search(r',(.+)$', extinf)
                        logo_m = re.search(r'tvg-logo="([^"]+)"', extinf)
                        group_m = re.search(r'group-title="([^"]+)"', extinf)
                        id_m = re.search(r'channel(\d+)', line)

                        if id_m:
                            cid = int(id_m.group(1))
                            community_data[cid] = {
                                "name": name_m.group(1).strip() if name_m else None,
                                "logo": logo_m.group(1) if logo_m else None,
                                "group": group_m.group(1) if group_m else None,
                                "url": line.strip(),
                            }
                log(f"  -> Parsed {len(community_data)} channels from community playlists")
                break
        except Exception as e:
            log(f"  [ERR] {url[:50]}: {e}")

    if community_data:
        with open("community_metadata.json", "w") as f:
            json.dump(community_data, f, indent=2)

    return community_data

# ============================================================
# MERGE & REGENERATE
# ============================================================
def merge_metadata(enriched, community_data):
    """Merge community metadata into enriched channels."""
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
            if cd.get("logo"):
                ch["logo"] = cd["logo"]
            if cd.get("group"):
                ch["group"] = cd["group"]
            updated += 1

    log(f"  -> Updated {updated} channels with community metadata")
    return enriched

# ============================================================
# MAIN
# ============================================================
def main():
    # Clear log
    open(LOG_FILE, "w").close()
    log("IndiHome TV Channel Scanner - START")

    # Step 1: Probe endpoints
    probed = probe_endpoints()

    # Step 2: Brute-force
    valid = brute_force_channels()
    if not valid:
        log("No channels found! Exiting.")
        sys.exit(1)

    # Step 3: Enrich
    enriched = enrich_channels(valid)

    # Step 4: Scan m3u8 content
    m3u8_meta = try_discover_from_m3u8(valid)

    # Step 5: Generate initial M3U
    generate_m3u(enriched)

    # Step 6: Fetch community metadata
    community = fetch_community_metadata()
    if community:
        enriched = merge_metadata(enriched, community)
        # Regenerate with merged data
        generate_m3u(enriched)

    log("=" * 50)
    log("DONE! Output: " + OUTPUT_FILE)
    log("=" * 50)

if __name__ == "__main__":
    main()   
