# WEBSOCKET ALIGNMENT AUDIT

## 1. Frontend Architecture
- **Connection URLs**: Hardcoded to `/ws/telemetry` (plus a secondary `ws_market_data` for `/ws/market-data` and `/ws/strategies/events/ws/{sid}` inside `useEventDag`).
- **Subscription Model**: Multiplexed channels via JSON payload.
  - Expects to send: `{"type": "subscribe", "channel": "bot_status"}`
  - Listens via `wsClient.subscribe(channel_string, callback)`.
  - Uses `WS_CHANNELS` constant object (e.g. `WS_CHANNELS.SIGNAL_TRACE`, `bot_status`).
- **Message Formats**: `{"type": "signal_update", "strategy_id": "...", ...}`
- **Authentication**: Connects unauthenticated to `/ws/telemetry`, handles auth via initial handshake payloads (or fails).

## 2. Backend Architecture
- **Connection URLs** (`api_ws/ws_routes.py`):
  - `/ws/user/{user_id}?token={token}`
  - `/ws/pnl/{user_id}?token={token}`
  - `/ws/ticker/{symbol}`
  - `/ws/orderbook/{symbol}`
  - `/ws/candles/{symbol}/{timeframe}`
- **Connection URLs** (DAG & Admin):
  - `/ws/{task_id}` (`dag_tasks.py`)
  - `/ws/{session_id}` (`dag_event_loop.py`)
  - `/ws/{tenant_id}` and `/ws/public/{channel}` (`ws_server.py`)
- **Subscription Model**: Topic-based endpoints. The client connects directly to the specific endpoint for the data type. There is NO multiplexed router for analytics/bots.
- **Message Formats**: e.g., `{"type": "fill", ...}`, `{"type": "initial_positions", ...}`
- **Authentication**: JWT validation passed as a `?token=` query parameter directly in the connection string (`_validate_ws_token()`).

## 3. Mismatch Summary
**CRITICAL FAILURE**: The frontend and backend WebSocket architectures are completely disjointed. 
- The frontend expects to connect to a single `/ws/telemetry` router and subscribe to multiplexed `bot_status` or `strategy_update` channels.
- The backend offers dedicated per-user streams (`/ws/user/{user_id}`) that push fills, orders, and balances, but no specific `bot_status` or `telemetry` multiplexer.
- The frontend is not passing the `token` in the URL query string, meaning any connection attempt to backend's protected routes will fail immediately with 4001 Unauthorized.
