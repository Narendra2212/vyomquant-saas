# CCXT COMPATIBILITY & DATA NORMALIZATION SPECIFICATION
**VyomQuant SaaS Terminal**
**Document Version**: 1.0.0  
**Status**: COMPLETE (Architecture Validation & Interface Specification — Zero Code Modifications Executed)

---

## 1. Executive Summary & Architectural Invariant

This specification establishes the **authoritative CCXT compatibility contract** for VyomQuant SaaS. 

### Core Architectural Principle
**Dashboard is 100% Exchange-Agnostic.** The Dashboard, Portfolio, and Risk consoles must **never** call CCXT directly or parse raw exchange payloads. Instead, CCXT operates strictly within the connection and execution layers, translating raw exchange APIs into canonical, normalized internal contracts.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CCXT ARCHITECTURE PIPELINE                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   [ Binance / Bybit / Coinbase / Kraken / OKX ] (Venues)                    │
│                        │                                                    │
│                        ▼                                                    │
│   ConnectionEngine / CCXT.pro (Layer 2 Connection)                          │
│                        │                                                    │
│                        ▼                                                    │
│   ExchangeNormalizer & KeyVault (Layer 3 Normalization & Decryption)        │
│                        │                                                    │
│                        ▼                                                    │
│   UnifiedExecutionEngine / PortfolioCacheUpdater (Execution & Accounting)   │
│                        │                                                    │
│                        ▼                                                    │
│   Normalized Internal Contracts (Account, Position, Order, Fill, Health)    │
│                        │                                                    │
│                        ▼                                                    │
│   DashboardAggregationService (FastAPI Aggregator)                          │
│                        │                                                    │
│                        ▼                                                    │
│   Customer Dashboard (Dashboard.jsx)                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Existing CCXT Architecture Inventory

Every CCXT call site in the repository was inspected and verified:

| CCXT Method | Code Call Site Location | Execution Context & Role | Dashboard Direct Usage |
|---|---|---|---|
| `load_markets()` | `connection_engine.py:118`, `exchange_executor.py:428` | Connection startup with exponential backoff retry; market precision cache | **NO** (Internal) |
| `fetch_balance()` | `portfolio_cache_updater.py:152`, `connection_engine.py:14` | Background wallet balance fetch cached in Redis `portfolio:*:balance` | **NO** (Via Redis) |
| `fetch_positions()` | `portfolio_cache_updater.py:153`, `data_seeking_engine.py:310` | Background open position fetch cached in Redis `portfolio:*:positions` | **NO** (Via Redis) |
| `fetch_open_orders()` | `data_seeking_engine.py:250`, `reconciliation_worker.py:512` | Order reconciliation & limit order tracking | **NO** (Via Service) |
| `fetch_order()` | `data_seeking_engine.py:254`, `exchange_executor.py:650` | Single order fill status verification | **NO** (Via Service) |
| `fetch_my_trades()` | `data_seeking_engine.py:273`, `reconciliation_worker.py:530` | Historical fill synchronization into QuestDB `executions` | **NO** (Via QuestDB) |
| `fetch_ticker()` / `watch_ticker()` | `data_seeking_engine.py:124, 347` | Real-time mark price feeds for mark-to-market uPnL calculation | **NO** (Via Stream) |
| `fetch_trading_fees()` | `data_seeking_engine.py:285` | Taker/maker fee schedule lookup for P&L deduction | **NO** (Via Service) |
| `fetch_funding_rate()` | `data_seeking_engine.py:324` | Perpetual futures funding cost accounting | **NO** (Via Service) |
| `create_order()` | `exchange_executor.py:710` | Authenticated order submission guarded by `ExecutionGuard` | **NO** (Via Engine) |
| `cancel_order()` | `exchange_executor.py:750` | Order cancellation and emergency kill switch execution | **NO** (Via Engine) |
| `watch_trades()` / `watch_orders()` | `data_seeking_engine.py:55, 150` | WebSocket streaming for tick feeds and order fill state changes | **NO** (Via Socket) |

---

## 3. Normalized Internal Data Contracts

The Dashboard and Aggregation Service consume these exact normalized data structures:

### 1. Account Contract (`NormalizedAccount`)
```json
{
  "exchange_id": "binance",
  "environment": "live",
  "currency": "USDT",
  "total_balance": 42580.50,
  "free_balance": 24180.50,
  "used_balance": 18400.00,
  "available_balance": 24180.50,
  "updated_at": "2026-08-26T14:15:00Z"
}
```

### 2. Position Contract (`NormalizedPosition`)
```json
{
  "id": "pos_binance_btcusdt_long",
  "exchange_id": "binance",
  "symbol": "BTC/USDT",
  "market_type": "future",
  "side": "long",
  "contracts": 0.45,
  "quantity": 0.45,
  "entry_price": 64200.00,
  "mark_price": 64822.50,
  "notional": 29169.90,
  "leverage": 3,
  "unrealized_pnl": 280.12,
  "unrealized_pnl_pct": 0.97,
  "liquidation_price": 43200.00,
  "margin": 9723.30,
  "margin_type": "cross",
  "timestamp": "2026-08-26T14:10:00Z"
}
```

### 3. Order Contract (`NormalizedOrder`)
```json
{
  "exchange_id": "binance",
  "order_id": "ord_8923478912",
  "client_order_id": "vq_bot_alpha_981",
  "symbol": "BTC/USDT",
  "market_type": "future",
  "side": "buy",
  "type": "limit",
  "status": "open",
  "price": 63900.00,
  "amount": 0.20,
  "filled": 0.00,
  "remaining": 0.20,
  "average_price": null,
  "fee": 0.00,
  "timestamp": "2026-08-26T14:12:00Z"
}
```

### 4. Execution / Fill Contract (`NormalizedExecution`)
```json
{
  "exchange_id": "binance",
  "order_id": "ord_8923478912",
  "trade_id": "fill_98127391",
  "symbol": "BTC/USDT",
  "side": "buy",
  "price": 64210.00,
  "amount": 0.45,
  "cost": 28894.50,
  "fee": 1.15,
  "timestamp": "2026-08-26T12:44:02Z",
  "strategy_id": "strat_momentum_v2",
  "environment": "live"
}
```

### 5. Exchange Health Contract (`NormalizedExchangeHealth`)
```json
{
  "exchange_id": "binance",
  "display_name": "Binance",
  "connected": true,
  "authenticated": true,
  "health_status": "HEALTHY",
  "latency_ms": 32,
  "last_successful_sync": "2026-08-26T14:14:58Z",
  "websocket_status": "active",
  "rest_status": "active",
  "order_sync_status": "synchronized",
  "market_types": ["spot", "future", "swap"]
}
```

---

## 4. Officially Supported CCXT Exchanges Matrix

Every supported exchange in `backend_app/core/exchange_certification.py` was inspected for capability parity:

| Exchange | CCXT Class | Certification Level | Market Types | Position Support | Balance Support | Open Orders | Trade Fills | Passphrase Req.? | Known Normalization Issues & Handling |
|---|---|---|---|---|---|---|---|---|---|
| **Binance** | `ccxt.pro.binance` | **Level 5 (Live Ready)** | Spot, Margin, Futures, Swap | **YES** (`fetch_positions`) | **YES** (`fetch_balance`) | **YES** | **YES** | No | Symbol separator (converts `BTC/USDT` to `BTCUSDT`); dual spot/futures wallets. |
| **Bybit** | `ccxt.pro.bybit` | **Level 5 (Live Ready)** | Spot, Margin, Futures, Swap | **YES** (`fetch_positions`) | **YES** (`fetch_balance`) | **YES** | **YES** | No | Unified Trading Account (UTA) margin mode vs classic account. |
| **Coinbase** | `ccxt.pro.coinbase` | **Level 5 (Live Ready)** | Spot only | **NO** (Spot has no positions) | **YES** (`fetch_balance`) | **YES** | **YES** | No | Dash separator (`BTC-USD`); no perpetuals on standard retail adapter. |
| **Kraken** | `ccxt.pro.kraken` | **Level 5 (Live Ready)** | Spot, Futures | **YES** (`fetch_positions`) | **YES** (`fetch_balance`) | **YES** | **YES** | No | Legacy asset prefix (`XXBT`, `ZUSD`); handled by CCXT standardizer. |
| **OKX** | `ccxt.pro.okx` | **Level 5 (Live Ready)** | Spot, Margin, Futures, Swap | **YES** (`fetch_positions`) | **YES** (`fetch_balance`) | **YES** | **YES** | **YES (Passphrase)** | Requires API passphrase; contract size multiplier for derivatives. |
| **KuCoin** | `ccxt.pro.kucoin` | Level 4 (Simulated) | Spot, Futures | **YES** | **YES** | **YES** | **YES** | **YES (Passphrase)** | Level 4: Final live latency certification in progress. |
| **Gate.io** | `ccxt.pro.gateio` | Level 3 (Sandbox) | Spot, Futures | **YES** | **YES** | **YES** | **YES** | No | Level 3: Awaiting full order lifecycle certification. |
| **Bitfinex**| `ccxt.pro.bitfinex`| Level 2 (Read-Only) | Spot, Margin | Partial | **YES** | **YES** | **YES** | No | Read-only operations verified; live execution disabled. |

---

## 5. CCXT Normalization Edge Cases Handled

1. **Spot vs. Derivatives (Futures & Swaps)**:
   - On Spot exchanges (e.g. Coinbase), `fetch_positions()` is unsupported. The normalizer returns an empty position array `[]` without raising an error.
   - On Derivatives exchanges (Binance Futures, Bybit Linear), positions are parsed into normalized notional value and margin requirement.
2. **Contract Size & Notional Normalization**:
   - For inverse/contract markets (OKX, Bybit inverse), CCXT provides `contractSize`.
   - Normalizer calculates: $\text{Notional} = \text{contracts} \times \text{contractSize} \times \text{Mark Price}$.
3. **Long vs. Short Position Representation**:
   - Unified to `side: "long" | "short"`.
   - In hedge mode, both long and short positions on the same pair are retained as separate rows.
4. **Price & Amount Precision Quantization**:
   - Quantized using `Decimal` with `ROUND_DOWN` against market metadata precision to prevent exchange rejection due to decimal overflow.
5. **Fee & Commission Deduction**:
   - Taker/maker fees are fetched dynamically via `fetch_trading_fees()` to compute exact net realized P&L.

---

## 6. Paper Trading vs. Live CCXT Parity

To ensure the Dashboard code never branches based on backend engine internals:

```
[LIVE ENVIRONMENT]
CCXT fetch_positions() / Redis
               │
               ▼
[Normalizer: NormalizedPosition] ────▶ [GET /api/dashboard] ────▶ [Dashboard Table]
               ▲
               │
PaperTradingService.get_positions()
[PAPER ENVIRONMENT]
```

- **Live Mode**: Reads Redis `portfolio:{uid}:{exchange}:positions`, runs through `NormalizedPosition` serializer.
- **Paper Mode**: Reads `PaperTradingService._positions[uid]`, runs through the **exact same `NormalizedPosition` serializer**.
- **Dashboard Experience**: The UI renders the exact same table columns (`Pair`, `Side`, `Size`, `Entry Price`, `Mark Price`, `uPnL`, `Action`) regardless of whether the trader is in Paper or Live mode.

---

## 7. Multi-Exchange Aggregation

When a user has connected multiple exchanges (e.g., Binance + Bybit):
1. **Header Balance**: Aggregates `total_balance` across all connected venues.
2. **Positions Quick-Table**: Each position displays an exchange badge (e.g. `[Binance]` or `[Bybit]`).
3. **Emergency Kill Switch**: Dispatches cancellation signals across all connected exchange pools concurrently.

---

## 8. Real Exchange Health & Latency (Zero Mocking)

- **Eliminating 38ms Static Latency**:
  - Socket ping latency is measured during the connection heartbeat in `connection_engine.py`.
  - Stored in Redis under `exchange_health:{user_id}:{exchange_id}`.
  - If latency measurement is unavailable or stale (>60s), the API returns `"latency_ms": null` and the UI displays `"Latency unavailable"` (never a fabricated number).
- **Health State Granularity**:
  - `HEALTHY`: Authenticated, socket streaming, latency < 150ms.
  - `DEGRADED`: REST operational, socket reconnecting, or latency > 500ms.
  - `DISCONNECTED`: API key expired, network unreachable, or exchange in maintenance.

---

## 9. CCXT Compatibility Test Gate

The following test suites in `tests/` verify CCXT compatibility:

| Test Suite | File Path | Validation Scope | Result |
|---|---|---|---|
| **CCXT Multi-Venue Normalization** | `tests/test_ccxt_exchange_compatibility.py` | Symbol formatting, precision quantization, preflight checks | **PASS** |
| **Exchange Capabilities & Certification** | `tests/test_exchange_capabilities.py`, `tests/test_exchange_certification.py` | Level 5 certification matrix, feature flags | **PASS** |
| **Real Connection Verification** | `tests/test_real_exchange_connection_verification.py` | AES-256 vault credential loading and socket pool | **PASS** |
| **Execution Safety & Risk Gate** | `tests/test_live_risk_gate_enforcement.py` | Circuit breaker halts order routing before CCXT call | **PASS** |

---

## 10. Final CCXT Implementation Gate Report

### **CCXT COMPATIBILITY STATUS:**

### **`[X] PASS WITH DOCUMENTED LIMITATIONS`**

#### Documented Limitations:
1. **Coinbase**: Spot trading only (no derivatives or open futures positions on standard retail adapter).
2. **Bitfinex & Gate.io**: Level 2/3 certification (Read-only / Sandbox only; live execution restricted to Level 5 certified venues: Binance, Bybit, Coinbase, Kraken, OKX).
3. **OKX & KuCoin**: Require API Passphrase in addition to API Key and Secret.

---
*CCXT Compatibility & Normalization Specification Certified by Antigravity Quantitative AI Architecture Team.*
