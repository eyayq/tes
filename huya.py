#!/usr/bin/env python3
"""
Vidio IPTV Playlist Generator
Scrape channel live dari vidio.com dan generate playlist.m3u
dengan format: #EXTINF:-1 tvg-id="..." tvg-logo="..." group-title="..."
"""

import json
import re
import time
import logging
import requests
from base64 import b64encode
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Konfigurasi ───────────────────────────────────────────────────────────────
OUTPUT_FILE = "playlist.m3u"
MAX_WORKERS = 5          # jumlah thread untuk fetch stream URL
REQUEST_TIMEOUT = 15     # timeout per request (detik)
STREAM_URL_EXPIRY = 120  # URL stream expire setelah ~120 detik (sesuai API)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vidio-m3u")

# ─── Konstanta API ─────────────────────────────────────────────────────────────
API_AUTH = "https://api.vidio.com/auth"
API_LIVESTREAMINGS_DETAIL = "https://api.vidio.com/api/livestreamings/{streamid}/detail"
API_LIVESTREAMINGS_STREAM = "https://api.vidio.com/livestreamings/{streamid}/stream?initialize=true"
PAGE_LIVE = "https://www.vidio.com/live"

API_KEY_PUBKEY = b"dPr0QImQ7bc5o9LMntNba2DOsSbZcjUh"
API_KEY_IV = b"C8RWsrtFsoeyCyPt"

# ─── Dataclass ─────────────────────────────────────────────────────────────────
@dataclass
class Channel:
    streamid: str
    name: str
    logo: str = ""
    group: str = "Vidio"
    stream_url: str = ""


# ─── Vidio API Client ──────────────────────────────────────────────────────────
class VidioClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Origin": "https://www.vidio.com",
            "Referer": "https://www.vidio.com/",
            "Accept": "application/json, text/plain, */*",
        })
        self._api_key: Optional[str] = None
        self._signature: Optional[tuple[str, str]] = None

    def _get_signature(self, page_url: str = PAGE_LIVE) -> tuple[str, str]:
        """Ekstrak streamSignature (clientId + signature) dari halaman Vidio."""
        if self._signature:
            return self._signature

        resp = self.session.get(page_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        html = resp.text

        # Cari pattern: "streamSignature":{"clientId":"...","signature":"..."}
        pattern = r'"streamSignature"\s*:\s*\{\s*"clientId"\s*:\s*"([^"]+)"\s*,\s*"signature"\s*:\s*"([^"]+)"'
        match = re.search(pattern, html)
        if not match:
            # Fallback: coba cari di script tag
            pattern2 = r'streamSignature[^}]*clientId[^"]*"?([a-zA-Z0-9_-]+)"?[^}]*signature[^"]*"?([a-zA-Z0-9_-]+)"?'
            match = re.search(pattern2, html)

        if not match:
            raise RuntimeError("Gagal menemukan streamSignature di halaman Vidio")

        self._signature = (match.group(1), match.group(2))
        log.info(f"Signature ditemukan: clientId={self._signature[0][:8]}...")
        return self._signature

    def _get_api_key(self) -> str:
        """Dapatkan dan enkripsi API key."""
        if self._api_key:
            return self._api_key

        resp = self.session.post(API_AUTH, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        api_key = data["api_key"]

        # Enkripsi dengan AES-CBC (sama seperti streamlink plugin)
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad
        except ImportError:
            # Fallback: pakai pycryptodome atau plain
            try:
                from Cryptodome.Cipher import AES
                from Cryptodome.Util.Padding import pad
            except ImportError:
                log.warning("pycryptodome tidak terpasang, menggunakan API key mentah")
                self._api_key = api_key
                return self._api_key

        cipher = AES.new(API_KEY_PUBKEY, AES.MODE_CBC, API_KEY_IV)
        padded = pad(api_key.encode("utf-8"), AES.block_size)
        encrypted = cipher.encrypt(padded)
        self._api_key = b64encode(encrypted).decode("utf-8")
        log.info("API key berhasil di-enkripsi")
        return self._api_key

    def _query_api(self, url: str) -> dict:
        """Query API Vidio dengan header autentikasi."""
        client_id, signature = self._get_signature()
        api_key = self._get_api_key()

        headers = {
            "x-api-key": api_key,
            "x-api-platform": "web-desktop",
            "x-client": client_id,
            "x-secure-level": "2",
            "x-signature": signature,
        }

        resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def get_stream_url(self, streamid: str) -> Optional[str]:
        """Dapatkan URL HLS stream untuk channel tertentu."""
        try:
            url = API_LIVESTREAMINGS_STREAM.format(streamid=streamid)
            data = self._query_api(url)
            hls_url = data.get("data", {}).get("attributes", {}).get("hls")
            if hls_url:
                return hls_url
        except Exception as e:
            log.debug(f"Gagal get stream URL untuk {streamid}: {e}")
        return None

    def get_channel_detail(self, streamid: str) -> dict:
        """Dapatkan detail channel (logo, nama, kategori)."""
        try:
            url = API_LIVESTREAMINGS_DETAIL.format(streamid=streamid)
            data = self._query_api(url)
            return data.get("data", {})
        except Exception as e:
            log.debug(f"Gagal get detail untuk {streamid}: {e}")
            return {}


# ─── Scraper Halaman Live ─────────────────────────────────────────────────────
def scrape_live_page(client: VidioClient) -> list[Channel]:
    """Scrape halaman vidio.com/live untuk mendapatkan daftar channel."""
    log.info("Meng-scrape halaman live Vidio...")
    resp = client.session.get(PAGE_LIVE, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    channels = []

    # ── Strategi 1: Cari JSON ter-embed di halaman (Next.js __NEXT_DATA__) ──
    next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if next_data_match:
        try:
            next_data = json.loads(next_data_match.group(1))
            # Jelajahi struktur untuk menemukan daftar livestreaming
            channels = _extract_from_next_data(next_data)
            if channels:
                log.info(f"Ditemukan {len(channels)} channel dari __NEXT_DATA__")
                return channels
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Strategi 2: Cari pattern streamid dari link di HTML ──
    # Pattern: /live/12345-nama-channel
    link_pattern = r'href="[^"]*/live/(\d+)-([^"/]+)'
    matches = re.findall(link_pattern, html)
    seen_ids = set()
    for streamid, slug in matches:
        if streamid not in seen_ids:
            seen_ids.add(streamid)
            name = slug.replace("-", " ").strip()
            channels.append(Channel(
                streamid=streamid,
                name=name,
                group="Vidio",
            ))

    if channels:
        log.info(f"Ditemukan {len(channels)} channel dari link pattern")
    else:
        log.warning("Tidak ada channel ditemukan dari scraping HTML")

    return channels


def _extract_from_next_data(data: dict) -> list[Channel]:
    """Ekstrak channel dari __NEXT_DATA__ structure."""
    channels = []

    def find_livestreamings(obj, path=""):
        """Recursively cari key 'livestreamings' atau 'liveChannels' di JSON."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("livestreamings", "liveChannels", "live_channels", "items") and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            ch = _parse_channel_item(item)
                            if ch:
                                channels.append(ch)
                else:
                    find_livestreamings(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                find_livestreamings(item, f"{path}[{i}]")

    find_livestreamings(data)
    return channels


def _parse_channel_item(item: dict) -> Optional[Channel]:
    """Parse satu item channel dari JSON."""
    # Berbagai kemungkinan struktur
    streamid = item.get("id") or item.get("streamId") or item.get("stream_id")
    if not streamid:
        attrs = item.get("attributes", {})
        streamid = attrs.get("id") or attrs.get("streamId")
    if not streamid:
        return None

    streamid = str(streamid)

    # Nama
    name = item.get("name") or item.get("title") or ""
    if not name:
        attrs = item.get("attributes", {})
        name = attrs.get("name") or attrs.get("title") or ""

    # Logo
    logo = item.get("logo") or item.get("thumbnail") or item.get("image") or ""
    if not logo:
        attrs = item.get("attributes", {})
        logo = attrs.get("logo") or attrs.get("thumbnail") or attrs.get("image") or ""
        # Atau dari relationships
        rels = item.get("relationships", {})
        if "logo" in rels:
            logo_data = rels["logo"].get("data", {})
            logo = logo_data.get("attributes", {}).get("url", "")

    # Group / Kategori
    group = item.get("category") or item.get("genre") or item.get("group") or "Vidio"
    if not group or group == "Vidio":
        attrs = item.get("attributes", {})
        group = attrs.get("category") or attrs.get("genre") or "Vidio"

    if not name:
        return None

    return Channel(
        streamid=streamid,
        name=name,
        logo=logo,
        group=group,
    )


# ─── Fetch Stream URLs (parallel) ─────────────────────────────────────────────
def fetch_stream_urls(channels: list[Channel], client: VidioClient) -> list[Channel]:
    """Fetch stream URL untuk semua channel secara parallel."""
    log.info(f"Fetching stream URL untuk {len(channels)} channel...")

    def _fetch(ch: Channel) -> Channel:
        url = client.get_stream_url(ch.streamid)
        if url:
            ch.stream_url = url
        else:
            # Fallback: gunakan URL halaman Vidio (bisa dipakai streamlink)
            ch.stream_url = f"https://www.vidio.com/live/{ch.streamid}-"
        return ch

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch, ch): ch for ch in channels}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log.debug(f"Error: {e}")

    success = sum(1 for ch in channels if ch.stream_url)
    log.info(f"Berhasil: {success}/{len(channels)} channel memiliki stream URL")
    return channels


# ─── Generate M3U ─────────────────────────────────────────────────────────────
def generate_m3u(channels: list[Channel], output_file: str = OUTPUT_FILE) -> str:
    """Generate file playlist.m3u dengan format M3U Extended."""
    lines = ["#EXTM3U"]

    # Header info
    lines.append(f'#EXTVLCOPT:http-referrer="https://www.vidio.com/"')

    for ch in channels:
        if not ch.stream_url:
            continue

        # Sanitasi nama untuk tvg-id
        tvg_id = f"vidio.{ch.streamid}"

        # Escape karakter khusus di attribute
        name_escaped = ch.name.replace('"', "'")
        group_escaped = ch.group.replace('"', "'")

        line = (
            f'#EXTINF:-1 '
            f'tvg-id="{tvg_id}" '
            f'tvg-logo="{ch.logo}" '
            f'group-title="{group_escaped}" '
            f'{name_escaped}'
        )
        lines.append(line)
        lines.append(ch.stream_url)

    content = "\n".join(lines) + "\n"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    log.info(f"Playlist tersimpan: {output_file} ({len(channels)} channel)")
    return content


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 50)
    log.info("Vidio IPTV Playlist Generator")
    log.info("=" * 50)

    client = VidioClient()

    # Step 1: Scrape daftar channel
    channels = scrape_live_page(client)
    if not channels:
        log.error("Tidak ada channel ditemukan. Pastikan Vidio sedang live.")
        return

    # Step 2: Enrich dengan detail dari API (logo, kategori)
    log.info("Mendapatkan detail channel dari API...")
    for ch in channels:
        if not ch.logo or ch.group == "Vidio":
            detail = client.get_channel_detail(ch.streamid)
            attrs = detail.get("attributes", {})
            if not ch.logo:
                ch.logo = attrs.get("logo") or attrs.get("thumbnail") or ""
            if ch.group == "Vidio":
                ch.group = attrs.get("category") or attrs.get("genre") or "Vidio"
            if not ch.name:
                ch.name = attrs.get("name") or attrs.get("title") or ch.name
        time.sleep(0.1)  # rate limit

    # Step 3: Fetch stream URLs
    channels = fetch_stream_urls(channels, client)

    # Step 4: Generate M3U
    generate_m3u(channels)

    log.info("Selesai! ✅")
    log.info(f"⚠️  Catatan: URL stream expire setelah ~{STREAM_URL_EXPIRY} detik.")
    log.info("   Jalankan ulang script ini secara berkala (cron) untuk update playlist.")


if __name__ == "__main__":
    main()   
