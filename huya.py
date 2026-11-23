# huya.py
import asyncio
import re
from collections.abc import KeysView
from itertools import chain
from pathlib import Path
from urllib.parse import urljoin

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FUTBOLX"
CACHE_FILE = Cache(TAG, exp=19_800)
M3U_FILE = Path(__file__).parent / "playlist.m3u"

BASE_URL = "https://www.futbol-x.top"

SPORT_URLS = [
    urljoin(BASE_URL, f"api/{sport}.json")
    for sport in [
        "americanfootball",
        "basketball",
        "darts",
        "fights",
        "football",
        "motorsports",
        "nhl",
        "others",
        "tennis",
        "wrestling",
    ]
]


def write_m3u() -> None:
    lines: list[str] = ["#EXTM3U"]
    for key, info in urls.items():
        source = info["source"]
        tvg_id = info.get("tvg-id", "Live.Event.us")
        logo = info.get("logo", "")
        refer = info.get("refer", "")
        m = re.match(r"^\[(.+?)\]", key)
        group = m.group(1) if m else "Live"

        attrs = f'#EXTINF:-1 tvg-id="{tvg_id}"'
        if logo:
            attrs += f' tvg-logo="{logo}"'
        attrs += f' group-title="{group}"'
        if refer:
            attrs += f' http-referrer="{refer}"'
        attrs += f",{key}"

        lines.append(attrs)
        lines.append(source)

    M3U_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Playlist written: {len(urls)} entries")


async def get_events(
    cached_keys: KeysView[str],
) -> dict[str, dict[str, str | float]]:
    events: dict[str, dict[str, str | float]] = {}
    tasks = [network.request(url, log=log) for url in SPORT_URLS]
    results = await asyncio.gather(*tasks)

    if not (
        api_data := [
            *chain.from_iterable(
                r.json().get("streams", {}) for r in results if r
            )
        ]
    ):
        return events

    now = Time.rn()
    ptrn = re.compile(r"^(https?:\/\/)?(www\.)?.*\.m3u8$", re.I)

    for event in api_data:
        if not (streams := event.get("streams")):
            continue
        for event_info in streams:
            if not all(
                values := [
                    event_info.get(k)
                    for k in ("name", "tag", "starts_at", "streams")
                ]
            ):
                continue
            name, sport, event_time, event_streams = values
            event_dt = Time.from_str(event_time, tz_name="MSK")
            if event_dt.date() != now.date():
                continue
            for stream_info in event_streams:
                if not (source := stream_info.get("url")):
                    continue
                elif not ptrn.search(source):
                    continue
                url_title = stream_info["title"]
                if (
                    key := f"[{sport}] {name} | {url_title} ({TAG})"
                ) in cached_keys:
                    continue
                tvg_id, logo = leagues.get_tvg_info(sport, name)
                events[key] = {
                    "source": source,
                    "logo": logo,
                    "refer": BASE_URL,
                    "timestamp": now.timestamp(),
                    "tvg-id": tvg_id or "Live.Event.us",
                }
    return events


async def scrape() -> None:
    cached_sources = CACHE_FILE.load()
    valid_count = len(
        valid_sources := {
            k: v for k, v in cached_sources.items() if v["source"]
        }
    )
    urls.update(valid_sources)
    log.info(f"Loaded {valid_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')
    urls.update(await get_events(cached_sources.keys()))

    new_count = len(urls) - valid_count
    if new_count:
        log.info(f"Collected {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(urls)
    write_m3u()


if __name__ == "__main__":
    asyncio.run(scrape())   
