# Phase 11B-A14 — D5′ Consolidation, Production-Path Quota Verification and Safe CI Repair

**Outcome: the consolidated candidate is fully prepared and locally verified. It was NOT
committed, pushed, or run in CI, because this task grants no explicit authorization for remote
mutation** — Phase 1 directs me to "finish the analysis, prepare the proposed patch and stop
before remote mutation" in exactly that case.

**The canonical PR remains UNDETERMINED and needs your decision.** This task recommends #6; the
owner of the webhook keyspace fix targets #7 (see
`staging/PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md`); #7 and #8 are tree-identical duplicates.

QUOTA-01 OPEN. Live trading OFF / NOT CERTIFIED. No launch-readiness or gate-register edits.

---

## 1. Phase 0 — Before-state inventory (all re-verified, nothing trusted from history)

| Item | Value | vs `PHASE11B_A13_D5_REPAIR_REPORT.md` |
|---|---|---|
| branch / HEAD | `main` / `074b772a` | unchanged |
| `refs/heads/main` = `origin/main` | `074b772a` | unchanged |
| D5′ branch local = remote | `d07014d3` | unchanged |
| staged entries | 22 | unchanged |
| **staged-content fingerprint** (`git ls-files -s`) | `CF474F1B83FD37173F0AD3F2B5417D07CE4B26DC66A5CF4D6697AE9ACB568767` | **identical** |

### 1a. The three open D5′ PRs

| PR | Branch | Head | Tree | State | Mergeable | Review | Latest `01-pr-check` run |
|---|---|---|---|---|---|---|---|
| #6 | `ci/d5-real-redis-quota-evidence` | `d07014d3` | `d28b6d1a…` | OPEN | MERGEABLE / **UNSTABLE** | none | `38047483583` — **failure** |
| #7 | `ci/d5-prime-real-redis` | `49e70da2` | **`271f13c2…`** | OPEN | MERGEABLE / **UNSTABLE** | none | `38044138718` — **failure** |
| #8 | `ci/d5-real-redis-switch` | `a624ce89` | **`271f13c2…`** | OPEN | MERGEABLE / **UNSTABLE** | none | `38046625040` — **failure** |

Tree-identity of #7 and #8 re-verified by `rev-parse ^{tree}`. **All three fail CI.**

### 1b. New evidence this phase — PR #7's run independently reproduces the diagnosis

A13 only examined PR #6's run. I extracted PR #7's `Unit & Integration Tests` job
(`114190594445`, run `38044138718`). It has **no diagnostic job** and a *different* webhook
variant (416 lines, with the `skip`→`fail` strengthening, no keyspace probe), yet the per-test
outcome is identical:

| File | PR #6 run `38047483583` | PR #7 run `38044138718` |
|---|---|---|
| `test_quota01_exchange_slot_is_atomic.py` | 1 passed / **21 FAILED** | 1 passed / **21 FAILED** |
| `test_quota01_metered_reservation_is_hard.py` | **7 passed** | **7 passed** |
| `test_webhook_duplicate_delivery_real_redis.py` | 4 passed / **2 FAILED** | 4 passed / **2 FAILED** |

Same lone slot-lock survivor (`test_retrying_a_refused_request_does_not_eventually_succeed`) and
the same two webhook failures (`test_the_lock_is_recorded_in_redis_under_the_documented_key`,
`test_an_in_flight_delivery_blocks_a_concurrent_one`). This confirms both causes are
PR-independent: the slot-lock failures are the absent implementation, and the webhook failures
are the keyspace split — which #7's `skip`→`fail` change does not address. #8 is tree-identical
to #7, so its run follows by construction.

### 1c. Ownership and cross-workstream reports

Concurrent workstreams have their own reports; phase labels collide, so filenames are used:

| Report | Owner | Bearing on A14 |
|---|---|---|
| `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` | webhook workstream | Owns `tests/test_webhook_duplicate_delivery_real_redis.py`; implemented F2+F3; **targets PR #7**; took no remote action |
| `PHASE11B_A13_REAL_REDIS_VERIFICATION.md` | concurrent | Records gate `state=run` evidenced; canonical PR **UNDETERMINED**; real-Redis acceptance **FAILED** |
| `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` | concurrent | Finding IDs F1–F5 (below) |
| `PHASE11B_A13_D5_REPAIR_REPORT.md` | mine | A13 baseline this phase builds on |

Authoritative finding IDs from A9, which I adopt rather than renaming:

| ID | Finding | File | Owner | Status |
|---|---|---|---|---|
| F1 | Ship the slot-guard with its tests, or hold the two real-Redis tests | `subscription_dependencies.py` | other workstream | **coordination required** |
| F2 | Derive the webhook client from `get_redis_url(REDIS_DB_CACHE)` | webhook test | webhook workstream | **done (worktree)** |
| F3 | Fixture-time bidirectional keyspace proof | webhook test | webhook workstream | **done (worktree)** |
| F4 | Honour an explicit DB in `REDIS_URL`; read at call time not import time | `backend/redis_manager.py` | **unowned** | separate, medium risk |
| F5 | Webhook idempotency must distinguish "new" from "cache unavailable" | `billing.py` + cache | other workstreams | separate, **high risk (billing)** |

### 1d. Implementation-file state (re-verified)

| File | Git state | Staged | Unstaged |
|---|---|---|---|
| `backend_app/backend/redis_manager.py` | clean | — | — |
| `backend_app/core/subscription_engine.py` | clean | — | — |
| `backend_app/core/subscription_dependencies.py` | ` M` | — | +253 / −0 |
| `backend_app/routers/exchange.py` | **`MM`** | **+268 / −15** (other workstream) | +154 / −14 |
| `backend_app/core/cache/redis_manager.py` | ` M` | — | +185 / −5 (other workstream) |

## 2. Phase 1 — Canonical PR: UNDETERMINED (your decision required)

| Option | For | Against |
|---|---|---|
| **#6** (this task's recommendation) | Sole carrier of the `d5-redis-diagnostics` job — the only mechanism that produced usable per-test tracebacks and JUnit artifacts; already has the `/0`-ready workflow lineage | Oldest webhook variant (lacks both the probe and the `skip`→`fail` change) |
| **#7** | Target of the webhook fix's owner; carries the `skip`→`fail` strengthening | No diagnostic job; still lacks the probe; duplicated by #8 |
| **#8** | — | **Byte-identical to #7**; redundant by construction |

I did **not** close, edit, push, or re-target any PR. Recommendation stands as the task states —
**#6**, because the diagnostic job is what makes failures diagnosable — but adopting #6 requires
the webhook owner's agreement since it carries their file.

## 3. Phase 2 — Consolidated candidate (PREPARED, NOT COMMITTED)

The worktree already holds the consolidated content for the test files (the webhook probe from
its owner; the production-path quota tests from A13). The only piece that did not exist anywhere
was the workflow with **both** gate URLs aligned, which I built and validated this phase.

### Proposed commit (parent `d07014d3`, if #6 is canonical)

| Path | Proposed blob | Currently in PR #6 | Change |
|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | **`a2f39ebb`** | `57e2cee8` | corrected comment block (5→37 lines) + **both** `AERORA_TEST_REDIS_URL` → `/0` |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | **`3ffbccde`** | `105b6567` | adopt the owner's F2+F3 probe version unchanged |
| `tests/test_quota01_metered_reservation_is_hard.py` | **`7baff0b6`** | `f48103ac` | +266 lines: 3 helpers + 2 production-path tests |
| `tests/real_redis_gate.py` | unchanged | — | none |
| `tests/test_quota01_exchange_slot_is_atomic.py` | unchanged | — | none — **blocked, see §4** |

The webhook blob `3ffbccde…` matches the hash recorded in the owner's own report, confirming I
am proposing **their** file unmodified.

### Workflow candidate validation

```
env assignments rewritten      : 2 (expected 2)
env lines still /15            : 0
comment keeps historical /15   : True
YAML parses                    : OK
has d5-redis-diagnostics       : True
has provisioning-and-isolation : False      <-- other workstream's hunk correctly EXCLUDED
unit-tests AERORA              : redis://localhost:6379/0
diagnostic AERORA              : redis://localhost:6379/0
diag continue-on-error         : True
pre-existing jobs MODIFIED     : ['unit-tests', 'd5-redis-diagnostics']   (only the two env values)
```

**An error I made and corrected.** My first build used a global string replace of
`redis://localhost:6379/15`. It hit **5** occurrences — 2 env values and 3 inside the
explanatory comment — and corrupted the defect narrative: the comment must keep quoting
`redis://localhost:6379/15?db=0` to explain *why* the database resolved to 0. Inspecting the
output caught it; the builder now uses a **line-targeted** regex on the `AERORA_TEST_REDIS_URL:`
assignments only, and the historical `/15` references are preserved (asserted above).

The `provisioning-and-isolation` job is byte-untouched and stays out of the commit — it is not
present in PR #6's workflow and was not added.

## 4. Phase 3 — Exchange-slot boundary: BLOCKED (re-verified independently)

```
                 guard def   store_keys refs
HEAD                  0            0
PR #6 (d07014d3)      0            0
PR #7 (49e70da2)      0            0
INDEX                 —            0
WORKTREE              1            3
```

And the entanglement, re-measured this phase rather than cited:

```
git apply --cached --recount --check <unstaged exchange.py patch>   (private index = HEAD)
  error: patch failed: backend_app/routers/exchange.py:366
  error: backend_app/routers/exchange.py: patch does not apply      exit 1
staged layer: 268 / 15   (tenant-isolation / RLS / credential-vault workstream)
```

**No action taken.** I did not copy the implementation, rewrite `exchange.py`, or remove
`tests/test_quota01_exchange_slot_is_atomic.py` — all three require owner approval that does not
exist. This is A9's **F1**.

**Consequence that must not be glossed:** committing the candidate in §3 will **not** turn CI
green. The 21 slot-lock failures persist by construction until F1 lands. The candidate is
expected to fix the 2 webhook failures and add 2 production-path quota tests; it cannot fix F1.

## 5. Phase 4 — Verification evidence

Redis genuinely reachable locally: **NO** (`127.0.0.1:6379` closed; nothing installed).

| # | Command | Exit | Passed | Failed | **Skipped** | Redis real? |
|---|---|---|---|---|---|---|
| 1 | `yaml.safe_load` + job/env assertions on the candidate | 0 | — | — | — | n/a |
| 2 | `pytest --collect-only` metered file | 0 | 9 collected | — | — | n/a |
| 3 | `pytest` 3 D5′ files, opt-in **unset** | 0 | **26** | 0 | **11** | **no** |
| 4 | `pytest` metered file, `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/0` (configured, unreachable) | 0 | 6 | **3** | **0** | configured, dead |
| 5 | `pytest` ×5 adjacent regressions (quota wiring, hard-enforcer, redis-fallback lock, entitlement matrix, rate-limit backend) | 0 | **84** | 0 | 0 | n/a |
| 6 | slot-lock file as evidence | — | **NOT RUN** | — | — | — |

**Step 4 is the important one.** It proves my two new production-path tests honour the gate
contract: with Redis configured but unreachable they **FAIL with zero skips**, alongside the
pre-existing real-Redis test. A broken service cannot hide behind a green run.

**Step 3's 11 skips, counted separately from passes** (9 in A13 + my 2 new):
2 slot real-Redis, 3 metered real-Redis (1 pre-existing + 2 new), 6 webhook. Every one is the
legitimate opt-in-unset opt-out. **A skip is not a pass**, and the two new production-path tests
are therefore **UNEXECUTED** against real Redis.

**Step 6, explicitly:** the slot-lock tests pass in this worktree only because the worktree
carries the uncommitted implementation. Worktree-only code is not evidence for a PR commit —
that conflation is what misled `staging/PHASE11B_A6_D5_COMMIT_AND_CI_RESULT.md`.

### Individual real-Redis results actually obtained (from CI, not local)

| Test | PR #6 run `38047483583` | PR #7 run `38044138718` |
|---|---|---|
| gate `state=run` | **YES** (artifact `gate_state.txt`) | not instrumented |
| `test_concurrent_reservations_are_atomic_against_real_redis` | PASS | PASS |
| `test_the_slot_lock_is_mutually_exclusive_against_real_redis` | **FAIL** (`AttributeError: … _slot_lock_key`) | **FAIL** |
| `test_the_guard_admits_exactly_the_limit_against_real_redis` | **FAIL** (same) | **FAIL** |
| `test_the_credential_gate_refuses_test_keys` | PASS | PASS |
| `test_a_redelivered_webhook_is_ignored` | PASS | PASS |
| `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **FAIL** (keyspace split) | **FAIL** |
| `test_a_different_event_id_is_not_deduplicated` | PASS | PASS |
| `test_an_in_flight_delivery_blocks_a_concurrent_one` | **FAIL** (keyspace split) | **FAIL** |
| `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | PASS — **vacuously** (asserts absence in a DB the app never writes) | PASS — vacuously |

Per the instruction not to read a job's status as success: the `d5-redis-diagnostics` job
reported `success` **only** because of `continue-on-error`. The authoritative numbers come from
its JUnit artifacts — slot `22 tests / 21 failures`, metered `7 / 0`, webhook `6 / 2`.

## 6. Preservation proof

| Check | Result |
|---|---|
| staged-content fingerprint, before → after | `CF474F1B…` → **`CF474F1B…`** (identical) |
| staged entries | 22 → 22 |
| `HEAD` / `refs/heads/main` | `074b772a`, unmoved |
| D5′ branch local / remote | `d07014d3` / `d07014d3`, **unmoved** |
| Tracked files modified **by A14** | **none** — the candidate workflow was built to a temp path, not the worktree |
| `provisioning-and-isolation` hunk | untouched; excluded from the candidate |
| Concurrent webhook file | **not edited** (blob `3ffbccde…` unchanged) |
| `git add` / commit / push / PR change / CI trigger | **none** |
| Helper scripts | removed |

## 7. Unresolved owner decisions (blocking)

1. **Canonical PR.** #6 vs #7; close the #7/#8 duplicate. Needs the webhook owner's agreement
   because #6 would carry their file. *(This task did not authorize closing anything.)*
2. **F1 — slot-guard.** Ship `subscription_dependencies.py` + the `exchange.py` call site, or
   hold `tests/test_quota01_exchange_slot_is_atomic.py` out of D5′. `exchange.py` is entangled
   with the credential-vault workstream's staged 268/15, so only that owner can land it.
3. **Authorization to commit/push the §3 candidate** and await CI.
4. **F4** (`get_redis_url` ignoring an explicit DB, captured at import) — unowned. Until then the
   `/0` alignment and the derived-URL probe are the correct compensations, not a real fix.
5. **F5** (webhook idempotency fail-open on a cache read fault) — billing-critical, separate.

## 8. QUOTA-01 disposition

**OPEN**, and real-Redis acceptance is **FAILED rather than merely unverified** — the tests ran
against a reachable Redis (`state=run`) and 23 of them failed.

| Claim | Status |
|---|---|
| D5′ opt-in reaches CI; gate executes rather than skipping | **PROVEN** (run `38047483583`, `state=run`) |
| Configured-but-unreachable Redis fails, never skips | **PROVEN** locally (step 4: 3 failed, 0 skipped) |
| Redis INCR/DECR atomicity | proven — a Redis property, **not** application evidence |
| Production `reserve_quota` against real Redis | **test prepared, UNEXECUTED** — no evidence yet |
| Exchange-slot concurrency + release safety | **NO EVIDENCE** — implementation in no commit (F1) |
| Webhook idempotency against real Redis | 4 of 6 proven; 2 fail on the keyspace split; fix prepared but **UNEXECUTED**; 1 of the 4 passes **vacuously** until the probe lands |
| order-rate / daily-trade / active-order ceilings | **OPEN** — not implemented |
| Slot-lock release non-atomicity residual | **OPEN**, documented |

Live trading remains **OFF / NOT CERTIFIED**.
