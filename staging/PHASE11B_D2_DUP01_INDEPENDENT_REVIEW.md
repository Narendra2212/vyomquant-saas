# PHASE 11B-D2 — INDEPENDENT DUP-01 REVIEW AND RETRY-REGRESSION ASSESSMENT

**Date:** 2026-10-10
**Live trading:** OFF / NOT CERTIFIED. No venue contacted, no credential, no order, no
CCXT change, no migration, no listener started, nothing committed or pushed.
**Code changed: NONE.** Decision is **B — propose test-only changes**, not implemented
here, per the brief.

---

## HEADLINE — THE PRIOR CONCLUSION WAS WRONG

Phase 11B-D concluded:

> "there is no live path that resubmits a logical order to a venue"

**That is incorrect.** A live retry path exists and is reachable:

```
ExecutionRecordRepository.check_idempotent_execution
    CASE 3  status == FAILED  and allow_failed_retry  ->  resets row to PENDING,
                                                          returns "allow_retry"
    CASE 4  status == PENDING                         ->  returns "allow_retry"

execution_engine.execute_with_idempotency
    branches on: skip_return_result | skip_already_running | skip_failed_no_retry
    *** NO BRANCH FOR "allow_retry" ***  -> falls through to STEP 3
    -> claim_execution succeeds (row is PENDING again)
    -> _execute_trade_internal -> place_order -> create_order   ** SECOND SUBMISSION **
```

and `execution_engine.py:892` passes **`allow_failed_retry=True`** explicitly.

`check_idempotent_execution` **never consults `submitted_at`** — the one field TRADE-07
introduced to record "this order reached the venue." So the retry decision is made
without looking at the evidence of submission.

**DUP-01 is therefore not defence-in-depth. It is the last line of defence on a
reachable duplicate-order path.**

---

## 1. Fresh baseline and ownership

| | |
|---|---|
| Branch / HEAD | `main` @ `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772a…` — **0 ahead / 0 behind** |
| Staged / unstaged / total | **22 / 100 / 254** |
| CCXT pinned / installed | `4.3.92` / `4.3.92` |
| Reset / stash / clean / revert / stage / commit | **none** |

**The tree is identical to the Phase 11B-D baseline** (same HEAD, same 22/100/254). So the
reversal below is **not** caused by a tree change — it is a defect in the prior analysis.

### 1.1 Path corrections to the brief

Four paths in the task description do not exist as written:

| Brief | Actual |
|---|---|
| `backend_app/core/exchange_executor.py` | `backend_app/backend/exchange_executor.py` |
| `backend_app/core/transactional_execution_manager.py` | `backend_app/backend/transactional_execution_manager.py` |
| `backend_app/core/signal_service.py` | `backend_app/backend/signal_service.py` |
| `backend_app/core/order_watchdog.py` | `backend_app/backend/order_watchdog.py` |

`backend_app/core/execution_engine.py` is correct as given. The watchdog module was
discovered by inspection, as the brief allows.

### 1.2 Ownership of inspected files (all unchanged by this workstream)

| File | Diff vs HEAD |
|---|---|
| `backend_app/core/execution_engine.py` | +911 / −28 |
| `backend_app/core/models/execution_record.py` | +721 / −5 |
| `backend_app/backend/exchange_executor.py` | +1188 / −55 |
| `backend_app/backend/exchange_websocket_listener.py` | +235 / −33 |
| `backend_app/backend/transactional_execution_manager.py` | +67 / −2 |
| Test files in scope | untracked (new in earlier phases) |

---

## 2. Live venue-write call-site inventory

Independently re-derived.

| Call site | Classification |
|---|---|
| `execution_engine.py:1490` → `exchange_executor.place_order` | **LIVE VENUE WRITE** — the only one |
| `exchange_executor.py:1645` → `self._exchange.create_order` | **LIVE VENUE WRITE** — reached only from `place_order` |
| `exchange_executor.py:1711` → `cancel_order` | live, but a cancel, not a submission |
| `exchange_executor.py:2059` (`place_order_and_wait_confirmation`) | **DEAD** — no live callers |
| `circuit_breaker.py:83`, `:215` | **DEAD** — docstring examples (`place_order(...)` literal) |
| `exchange_simulator.py:33` | **SIMULATED** |
| `paper_trading.py:183` | **PAPER** |
| `distributed_execution/idempotent_exchange_submission.py` | **DEAD** — returns `RISK_EVALUATION_REQUIRED` before the engine |
| `distributed_execution/retry_deduplication_manager.py` | **DEAD** — no live caller |
| `OrderWatchdog` / recovery / reconciliation | **RECOVERY ONLY** — never submits (verified, §3.3) |

---

## 3. Retry and recovery path analysis

### 3.1 The confirmed unsafe sequence

| Step | What happens | Evidence |
|---|---|---|
| 1 | Tick N: `mark_submitted` writes `submitted_at` **before** the venue call | `execution_engine.py:1455-1470` |
| 2 | `create_order` is sent; the response is lost (timeout) | `:1645` |
| 3 | `_handle_ccxt_error` classifies it `NetworkError`; `place_order` returns `status="rejected"` — **an ambiguous outcome recorded as a determinate failure** | `exchange_executor.py` failure branch |
| 4 | The engine writes **`ExecutionStatus.FAILED`** | `execution_engine.py:1091`, `:1119` |
| 5 | Tick N+1, same 5-minute bucket, same side/qty/price → **same `execution_id`** | `generate_execution_id`, `execution_interval_minutes=5` |
| 6 | `check_idempotent_execution` CASE 3: FAILED + `allow_failed_retry=True` → **resets the row to PENDING**, returns `allow_retry` | `execution_record.py:412-438` |
| 7 | The engine has **no `allow_retry` branch** → falls through to the claim | measured: branches are only `skip_return_result`, `skip_already_running`, `skip_failed_no_retry` |
| 8 | `claim_execution` succeeds — the row is PENDING again | `:950` |
| 9 | `create_order` is sent **a second time** | — |
| 10 | binance/bybit/okx/kucoin: the same `clientOrderId` is forwarded, so the venue *may* refuse — **UNVERIFIED on every venue**. **coinbase/kraken: our id never reaches the venue, so nothing can refuse it** | §4 |

**On coinbase and kraken, step 10 is a second real order.**

### 3.2 A second, related gap: `UNKNOWN` is unhandled

`check_idempotent_execution` explicitly handles `COMPLETED`, `EXECUTING`, `FAILED`,
`PENDING`. **`UNKNOWN` is not among them** — and `UNKNOWN` is precisely the status
`OrderWatchdog` assigns to an order that **may be live at the venue**.

Behaviour for an existing `UNKNOWN` row is **undefined**: it matches no case, falls past
CASE 4, and reaches the insert path, whose conflict fallback (`:520-531`) also handles
only COMPLETED/EXECUTING/PENDING/FAILED and otherwise `raise e`. So it most likely raises
rather than resubmitting — **accidentally safer, but not a designed refusal and not
tested.** Classified **UNKNOWN PENDING FURTHER EVIDENCE**; I am not claiming either way.

### 3.3 What genuinely does hold

Re-verified, not carried over:

| Protection | Status |
|---|---|
| `claim_execution` atomicity | **HOLDS** — conditional `UPDATE … WHERE upper(status::text)='PENDING' RETURNING *`, rowcount-guarded. Two concurrent workers cannot both claim |
| `mark_submitted` durable before the venue call, with refusal if unrecordable | **HOLDS** |
| `OrderWatchdog` never resubmits | **HOLDS** — proven structurally at `test_trade07_…:812-849` ("there is no such call to reach") |
| `signal_service` Req 19.2 / 19.3 refuse resubmission under a used key | **HOLDS** — ~20 sites |
| ccxt does not auto-retry | **HOLDS** — no retry logic in async `fetch()`, no `maxRetriesOnFailure` |
| No `@retry` / `tenacity` / `@backoff` on the exchange path | **HOLDS** |

**The gap is specifically in the engine's own idempotency state machine**, between two
separate invocations of `execute_with_idempotency` driven by the outer
`BotRunner._inner_loop`. Every protection above is real; none covers this.

### 3.4 Why Phase 11B-D missed it — method failure, not a tree change

My prior method was an AST walk of `execute_with_idempotency` and `_execute_trade_internal`
for `For`/`While`/`AsyncFor` nodes and submit-call counts. It returned
`loops=[] internal_submit_calls=1` and I treated that as sufficient.

**It was blind by construction.** The retry is not a loop inside either function. It is a
**durable state reset across two separate invocations**, with the loop living one level up
in `_inner_loop`. A within-function loop check cannot see that, and I did not follow
`allow_retry` to its handler — had I done so I would have found there isn't one.

I also over-weighted the `signal_service` and `OrderWatchdog` "never resubmits"
guarantees. Both are genuine and both are about *other* paths.

### 3.5 Reachability

| Condition | Assessment |
|---|---|
| Live trading enabled | **OFF today** — so not reachable in production right now |
| Identical side / qty / price across ticks | Stable for a limit order at a strategy-computed price, or when `price` is passed as `None`/0 for a market order. Varies if the live price is folded in |
| Within the same 5-minute bucket | Plausible for any sub-5-minute tick interval |
| Ambiguous first attempt | A timeout, which is the ordinary failure mode under load |

Reachable in principle and dependent on strategy and tick configuration, not on anything
exotic. **Not a theoretical risk.**

---

## 4. Venue mapping — re-checked on the pinned version

| Venue | Platform `execution_id` reaches the adapter | CCXT forwards to the native field | ID reused or regenerated | Venue-side duplicate **rejection observed** |
|---|---|---|---|---|
| binance | Yes | **Yes** → `newClientOrderId` | reused (ours); ccxt invents a random one if we send none | **NOT OBSERVED** |
| bybit | Yes | **Yes** → `orderLinkId` | reused | **NOT OBSERVED** |
| okx | Yes | **Yes** → `clOrdId` | reused | **NOT OBSERVED** |
| kucoin | Yes | **Yes** → `clientOid` | reused | **NOT OBSERVED** |
| **coinbase** | Yes | **NO** — ccxt sets `client_order_id = id + '-' + self.uuid()` | **regenerated per attempt** | **NOT OBSERVED** |
| **kraken** | Yes | **NO** — no `create*`/`edit*` method mentions `clientOrderId` or `userref` | not sent at all | **NOT OBSERVED** |

**Source inspection is not proof of venue-side duplicate rejection**, and none is claimed.
**Scenario 8 remains UNVERIFIED for all six venues.** No sandbox evidence exists anywhere
in the repository.

---

## 5. Regression-test coverage matrix

| Phase 2 question | Covered? | Evidence |
|---|---|---|
| 1. Do tests assert the absence of a retry **today**? | **NO** for the engine path | **Zero** tests reference `allow_retry`, `allow_failed_retry` or `skip_failed_no_retry` |
| 2. Would they detect a retry loop added to an existing live execution function? | **Partly** — `test_trade07_…:812-849` is structural but scoped to the **recovery** path, not the engine |
| 3. Would they detect a new retry helper or an additional live venue-write call site? | **NO** — no test inventories venue-write call sites |
| 4. Does ambiguous-submission recovery remain fail-closed? | **YES** — TRADE-07 structural + behavioural tests |
| 5. Do the atomic claim and durable `submitted_at` invariants hold? | **YES** — `test_trade01_concurrent_exposure_postgres.py`, `test_trade07_…` |

### 5.1 False-confidence risks identified

| Risk | Why it misleads |
|---|---|
| **`test_idempotent_submission_fails_closed.py`** | Tests `distributed_execution/idempotent_exchange_submission.py`, which has **no live callers**. Reads as proof that submission fails closed; the live engine path is a different module |
| **TRADE-07 structural no-resubmit test** | The strongest assertion in this area, and scoped to recovery. Easy to read as covering all resubmission |
| **`test_dup01_client_order_id_mapping.py`** | Source-level only. States this in its own header, but a summary reader may take it as idempotency coverage |
| **`place_order_mock.call_count == 1` assertions** | All single-invocation, within one `execute_with_idempotency` call. None drives two sequential invocations across a FAILED state |
| **My own Phase 11B-D AST check** | `loops=[]` is true and irrelevant to a cross-invocation state reset |

Existing assertions were **not weakened** and nothing was skipped.

---

## 6. Baseline test results

```powershell
python -m pytest tests/test_dup01_client_order_id_mapping.py `
  tests/test_idempotent_submission_fails_closed.py `
  tests/test_execution_paths_that_claim_an_exchange.py `
  tests/test_order_normalization_and_venue_idempotency.py `
  tests/test_fill01_partial_fill_accounting.py `
  tests/test_single_execution_pipeline.py `
  -q --no-header --tb=short -rsx --timeout=300
```

`EXIT=0` · **366.0 s** · **105 passed · 0 failed · 0 skipped · 0 xfailed · 0 xpassed ·
0 collection errors** — [`p11bd2_baseline.txt`](p11bd2_baseline.txt)

Identical to Phase 11B-D (105 passed, 481 s then). **The suite is green and the defect is
present — which is the regression gap, demonstrated rather than argued.**

Environment: Windows, `.venv` Python 3.12.10, pytest 9.1.1, ccxt 4.3.92, no Redis, no
`DATABASE_URL`, `VYOMQUANT_MODE=safe`. PostgreSQL-gated suites excluded — they would skip,
and a skip is not a pass.

---

## 7. Differences from Phase 11B-D

| Item | 11B-D | 11B-D2 |
|---|---|---|
| Live retry path exists? | "No" | **YES — confirmed** |
| DUP-01 severity | "seventh layer, defence in depth" | **last line of defence on a reachable path** |
| Verdict | REJECT | **B — propose test-only changes** |
| `submitted_at` consulted on retry? | not examined | **No** — confirmed gap |
| `UNKNOWN` status handling | not examined | **Unhandled; undefined** |
| `allow_retry` handler | not examined | **Does not exist** — falls through to submit |
| Tree state | 22/100/254 | **identical** |
| Claim atomicity, ccxt no-retry, watchdog no-resubmit | holds | **holds — re-verified** |

The 11B-D correction of the *original* Phase 11B-C framing was itself partly wrong. The
11B-C statement that `clientOrderId` is the primary protection against a retry becoming a
second order was **closer to correct** than my 11B-D rebuttal. The platform layers are
real, but none of them covers the FAILED→retry path.

---

## 8. Decision

```
B. PROPOSE A NARROWLY SCOPED TEST-ONLY CHANGE — not implemented in this task.
```

A production fix is **also** required but is explicitly unauthorized here ("No new retry
guard… no order-submission behavior change"). It is escalated in §10.

### 8.1 Proposed tests — specification only

**T1 — `allow_retry` must not reach a second venue submission after a recorded submission**
- *File:* new, `tests/test_dup02_failed_retry_does_not_resubmit.py`
- *Setup:* in-memory/SQLite `ExecutionRecordModel` with `status=FAILED` and
  `submitted_at` **set**; a fake executor counting `place_order` calls; the real
  `execute_with_idempotency`.
- *Failure condition:* fails if `place_order` is invoked when `submitted_at` is already
  set — i.e. fails **today**, by design, until a guard exists.
- *Not covered by:* anything. Zero tests reference `allow_retry`.

**T2 — `allow_retry` has an explicit handler**
- *Assertion:* every action `check_idempotent_execution` can return has a corresponding
  branch in `execute_with_idempotency`.
- *Failure condition:* fails today — `allow_retry` has no branch and reaches the claim by
  fallthrough.
- *Value:* catches any future return value added without a handler.

**T3 — venue-write call-site inventory is pinned**
- *Assertion:* the set of live `create_order` / `place_order` call sites equals a pinned
  allowlist.
- *Failure condition:* fails when a new live venue-write site or retry helper appears.
- *Addresses:* Phase 2 question 3, currently uncovered.

**T4 — `UNKNOWN` has a defined, fail-closed outcome**
- *Assertion:* `check_idempotent_execution` on an `UNKNOWN` row neither returns
  `allow_retry` nor permits submission.
- *Failure condition:* fails if it ever resolves to a submission.
- *Note:* current behaviour is undefined; this test would **define** it. Needs the
  production decision in §10 first.

**T5 — a timeout is not recorded as a determinate failure**
- *Assertion:* an ambiguous venue outcome does not produce `ExecutionStatus.FAILED`.
- *Failure condition:* fails today.
- *Depends on* the §10 decision, since the correct terminal state is a product choice.

T1–T3 are implementable without any production change. T4 and T5 encode a decision that
has not been made.

---

## 9. Residual risks and evidence limitations

| Item | Status |
|---|---|
| **FAILED → `allow_retry` → second submission** | **CONFIRMED, reachable.** Unauthorized to fix here |
| **PENDING → `allow_retry`** | **CONFIRMED** — same fallthrough; a stale PENDING row with `submitted_at` set is equally exposed |
| **`UNKNOWN` unhandled** | **UNDEFINED** — likely raises; not designed, not tested |
| Venue-side duplicate rejection | **UNVERIFIED on all six venues.** Scenario 8 outstanding |
| coinbase / kraken have no venue-side id | **CONFIRMED** — pinned by test |
| kraken `userref` is a 32-bit int | Any mapping needs a lossy derivation — design decision |
| DUP-01 verdicts are CCXT-version-specific | Re-measure on any upgrade |
| Live trading OFF | The only reason this is not currently exploitable |
| Ledger dedup on `fill_id` | Separate owner; not established |

**DUP-01 is NOT closed.** Source-level tests passing does not close it.

---

## 10. Prioritized outstanding verification work

1. **[P0 — needs authorization] Decide the terminal state for an ambiguous submission.**
   Should a timeout write `FAILED` (retryable) or `UNKNOWN` (not)? `signal_service` already
   models this correctly with DETERMINATE / INDETERMINATE classification; the engine does
   not. This is the root cause, and it is a product decision about what a lost response
   means. Owner: execution-engine owner.
2. **[P0 — needs authorization] Make the retry consult `submitted_at`.** The narrowest
   plausible fix: refuse `allow_retry` when `submitted_at` is set, and route the row to the
   watchdog's adjudication instead. Preserves first-submission routing entirely, changes no
   allowlist. **Requires its own proposal.**
3. **[P1] Define `UNKNOWN` handling** in `check_idempotent_execution`, fail-closed.
4. **[P1] Implement T1–T3** (no production change needed).
5. **[P2] Run scenario 8** when a sandbox credential exists — the only way to establish
   venue-side rejection, and the only thing that would make the four translating venues
   genuinely safe against this path.
6. **[P2] Re-measure DUP-01** after any CCXT upgrade.
7. **[P3] Correct the stale comment** in `exchange_executor.py` claiming a ccxt internal
   retry can duplicate an order — measured false on 4.3.92.

---

## 11. Boundaries observed

No new retry guard. No new retry mechanism. No routing or allowlist change. No CCXT
upgrade. No credentials, sandbox orders or production calls. No migrations. No commit,
push, PR or CI trigger. The WebSocket listener was not started. Phase 11A/11 launch-gate
registers and readiness scorecards were not edited.
`PHASE11B_D_DUP01_RETRY_SAFETY_BASELINE.md` was **not overwritten** — this document
supersedes its conclusion and says so.

Files added: this report and [`p11bd2_baseline.txt`](p11bd2_baseline.txt). **No source
file was modified.**

```
TRADING LIVE — OFF / NOT CERTIFIED
```
