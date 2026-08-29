# DASHBOARD PHASE 2C — SAFETY & REAL-TIME HARDENING SOURCE MAP
**VyomQuant SaaS Terminal**  
**Document Type**: Pre-Implementation Forensic Source Map & Safety Audit  
**Phase**: Phase 2C  
**Timestamp**: 2026-08-26T15:28:00Z  
**Status**: **`AUDIT COMPLETE — READY FOR IMPLEMENTATION`**

---

## 1. Authoritative Backend APIs & Contract Mapping

All required safety, risk, and telemetry endpoints already exist in the backend architecture. **Zero new REST endpoints are required.**

| Operational Feature | Authoritative Endpoint | HTTP Method | Request Payload | Response Schema | Tenant / Environment Isolation |
|---|---|---|---|---|---|
| **Emergency Kill Switch (Activate)** | `/api/risk/kill-switch` | `POST` | `{"reason": "Manual emergency halt"}` | `{"status": "halted", "kill_switch_active": true, "message": "..."}` | Multi-tenant JWT auth; immediate global and user execution block in `UnifiedExecutionEngine`. |
| **Emergency Kill Switch (Recover)** | `/api/risk/kill-switch/recover` | `POST` | Empty | `{"status": "active", "kill_switch_active": false, "message": "..."}` | JWT auth; resets `_user_kill_switch_state[uid]` and broadcasts recovery event. |
| **Audit Risk Violations** | `/api/risk/violations` | `GET` | Query `?limit=50` | `[{"violation_type": "...", "reason": "...", "timestamp": "..."}]` | Strictly filtered by `user_id`. |
| **Strategy Pause** | `/api/strategies/{id}/pause` | `POST` | Empty | `{"status": "paused", "strategy_id": "...", "bot_stopped": true}` | Validates user ownership; signals `BotRunner` / `FleetManager`. |
| **Strategy Resume** | `/api/strategies/{id}/resume` | `POST` | Empty | `{"status": "running", "strategy_id": "...", "bot_started": true}` | Validates strategy blueprint & ML model readiness before starting bot. |
| **Deployment Lifecycle** | `/api/deployments/{id}/pause`<br>`/api/deployments/{id}/resume` | `POST` | `{"reason": "..."}` | Deployment state machine transition record | Formal lifecycle state machine (`RUNNING`, `PAUSED`, `STOPPED`, `FAILED`). |
| **Notifications Feed** | `/api/notifications` | `GET` | Query `?unread_only=true` | `[{"id": "...", "category": "risk", "severity": "critical", "title": "...", "message": "..."}]` | Authoritative source for order rejections and execution errors. |
| **Single Dashboard Aggregation** | `/api/dashboard` | `GET` | Query `?environment=live&equity_days=30` | Full normalized `DashboardData` payload | Cached separately under `dashboard:{uid}:{env}:{days}`. |

---

## 2. Real-Time WebSocket Infrastructure

The platform already contains a centralized WebSocket architecture. **Zero second WebSocket servers or protocols should be created.**

### Existing WebSocket Routes & Channels:
1. **Dashboard Incremental Channel (`/ws/dashboard`)**:
   - **Location**: `backend_app/api_ws/ws_routes.py` line 918
   - **Auth**: Token validation via `_validate_ws_token(token, user_id)`
   - **Channel ID**: `dashboard_{user_id}`
   - **Event Broadcaster**: `broadcast_dashboard_update(user_id, update_type, data)`
   - **Supported Update Types**:
     - `strategy_status`: Strategy running/paused/stopped/error transitions
     - `signal_trace`: New algorithmic signals generated
     - `notification`: Risk breach, order fill, or execution failure
     - `risk_alert`: Circuit breaker activation, kill switch trigger
     - `exchange_health`: Venue disconnection, latency status changes
2. **Central Frontend Client (`algo22-terminal/src/websocketClient.js`)**:
   - Built-in reconnection with exponential backoff (max 30s)
   - JWT authentication injection on connection handshake
   - Heartbeat ping/pong (30s interval, 10s timeout)
   - Channel subscription management: `wsClient.subscribe(event_type, callback)`

---

## 3. Normalized Derivatives Position Contract & Liquidation Mathematics

### Position Schema (`NormalizedPosition`):
Every position returned by `dashboard_aggregation_service.py` is normalized across all exchanges (Binance, Bybit, Kraken, OKX) into:
```typescript
interface NormalizedPosition {
  id: string;                    // e.g. "pos_binance_btc_usdt"
  exchange_id: string;           // authoritative: "binance" | "bybit" | "kraken" | "okx" | "paper"
  environment: "live" | "paper";
  symbol: string;                // "BTC/USDT"
  market_type: "spot" | "future" | "swap";
  side: "long" | "short";
  contracts: number;             // position size
  quantity: number;
  entry_price: number;
  mark_price: number;
  notional: number;              // contracts * mark_price
  leverage: number;              // integer leverage (e.g. 1, 3, 10)
  unrealized_pnl: number;        // mark-to-market PnL in quote currency
  unrealized_pnl_pct: number;    // % gain/loss relative to initial margin
  liquidation_price: number | null; // null for spot
  margin: number;                // notional / leverage
  margin_type: "cross" | "isolated";
  timestamp: string;
}
```

### Liquidation Distance Calculation:
For derivatives positions where `liquidation_price != null` and `liquidation_price > 0`:
- **LONG Positions**:
  $$\text{Liquidation Distance \%} = \frac{\text{Mark Price} - \text{Liquidation Price}}{\text{Mark Price}} \times 100$$
- **SHORT Positions**:
  $$\text{Liquidation Distance \%} = \frac{\text{Liquidation Price} - \text{Mark Price}}{\text{Mark Price}} \times 100$$
- **SPOT Positions**:
  - `liquidation_price` is strictly `null`.
  - Liquidation Distance is explicitly `null` and displayed as `—` (Never `$0.00` or `0.0%`).

---

## 4. Execution & Safety Boundary Invariants

### Direct Manual Execution Barrier (Boundary Lock):
- In `backend_app/routers/portfolio.py` lines 380–405, direct manual trade submission from the UI (`/portfolio/close-all`) is **explicitly blocked with HTTP 403 `DIRECT_EXECUTION_BLOCKED`**.
- **Architectural Reason**: All trade execution must flow exclusively through `BotRunner` $\rightarrow$ `ExecutionGuard` $\rightarrow$ `UnifiedExecutionEngine` to ensure idempotency locks, tenant risk limits, and slippage firewalls.
- **Trader Action Path**:
  - **Emergency Action**: Use the **Emergency Kill Switch** (`POST /api/risk/kill-switch`), which blocks all order submissions across all running bots instantly.
  - **Orderly Exit**: Use **Strategy Stop / Pause** (`POST /api/strategies/{id}/pause`), which invokes the strategy's canonical exit block (`action_close_position`) via the bot runner.

---

## 5. What Can Be Reused vs What Must NOT Be Created

### What Can Be Reused Directly:
1. `riskApi.killSwitch({ reason })` in `algo22-terminal/src/api/modules/risk.js`
2. `api.strategies.pause(id)` / `api.strategies.resume(id)`
3. `wsClient` in `algo22-terminal/src/websocketClient.js`
4. `/ws/dashboard` channel in `backend_app/api_ws/ws_routes.py`
5. `dashboardRes.positions`, `dashboardRes.risk`, `dashboardRes.executions` in `Dashboard.jsx`

### What Must NOT Be Created:
1. ❌ DO NOT create a separate direct trade execution endpoint in React.
2. ❌ DO NOT create a second WebSocket protocol or port.
3. ❌ DO NOT create an exchange-specific position parser.
4. ❌ DO NOT create mock latency or fake liquidation distances.
5. ❌ DO NOT modify Admin Panel or reactivate Copilot UI.

---

## 6. Prioritized Implementation Sequence (P0 / P1 / P2)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ P0: CRITICAL TRADING SAFETY (Next Immediate Code Step)                      │
│ 1. Emergency Kill Switch Button & Modal in Dashboard.jsx                    │
│ 2. High-Visibility Order Rejection & Bot Failure Alert Banner               │
│ 3. Margin Mode (CROSS/ISOL) & Liquidation Distance % in Positions Table     │
├─────────────────────────────────────────────────────────────────────────────┤
│ P1: REAL-TIME STREAMING & BOT TELEMETRY                                     │
│ 1. Connect websocketClient to /ws/dashboard for live delta events           │
│ 2. Strategy Bot error telemetry display with 1-click Restart               │
├─────────────────────────────────────────────────────────────────────────────┤
│ P2: UX REFINEMENT & NAVIGATION DENSITY                                      │
│ 1. Compact / Standard layout toggle for high position counts                │
│ 2. Direct Sidebar navigation links to /app/portfolio and /app/trades       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Audit Conclusion & Gate Verdict

**Status**: **`PASS (READY FOR IMPLEMENTATION)`**  
All required authoritative endpoints, WebSocket channels, and normalized contracts are present and verified. No architectural blockers exist.
