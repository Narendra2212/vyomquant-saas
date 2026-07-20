import asyncio
import websockets
import json

async def test_auth():
    uri = "ws://localhost:8000/ws/telemetry"
    
    print("\n--- Test 1: Missing Token ---")
    try:
        async with websockets.connect(uri) as ws:
            pass
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Connection failed as expected. Status code: {e.status_code}")
    except Exception as e:
        print(f"Connection failed with: {e}")

    print("\n--- Test 2: Invalid Token ---")
    try:
        async with websockets.connect(f"{uri}?token=invalid_jwt_token_here") as ws:
            pass
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Connection failed as expected. Status code: {e.status_code}")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"Connection closed by server. Code: {e.code}, Reason: {e.reason}")
    except Exception as e:
        print(f"Connection failed with: {e}")

    print("\n--- Test 3: Valid Token (test_token) ---")
    try:
        async with websockets.connect(f"{uri}?token=test_token") as ws:
            print("Connected successfully!")
            
            # Send subscribe
            await ws.send(json.dumps({"type": "subscribe", "channel": "bot_status"}))
            response = await ws.recv()
            print(f"Response to subscribe: {response}")

            # Send ping
            await ws.send(json.dumps({"type": "ping"}))
            response = await ws.recv()
            print(f"Response to ping: {response}")

    except Exception as e:
        print(f"Connection failed with: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth())
