# PHASE 11B-D — DUP-01 RETRY SAFETY: BASELINE, DESIGN, AND VERDICT

**Date:** 2026-10-10
**Live trading:** OFF / NOT CERTIFIED. No venue contacted, no credential, no order, no
CCXT change, no listener started, nothing committed or pushed.
**Code changed by this workstream: NONE.** The reason is the finding — §4.

---

## VERDICT

```
REJECT — the proposed retry guard is NOT justified by current code.
         The Phase 11B-C report is CORRECTED with evidence below.
```

DUP-01's *mapping* gap is real and confirmed. The *unsafe-retry* scenario it was raised
against is **not reachable in the current code**: there is no live path that resubmits a
logical order to a venue. Implementing Option A would guard a path that does not exist,
and the brief's own instruction is to trace the retry path before assuming Option A is
safe. It was traced. It is not there.

---

## 1. Baseline and ownership

| | |
|---|---|
| Branch / HEAD | `main` @ `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Upstream | `origin/main`, no divergence |
| Staged / unstaged / total | **22 / 100 / 254** |
| CCXT pinned and installed | `4.3.92` / `4.3.92` — match |
| Reset / stash / clean / revert / commit / push | **none** |

Concurrent artifacts observed and **not edited**: `PHASE11A_READINESS_SCORECARD.json`,
`PHASE11A_LAUNCH_GATE_REGISTER.md`, `PHASE11A_SANDBOX_CERTIFICATION_REPORT.md`,
`PHASE11A_NEXT_ACTIONS.md`, `PHASE11_LIVE_TRADING_READINESS_AUDIT.md`,
`PHASE11_LAUNCH_BLOCKER_REGISTER.md`, `PHASE11_READINESS_EVIDENCE.json`.

Ownership was establishable; nothing was edited, so no hunk conflict arose.

---

## 2. Retry-flow trace — the decisive evidence

A single order, from intent to every possible second submission.

```
BotRunner._inner_loop                          (master_executor.py:385)
  └─ execution_engine.execute_with_idempotency (:656-1131)   loops: NONE
       ├─ repo.check_idempotent_execution      (:882)  → skip_already_running / claim
       ├─ repo.claim_execution                 (:950)  ATOMIC conditional UPDATE
       │    UPDATE execution_records SET status='EXECUTING'
       │    WHERE execution_id=:id AND tenant_id=:t AND upper(status::text)='PENDING'
       │    RETURNING *                        → one worker only, guarded by rowcount
       └─ _execute_trade_internal              (:1133-1648) loops: NONE
            ├─ repo.mark_submitted             (:1455-1470) DURABLE, BEFORE the venue call
            ├─ require_live_authorization      (exchange_executor.py:1449)
            ├─ enforce_kill_switch             (:1506)
            └─ place_order → create_order      (:1645)   ← THE ONLY LIVE VENUE WRITE
                 on ANY exception → OrderResult(success=False); NO retry

           ambiguous outcome (submitted_at set, order_id NULL)
             └─ OrderWatchdog.recover_unsubmitted_executions
                  └─ _adjudicate_ambiguous_execution → find_order_by_client_order_id
                       filled      → COMPLETED + CRITICAL alert
                       not_found   → FAILED
                       unreachable → UNKNOWN, left unresolved
                       ** NEVER RESUBMITS **
```

### 2.1 Measured facts

| # | Fact | How established |
|---|---|---|
| 1 | **One** live venue submission call site | `execution_engine.py:1490`. The other `place_order` hits are docstrings (`circuit_breaker.py:83,215`), the simulator, the paper service, and `place_order_and_wait_confirmation` (no live callers) |
| 2 | **Zero loops** in `execute_with_idempotency` and `_execute_trade_internal`; exactly **one** submit call each | AST walk for `For`/`While`/`AsyncFor` and submit-call counting |
| 3 | **ccxt does not auto-retry.** `ccxt.async_support` `fetch()` contains no retry-like line; no `maxRetriesOnFailure` attribute | `inspect.getsource` + attribute probe on 4.3.92 |
| 4 | No `@retry` / `tenacity` / `@backoff` decorator anywhere on the exchange path | repo-wide search |
| 5 | Submission evidence is **durable before** the venue call | `mark_submitted` at `:1455-1470`, with an explicit refusal if it cannot be recorded |
| 6 | The claim is **atomic in the database** | conditional `UPDATE ... RETURNING` guarded by rowcount; ORM fallback is also a single rowcount-guarded UPDATE |
| 7 | Recovery **never resubmits**, proven **structurally** | `test_trade07_stale_execution_recovery_postgres.py:812-849` — "there is no such call to reach… proves no path can, including ones no test exercised" |
| 8 | `signal_service` forbids resubmission under a used idempotency key (Req 19.2 / 19.3) | ~20 distinct code sites |

**Fact 3 also corrects an earlier claim of mine.** The Workstream E note said "a ccxt
internal retry… becomes a second REAL order." That premise is false for 4.3.92 — ccxt has
no retry. The comment in `exchange_executor.py` stating it is stale; flagged, not edited
(shared file, and it is a comment, not behaviour).

### 2.2 Who owns the retry decision, and what evidence it holds

**Nobody retries.** The only component that *could* is `OrderWatchdog`, and at that point
it holds: `submitted_at`, a NULL `order_id`, the deterministic `execution_id`, and the
venue's answer to `find_order_by_client_order_id`. It uses that to **classify**, never to
resubmit — UNKNOWN is its third outcome precisely because resubmitting would double a
position and failing would orphan live exposure.

---

## 3. Venue-specific contract — DUP-01 re-verified

Re-measured against the currently installed CCXT, not carried over.

| Venue | Native field | Our `execution_id` arrives? | Mechanism |
|---|---|---|---|
| binance | `newClientOrderId` | **Yes** | `safe_string_2(params,'newClientOrderId','clientOrderId')` → `request[var]` |
| bybit | `orderLinkId` | **Yes** | translated |
| okx | `clOrdId` | **Yes** | translated |
| kucoin | `clientOid` | **Yes** | translated |
| **coinbase** | `client_order_id` | **NO** | ccxt builds `id + '-' + self.uuid()`, discarding ours |
| **kraken** | `userref` | **NO** | no `create*`/`edit*` method mentions either |

Classification the brief asks for, kept distinct:

| Distinction | Status |
|---|---|
| Platform-generated ID reused for the same logical order | **Yes** — `execution_id` is deterministic from (tenant, strategy, symbol, time bucket, side, qty, price) |
| New ID per submission attempt | **No** on the platform side. **Yes** on coinbase (random uuid) and on binance *only when* we supply none (`brokerId + uuid22()`) |
| Venue-native ID accepted by the adapter | **Yes** for binance/bybit/okx/kucoin; **No** for coinbase/kraken |
| Actual venue-side duplicate **rejection** | **UNKNOWN for all six.** Not establishable by source inspection. Requires scenario 8. **Not claimed.** |

### 3.1 Separate protection that already exists

The brief asks for this explicitly, and it is the crux:

| Layer | Prevents |
|---|---|
| Deterministic `execution_id` | A regenerated signal produces the same key, not a new one |
| `check_idempotent_execution` | Returns `skip_already_running` for an EXECUTING row |
| `claim_execution` — atomic DB UPDATE | Two concurrent workers cannot both claim |
| `mark_submitted` before the venue call | A crash mid-submission is classifiable, not ambiguous-forever |
| `OrderWatchdog` | Classifies and alerts; **never** resubmits |
| `signal_service` Req 19.2/19.3 | Refuses to resubmit under a used key |
| DB `uq_signals_idempotency_key` | Database-level uniqueness |

**The venue-side client order id is the *seventh* layer, not the first.**

---

## 4. Correction to the Phase 11B-C report

That report stated:

> "the platform's **only** protection against a retry becoming a second real order is
> that `execution_id` is forwarded as `clientOrderId` and that the venue enforces
> uniqueness on it"

**That was wrong, and it overstated the risk.** The primary protection is platform-side
and multi-layered (§3.1). The venue-side id is defence in depth. The corrected statement:

> On coinbase and kraken the platform has **no venue-side** duplicate protection, so the
> seventh layer is absent there. The first six still hold, and no live path exercises a
> retry, so no reachable duplicate-order scenario follows from the gap today.

The mapping finding itself stands unchanged and re-verified. Only its *severity framing*
was wrong. `PHASE11B_C_ORDER_INTEGRITY_REPORT.md` is historical evidence and was **not
edited**; this section is the correction of record.

---

## 5. Baseline test results — before any decision

```powershell
python -m pytest tests/test_dup01_client_order_id_mapping.py `
  tests/test_idempotent_submission_fails_closed.py `
  tests/test_execution_paths_that_claim_an_exchange.py `
  tests/test_order_normalization_and_venue_idempotency.py `
  tests/test_fill01_partial_fill_accounting.py `
  tests/test_single_execution_pipeline.py `
  -q --no-header --tb=short -rsx --timeout=300
```

`EXIT=0` · duration **481.4 s** · **105 passed · 0 failed · 0 skipped · 0 xfailed ·
0 xpassed · 0 collection errors** — [`p11bd_baseline.txt`](p11bd_baseline.txt)

Environment: Windows, `.venv` Python 3.12.10, pytest 9.1.1, ccxt 4.3.92, no Redis, no
`DATABASE_URL`, `VYOMQUANT_MODE=safe`. No pre-existing failure inherited. PostgreSQL-gated
recovery suites were **not** included — they would skip without
`AERORA_TEST_DATABASE_URL`, and a skip is not a pass.

---

## 6. Design comparison

### Option A — refuse retries when venue-side idempotency is unestablished

| | |
|---|---|
| Safety gained | **None reachable.** §2 establishes there is no live retry to refuse |
| Failure mode | A guard with no live caller. It would read as protection in review while guarding nothing — the same class of defect as the dead `exchange_rate_limiter` module and the advisory-only `SAFETY_BLOCKED` that Workstream G had to fix |
| First submissions | unchanged |
| Timeouts / ambiguous | unchanged — already terminal, already never resubmitted |
| Durable state required | none |
| Watchdog compatibility | compatible but redundant |
| Verdict | **Rejected — unjustified.** Cannot be implemented without inventing a retry path to intercept |

### Option B — block all submissions to coinbase and kraken

| | |
|---|---|
| Safety gained | Removes the venues, hence the gap |
| Failure mode | Changes routing for venues with **no demonstrated reachable defect**. Over-refusal is a trading outage |
| Boundary | **Explicitly forbidden** — "Do not change… venue allowlist" without separate authorization |
| Verdict | **Rejected — out of scope and disproportionate** |

### Option C — implement an explicit venue-specific mapping

| | |
|---|---|
| Feasibility | **coinbase: no.** ccxt sets `client_order_id` itself inside `create_order`; our params value is discarded. Overriding needs patching pinned-dependency internals. **kraken: `userref` is a 32-bit signed integer**, so a 64-char `execution_id` cannot be carried — it would need a lossy hash, and a truncated hash weakens the uniqueness it exists to provide |
| Failure mode | Inventing an id derivation ccxt does not support; silent divergence on a ccxt upgrade |
| Verdict | **Rejected for now.** Correct eventual fix, needs its own task plus sandbox verification. Would also be re-measured by the CCXT upgrade already proposed |

### Option D — document, pin by test, defer

| | |
|---|---|
| Safety gained | The gap cannot regress silently: `test_dup01_client_order_id_mapping.py` fails if a mapping changes in **either** direction, and `test_every_routable_venue_has_a_measured_mapping_verdict` fails if a venue is added without a measured verdict |
| Cost | Zero code change; preserves all routing and gates |
| Verdict | **Already in place from Phase 11B-C.** This is the narrowest design that prevents duplicate orders without changing unrelated routing behaviour, which is what the brief asks to prefer |

**Selected: D (status quo) + this correction.** No new code is justified.

---

## 7. Minimal diff

**Empty.** No source file was modified.

```
staging/PHASE11B_D_DUP01_RETRY_SAFETY_BASELINE.md   new — this report
staging/p11bd_baseline.txt                          new — baseline run log
```

Verified unchanged by this workstream: `exchange_executor.py` (+1188/−55),
`exchange_websocket_listener.py` (+235/−33), `transactional_execution_manager.py`,
`connection_engine.py`, `routers/exchange.py`, `exchange_certification.py`,
`exchange_capability_registry.py`, `requirements-base.txt` (untracked-clean), `pytest.ini`,
`tests/conftest.py`. Routing allowlist still six venues; bitget still blocked;
`registry.tradable()` still empty.

---

## 8. Acceptance criteria, assessed honestly

| Criterion | Status |
|---|---|
| Current code confirms the issue **or** the report is corrected with evidence | **Met via correction** — mapping gap confirmed; retry reachability disproved (§2, §4) |
| Every retry path has an identified, tested safety decision | **Met** — the only candidate is `OrderWatchdog`, whose refusal is proven **structurally** |
| An ambiguous submission cannot silently become an unsafe second order | **Met** — no resubmit call exists to reach; durable `submitted_at` before the venue call; UNKNOWN is a first-class outcome |
| Routing unchanged unless authorized | **Met** — nothing changed |
| Assertions and safety gates intact | **Met** — nothing weakened, no skip or xfail added |
| Focused and regression tests pass | **Met** — 105 passed, 0 failed, 0 skipped |
| Remaining venue-side and ledger-level uncertainties documented | **Met** — §9 |

---

## 9. Residual risks

| Risk | Status |
|---|---|
| **Venue-side duplicate rejection is UNKNOWN on all six venues** | Source inspection cannot establish it. Needs **scenario 8**. Not claimed for any venue, including the four where translation is confirmed |
| coinbase and kraken have **no** venue-side idempotency | Confirmed, pinned by test, accepted as the seventh layer being absent while the first six hold |
| binance invents a random id when we supply none | A wire-level observer would see an id and wrongly infer protection. No live path omits ours, but worth knowing |
| kraken `userref` is a 32-bit int | Any future mapping needs a lossy derivation — a design decision, not a code task |
| Ledger dedup on `fill_id` | Separate owner. Not established here |
| DUP-01 verdicts are **CCXT-version-specific** | Must be re-measured on the proposed 4.5.85 upgrade |
| A future contributor **adds** a retry | The seventh layer would then matter. Mitigated only by the structural TRADE-07 test, which covers recovery — **not** a hypothetical new retry elsewhere. Recorded as the main reason to keep DUP-01 open |
| OKX `load_markets` broken; no Redis; no credentials | Unchanged blockers from Phase 11B |

---

## 10. Recommended next action

1. **Keep DUP-01 open as a documented defence-in-depth gap**, not a launch blocker. Its
   severity framing is corrected here.
2. **Do not implement a retry guard.** Revisit only if a retry path is introduced — at
   which point the guard belongs *with* that path, which will have a clear owner.
3. **Run scenario 8** when credentials exist. It is the only thing that can establish
   venue-side duplicate rejection, on any venue.
4. **Re-measure DUP-01 after the CCXT upgrade** — the verdicts are version-specific.
5. If venue-side idempotency on coinbase/kraken is later required, raise Option C as its
   own task, including the `userref` 32-bit constraint.

```
TRADING LIVE — OFF / NOT CERTIFIED
```

This workstream cannot and does not certify paper trading or authorize live trading. The
Phase 11A readiness scorecard and launch-gate register were not edited.
