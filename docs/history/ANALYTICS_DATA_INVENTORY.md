# ANALYTICS DATA INVENTORY

## Overview
This inventory catalogs the available analytics data structures currently accessible in the backend source code across QuestDB, Redis, and Supabase.

## 1. Time-Series Telemetry (QuestDB)
**File**: `backend_app/backend/telemetry_engine.py`
**Class**: `TelemetryEngine`

| Data Target | Function | SQL View/Table | Return Structure |
| :--- | :--- | :--- | :--- |
| **Live PnL** | `get_live_pnl` (L300) | `live_user_pnl` | `Optional[dict]` |
| **OHLCV Candles** | `get_chart_candles` (L313) | `kline_1m` | `Optional[dict]` |
| **Account Health** | `get_account_health` (L329) | `account_health` | `{"current_drawdown_pct": float, "daily_pnl_pct": float, "total_exposure_usdt": float}` |
| **Equity Curve** | `get_equity_curve` (L368) | `equity_curve` | `[{"timestamp": str, "equity": float}]` |
| **Trade History** | `get_trade_history` (L391) | `executions` | `list[dict]` |
| **Asset Allocation**| `get_portfolio_allocation` (L423) | `portfolio_allocation` | `[{"asset": str, "value_usd": float, "pct": float}]` |
| **PnL Heatmap** | `get_pnl_heatmap` (L440) | `executions` (Aggregated) | `[{"date": str, "pnl_usd": float}]` |
| **System Metrics**| `get_system_metrics` (L462) | `system_metrics` | `[{"timestamp": str, "cpu": float, "ram": float, "lat": float}]` |
| **Leaderboard** | `get_leaderboard` (L478) | `leaderboard_view` | `[{"rank": int, "user_id": str, "username": str, "pnl_pct": float, ...}]` |

## 2. Aggregated Performance (QuestDB via API)
**File**: `backend_app/routers/analytics.py`
**Function**: `get_performance` (L21)
- **Source**: `executions` table via `SUM()`, `AVG()`, `STDDEV()`
- **Return Structure**: `{"period_days": int, "total_trades": int, "winning_trades": int, "win_rate": float, "total_pnl": float, "avg_pnl": float, "sharpe_ratio": float}`

## 3. Real-Time Caching (Redis)
**File**: `backend_app/routers/portfolio.py`
**Function**: `_get_portfolio_state` (L32)
- **Redis Keys**: 
  - `portfolio:{user_id}:{exchange_id}:balance` (Hash)
  - `portfolio:{user_id}:{exchange_id}:positions` (String/JSON)
- **Data Structure**: `{"available_balance": str, "total_equity": str, "total_exposure": str, "positions": dict, "daily_pnl": str}`

## 4. Bot & Signal Event Telemetry
**File**: `routers/bot_monitoring.py`
- **Source**: `backend.bot_telemetry.telemetry`
- **Data**: Detailed execution events, risk blocks, signal generation, and pipeline traces.
