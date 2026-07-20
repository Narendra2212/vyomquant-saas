import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/ws/telemetry?token=test_token"
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")
            
            # Subscribe to bot_status and signal_trace
            await websocket.send(json.dumps({"type": "subscribe", "channel": "bot_status"}))
            print(f"Sent: bot_status subscribe")
            res = await websocket.recv()
            print(f"Received: {res}")
            
            await websocket.send(json.dumps({"type": "subscribe", "channel": "signal_trace"}))
            print(f"Sent: signal_trace subscribe")
            res = await websocket.recv()
            print(f"Received: {res}")
            
            await websocket.send(json.dumps({"type": "subscribe", "channel": "execution_events"}))
            print(f"Sent: execution_events subscribe")
            res = await websocket.recv()
            print(f"Received: {res}")
            
            await websocket.send(json.dumps({"type": "subscribe", "channel": "risk_events"}))
            print(f"Sent: risk_events subscribe")
            res = await websocket.recv()
            print(f"Received: {res}")
            
            # Send replay request to trigger sending events from buffer
            await websocket.send(json.dumps({"type": "replay"}))
            print(f"Sent: replay")
            
            # Wait for some events
            events = []
            for _ in range(5):
                try:
                    res = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    print(f"Event: {res}")
                    events.append(res)
                except asyncio.TimeoutError:
                    print("Timeout waiting for more events")
                    break
                    
            with open("ws_test_out.txt", "w") as f:
                for e in events:
                    f.write(e + "\n")
                    
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
