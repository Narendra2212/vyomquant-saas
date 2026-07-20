"""
Signal Trace Validation Test — Phase 2, Sprint 1D.2
Tests that DAG Event Loop emits signal_trace events via WebSocket.
"""
import sys, os, asyncio, json
os.environ['AERORA_MODE'] = 'paper'
sys.path.insert(0, '.')

from unittest.mock import MagicMock
from backend_app.core.models.execution_record import ExecutionRecordRepository
ExecutionRecordRepository.__init__ = lambda self, *a, **k: None
ExecutionRecordRepository.check_idempotent_execution = MagicMock(return_value=('exec_123', 'execute', None))
ExecutionRecordRepository.claim_execution = MagicMock(return_value=(True, None))
ExecutionRecordRepository.update_status = MagicMock()
ExecutionRecordRepository.record_attempt = MagicMock()

from backend_app.core.execution_engine import ExecutionEngine
import backend_app.core.execution_engine
backend_app.core.execution_engine.SessionLocal = MagicMock()
ExecutionEngine._validate_strategy_exists = lambda self, tid, sid: True

from backend_app.main import app
import uvicorn, httpx, websockets


async def listener(captured: dict, ready_event: asyncio.Event):
    uri = "ws://127.0.0.1:8009/ws/telemetry?token=test_token"
    try:
        async with websockets.connect(uri) as ws:
            for ch in ["signal_trace", "bot_status"]:
                await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
                await ws.recv()
            print("Signal listener ready")
            ready_event.set()
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    d = json.loads(msg)
                    if "payload" in d:
                        ch = d.get("channel")
                        captured.setdefault(ch, []).append(d)
                        print(f"CAPTURED {ch}: {d['type']}")
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"Listener stopped: {e}")


async def main():
    config = uvicorn.Config(app, host="127.0.0.1", port=8009, log_level="warning")
    server_task = asyncio.create_task(uvicorn.Server(config).serve())
    await asyncio.sleep(4)

    captured = {}
    ready = asyncio.Event()
    listener_task = asyncio.create_task(listener(captured, ready))
    await ready.wait()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "http://127.0.0.1:8009/api/strategies/events/start",
            json={
                "dag_nodes": [
                    {"id": "n-0", "type": "input", "label": "Feed", "params": {"symbol": "BTC/USDT", "timeframe": "1m"}},
                    {"id": "n-1", "type": "indicator", "label": "RSI", "params": {"window": 2, "source": "close"}},
                    {"id": "n-2", "type": "logic", "label": "GT", "params": {"operator": "GT", "threshold": 30}},
                    {"id": "n-3", "type": "action", "label": "Buy", "params": {"action": "buy", "amount": 1.0}}
                ],
                "dag_edges": [
                    {"source": "n-0", "target": "n-1"},
                    {"source": "n-1", "target": "n-2"},
                    {"source": "n-2", "target": "n-3"}
                ],
                "symbols": ["BTC/USDT"],
                "simulation_mode": True,
                "simulation_speed": 1000.0
            },
            headers={"Authorization": "Bearer DEV_ADMIN_TOKEN_999"},
        )
        print(f"DAG start: {r.status_code}")

    print("Waiting 15s for signal trace events...")
    await asyncio.sleep(15)

    print(f"\nCaptured channels: {list(captured.keys())}")
    from backend_app.backend.ws_event_stream import ws_streamer, ChannelType
    buf = ws_streamer.replay_buffer._tenant_buffers.get("test_user_id_123", {}).get(ChannelType.SIGNAL_TRACE, [])
    print(f"Buffer signal_trace count: {len(buf)}")
    for m in buf[:2]:
        try:
            print(f"  -> {m['message'].to_json()[:400]}")
        except Exception:
            print(f"  -> {m}")

    with open("signal_trace_results.json", "w") as f:
        json.dump(captured, f, indent=2)
    print("Done — results in signal_trace_results.json")
    server_task.cancel()
    listener_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
