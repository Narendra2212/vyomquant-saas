# SPRINT 1C.3 FINAL WIRING AUDIT

## Overview
This report classifies the readiness of every dashboard analytics feature to be wired to the backend.

### 1. Equity Curve
- **Feature**: Portfolio historical performance line chart.
- **Backend Ready?**: YES (`GET /api/portfolio/equity-curve`)
- **Frontend Ready?**: YES (`PremiumDashboard.jsx`, `EquityCurveChart`)
- **API Ready?**: YES (Mapped in `endpoints.user.getEquityCurve`)
- **WebSocket Ready?**: N/A
- **Missing Files**: None
- **Missing State**: None (State exists, but heavily relies on `demoMode` mock override).
- **Estimated LOC**: ~20 lines (Remove mock generation, enforce API usage).
- **Classification**: **READY TO WIRE**

### 2. Trade History & Live Positions
- **Feature**: List of open positions and recent executions.
- **Backend Ready?**: YES (`GET /api/portfolio/recent-transactions` and `/ws/user/{user_id}`)
- **Frontend Ready?**: YES (`LivePositions` component in `DashboardUpgrades.jsx`)
- **API Ready?**: NO (Endpoint is unmapped in `src/api/modules/portfolio.js`)
- **WebSocket Ready?**: NO (Frontend does not connect to `/ws/user/{user_id}`)
- **Missing Files**: None
- **Missing State**: Need to add `positions` state to `PremiumDashboard` to pass into `LivePositions`.
- **Estimated LOC**: ~40 lines (Add API mapping, add React state, map props).
- **Classification**: **REQUIRES API PATCH**

### 3. Performance Metrics
- **Feature**: Grid of stats (Win Rate, Sharpe Ratio, Profit Factor).
- **Backend Ready?**: YES (`GET /api/analytics/performance`)
- **Frontend Ready?**: YES (`PerformanceMetrics` in `DashboardUpgrades.jsx`)
- **API Ready?**: NO (Endpoint is unmapped in frontend `apiClient`)
- **WebSocket Ready?**: N/A
- **Missing Files**: None
- **Missing State**: Need to add `metrics` state to `PremiumDashboard`.
- **Estimated LOC**: ~30 lines.
- **Classification**: **REQUIRES API PATCH**

### 4. PnL Heatmap
- **Feature**: 2D grid showing daily PnL intensity.
- **Backend Ready?**: YES (`GET /api/portfolio/heatmap`)
- **Frontend Ready?**: YES (`PnlHeatmap` component inside `PremiumDashboard`)
- **API Ready?**: NO (Endpoint is unmapped).
- **WebSocket Ready?**: N/A
- **Missing Files**: None
- **Missing State**: `heatmapData` state is missing (currently hardcoded as a randomized 28-item array).
- **Estimated LOC**: ~25 lines.
- **Classification**: **REQUIRES API PATCH**

### 5. Strategy/Bot Monitoring Console
- **Feature**: Comprehensive real-time view of active bots, signals, and infrastructure health.
- **Backend Ready?**: NO (No dedicated REST endpoint that returns aggregated bot telemetry. It exists fragmented across `/api/strategies`).
- **Frontend Ready?**: YES (`BotMonitoringConsole.jsx` has extensive UI).
- **API Ready?**: NO.
- **WebSocket Ready?**: NO. Severe architectural mismatch. Frontend expects a multiplexed router on `/ws/telemetry` for `bot_status`, `signal_trace`, etc. Backend only has per-user balance/order streams or per-session DAG streams.
- **Missing Files**: Backend may need a dedicated telemetry multiplexer or the frontend needs to be completely rewritten to handle disjointed WebSocket routes.
- **Missing State**: N/A (State is there, but mock-heavy).
- **Estimated LOC**: ~300+ lines (Requires bridging the WebSocket gap).
- **Classification**: **BLOCKED** (Requires WS architecture alignment first).

### 6. Risk Events & Alerts
- **Feature**: Banner alerts for critical events (kill switch, drawdown limits).
- **Backend Ready?**: NO (ExecutionGuard logs it, but no dedicated `/ws/risk` endpoint or push mechanism exists outside of general errors).
- **Frontend Ready?**: YES (`RiskAlertBanner` component).
- **API Ready?**: NO.
- **WebSocket Ready?**: NO.
- **Missing Files**: Backend risk telemetry emitter.
- **Estimated LOC**: ~100+ lines.
- **Classification**: **BLOCKED**

## Conclusion
The dashboard's REST-based analytics (Equity Curve, Heatmap, Performance Metrics, Trade History) are fundamentally **READY TO WIRE** or simply **REQUIRE AN API PATCH** to map the endpoints and state. The real-time bot monitoring and signal tracing via WebSockets is **BLOCKED** due to a complete mismatch in WebSocket routing and multiplexing architectures.
