import asyncio
import websockets
import json
from datetime import datetime
import uvicorn
from fastapi import FastAPI
import threading
import time

# Create a test FastAPI app that includes the real websocket router
app = FastAPI()
from backend_app.api_ws.ws_routes import ws_router
from backend_app.backend.ws_event_stream import (
    ws_streamer,
    publish_bot_health,
    publish_signal_trace,
    publish_execution,
    publish_risk_event,
    publish_deployment
)
app.include_router(ws_router)

@app.on_event("startup")
async def startup():
    await ws_streamer.start()

@app.on_event("shutdown")
async def shutdown():
    await ws_streamer.stop()

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="error")

async def simulate_internal_publisher():
    # Wait for clients to connect
    await asyncio.sleep(2)
    
    tenant_id = "test_user_id_123"
    
    print("Publishing bot_status...")
    await publish_bot_health(tenant_id, {
        "bot_id": "bot_1",
        "status": "running",
        "health": 100,
        "latency_ms": 15
    })
    
    print("Publishing signal_trace...")
    await publish_signal_trace(tenant_id, {
        "symbol": "BTC-USDT",
        "signal": "buy",
        "strength": 0.85
    })
    
    print("Publishing execution_events...")
    await publish_execution(tenant_id, {
        "order_id": "ord_1",
        "symbol": "BTC-USDT",
        "status": "filled",
        "price": 65000.0,
        "amount": 0.1
    })
    
    print("Publishing risk_events...")
    await publish_risk_event(tenant_id, {
        "type": "circuit_breaker",
        "severity": "high",
        "message": "Volatility threshold exceeded"
    })
    
    print("Publishing deployment_events...")
    await publish_deployment(tenant_id, {
        "strategy_id": "strat_1",
        "status": "deployed",
        "version": "1.0.0"
    })

async def capture_events():
    uri = "ws://127.0.0.1:8001/ws/telemetry?token=test_token"
    
    captured = {}
    
    try:
        async with websockets.connect(uri) as ws:
            # Subscribe to all channels
            channels = [
                "bot_status", 
                "signal_trace", 
                "execution_events", 
                "risk_events", 
                "deployment_events"
            ]
            
            for ch in channels:
                await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                await ws.recv() # Consume subscribe ack
                
            print("Connected and subscribed to all channels. Listening for events...")
            
            # Start the simulation task in the background of the main thread?
            # No, simulate_internal_publisher needs to run in the uvicorn event loop...
            # Actually, to make it simple, we can just hit an endpoint, but since we didn't add one, 
            # let's trigger it directly if we are in the same process.
            pass
            
    except Exception as e:
        print(f"Client error: {e}")

# Since we need the publisher to run in the server's event loop, let's add a temporary endpoint to the test server
@app.post("/trigger")
async def trigger_events():
    tenant_id = "test_user_id_123"
    await publish_bot_health(ws_streamer, tenant_id, "bot_1", "strat_1", {"bot_id": "bot_1", "status": "running", "health": 100, "latency_ms": 15})
    await publish_signal_trace(ws_streamer, tenant_id, "bot_1", "strat_1", {"symbol": "BTC-USDT", "signal": "buy", "strength": 0.85})
    await publish_execution(ws_streamer, tenant_id, "bot_1", {"order_id": "ord_1", "symbol": "BTC-USDT", "status": "filled", "price": 65000.0, "amount": 0.1})
    await publish_risk_event(ws_streamer, tenant_id, "bot_1", {"type": "circuit_breaker", "severity": "high", "message": "Volatility threshold exceeded"})
    await publish_deployment(ws_streamer, tenant_id, "strat_1", {"strategy_id": "strat_1", "status": "deployed", "version": "1.0.0"})
    return {"status": "ok"}

async def test_client():
    uri = "ws://127.0.0.1:8001/ws/telemetry?token=test_token"
    captured = {}
    
    try:
        async with websockets.connect(uri) as ws:
            channels = ["bot_status", "signal_trace", "execution_events", "risk_events", "deployment_events"]
            for ch in channels:
                await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                res = await ws.recv()
                
            print("Subscribed. Triggering events via REST...")
            
            # Trigger via REST using httpx or aiohttp
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8001/trigger", method="POST")
            urllib.request.urlopen(req)
            
            print("Events triggered. Capturing...")
            
            # Receive 5 events
            for _ in range(5):
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                if "payload" in data:
                    ch = data.get("channel")
                    captured[ch] = data
                    print(f"Captured {ch}: {data['payload']}")
                    
            with open("captured_events.json", "w") as f:
                json.dump(captured, f, indent=2)
                
            print("\n--- Testing Replay Buffer ---")
            # Close connection and reconnect to test replay
            await ws.close()
            
        print("Reconnecting...")
        async with websockets.connect(uri) as ws:
            # Re-subscribe and request replay
            for ch in channels:
                await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                await ws.recv()
                
                # Request replay
                await ws.send(json.dumps({"type": "replay", "channel": ch, "since_timestamp": (datetime.now().timestamp() - 60)}))
            
            # Should receive replayed events
            print("Listening for replayed events...")
            replays = 0
            while replays < 5:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                if "payload" in data:
                    print(f"Replayed {data.get('channel')}: {data['payload']}")
                    replays += 1
            
    except Exception as e:
        print(f"Test error: {e}")

if __name__ == "__main__":
    # Start server in thread
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    
    # Wait for server to start
    time.sleep(2)
    
    # Run client
    asyncio.run(test_client())
