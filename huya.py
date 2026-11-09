import requests, json

token = "kat_aBAhHd_aLVu-4dyfYCzpja9SVvFyEXbOeXwoYHfxAuQ"
h = {"Authorization": f"Bearer {token}", "Accept": "application/json",
     "User-Agent": "Mozilla/5.0", "x-app-platform": "web"}

# Test: /livestreams (tanpa /featured)
r = requests.get("https://web.kick.com/api/v1/livestreams",
                 params={"limit": 50, "language": "id", "page": 1},
                 headers=h, timeout=15)
print(f"Status: {r.status_code}")
d = r.json()
data = d.get("data", {})
streams = data.get("livestreams", [])
print(f"Stream: {len(streams)}")
print(f"Pagination: {data.get('pagination')}")

# Test page 2
r2 = requests.get("https://web.kick.com/api/v1/livestreams",
                  params={"limit": 50, "language": "id", "page": 2},
                  headers=h, timeout=15)
print(f"\nPage 2: {r2.status_code}")
d2 = r2.json()
print(f"Stream: {len(d2.get('data', {}).get('livestreams', []))}")   
