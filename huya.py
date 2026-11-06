from curl_cffi import requests
import json

s = requests.Session(impersonate="chrome")
s.headers.update({
    "client-id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
    "content-type": "text/plain;charset=UTF-8",
    "origin": "https://m.twitch.tv",
    "referer": "https://m.twitch.tv/",
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36",
})

ops = [{
    "operationName": "MwebFrontPageQuery",
    "variables": {"first": 20, "itemsPerRow": 8, "platform": "mobile_web", "requestID": "", "after": ""},
    "extensions": {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": "9d3a6396104b829d516c9c5a02c89bfe81c4b2453d34143ca2c1f1144d9afe50"
        }
    }
}]

r = s.post("https://gql.twitch.tv/gql", json=ops)
print(r.status_code)
print(r.text[:2000])   
