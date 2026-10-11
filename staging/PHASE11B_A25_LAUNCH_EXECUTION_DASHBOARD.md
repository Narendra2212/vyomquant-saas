# Phase 11B / A25 — Launch Execution Dashboard

**Date:** 2026-10-11 · **Launch target:** 2026-10-25 · **Scope:** paper/retail only
**Destination verified ABSENT before writing.** No existing artifact was overwritten.
**Companion deliverable:** `staging/PHASE11B_A25_OWNER_MAP.md` (Workstreams A and B)
**Primary planning source:** `staging/PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md`

> **LIVE TRADING: OFF / NOT CERTIFIED.** Nothing in this document is a live-trading
> certification. A paper-only launch is a **separate release decision** and is not described
> here as any form of live certification.

This was a **read-only coordination phase**. No source, test, migration, workflow or
configuration file was modified; no CI was dispatched; no credential, DSN or Redis instance
was accessed. **Nothing here is an approval.**

---

## 1. Evidence classes used in this dashboard

The brief requires these be kept apart, so they are labelled on every claim:

| Tag | Meaning |
|---|---|
| **`SRC`** | Source-derived fact — read from code this phase |
| **`REC`** | Recorded test result from a prior phase — not re-run |
| **`VER`** | Verified this phase by a command I ran |
| **`PROJ`** | Projection or inference — not evidence |

---

## 2. Launch posture — unchanged, and deliberately not revised

The authoritative scorecard `staging/PHASE11A_READINESS_SCORECARD.json` was **not edited**
this phase. Figures below are quoted, not recomputed.

| Track | Readiness | Classification |
|---|---|---|
| **Paper** | **53%** evidence-backed | partial |
| **Live** | **0%** | **TRADING LIVE — OFF / NOT CERTIFIED** |
| **Production** | **0%** | **PRODUCTION — NOT CLASSIFIED** |

Paper-track weight ledger `REC`:

| Bucket | Weight | Criteria |
|---|---|---|
| Fully met | **0.53** | kill_switch_fail_closed · paper_live_isolation · referral_payout_excluded_and_failsafe · fee_and_money_integrity · connection_credential_lifecycle |
| PARTIAL, not counted | **0.22** | `quota_hard_limit` (0.12) · `tenant_isolation_for_paper` (0.10) |
| BLOCKED/OPEN, not counted | **0.25** | `paper_sandbox_e2e_evidence` (0.15, BLOCKED_ON_CREDENTIAL) · `recon_operational_for_paper` (0.10, OPEN) |

**I did not revise the readiness percentage.** Two scorecard *reasons* are stale, recorded
here for the scorecard owner without being changed:

1. `quota_hard_limit` evidence reads *"real-Redis integration UNVERIFIED (no Redis server)"*.
   That reason is **now stale** `VER` — real-Redis integration **was** exercised in CI run
   38061973739 with the gate reporting `state=run` and **0 skips**. The `PARTIAL` *status*
   remains defensible, but for a **different** reason than the one recorded (see §4).
2. `recon_operational_for_paper` demands **venue** reconciliation on the **paper** track.
   §3.4 argues that is a gate-definition problem, not an implementation backlog item.

Correcting either reason is a scorecard-owner action through an authorized process. **Not
done here.**

---

## 3. Workstream C — reconciliation wiring: source-backed recommendation

### 3.1 Recommendation up front

> **DO NOT wire `ExchangeReconciliationService` into `backend_app/main.py`.** Not in this
> phase, and not as the next implementation task either.
>
> `main.py` was **not edited** `VER` (blob `a3849d4acd7afcf4e53054fc85b16c786776eada`,
> unchanged).

Wiring it would be wrong on four independent counts, three of which are already written down
as prerequisites inside an **executable guard**, and the fourth of which I found this phase.

### 3.2 The five required answers

**Q1 — Is the service instantiated anywhere in production code? `SRC` NO.**

- Defined at `backend_app/backend/exchange_reconciliation.py:74`.
- `backend_app/main.py` contains **zero** occurrences of `exchange_reconciliation`.
- `tests/test_exchange_reconciliation_startup_assessment.py:73`
  `test_the_service_is_not_started_anywhere` pins that `start_reconciliation_loop` has **no
  application caller**.
- The one historical call site is now a **20-line comment** at
  `backend_app/backend/exchange_websocket_listener.py:686-709`. `_fallback_poll` used to
  import `ExchangeReconciliationEngine` — a class that **does not exist** (the class is
  `…Service`) — inside a bare `try`, so every invocation raised `ImportError` and was logged
  as a generic "Fallback poll error" that read like a transient venue problem. It was deleted
  deliberately: *"the honest behaviour is to stop pretending a safety net exists."*
- **Do not confuse two components.** `backend_app/backend/distributed_execution/exchange_reconciliation_engine.py`
  defines a different thing — `ReconciliationScheduler`, with `get_exchange_reconciliation_engine()`
  at L757. The startup-assessment test draws the line: the scheduler *"processes mismatches
  that something else recorded; it does not itself talk to any exchange."* The component that
  would **fetch from the venue** is the unstarted one.

**Q2 — Is start/stop idempotent and safe? `SRC` NO, and the stop path is defective.**

This is a **new finding this phase**, not carried from A24.

| Element | Line | Finding |
|---|---|---|
| `self._running = False` | 110 | initial |
| `self._reconciliation_task: Optional[asyncio.Task] = None` | 111 | initialised… |
| `start_reconciliation_loop(self, tenant_id, …)` | 587 | **per-tenant**, not per-app |
| `self._running = True` | 603 | |
| `while self._running:` | 609 | **blocking coroutine — it is not a task spawner** |
| `stop_reconciliation_loop()` | 635 | sets `_running = False` |
| `if self._reconciliation_task and not …done(): .cancel()` | 639-640 | **dead code** |

Three consequences:

- **`_reconciliation_task` is never assigned anywhere.** It is set to `None` at L111 and no
  code path ever writes it. So `stop_reconciliation_loop`'s cancel branch can never execute;
  stop degrades to a flag that takes effect only at the loop's next iteration boundary. **The
  service's declared shutdown mechanism does not work.**
- **Not idempotent.** `start_reconciliation_loop` has no re-entry guard, so awaiting it twice
  runs two concurrent loops over the same rows.
- **It would hang boot.** Because it `await`s a `while` loop rather than creating a task, a
  naive `await service.start_reconciliation_loop(...)` in lifespan never returns. Every other
  service in that lifespan is task-based — the code says so explicitly for
  `AssetUniverseRefresher`: *"`start()` only creates the task, so a slow or unreachable
  exchange delays no boot."* `SRC`

**Q3 — Is leader election needed? `SRC` YES. It is required, and it does not exist.**

- Guard docstring: gunicorn runs **`--workers ${WORKERS:-2}`** with **no leader election**, so
  **N processes would reconcile the same rows concurrently.**
- `backend_app/main.py` contains **no leader-election code** `VER`. Stated precisely, because
  the naive search is a trap: a case-insensitive search for `leader|elect` returns exactly
  **one** hit, and it is the substring `elect` inside the word *"select"* in a prose comment at
  line 265. **Zero** real matches.
- Scorecard `exchange_reconciliation_service_WIRED_AND_RUNNING` = `NOT_STARTED`: *"main.py
  lifespan does not start ExchangeReconciliationService; no leader election"* `REC`.

**Q4 — Is it safe for a paper-only deployment? `SRC` The question is mis-framed, and that is
the finding.**

The service's job is to **fetch orders from a venue** and compare them to internal records.
In a paper-only launch with live trading hard-OFF there is **no venue order flow to
reconcile**. And the service correctly refuses to run without an exchange client — L227-232
raises rather than reporting zero open orders, because *"'I cannot see the exchange' and 'the
exchange is empty' must not be the same value"*. So wiring it into a paper-only deployment
would produce a service that **raises on every cycle**: a correct refusal, but the opposite of
"operational".

**Q5 — Which tests cover failure, shutdown and duplicate workers?**

| Concern | Coverage | Tests |
|---|---|---|
| **Venue-failure fail-safe** | **Covered** `REC` | `tests/test_p10d_exchange_reconciliation_fails_safely.py` (refusal when no exchange client); `tests/test_exchange_reconciliation_startup_assessment.py` (fail-safe on exchange errors); `tests/test_exchange_safety_fix.py` |
| **Shutdown / graceful stop** | **NOT COVERED** — and the mechanism is broken (Q2) | none |
| **Duplicate worker / concurrent reconcilers** | **NOT COVERED** | none |
| **Not-wired state** | **Pinned by two guards** | both files above |

### 3.3 The guard that makes this a hard gate

`tests/test_p10d_exchange_reconciliation_fails_safely.py` →
`test_the_service_is_not_registered_in_the_application_lifespan` `SRC`:

```python
assert "exchange_reconciliation" not in main_src, (
    "backend_app/main.py now references exchange_reconciliation. Starting this "
    "service needs the duplicate-worker, tenant-enumeration and ghost-order-"
    "idempotency questions answered first — see this module's docstring.")
```

**Editing `main.py` to wire this service would turn a currently-passing test red.** The
prerequisites are not informal advice in a report — they are enforced in CI. Its docstring
names all three, and adds the incumbent-service objection:

1. **Duplicate worker** — `--workers 2`, no leader election.
2. **Tenant enumeration** — the loop is per-tenant and *"nothing enumerates tenants"*.
3. **Ghost-order idempotency** — unresolved.
4. **`OrderWatchdog` already runs a reconciliation loop over the same `execution_records`
   rows.**

Add the defect from Q2 and there are **four** unresolved prerequisites, not three.

### 3.4 The decision gate, stated as a gate rather than solved

Per the brief, I am **not** inventing a safe implementation. Two decisions must be made by
people, and they are different people:

| ID | Decision | Who must decide | State |
|---|---|---|---|
| **D-A25-C1** | Does `recon_operational_for_paper` mean **venue** reconciliation, or **internal execution-record** reconciliation? | **Scorecard owner** (the gate's author) | **UNRESOLVED** |
| **D-A25-C2** | If venue reconciliation is required for paper: authorize leader election + tenant enumeration + ghost-order idempotency, and accept overlap with `OrderWatchdog` | **"reconciliation owner"** (role recorded at `EXCHANGE_SANDBOX_CERTIFICATION.md:431`; **person UNIDENTIFIED**) **+ a `main.py` coordinator** (three workstreams contend for that lifespan block) | **UNRESOLVED** |

**`D-A25-C1` should be answered first, and it is free.** `PROJ` If the paper gate means
internal execution-record reconciliation, then `OrderWatchdog` — already wired at
`main.py:432`, `service_status["order_watchdog"] = "ACTIVE"`, interval 30 s / stale 60 s /
alert 300 s `SRC` — is a candidate satisfier already running, and the 0.10 could be closed by
**evidence** rather than by new code. The open watchdog defect **W-1b** (`REC`,
`PHASE11B_D5_WATCHDOG_REPAIR_REPORT.md`) would need closing first. If instead it means venue
reconciliation, then the gate is **not achievable on a paper-only launch at all** and belongs
on the live track — which would itself change the paper denominator, and is therefore
explicitly a scorecard-owner action, not mine.

I am not asserting either reading. I am recording that **a 0.10 launch gate is blocked on a
definition nobody has been asked to confirm.**

---

## 4. Workstream D — quota evidence reconciled into three categories

**The quota implementation was not changed this phase** `VER` (`subscription_engine.py` blob
`256f03994b395d383fc49dd5b87a456ba68d99cb`, unchanged).

### 4.1 The fingerprint comparison that decides everything

Run this phase `VER`:

| Blob of `backend_app/core/subscription_engine.py` | Value |
|---|---|
| at `HEAD` (`074b772a`) | `edc0c9e9c4dac9f8b662a508cfba9fc405cc9dc0` |
| at the **CI commit** `f5b99e57` (what run 38061973739 executed) | `edc0c9e9c4dac9f8b662a508cfba9fc405cc9dc0` — **IDENTICAL to HEAD** |
| in the **worktree** now (the fix) | `256f03994b395d383fc49dd5b87a456ba68d99cb` — **different** |

And `git diff-tree` on the CI commit shows it touched exactly three files `VER`:
`.github/workflows/01-pr-check.yml`, `tests/test_quota01_metered_reservation_is_hard.py`,
`tests/test_webhook_duplicate_delivery_real_redis.py` — **`subscription_engine.py` is not
among them.**

> **Conclusion: every real-Redis quota result on record was produced against the UNFIXED quota
> engine. The refusal-clamp fix has never been executed by any CI run.** It exists only as an
> uncommitted, unstaged worktree modification (` M` +61/−4).

This is the cleanest available answer to "current vs missing", and it was not knowable from
the reports alone.

### 4.2 Category 1 — PROVEN by current evidence

| Claim | Evidence |
|---|---|
| The real-Redis gate **actually executes** rather than silently skipping | `REC` run 38061973739 diagnostics artifact 11673133427 → `gate_state.txt`: `real-Redis gate: state=run (AERORA_TEST_REDIS_URL=set, remote_allowed=False)`; `metered.xml` 9 tests, **0 skipped** |
| The metered quota **hard limit holds** against real Redis | `REC` exactly **10 of 50** concurrent reservations admitted; `get_quota_usage` == 10; raw Redis key == 10 |
| D5′ mechanism (the A18 change) works as intended | `REC` workflow diff exactly +2/−2, `/15`→`/0` at lines 216 and 735; push `d07014d3..f5b99e57` fast-forward, verified host-side |
| The 61 CI failures are **not** a D5′ regression | `REC` node-ID comparison vs baseline run 37346853502: **37 recurred, 22 new (all in D5′ files), 0 gone** |

**Scope limit:** all of this is proven **for engine blob `edc0c9e9` only**.

### 4.3 Category 2 — IMPLEMENTED but UNVERIFIED

| Item | Where it lives | Why unverified |
|---|---|---|
| **Refusal-value clamp** — `settled = min(max(reverted, 0), effective)` | worktree ` M` +61/−4, blob `256f0399` | **Never in any commit, therefore never in any CI run** `VER` §4.1. Addresses the recorded defect `a refusal reported usage 36, outside [0, 10]` (`assert 36 <= 10`, `tests/test_quota01_metered_reservation_is_hard.py:469`) |
| **Fail-open guard** — `if amount > 0 and new_usage < amount: return False, 0, effective` | same | Same reason. Targets **D1b**: the cache proxies return `0` on exception (`core/cache/redis_manager.py` `incrby` L927-935, `decrby` L937-945), so an INCRBY error yields `0 > limit` == False and `reserve_quota` returns `True` — admitting a reservation it never metered. **Investigated, never reproduced.** |
| **Exchange-slot atomicity guard (F1)** | `core/subscription_dependencies.py` ` M` +253/−0 (worktree) + call site in `exchange.py` `MM` | Guard absent from `main`, so **21 of 22** slot-lock tests failed in CI with `AttributeError: … has no attribute '_slot_lock_key'` `REC`. The test and the implementation both exist; neither is committed. `A10:243` records that this file's diff **refuses** `git apply --cached --check` against a HEAD-seeded index and "must not be cherry-picked, reconstructed, or hand-rewritten." |

A documented discrepancy, left uncorrected because it is another session's text: the
concurrent fix's comment cites a refusal value of **"35"**, where the CI artifacts record
**36** `REC`.

### 4.4 Category 3 — MISSING or BLOCKED

| Gap | Blocked on |
|---|---|
| Real-Redis verification of the **fixed** engine | Committing the worktree fix (no owner, no authorization) **or** a local Redis. Port 6379 closed, `docker` not on PATH `REC` |
| **Webhook idempotency** under real Redis | **5 ERRORS**, all in `test_webhook_duplicate_delivery_real_redis.py`, from `Event loop is closed` `REC`. Blocked on the Redis lifecycle defect — whose file is **UNOWNED** (owner map A-1) |
| **F5** — webhook cannot distinguish "new" from "cache unavailable" | **UNOWNED**, billing-critical (owner map A-4) |
| order-rate / daily-trade / active-order ceilings | **OPEN — no gate exists** `REC` |
| Any claim that quota enforcement is correct **as deployed** | `main` carries the unfixed engine; PR #6 is OPEN/UNSTABLE with `reviewDecision: ''` |

---

## 5. Workstream E — prerequisites recorded only

**No Redis investigation was performed or resumed this phase.** Per the brief, prerequisites
only.

`r6_d` **remains strict XFAIL and must stay that way** until the runtime-health recovery scope
is implemented **and verified**. Current state `REC`:
`tests/test_redis_debounce.py` L379-388 `xfail(strict=True)`, raising line **413**;
`xfail_strict` is absent from `pytest.ini`, so `strict=True` is explicit and must not be
removed. File blob `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc`, unchanged `VER`.

| # | Prerequisite | State |
|---|---|---|
| E-1 | **An owner for `backend_app/backend/redis_manager.py`** | **UNIDENTIFIED** — the gating prerequisite. Five sessions have halted here |
| E-2 | `D-A23C-1` (three P1 lifecycle changes), `D-A23C-7` (new lifecycle test file) | **UNRESOLVED** — *"no grantor exists"* |
| E-3 | `D-A23C-8a` — core-cache owner acknowledges the `close()` contract at `core/cache/redis_manager.py:619` | **NOT GRANTED — still required** |
| E-4 | Option B design conflict | **UNRESOLVED.** `PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md:11` holds option B *"cannot satisfy its own stated verification criterion"* and `:152` that it *"would leave the kill switch, audit trail… unprotected"* |
| E-5 | **The third unlocked `initialize()` caller** | **UNRESOLVED** |
| E-6 | A reachable real Redis to verify against | **ABSENT** locally |

> **No concurrency-safety claim is made.** While E-5 is unresolved, the Redis accessor path
> must not be described as concurrency-safe.

**And the strategic point A24 established, which still holds:** the Redis workstream is **not
on the critical path** to the limited SaaS launch. It carries none of the 0.25 blocked weight.
It should not be worked before §6.

---

## 6. Top three next actions

Ordered by weight unblocked per unit of authority required. Each names who must act.

### 1 · Assign an owner for sandbox credential provisioning, then obtain **two** testnet variables for **one** venue
**Unblocks `paper_sandbox_e2e_evidence` — 0.15, the largest single unmet weight.**
Needs no code, no CI, no database, no approval beyond procurement. The harness is complete and
fail-closed; the separation between sandbox and live is established through four independent
layers (owner map §3.1). Recommended first venue **binance** (`testnet.binance.vision`), which
needs exactly `AERORA_SANDBOX_BINANCE_API_KEY` and `AERORA_SANDBOX_BINANCE_SECRET` plus the
gate `AERORA_EXCHANGE_SANDBOX_CERT=1` and selector `AERORA_SANDBOX_VENUE=binance`. The sandbox
account must also be **funded with testnet assets**.
**Blocker:** this area has **no owner and no owner role recorded anywhere** — the only area in
the map in that condition (owner map A-9).

### 2 · Get `D-A25-C1` answered by the scorecard owner
**Potentially unblocks `recon_operational_for_paper` — 0.10 — with no code change.**
One question: does the paper gate mean **venue** reconciliation or **internal
execution-record** reconciliation? It is the **only** route to that 0.10 that does not require
defeating a passing guard test, building leader election, and overlapping an already-running
service. `PROJ` If the answer is "internal", the likely path is evidence from the
already-wired `OrderWatchdog` plus closure of **W-1b**.

### 3 · Grant `AP-W1D-1` (`DB owner, read access`)
**The only decision whose owner role is recorded *and* is not the most-contended role.**
Per W1D11 §4 it is *"the single next action needing nothing from the Code owner"*. It would
establish whether any declared key is actually unique in a live catalogue — verified for
**0 of 16** dimensions today — and is the one lane that can advance immediately. It is **not**
a blocker for `AP-W1D-3`.

**Deliberately not in the top three:** all Redis lifecycle work (§5), the quota clamp commit
(needs an owner and a real-Redis lane), CD-8, and schema drift. None of them carries blocked
paper-track weight.

---

## 7. Approvals and resources still required

### 7.1 Approvals — all withheld, none newly granted

| ID | Scope | Owner | State |
|---|---|---|---|
| `AP-W1D-1` / `-2` / `-4` / `-5` | PostgreSQL verification lane | `DB owner` variants | **NOT GRANTED** |
| `AP-W1D-3` | Classifier implementation + 10 regression tests | `Code owner` | **NOT GRANTED** |
| `D1`–`D5` (W1D) | Classifier semantics, scope, placement, severity | `Code owner` (+ production-audit stakeholder for D5) | **UNRESOLVED — no register entry** |
| `D-A23C-1` / `-7` | Redis lifecycle repair + test file | **UNIDENTIFIED** | **UNRESOLVED — no grantor** |
| `D-A23C-8a` | Core-cache `close()` contract | core-cache owner | **NOT GRANTED** |
| `AP-W1D` option A / core-proxy invalidation | Redis | — | **NOT GRANTED** (`A22V:332`) |
| `D8(a)` / `D8(b)` | CD-8 | — | **ABSENT** (`U9`) |
| `D1`–`D7` (TENANT-01) | Tenant isolation | — | **UNRESOLVED** |
| **`D-A25-C1`** | Meaning of `recon_operational_for_paper` | **Scorecard owner** | **NEW — UNRESOLVED** |
| **`D-A25-C2`** | Authorize reconciliation wiring + prerequisites | reconciliation owner + `main.py` coordinator | **NEW — UNRESOLVED** |

**Total granted: 0.** Three independent repository-wide sweeps across three sessions have
found zero affirmative grants (owner map A-6). `W1D10:360` is a **blockquote grant
template**, not a grant — its own line 368 says *"I am not treating it as a grant."*

### 7.2 Resources

| Resource | State | Gates |
|---|---|---|
| Sandbox testnet credential + funded account | **ABSENT** | 0.15 |
| Local or CI Redis for the fixed engine | **ABSENT** (6379 closed, no docker) | quota/webhook verification |
| Production DSN | **NEVER EXISTED in any phase** | `prod_dsn_verify`, production track |
| PostgreSQL cluster for the 12 PG-gated tests | **ABSENT** (`AP-W1D-5`) | PG lane |
| A named human owner for **any** of the 9 mapped areas | **ABSENT** | effectively everything |

---

## 8. Tests actually run this phase

> **NONE. Zero tests were executed.**

This was a read-only coordination phase. I ran only read-only `git` plumbing
(`rev-parse`, `diff-tree`, `hash-object`, `ls-files`, `log`, `status`, `rev-list`) and
text searches. **No `pytest` invocation, no CI dispatch, no service start.**

Every test figure in this dashboard is tagged **`REC`** — a result recorded in a prior phase,
re-quoted, **not** re-verified. In particular these are **not** current verification:

- `9 passed, 1 xfailed in 6.76s` (A22 R6 suite)
- `61 failed, 12027 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed, 5 errors in 661.56s` (CI run 38061973739)
- `43 passed` (adjacent Redis suites)
- `48 passed` / `103 passed, 12 skipped, 1 xfailed` (W1D drift group)

What I **did** verify this phase `VER`: the three `subscription_engine.py` blobs and the CI
commit's file list (§4.1); `main.py` has no leader-election code (§3.2 Q3, including the
`"select"` substring false positive); no `CODEOWNERS` file exists anywhere, including ignored
paths; `git log --all` has one author identity over 464 commits; the sandbox variable
names and gate constants as written in `tests/exchange_sandbox/harness.py`; and the
preservation set in §9.

---

## 9. Preservation — entry equals exit

All values below re-measured at exit `VER` (`staging/p11b25_preserve.py` →
`staging/p11b25_preserve.txt`).

| Measurement | Entry | Exit | Verdict |
|---|---|---|---|
| branch | `main` | `main` | **SAME** |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | **SAME** |
| `origin/main` | `074b772ab16…` | same | **SAME** |
| ahead / behind | 0 / 0 | 0 / 0 | **SAME** |
| staged | 22 | **22** | **SAME** |
| unstaged | 102 | **102** | **SAME** |
| untracked (collapsed) | 139 | **139** | **SAME** |
| unmerged | 0 | **0** | **SAME** |
| tracked entries | 2104 | **2104** | **SAME** |
| index digest, **method A14** | `7379218b9928…1392d` | identical | **SAME** |
| index digest, **method W1D11 "Digest A"** | `98ae5bdf2af9…2704` | identical | **SAME** |

Blob fingerprints, all unchanged `VER`:

| Path | Blob |
|---|---|
| `tests/test_quota01_exchange_slot_is_atomic.py` | `2d7ccbbb04b3af04675b365809d89b4429db4888` |
| `tests/test_quota01_metered_reservation_is_hard.py` | `7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `3ffbccde68c3b57485a9971d4977c34aff598b47` |
| `tests/real_redis_gate.py` | `d40a1bb7766307f067d914ebc8e6fd705e04a222` |
| `tests/test_redis_debounce.py` | `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc` |
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13ba089b5cbe9d790161176de2` (clean) |
| `backend_app/core/subscription_engine.py` | `256f03994b395d383fc49dd5b87a456ba68d99cb` |
| `backend_app/main.py` | `a3849d4acd7afcf4e53054fc85b16c786776eada` |

### 9.1 A method defect in my own verification, disclosed

My first exit check reported the index digest **CHANGED**. It had not. I had joined the sorted
`git ls-files --stage` lines with newlines, whereas the entry script
(`staging/p11b25_rebase.py` L31-33) **concatenated them with no separator**. Same content,
different serialisation, different hash.

Resolved rather than assumed `VER`: I enumerated ten serialisations
(`staging/p11b25_digest_probe.py`). The entry method reproduces `7379218b…` exactly, **and**
an independent method — W1D11 §7.1's "Digest A", defined by a different session — reproduces
`98ae5bdf…` exactly. **Two independently-defined digests over the same index both reproduce
their recorded values**, which settles that the staged content has not moved.

**The transferable lesson, and it has now cost two sessions:** a bare digest value is **not a
portable invariant**. The serialisation must be recorded with it. `staging/p11b25_preserve.py`
now computes both methods with their serialisations documented inline. This is the same class
of error as A14's original finding that the raw `.git/index` checksum is unusable because it
embeds stat data — and W1D11 §7.1 independently notes the same hazard
(`core.autocrlf=true` lets a read-only `git status` rewrite the stat cache).

### 9.2 Artifacts created — all untracked, all under `staging/`

```
PHASE11B_A25_OWNER_MAP.md                    deliverable 1
PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md   deliverable 2 (this file)
p11b25_rebase.py / .txt                      entry rebaseline
p11b25_entry_status.txt                      entry status snapshot
p11b25_preserve.py / .txt                    exit preservation, two digest methods
p11b25_digest_probe.py / .txt                serialisation resolution (§9.1)
```

No existing report, artifact or other workstream's file was modified or overwritten.

---

## 10. Confirmations

This phase did **not**: edit any source file, test, migration, workflow or shared
configuration; create or modify a test; stage, commit, push, reset, clean, stash, revert or
switch branches; mutate the shared Git index; write to the shared Git object database;
trigger, dispatch or re-run CI; connect to PostgreSQL, Redis, any exchange or any payment
endpoint; access a production DSN; install a package; start a server; make a network call;
read, print, copy or commit any credential value; or overwrite any existing report.

**Credential handling:** variable **names** only. No value was read, printed, logged, copied
into a report, or committed.

| Item | Status |
|---|---|
| **Live trading** | **OFF / NOT CERTIFIED** |
| Paper-only launch | a **separate release decision**, not certified here |
| Paper track | **53%**, unrevised |
| Live / Production tracks | **0% / 0%**, unrevised |
| Approvals granted this phase | **0** |
| Owners identified as individuals | **0 of 9** |
| Tests executed this phase | **0** |
| Scorecard edited | **No** |

Nothing in this document constitutes an approval, and no owner was contacted.
