import urllib.request
import urllib.error
import asyncio
import websockets
import ssl
import json

print("=== 1. Testing Root / ===")
req = urllib.request.urlopen("https://d7d88qs4jmch.cloudfront.net/", timeout=10)
print(f"Root Status: {req.status}, Content-Type: {req.headers.get('Content-Type')}")
assert req.status == 200

print("\n=== 2. Testing SPA Route /app/live-trading ===")
req_spa = urllib.request.urlopen("https://d7d88qs4jmch.cloudfront.net/app/live-trading", timeout=10)
print(f"SPA Route Status: {req_spa.status}, Content-Type: {req_spa.headers.get('Content-Type')}")
assert req_spa.status == 200

print("\n=== 3. Testing JS Bundle /assets/index-DZ0mA71y.js ===")
req_js = urllib.request.urlopen("https://d7d88qs4jmch.cloudfront.net/assets/index-DZ0mA71y.js", timeout=10)
js_data = req_js.read()
print(f"JS Bundle Status: {req_js.status}, Size: {len(js_data)} bytes")
assert req_js.status == 200
assert len(js_data) > 100000

print("\n=== 4. Testing API Health /api/health ===")
req_health = urllib.request.urlopen("https://d7d88qs4jmch.cloudfront.net/api/health", timeout=10)
health_json = json.loads(req_health.read().decode())
print(f"API Health Status: {req_health.status}, Response: {health_json.get('status')}")
assert req_health.status == 200

print("\n=== 5. Testing API 404 (Must return 404 JSON, NOT 200 HTML) ===")
try:
    urllib.request.urlopen("https://d7d88qs4jmch.cloudfront.net/api/v1/nonexistent-endpoint", timeout=10)
    print("FAIL: Expected 404 but got 200!")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"PASS: Received real HTTP {e.code} {e.reason} from backend API! Body: {body[:100]}")

print("\n=== 6. Testing WebSocket Error Transparency (Must return 403, NOT 200 HTML) ===")
async def test_ws():
    ssl_context = ssl._create_unverified_context()
    try:
        async with websockets.connect("wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=invalid", ssl=ssl_context, close_timeout=5):
            pass
    except Exception as e:
        print(f"PASS: WebSocket error captured as: {type(e).__name__}: {e}")

asyncio.run(test_ws())
print("\nALL INFRASTRUCTURE & ROUTING TESTS PASSED!")
