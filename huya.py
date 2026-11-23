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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
PROXY = "http://svr00000:bhns0000@55.223.779.55:6641"

SPORTS = [
    "americanfootball", "basketball", "darts", "fights",
    "football", "motorsports", "nhl", "others", "tennis", "wrestling",
]
SPORT_URLS = [urljoin(BASE_URL, f"api/{s}.json") for s in SPORTS]
API_247 = urljoin(BASE_URL, "api/stream-24-7")

STREAM_RE = re.compile(r"^(https?:\/\/)?(www\.)?.*\.(m3u8|ts)(\?.*)?$", re.I)


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
        lines.append(f"#EXTVLCOPT:http-referrer={BASE_URL}")
        lines.append(info["source"])
    M3U_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Playlist: {len(urls)} entries")


async def get_sports(client: httpx.AsyncClient) -> None:
    tasks = [client.get(u) for u in SPORT_URLS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    now = datetime.now(timezone.utc)

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
                    if not STREAM_RE.search(src):
                        continue
                    key = f"[{sport}] {name} | {s.get('title','')} ({TAG})"
                    if key in urls:
                        continue
                    urls[key] = {"source": src, "tvg-id": "Live.Event.us", "logo": logo}

    log.info(f"Sports: {len(urls)} events")


async def get_247(client: httpx.AsyncClient) -> None:
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
            poster = stream.get("poster", "")
            if not name:
                continue

            # Stream URLs ada di stream["streams"] (array of {url, label})
            for s in stream.get("streams", []):
                src = s.get("url", "")
                if not STREAM_RE.search(src):
                    continue
                label = s.get("label", "")
                key = f"[{category}] {name} | {label} ({TAG})" if label else f"[{category}] {name} ({TAG})"
                if key in urls:
                    continue
                urls[key] = {"source": src, "tvg-id": "Live.Event.us", "logo": poster}
                count += 1

    log.info(f"24/7: {count} streams")


async def scrape() -> None:
    headers = {"User-Agent": USER_AGENT, "Referer": BASE_URL}
    async with httpx.AsyncClient(
        timeout=30,
        headers=headers,
        follow_redirects=True,
        proxy=PROXY,
    ) as client:
        await get_sports(client)
        await get_247(client)

    log.info(f"Total: {len(urls)} events")
    write_m3u()


if __name__ == "__main__":
    asyncio.run(scrape())   
