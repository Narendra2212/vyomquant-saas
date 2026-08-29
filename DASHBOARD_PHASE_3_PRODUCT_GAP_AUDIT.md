# DASHBOARD PHASE 3 — FORENSIC PRODUCT GAP AUDIT
**VyomQuant SaaS Terminal**  
**Phase**: Phase 3 — Trading Platform Operational Completion (Phase 3A Gap Audit)  
**Target Pages & Modules**:  
- `Dashboard.jsx` (Mission Control / Cockpit)
- `Portfolio.jsx` (Institutional Asset Distribution & Analytics)
- `TradeHistory.jsx` (Execution Ledger & Audited Fills)
- `Strategies.jsx` (Strategy Deployment & Lifecycle Hub)
- `RiskSettings.jsx` (Risk Architecture & Guard Parameters)
- `ExchangeManager.jsx` (Exchange API Vault & Connectivity)
- `SignalTrace.jsx` (Execution Decision Forensics)
- `NotificationCenter.jsx` (Operational Alert Center)
- `Sidebar.jsx` (Global Navigation)  
**Timestamp**: 2026-08-26T16:32:00Z  
**Status**: **`AUDIT COMPLETE`**

---

## 1. Executive Summary

With Phase 1 through 2E complete, the Dashboard functions as a hardened, high-density Mission Control Cockpit. However, an algorithmic trader does not operate solely on the Dashboard: the Dashboard delegates deep investigative and configuration tasks to dedicated pages.

This forensic gap audit analyzed the trader journey between the Dashboard and its surrounding pages:
1. **Dashboard $\rightarrow$ Portfolio**: Traders clicking "View all in Portfolio" expect deep position ledger and exposure analysis; currently `Portfolio.jsx` lacks a dedicated Open Positions table and lacks an explicit Live/Paper environment toggle.
2. **Dashboard $\rightarrow$ Strategies**: When a strategy fails on the Dashboard (e.g. rate limit error), the trader clicks `Inspect` $\rightarrow$ `/app/strategies`. `Strategies.jsx` should accept context (`?strategy_id=...` or `?environment=...`) to highlight and focus the relevant bot.
3. **Dashboard $\rightarrow$ Trade History**: Trade History lacks an authoritative `Venue` (`exchange_id`) column in its table and lacks an environment filter (`live` vs `paper`).
4. **Dashboard $\rightarrow$ Risk Settings**: `RiskSettings.jsx` manages configuration parameters (Loss limit, Max positions, Leverage), but lacks real-time WebSocket sync with `risk.kill_switch_activated` and `risk.kill_switch_recovered` events.
5. **Dashboard $\rightarrow$ Signal Trace**: `SignalTrace.jsx` successfully accepts `?strategy_id=` query params and provides full signal-to-order decision logs.

---

## 2. Page-by-Page Forensic Gap Analysis

### 2.1 Portfolio (`Portfolio.jsx`)
- **Current State**: Renders Total Value, Unrealized P&L, Realized P&L, ROI %, 90-day Equity Curve, Asset Allocation donut, and Daily P&L calendar heatmap.
- **Identified Gaps**:
  - **Gap P1.1 (Environment Ambiguity)**: `Portfolio.jsx` attempts `api.portfolio.getSummary().catch(() => api.paper.getSummary())` without an explicit Live vs. Paper environment toggle or indicator.
  - **Gap P1.2 (Missing Open Positions Table)**: When a trader clicks "View all in Portfolio" from the Dashboard, `Portfolio.jsx` provides macro analytics but no detailed table of open positions with mark price, entry price, liquidation distance, and margin modes.
- **Classification**: **`P1 (High Operational Value)`**

### 2.2 Strategies Hub (`Strategies.jsx`)
- **Current State**: Lists automated strategies with status badges, PnL, Win Rate, Drawdown, timeframe, and controls (Deploy, Pause, Run, Stop, Edit).
- **Identified Gaps**:
  - **Gap P1.3 (URL Context Focus)**: When navigated from Dashboard with `?strategy_id=strat_123` or `?environment=live`, `Strategies.jsx` does not auto-filter or highlight the target strategy.
  - **Gap P2.1 (Error Detail Telemetry)**: If a strategy status is `error` or `failed`, `Strategies.jsx` displays a status dot but does not render the exact error string in the strategy row.
- **Classification**: **`P1 (High Operational Value)`**

### 2.3 Trade History Ledger (`TradeHistory.jsx`)
- **Current State**: Displays summary metrics (Total Trades, Profitable, Losing, Win Rate, Total PnL), side filters (ALL, BUY, SELL, PROFIT), CSV export, and execution table.
- **Identified Gaps**:
  - **Gap P1.4 (Missing Venue Identifier)**: The table columns are `#`, `Time`, `Pair`, `Side`, `Entry`, `Exit`, `Size`, `P&L`, `Fees`, `Slippage`, `Strategy`. The authoritative `Venue` (`exchange_id`) column is missing, preventing multi-exchange traders from identifying which exchange executed the fill.
  - **Gap P2.2 (Environment Filtering)**: Lacks a `LIVE` vs `PAPER` filter toggle.
- **Classification**: **`P1 (High Operational Value)`**

### 2.4 Risk Settings (`RiskSettings.jsx`)
- **Current State**: Slider configuration for Daily Loss Limit, Max Positions, Max Leverage; toggles for Kill Switches; strategy-level capital allocations.
- **Identified Gaps**:
  - **Gap P2.3 (Real-Time Kill Switch Sync)**: Does not listen to WebSocket `risk.kill_switch_activated` / `risk.kill_switch_recovered` events, requiring manual page refresh if triggered from Dashboard.
- **Classification**: **`P2 (Medium Value)`**

### 2.5 Exchange Manager (`ExchangeManager.jsx`)
- **Current State**: Secure key entry, AES encrypted backend storage, connection testing, latency display, and exchange deletion.
- **Findings**: Fully compliant with security invariants. Key secrets are never displayed in clear text. Latency displays `"Latency unavailable"` when ping is unmeasured.
- **Classification**: **`PASS`**

### 2.6 Signal Trace (`SignalTrace.jsx`)
- **Current State**: Full signal lifecycle tracing with decision visualization, timeline audit, ML type analysis, and URL search param handling (`?strategy_id=...`).
- **Classification**: **`PASS`**

### 2.7 Notification Center (`NotificationCenter.jsx`) & Sidebar (`Sidebar.jsx`)
- **Current State**: Categorized notifications with action linking (`actionPath`) and real-time unread count updates via WebSocket.
- **Classification**: **`PASS`**

---

## 3. Findings Classification Summary

| ID | Finding Description | Severity | Affected File | Recommended Scope |
|---|---|---|---|---|
| **GAP-01** | `Portfolio.jsx` lacks Live/Paper environment toggle and fallback mixes paper with live. | **P1** | `Portfolio.jsx` | Add explicit Live/Paper selector matching Dashboard. |
| **GAP-02** | `Portfolio.jsx` lacks an Open Positions detail table when linked from Dashboard. | **P1** | `Portfolio.jsx` | Render a responsive positions table with mark price & unrealized PnL. |
| **GAP-03** | `TradeHistory.jsx` table omits `Venue` (`exchange_id`) column. | **P1** | `TradeHistory.jsx` | Add Venue badge column to table and CSV export. |
| **GAP-04** | `Strategies.jsx` does not auto-filter or highlight when receiving `?strategy_id=` or `?environment=`. | **P1** | `Strategies.jsx` | Support URL search params for contextual strategy focus. |
| **GAP-05** | `Strategies.jsx` does not render exact error message for failed strategies. | **P2** | `Strategies.jsx` | Display error reason and CTA to Signal Trace. |
| **GAP-06** | `RiskSettings.jsx` does not subscribe to WebSocket kill-switch events. | **P2** | `RiskSettings.jsx` | Subscribe to risk events for live toggle sync. |
| **GAP-07** | `TradeHistory.jsx` lacks Live vs Paper filter toggle. | **P2** | `TradeHistory.jsx` | Add environment filter button group. |

---

## 4. Proposed Minimal Safe Implementation Scope (Phase 3B)

To maintain architectural stability and avoid regressions, implement only the following high-value P1 items:

1. **Portfolio Page Alignment (`Portfolio.jsx`)**:
   - Add Live / Paper environment toggle in Header.
   - Separate Live summary query from Paper summary query (remove ambiguous `.catch(() => api.paper.getSummary())`).
   - Add Open Positions table section with venue, side, entry, mark, and unrealized P&L.
2. **Trade History Ledger Enhancement (`TradeHistory.jsx`)**:
   - Add `Venue` (`exchange_id`) column to the ledger table and CSV export.
   - Add Live / Paper environment toggle.
3. **Strategies Context Linking (`Strategies.jsx`)**:
   - Parse `?strategy_id=` and `?environment=` from search params to automatically focus the selected strategy.
   - Display error reason badge for `failed`/`error` strategies.
4. **Risk Settings Real-time Sync (`RiskSettings.jsx`)**:
   - Add WebSocket subscription to `risk.kill_switch_activated` and `risk.kill_switch_recovered` to ensure toggles stay synchronized across browser tabs.

---

## 5. Architectural Invariants Preserved

- Zero changes to `AdminDashboard.jsx`, `backend_app/routers/admin.py`, or Copilot files (**0 diff**).
- Zero changes to `UnifiedExecutionEngine`, `ExecutionGuard`, or CCXT normalization.
- `DIRECT_EXECUTION_BLOCKED` remains strictly enforced on the backend.
- `GET /api/dashboard` remains the authoritative financial truth for the Dashboard.
