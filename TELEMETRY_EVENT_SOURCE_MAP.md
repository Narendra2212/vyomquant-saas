# Telemetry Event Source Map

## Objective
Trace every call into the `BotTelemetryService` components (`SignalEventStore`, `ExecutionEventStore`, `RiskEventStore`) and identify the exact files, functions, and triggers responsible for telemetry generation.

---

## 1. BotTelemetryService (Health / Status)

### Source: `backend_app/backend/exchange_telemetry.py`
- **Function**: `_maintain_connection`
  - **Trigger**: Called in a background loop. Records reconnection attempts.
  - **Call**: `await telemetry.health.record_reconnect(bot_id)`
  - **Call**: `await publish_bot_health(...)`
- **Function**: `_handle_latency_spike`
  - **Trigger**: When exchange latency exceeds bounds.
  - **Call**: `await telemetry.health.update_bot_status(..., status=BotStatus.ERROR)`
  - **Call**: `await publish_bot_health(...)`

### Source: `backend_app/backend/master_executor.py`
- **Function**: `execute_paper_trade` / `execute_live_trade`
  - **Trigger**: When an order is processed by the main executor.
  - **Call**: `await self.telemetry.get_account_health(...)`

---

## 2. SignalEventStore

- **Function**: `record_signal_event`
  - **Trigger**: **NEVER CALLED.**
  - **Status**: The internal `SignalEventStore` inside `BotTelemetryService` is completely disconnected from the live trading engine.

*Note: Signals are instead pushed directly to the websocket via `ws_event_stream.publish_signal_trace` in `backend_app/backend/dag_event_loop.py` (Function `process_node_result`).*

---

## 3. ExecutionEventStore

### Source: `backend_app/backend/exchange_telemetry.py`
- **Function**: `on_order_execution`
  - **Trigger**: When an order is filled by the CCXT connector.
  - **Call**: `await telemetry.executions.record_execution_event(...)`
  - **Call**: `await publish_execution(...)`
- **Function**: `on_order_rejected`
  - **Trigger**: When an order is rejected by the exchange.
  - **Call**: `await telemetry.executions.record_execution_event(..., success=False, error=...)`
  - **Call**: `await publish_execution(...)`

### Source: `backend_app/backend/master_executor.py`
- **Function**: `_verify_execution`
  - **Trigger**: When an execution is verified.
  - **Call**: `await self.telemetry.log_execution(...)`

---

## 4. RiskEventStore

### Source: `backend_app/backend/exchange_telemetry.py`
- **Function**: `on_order_rejected`
  - **Trigger**: On order rejection (e.g. INSUFFICIENT_FUNDS or POSITION_LIMIT).
  - **Call**: `await publish_risk_event(...)` 
  - **Note**: This publishes the risk event to the websocket, but it does **not** call `telemetry.risks.record_risk_event()` to store it in memory.

- **Function**: `_handle_latency_spike`
  - **Trigger**: Extremely high exchange latency.
  - **Call**: `await publish_risk_event(...)`
  - **Note**: Does not call `telemetry.risks.record_risk_event()`.

- **Function**: `record_risk_event` (inside `RiskEventStore`)
  - **Trigger**: **NEVER CALLED.**
  - **Status**: The internal `RiskEventStore` is disconnected.

---

## Summary
The live engine relies heavily on `exchange_telemetry.py` and `dag_event_loop.py` to push real-time events *directly* to the websocket streamer (`ws_event_stream.py`), completely bypassing the internal memory stores (`SignalEventStore` and `RiskEventStore`) inside `BotTelemetryService`. `ExecutionEventStore` is the only component actively recording data in memory.
