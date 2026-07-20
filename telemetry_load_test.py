"""
Telemetry Load Test — Phase 8, Sprint 1D.2
Tests WebSocket event throughput under load: 100, 500, 1000 execution events.
Injects events via ExchangeTelemetryHooks (the only permitted path).
"""
import sys, os, asyncio, json, time, statistics
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
import uvicorn, websockets
from decimal import Decimal


async def listener(captured: list, ready_event: asyncio.Event):
    """Listens to all channels and counts events."""
    uri = "ws://127.0.0.1:8010/ws/telemetry?token=test_token"
    async with websockets.connect(uri) as ws:
        for ch in ["execution_events", "risk_events", "signal_trace", "bot_status"]:
            await ws.send(json.dumps({"type": "subscribe", "channel": ch}))
            await ws.recv()
        ready_event.set()
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                d = json.loads(msg)
                if "payload" in d:
                    captured.append(d)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break


async def run_load_batch(n: int, tenant_id: str = "test_user_id_123") -> dict:
    """Fire n rejection events through on_order_rejected and measure throughput."""
    from backend_app.backend.exchange_telemetry import exchange_telemetry
    
    t0 = time.perf_counter()
    tasks = []
    for i in range(n):
        tasks.append(exchange_telemetry.on_order_rejected(
            exchange="binance",
            symbol="BTC/USDT",
            order_id=f"load_ord_{i}",
            bot_id="load_bot",
            signal_id=f"load_sig_{i}",
            reason="Insufficient Margin",
            error_code="INSUFFICIENT_FUNDS",
            tenant_id=tenant_id
        ))
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t0
    return {"n": n, "elapsed_s": round(elapsed, 4), "throughput_eps": round(n / elapsed, 1)}


async def main():
    config = uvicorn.Config(app, host="127.0.0.1", port=8010, log_level="warning")
    server_task = asyncio.create_task(uvicorn.Server(config).serve())
    await asyncio.sleep(4)

    captured = []
    ready = asyncio.Event()
    listener_task = asyncio.create_task(listener(captured, ready))
    await ready.wait()
    print("Load test listener ready")

    results = {}
    for batch_size in [100, 500, 1000]:
        before = len(captured)
        print(f"\nRunning batch: {batch_size} events...")
        r = await run_load_batch(batch_size)
        # Wait for WS delivery flush
        await asyncio.sleep(3)
        after = len(captured)
        delivered = after - before
        delivery_rate = round(delivered / batch_size * 100, 1)
        
        results[str(batch_size)] = {
            **r,
            "ws_delivered": delivered,
            "delivery_rate_pct": delivery_rate
        }
        print(f"  Batch {batch_size}: elapsed={r['elapsed_s']}s, throughput={r['throughput_eps']} eps, WS delivered={delivered} ({delivery_rate}%)")

    from backend_app.backend.ws_event_stream import ws_streamer
    try:
        stats = ws_streamer.get_stats()
    except AttributeError:
        stats = {"note": "get_stats not available"}
    
    print("\n=== LOAD TEST SUMMARY ===")
    for bs, r in results.items():
        print(f"  {bs} events: {r['throughput_eps']} eps | {r['ws_delivered']} delivered | {r['delivery_rate_pct']}% rate | {r['elapsed_s']}s")
    print(f"  Dropped messages: {stats.get('dropped_messages', {})}")
    
    with open("telemetry_load_results.json", "w") as f:
        json.dump({"results": results, "ws_stats": stats}, f, indent=2)
    print("\nDone — results in telemetry_load_results.json")
    
    server_task.cancel()
    listener_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
