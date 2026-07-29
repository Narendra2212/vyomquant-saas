# ANALYTICS ENDPOINT MAP

## 1. Analytics Endpoints
- **Route**: `GET /api/analytics/performance`
- **Request Schema**: Query param `days: int` (default 30, ge=1, le=365)
- **Response Schema**: JSON object `{period_days, total_trades, winning_trades, win_rate, total_pnl, avg_pnl, sharpe_ratio}`
- **Authentication**: `Depends(get_current_user)` (Bearer JWT)
- **Source File**: `backend_app/routers/analytics.py`
- **Line Numbers**: 21-92

## 2. Portfolio Endpoints
- **Route**: `GET /api/portfolio/summary`
- **Request Schema**: None
- **Response Schema**: JSON `{total_value, pnl_24h, unrealized_pnl}`
- **Authentication**: `Depends(get_current_user)`
- **Source File**: `backend_app/routers/portfolio.py` (Lines 103-117)

- **Route**: `GET /api/portfolio/equity-curve`
- **Request Schema**: Query param `days: int`
- **Response Schema**: Array of JSON `{timestamp, equity}`
- **Source File**: `backend_app/routers/portfolio.py` (Lines 120-142)

- **Route**: `GET /api/portfolio/allocation`
- **Response Schema**: Array of JSON `{asset, value_usd, pct}`
- **Source File**: `backend_app/routers/portfolio.py` (Lines 145-160)

- **Route**: `GET /api/portfolio/heatmap`
- **Request Schema**: Query param `months: int`
- **Response Schema**: Array of JSON `{date, pnl_usd}`
- **Source File**: `backend_app/routers/portfolio.py` (Lines 163-181)

- **Route**: `GET /api/portfolio/recent-transactions`
- **Request Schema**: Query params `limit: int`, `days: int`
- **Response Schema**: JSON `{transactions: [...], count, period_days, generated_at}`
- **Source File**: `backend_app/routers/portfolio.py` (Lines 185-232)

## 3. Bot Monitoring Endpoints
*Note: Dedicated "bot" endpoints do not exist. Bot management goes through strategies.*
- **Route**: `POST /api/strategies/{strategy_id}/deploy`
- **Route**: `POST /api/strategies/{strategy_id}/stop`
- **Route**: `POST /api/strategies/{strategy_id}/pause`
- **Route**: `POST /api/strategies/{strategy_id}/resume`
- **Source File**: `backend_app/routers/strategies.py`

## 4. WebSocket Endpoints
- **Route**: `ws://.../ws/ticker/{symbol}`
- **Route**: `ws://.../ws/orderbook/{symbol}`
- **Route**: `ws://.../ws/candles/{symbol}/{timeframe}`
- **Route**: `ws://.../ws/user/{user_id}?token={token}&exchange_id={exchange_id}`
- **Route**: `ws://.../ws/pnl/{user_id}?token={token}`
- **Source File**: `backend_app/api_ws/ws_routes.py` (Lines 327, 454, 499, 557, 782)
- **Authentication**: Locally verified HS256 JWT via query param `token`.

- **Route**: `ws://.../ws/{task_id}` (DAG execution trace)
- **Source File**: `backend_app/routers/dag_tasks.py` (Line 297)

## 5. TelemetryEngine
- Provided via Dependency Injection `get_telemetry`.
- Connects to QuestDB natively to query aggregations via `telemetry.execute_query()`.

## 6. Redis Portfolio Cache
- **Keys**: `portfolio:{user_id}:{exchange_id}:balance` and `portfolio:{user_id}:{exchange_id}:positions`.
- **Usage**: Queried in `_get_portfolio_state()` inside `backend_app/routers/portfolio.py` (Lines 32-100) using `redis_manager`.

## 7. QuestDB Telemetry Sources
- **Table**: `executions` (Columns: timestamp, symbol, side, amount, price, pnl, fee, order_type)
- **Table**: `live_user_pnl` (Columns: total_value, pnl_24h, unrealized_pnl)
- **Table**: `equity_curve` (Columns: timestamp, equity)
- **Table**: `portfolio_allocation` (Columns: asset, value_usd, pct)
