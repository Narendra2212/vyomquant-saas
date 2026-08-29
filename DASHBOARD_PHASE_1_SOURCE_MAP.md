# DASHBOARD PHASE 1 — FORENSIC SOURCE MAP
**VyomQuant SaaS Terminal**
**Document Version**: 1.0.0  
**Status**: COMPLETE (Discovery & Source Mapping — Zero Code Modifications Executed)

---

## 1. Authoritative Source Map

The table below maps every financial, operational, and risk metric required on the customer dashboard to its underlying database engine, cache layer, service authority, freshness characteristics, and calculation method:

| DATA FIELD | SOURCE | AUTHORITY | ENVIRONMENT | FRESHNESS | CALCULATION | KNOWN LIMITATIONS |
|---|---|---|---|---|---|---|
| **Total Equity** | QuestDB `live_user_pnl` (Live) / `PaperTradingService._accounts` (Paper) | Server-Authoritative (`TelemetryEngine` / `PaperTradingService`) | Isolated (`live` vs `paper`) | 1-5s cycle (Live) / Instant in-memory (Paper) | `Cash Balance + Total Unrealized P&L across all open positions` | If QuestDB sidecar is down, falls back to Redis cache or `$0.00`. |
| **Available Balance** | CCXT `fetch_balance` -> Redis `portfolio:*:balance` (Live) / `PaperTradingService._accounts` (Paper) | Server-Authoritative (`KeyVault` + CCXT / `PaperTradingService`) | Isolated (`live` vs `paper`) | 5s background sync (Live) / Instant (Paper) | `Total Equity - Margin Deployed in Positions - Capital Locked in Orders` | Previously omitted from QuestDB `live_user_pnl` view. Must read Redis balance key or Paper account. |
| **Free Balance** | CCXT `free` USDT / USD wallet balance | Exchange Authoritative (`portfolio_cache_updater.py`) | Isolated | 5s sync | `balance["free"]["USDT"]` | Depends on connected exchange API rate limits. |
| **Used Balance / Margin** | CCXT `used` / `locked` balance | Exchange Authoritative (`portfolio_cache_updater.py` / `PaperTradingService`) | Isolated | 5s sync | `total_balance - free_balance` | For spot accounts, used balance represents capital locked in open limit orders. |
| **Today's Realized P&L** | QuestDB `executions` (Live) / `PaperTradingService._trades` (Paper) | Server-Authoritative (`executions` table / `_trades` list) | Isolated | Instant on trade fill | $\sum_{\text{trades today}} (\text{Realized P&L} - \text{Fees})$ since 00:00:00 UTC | **Critical Fix**: Previously cloned lifetime `total_pnl`. Must be filtered by timestamp $\ge$ 00:00 UTC. |
| **Unrealized P&L** | Redis `portfolio:*:positions` (Live) / `PaperTradingService._positions` (Paper) | Server-Authoritative (`portfolio_cache_updater.py` / `PaperTradingService`) | Isolated | Mark price tick stream | $\sum_{\text{open positions}} (\text{Mark Price} - \text{Entry Price}) \times \text{Quantity}$ | **Critical Fix**: Previously cloned lifetime `total_pnl`. Must be calculated from open positions. |
| **Cumulative Total P&L** | QuestDB `live_user_pnl.total_pnl` (Live) / `PaperTradingService.total_pnl` (Paper) | Server-Authoritative | Isolated | 15-min rollup (Live) / In-memory (Paper) | $\text{Current Equity} - \text{Starting Capital}$ | Lifetime all-time profit/loss across account history. |
| **Open Positions** | Redis `portfolio:{uid}:{exchange}:positions` (Live) / `PaperTradingService._positions` (Paper) | Server-Authoritative (`portfolio_cache_updater.py` / `PaperTradingService`) | Isolated | 5s sync (Live) / Instant (Paper) | Normalized array of active positions (`contracts`, `notional`, `entry_price`, `mark_price`, `uPnL`, `leverage`) | Spot accounts return empty array `[]` without error. |
| **Recent Executions / Fills** | QuestDB `executions` (Live) / `PaperTradingService._trades` (Paper) | Immutable Ledger (`telemetry.execute_query`) | Isolated | Instant on fill | Top 5 recent filled trades ordered by timestamp descending | Must not confuse raw strategy signals with actual executed fills. |
| **Active Running Bots** | Supabase `strategies` & `strategy_deployments` | PostgreSQL Database | Shared / Filterable | Instant query | Bots in `status == 'active'` / `is_active == true` | Strategy definition must be distinguished from deployed running worker. |
| **Risk Status & Utilization** | Supabase `risk_settings` + In-Memory `get_risk_status` | Server-Authoritative (`routers/risk.py`) | Shared | Real-time | Daily Loss utilized vs `max_daily_loss`, Open positions vs `max_positions` | Schema mismatch fixed: Returns both `risk_score` (0-100) and `risk_level` (SAFE/WARNING/CRITICAL/BLOCKED). |
| **Drawdown %** | QuestDB `account_health` (Live) / `PaperTradingService` (Paper) | Server-Authoritative | Isolated | 15-min bar rollup | $(\text{Peak Equity} - \text{Current Equity}) / \text{Peak Equity} \times 100$ | If no historical peak exists, drawdown is 0.0%. |
| **Exchange Health & Latency** | CCXT heartbeat / Redis `exchange_health:*` | Connection Engine (`connection_engine.py`) | Live only | On connection event | Socket round-trip ping time in milliseconds | **Critical Fix**: Eliminates static 38ms value. If unavailable, returns `null` ("Latency unavailable"). |

---

## 2. Environment Isolation Verification

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ENVIRONMENT ISOLATION BOUNDARY                     │
├──────────────────────────────────────┬──────────────────────────────────────┤
│ LIVE TRADING ENVIRONMENT             │ PAPER SIMULATION ENVIRONMENT         │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ • `GET /api/dashboard?environment=live`│ • `GET /api/dashboard?environment=paper`│
│ • Reads QuestDB `live_user_pnl`      │ • Reads `PaperTradingService` account│
│ • Reads Redis `portfolio:*:positions`│ • Reads `PaperTradingService` posit. │
│ • Reads QuestDB `executions`         │ • Reads `PaperTradingService` trades │
│ • NEVER reads Paper dictionaries     │ • NEVER loads exchange API keys      │
│ • ZERO virtual simulation data       │ • ZERO CCXT live network requests    │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

*This source map represents the authoritative blueprint for Phase 1 backend implementation.*
