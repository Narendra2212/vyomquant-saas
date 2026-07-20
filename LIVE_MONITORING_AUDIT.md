# LIVE MONITORING AUDIT

## Source Information
**Target File**: `routers/bot_monitoring.py`
**Dependencies**: `backend_app/backend/bot_telemetry.py`, `backend_app/backend/ws_event_stream.py`

## Endpoints and Data Structures
### 1. Bot Details
**Function**: `get_bot_details` (L348)
**Line Range**: 348-397
**Data Structure**: `BotDetailResponse`
```json
{
  "registration": {
    "bot_id": "...", "strategy_id": "...", "strategy_name": "...", 
    "exchange": "...", "symbol": "...", "mode": "...", "created_at": "..."
  },
  "health": {
    "bot_id": "...", "status": "running", "uptime_seconds": 120.5,
    "last_heartbeat": "...", "last_signal_at": "...", "last_signal_status": "...",
    "execution_latency_ms": 15.2, "reconnect_count": 0, "websocket_connected": true,
    "error_count": 0, "signal_count": 5, "execution_count": 2, "pnl_24h": "100.50"
  },
  "recent_signals": 5,
  "recent_risk_events": 0
}
```
**Runtime Flow**: Hits `telemetry.get_full_bot_status(bot_id)` which reads state dictionaries in memory.

### 2. Signal Traces
**Function**: `get_bot_signals` (L401)
**Line Range**: 401-456
**Data Structure**: `List[SignalEventResponse]`
```json
[
  {
    "event_id": "...", "bot_id": "...", "timestamp": "...", "signal_type": "buy",
    "symbol": "BTC/USDT", "price": "27000.5", "confidence": 0.85, "dag_path": "...",
    "status": "executed", "ml_confidence": 0.90
  }
]
```
**Runtime Flow**: Reads from `telemetry.signals.get_recent_signals`.

### 3. Executions
**Function**: `get_bot_executions` (L459)
**Line Range**: 459-499
**Data Structure**: `List[ExecutionEventResponse]`
```json
[
  {
    "event_id": "...", "bot_id": "...", "signal_id": "...", "timestamp": "...",
    "symbol": "BTC/USDT", "side": "buy", "size": "0.1", "price": "27000.5",
    "filled_amount": "0.1", "fees": "0.01", "slippage": "0.001", "latency_ms": 10.5,
    "success": true
  }
]
```
**Runtime Flow**: Reads from `telemetry.executions.get_recent_executions`.

### 4. Risk Events
**Function**: `get_bot_risk_events` (L522)
**Line Range**: 522-561
**Data Structure**: `List[RiskEventResponse]`
```json
[
  {
    "event_id": "...", "bot_id": "...", "timestamp": "...", "event_type": "drawdown",
    "severity": "high", "description": "Drawdown exceeded 5%", "signal_id": null,
    "blocked": true
  }
]
```

### 5. WebSockets
**Target File**: `backend_app/backend/ws_event_stream.py`
**Channels**: `bot_status` (`/ws/bots`), `signal_trace` (`/ws/signals`)
**Runtime Flow**: Uses Redis pub/sub (`ws_streamer.subscribe`) to stream live telemetry to specific authenticated tenants.
