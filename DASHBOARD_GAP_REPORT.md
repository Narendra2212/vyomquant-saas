# DASHBOARD GAP REPORT

## Goal
Determine whether Sprint 1C requires new analytics features or merely wiring existing backend telemetry into existing frontend dashboards.

## 1. UI Components vs Data Reality
The frontend already possesses the necessary visual components to display comprehensive analytics.
- **Charts**: `EquityCurveChart`, `DrawdownChart`, `PremiumDashboard` `<AreaChart>`
- **Tables & Metrics**: `PerformanceMetrics`, `StatGrid`, `LivePositions`, `SystemStatus`, `RiskAlertBanner`, `BotMonitoringConsole` (bots, signals, risk, infrastructure).

However, these components are almost entirely decoupled from the backend. They operate on hardcoded state, generated random data, or mock `demoMode` structures.

## 2. API Wiring Gaps
- **REST Disconnect**:
  - The frontend has NO `fetch`/`get` calls to `/api/portfolio/recent-transactions`, `/api/analytics/performance`, or `/api/bots/*`.
  - The frontend calls `/api/portfolio/equity-curve` only under specific circumstances but defaults to mocked random-walks.
- **WebSocket Disconnect**:
  - The frontend expects a single WebSocket connection to `/ws/telemetry` which handles multiplexed channels (`bot_status`, `signal_trace`, etc.).
  - The backend provides dedicated un-multiplexed endpoints (`/ws/bots`, `/ws/signals`).

## 3. Backend Persistence Gaps (from SPRINT 1C.1)
- **Backtesting Metrics**: Backend backtest execution results are returned directly in-memory to the client upon request. They are **not persisted** to QuestDB or Redis. Therefore, any dashboard attempting to show historical backtest stats has no database table to pull from.
- **Live Trading Equity**: Redis stores live equity streams, but `equity-curve` aggregates need mapping to QuestDB if long-term historical charts are needed.

## Conclusion
**Sprint 1C does NOT require building new visual dashboard features or React components from scratch.** The presentation layer is rich and complete.

**Sprint 1C requires massive WIRING and BOUNDARY ALIGNMENT.**
The work required is strictly data-plumbing:
1. Replace all mock arrays in `BotMonitoringConsole` and `PremiumDashboard` with `apiClient` fetches hitting the backend endpoints mapped in Sprint 1C.1.
2. Resolve the WebSocket mismatch: Either update the frontend to connect to the dedicated `/ws/bots` and `/ws/signals` endpoints, or update the backend to support a multiplexed `/ws/telemetry` router.
3. Bridge the REST gap: Connect `/api/portfolio/recent-transactions` to the `LivePositions` / open positions list, and map `/api/analytics/performance` to the `PerformanceMetrics` grid.
