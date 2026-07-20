import os
os.environ['AERORA_MODE'] = 'paper'
import asyncio
import httpx
import websockets
import json
import uvicorn
from decimal import Decimal
from unittest.mock import MagicMock, MagicMock
import uuid

# Mock the ExecutionRecordRepository logic to avoid db hits
from backend_app.core.models.execution_record import ExecutionRecordRepository
original_init = ExecutionRecordRepository.__init__
ExecutionRecordRepository.__init__ = lambda self, *args, **kwargs: None
ExecutionRecordRepository.check_idempotent_execution = MagicMock(return_value=("exec_123", "execute", None))
ExecutionRecordRepository.claim_execution = MagicMock(return_value=(True, None))
ExecutionRecordRepository.update_status = MagicMock()
ExecutionRecordRepository.record_attempt = MagicMock()

from backend_app.core.execution_engine import ExecutionEngine
from backend_app.backend.exchange_executor import CCXTExchangeExecutor
import backend_app.core.execution_engine
backend_app.core.execution_engine.SessionLocal = MagicMock()
ExecutionEngine._validate_strategy_exists = lambda self, tenant_id, strategy_id: True

from backend_app.main import app

async def run_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8006, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def ws_listener(captured_events, ready_event):
    uri = "ws://127.0.0.1:8006/ws/telemetry?token=test_token"
    try:
        async with websockets.connect(uri) as ws:
            for ch in ["bot_status", "signal_trace", "execution_events", "risk_events", "deployment_events"]:
                await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                await ws.recv() # ack
            
            print("WS Client Subscribed. Listening for live telemetry...")
            ready_event.set()
            
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(msg)
                if "payload" in data:
                    ch = data.get("channel")
                    captured_events.setdefault(ch, []).append(data)
                    print(f"Captured {ch} from Live Pipeline")
    except Exception as e:
        print(f"WS listener stopped: {e}")

async def trigger_signal_pipeline():
    """Trigger the DAG Event Loop through standard REST to test Signal emission natively"""
    payload = {
      "dag_nodes": [
        {"id": "n-0", "type": "input", "label": "Feed", "params": {"symbol": "BTC/USDT", "timeframe": "1m"}},
        {"id": "n-1", "type": "indicator", "label": "RSI", "params": {"window": 14, "source": "close"}},
        {"id": "n-2", "type": "logic", "label": "GT", "params": {"operator": "GT", "threshold": 70}},
        {"id": "n-3", "type": "action", "label": "Sell", "params": {"action": "sell", "amount": 1.0}}
      ],
      "dag_edges": [
        {"source": "n-0", "target": "n-1"},
        {"source": "n-1", "target": "n-2"},
        {"source": "n-2", "target": "n-3"}
      ],
      "symbols": ["BTC/USDT"],
      "simulation_mode": True,
      "simulation_speed": 100.0
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post("http://127.0.0.1:8006/api/strategies/events/start", json=payload, headers={"Authorization": "Bearer DEV_ADMIN_TOKEN_999"})
            print(f"Signal Pipeline start status: {r.status_code}")
        except Exception as e:
            print(f"Signal pipeline trigger failed: {e}")


async def trigger_execution_pipeline():
    from backend_app.backend.distributed_execution.orchestrator import ExecutionOrchestrator
    from backend_app.backend.exchange_telemetry import exchange_telemetry
    from decimal import Decimal
    
    bot_id = "test_bot_live_123"
    exchange_telemetry.register_exchange_bot(bot_id, "binance", "BTC/USDT")
    
    from unittest.mock import AsyncMock
    
    from backend_app.backend.distributed_execution.queue_manager import queue_manager
    queue_manager.publish_execution_job = AsyncMock(return_value=True)

    orchestrator = ExecutionOrchestrator()
    orchestrator.exchange_gateways["binance"] = "fake_gateway"
    
    tenant_id = "test_user_id_123"
    
    print("Executing standard BUY order...")
    await orchestrator.submit_execution_job(
        tenant_id=tenant_id,
        strategy_id="strat_test",
        bot_id=bot_id,
        signal_id="sig_123",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.1"),
        price=Decimal("0")
    )
    
    print("Executing reject scenario...")
    # Trigger a reject
    from backend_app.backend.ws_event_stream import ws_streamer
    print('STATS BEFORE REJECT:', ws_streamer.get_stats())
    await exchange_telemetry.on_order_rejected(
        exchange="binance",
        symbol="BTC/USDT",
        order_id="rej_ord_123",
        bot_id=bot_id,
        signal_id="sig_123",
        reason="Insufficient Margin",
        error_code="INSUFFICIENT_FUNDS",
        tenant_id=tenant_id
    )

async def main():
    server_task = asyncio.create_task(run_server())
    await asyncio.sleep(4)
    
    captured_events = {}
    ready_event = asyncio.Event()
    ws_task = asyncio.create_task(ws_listener(captured_events, ready_event))
    await ready_event.wait()
    
    print("\n--- TRIGGERING SIGNAL PIPELINE ---")
    await trigger_signal_pipeline()
    
    print("\n--- TRIGGERING EXECUTION & RISK PIPELINE ---")
    await trigger_execution_pipeline()
    
    print("\nWaiting 5 seconds for telemetry flush...")
    await asyncio.sleep(5)
    
    print("\n--- EVENT CAPTURE SUMMARY ---\n")
    from backend_app.backend.ws_event_stream import ws_streamer, ChannelType
    from backend_app.backend.exchange_telemetry import ws_streamer as et_ws_streamer
    
    print(f"SINGLETON IDENTITY CHECK:")
    print(f"  ws_event_stream ws_streamer id : {id(ws_streamer)}")
    print(f"  exchange_telemetry ws_streamer id: {id(et_ws_streamer)}")
    print(f"  SAME OBJECT: {ws_streamer is et_ws_streamer}")
    print()
    
    all_channels = [c for c in ChannelType]
    print('### REPLAY BUFFER (ALL CHANNELS) ###')
    tenant_buf = ws_streamer.replay_buffer._tenant_buffers.get('test_user_id_123', {})
    total_buffered = 0
    for ch in all_channels:
        events = tenant_buf.get(ch, [])
        if events:
            print(f"  {ch.value}: {len(events)} event(s)")
            for m in events:
                try:
                    print(f"    -> {m['message'].to_json()}")
                except Exception:
                    print(f"    -> {m}")
            total_buffered += len(events)
    if total_buffered == 0:
        print("  (EMPTY — no events captured in buffer)")
    print('####################################')
    
    print()
    print('### WS CAPTURED EVENTS (per channel) ###')
    if captured_events:
        for channel, events in captured_events.items():
            print(f"  Channel: {channel} | Captured: {len(events)}")
            for ev in events[:2]:
                print(f"    sample: {json.dumps(ev, indent=4)[:200]}")
    else:
        print("  (EMPTY — WS listener received no events)")
    
    with open("live_telemetry_results.json", "w") as f:
        json.dump(captured_events, f, indent=2)
        
    print("\nDumped raw events to live_telemetry_results.json")
    server_task.cancel()
    ws_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
