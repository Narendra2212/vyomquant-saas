# PHASE 11B — NEXT FIXES

**Stage 4 outcome.** No code change was made in this workstream, and that is the
recommendation rather than an omission — see §1.
**Date:** 2026-10-10
**Live trading:** OFF. No venue contacted. No credential. No order. Nothing committed.

Companions: [`PHASE11B_EXCHANGE_PREREQUISITES.md`](PHASE11B_EXCHANGE_PREREQUISITES.md) ·
[`PHASE11B_SANDBOX_SCENARIO_MATRIX.md`](PHASE11B_SANDBOX_SCENARIO_MATRIX.md)

---

## 1. Why no code changed in this pass

Stage 4 authorises the smallest correction **for a defect established by code evidence**.
This workstream established exactly one new fact — that OKX `load_markets` remains broken
on the pinned CCXT — and its fix is a dependency upgrade, which is explicitly out of scope
without approval.

The remaining items below were established in earlier phases and carry owners. The brief
also forbids broad changes to the executor, router, reconciliation service or dependency
versions in one pass. Making any of them here would have breached that.

Two files **were** fixed during this workstream, **by their owners, not by me**: EXCH-04
(`exchange_certification.py`) and EXCH-05 (`routers/support.py`). Both now pass their
reproductions. See the matrix §5.

---

## 2. Ranked next fixes

Ordered by what unblocks sandbox certification soonest per unit of risk.

### P1 · OKX `load_markets` — CCXT upgrade
| | |
|---|---|
| Evidence | Reproduced **offline** this workstream: `okx.parse_market` on a pre-open SPOT instrument (`baseCcy`/`quoteCcy` empty) raises `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`. Corroborated by the Phase 10F live measurement (`LIBRARY_DEFECT`) |
| Files | `requirements-base.txt:95` (`ccxt[async]==4.3.92` → `4.5.85`) |
| Ownership | **Dependency triage.** Out of scope here; requires approval |
| Expected behaviour | OKX `load_markets()` returns its market universe (measured at **4,501 markets** on 4.5.85 — `staging/ccxt_upgrade_delta.json`). Also recovers `gemini` |
| Blast radius | **Large.** Touches every venue path. Needs the full regression run, and `SANDBOX_MECHANISMS` must be **re-measured** with `staging/probe_exch01_sandbox_endpoints.py` because a venue's sandbox mechanism could change |
| Focused tests | `staging/probe_exch01_sandbox_endpoints.py`; `tests/test_exch01_sandbox_endpoint_verification.py`; `tests/exchange_sandbox/test_sandbox_routing_contract.py`; then `scripts/verify_exchange_connectivity.py` |
| Fail-closed preserved? | Yes — the mechanism table refuses unmeasured venues, so a venue whose mechanism changed would be refused rather than silently mis-routed |
| Note | Also corrects a stale in-tree claim: `connection_engine.py:196-250` says the OKX fix is absent from 4.4.1 and 4.4.50. It **is present** in 4.5.85 |

### P1 · EXCH-07 — restart recovery never re-queries open orders
| | |
|---|---|
| Evidence | `master_executor._recover_live_state` re-queries **balance only**; `fetch_open_orders` is never called on any restart path. No account-level venue sweep exists |
| Files | `backend_app/backend/master_executor.py` |
| Ownership | execution/recovery owner |
| Expected behaviour | On restart, re-query open orders per connected venue and reconcile against `execution_records` before the strategy loop resumes |
| Why it matters | Scenario 10 certifies that the venue *makes* recovery possible. Nothing performs it, so a resting order the platform has lost track of stays invisible |
| Focused tests | extend `tests/test_trade07_stale_execution_recovery_postgres.py`; add a mocked restart test asserting `fetch_open_orders` is called per venue |
| Fail-closed preserved? | Must be. A recovery sweep must not resubmit or cancel — `OrderWatchdog` already sets that precedent and never resubmits |

### P1 · EXCH-09 — reconciliation escalates from a table no detector writes
| | |
|---|---|
| Evidence | `core/reconciliation_scheduler.py` is started (`main.py:456`) and polls `reconciliation_mismatches` every 30 s. Of three detectors: `reconciliation_worker.py` header says `NOTHING STARTS IT`; `exchange_reconciliation.py` is referenced only through a class name absent from its module; `distributed_execution/exchange_reconciliation_engine.py` has every data source stubbed with hardcoded `["tenant_1","tenant_2"]` |
| Files | `backend_app/backend/reconciliation_worker.py`, `backend_app/core/reconciliation_scheduler.py` |
| Ownership | reconciliation owner |
| Expected behaviour | One detector actually runs and writes mismatches; the scheduler's escalation then has input |
| Open product question, not a code question | `reconciliation_worker.py`'s own header states correction is deliberately unimplemented: which side is authoritative, whether a correction may itself place or cancel an order, and what becomes of PnL already reported. **Needs an owner decision, not a default** |
| Focused tests | `tests/test_p10d_exchange_reconciliation_fails_safely.py`; `tests/test_exchange_reconciliation_startup_assessment.py` |
| Fail-closed preserved? | Detection only. Any auto-correction that could place or cancel an order must be separately authorised |

### P2 · Scenario 6b — `OrderNotFound` has no classification branch
| | |
|---|---|
| Evidence | `_handle_ccxt_error` substring ladder has no `OrderNotFound` branch; a venue's "unknown order" message falls through. A caller cannot distinguish "already gone" from "venue unreachable, retry" |
| Files | `backend_app/backend/exchange_executor.py` (`_handle_ccxt_error`) |
| Ownership | **venue-boundary owner — coordinate.** This is shared order-path code and another workstream holds +1188/−55 on this file |
| Expected behaviour | Add a terminal, non-retryable `ORDER_NOT_FOUND` class, keyed on ccxt's typed `OrderNotFound` first and wording second — the pattern `scripts/verify_exchange_connectivity.py::classify` already uses |
| Smallest form | One branch plus one error class. Does **not** require touching `place_order` |
| Focused tests | extend `tests/test_exch01_sandbox_endpoint_verification.py` with a classification case; scenario 6b then asserts rather than characterises |
| Fail-closed preserved? | Yes — the new class is non-retryable |

### P2 · EXCH-08 — venue-reported fees are discarded
| | |
|---|---|
| Evidence | `TransactionalExecutionManager.handle_fill` has no fee field; the WS listener extracts `fee.cost`/`fee.currency` into `FillEvent` then drops both at `:344-351`; PnL uses a flat `fee_rate=0.001` for every venue and both sides. The well-built `fee_engine` has no production caller |
| Files | `backend_app/backend/transactional_execution_manager.py`, `backend_app/backend/exchange_websocket_listener.py`, `backend_app/backend/fee_engine.py` |
| Ownership | **ledger/accounting owner — must not be touched by this workstream** |
| Focused tests | `tests/test_fee_denomination_is_not_assumed.py`, `tests/test_fee01_tenant_is_validated_not_sentinel_postgres.py` |

### P2 · EXCH-12 — the WS fill listener is never started, and its reconnect logic is defective
| | |
|---|---|
| Evidence | `get_exchange_websocket_listener` has no callers; `main.py` does not reference it. Four independent defects: fixed 1 s retry with no backoff/jitter/cap; `symbols` argument accepted and unused so no explicit resubscribe set; a `get_or_create_exchange` failure permanently exits the task; `start_listening` never sets `_running`. It also calls `get_or_create_exchange` with **no credentials** for `watch_orders`, a private channel |
| Files | `backend_app/backend/exchange_websocket_listener.py` |
| Ownership | execution/streaming owner |
| Note | Starting it **would activate** the scenario 5b duplicate-fill hazard (§3). Fix the accumulation first |

### P3 · Per-exchange rate limits are configured but unused
| | |
|---|---|
| Evidence | `core/exchange_rate_limiter.py` (binance 100/s … kraken 1/s) has **zero importers**; the live limiter is `RateLimiter(requests_per_second=5.0)`, flat for every venue. `core/exchange_rate_limit_engine.py` holds the only 429 adaptive throttle and is also unimported. No `Retry-After` parsing on the live path |
| Files | `backend_app/backend/exchange_executor.py`, `backend_app/core/exchange_rate_limiter.py` |
| Ownership | venue-boundary owner — coordinate |
| Latent bug if wired as-is | `ExchangeRateLimiter.get_wait_time` delegates to `bucket.get_wait_time()`, which exists on the deprecated `TokenBucket` but **not** on `AsyncTokenBucket` — it would raise `AttributeError` |

---

## 3. Known hazard to fix before enabling WebSocket fills

Recorded here because the ordering matters and getting it wrong duplicates fills.

`exchange_websocket_listener._handle_fill_event` (`:296-360`): when `trades` is empty but
`lastTradeTimestamp` is set, it synthesises **one** trade whose `amount` is the
**cumulative** `filled`, with `trade_id` set to the order id. A second partial-fill event
re-posts the entire cumulative quantity as a new fill, deduplicated only by that reused id.

Separately, `_update_order_in_db` (`:373-400`) **assigns** `filled_size = str(event.filled)`
rather than accumulating. That is correct only if ccxt's `filled` is order-cumulative on
every venue — **unverified on binance, bybit and okx**, and scenario 5b is the test that
would settle it.

Both are latent only because the listener is never started. **Fix the accumulation and
the synthesised-trade fallback before EXCH-12.**

---

## 4. Not recommended in this pass

| | Why |
|---|---|
| Changing the exchange allowlist | Out of scope and forbidden. Still 6; bitget blocked |
| Unblocking bitget | Forbidden. The upstream CCXT behaviour is unchanged |
| Upgrading CCXT | Requires approval; large blast radius. Proposed as P1 above |
| Installing Redis or applying migrations | Requires approval. Reported as BLOCKED instead |
| Editing the authoritative launch registry | Explicitly excluded from this workstream |
| Editing `connection_engine.py` or `routers/exchange.py` | Workstream C owns them |
| Relaxing or skipping the bitget contract test | It is an accurate upstream characterisation |

---

## 5. Sequencing recommendation

**To reach SANDBOX ORDER FLOW VERIFIED on binance and bybit** — the shortest credible path:

1. Provision an **isolated test Redis** (unblocks the most for the least risk; without it
   every order is correctly refused).
2. Obtain and supply **testnet credentials** for binance and bybit under
   `AERORA_SANDBOX_<VENUE>_*`. Fund both accounts.
3. Confirm the test database schema at the required migration head with
   `AERORA_TEST_DATABASE_URL` set deliberately.
4. Run **scenario 8 first** — `clientOrderId` echo-back and duplicate refusal, per venue.
   It is the only check on the platform's sole protection against a retry becoming a
   second real order, and CCXT does not normalise that field. A pass on Binance says
   nothing about Bybit.
5. Run scenarios 1–4, 5a, 6a–6c, 7, 9b, 10–12. Record 5b and 9a as missing evidence with
   their stated reasons.
6. Only then consider EXCH-07 and EXCH-09, which the scenarios will have characterised
   with real venue data.

**OKX additionally requires the P1 CCXT upgrade** and cannot be certified before it.

---

## 6. Classification

| Venue | Highest state attained | Next blocking item |
|---|---|---|
| **binance** | **OFFLINE MECHANISM VERIFIED** | isolated Redis, then credential |
| **bybit** | **OFFLINE MECHANISM VERIFIED** | isolated Redis, then credential |
| **okx** | **OFFLINE MECHANISM VERIFIED** | CCXT upgrade (P1), then Redis + credential |

```
TRADING LIVE — OFF / NOT CERTIFIED
```

No venue is certified. No paper-trading or live-trading GO decision follows from this
workstream.
