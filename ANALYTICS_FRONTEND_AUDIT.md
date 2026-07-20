# ANALYTICS FRONTEND AUDIT

## 1. PremiumDashboard
**Exact file path**: `algo22-terminal/src/pages/PremiumDashboard.jsx`
**Component name**: `PremiumDashboard`
* **Existing API calls**:
  * `endpoints.user.getStats()`
  * `endpoints.user.getEquityCurve()`
  * `endpoints.strategies.list()`
* **Existing mock data**:
  * Massive use of `demoMode` mock objects.
  * Hardcoded fallback `stats` (`totalEquity: 12450.00`, `totalPnl: 342.50`, `winRate: 68.5`, etc.).
  * `equityCurve` uses an iterative random walk (`Math.random()`) when in demo mode or when API returns empty.
  * `activeBots` uses an array of mock strategy objects if in demo mode.
  * `PnlHeatmap` component inside generates 28 days of random numbers.
* **Existing charts**: `<AreaChart>` (Recharts) for Portfolio Performance.
* **Existing tables**: Simple mapped `div` list for "Open Positions", plus a CSS-grid for `PnlHeatmap`.
* **Existing websocket usage**: None.

## 2. BotMonitoringConsole
**Exact file path**: `algo22-terminal/src/components/BotMonitoringConsole.jsx`
**Component name**: `BotMonitoringConsole`
* **Existing API calls**: None direct.
* **Existing mock data**:
  * Entire `useEffect` on load initializes mock data for `bots` (e.g., BTC Scalper, ETH Arbitrage).
  * Hardcoded `exchanges` array (Binance, Coinbase, Kraken, Bybit).
  * Hardcoded `riskMetrics` (drawdown, exposure, kill switch).
  * Hardcoded `infrastructure` (websocket, redis, queue depth, latency).
* **Existing charts**: None.
* **Existing tables**: List view of "Active Bots", List view of "Signal Activity" trace, Grid view for Exchange Connectivity and Infrastructure.
* **Existing websocket usage**: 
  * Subscribes to `WS_CHANNELS.SIGNAL_TRACE`
  * Subscribes to `WS_CHANNELS.BOT_STATUS`
  * Relies on a generic `wsClient.subscribe` multiplexer instead of dedicated endpoint URLs.

## 3. StrategyDashboard
**Exact file path**: `algo22-terminal/src/components/StrategyDashboard.jsx`
**Component name**: `StrategyDashboard`
* **Existing API calls**: None.
* **Existing mock data**: No hardcoded mock arrays on load, but fully relies on WebSockets to populate data.
* **Existing charts**: None.
* **Existing tables**: List view of strategies with columns for status, PnL, Signals/min, Last Signal, Active Positions, Symbol.
* **Existing websocket usage**:
  * Subscribes directly to raw string channels: `strategy_update`, `strategy_status`, `signal_update`, `strategy_pnl`, `strategy_positions` using `wsClient.subscribe()`.

## 4. DashboardUpgrades
**Exact file path**: `algo22-terminal/src/components/DashboardUpgrades.jsx`
**Component names**: `RiskAlertBanner`, `EquityCurveChart`, `DrawdownChart`, `LivePositions`, `StatGrid`, `PerformanceMetrics`, `SystemStatus`
* **Existing API calls**: None (Pure UI components).
* **Existing mock data**: None (Accepts props).
* **Existing charts**: 
  * `<AreaChart>` (Recharts) for `EquityCurveChart`.
  * Custom pure SVG `<path>` and `<circle>` generation for `DrawdownChart`.
* **Existing tables**:
  * `LivePositions` list view.
  * `StatGrid` card layout.
* **Existing websocket usage**: None.
