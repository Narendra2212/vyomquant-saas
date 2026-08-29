# DASHBOARD PHASE 2B — FORENSIC UX & PRODUCT AUDIT
**VyomQuant SaaS Terminal**  
**Document Type**: Forensic Product & UX Audit (Retail Crypto Quant Trader Perspective)  
**Phase**: Phase 2B  
**Target File**: `algo22-terminal/src/pages/Dashboard.jsx`  
**Cross-Page Scope**: Portfolio, Trade History, Strategies, Strategy Detail, Risk Settings, Exchange Manager, Signal Trace, Notifications, Billing, Profile, Marketplace, Support  
**Timestamp**: 2026-08-26T15:25:00Z  

---

## 1. Executive Summary & Trader Persona Framing

### The Target Trader Persona:
The primary customer of VyomQuant is a **retail crypto algorithmic trader** running automated strategies across spot and perpetual futures markets on tier-1 venues (**Binance, Bybit, Kraken, OKX, Coinbase**). 

This trader is **not** looking for a generic SaaS analytics dashboard with marketing vanity cards. They require a **real-time mission control / trading cockpit** that instantly answers eight non-negotiable operational questions:
1. **Capital Safety**: *Is my capital safe right now? What is my current drawdown and risk utilization?*
2. **Daily Performance**: *Am I net positive or negative today? How much of that is locked in (Realized) vs fluctuating (Unrealized)?*
3. **Open Exposure**: *What positions are currently open, on which venues, at what leverage, and how close am I to liquidation?*
4. **Bot Health**: *Are my automated bots actively running, paused, or silently failing?*
5. **Venue Connectivity**: *Are my exchange API keys connected, authenticated, and reporting low latency?*
6. **Execution Integrity**: *Did any order get rejected, slippage-spiked, or rate-limited in the last 15 minutes?*
7. **Circuit Breakers**: *Is any automated kill switch or safety gate currently triggered?*
8. **Environment Isolation**: *Am I looking at real money (LIVE) or simulation (PAPER)?*

---

## 2. Forensic Evaluation of the Newly Implemented Dashboard

The Phase 2A implementation successfully transitioned `Dashboard.jsx` from a marketing/referral portal into a true quantitative trading cockpit. Below is the forensic evaluation of each zone:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ZONE 1: Global Status Bar (LIVE/PAPER Switch • System Diagnostics • Refresh Sync)       │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ ZONE 2: Hero Capital Grid (Equity • Available Cash • Today's P&L • Exposure • Risk)   │
├──────────────────────────────────────────────────┬─────────────────────────────────────┤
│ ZONE 3: Open Positions Live Table                │ ZONE 4: Active Strategy Bot Fleet   │
│ (Asset, Venue, Side, Size, Entry, Mark, uPnL)    │ (Status, Pair, Today PnL, Controls) │
├──────────────────────────────────────────────────┼─────────────────────────────────────┤
│ ZONE 8: Historical Equity Trajectory (AreaChart) │ ZONE 5: Risk & Safety Guard Matrix  │
│ (1D, 1W, 1M, 3M, ALL Timeframe Selectors)        │ (Loss Limit, Capacity, Drawdown)    │
├──────────────────────────────────────────────────┼─────────────────────────────────────┤
│ ZONE 7: Recent Executions / Audit Fills          │ ZONE 6: Exchange Venues & Latency   │
│ (Timestamp, Pair, Venue, Side, Price, Realized)  │ (Connection State, Measured Ping)   │
└──────────────────────────────────────────────────┴─────────────────────────────────────┘
```

### Strengths of Phase 2A:
1. **Unmistakable Environment Isolation**: Distinct visual separation between Emerald Green (`LIVE TRADING` / `REAL CAPITAL ACTIVE`) and Electric Indigo (`PAPER SIMULATION`).
2. **Decomposed Financial Reality**: Total Equity is completely decoupled from Lifetime Cumulative P&L; Today's P&L explicitly separates Realized gains from Mark-to-Market Unrealized fluctuations.
3. **Authoritative Multi-Venue Tagging**: Positions and executions preserve and display their exact `exchange_id` (`binance`, `bybit`, `kraken`).
4. **Zero Fabrication**: Measured latency is displayed when real; falls back to `"Latency unavailable"` (0ms and 38ms fake values eliminated). Spot liquidation price displays `—` rather than `$0.00`.
5. **Zero Promotional Clutter**: Deferred Copilot, affiliate links, and marketplace upsells completely removed from first screen.

---

## 3. Product & Information Architecture Mapping Across SaaS Pages

To eliminate duplication and establish strict page ownership, each page's distinct responsibility is defined below:

| Page | Route | Owned Domain / Unique Responsibility | What Belongs on Dashboard | What Must NOT Be on Dashboard |
|---|---|---|---|---|
| **Dashboard** | `/app/dashboard` | **Mission Control / First-Screen Cockpit**: Real-time snapshot of capital, open positions, bot status, risk guard, and recent fills. | High-level summary of all operational domains. | Deep configuration forms, historical ledgers, DAG graph editors. |
| **Portfolio** | `/app/portfolio` | **Asset Allocation & Wealth Analytics**: Asset weight breakdown (Pie chart), multi-token wallet balances, 90-day P&L heatmap, cumulative ROI. | Total Equity, Available Liquidity, Open Positions list. | Asset allocation pie charts, monthly heatmap squares, token transfer forms. |
| **Trade History** | `/app/trades` | **Audited Institutional Ledger**: Full paginated trade log with slippage, exchange order IDs, fee breakdown, CSV exports, date range filters. | Last 5 recent fills. | Full 1,000-trade paginated table, CSV export modal, detailed fee breakdown. |
| **Strategies** | `/app/strategies` | **Bot Fleet Management & Deployment**: Strategy library, compiler deployment wizard, version rollback, worker region assignment. | Top 5 active strategy instances + quick Pause/Resume. | Compiler plan JSON view, deployment wizard form, version history. |
| **Strategy Detail** | `/app/strategies/:id` | **Single Bot Telemetry**: Bot-specific equity curve, runtime logs, execution signals, parameter overrides. | Nothing (summarized in bot fleet list). | In-depth signal logs, DAG node execution step metrics. |
| **Strategy Builder**| `/app/builder` | **Visual Quant IDE**: ReactFlow DAG editor, block palette, indicator parameters, syntax validation. | "Create Strategy" quick CTA button. | Node palettes, block parameters, compiler logs. |
| **Backtester** | `/app/backtest` | **Historical Simulation**: Backtest runner, Monte Carlo analysis, Sharpe/Sortino ratios, trade list. | Nothing. | Backtest parameter sliders, candle charts, Monte Carlo distributions. |
| **Exchange Manager**| `/app/exchange` | **API Key Vault & Connectivity**: API key/secret input, passphrase encryption, IP whitelist checker, CCXT capability audit. | Venue connectivity badges + live ping latency. | API key entry fields, passphrase forms, IP whitelist guide. |
| **Risk Settings** | `/app/risk` | **Safety Parameter Configuration**: Daily loss limit sliders, max leverage, black swan triggers, per-strategy capital caps. | Risk Score gauge, Loss limit utilization bar, Kill switch state. | Slider configuration controls, strategy capital limit editor. |
| **Signal Trace** | `/app/signal-trace` | **Forensic Pipeline Debugger**: Signal-to-execution DAG trace, reject reason auditor. | Actionable warning banner if signal rejected. | Full JSON request/response telemetry. |
| **Notifications** | `/app/notifications`| **Historical Alert Feed**: Categorized notification inbox (Risk, System, Trade, Billing). | High-priority operational alerts only. | Full unread notification inbox list. |
| **Billing** | `/app/billing` | **Subscription & Quotas**: Tier management, Stripe checkout, invoice downloads. | Subscription plan label in diagnostic popover. | Pricing cards, checkout buttons, invoice lists. |
| **Profile & 2FA** | `/app/profile`, `/app/2fa` | **Identity & Security**: MFA QR codes, session management, password resets. | Nothing. | Security settings, TOTP inputs. |
| **Marketplace** | `/app/marketplace` | **Community Strategy Discovery**: Published strategies, subscriber metrics. | Nothing. | Strategy browsing cards, monetization earnings. |

---

## 4. Detailed Forensic Gap & Trading Risk Analysis

### A. What is Missing for an Active Retail Crypto Quant Trader?
1. **Emergency Panic Action ("Halt All & Cancel Orders")**:
   - *Risk*: If market flash-crashes or an algorithmic loop malfunctions, the trader currently has to navigate to `/app/risk` to toggle the kill switch.
   - *Recommendation*: Add a guarded **Emergency Halt / Kill Switch** modal trigger directly in the Dashboard header.
2. **Order Rejection & Warning Visibility**:
   - *Risk*: If Binance returns `API-key format invalid` or Bybit returns `Insufficient margin`, this error is easily buried if the user only sees a table of successful fills.
   - *Recommendation*: Add a high-visibility **Critical Attention Banner** at the top of Zone 7 whenever an order is rejected or a strategy enters `ERROR` state.
3. **Perpetual Futures Position Details (Margin Mode & Liq Distance)**:
   - *Risk*: For Bybit/Binance Futures, traders care deeply about whether a position is **Cross** or **Isolated**, and how many percentage points the current mark price is away from the **Liquidation Price**.
   - *Recommendation*: Display margin mode tag (`CROSS` / `ISOL`) and calculate Liquidation Distance % (`(Mark - Liq) / Mark`).
4. **WebSocket Live Streaming Ticks**:
   - *Risk*: Current dashboard relies on mount fetch and manual sync. During high volatility, P&L can move 5% in 30 seconds.
   - *Recommendation*: Wire lightweight WebSocket ticker channels to stream live mark price and unrealized P&L updates without polling.

### B. What is Duplicated or Overlapping?
1. **Exchange Connection Display**: Both Zone 6 on Dashboard and `/app/exchange` show connection status. Dashboard correctly keeps it to a 2-line summary; `/app/exchange` owns the API credentials.
2. **Strategy Running State**: Dashboard provides quick Pause/Resume. Full lifecycle (edit, clone, backtest, deploy) is cleanly owned by `/app/strategies`.

### C. What Creates Visual Noise or Friction?
1. **Equity Curve Aspect Ratio**: In standard 1080p desktop viewports, an overly tall equity curve pushes open positions below the fold. The 180px height implemented in Phase 2A correctly preserves the vertical viewport for positions and risk metrics.
2. **Color Hierarchy**: Green/Red must strictly be reserved for financial P&L and operational safety (Armed vs Triggered). Venue badges and market types must use neutral dark slate/cyan tones to avoid color fatigue.

---

## 5. Prioritized Recommendation Report (P0 / P1 / P2 / P3)

### **P0: Critical Trading Safety & Data Integrity (Immediate Priority)**
- **P0.1 — Emergency Kill Switch Action on Dashboard**: Add a guarded 1-click "Emergency Halt" button on the Mission Control header with instant confirmation modal that calls `POST /api/risk/kill-switch/activate` to halt all bots and cancel open orders immediately.
- **P0.2 — High-Visibility Order Rejection & Bot Failure Alerts**: Surface a dedicated alert banner at the top of the cockpit when QuestDB/PostgreSQL logs an execution status of `FAILED`, `BLOCKED`, or a strategy state of `ERROR`.
- **P0.3 — Liquidation Distance & Margin Mode on Positions**: Enrich position table rows to show Margin Mode (`CROSS` / `ISOL`) and compute dynamic Liquidation Distance % (`Liq Dist: 14.2%`) for derivatives positions.

### **P1: Operational Excellence & Real-Time Performance**
- **P1.1 — WebSocket Real-Time P&L & Mark Price Streaming**: Connect `websocketClient.js` to subscribe to mark price delta events, updating open position valuations and today's unrealized P&L in real time without manual sync.
- **P1.2 — 1-Click Position Close CTA**: Add a concise "Market Close" confirmation action button directly on open position rows to allow instant manual exit during emergency volatility.
- **P1.3 — Strategy Error Telemetry & Quick Restart**: In the Strategy Bot fleet cards, if a strategy is in `ERROR` status, display the exact error reason (e.g. `Exchange Rate Limit Exceeded`, `Insufficient Funds`) with a "Restart" button.

### **P2: UX Polish, Layout Density & Micro-Interactions**
- **P2.1 — Compact / Dense Layout Toggle**: Allow advanced quantitative traders to toggle between "Standard" and "Dense" view (smaller font size, compact row height for 10+ open positions).
- **P2.2 — Exchange Weight & Rate Limit Meter**: Display current REST API rate limit utilization (e.g. `Binance IP Weight: 180/1200`) in the Exchange Health venue cards.
- **P2.3 — Quick Links in Navigation Sidebar**: Add direct Sidebar links for `/app/portfolio` and `/app/trades` to ensure seamless 1-click navigation from anywhere in the terminal.

### **P3: Advanced Telemetry & Future Optimizations**
- **P3.1 — Multi-Asset Exposure Sunburst / Mini-Bar**: Add a small horizontal 1-line asset allocation bar (e.g., `60% BTC | 25% ETH | 15% SOL`) inside the Market Exposure hero card.
- **P3.2 — Execution Slippage Tracking**: Calculate execution slippage (`Expected vs Filled Price`) and display it in recent execution tooltips.

---

## 6. Audit Conclusion

The modernized `Dashboard.jsx` provides a solid, financially accurate, environment-safe trading cockpit for retail crypto quants. By implementing the P0 and P1 safety and real-time enhancements in upcoming iterations, the VyomQuant SaaS Dashboard will reach institutional-grade execution parity with professional trading terminals.
