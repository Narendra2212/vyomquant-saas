# EXCHANGE ADAPTER INVENTORY AND CAPABILITY MATRIX

**Phase 10 · Workstream E · Step 1–2**
**Revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`), working tree dirty — see §0.3
**Date:** 2026-10-10
**Live trading during this work:** OFF. No order was placed. No credential was used or invented.

---

## 0. How to read this document

### 0.1 The three evidence classes, kept apart

This workstream exists because the Phase 9 report does not establish end-to-end exchange
sandbox execution. Every claim below is therefore labelled with how it was established:

| Label | Means |
|---|---|
| **CODE** | Read in the source. Says what is implemented, not that it works. |
| **MEASURED** | Executed locally and observed. Cited to a run log in `staging/`. |
| **MOCK** | A test exists and passes, against a substitute venue. Not venue evidence. |
| **SANDBOX** | Observed against a real exchange sandbox. |
| **NONE** | No evidence of any kind. |

**There is not one single `SANDBOX` cell in this document.** That is the headline finding
of Step 1, and it is unchanged from Phase 7's position: no order from this platform has
ever been observed at an exchange sandbox, in any phase.

### 0.2 What "supported" is not

Per the brief, an exchange is not counted as supported because it appears in a registry or
configuration list. Four separate registries in this codebase name exchanges, they
disagree with each other, and two of them make claims the installed library contradicts:

| Registry | File | Names | Agrees with reality? |
|---|---|---|---|
| Order-execution allowlist | `backend_app/backend/exchange_executor.py:1756` | 7 | **Yes** — this is the only authoritative one |
| Rate-limit enum | `backend_app/core/exchange_rate_limiter.py:155-169` | 10 | Moot — the module has zero importers |
| Certification registry | `backend_app/core/exchange_certification.py:93-258` | 8 | **No** — EXCH-04 |
| Connection form schema | `backend_app/core/exchange_connection_schema.py:98-400` | 6 | **No** — EXCH-04 |
| Market-data connector | `backend_app/backend/connection_engine.py:509-514` | ~100 | Technically yes, commercially misleading — EXCH-10 |

### 0.3 Working-tree note

`git status` at the time of writing shows ~80 modified and added paths from other
workstreams in flight. Nothing in this workstream edited any of them. The files this
workstream created are listed in §6 of
[`EXCHANGE_SANDBOX_CERTIFICATION.md`](EXCHANGE_SANDBOX_CERTIFICATION.md); all are new.

---

## 1. Adapter inventory

### 1.1 There are no per-exchange adapters

The single most important structural fact about this integration layer:

> **Every exchange in every registry is served by exactly one generic class.** There is no
> `BinanceAdapter`, no `KrakenClient`, no venue-specific signing code, and no exchange REST
> base URL constructed anywhere in `backend_app`.

**CODE.** Two generic components, both thin wrappers over `ccxt`:

| Component | File | Role | Venue scope |
|---|---|---|---|
| `CCXTExchangeExecutor` | `backend_app/backend/exchange_executor.py:842` | **Orders**, balance, order status | 7, by allowlist |
| `ConnectionEngine` | `backend_app/backend/connection_engine.py:96` | Market data, WebSockets | any `ccxt.pro` id (~100) |

`ccxt[async]==4.3.92` is pinned (`requirements-base.txt:95`) and installed — **MEASURED**,
`pip show ccxt` → `4.3.92`.

The entire per-exchange differentiation on the live path amounts to three things:

1. a `password` passthrough for the three passphrase venues (`exchange_executor.py:859-860`);
2. two `if self.exchange_id ==` branches in `normalize_symbol` (`:524-546`) — and that
   method is **not called by `place_order`**, which uses `format_symbol_for_ccxt` instead;
3. a table of rate-limit numbers in a module nothing imports.

Everything else venue-specific — endpoints, signing, error codes, precision conventions —
lives inside ccxt.

**Consequence for certification:** venue-specific risk cannot be reasoned about from this
codebase. It is ccxt's behaviour per venue, and ccxt's per-venue behaviour is only
observable by talking to that venue. This is precisely why sandbox evidence is not
optional here, and why its absence is the central gap.

### 1.2 Which exchanges can actually receive an order

**CODE**, `ExchangeExecutorFactory.SUPPORTED_EXCHANGES` (`exchange_executor.py:1756`),
one line:

```python
SUPPORTED_EXCHANGES = ["binance", "bybit", "coinbase", "kraken", "okx", "bitget", "kucoin"]
```

`create()` does a membership check then `getattr(ccxt, exchange_id)`. Anything else raises
`ValueError: Unsupported exchange`.

| Exchange | Order path | Named in | Status |
|---|---|---|---|
| binance | **yes** | allowlist, rate enum, schema, certification L5 | Generic CCXT |
| bybit | **yes** | allowlist, rate enum, schema, certification L5 | Generic CCXT |
| coinbase | **yes** | allowlist, rate enum, schema, certification L5 | Generic CCXT |
| kraken | **yes** | allowlist, rate enum, schema, certification L5 | Generic CCXT |
| okx | **yes** | allowlist, rate enum, schema, certification L5 | Generic CCXT |
| kucoin | **yes** | allowlist, rate enum, schema, certification L4 | Generic CCXT |
| bitget | **yes** | allowlist, rate enum **only** | Generic CCXT; no form, no certification record → `/supported` reports `certification_level: 0` |
| gateio | no — `ValueError` | rate enum, certification L3 | **Facade** |
| mexc | no — `ValueError` | rate enum | **Facade** |
| htx | no — `ValueError` | rate enum | **Facade** |
| bitfinex | no — `ValueError` | certification L2 | **Facade** |

**Exchanges that do not exist in this codebase in any form:** Delta Exchange, Zerodha /
Kite, and any other Indian broker. Grep over `backend_app/**/*.py` for
`zerodha|kite|delta_exchange|deltaexchange` returns nothing. If the product plan assumes
an Indian equity or F&O venue, there is no integration to certify — reported to the
coordinator in §2.

### 1.3 Dead and stubbed code on the exchange path

**MEASURED** (import-graph search over `backend_app/`, `tests/`, `scripts/`). Each of these
modules exists, reads as a working feature, and is never called from a live path:

| Module | What it would do | Why it matters |
|---|---|---|
| `core/exchange_rate_limiter.py` | per-exchange token buckets | zero importers; the only per-venue rate config in the repo |
| `core/exchange_rate_limit_engine.py` | adaptive throttle on 429 | zero importers; the **only** 429 back-off logic in the repo |
| `exchange_executor.CircuitBreaker` | trip on repeated venue failure | `CircuitBreaker.call` has no call site |
| `backend/fee_engine.py` | record real venue fees | its own docstring records that no production path calls it |
| `backend/exchange_websocket_listener.py` | fills as "primary source of truth" | never started; `main.py` does not reference it |
| `backend/exchange_reconciliation.py` | orders vs venue open orders | only referenced from the unstarted listener, via a class name that **does not exist** in the module (`ExchangeReconciliationEngine` vs the actual `ExchangeReconciliationService`) — would `ImportError` if reached |
| `distributed_execution/exchange_reconciliation_engine.py` | order+position+balance reconciliation on a schedule | never started, and **every data source is a stub** returning `[]` with hardcoded `["tenant_1","tenant_2"]` / `["binance","bybit"]` |
| `backend/reconciliation_worker.py` | the real drift detector | its own header says `NOTHING STARTS IT` |
| `distributed_execution/idempotent_exchange_submission.py` | claim-based duplicate prevention | returns `RISK_EVALUATION_REQUIRED` before reaching the engine |
| `exchange_executor.place_order_with_idempotency` / `place_order_and_wait_confirmation` | confirmation polling | no live callers |
| `data_seeking_engine.fetch_my_trades` | fill history | no callers at all |

**What actually runs in production** on the exchange path is narrow: `OrderWatchdog`
(`main.py:431`) and `core/reconciliation_scheduler.py` (`main.py:456`) — and the latter
never contacts an exchange. It polls a `reconciliation_mismatches` table and escalates by
severity, and **no running detector writes to that table.**

---

## 2. Per-exchange capability matrix

Legend: ✔ implemented · ✖ not implemented · n/a not applicable · **evidence class in bold**

### 2.1 Connectivity and account

| Capability | binance | bybit | coinbase | kraken | okx | bitget | kucoin |
|---|---|---|---|---|---|---|---|
| Adapter implemented | generic | generic | generic | generic | generic | generic | generic |
| Bespoke adapter code | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ | ✖ |
| Auth: key+secret via ccxt | ✔ **CODE** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Auth: passphrase | n/a | n/a | ✔ | n/a | ✔ **req** | ✔ **req** | ✔ **req** |
| Platform-side signing | ✖ — ccxt signs; no `hmac` on any exchange path | | | | | | |
| Credential at rest | Fernet/`MultiFernet` AES-256, `exchange_keys` table, fail-closed without `MASTER_ENCRYPTION_KEYS` **CODE** | ← | ← | ← | ← | ← | ← |
| Auth verified at a venue | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** |
| Balance retrieval | ✔ `fetch_balance` **CODE/MOCK** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Balance verified at a venue | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** |

Note on balance: `get_balance()` swallows every exception and returns `{}`
(`exchange_executor.py:1434-1437`), so an empty map is indistinguishable from a venue
error. Separately, `master_executor.py:264-280` substitutes a fabricated
`{"USDT": {"total": 10000.0}}` wallet when `api_key` is falsy, and that figure feeds
position sizing — flagged as EXCH-11.

### 2.2 Market data

| Capability | All seven |
|---|---|
| REST candles | ✔ `fetch_ohlcv`, paginated, exponential backoff **CODE** (`data_seeking_engine.py:206-240`) |
| REST ticker / order book | ✔ `fetch_ticker`, `fetch_order_book` **CODE** |
| WebSocket candles / ticker | ✔ `watch_ohlcv`, `watch_ticker` via `ccxt.pro` **CODE** |
| WebSocket order/fill stream | ✔ implemented, **✖ never started** — EXCH-12 |
| Market-data sandbox routing | `set_sandbox_mode` **MEASURED**; correct, and `try/except`-warned when unsupported |
| Hardcoded-venue exception | `routers/strategies.py:3065` instantiates `ccxt.binance()` and calls **synchronous** `fetch_ohlcv` inside a request handler |

Three hardcoded WebSocket URLs exist and all three are wrong or placeholder:
`event_listener.py:430` builds `wss://stream.binance.com:9443/ws/order@{tenant_id}` — Binance
has no such stream and user-data streams need a `listenKey`, so it cannot work; and
`dag_event_loop.py:2029` / `dag_risk_integration.py:746` are literal
`wss://stream.exchange.com/...` placeholders.

### 2.3 Order types

**CODE.** `OrderType` declares six members (`exchange_executor.py:65-73`) and `map_order_type`
(`:643-654`) passes each through as ccxt's `type` string unchanged.

| Order type | Enum member | Reaches a venue as a valid ccxt type? |
|---|---|---|
| Market | `MARKET` → `"market"` | ✔ unified |
| Limit | `LIMIT` → `"limit"` | ✔ unified |
| Stop market | `STOP_MARKET` → `"stop_market"` | ✖ **not a ccxt unified type** |
| Stop limit | `STOP_LIMIT` → `"stop_limit"` | ✖ **not a ccxt unified type** |
| Take-profit market | `TAKE_PROFIT_MARKET` | ✖ **not a ccxt unified type** |
| Take-profit limit | `TAKE_PROFIT_LIMIT` | ✖ **not a ccxt unified type** |

So **four of six declared order types are facade** — identical for all seven venues. The
trigger price itself is handled correctly (`params["stopPrice"]`, `:1111-1114`, with a
comment recording that the previous `create_order(**params)` form raised `TypeError` on
every stop order), but the `type` string most venues will reject.

**Absent entirely** across all venues: OCO, post-only, reduce-only, `timeInForce`. Grep for
`reduceOnly|postOnly|timeInForce|OCO` on the exchange path returns nothing. `reduce_only`
exists only as an internal risk-check bypass flag for cancels and close-alls and is never
sent to a venue.

This directly contradicts the certification registry, which marks `post_only=True` and
`reduce_only=True` for six of the seven — EXCH-04.

### 2.4 Order lifecycle

| Capability | binance | bybit | coinbase | kraken | okx | bitget | kucoin |
|---|---|---|---|---|---|---|---|
| Create order | ✔ **CODE/MOCK** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Cancel order | ✔ **CODE/MOCK** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Status by venue id | ✔ `fetch_order` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Status by **our** client id | ✔ 3-strategy lookup **CODE** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Client id accepted by venue | **NONE** — unverified on every venue | | | | | | |
| Open-order list | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Fill / trade list | ✔ implemented, **no callers** | ← | ← | ← | ← | ← | ← |
| Any of the above at a venue | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** | **NONE** |

`find_order_by_client_order_id` (`:1265-1413`) is the best-engineered function on this path:
three fallback strategies, and it distinguishes "the venue says no such order" (a definite,
safe negative) from "the venue could not be asked" (unresolved). It is the **only** recovery
handle for an order whose submission response was lost, and `OrderWatchdog` depends on it.

It rests on an unverified premise: that ccxt's unified `clientOrderId` maps to each venue's
native field — `newClientOrderId` on Binance, `orderLinkId` on Bybit, `clientOid` on KuCoin,
numeric `userref` on Kraken. Nothing in this repository verifies the mapping or that the
venue echoed the id back. **This is the premise the platform's entire duplicate-order
protection rests on, and it is untested on all seven venues.** Scenario 8 of the sandbox
plan exists to settle it.

### 2.5 Partial fills

| Aspect | Finding |
|---|---|
| Partial-fill event type | ✔ `OrderEventType.PARTIAL_FILL` exists (`exchange_websocket_listener.py:45`) |
| Cumulative quantity | Taken from ccxt's `filled`, **assigned not accumulated** (`:373-400`, `order.filled_size = str(event.filled)`) |
| Is assignment correct? | Only if ccxt's `filled` is order-cumulative on every venue. **Unverified at any venue.** |
| Missed-event reconstruction | ✖ none. No sequence number, no version check |
| Double-count hazard | ✔ present. If `trades` is empty but `lastTradeTimestamp` is set, `_handle_fill_event` (`:296-360`) synthesises **one** trade whose `amount` is the **cumulative** `filled`, with `trade_id` = the order id. A second partial-fill event re-posts the whole cumulative quantity, deduplicated only by that reused id |
| Evidence | **NONE** at any venue |

The listener carrying all of this is never started, so in production fills arrive via
`OrderWatchdog`'s 5–10 s polling loop instead — which compares `filled_size` strings and
has no partial-fill-specific handling.

### 2.6 Precision, minimum quantity, minimum notional

**This is the strongest part of the integration**, and it is fully generic — driven by
ccxt's unified `markets` map rather than hardcoded tables.

| Aspect | Finding | Evidence |
|---|---|---|
| Symbol-info fetch | ✔ real `load_markets()` on connect | **CODE** `:436-439`, `:876` |
| Both ccxt precision conventions | ✔ `DECIMAL_PLACES` **and** `TICK_SIZE` (`_decimal_places`, `:461-496`) | **CODE** |
| Refuses the ambiguous value `1` | ✔ returns `None` rather than guessing | **CODE** |
| Float read safely | ✔ `Decimal(repr(v))`, not `Decimal(v)` | **CODE** |
| Tolerates raw and unified symbols | ✔ `_market_for` (`:498-515`) | **CODE** |
| Rounding direction | ✔ `ROUND_DOWN` throughout — can only shrink an order | **CODE** |
| Amount **step** snapping | ✔ distinct from decimal truncation (`:608-621`) | **CODE** |
| Min quantity enforced pre-send | ✔ `BELOW_MIN_QUANTITY` (`:1057-1072`) | **CODE/MOCK** |
| Min notional enforced pre-send | ✔ `BELOW_MIN_NOTIONAL` (`:1074-1090`) | **CODE/MOCK** |
| Fallback when venue silent | 8 decimal places (`:573-585`) | **CODE** |
| Verified against real venue metadata | **NONE** — `load_markets()` has never run against any venue in a test | |

Two caveats. ccxt 4.3.92's `okx.parse_market` is documented in
`connection_engine.py:196-250` as raising `TypeError` on a pre-open OKX SPOT instrument, with
the note *"Failing fast does NOT make the venue load — OKX markets still do not load on this
ccxt"* — so OKX precision data may be unobtainable on the pinned version while the
certification registry marks OKX `LEVEL_5_PRODUCTION_READY` + `HEALTHY`. And dev-mode
injects mock markets with integer `precision` (`connection_engine.py:240-300`), the
`DECIMAL_PLACES` convention, whereas Binance/Kraken/Coinbase report `TICK_SIZE` — so
dev-mode rounding does not match production.

### 2.7 Rate limiting and error handling

| Aspect | Finding | Evidence |
|---|---|---|
| Per-exchange limits configured | ✔ binance 100/s … kraken 1/s (`exchange_rate_limiter.py:171-182`) | **CODE** |
| …and used? | ✖ **module has zero importers** | **MEASURED** |
| What actually throttles orders | `RateLimiter(requests_per_second=5.0)` — **flat 5/s for every venue** (`exchange_executor.py:689`) | **CODE** |
| …plus ccxt's `enableRateLimit` | ✔ the **only** genuinely per-venue throttle, and it belongs to ccxt | **CODE** |
| HTTP 429 handling | ✖ none on the live path | **MEASURED** |
| `Retry-After` parsing | ✖ none. `RateLimitError` hardcodes `retry_after=60` and no caller reads it | **CODE** |
| Per-venue error-code table | ✖ none. No `-1021`, `-2010`, `10006` anywhere | **MEASURED** |
| Error classification | substring match on `str(error)` (`_handle_ccxt_error`, `:803-836`) | **CODE** |
| ccxt typed exceptions used | ✖ `InsufficientFunds`, `DDoSProtection`, `OrderNotFound` appear nowhere | **MEASURED** |
| Circuit breaker | implemented, **never called** | **MEASURED** |

The classifier's ladder is `insufficient|balance` → `rate limit|too many requests` →
`symbol|market` → `network|timeout|connection` → `authentication|apikey|key`. Two
observations: there is no `OrderNotFound` branch, so cancelling an already-cancelled order
is indistinguishable from a transient failure (scenario 6b); and the bare `"key"` substring
is broad enough to misclassify any message mentioning a client order id — reachable only
after the earlier branches, but worth knowing.

Kraken's real private limit is 1 req/s and Binance's is 100. The live limiter treats them
identically at 5.

### 2.8 Retry, timeout, idempotency

| Aspect | Finding | Evidence |
|---|---|---|
| HTTP timeout, order path | **unset** → ccxt default 10 s (`exchange_executor.py:856-860`) | **CODE** |
| HTTP timeout, market data | 30 s, commented "CRITICAL for order submission safety" (`connection_engine.py:134`) | **CODE** |
| …the asymmetry | The path labelled order-critical is the one *without* the timeout | |
| Retry on order submission | ✖ none. Single `try`; any exception → `OrderResult(status="rejected")` | **CODE** |
| Timeout labelled `"rejected"` | ✔ — a claim the code has not established (`:1175-1197`) | **CODE** |
| Venue-side idempotency key | ✔ `params["clientOrderId"] = execution_id` (`:1119-1120`) | **CODE** |
| Key is exchange-native? | ccxt **unified** `clientOrderId`; per-venue mapping unverified | **NONE** |
| Venue echo-back verified | ✖ nothing checks it | **MEASURED** |
| If `execution_id` unset | key omitted — order goes out with **no** venue idempotency | **CODE** |

What saves the mislabelled timeout is downstream, not local: `signal_service.
classify_execution_failure` (`:3086-3130`) splits failures into DETERMINATE (venue refused →
`FAILED`) and INDETERMINATE (no answer → signal **held at PENDING** for the recovery sweep),
and **defaults to INDETERMINATE** for unrecognised exception types. That is the right
direction, and it is the only place "unknown outcome" is a first-class state.

### 2.9 Timeout after submission, reconnect, restart recovery

| Aspect | Finding | Evidence |
|---|---|---|
| In-doubt adjudication | ✔ `OrderWatchdog.recover_unsubmitted_executions`, **started** at `main.py:431` | **CODE** |
| Classification | `failed_before_submission` → FAILED; `ambiguous_submission` → ask venue by `clientOrderId`; else leave | **CODE** |
| Ever resubmits? | ✖ **never** — correct, and stated explicitly in the code | **CODE** |
| Detection latency | up to **15 min** (`RECOVERY_STALE_AFTER_SECONDS = 900`) + a 30 s tick | **CODE** |
| Fill found at venue | COMPLETED + `logger.critical` + alert: "position accounting must be reconciled" | **CODE** |
| WS reconnect | fixed **1 s** retry, no backoff, no jitter, no cap, no metric; `symbols` arg accepted and unused; `start_listening` never sets `_running` | **CODE** |
| …and the listener | **never started** | **MEASURED** |
| Restart: re-query open orders | ✖ **no**. `master_executor._recover_live_state` re-queries **balance only**; never calls `fetch_open_orders` | **MEASURED** |
| Account-level "what is open at the venue" sweep | ✖ exists nowhere | **MEASURED** |

### 2.10 Fees

| Aspect | Finding | Evidence |
|---|---|---|
| Venue `commission` / `commissionAsset` parsed | ✖ appears nowhere for live fills | **MEASURED** |
| Fee engine quality | **good** — three fee-currency cases, and refuses to value a third-asset fee (e.g. BNB on BTC/USDT) rather than treating 0.0005 BNB as \$0.0005 | **CODE** |
| …and its callers | ✖ **none in production**; stated in its own docstring and confirmed | **MEASURED** |
| Live fill accounting | `TransactionalExecutionManager.handle_fill` has **no fee field at all** | **CODE** |
| WS listener | extracts `fee.cost`/`fee.currency` into `FillEvent`, then **drops both** when building `fill_data` (`:344-351`) | **CODE** |
| What reaches PnL | flat `fee_rate=0.001`, identical for every venue and both maker and taker | **CODE** |
| Per-venue fee accuracy | **NONE** | |

`fetch_trading_fees` is implemented (`data_seeking_engine.py:276-300`) with a comment noting
that without it "fee estimates may be wrong by 3-10x on VIP tiers". It has no callers.

### 2.11 Reconciliation

| Implementation | Compares | Scheduled? | Contacts a venue? |
|---|---|---|---|
| `OrderWatchdog._reconcile_open_orders` | per-order status, `order_id IS NOT NULL` only | ✔ every 5–10 s | ✔ **the only one that does** |
| `OrderWatchdog.recover_unsubmitted_executions` | `order_id IS NULL` population | ✔ every 30 s | ✔ |
| `exchange_reconciliation.py` | orders vs `fetch_open_orders` | ✖ | would, if started |
| `distributed_execution/exchange_reconciliation_engine.py` | orders+positions+balances | ✖ | ✖ **every source stubbed** |
| `reconciliation_worker.py` | 5 drift classes, orders+positions+balances | ✖ `NOTHING STARTS IT` | would |
| `core/reconciliation_scheduler.py` | a DB table | ✔ every 30 s | ✖ |

So: **balance reconciliation runs nowhere. Fill/trade reconciliation exists nowhere.
Position drift against the venue is detected by nothing that executes.** The only live
reconciliation is per-order status polling, and the scheduler that *is* running escalates
mismatches that no detector writes.

One piece of good hardening worth preserving: `_fetch_exchange_orders`
(`exchange_reconciliation.py:231-239`) re-raises on API error instead of returning `[]`,
because an empty list would make the caller cancel the entire book on a transient 429.

### 2.12 Sandbox routing — MEASURED

Full run: [`staging/p10e_sandbox_probe.txt`](p10e_sandbox_probe.txt) ·
machine-readable: [`staging/phase10_exchange_sandbox_matrix.json`](phase10_exchange_sandbox_matrix.json)

| Exchange | `urls['test']` | `set_sandbox_mode` (market data) | `connect(sandbox=True)` (**orders**) | Certifiable? |
|---|---|---|---|---|
| binance | ✔ | accepted, URL → testnet | **routes to testnet** | ✔ |
| bybit | ✔ | accepted, URL → testnet | **routes to testnet** | ✔ |
| okx | ✔ | accepted, header `x-simulated-trading: 1` | **routes via header** | ✔ |
| coinbase | ✖ | `NotSupported` (warned) | **RAISES `NotSupported`** | ✖ |
| kraken | ✖ | `NotSupported` (warned) | **RAISES `NotSupported`** | ✖ |
| kucoin | ✖ | `NotSupported` (warned) | **RAISES `NotSupported`** | ✖ |
| bitget | ✖ | accepted, **changes nothing** | **ACCEPTED — STAYS ON PRODUCTION** | ✖ **EXCH-01** |

OKX is the case a URL comparison alone would misclassify: demo trading shares the
production host and is selected by a header, so "URL unchanged" is the correct outcome
there and the harness checks headers too.

Two mechanisms exist and they are not equivalent. `ConnectionEngine` calls
`set_sandbox_mode(True)` explicitly, inside a `try/except` that only warns — correct.
`CCXTExchangeExecutor.connect` never calls it; it passes `config["sandbox"] = True` and
`config["options"] = {"sandbox": True}` to the constructor. ccxt 4.3.92's
`Exchange.__init__` reads

```python
is_sandbox = self.safe_bool_2(self.options, 'sandbox', 'testnet', False)
if is_sandbox:
    self.set_sandbox_mode(is_sandbox)
```

— from `self.options`, so the **top-level key is dead code and the `options` key is doing
all the work** (EXCH-03). The pair reads as belt-and-braces and is not: deleting the
"redundant" second line would silently route every sandbox order to production.

---

## 3. Findings raised by this workstream

Full reproduction steps, code paths, risk and recommended owners are in
[`EXCHANGE_SANDBOX_CERTIFICATION.md` §5](EXCHANGE_SANDBOX_CERTIFICATION.md). Summary:

| ID | Pri | Exchange | Capability | One line |
|---|---|---|---|---|
| **EXCH-01** | **P0-class** | bitget | sandbox routing | "Sandbox mode" is accepted, logged, and sends orders to **production** with real money |
| EXCH-02 | P1 | coinbase, kraken, kucoin | sandbox routing | `connect(sandbox=True)` raises `NotSupported` from outside the `try`, unclassified |
| EXCH-03 | P3 | all | sandbox routing | `config["sandbox"]` is dead code; only `config["options"]["sandbox"]` arms it |
| **EXCH-04** | **P1** | coinbase, kucoin, okx, +order types | certification registry | Registry claims sandboxes and order types that do not exist, and gates live deployment on those claims |
| EXCH-05 | P2 | coinbase, kucoin | product copy | Customer-facing FAQ advertises two sandboxes that raise `NotSupported` |
| EXCH-06 | P3 | bitget | registry coverage | Tradable but absent from the certification registry → `certification_level: 0` |
| EXCH-07 | P1 | all | restart recovery | Restart re-queries balance only; resting orders are never rediscovered |
| EXCH-08 | P1 | all | fee accounting | Venue-reported fees are parsed nowhere; PnL uses a flat 0.001 estimate |
| EXCH-09 | P1 | all | reconciliation | The only scheduled reconciler escalates from a table no running detector writes |
| EXCH-10 | P2 | ~100 venues | product copy | `/api/exchanges/supported` advertises every ccxt venue; 7 can trade |
| EXCH-11 | P2 | all | balance | A fabricated \$10,000 USDT wallet feeds position sizing when `api_key` is falsy |
| EXCH-12 | P2 | all | fills | The WebSocket listener documented as "primary source of truth" is never started |

---

## 4. Step 2 — launch scope, and an ambiguity for the coordinator

### 4.1 The mandatory exchange list is NOT defined in this repository

**Reported, not resolved.** Per the brief, this is escalated rather than silently decided.

Searched: `.kiro/specs/*/requirements.md`, `design.md`, `tasks.md`,
`production-launch-hardening/bugfix.md`, `deployment_go_no_go.md`,
`FINAL_LAUNCH_READINESS_REPORT.md`, `FINAL_LAUNCH_SCORECARD.md`,
`POST_LAUNCH_IMPROVEMENTS.md`, `docs/`.

What exists:

- a **constraint**, repeated in several places and respected by this workstream:
  `production-launch-hardening/tasks.md:1022` — *"Live-order testing against a real
  exchange is out of bounds."*
- a **recommendation about product copy**, `FINAL_PRE_LAUNCH_TEST_REPORT.md:230` — state the
  supported count as 7 (or 6 verified), and either gate `/api/exchanges/supported` to the
  tradable set or label the rest as not tradable.
- a **standing verdict**: `TRADING LIVE: OFF / NOT CERTIFIED`, `PAPER-ONLY: NO-GO`.

What does not exist: any document naming which exchanges must work at launch.

**Three questions for the coordinator.** Each changes the answer materially:

1. **Which venues is the business committed to at launch?** Everything in §4.2 is a
   recommendation derived from what is certifiable, not from a commitment.
2. **Is any Indian broker in scope?** There is no Delta Exchange or Zerodha code of any
   kind. If an Indian venue is assumed, this is a missing integration, not a defect.
3. **Does launch mean live trading, paper trading, or both?** Phase 7 and 9 leave
   `TRADING PAPER` NOT CLASSIFIED specifically because no paper order has ever been
   observed at a venue sandbox. If launch is paper-only, the sandbox evidence in §4.2
   is still required, for the same reason.

### 4.2 Recommendation — smallest useful first-launch set

**Recommendation only. Not a launch commitment, and not a certification.**

> **Launch on Binance and Bybit. Hold OKX for a second wave. Remove the other four from
> the tradable set until each is separately justified.**

| Venue | Recommendation | Why |
|---|---|---|
| **binance** | **launch candidate** | Only venue with any bespoke code; real testnet with URL-level separation; largest retail overlap |
| **bybit** | **launch candidate** | Real testnet with URL-level separation; the only other venue that can be certified the same way |
| okx | second wave | Sandbox is certifiable (header), **but** `load_markets` is documented as broken on the pinned ccxt, which would block precision data. Needs that resolved first |
| coinbase | **remove from tradable set** | No sandbox exists. Cannot be certified at all on this stack. Currently advertised as having one |
| kraken | **remove from tradable set** | No sandbox exists. Registry is honest about it; the capability still cannot be certified |
| kucoin | **remove from tradable set** | No sandbox exists. Currently advertised as having one. Already L4/`DEGRADED` |
| **bitget** | **remove immediately — ahead of any launch decision** | EXCH-01. Not a scope question: it is a live money-safety defect today |

Two venues, not one, because a single-venue launch makes every venue-specific assumption
in §2 invisible — the `clientOrderId` mapping, the cumulative-`filled` premise and the
precision convention all differ per venue, and one venue cannot reveal that.

**What this recommendation is conditional on.** It is "smallest useful set to *attempt*
certification on", not "set that is certified". Nothing in §2 has a single `SANDBOX`
cell. Launching Binance and Bybit requires running the plan in
[`EXCHANGE_SANDBOX_CERTIFICATION.md`](EXCHANGE_SANDBOX_CERTIFICATION.md) §2 with real
testnet credentials and getting green results — which cannot be done in this environment,
because no sandbox credential exists and none was invented.

---

## 5. Provenance

| Claim class | How established |
|---|---|
| Adapter structure, dead code, call graphs | Source reading and import-graph search over `backend_app/`, `tests/`, `scripts/` |
| Sandbox availability and routing | Executed: `staging/probe_phase10_exchange_sandbox.py` → `staging/p10e_sandbox_probe.txt`, `staging/phase10_exchange_sandbox_matrix.json` |
| EXCH-01, EXCH-03, EXCH-04, EXCH-05 | Executed: `tests/exchange_sandbox/test_sandbox_routing_contract.py` → `staging/p10e_routing_contract.txt` |
| ccxt version | `pip show ccxt` → 4.3.92, matching the `requirements-base.txt:95` pin |
| ccxt sandbox mechanism | `inspect.getsource` on `Exchange.__init__`, `Exchange.set_sandbox_mode` and the four per-venue overrides |
| Absence of sandbox credentials | `exchange_keys.json` parses to `{}`; no `AERORA_SANDBOX_*`, `*_TESTNET` or `MASTER_ENCRYPTION_KEYS` in the environment; no CI workflow sets one |

Trees deliberately excluded as dead copies, confirmed by zero imports from `backend_app`:
`aerora_quant_platform/`, `connection_layer/`, `archive/`, `deploy-archive/`.
