#!/usr/bin/env python3
"""DEBUG: Cek apa yang dikembalikan GQL API"""

import requests
import json

CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

_session = requests.Session()
_session.headers.update({
    "Client-ID": CLIENT_ID,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})

# ─── Test 1: Query sederhana (user) ─────────────────────────────
print("=" * 50)
print("TEST 1: Query sederhana (user pokimane)")
print("=" * 50)

r = _session.post(
    "https://gql.twitch.tv/gql",
    json={"query": 'query { user(login: "pokimane") { id displayName } }'},
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")

# ─── Test 2: topStreams ─────────────────────────────────────────
print("\n" + "=" * 50)
print("TEST 2: topStreams")
print("=" * 50)

query = """
query TopStreams($first: Int!) {
    topStreams(first: $first) {
        edges {
            node {
                id
                displayName
                viewerCount
                game { name }
                title
            }
        }
    }
}
"""

r = _session.post(
    "https://gql.twitch.tv/gql",
    json={"query": query, "variables": {"first": 10}},
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:1000]}")

# ─── Test 3: streams (tanpa topStreams) ─────────────────────────
print("\n" + "=" * 50)
print("TEST 3: streams")
print("=" * 50)

query3 = """
query Streams($first: Int!) {
    streams(first: $first) {
        edges {
            node {
                id
                displayName
                viewerCount
                game { name }
                title
            }
        }
    }
}
"""

r = _session.post(
    "https://gql.twitch.tv/gql",
    json={"query": query3, "variables": {"first": 10}},
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:1000]}")   
