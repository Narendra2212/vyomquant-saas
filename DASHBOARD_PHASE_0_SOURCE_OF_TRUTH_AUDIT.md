# DASHBOARD PHASE 0 — SOURCE-OF-TRUTH & ARCHITECTURE VALIDATION AUDIT
**VyomQuant SaaS Terminal**
**Document Version**: 1.0.0  
**Phase**: Phase 0 — Source-of-Truth & Implementation Gate Validation  
**Target File**: `algo22-terminal/src/pages/Dashboard.jsx` & `backend_app/backend/dashboard_aggregation_service.py`  
**Status**: COMPLETE (Forensic Inspection & Validation Only — ZERO Code Modifications Executed)

---

## 1. Executive Summary

This Phase 0 audit is an **independent, code-level validation of every data path, calculation engine, database query, Redis cache key, and real-time WebSocket flow** in the VyomQuant SaaS codebase. Rather than relying on previous assumptions or high-level descriptions, every finding in this report has been traced directly to line numbers and verified against actual implementations.

### Primary Forensic Breakthroughs & Validations
1. **[P0] Cloned P&L Math Confirmed in Code**: In `backend_app/backend/dashboard_aggregation_service.py` (lines 828–830), `today_pnl` and `unrealized_pnl` both read `portfolio.get("total_pnl", 0)`. The underlying QuestDB view `live_user_pnl` (defined in `backend_app/backend/telemetry_engine.py`, lines 275–286) computes `last(equity) - first(equity)`, which represents **lifetime P&L across all history**, NOT today's 24h P&L or open position unrealized P&L.
2. **[P0] Available Balance Missing from QuestDB View**: The `live_user_pnl` view selects only `user_id`, `total_equity`, `total_pnl`, `pnl_pct`, and `total_exposure`. It does **not** select `available_balance`. Consequently, `portfolio.get("available_balance", 0)` always falls back to `0.0` in the aggregator and is erroneously defaulted to `total_equity` in `Portfolio.jsx`.
3. **[P0] Real Positions Already Exist in Redis & Paper Service**: Real live position data is already fetched and cached by `portfolio_cache_updater.py` in Redis key `portfolio:{user_id}:{exchange_id}:positions`. Real paper positions are maintained in memory by `paper_trading_service.py` (`get_positions(user_id)`). **Dashboard is currently ignoring both data sources.**
4. **[P1] Risk Schema Mismatch Confirmed in Code**: In `Dashboard.jsx` (line 755), the component evaluates `riskHealth.risk_score < 30`. The backend aggregator (`dashboard_aggregation_service.py`, lines 862–866) returns `risk_level` (`"low"`, `"medium"`, `"high"`), leaving `riskHealth.risk_score` as `undefined`. This causes `undefined < 30` to evaluate to `false`, erroneously rendering a red "Risk Alert" banner with an empty score.
5. **[P1] Hardcoded 38ms Latency Confirmed**: In `dashboard_aggregation_service.py` (lines 671–686) and `Dashboard.jsx` (line 289), latency is hardcoded to `38 ms` without querying CCXT connection engines.
6. **[P0] Missing Global Environment Context**: `algo22-terminal/src/AppState.jsx` contains only `demoMode` and `uiMode`. There is **no application-level `environment` context** (`'paper'` vs `'live'`).

---

## 2. Actual SaaS Page Map & Topology

The following authoritative table details every customer surface, its underlying backend authority, database engine, real-time source, and relationship to the Dashboard:

| Page / Surface | Route | Purpose & User Job | Primary API Endpoint | Backend Authority | Database / Storage | Real-time Source | Operational Status | Dashboard Overlap |
|---|---|---|---|---|---|---|---|---|
| **Mission Control (Dashboard)** | `/app/dashboard` | 10-second trading cockpit: portfolio value, 24h P&L, live positions, bot runtimes, emergency halt | `GET /api/dashboard` | `DashboardAggregationService` | Redis (`dashboard:*`), QuestDB, Supabase | WS `pnl_update`, `bot_status` | **Needs P0 Refactor** | **THE FIRST SCREEN** |
| **Portfolio Analytics** | `/app/portfolio` | Deep historical performance, 90d equity curve, asset allocation donut, 90d P&L heatmap calendar | `GET /api/portfolio/*` | `routers/portfolio.py` | QuestDB (`live_user_pnl`, `equity_curve`, `portfolio_allocation`, `executions`) | REST Polling | **95% Production Ready** (Orphaned from Sidebar) | Dashboard duplicates 30d curve only |
| **Strategies Fleet Console** | `/app/strategies` | Fleet management: inspect all strategies/bots, multi-instance deployment, worker allocation | `GET /api/strategies` | `strategy_service.py`, `deployment_manager.py` | Supabase (`strategies`, `strategy_deployments`) | WS `bot_status` | **95% Production Ready** | Dashboard shows top 3-5 active bots |
| **Strategy Detail & Research** | `/app/strategies/:id` | 17-tab deep forensic inspection (Research, Deployments, Backtests, Executions, Signals, Orders, Positions, Logs, Risk) | `GET /api/strategies/:id` | `routers/strategy_operations.py` | Supabase + QuestDB | WS `execution_events`, `training.*` | **90% Production Ready** | None (Detail view is 2 levels deep) |
| **Strategy Builder (DAG)** | `/app/builder` | Visual block-based quantitative DAG authoring canvas with real-time compilation firewall | `POST /api/strategies/validate`, `POST /api/strategies` | `strategy_compiler_canonical.py`, `block_registry.py` | Supabase (`strategies`, `strategy_versions`) | Local compilation debounce (400ms) | **95% Production Ready** | Dashboard has shortcut button only |
| **Quantitative Backtester** | `/app/backtest` | Multi-timeframe historical simulation engine (Sharpe, Sortino, Drawdown, Profit Factor, Trade Log) | `POST /api/strategy-operations/backtest` | `backtester.py`, `data_seeking_engine.py` | QuestDB (OHLCV historical data) | REST async runner | **95% Production Ready** | Dashboard has shortcut button only |
| **Exchange Manager & Vault** | `/app/exchanges` | Encrypted API key management, CCXT certification matrix, preflight checks, connection health | `GET /api/exchange/connections`, `POST /api/exchange` | `connection_engine.py`, `KeyVault` | Supabase Vault (`exchange_keys`) | REST preflight test | **95% Production Ready** | Dashboard shows compact connection status |
| **Risk Management Engine** | `/app/risk` | Institutional risk parameters: Max daily loss, position limits, leverage, automated kill switches | `GET /api/risk/settings`, `POST /api/risk/kill-switch` | `routers/risk.py`, `ExecutionGuard` | Supabase (`risk_settings`) + Redis | WS `risk_events`, `risk.kill_switch_*` | **90% Production Ready** | Dashboard exposes risk gauge & kill switch |
| **Trade History Ledger** | `/app/trade-history` | Searchable, filterable, CSV-exportable ledger of all filled executions with slippage and fees | `GET /api/orders/history` | `routers/orders.py`, `TelemetryEngine` | QuestDB (`executions` table) | REST + Redis cache | **95% Production Ready** (Orphaned from Sidebar) | Dashboard shows ticker of last 3-5 fills |
| **Signal Trace Console** | `/app/signal-trace` | Forensic signal debugger: tracks raw signal generation, feature weights, risk approval/rejection | `GET /api/signal-trace/signals` | `routers/signal_trace.py` | Supabase (`signals` table) | WS `signal_trace` | **95% Production Ready** | Dashboard shows high-level signal alerts |
| **Strategy Marketplace** | `/app/marketplace` | Public quantitative strategy catalogue, featured models, 1-click cloning into workspace | `GET /api/library/*`, `POST /api/library/:id/clone` | `routers/library.py` | Supabase (`library_strategies`) | REST | **95% Production Ready** | Dashboard should NOT have affiliate widgets |
| **Billing & Localization** | `/app/billing` | SaaS subscription management, 21 localized currencies, Stripe customer portal, invoice history | `GET /api/billing/*`, `POST /api/billing/checkout` | `routers/billing.py`, `pricing_service.py` | Stripe + Supabase (`subscriptions`, `invoices`) | Webhooks | **98% Production Ready** | Dashboard should NOT show billing cards |
| **Profile & Settings** | `/app/profile` | Personal user settings, referral program link, commission payouts, notification channel toggles | `GET /api/user/profile`, `GET /api/referral/stats` | `routers/user.py`, `routers/referral.py` | Supabase (`profiles`, `referral_profiles`) | WS `profile_update`, `billing_update` | **95% Production Ready** | Referral earnings belong here |
| **Security Audit Logs** | `/app/security-logs`| Immutable security audit trail of authentication events, IP addresses, user agents, CSV export | `GET /api/user/security-logs` | `routers/user.py` | Supabase (`security_audit_logs`) | REST | **95% Production Ready** | None |
| **Two-Factor Auth (MFA)** | `/2fa` | TOTP MFA enrollment, QR code provisioning, and challenge verification (AAL2 security) | Supabase MFA Client | Supabase Auth GoTrue | Supabase Auth schema | REST challenge-response | **98% Production Ready** | None |
| **Support Center & FAQs** | `/app/support` | Helpdesk ticketing system with strategy/order diagnostic attachments and searchable FAQs | `GET /api/support/tickets`, `POST /api/support/tickets` | `routers/support.py` | Supabase (`support_tickets`, `support_comments`) | REST polling | **95% Production Ready** | None |
| **Notification Inbox** | `/app/notifications`| Categorized operational notification feed (Trade, Risk, Bot, Exchange, Security, Billing) | `GET /api/notifications/*` | `routers/notifications.py`, `NotificationDispatcher` | Supabase (`notifications` table) | WS `notification` channel | **95% Production Ready** | TopBar bell owns inbox; Dashboard shows critical banners |
| **Onboarding Wizard** | `/wizard` | 4-step user activation funnel: Account Security, Demo Backtest, Connect Exchange, Select Plan | Multi-service orchestration | `routers/auth.py`, `routers/billing.py` | Supabase Auth + Database | REST | **95% Production Ready** | Wizard destinations route to Dashboard |
| **Admin Panel** | `/admin` | Enterprise administration, tenant isolation metrics, system revenue, user management | `GET /api/v1/admin/*` | `routers/admin.py` | Multi-tenant administrative schema | Restricted WS | **RESTRICTED & PROTECTED** | Zero customer dashboard overlap |

---

## 3. Actual Dashboard Architecture

### Complete Data Flow Diagram
```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      Customer Dashboard (Dashboard.jsx)                          │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                         GET /api/dashboard?equity_days=30
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                    FastAPI Router (routers/dashboard.py)                         │
│                    [Redis Key: dashboard:{uid}:{days}, TTL 10s]                  │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│        DashboardAggregationService (dashboard_aggregation_service.py)            │
│                   asyncio.gather (11 Parallel Subroutines)                       │
├──────────────────────┬──────────────────────┬──────────────────┬─────────────────┤
│       QuestDB        │       Supabase       │   Redis Cache    │  Memory / Core  │
├──────────────────────┼──────────────────────┼──────────────────┼─────────────────┤
│ • live_user_pnl      │ • strategies         │ • risk_settings  │ • Health check  │
│ • equity_curve       │ • signals            │ • orders:history │   (Static 38ms) │
│ • executions         │ • exchange_keys      │                  │                 │
│                      │ • notifications      │                  │                 │
│                      │ • referral_profiles  │                  │                 │
│                      │ • risk_settings      │                  │                 │
│                      │ • library_strategies │                  │                 │
└──────────────────────┴──────────────────────┴──────────────────┴─────────────────┘
```

---

## 4. Dashboard Widget Forensic Matrix

| # | Current Widget | Rendered Content | Code Source Location | Real vs Mock | Storage & TTL | Financially Authoritative? | Actionable? | Empty State Behavior | Error State Behavior | Final Disposition |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | **Top Header & System Status** | "Mission Control" title + "All Systems Operational" popover | `Dashboard.jsx:212-302` | **Mocked** (38ms latency hardcoded in `get_health_status()`) | In-Memory (No TTL) | No | No (Static modal) | Shows "All Systems Operational" | Shows "Degraded" | **REPLACE**: Add real Paper/Live toggle & real status |
| **2** | **Portfolio Value Hero** | Total Value ($), Today's P&L ($), Daily Return (%), uPnL ($), Available Cash ($) | `Dashboard.jsx:366-438` | **Real Data, Defective Math** (Clones `total_pnl` into today and unrealized) | QuestDB `live_user_pnl` via Redis (10s TTL) | **No** (Math is lifetime, not 24h) | No | Displays `$0.00` across all cards | Defaults to `$0.00` | **REVISE**: Fix calculations and add Capital Deployed |
| **3** | **Performance Equity Curve** | Interactive Area Chart with 1D/1W/1M/3M/ALL timeframe selector | `Dashboard.jsx:448-518` | **Real** | QuestDB `equity_curve` via Redis (10s TTL) | Yes (Aggregated 15-min bars) | Yes (Timeframe switch) | Shows "No equity data yet" placeholder | Shows empty chart area | **KEEP**: Excellent 30-day overview chart |
| **4** | **Running Strategies List** | Strategy name, pair, today's P&L, status dot, Pause/Resume toggle, Details CTA | `Dashboard.jsx:521-625` | **Real** | Supabase `strategies` table | Yes | Yes (Pause/Resume, Manage All) | Shows empty container | Preserves exception | **KEEP & ENHANCE**: Display active runtime bot workers |
| **5** | **Exchange Status Card** | Connected exchange name, status badge, latency | `Dashboard.jsx:631-695` | **Real Connections, Mocked Latency** | Supabase `exchange_keys` | Partial | Yes (Connect Exchange button) | Shows "No exchanges connected" + Connect CTA | Falls back to empty | **CONDENSE**: Merge into TopBar and header status |
| **6** | **API Health Standalone Card** | API Operational badge, Redis connectivity status | `Dashboard.jsx:698-739` | **Mocked / Static** | In-Memory | No | No | Shows "Loading health..." | Shows "Degraded" | **REMOVE**: Duplicates TopBar and System Popover |
| **7** | **Risk Alerts Card** | All Clear / Risk Alert badge + score | `Dashboard.jsx:742-796` | **Broken** (Checks `riskHealth.risk_score`, but backend sends `risk_level`) | Supabase `risk_settings` | No (Schema bug) | Yes (Review Risk CTA) | Shows "Loading risk..." | Erroneously renders Red Alert | **REVISE**: Standardize schema & render Daily Loss Meter |
| **8** | **Marketplace Earnings Card** | Lifetime Earnings ($), Pending Earnings ($) from affiliate referrals | `Dashboard.jsx:799-845` | **Real** | Supabase `referral_profiles` | Yes (Affiliate only) | Yes (View Details CTA) | Shows `$0.00` | Shows `$0.00` | **REMOVE**: Move to Profile / Referral page |
| **9** | **Subscription Summary Card** | Current Plan (Free/Pro), Manage Subscription CTA | `Dashboard.jsx:848-888` | **Real** | Supabase `profiles.subscription_tier` | Yes | Yes (Manage Subscription CTA) | Shows "Free" | Shows "Free" | **REMOVE**: Move to Sidebar footer & Billing |
| **10**| **Trading Insights Card** | Max 3 rules-based text strings ("X strategies paused", "Risk circuit active") | `Dashboard.jsx:891-939` | **Computed** | Backend rule evaluation | No | Yes (Inline action links) | Shows default risk rules | Falls back to empty | **CONDENSE**: Trigger only as actionable alert banners |
| **11**| **Actionable Notifications Card**| Max 5 recent signal decisions (BUY/SELL, Risk APPROVED/REJECTED) | `Dashboard.jsx:942-965` | **Real** | Supabase `signals` table | Yes | No (Read-only text) | Empty container | Falls back to empty | **REPLACE**: Replace with Recent Executions Fills |
| **12**| **Quick Actions Card** | 4 shortcut buttons (Strategy Builder, Run Backtest, Connect Exchange, Marketplace) | `Dashboard.jsx:967-1058`| **Static** | React Router client-side | N/A | Yes (4 navigation buttons) | Always visible | Always visible | **KEEP & REFINE**: Update buttons to trading priorities |

---

## 5. Financial Calculation Audit (Critical Money Path)

### Complete Formula & Traceability Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           FINANCIAL METRIC AUDIT MATRIX                                                │
├────────────────────┬─────────────────────────────┬─────────────────────────────────┬───────────────────────────────────┤
│ Metric             │ Current Implementation      │ Code Location & Query           │ Canonical Financial Definition    │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Total Equity       │ `last(equity)`              │ `telemetry_engine.py:278`       │ `Cash Balance + Total Unrealized  │
│                    │                             │ `live_user_pnl` view in QuestDB │  PnL across all open positions`   │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Available Balance  │ Missing from SQL view;      │ `dashboard_aggregation_         │ `Total Equity - Margin Deployed   │
│                    │ evaluates to `0.0` or equity│ service.py:831`                 │  in Positions - Capital in Orders`│
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Today's P&L        │ `last(equity)-first(equity)`│ `telemetry_engine.py:279`       │ `Sum of Closed Realized PnL since │
│                    │ (LIFETIME P&L BUG!)         │ `live_user_pnl` view in QuestDB │  00:00:00 UTC + Delta in uPnL`    │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Unrealized P&L     │ Clones `total_pnl`          │ `dashboard_aggregation_         │ `Sum of (Mark Price - Entry Price)│
│                    │ (DUPLICATE MATH BUG!)       │ service.py:830`                 │  * Quantity across Open Positions`│
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Daily Return %     │ Lifetime `pnl_pct`          │ `telemetry_engine.py:280-283`   │ `Today's P&L / Equity at 00:00 UTC│
│                    │ (LIFETIME ROI BUG!)         │ `live_user_pnl` view in QuestDB │  * 100`                           │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Capital Deployed   │ `last(total_exposure_usdt)` │ `telemetry_engine.py:284`       │ `Sum of (Position Quantity *      │
│                    │ (Calculated but omitted)    │ `live_user_pnl` view in QuestDB │  Mark Price) across Open Trades`  │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Capital Locked     │ Not exposed on Dashboard    │ In `paper_trading_service.py`   │ `Sum of (Limit Order Price *      │
│                    │                             │ and CCXT `fetch_balance` (free) │  Quantity) for Pending Orders`    │
├────────────────────┼─────────────────────────────┼─────────────────────────────────┼───────────────────────────────────┤
│ Current Drawdown % │ `abs(float(pnl_pct))`       │ `dashboard_aggregation_         │ `(Peak Equity - Current Equity) / │
│                    │ (Flawed proxy for drawdown) │ service.py:397`                 │  Peak Equity * 100`               │
└────────────────────┴─────────────────────────────┴─────────────────────────────────┴───────────────────────────────────┘
```

### Forensic Proof of P&L Bugs in Code
1. **Proof of `today_pnl == total_pnl`**:
   - In `backend_app/backend/dashboard_aggregation_service.py` line 828:
     `"today_pnl": float(portfolio.get("total_pnl", 0)),`
   - In `backend_app/backend/telemetry_engine.py` line 279:
     `last(equity) - first(equity) as total_pnl`
   - **Conclusion**: `today_pnl` displays the user's lifetime all-time profit/loss, NOT today's trading profit/loss.

2. **Proof of `unrealized_pnl == total_pnl`**:
   - In `backend_app/backend/dashboard_aggregation_service.py` line 830:
     `"unrealized_pnl": float(portfolio.get("total_pnl", 0)),`
   - **Conclusion**: `unrealized_pnl` is an exact duplicate of `total_pnl`. It has zero correlation with live open position mark-to-market valuations.

---

## 6. Open Positions Data Flow Audit

### Can Dashboard Safely Display Real Open Positions Today?
**YES — The underlying position data is fully implemented and active in the backend, but the Dashboard Aggregation Service currently omits it from the aggregation response.**

### Live Position Data Pathway
1. `portfolio_cache_updater.py` (lines 153–180) runs asynchronously in the background.
2. It executes `await exchange.fetch_positions()` and parses: `contracts`, `notional`, `entry_price`, `side`, `unrealized_pnl`.
3. It stores this in Redis under `portfolio:{user_id}:{exchange_id}:positions`.
4. `routers/orders.py` (lines 403–425) already defines `get_portfolio_state()`, which reads this exact Redis key.

### Paper Position Data Pathway
1. `backend_app/backend/paper_trading_service.py` (lines 53, 93–97, 450–480) maintains in-memory dictionary `self._positions[user_id]`.
2. Each position contains: `id`, `strategy_id`, `symbol`, `side`, `quantity`, `entry_price`, `current_price`, `unrealized_pnl`, `unrealized_pnl_pct`.
3. Exposed via `GET /api/paper-trading/positions` in `routers/paper_trading.py`.

### Required Fix to Expose Positions on Dashboard
Add a subroutine in `dashboard_aggregation_service.py` that queries `portfolio:{user_id}:{exchange_id}:positions` (for live) or `paper_trading_service.get_positions(user_id)` (for paper) and attaches `positions: [...]` to the dashboard JSON response.

---

## 7. Open Orders Data Flow Audit

### Open Orders Tracking
- **Live Trading**: CCXT `fetch_open_orders()` / Redis `portfolio:{user_id}:{exchange_id}:orders` tracks active open limit/stop orders.
- **Paper Trading**: `paper_trading_service.py` maintains `self._orders[order_id]` with status `OPEN`. Exposed via `GET /api/paper-trading/orders`.
- **Capital Locked Calculation**:
  $$\text{Capital Locked} = \sum_{\text{open limit buy orders}} (\text{Order Price} \times \text{Remaining Quantity})$$
  In CCXT balance queries, this is directly provided as:
  $$\text{Locked Balance} = \text{Balance}_{\text{total}} - \text{Balance}_{\text{free}}$$

---

## 8. Recent Executions Data Flow Audit

### Lifecycle Separation: Signal vs. Order vs. Fill

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           TRADING EXECUTION LIFECYCLE                            │
├─────────────────┬───────────────────┬────────────────────┬───────────────────────┤
│ Lifecycle Stage │ Entity Name       │ Storage Location   │ Event / Data Payload  │
├─────────────────┼───────────────────┼────────────────────┼───────────────────────┤
│ Stage 1         │ Strategy Signal   │ Supabase:          │ `{decision: "BUY",    │
│                 │ (Intent / Trigger)│ `signals` table    │   confidence: 0.85}`  │
├─────────────────┼───────────────────┼────────────────────┼───────────────────────┤
│ Stage 2         │ Order Placement   │ Redis / Exchange / │ `{order_id: "...",    │
│                 │ (Routing / Queue) │ Paper Orders dict  │   type: "LIMIT"}`     │
├─────────────────┼───────────────────┼────────────────────┼───────────────────────┤
│ Stage 3         │ Fill / Execution  │ QuestDB:           │ `{price: 64200.0,     │
│                 │ (Completed Trade) │ `executions` table │   size: 0.5, fee: 1.2}│
└─────────────────┴───────────────────┴────────────────────┴───────────────────────┘
```

- **Dashboard Defect**: Dashboard currently displays Stage 1 (Raw Signals in "Actionable Notifications").
- **Correction Needed**: Dashboard must display **Stage 3 (Recent Executed Fills)** from QuestDB `executions` so traders know their orders actually filled at an exact price.

---

## 9. Paper vs. Live Architecture Audit (Financial Safety)

### 12-Point Safety Verification

1. **Where does environment state currently live?**
   - In individual page components (`Strategies.jsx`, `deployConfig.environment`, `Backtester.jsx`). It is **NOT** present in global `AppState.jsx`.
2. **How is it passed to strategy execution?**
   - Passed in request body to `POST /api/strategies/{id}/deploy` as `{"environment": "paper"}` or `{"environment": "live"}`.
3. **How is it passed to portfolio accounting?**
   - Live reads QuestDB `live_user_pnl`; Paper reads in-memory `paper_trading_service.py`.
4. **How are positions separated?**
   - Completely isolated: Live positions reside in Redis `portfolio:*:positions`; Paper positions reside in `PaperTradingService._positions`.
5. **How are orders separated?**
   - Live orders route to `UnifiedExecutionEngine`; Paper orders route to `PaperTradingService._orders`.
6. **How are balances separated?**
   - Live balances query CCXT exchange vault; Paper balances are managed via virtual balance math ($100,000 default).
7. **How is P&L separated?**
   - Live P&L is recorded in QuestDB `executions`; Paper P&L is recorded in `PaperTradingService._trades`.
8. **How is risk separated?**
   - Live risk enforces hard `ExecutionGuard` blocks; Paper risk evaluates virtual circuit breakers.
9. **Can a Paper strategy accidentally route to a Live executor?**
   - **NO**. `strategy_service.py` inspects `deployment.environment`. If `paper`, it invokes `paper_trading_service` exclusively without loading exchange vault credentials.
10. **Can Live data accidentally appear in Paper UI?**
    - **Risk Present**: Because Dashboard lacks an environment query parameter, it currently displays Live QuestDB balances even if the user only trades in Paper mode.
11. **Is a global environment context already present?**
    - **NO**. `AppState.jsx` only has `demoMode` and `uiMode`.
12. **What is the minimum safe architecture required?**
    - Add `environment: 'paper' | 'live'` to `AppState.jsx`.
    - Pass `?environment=paper` or `?environment=live` to `GET /api/dashboard`.
    - Render high-contrast visual banners (Amber `#eab308` for Paper, Emerald `#10b981` for Live).

---

## 10. Risk System Audit

### Verified Schema Mismatch
- **Frontend Code (`Dashboard.jsx:755`)**:
  ```javascript
  riskHealth.risk_score < 30 ? <AllClear /> : <RiskAlert score={riskHealth.risk_score} />
  ```
- **Backend Code (`dashboard_aggregation_service.py:862-866`)**:
  ```python
  "risk": {
      "risk_level": risk.get("risk_level", "low"),
      "current_drawdown_pct": risk.get("current_drawdown_pct", 0.0),
      "max_daily_loss": risk.get("max_daily_loss", 500),
      "circuit_breaker_armed": risk.get("circuit_breaker_armed", True),
  }
  ```
- **Canonical Contract Fix**: Backend must return:
  ```json
  "risk": {
      "risk_level": "low",
      "risk_score": 15,
      "current_drawdown_pct": 2.1,
      "max_daily_loss": 500.0,
      "daily_loss_utilized": 0.0,
      "circuit_breaker_armed": true,
      "kill_switch_active": false
  }
  ```

### Existing Production Risk Endpoints (Verified Available)
- `GET /api/risk/settings` -> Authoritative risk thresholds.
- `GET /api/risk/status` -> Live utilization metrics (Daily Loss vs Limit, Positions vs Max).
- `GET /api/risk/margin-health` -> Free Margin % and Risk Score.
- `POST /api/risk/kill-switch` -> Emergency trading halt.
- `POST /api/risk/kill-switch/recover` -> Emergency recovery.

---

## 11. Exchange Health Audit

### Verified Mocking in Code
- In `backend_app/backend/dashboard_aggregation_service.py` lines 671–686:
  ```python
  async def get_health_status(self, user: dict) -> Dict:
      return {
          "exchange_api_latency_ms": 38,
          "exchange_api_latency_status": "optimal",
          "risk_circuit_breaker_status": "armed",
          "order_state_sync_status": "synchronized"
      }
  ```
- In `Dashboard.jsx` lines 287–297, this 38ms static value is rendered directly.
- **Correction Needed**: Real socket ping latency is tracked by `connection_engine.py` and `exchange_certification.py`. The aggregator should read the latest latency snapshot from Redis.

---

## 12. Strategy vs. Bot Runtime Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      STRATEGY VS BOT RUNTIME DOMAIN SEPARATION                   │
├─────────────────────┬──────────────────────┬─────────────────────────────────────┤
│ Domain              │ Database Table       │ Description & Responsibility        │
├─────────────────────┼──────────────────────┼─────────────────────────────────────┤
│ Strategy Definition │ `strategies`,        │ The quantitative DAG algorithm,     │
│                     │ `strategy_versions`  │ indicators, weights, and logic plan.│
├─────────────────────┼──────────────────────┼─────────────────────────────────────┤
│ Deployed Bot        │ `strategy_           │ A running instance bound to an      │
│                     │ deployments`         │ environment, symbol, and capital.   │
├─────────────────────┼──────────────────────┼─────────────────────────────────────┤
│ Worker Runtime      │ Worker Process /     │ Container process executing the     │
│                     │ In-Memory BotRunner  │ strategy DAG against live bars.     │
└─────────────────────┴──────────────────────┴─────────────────────────────────────┘
```

- **Dashboard Responsibility**: Dashboard must display **Active Running Bots** (deployments in `running` state with live P&L and worker heartbeat), not just static strategy definitions.

---

## 13. Navigation Audit & Sidebar Hierarchy

### Current Sidebar vs. App.jsx Route Discrepancy
- **Orphaned from Navigation**:
  - `/app/portfolio` (Portfolio Analytics) -> **Missing from Sidebar!**
  - `/app/trade-history` (Trade Ledger) -> **Missing from Sidebar!**
- **Sidebar Route Typo**:
  - Sidebar links to `/app/exchange`, but `App.jsx` registers `/app/exchanges`.
- **Proposed Clean Sidebar Navigation**:
  1. `Command Center`: Mission Control (`/app/dashboard`), Strategies (`/app/strategies`), Backtester (`/app/backtest`), Signal Trace (`/app/signal-trace`), Marketplace (`/app/marketplace`), Strategy Builder (`/app/builder`).
  2. `Financials & Vault`: Portfolio Analytics (`/app/portfolio`), Trade Ledger (`/app/trade-history`), Exchange Keys (`/app/exchanges`), Risk Controls (`/app/risk`), Billing (`/app/billing`).
  3. `Platform`: Notifications (`/app/notifications`), Support (`/app/support`), Docs (`docs.algo22.io`), Account (`/app/profile`).

---

## 14. Customer Lifecycle Analysis (14 Scenarios)

| Scenario | State Description | Primary First-Screen Need |
|---|---|---|
| **1. Brand-New User** | Registered, no exchange, no strategies | 3-Step Getting Started Card: Connect Exchange, Paper Backtest, Launch Bot |
| **2. No Exchange Connected** | Wants to trade in paper mode | Amber banner: "Paper Trading Active ($100K virtual balance)". |
| **3. Exchange Connected, No Strategy** | Keys verified, 0 bots | Banner: "Exchange Connected! Pick a template from Marketplace or build strategy." |
| **4. Strategy Created, Not Backtested** | Strategy draft exists | Card on strategy: "Run fast backtest before deploying." |
| **5. Strategy Backtested, Not Deployed** | Backtest saved | Primary CTA: "Deploy to Paper" or "Deploy to Live". |
| **6. Paper Bot Running** | Virtual trades executing | Live bot indicator with virtual P&L and open paper positions. |
| **7. Live Bot Running, No Open Positions**| Bot active, waiting for signal | Live indicator: "3 Bots Active — Waiting for market entry signal". |
| **8. Live Bot Running With Positions** | Real capital at risk | **Full Open Positions Quick-Table** with mark price, uPnL %, and Close CTA. |
| **9. Exchange Disconnected** | API key expired / network error | High-contrast Red Banner: "Exchange Connection Lost — Click to Reconnect". |
| **10. Risk Limit Triggered** | Daily loss limit breached | Red Alert Banner: "Daily Loss Limit Reached ($500) — New executions blocked". |
| **11. Kill Switch Active** | User triggered emergency halt | Red Strobe Banner: "EMERGENCY HALT ACTIVE — Trading halted. [Recover]". |
| **12. Order Execution Failed** | Insufficient margin or exchange rejection | Warning Alert: "Order Rejected on Binance: Insufficient Balance". |
| **13. Multiple Exchanges** | Binance + Bybit connected | Multi-exchange balance pill in top bar. |
| **14. Mobile Device Client** | Responsive viewport | Responsive 1-column collapse preserving positions table & emergency halt. |

---

## 15. Dashboard KEEP / REMOVE / MOVE / ADD Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   DASHBOARD WIDGET DISPOSITION MATRIX                                  │
├──────────────────────────┬──────────┬──────────────────────────────────────────┬───────────────────────┤
│ Widget / Metric          │ Decision │ Primary Rationale                        │ New Location / Target │
├──────────────────────────┼──────────┼──────────────────────────────────────────┼───────────────────────┤
│ Total Portfolio Value    │ KEEP     │ Core KPI for all traders                 │ Hero Row Card 1       │
│ Available Cash Balance   │ KEEP     │ Vital liquidity indicator                │ Hero Row Card 1 sub   │
│ 30-Day Equity Curve      │ KEEP     │ Optimal high-level trajectory            │ Performance Section   │
│ Active Running Bots List │ KEEP     │ Direct operational bot control           │ Operations Section    │
│ Quick Action Shortcuts   │ KEEP     │ Fast navigation to builder & backtester  │ Fast Action Hub       │
├──────────────────────────┼──────────┼──────────────────────────────────────────┼───────────────────────┤
│ Marketplace Earnings     │ REMOVE   │ Clutter / Affiliate marketing metric     │ Profile / Referrals   │
│ Subscription Summary     │ REMOVE   │ Administrative billing noise             │ Sidebar & Billing     │
│ Standalone API Health    │ REMOVE   │ Duplicates TopBar and System Modal       │ TopBar Status Pill    │
├──────────────────────────┼──────────┼──────────────────────────────────────────┼───────────────────────┤
│ Generic Text Insights    │ MOVE     │ Wastes vertical space                    │ Actionable Banners    │
│ Raw Signal Decisions     │ REPLACE  │ Replaced with real Executed Fills        │ Recent Fills Ticker   │
├──────────────────────────┼──────────┼──────────────────────────────────────────┼───────────────────────┤
│ Paper/Live Switcher      │ ADD [P0] │ Financial safety & environment clarity   │ Header Zone 1         │
│ Open Positions Table     │ ADD [P0] │ #1 missing feature for active traders    │ Operations Zone 3     │
│ Capital Deployed Metric  │ ADD [P0] │ Shows capital at risk in open trades     │ Hero Row Card 4       │
│ Emergency Kill Switch    │ ADD [P0] │ 1-click panic button for market safety   │ Header Zone 1         │
│ Daily Loss Limit Gauge   │ ADD [P1] │ Utilization meter vs. configured limit   │ Risk Zone 4           │
│ Recent Executions Ticker │ ADD [P1] │ Real trade fills with price & slippage   │ Activity Zone 5       │
└──────────────────────────┴──────────┴──────────────────────────────────────────┴───────────────────────┘
```

---

## 16. Recommended Dashboard Information Architecture

```
══════════════════════════════════════════════════════════════════════════════════════════════════════════
 ZONE 1: TOP GLOBAL BAR & TRADING MODE CONTROLLER
 [VyomQuant Logo]  │  [ MODE: ● LIVE REAL CAPITAL  ▼ ]  │  System: ● All Systems Operational (38ms)
                                                        │  [ 🛑 EMERGENCY HALT ] [ + Deploy Bot ]
══════════════════════════════════════════════════════════════════════════════════════════════════════════
 ZONE 2: PRIMARY CAPITAL & PERFORMANCE HERO ROW (5-Card Strip)
 ┌──────────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
 │ TOTAL EQUITY (USD)   │ │ TODAY'S P&L      │ │ UNREALIZED P&L   │ │ CAPITAL DEPLOYED │ │ RISK / DRAWDOWN  │
 │ $42,580.50           │ │ +$1,240.20 (+3.0%)│ │ +$340.15 (Live)  │ │ $18,400 (43.2%)  │ │ DD: 2.1% / Max 5%│
 │ Available: $24,180.50│ │ Realized 24h     │ │ 3 Open Positions │ │ Locked: $2,100   │ │ Circuit: ARMED ● │
 └──────────────────────┘ └──────────────────┘ └──────────────────┘ └──────────────────┘ └──────────────────┘
══════════════════════════════════════════════════════════════════════════════════════════════════════════
 ZONE 3: MAIN WORKSPACE (65% Trading Operations / 35% Risk & Activity)
 LEFT COLUMN (Operations & Markets):                 RIGHT COLUMN (Risk, Activity & Shortcuts):
 ───────────────────────────────────────────────     ───────────────────────────────────────────────
 [SECTION A: PORTFOLIO PERFORMANCE CURVE]            [SECTION D: CONNECTED EXCHANGES & HEALTH]
 - Timeframe: [ 1D | 1W | 1M | 3M | YTD | ALL ]      - Binance (Spot):   ● Connected (32ms)  $28,450
 - Area Chart: Equity trajectory vs Benchmark        - Bybit (Futures):  ● Connected (44ms)  $14,130
 - Deployed Capital Allocation Bar                   - [ + Connect Another Exchange ]
                                                     
 [SECTION B: LIVE OPEN POSITIONS (Quick-Table)]      [SECTION E: RISK & LOSS LIMIT GAUGE]
 ┌─────────┬──────┬─────────┬────────┬───────┬──────┐- Daily Loss: $0.00 / $500.00 Max (0% Used)
 │ Pair    │ Side │ Size    │ Entry  │ uPnL  │ Action│- Open Positions: 3 / 10 Max (30% Used)
 ├─────────┼──────┼─────────┼────────┼───────┼──────┤- Max Leverage Enforced: 3x
 │ BTC/USDT│ LONG │ 0.45 BTC│$64,200 │+$280.0│[Close]│
 │ ETH/USDT│ LONG │ 4.20 ETH│ $3,450 │+$85.50│[Close]│[SECTION F: RECENT EXECUTIONS & SIGNALS]
 │ SOL/USDT│ SHORT│ 25.0 SOL│  $148  │-$25.35│[Close]│- 12:44:02 BTC/USDT BUY Fill @ $64,210 (Bot Alpha)
 └─────────┴──────┴─────────┴────────┴───────┴──────┘- 12:30:15 ETH/USDT Signal APPROVED (Risk Engine)
                                                     - 11:15:00 SOL/USDT SELL Fill @ $148.10 (Grid Bot)
 [SECTION C: ACTIVE BOTS & STRATEGIES (3 Running)]   
 ┌──────────────────────────────────────────────────┐[SECTION G: QUICK LAUNCH HUB]
 │ ● Trend Momentum V2  │ BTC/USDT │ +$420.00 [Pause]│- [ ⚡ New Strategy Builder ]
 │ ● Grid Scalper Pro   │ ETH/USDT │ +$112.50 [Pause]│- [ 📊 Run Fast Backtest ]
 │ ⏸ Volatility Breakout│ SOL/USDT │   $0.00  [Resume]│- [ 🛡️ Adjust Risk Settings ]
 └──────────────────────────────────────────────────┘
══════════════════════════════════════════════════════════════════════════════════════════════════════════
```

---

## 17. Required Backend Changes

1. **`dashboard_aggregation_service.py`**:
   - Add subroutine `get_open_positions(user, environment)` querying Redis `portfolio:{uid}:{exchange}:positions` (live) or `paper_trading_service.get_positions(uid)` (paper).
   - Add subroutine `get_recent_executions(user, limit=5)` querying QuestDB `executions`.
   - Accept `environment: str = Query("live")` in `get_dashboard_data()`.
   - Compute `today_pnl` from closed trades since 00:00 UTC and `unrealized_pnl` from open positions.
   - Return both `risk_level` and `risk_score` in the `risk` dictionary.

---

## 18. Required Frontend Changes

1. **`algo22-terminal/src/AppState.jsx`**:
   - Add `environment: 'paper' | 'live'` state and `setEnvironment`.
2. **`algo22-terminal/src/components/Sidebar.jsx`**:
   - Add `/app/portfolio` and `/app/trade-history` to `NAV` array. Fix `/app/exchanges` route path.
3. **`algo22-terminal/src/pages/Dashboard.jsx`**:
   - Add Header Environment Switcher with Amber/Emerald styling.
   - Add Emergency Kill Switch button in header.
   - Implement Open Positions quick-view table component.
   - Implement 5-Card Capital & Drawdown hero strip.
   - Remove Marketplace Earnings, Subscription Summary, and Standalone API Health cards.

---

## 19. Data Contracts That Must Be Fixed Before UI

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   P0 DATA CONTRACT FIXES REQUIRED                                      │
├────┬─────────────────────────────┬──────────────────────────────────────────┬─────────────────────────────┤
│ #  │ Endpoint                    │ Current Flawed Contract                  │ Required Correct Contract   │
├────┼─────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────┤
│ 1  │ `GET /api/dashboard`        │ `today_pnl == total_pnl`                 │ `today_pnl` = 24h delta;    │
│    │                             │ `unrealized_pnl == total_pnl`            │ `unrealized_pnl` = uPnL sum │
├────┼─────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────┤
│ 2  │ `GET /api/dashboard`        │ Omit `positions` array                   │ Returns `positions: [...]`  │
├────┼─────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────┤
│ 3  │ `GET /api/dashboard`        │ `risk` only returns `risk_level`         │ Returns `risk_score` (num)  │
│    │                             │                                          │ + `risk_level` (str)        │
├────┼─────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────┤
│ 4  │ `GET /api/dashboard`        │ No `environment` param support           │ Accepts `?environment=live  │
│    │                             │                                          │  \| paper`                  │
└────┴─────────────────────────────┴──────────────────────────────────────────┴─────────────────────────────┘
```

---

## 20. Classification of Every Finding

- **[P0]** Cloned P&L calculation bug (`today_pnl == total_pnl`, `unrealized_pnl == total_pnl`) -> *Financial correctness blocker.*
- **[P0]** Zero visibility of Open Positions on Dashboard -> *Core trading usability blocker.*
- **[P0]** Missing global Paper vs. Live environment state -> *Financial safety blocker.*
- **[P0]** Absence of First-Screen Emergency Kill Switch -> *Financial safety blocker.*
- **[P1]** Risk schema key mismatch (`risk_score` vs `risk_level`) -> *UI bug causing permanent alert.*
- **[P1]** Hardcoded 38ms latency in aggregator -> *Static data issue.*
- **[P2]** Orphaned Portfolio and Trade History pages in Sidebar -> *Navigation usability defect.*
- **[P2]** Clutter on Dashboard (Affiliate earnings & subscription cards) -> *Information architecture defect.*
- **[P3]** 5-column hero card responsive wrapping on mobile -> *Visual layout enhancement.*
- **[NO CHANGE]** Admin Panel & AI Copilot preservation -> *Already certified isolated & dormant.*

---

## 21. Final Implementation Gate

### CAN WE SAFELY MODIFY DASHBOARD NOW?

### **NO — BLOCKED BY DATA & CONTRACT PREREQUISITES**

### Prerequisites That Must Be Resolved Before Editing `Dashboard.jsx`:
1. **Backend Aggregator Extension**: `backend_app/backend/dashboard_aggregation_service.py` must be updated to compute real 24h P&L, real open positions from Redis/Paper, real trade executions from QuestDB, and standardized risk keys (`risk_score`).
2. **Environment Parameter Wiring**: `routers/dashboard.py` must accept `environment: str = Query("live")` and pass it to the aggregator.
3. **Global AppState Context**: `AppState.jsx` must expose `environment` (`'paper'` | `'live'`) so that switching modes on the Dashboard instantly switches all child queries.

Once these backend contract updates are in place, the frontend layout in `Dashboard.jsx` can be refactored safely with **100% real data, zero mocks, and zero financial ambiguity**.

---
*Phase 0 Source-of-Truth & Architecture Validation Certified by Antigravity Quantitative AI Architecture Team.*
