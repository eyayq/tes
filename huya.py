# huya.py
import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FUTBOLX"
M3U_FILE = Path(__file__).parent / "playlist.m3u"
BASE_URL = "https://www.futbol-x.top"
VERCEL_URL = "https://futbolxs.vercel.app"
USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"

SPORTS = [
    "americanfootball", "basketball", "darts", "fights",
    "football", "motorsports", "nhl", "others", "tennis", "wrestling",
]
SPORT_URLS = [urljoin(BASE_URL, f"api/{s}.json") for s in SPORTS]
API_247 = f"{VERCEL_URL}/api/stream-24-7"


def write_m3u() -> None:
    lines: list[str] = ["#EXTM3U"]
    for key, info in urls.items():
        m = re.match(r"^\[(.+?)\]", key)
        group = m.group(1) if m else "Live"
        attrs = f'#EXTINF:-1 tvg-id="{info.get("tvg-id","")}"'
        if info.get("logo"):
            attrs += f' tvg-logo="{info["logo"]}"'
        attrs += f' group-title="{group}"'
        attrs += f",{key}"
        lines.append(attrs)
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(f"#EXTVLCOPT:http-referrer={info.get('refer', BASE_URL)}")
        lines.append(info["source"])
    M3U_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Playlist: {len(urls)} entries")


async def get_sports(client: httpx.AsyncClient) -> None:
    tasks = [client.get(u) for u in SPORT_URLS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    now = datetime.now(timezone.utc)
    ptrn = re.compile(r"^(https?:\/\/)?(www\.)?.*\.m3u8$", re.I)

    for resp in results:
        if isinstance(resp, Exception) or resp.status_code != 200:
            continue
        for event in resp.json().get("streams", []):
            for ev in event.get("streams", []):
                name, sport, starts = ev.get("name"), ev.get("tag"), ev.get("starts_at")
                if not all([name, sport, starts]):
                    continue
                try:
                    ev_dt = datetime.fromisoformat(starts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ev_dt.date() != now.date():
                    continue
                logo = ev.get("logo", "")
                for s in ev.get("streams", []):
                    src = s.get("url", "")
                    if not ptrn.search(src):
                        continue
                    key = f"[{sport}] {name} | {s.get('title','')} ({TAG})"
                    if key in urls:
                        continue
                    urls[key] = {
                        "source": src,
                        "tvg-id": "Live.Event.us",
                        "logo": logo,
                        "refer": BASE_URL,
                    }


async def get_247(client: httpx.AsyncClient) -> None:
    """Scrape 24/7 streams dari vercel.app"""
    resp = await client.get(API_247)
    if resp.status_code != 200:
        log.warning(f"24/7 API failed: {resp.status_code}")
        return

    data = resp.json()
    if not data.get("success") or not data.get("streams"):
        log.warning("24/7 API returned no streams")
        return

    count = 0
    for category, items in data["streams"].items():
        if not isinstance(items, list):
            continue
        for stream in items:
            name = stream.get("name", "")
            uri_name = stream.get("uri_name", "")
            poster = stream.get("poster", "")
            if not name or not uri_name:
                continue

            # Fetch actual stream URL
            ch_url = f"{VERCEL_URL}/api/channel?name={uri_name}"
            try:
                ch_resp = await client.get(ch_url)
                if ch_resp.status_code != 200:
                    continue
                ch_data = ch_resp.json()
                src = ch_data.get("url", "")
            except Exception:
                continue

            if not src:
                continue

            key = f"[{category}] {name} ({TAG})"
            if key in urls:
                continue
            urls[key] = {
                "source": src,
                "tvg-id": "Live.Event.us",
                "logo": poster,
                "refer": f"{VERCEL_URL}/?channel={uri_name}",
            }
            count += 1

    log.info(f"24/7: {count} streams")


async def scrape() -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(
        timeout=20, headers=headers, follow_redirects=True
    ) as client:
        await get_sports(client)
        await get_247(client)

    log.info(f"Total: {len(urls)} events")
    write_m3u()


if __name__ == "__main__":
    asyncio.run(scrape())   
