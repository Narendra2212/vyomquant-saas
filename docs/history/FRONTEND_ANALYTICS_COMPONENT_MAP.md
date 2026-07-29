# FRONTEND ANALYTICS COMPONENT MAP

## 1. PremiumDashboard.jsx
- **Path**: `algo22-terminal/src/pages/PremiumDashboard.jsx`
- **State Variables**: `stats`, `equityCurve`, `activeBots`, `isPnlHeatmapLoading`
- **Props**: None (Top-level page component)
- **useEffects**: 
  - `useEffect(() => { loadDashboardData() }, [demoMode])` 
  - Generates fake math-random equity curve and fake bot entries.
- **WebSocket Subscriptions**: None
- **Mock Data Generators**: Heavy random generation if `demoMode` is true or if API calls fail.
- **DemoMode Toggles**: Uses `const [demoMode, setDemoMode]` globally or locally.
- **Charts**: `<AreaChart>` from `recharts` for equity.
- **Tables/Cards**: Stat header cards, Open Positions list, `PnlHeatmap` component.

## 2. BotMonitoringConsole.jsx
- **Path**: `algo22-terminal/src/components/BotMonitoringConsole.jsx`
- **State Variables**: `bots`, `signals`, `exchanges`, `riskMetrics`, `infrastructure`, `selectedBot`, `filter`, `isPaused`
- **Props**: `wsClient`, `accountId`, `onBotError`, `onSignalClick`
- **useEffects**:
  - `useEffect(() => { ... })` sets up massive mock arrays for bots, exchanges, risk, infrastructure.
  - `useEffect(() => { ... })` listens to `wsClient` to append signals.
- **WebSocket Subscriptions**: 
  - `wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleSignal)`
  - `wsClient.subscribe(WS_CHANNELS.BOT_STATUS, handleInfrastructure)`
- **Mock Data Generators**: Yes, initial state is entirely populated by mock data.
- **DemoMode Toggles**: N/A
- **Charts**: None
- **Tables/Cards**: Active Bots list, Signal trace list, Exchange connectivity grid, Risk Status grid, Infrastructure grid.

## 3. StrategyDashboard.jsx
- **Path**: `algo22-terminal/src/components/StrategyDashboard.jsx`
- **State Variables**: `strategies`, `selectedStrategy`, `criticalAlerts`, `lastUpdate`
- **Props**: `wsClient`, `accountId`, `onStrategyError`, `onStrategySelect`
- **useEffects**:
  - `useEffect(() => { ... })` sets up WebSocket listeners.
- **WebSocket Subscriptions**:
  - `wsClient.subscribe('strategy_update', handleMessage)`
  - `wsClient.subscribe('strategy_status', handleMessage)`
- **Mock Data Generators**: None. Populates entirely from WS.
- **Charts**: None.
- **Tables/Cards**: Strategy list with status/metrics cards.

## 4. DashboardUpgrades.jsx
- **Path**: `algo22-terminal/src/components/DashboardUpgrades.jsx`
- **State Variables**: Local interaction states (`hoverData`, `selectedPosition`)
- **Props**: `data`, `metrics`, `status`, `positions`, `statItems`
- **useEffects**: None (Pure UI components).
- **WebSocket Subscriptions**: None.
- **Mock Data Generators**: None.
- **Charts**: `EquityCurveChart` (`<AreaChart>`), `DrawdownChart` (custom SVG path).
- **Tables/Cards**: `LivePositions` list, `StatGrid`, `PerformanceMetrics` card, `SystemStatus` card, `RiskAlertBanner`.
