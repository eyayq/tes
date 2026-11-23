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
API_247 = urljoin(BASE_URL, "api/stream-24-7")


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
        lines.append(f"#EXTVLCOPT:http-referrer={info.get('refer','')}")
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
    resp = await client.get(API_247)
    if resp.status_code != 200:
        log.warning(f"24/7 list failed: {resp.status_code}")
        return

    data = resp.json()
    streams = data.get("streams", data) if isinstance(data, dict) else {}
    if not streams:
        log.warning("24/7: no streams in response")
        return

    count = 0
    for category, items in streams.items():
        if not isinstance(items, list):
            continue
        for stream in items:
            name = stream.get("name", "")
            slug = stream.get("uri_name") or stream.get("slug") or stream.get("name", "").lower().replace(" ", "-")
            poster = stream.get("poster", "")
            if not name or not slug:
                continue

            ch_url = f"{VERCEL_URL}/api/channel?name={slug}"
            try:
                ch_resp = await client.get(ch_url)
                if ch_resp.status_code != 200:
                    continue
                src = ch_resp.json().get("url", "")
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
                "refer": f"{VERCEL_URL}/?channel={slug}",
            }
            count += 1

    log.info(f"24/7: {count} streams")


async def scrape() -> None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        await get_sports(client)
        await get_247(client)

    log.info(f"Total: {len(urls)} events")
    write_m3u()


if __name__ == "__main__":
    asyncio.run(scrape())   
