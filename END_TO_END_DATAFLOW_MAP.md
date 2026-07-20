# END-TO-END DATA FLOW MAP

## 1. Equity Curve Flow
```text
QuestDB (equity_curve table)
 ↓
TelemetryEngine.execute_query()
 ↓
GET /api/portfolio/equity-curve
 ↓
apiClient (endpoints.user.getEquityCurve)
 ↓
PremiumDashboard.jsx (equityCurve state)
 ↓
EquityCurveChart (recharts <AreaChart>)
```

## 2. Trade History Flow
```text
QuestDB (executions table)
 ↓
TelemetryEngine.execute_query()
 ↓
GET /api/portfolio/recent-transactions
 ↓
[GAP: Missing API module binding]
 ↓
[GAP: Missing state variable in Dashboard]
 ↓
LivePositions (DashboardUpgrades.jsx)
```

## 3. Performance Metrics Flow
```text
QuestDB (executions table)
 ↓
TelemetryEngine.execute_query()
 ↓
GET /api/analytics/performance
 ↓
[GAP: Missing API module binding]
 ↓
[GAP: Missing state variable]
 ↓
PerformanceMetrics (DashboardUpgrades.jsx)
```

## 4. Live Bot Monitoring Flow
```text
[GAP: Backend strategy/bot aggregation service]
 ↓
[GAP: Backend /ws/bots endpoint or REST equivalent]
 ↓
BotMonitoringConsole.jsx (WebSocket or REST fetch)
 ↓
BotMonitoringConsole.jsx (bots state)
 ↓
Active Bots List UI
```

## 5. Signal Trace Flow
```text
Backend DAG Event Loop
 ↓
ws_server.py (/ws/{session_id} or similar)
 ↓
[GAP: Frontend expects multiplexed /ws/telemetry with 'signal_trace' channel]
 ↓
BotMonitoringConsole.jsx (signals state)
 ↓
Signal Activity UI
```

## 6. Risk Events Flow
```text
SafetyMonitor / ExecutionGuard
 ↓
[GAP: No dedicated backend risk event stream endpoint]
 ↓
[GAP: Frontend expects multiplexed 'risk_events' channel]
 ↓
BotMonitoringConsole.jsx (riskMetrics state)
 ↓
Risk Status Grid UI
```
