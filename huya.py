import requests, time, hmac, hashlib, uuid

t = str(time.time())
sig = hmac.new(f"V1d10D3v:{t}".encode(), t.encode(), hashlib.sha256).hexdigest()
luws = f"{str(uuid.uuid4()).upper()}_186957857"

resp = requests.get(
    "https://api.vidio.com/livestreamings/204/stream?initialize=true",
    headers={
        "x-api-key": "CH1ZFsN4N/MIfAds1DL9mP151CNqIpWHqZGRr+LkvUyiq3FRPuP1Kt6aK+pG3nEC1FXt0ZAAJ5FKP8QU8CZ5/ieyIuWEkvNBqXdsdlXM2838+3SFjs9rtlntv7l7DHkBHWK1q/4vLGad8GAj3vfLciRFnquHuZMMW+4EX8yTe1w=",
        "x-api-platform": "web-mobile",
        "x-secure-level": "2",
        "x-client": t,
        "x-signature": sig,
        "x-user-id": "186957857",
        "x-user-email": "bhianesee05@gmail.com",
        "luws": luws,
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
        "Origin": "https://m.vidio.com",
        "Referer": "https://m.vidio.com/",
    },
    cookies={
        "ahoy_visitor": "3489c20a-d32e-468a-bd1b-8f7d150fa132",
        "plenty_id": "186957857",
        "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJkYXRhIjp7InR5cGUiOiJhY2Nlc3NfdG9rZW4iLCJ1aWQiOjE4Njk1Nzg1N30sImV4cCI6MTc5NDQ0MzM3MX0.vIjiSE4zy4-12PSqMrov5hxB6kwKdr26_EDVmeI6sfY",
        "_vidio_session": "gDIPH2EpOrLiSN%2F56O4fqK4dUAfNzDuXx7RTU3RSOHKZhko3b%2F7QueM7b8wz0KIejrPEnUYD6s1%2Bca9u3%2F9lOTMq2ZDHoLXeS0B93ZjSK0TMES9WbSliDrwsdNNGn%2BhsxG6mjYUKoX5YMRs1uKkNrHZNFkxV2wRZPhdLUb6VeYpVaimQoxkxxXW9ntqzr9BHxyG%2B3%2FgjsXNR4Qhd8wekXzg%2BdQtIZsuU%2BSZPVf3QBD1OmBXllXqqefkvqH2XRYxHjxhvqy%2BGcUYhko%2BYswcvl7w1BtIu6A%3D%3D--IvrppznqVeItE5ZM--e34uPg3dmCb3kRKAGa%2Bulw%3D%3D",
    },
)
print(resp.status_code)
print(resp.json().get("data", {}).get("attributes", {}).get("hls", "")[:80])   
