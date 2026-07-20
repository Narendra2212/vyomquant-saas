# MOCK DATA REPLACEMENT MAP

## 1. PremiumDashboard.jsx
- **Mock Source**: `demoMode` state and `Math.random()` generation inside `useEffect`.
- **Target Variables**: `stats`, `equityCurve`, `activeBots`, `PnlHeatmap` grids.
- **Replacement Endpoints**:
  - `stats`: `GET /api/analytics/performance`
  - `equityCurve`: `GET /api/portfolio/equity-curve` (Ensure `demoMode` bypass is removed)
  - `activeBots`: `GET /api/strategies` (or via WebSockets)
  - `PnlHeatmap`: `GET /api/portfolio/heatmap`

## 2. BotMonitoringConsole.jsx
- **Mock Source**: Hardcoded arrays initialized on mount in `useEffect`.
- **Target Variables**: `bots`, `signals`, `exchanges`, `riskMetrics`, `infrastructure`.
- **Replacement Endpoints**:
  - `bots`: Needs a backend REST endpoint or WebSocket mapping (e.g., aggregating strategy states).
  - `signals`: `WS_CHANNELS.SIGNAL_TRACE` must be mapped to real backend WebSocket stream (`/ws/{session_id}` or similar).
  - `exchanges`: Exchange status should come from a health endpoint.
  - `riskMetrics`: Should come from a dedicated risk telemetry endpoint or via `/ws/pnl`.
  - `infrastructure`: Should come from backend Redis heartbeat or system health endpoints.

## 3. StrategyDashboard.jsx
- **Mock Source**: N/A. Component listens to `wsClient` but fails because the client disconnects or channels are invalid.
- **Target Variables**: `strategies`
- **Replacement Endpoints**: Needs correct WebSocket connection to `/ws/{task_id}` or `/ws/{session_id}` instead of the generic unused `strategy_update` channels.

## 4. LivePositions (DashboardUpgrades.jsx)
- **Mock Source**: Passed in as props, but parent often generates fake data in demo mode.
- **Target Variable**: `positions` list prop.
- **Replacement Endpoints**: `GET /api/portfolio/recent-transactions` or the `initial_positions` push from `/ws/user/{user_id}`.
