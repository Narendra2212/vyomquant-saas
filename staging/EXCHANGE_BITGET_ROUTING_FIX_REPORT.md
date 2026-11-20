# EXCH-01 FOLLOW-UP — BITGET ROUTING CONSISTENCY FIX

**Phase 10 · Workstream G**
**Date:** 2026-10-10
**Live trading:** OFF throughout. No order submitted. No credential used. No network call.

---

## 1. Baseline

Established by inspection before any edit. The prior Workstream F report was treated as
historical evidence only; the working tree was verified directly.

| | |
|---|---|
| Branch | `main` |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| HEAD subject | *Give the deploy gate permission to read the answer it refuses without* |
| Changed paths at baseline | **241** |
| Reset / stash / clean / revert / checkout | **none** |

### 1.1 Relevant files already modified by other workstreams

| Path | Baseline status | Note |
|---|---|---|
| `backend_app/backend/exchange_executor.py` | ` M` | **665 insertions / 50 deletions** already present |
| `tests/conftest.py` | ` M` | pre-existing |
| `pytest.ini` | ` M` | pre-existing |
| `backend_app/routers/exchange.py` | `MM` | pre-existing, staged + unstaged |
| `tests/test_exchange_safety_fix.py` | ` M` | pre-existing |
| `tests/test_execution_paths_that_claim_an_exchange.py` | ` M` | pre-existing |
| ~30 other `tests/*.py` | ` M` / `A` | pre-existing |

### 1.2 Verification that Bitget was still routable

**Not assumed — read from the working tree.** `backend_app/backend/exchange_executor.py`
at baseline:

```python
SUPPORTED_EXCHANGES = ["binance", "bybit", "coinbase", "kraken", "okx", "bitget", "kucoin"]
```

A `git diff` of that file filtered for `bitget|SUPPORTED_EXCHANGES` returned **nothing**,
establishing that the allowlist line sat at its committed state and that the other
workstream's 665 insertions did not touch it. That mattered: it meant the one-line change
below could not collide with their in-flight work.

---

## 2. Root cause and affected routing paths

### 2.1 Root cause

`core/exchange_capability_registry.py` declared its own `SAFETY_BLOCKED` dict and filtered
bitget out of `order_routing_allowlisted()`. The registry reads `SUPPORTED_EXCHANGES`
**from** the executor; **nothing in the executor ever read the registry.**

So the block was advisory. The registry's *report* was correct and the executor's
*behaviour* was not — which is the more dangerous of the two possible inconsistencies,
because it reads as fixed. The capability API returned `safety_blocked: ["bitget"]` while
the order path would have signed a real bitget order with a real credential.

### 2.2 Paths that could still route to Bitget

| # | Path | Baseline behaviour |
|---|---|---|
| 1 | `ExchangeExecutorFactory.create("bitget", …)` | bitget in `SUPPORTED_EXCHANGES` → **built an executor** |
| 2 | `get_exchange_executor(…, exchange_id="bitget")` | delegates to the factory → **built an executor** |
| 3 | `CCXTExchangeExecutor(exchange_id="bitget", …)` | **no check whatsoever** |
| 4 | `asset_universe.supported_exchange_ids()` | reads the allowlist → **bitget markets reached the strategy asset selector** |

Path 3 is the one that made a factory-only fix insufficient: `CCXTExchangeExecutor` is a
public class and other code paths instantiate it directly.

Path 4 was not in the prior report and was found by tracing consumers of the allowlist.
It is why removal from the list is needed *in addition to* a constructor refusal — a
refusal alone would leave bitget advertised as routable to every reader of that attribute.

### 2.3 Out of scope, recorded not changed

`backend/reconciliation_worker.py:293` carries its own hardcoded venue list that includes
`bitget`. That module is never started (its own header states `NOTHING STARTS IT`), and
reconciliation is explicitly excluded from this task. **Reported, not touched.**

`core/exchange_rate_limiter.py` also names bitget, in a module with zero importers. Rate
limits are not routing permission; no change.

---

## 3. Files changed

| File | Change | Lines |
|---|---|---|
| `backend_app/backend/exchange_executor.py` | `SAFETY_BLOCKED_EXCHANGES`, `ExchangeRoutingBlocked`, `assert_exchange_routable`, two guard call sites, bitget removed from the allowlist | **+128 / −2** on top of the pre-existing +665/−50 |
| `backend_app/core/exchange_capability_registry.py` | `SAFETY_BLOCKED` now **imported** from the executor via `_load_safety_blocked()`, with a fail-closed fallback | +~40 / −12 |
| `tests/test_exch01_bitget_routing_is_blocked.py` | **new** — 22 focused tests | +~290 |
| `staging/p10g_focused.txt`, `staging/p10g_regression.txt` | **new** — run logs | — |
| `staging/EXCHANGE_BITGET_ROUTING_FIX_REPORT.md` | **new** — this report | — |

Changed-path count went 241 → 243.

### 3.1 Design decision

The authoritative block list was **moved into the executor** rather than left in the
registry and imported backwards. Two reasons:

- **Enforcement belongs at the point of construction.** A list consulted only by the
  layer that reports it is not a control.
- **It matches the dependency direction that already existed.** The registry already
  imported `SUPPORTED_EXCHANGES` from the executor, so reading the block list from the
  same place creates no import cycle. The reverse would have.

`_load_safety_blocked()` falls back to a hardcoded bitget entry if the executor import
fails, rather than returning an empty dict. An import error must not be the thing that
re-opens routing to a venue whose sandbox silently reaches production.

`ExchangeRoutingBlocked` subclasses `ValueError` deliberately: `create()` already raised
`ValueError` for an unsupported venue, so every existing caller's error handling continues
to fail closed without modification, while new code can still distinguish "withheld for a
money-safety defect" from "never supported".

---

## 4. Before / after

### 4.1 Allowlist and registry

| | Before | After |
|---|---|---|
| `ExchangeExecutorFactory.SUPPORTED_EXCHANGES` | 7 — incl. `bitget` | **6** — `binance, bybit, coinbase, kraken, okx, kucoin` |
| `asset_universe.supported_exchange_ids()` | 7 — incl. `bitget` | **6** |
| `registry.order_routing_allowlisted()` | 6 (filtered) | **6** |
| `registry.safety_blocked()` | `['bitget']` | `['bitget']` |
| Source of truth for the block | **two, disagreeing** | **one**, in the executor |
| `registry.tradable()` | 0 | **0** |

Verified by execution:

```
executor  : ['binance', 'bybit', 'coinbase', 'kraken', 'okx', 'kucoin']
universe  : ['binance', 'bybit', 'coinbase', 'kraken', 'okx', 'kucoin']
registry  : ['binance', 'bybit', 'coinbase', 'kraken', 'okx', 'kucoin']
blocked   : ['bitget']
tradable  : 0
```

All four consumers now agree. The six-venue set is unchanged from the approved baseline;
no exchange was added.

### 4.2 Behaviour per path

| Path | Before | After |
|---|---|---|
| `ExchangeExecutorFactory.create("bitget", …)` | returned an executor | `ExchangeRoutingBlocked` **with the reason** |
| `get_exchange_executor(…, "bitget")` | returned an executor | `ExchangeRoutingBlocked`, and **nothing cached** |
| `CCXTExchangeExecutor(exchange_id="bitget", …)` | returned an executor | `ExchangeRoutingBlocked` **before credentials are retained** |
| `"BITGET"` / `" bitget "` / `"BitGet"` | n/a | normalised and refused |
| `asset_universe` | offered bitget markets | does not |
| Approved six venues | constructed | **still construct** (negative control) |
| `mexc` (never supported) | `ValueError: Unsupported exchange` | unchanged — distinguishable from a block |

The guard is the **first statement** in `BaseExchangeExecutor.__init__`, ahead of the
assignments that retain the decrypted api key and secret, so a blocked venue never
produces an object holding credentials.

---

## 5. Test commands and results

### 5.1 Focused

```powershell
python -m pytest tests/test_exch01_bitget_routing_is_blocked.py -q --no-header --tb=short
```

**22 passed · 0 failed · 0 skipped · 0 collection errors** — [`p10g_focused.txt`](p10g_focused.txt)

Coverage against the brief's required cases:

| Required | Test |
|---|---|
| Bitget not in the executor allowlist | `test_bitget_is_not_in_the_executor_allowlist` |
| Registry still reports Bitget safety-blocked | `test_registry_reports_bitget_blocked_and_not_routable`, `test_registry_and_executor_agree_on_the_block` |
| Routing rejects Bitget before any order-submission method can execute | `test_factory_refuses_bitget`, `test_get_exchange_executor_refuses_bitget`, `test_direct_construction_of_a_bitget_executor_is_refused`, `test_the_refusal_happens_before_credentials_are_retained` |
| Six approved venues unchanged | `test_the_six_approved_routing_venues_are_unchanged` (exact set, so an **addition** also fails), `test_approved_venues_still_construct` |
| Connectivity / declared capability cannot expand the allowlist | `test_connectivity_cannot_widen_the_routing_allowlist` |
| Fail-closed trading authorization intact | `test_no_venue_is_trading_authorized` |

Additional cases not required but added: casing/whitespace evasion (5 parametrised),
cache pollution on refusal, `ValueError` compatibility for existing handlers, bitget
remaining `DISCOVERED` for market data, and the `asset_universe` consumer.

One test failed on first run — `test_asset_universe_no_longer_offers_bitget_markets`
imported `_universe_exchanges`, and the real function is `supported_exchange_ids`. My
test was wrong, not the code. Corrected and re-run; no assertion was weakened.

### 5.2 Regression

```powershell
python -m pytest tests/test_exch01_bitget_routing_is_blocked.py \
  tests/test_exchange_capability_registry.py tests/test_ccxt_exchange_compatibility.py \
  tests/test_exchange_capabilities.py tests/test_exchange_certification.py \
  tests/test_exchange_connection_schema.py tests/test_exchange_executor_cache_is_per_tenant.py \
  tests/test_exchange_preflight.py tests/test_exchange_runtime_health.py \
  tests/test_exchange_safety_fix.py tests/test_execution_paths_that_claim_an_exchange.py \
  tests/test_single_execution_pipeline.py tests/test_trade04_live_order_gate_at_venue_boundary.py \
  tests/test_paper01_paper_live_isolation.py tests/test_paper_live_separation.py \
  tests/test_quota01_exchange_slot_is_atomic.py tests/exchange_sandbox \
  -q --no-header --tb=line
```

**194 passed · 0 failed · 29 skipped · 0 collection errors** · 602.79s —
[`p10g_regression.txt`](p10g_regression.txt)

The 29 skips are the opt-in `tests/exchange_sandbox/` directory, gated on
`AERORA_EXCHANGE_SANDBOX_CERT=1` and not set here. Nothing was skipped to hide a failure;
no test was deleted or relaxed.

Suites chosen because they consume the allowlist or the venue boundary:
`test_trade04_live_order_gate_at_venue_boundary` (the live gate immediately before
`create_order`), `test_single_execution_pipeline` and
`test_execution_paths_that_claim_an_exchange` (executor construction), `test_paper_*`
(paper/live isolation), `test_quota01_exchange_slot_is_atomic` (passes `bitget` as a
credential-slot string, which is not routing and is unaffected).

---

## 6. Final diff and ownership-boundary verification

### 6.1 Did the three named files change?

| File | Changed by this workstream? |
|---|---|
| `backend_app/backend/exchange_executor.py` | **Yes** — allowlist, block list, exception, guard, two call sites |
| `backend_app/core/exchange_capability_registry.py` | **Yes** — `SAFETY_BLOCKED` now imported from the executor |
| Routing tests | **Yes** — `tests/test_exch01_bitget_routing_is_blocked.py` added |

### 6.2 Pre-existing modifications preserved

`exchange_executor.py` was already modified (+665/−50) by another workstream. My change
is additive on top of it. Verified by marker count — every one of their constructs is
still present:

```
TRADE-04 : 2    KILL-03 : 2                     TRADE-07 : 5
_executor_cache_key : 3                         require_live_authorization : 5
enforce_kill_switch : 4                         BELOW_MIN_NOTIONAL : 1
find_order_by_client_order_id : 2
```

Insertion count moved 665 → 793 and deletions 50 → 52: **+128 / −2 attributable to this
workstream.** The two deletions are the old `SUPPORTED_EXCHANGES` line and the blank line
replaced by the factory guard.

### 6.3 Scope exclusions — proven, not asserted

| Excluded | Baseline | Final | Touched by me? |
|---|---|---|---|
| `requirements-base.txt` | clean | **clean** | No — CCXT not upgraded |
| `backend_app/core/exchange_certification.py` | clean | **clean** | No |
| `backend_app/routers/support.py` | clean | **clean** | No |
| `backend_app/routers/exchange.py` | `MM` | `MM` | No |
| `pytest.ini` | ` M` | ` M` | No |
| `tests/conftest.py` | ` M` | ` M` | No |

The last three carry pre-existing modifications from other workstreams, so status alone
cannot prove I left them alone. A marker scan for `assert_exchange_routable`,
`ExchangeRoutingBlocked`, `SAFETY_BLOCKED_EXCHANGES` and `exch01` across all six files
returned **no matches**. The first three show no modification at all.

EXCH-02 through EXCH-12, sandbox certification, reconciliation, fee parsing, WebSocket
fills and order idempotency were **not** bundled in.

---

## 7. Remaining risks and blockers

| | |
|---|---|
| **Bitget is still named in two inert modules** | `reconciliation_worker.py:293` (module never started) and `exchange_rate_limiter.py` (zero importers). Neither grants routing permission. Out of scope by instruction; recorded for the owners. |
| **The block is a withholding, not a repair** | EXCH-01's underlying defect is unfixed: `CCXTExchangeExecutor.connect` still logs `"SANDBOX mode"` for any venue whose sandbox flag does nothing. The generic fix — assert after construction that the client actually left production — would close the whole class rather than one venue. Recommended, not applied; it is a change to shared connect logic and belongs with its owner. |
| **Four of six declared order types remain non-unified** | `stop_market`, `stop_limit`, `take_profit_*` are not CCXT unified types. Unrelated to this fix, tracked separately. |
| **No venue is trading-certified** | `SANDBOX_VERIFIED`, `TRADING_CERTIFIED` and `LIVE_TRADING_AUTHORIZED` are all 0. No sandbox credential exists. Unchanged by this work. |
| **Coordination still open** | The capability registry and this fix now agree, but `routers/exchange.py::/supported` still advertises every CCXT venue (EXCH-10). Owned elsewhere. |

No production-readiness claim is made. No trading authorization is granted. Live trading
remains OFF, and `registry.tradable()` returns an empty list.

---

## 8. Status

```
FIX VERIFIED
```

Bitget is removed from `ExchangeExecutorFactory.SUPPORTED_EXCHANGES`, refused by
`assert_exchange_routable` at both the factory and the base constructor, absent from
`asset_universe`, and reported blocked by a capability registry that now reads its block
list from the executor rather than declaring a second one. All four consumers agree. The
six approved routing venues are unchanged and nothing was added. 22 focused tests and 194
regression tests pass with zero failures and zero collection errors.
