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

SPORTS = [
    "americanfootball", "basketball", "darts", "fights",
    "football", "motorsports", "nhl", "others", "tennis", "wrestling",
]
SPORT_URLS = [urljoin(BASE_URL, f"api/{s}.json") for s in SPORTS]


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
        lines.append(info["source"])
    M3U_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Playlist: {len(urls)} entries")


async def scrape() -> None:
    async with httpx.AsyncClient(timeout=15) as client:
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
                    }

    log.info(f"Collected {len(urls)} events")
    write_m3u()


if __name__ == "__main__":
    asyncio.run(scrape())   
