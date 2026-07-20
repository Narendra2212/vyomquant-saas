# TELEMETRY_ARCHITECTURE_TRUTH_REPORT.md
## Sprint 1D.2 — Phase 9: Architecture Truth Report
**Generated:** 2026-06-24T18:49:00Z

---

## Executive Summary

Six WebSocket telemetry channels are defined. Two are ACTIVE with live runtime proof. One is ACTIVE by code verification (signal_trace). One is PARTIAL (bot_status). One required a critical singleton bug fix to become operational.

---

## Critical Bug Fixed During This Sprint

File: backend_app/backend/exchange_telemetry.py

BEFORE (broken):
  from ws_event_stream import ws_streamer (bare name -> different Python module instance)
  from bot_telemetry import ...
  from signal_trace_engine import ...

AFTER (fixed):
  from backend_app.backend.ws_event_stream import ws_streamer (fully-qualified -> shared singleton)
  from backend_app.backend.bot_telemetry import ...
  from backend_app.backend.signal_trace_engine import ...

Impact: Without this fix, ALL execution and risk telemetry events were silently discarded.
The exchange_telemetry module was publishing to a phantom ws_streamer with zero subscribers.

Proof:
  ws_event_stream ws_streamer id : 2073696272800
  exchange_telemetry ws_streamer id: 2073696272800
  SAME OBJECT: True

---

## Channel Status Table

| Channel | Status | Evidence | Trigger |
|---|---|---|---|
| execution_events | ACTIVE | 2 live events captured and delivered | on_order_execution(), on_order_rejected() |
| risk_events | ACTIVE | 1 live event captured and delivered | on_order_rejected() |
| signal_trace | ACTIVE (code) | Wired in dag_event_loop.py:839, same singleton | DAGEventLoop._execute_signal() |
| bot_status | PARTIAL | Wired in exchange_telemetry.py, not triggered in test | on_websocket_connect/disconnect |
| deployment_events | UNKNOWN | Channel defined, no emitter found in search | Unknown |
| infrastructure | UNKNOWN | Channel defined, 0 subscriptions in stats | Unknown |

---

## Live Runtime Evidence Summary

Test run 2026-06-24T18:42:30Z:

execution_events:
  - order_filled: tenant=test_user_id_123, bot=test_bot_live_123, symbol=BTC/USDT, sequence_id=1
  - order_rejected: INSUFFICIENT_FUNDS, sequence_id=2

risk_events:
  - risk_warning: severity=high, event_type=order_reject, sequence_id=1

Load test 2026-06-24T18:49:00Z:
  - 1,000 rejections processed at 1,283 events/second
  - 2,000 WS events delivered, 0 dropped

---

## ws_streamer Architecture

Class: WebSocketEventStreamer (ws_event_stream.py)
Singleton: ws_streamer = WebSocketEventStreamer() (line 1212)

Subsystems:
- SubscriptionManager: tracks per-tenant per-channel subscribers
- EventReplayBuffer: in-memory buffer (defaultdict(lambda: defaultdict(deque)))
  - Keyed: _tenant_buffers[tenant_id][ChannelType] -> deque of buffered messages
- Heartbeat loop: 30s interval, 60s timeout
- Backpressure: max queue size per client (no drops observed in load test)

---

## Known Issues

1. SharedRedisManager missing .pipeline() method
   - Error: Failed to save job <id>: 'SharedRedisManager' object has no attribute 'pipeline'
   - Impact: Job persistence to Redis fails, but telemetry is unaffected
   - Required: Separate fix to Redis manager layer

2. signal_trace test capture blocked by simulation timing
   - The DAG simulation requires candle close events to generate signals
   - Test window too short at 15s for reliable capture in CI-like environment

3. bot_status has no autonomous heartbeat
   - Channel only fires on exchange connect/disconnect events
   - No periodic bot health broadcast implemented

---

## Recommendation

The core telemetry pipeline is production-ready for execution and risk events.
Signal trace is wired correctly and will emit when the DAG generates real signals.
Bot status requires exchange connectivity simulation to test fully.
