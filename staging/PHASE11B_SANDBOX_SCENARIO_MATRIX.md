# PHASE 11B — SANDBOX SCENARIO MATRIX

**Authoritative definitions:** `tests/exchange_sandbox/test_sandbox_order_lifecycle.py`
(16 tests covering the 12 scenarios) and
`tests/exchange_sandbox/test_sandbox_routing_contract.py` (11 offline safe-alternative
tests).
**Date:** 2026-10-10
**Live trading:** OFF. No venue contacted. **No scenario was executed** — all 16 live
scenario tests skipped for want of a credential. No execution is claimed.

Prerequisites and blockers: [`PHASE11B_EXCHANGE_PREREQUISITES.md`](PHASE11B_EXCHANGE_PREREQUISITES.md)

---

## 1. How to read this matrix

| Column | Meaning |
|---|---|
| **Kind** | `unit` (no I/O) · `mock` (substitute venue) · `sandbox` (real venue sandbox) |
| **Applies to** | Of binance / bybit / okx. All three share one generic CCXT executor, so applicability differs only where a venue's sandbox or library support differs |
| **Status** | `SKIPPED — NO CREDENTIAL`, `PASSED (offline)`, `BLOCKED`, or `NOT INDUCIBLE` |

**Every venue-facing scenario is `sandbox` kind and every one is currently skipped.** The
offline tests in §4 are *not* substitutes for them; they answer a different, narrower
question and are labelled as such.

---

## 2. The 12 scenarios

### Scenario 1 — Authentication and account identity
| | |
|---|---|
| Intended behaviour | `connect()` authenticates against the sandbox and loads that venue's real market universe |
| Test | `test_01_authentication_and_account_identity` (:206) |
| Implementation entry | `CCXTExchangeExecutor.connect()` → `fetch_balance()` probe; `ExchangeNormalizer.load_markets()` |
| Kind | **sandbox** |
| Applies to | binance, bybit · **okx BLOCKED** (`load_markets` raises on pinned CCXT) |
| Needs | sandbox credential |
| Evidence to pass | `_connected is True` **and** a non-empty `normalizer._markets` containing the probe symbol. The market-map assertion exists because an empty map silently degrades every downstream rounding to an 8-decimal fallback |
| Failure behaviour | `connect()` raises a classified `AuthenticationError`; sandbox verification failure raises `SandboxVerificationFailed` and the client is closed and dereferenced first |
| Safety invariant | A client that cannot be proven to have left production is never used (EXCH-01) |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 2 — Balance retrieval
| | |
|---|---|
| Intended behaviour | Balances return in the platform's shape, distinguishing zero from absent |
| Test | `test_02_balance_retrieval` (:226) |
| Implementation entry | `CCXTExchangeExecutor.get_balance()` → `fetch_balance()` |
| Kind | **sandbox** · all three (okx blocked upstream) |
| Needs | credential **+ funded sandbox account** |
| Evidence to pass | non-empty map; every row exactly `{free, used, total}`; **all values `str`** (the money path is `Decimal`-based, so floats would be a precision defect) |
| Failure behaviour | `get_balance()` swallows every exception and returns `{}` — so the test asserts non-empty precisely because `{}` cannot be distinguished from an error |
| Safety invariant | An unfunded account and a swallowed failure must not look like success |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 3 — Order placement
| | |
|---|---|
| Intended behaviour | A minimum-size limit order far from market is accepted and returns a venue id |
| Test | `test_03_order_placement_rests_at_the_venue` (:248) |
| Implementation entry | `place_order()` → token check → `require_live_authorization` → `enforce_kill_switch()` → rate limiter → normalization → minimum checks → `create_order` (`:1645`) |
| Kind | **sandbox** |
| Needs | credential **+ reachable Redis** (kill switch fails closed) |
| Evidence to pass | `success`, a non-null `exchange_order_id`, and `status == "pending"` — pinning the deliberate choice that fills are owned by the event/polling path, not by the submission response |
| Failure behaviour | Returns `OrderResult(success=False, status="rejected")`; `ExecutionBlocked` and `LiveExecutionRefused` propagate rather than being converted |
| Safety invariant | Kill switch re-read as the **last** statement before the irreversible write |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 4 — Open-order and order-status retrieval
| | |
|---|---|
| Intended behaviour | The resting order is findable both by venue id **and** by the client order id we assigned |
| Test | `test_04_open_order_and_status_retrieval` (:280) |
| Implementation entry | `get_order_status()`; `find_order_by_client_order_id()` (3 fallback strategies) |
| Kind | **sandbox** |
| Needs | credential, Redis |
| Evidence to pass | lookup by venue id returns `open`/`new`/`pending`; lookup by **our** id returns `status != "not_found"` and `venue_order_id` equal to the id from submission |
| Failure behaviour | A clean miss returns `success=True, status="not_found"` (a definite negative); inability to ask returns `success=False` and the caller must leave the row unresolved |
| Safety invariant | **This is the only recovery handle for an unacknowledged submission**, and `OrderWatchdog._adjudicate_ambiguous_execution` depends on it |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 5a — Full fill
| | |
|---|---|
| Intended behaviour | A minimum-size market order fills and the venue reports filled quantity and average price |
| Test | `test_05a_full_fill_is_reported_with_price_and_quantity` (:320) |
| Kind | **sandbox** — the only scenario that deliberately spends sandbox balance |
| Needs | credential, Redis, funded account |
| Evidence to pass | status `closed`/`filled`, `filled_size > 0`, **`avg_price` not None** — a fill the platform cannot value is unusable |
| Safety invariant | A fill without a price must not be booked |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 5b — Partial fill accumulation ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | A partially-filled order reports **cumulative** filled quantity |
| Test | `test_05b_partial_fill_accumulation` (:343) — contains an explicit `pytest.skip` (:355) |
| Implementation entry | `exchange_websocket_listener._update_order_in_db` (`:373-400`) |
| Kind | **sandbox** |
| Status | **NOT INDUCIBLE** — and this is a real gap, not a formality |
| Why | A partial fill cannot be forced deterministically on a thin sandbox book. It needs either enough resting depth to cross part of a large limit order, or a counter-order from a second sandbox account |
| The premise at risk | `order.filled_size = str(event.filled)` is an **assignment, not an accumulation**. That is correct only if ccxt's `filled` is order-cumulative on every venue. **Unverified on all three.** If any venue reports per-event deltas, fills are silently lost |
| Second hazard | `_handle_fill_event` (`:296-360`): when `trades` is empty but `lastTradeTimestamp` is set, it synthesises **one** trade whose `amount` is the **cumulative** `filled`, with `trade_id` = the order id. A second partial-fill event re-posts the whole cumulative quantity, deduplicated only by that reused id — a **duplicate-fill** path |
| Mitigating today | The WebSocket listener is **never started** (EXCH-12), so fills arrive via `OrderWatchdog` polling. The hazard is latent, not live |
| Required evidence | Two funded sandbox accounts, or a venue with crossable depth. Recorded as missing |

### Scenario 6a — Cancellation
| | |
|---|---|
| Test | `test_06a_cancellation` (:369) · `cancel_order()` → `cancel_order` (`:1711`) |
| Kind | **sandbox** · Needs credential, Redis |
| Evidence to pass | cancel succeeds, then the venue reports `canceled`/`cancelled`/`expired` |
| Safety invariant | `cancel_order` is **deliberately not** kill-switch gated, so an operator can cancel resting orders during a halt |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 6b — Double cancel ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | Cancelling an already-cancelled order must be distinguishable from a transient failure |
| Test | `test_06b_double_cancel_is_distinguishable_from_a_real_failure` (:390) |
| Established defect | `_handle_ccxt_error` has **no `OrderNotFound` branch**, and the venue's "unknown order" wording falls through its substring ladder. A caller cannot tell "already gone, nothing to do" from "venue unreachable, retry" — opposite responses |
| Current test shape | **Characterisation**: asserts `success is False`, `status == "error"`, and that a reason is carried. It records the defect rather than asserting the fix |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 6c — Cancellation race ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | A cancel issued concurrently with a filling market order resolves to exactly one legible outcome |
| Test | `test_06c_cancel_racing_a_fill` (:419) |
| Evidence to pass | The forbidden state is the assertion: the venue must **not** both accept the cancel and report a non-zero `filled` with status `canceled`. Either outcome is acceptable; booking a cancellation over a real position is not |
| Safety invariant | Position accounting must never record a cancel for an order that filled |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 7 — Timeout after a potentially accepted order ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | An order whose response was lost is recoverable by the client order id |
| Test | `test_07_timeout_after_submission_is_recoverable_by_client_order_id` (:456) |
| Implementation entry | `find_order_by_client_order_id`; `OrderWatchdog.recover_unsubmitted_executions` → `_adjudicate_ambiguous_execution` |
| Honest limitation, stated in the test | A real socket timeout cannot be induced against a third party on demand. The test **reproduces the consequence** — the order is sent and the response discarded — not the cause. What is certified is the recovery handle, **not** timeout handling itself |
| Evidence to pass | the venue answers for our id, `status != "not_found"`, and `venue_order_id` matches |
| Failure behaviour | Watchdog resolves to COMPLETED (fill found), FAILED (definite negative), or UNKNOWN (could not ask) — and **never resubmits** |
| Safety invariant | Resubmitting would double a position; failing would orphan live exposure. UNKNOWN is the only honest third option |
| Residual risk | Detection latency up to **15 min** (`RECOVERY_STALE_AFTER_SECONDS = 900`) plus a 30 s tick |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 8 — Duplicate order / client order id uniqueness ⚠ HIGHEST VALUE
| | |
|---|---|
| Intended behaviour | The same `clientOrderId` sent twice must **not** create two orders |
| Test | `test_08_duplicate_client_order_id_is_refused_by_the_venue` (:502) |
| Implementation entry | `extra_params["clientOrderId"] = self._consumed_execution_id` (`:1623`) |
| Kind | **sandbox** — and it is **not substitutable by a mock**, because the property under test belongs to the venue |
| Evidence to pass | two assertions, in order: (1) the venue **echoes our id back** in the submission response — if it does not, uniqueness cannot be relied on at all; (2) a second submission under the same id is **refused** |
| Why this is first in priority | The platform's **only** protection against a retry becoming a second real order is that `execution_id` is forwarded as `clientOrderId` and that the venue enforces uniqueness on it. CCXT does **not** normalise this field — `newClientOrderId` on Binance, `orderLinkId` on Bybit, `clientOrderId` on OKX — and **nothing in this repository verifies the mapping or the echo-back on any venue** |
| Consequence if unverified | Any retry path above this layer may double a position. Two orders from one id is a money-loss defect |
| Venue-specific risk | Must be run **per venue**; a pass on Binance says nothing about Bybit |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 9a — Rate limits ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | A venue throttle is classified **retryable** |
| Test | `test_09a_rate_limit_response_is_classified_as_retryable` (:556) — 40 read-only `fetch_ticker` calls |
| Implementation entry | `_handle_ccxt_error` (substring ladder); live limiter is `RateLimiter(requests_per_second=5.0)` — **flat for every venue** |
| Established gap | The per-exchange rate-limit table (`core/exchange_rate_limiter.py`: binance 100/s … kraken 1/s) has **zero importers**. No HTTP 429 handling and no `Retry-After` parsing exists on the live path; `RateLimitError` hardcodes `retry_after=60` and no caller reads it |
| Evidence to pass | if the venue throttles, at least one error classifies as `RATE_LIMIT` with `retryable=True` |
| Hard to provoke | CCXT's own `enableRateLimit` throttles client-side first, so the test **skips with a named reason** if no throttle occurs — which is itself the recorded gap: the platform's 429 handling has never been observed |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 9b — Transient vs terminal classification
| | |
|---|---|
| Test | `test_09b_invalid_symbol_is_classified_terminal_not_retryable` (:590) |
| Why this half is inducible | A bad symbol is a guaranteed error from any venue and needs no order — the safe half of scenario 9 |
| Evidence to pass | `classified.retryable is False`. Retrying a deterministic rejection burns the rate budget against an error that cannot resolve |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 10 — Reconnect and restart recovery ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | A resting order survives a full disconnect/reconnect and is rediscoverable |
| Test | `test_10_restart_recovery_rediscovers_resting_orders` (:611) — places, tears the client down, builds a **new** executor from the same credential, re-queries |
| Evidence to pass | `fetch_open_orders(symbol)` on the fresh client lists the resting order id |
| What it does **not** prove | `master_executor._recover_live_state` re-queries **balance only** on restart and never calls `fetch_open_orders`. No account-level "what is actually open at the venue" sweep exists anywhere. **This scenario certifies that the venue makes recovery possible; nothing performs it** (EXCH-07) |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 11 — Fee and fill accounting
| | |
|---|---|
| Intended behaviour | The venue reports a fee with a currency on a real fill |
| Test | `test_11_venue_reported_fees_are_present_on_the_fill` (:663) |
| Scope, deliberately narrow | Certifies the **input** only. `TransactionalExecutionManager.handle_fill` has **no fee field**; the WS listener extracts `fee.cost`/`fee.currency` then drops both when building `fill_data` (`:344-351`); what reaches PnL is a flat `fee_rate=0.001`, identical for every venue and both sides. Whether the platform records the fee is **EXCH-08**, which no sandbox can answer |
| Evidence to pass | `fetch_my_trades` returns the trade; `fee.cost` is not None; `fee.currency` is present — an amount without its denomination cannot be valued, which is the third-asset case `fee_engine.record_fee` refuses to guess at |
| Status | **SKIPPED — NO CREDENTIAL** |

### Scenario 12 — Internal vs exchange reconciliation ⚠ SPECIAL ATTENTION
| | |
|---|---|
| Intended behaviour | The reads a reconciler needs are available and mutually consistent |
| Test | `test_12_exchange_state_is_queryable_for_reconciliation` (:708) |
| Scope, deliberately narrow | **Cannot be end-to-end.** `core/reconciliation_scheduler.py` is started (`main.py:456`) but only polls a `reconciliation_mismatches` table and escalates by severity — and **no running detector writes to that table** (EXCH-09). Of the three detectors, one is never started, one is referenced through a class name that does not exist in its module, and one has every data source stubbed |
| Evidence to pass | `fetch_open_orders()` **with no symbol** succeeds — exercised deliberately, because `exchange_reconciliation._fetch_exchange_orders` calls exactly that form (`:219`) and some venues require a symbol; the resting order appears; `get_order_status` agrees; the quote currency appears in the balance map |
| Status | **SKIPPED — NO CREDENTIAL** |

---

## 3. Scenario summary

| # | Scenario | Kind | binance | bybit | okx | Status |
|---|---|---|---|---|---|---|
| 1 | Auth / identity | sandbox | ✔ | ✔ | **blocked** | SKIPPED |
| 2 | Balance | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 3 | Order placement | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 4 | Open order / status | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 5a | Full fill | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 5b | **Partial fill** | sandbox | ⚠ | ⚠ | blocked | **NOT INDUCIBLE** |
| 6a | Cancellation | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 6b | **Double cancel** | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 6c | **Cancel race** | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 7 | **Timeout after submit** | sandbox | ⚠ consequence only | ⚠ | blocked | SKIPPED |
| 8 | **Duplicate client id** | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 9a | **Rate limit** | sandbox | ⚠ hard to provoke | ⚠ | blocked | SKIPPED |
| 9b | Terminal error | sandbox | ✔ | ✔ | blocked | SKIPPED |
| 10 | **Restart recovery** | sandbox | ✔ venue half only | ✔ | blocked | SKIPPED |
| 11 | Fee accounting | sandbox | ✔ input only | ✔ | blocked | SKIPPED |
| 12 | **Reconciliation** | sandbox | ⚠ venue half only | ⚠ | blocked | SKIPPED |

**0 of 12 executed. 0 of 12 certified, on any venue.**

---

## 4. Offline safe-alternative tests — what they do and do not establish

These are `unit`/`mock` kind and **pass**. They answer the layer below the scenarios:
*when "sandbox" is armed, where would an order go?* They are **not** venue certification.

| Test | Establishes |
|---|---|
| `test_the_sandbox_gate_is_explicit` | the opt-in exists, so a skipped run is self-explaining |
| `test_top_level_sandbox_config_key_is_dead_code` | `config["sandbox"]` is inert; `options["sandbox"]` does the work |
| `test_routable_venue_leaves_production_when_sandbox_is_armed` ×3 | binance/bybit swap the base URL; okx adds the header |
| `test_venue_without_a_sandbox_raises_from_the_constructor` ×3 | coinbase/kraken/kucoin raise `NotSupported` |
| `test_certification_registry_sandbox_claims_match_measured_reality` | **now passes** — EXCH-04 fixed concurrently |
| `test_customer_facing_faq_sandbox_claims_match_measured_reality` | **now passes** — EXCH-05 fixed concurrently |
| `test_bitget_sandbox_flag_is_inert_and_orders_reach_production` | **still fails, by design** — upstream CCXT fact |

Plus 28 platform-level tests in `tests/test_exch01_sandbox_endpoint_verification.py` and 22
in `tests/test_exch01_bitget_routing_is_blocked.py`.

---

## 5. Verification runs — exact commands and outcomes

Real exit codes. Offline verification is separated from venue evidence throughout.

### Run 1 — platform endpoint verification + bitget routing (offline)
```powershell
python -m pytest tests/test_exch01_sandbox_endpoint_verification.py `
  tests/test_exch01_bitget_routing_is_blocked.py `
  -q --no-header --tb=short --timeout=120
```
`EXIT=0` · **50 passed · 0 failed · 0 skipped · 0 xfailed · 0 xpassed · 0 collection errors**
— [`p11b_run1.txt`](p11b_run1.txt)

### Run 2 — the full sandbox suite, gate ON
```powershell
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
python -m pytest tests/exchange_sandbox -q --no-header --tb=no -rfs --timeout=180
```
`EXIT=1` · **10 passed · 1 failed · 16 skipped · 0 xfailed · 0 xpassed · 0 collection errors**
— [`p11b_run2.txt`](p11b_run2.txt)

- **16 skipped** = every live scenario test, each naming the exact env vars required and
  stating that a skip is missing evidence, not a pass.
- **1 failed** = `test_bitget_sandbox_flag_is_inert_and_orders_reach_production`, the
  upstream CCXT characterisation. Red by design on 4.3.92.

### Change since the Phase 10 closeout — and it is good news

The contract suite moved from **3 failed / 8 passed** to **1 failed / 10 passed**. Two
reproductions turned green because their owners fixed the underlying findings while this
workstream was running:

| Finding | Fix observed | Measured effect |
|---|---|---|
| **EXCH-04** | `exchange_certification.py`: `coinbase` and `kucoin` → `sandbox=False`, with `known_limitations` naming the measured `NotSupported` | registry test **passes** |
| **EXCH-05** | `routers/support.py`: FAQ now lists only Binance, Bybit, OKX | FAQ test **passes** |

Both fixes cite `tests/exchange_sandbox/test_sandbox_routing_contract.py` and
`staging/EXCHANGE_SANDBOX_CERTIFICATION.md`. Neither file was modified by this workstream.

This is also direct confirmation of the Workstream J documentation correction: the two
findings that *were* in this repository's gift went green, and the bitget one — which is
upstream — did not, exactly as recorded.

---

## 6. Classification

| Venue | Highest state attained | Scenario execution |
|---|---|---|
| **binance** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — 0/12 run |
| **bybit** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — 0/12 run |
| **okx** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — 0/12 run, plus `load_markets` broken |

```
TRADING LIVE — OFF / NOT CERTIFIED
```

No venue is certified. No paper-trading or live-trading GO decision follows from this
workstream.
