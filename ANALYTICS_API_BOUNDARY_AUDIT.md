# ANALYTICS API BOUNDARY AUDIT

## Overview
This audit compares the backend analytics endpoints and websockets (discovered in Sprint 1C.1) against what the frontend is actually requesting in the source code.

## 1. REST API Consumption Check

| Backend Endpoint | Frontend Consumption Status | Findings |
|-----------------|-----------------------------|----------|
| `/api/portfolio/equity-curve` | Partially Consumed | Found exactly once in `App.jsx` (L2998). Abstracted into `endpoints.user.getEquityCurve()` inside `PremiumDashboard.jsx`, but often falls back to randomized mock data via `demoMode`. |
| `/api/portfolio/recent-transactions` | **NOT CONSUMED** | No trace in frontend source code (`src/**/*.js`, `src/**/*.jsx`). The frontend has a "Live Positions" component but does not fetch recent transactions from this endpoint. |
| `/api/analytics/performance` | **NOT CONSUMED** | No REST calls hit `/api/analytics/performance`. The UI component `PerformanceMetrics` accepts a `metrics` prop but is not wired to this REST endpoint. |
| `/api/bots/*` | **NOT CONSUMED** | The frontend uses `/api/strategies/list` (`endpoints.strategies.list()`) to fetch list data. The backend `bot_monitoring.py` `/api/bots` endpoints are completely ignored by the frontend. |

## 2. WebSocket Consumption Check

| Backend Endpoint | Frontend Consumption Status | Findings |
|-----------------|-----------------------------|----------|
| `/ws/bots` | **NOT CONSUMED** | The frontend WebSocket client (`websocketClient.js`) hardcodes its connection URL path to `/ws/telemetry`. It never attempts to connect to `/ws/bots`. |
| `/ws/signals` | **NOT CONSUMED** | The frontend does not attempt to connect to `/ws/signals`. |

## 3. WebSocket Subscription Disconnect
The frontend expects a single multiplexed connection to `/ws/telemetry` where it can issue subscription commands:
```javascript
// In BotMonitoringConsole.jsx
wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleSignal);
wsClient.subscribe(WS_CHANNELS.BOT_STATUS, handleInfrastructure);
```
However, the backend (from Sprint 1B) implemented dedicated WebSocket endpoints:
```python
# In bot_monitoring.py
@router.websocket("/ws/bots")
@router.websocket("/ws/signals")
```
This is a hard boundary failure. The frontend is listening on channels over a multiplexed connection that doesn't match the dedicated websocket routes provided by the backend.

## Summary Conclusion
The frontend UI components exist (e.g., `BotMonitoringConsole`, `PremiumDashboard`) but they are largely powered by hardcoded, randomly generated mock data and misaligned websocket subscriptions. The new backend analytics endpoints are completely un-wired.
