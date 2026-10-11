# Phase 11B-A24 — Launch Critical-Path Coordination and Blocker Resolution Plan

**Date:** 2026-10-10 · **Target:** launch readiness by 2026-10-25 (15 days)
**Destination check:** `staging/PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md` verified
**ABSENT** before writing.
**Read-only coordination phase.** No production source, test, migration, CI config, or other
workstream's report modified. Nothing staged, committed, pushed. No database, Redis, exchange, or
CI contact.

---

## HEADLINE — three findings that change the plan

### 1. The Redis workstream is not on the critical path to a limited SaaS launch

Twenty Redis reports (~400 KB) have been produced, and five sessions are queued behind one
unmade decision. But the measured launch gates say the paper/retail track is held open by two
entirely different things:

| Paper-track gate | Weight | Status | Owner of the blocker |
|---|---|---|---|
| `paper_sandbox_e2e_evidence` | **0.15** | **BLOCKED_ON_CREDENTIAL** — "no `AERORA_SANDBOX_*` credential; 0 sandbox orders placed" | whoever can supply a venue sandbox credential |
| `recon_operational_for_paper` | **0.10** | **OPEN** — "`ExchangeReconciliationService` not started" in `main.py` lifespan | service/deployment owner |

Those two carry **25 of the 47 percentage points** that are not evidence-backed. Redis lifecycle,
option A, and the schema-drift classifier carry **none of them**. Redis matters for production
reliability and for closing QUOTA-01's remaining evidence, but **it is not what stands between
today and a limited SaaS launch.**

### 2. The blocker is not a missing approval — it is a missing *owner*

Every workstream is stopped at the same kind of gate, and the record is unusually unambiguous
about it. Two independent sessions ran repo-wide grant scans and reached the same result:

> `PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md:56` — "181 affirmative lines, **all false
> positives**" (open-source licence texts)
> `PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md:42-46` — "Every `GRANTED` token in
> `staging/PHASE11B_*.md` is one of exactly two things: the string `NOT GRANTED`, or a *request*
> for a grant… all 181 were false positives"

And the harder half, from the newest Redis report:

> `A23R:20` — "A and B are both **NOT GRANTED**. The target file is recorded as **UNOWNED**, so
> there is not even an identified [owner to ask]."

A missing approval can be chased. A missing owner cannot. **Naming owners is the single
highest-leverage action available**, and it costs no engineering time.

Evidence that this diagnosis is right: the two workstreams that **did** ship are exactly the two
with unambiguous sole ownership — watchdog D5 (`order_watchdog.py` +143/−9, plus 18 new tests) and
QUOTA-01 workstream A (`subscription_engine.py` +61/−4). Work proceeds where ownership is clear
and stalls where it is not.

### 3. One scorecard entry is now stale in our favour, and one sibling report contradicts the Redis plan

- **`quota_hard_limit` is scored PARTIAL because "real-Redis integration UNVERIFIED (no Redis
  server)."** That reason no longer holds: CI run **38061973739** executed the production
  `reserve_quota` against real Redis — exactly **10 of 50** admitted, `get_quota_usage` == 10 and
  the raw meter key == 10, gate `state=run`, zero skips. It should stay PARTIAL, but for a
  **narrower** reason (§2, QUOTA-01 row).
- **`PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` disputes the option-B design**, not just its
  approval: *"the approval being sought is for a change that cannot satisfy its own stated
  verification criterion"* (L11), and B-2′ placed in `get_redis_manager()` *"would leave the kill
  switch, audit trail… unprotected"* (L152) because those paths do not resolve through that
  accessor. This is a **design conflict that must be settled before approval**, not after — §3.

---

## 1. Baseline and preservation

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Index **content digest** | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` (unchanged since A23) |
| Staged / unstaged / untracked | **22 / 102 / 139** |
| Unmerged | 0 |
| Markdown reports in `staging/` + root | **242** |
| Files changed by this phase | **0** (this report only) |

Entry snapshot: `staging/p11b24_entry_status.txt`.

Highest-churn concurrent edits (unstaged), for collision avoidance:

```
1243  backend_app/backend/exchange_executor.py     446  backend_app/backend/order_watchdog.py   <- D5, shipped
 984  backend_app/core/execution_engine.py         434  backend_app/core/config.py
 845  backend_app/core/models/execution_record.py  325  backend_app/main.py
 740  backend_app/routers/billing.py               253  backend_app/core/subscription_dependencies.py
 723  backend_app/core/global_safety.py            225  tests/test_redis_debounce.py            <- A22 R6
 674  backend_app/backend/paper_trading_service.py
```

### Commands executed in this phase

| Command | Purpose | Result |
|---|---|---|
| `git rev-parse HEAD / origin/main / --abbrev-ref HEAD` | baseline | as above |
| `git ls-files --stage`, `git diff --cached/--numstat/--name-only`, `git status --porcelain`, `git ls-files --unmerged` | state | 22 / 102 / 139, 0 unmerged |
| `staging/p11b24_discover.py` | report inventory by topic | 242 docs, tabulated |
| `staging/p11b24_extract.py` | verdict/approval extraction from 17 authoritative reports | 574-line evidence file |
| targeted reads of `PHASE11A_READINESS_SCORECARD.json`, `W1D10:352-368` | launch gates; verify a grant-looking line | see below |

**No test suite was run in this phase**, and no test result is claimed. All test outcomes quoted
below are attributed to the phase that executed them.

**One line required careful handling.** `PHASE11B_W1D10…:360` reads
`> **AP-W1D-3 granted** for scripts/verify_provisioning.py …`. It is inside a blockquote under
"The next action is a human reply granting, in one message:", and L368 adds "I am not treating it
as a grant." It is a **template for a grant, not a grant.** The same report records AP-W1D-3 as
`NOT GRANTED` at L15 and L449.

---

## 2. Consolidated blocker register

Status vocabulary as specified. `BOA` = `BLOCKED_ON_AUTHORIZATION`.

### B1 · Redis pool lifecycle — option B + F3/F4 (G1/G2/G3)

| Field | Value |
|---|---|
| Source | `A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT`, `A23R`, `A23B`, `A23C`, `A23_LOOP_IDENTITY`, `A22`, `A22V`, `A21` |
| Severity | **Medium for launch, High for production reliability.** Not a paper-track gate |
| Status | **`BLOCKED_ON_AUTHORIZATION`** — and additionally **design-disputed** (§3) |
| Decision needed | Approve changes 1–4 in `backend_app/backend/redis_manager.py` + a new lifecycle test file; **and first settle the A23-loop-identity objection** |
| Owner | **UNIDENTIFIED.** `A23R:20` records the file as UNOWNED |
| Dependencies | None upstream. Blocks: full R6 closure, QUOTA-01's remaining real-Redis evidence |
| Existing tests | `tests/test_redis_debounce.py` — 9 passed, 1 strict XFAIL (`r6_d`), server-free (executed in A22) |
| Remaining acceptance | G1/G2/G3 "reproduced, not fixed" (`A23R:24`); pool-leak and partial-init tests missing |
| Smallest next action | **Name the owner.** Then settle the design conflict, then approve |

### B2 · Redis runtime-failure recovery — option A

| Field | Value |
|---|---|
| Source | `A20_DEFECT_REMEDIATION_READINESS` §3e, `A22` §6, `A22V` decision 3, `A23B` row 4 |
| Severity | **High for production** — the only option that recovers a real connection loss. `_connected` has one writer (`initialize()`), so a runtime failure is permanent |
| Status | **`BLOCKED_ON_AUTHORIZATION`** |
| Decision needed | Health invalidation in ~25 handlers at `core/cache/redis_manager.py:753-1016` |
| Owner | owner of `backend_app/core/cache/redis_manager.py` (` M`, +185/−5) — identified by file, not by name |
| Dependencies | **Sequential after B1.** A20/A22 both rank A as riskier; F3/F4 are prerequisites |
| Existing tests | `r6_d` strict XFAIL is the contract test. **It must remain XFAIL after B1** — an XPASS would signal scope creep into A |
| Remaining acceptance | R6 recovery contract; 5 webhook setup errors |
| Smallest next action | Hold until B1 lands. Do not bundle |

### B3 · TENANT-01 — tenant isolation depth

| Field | Value |
|---|---|
| Source | `TENANT01_U4_U6_INVESTIGATION`, `TENANT01_FAST_COORDINATION`, `TENANT01_REMEDIATION_READINESS`, `TENANT01_AUDIT` |
| Severity | **High for production, Medium for paper.** Scorecard: `tenant_isolation_for_paper` = PARTIAL (0.10); `tenant_force_rls` blocks both live and production tracks |
| Status | **`BLOCKED_ON_AUTHORIZATION`** — D1–D7 recorded, none granted |
| Decision needed | D1 (where to wire the GUC), D4 (gate `create_all`), D5 (`FORCE ROW LEVEL SECURITY`), D7 (which PostgreSQL lane) |
| Owner | O1/O3/O4/O5 roles named in the reports; **real owners unnamed** |
| Dependencies | D5 depends on prerequisites passing; D7 gates the whole PostgreSQL lane |
| Existing tests | breadth sweep 0 leaks; credential-vault negative control |
| Remaining acceptance | RLS **enabled but not forced** + owner-role ORM path = defence-in-depth gap. PostgreSQL enforcement **UNEXECUTED** |
| Smallest next action | Resolve **D1** — its own report recommends "OPTION 3 — defer implementation, resolve D1 first" because "the clean file is not the right place, and the right place is not clean" |

### B4 · QUOTA-01 — metered quota

| Field | Value |
|---|---|
| Source | `A19_D5_CI_EVIDENCE`, `A20`, `A20Q`, `A22`, `PHASE10_WSK`, `PHASE10_WSC` |
| Severity | **Medium.** Scorecard weight 0.12, currently PARTIAL |
| Status | **`AWAITING_VERIFICATION`** (workstream A), **`BLOCKED_ON_AUTHORIZATION`** (slot guard) |
| What is now **evidenced** | Production `reserve_quota` against real Redis, CI run 38061973739: **exactly 10 of 50** admitted, `get_quota_usage` == 10, raw key == 10, zero skips, gate `state=run`. Zero-allowance writes no meter |
| What is **fixed but unverified** | The refusal-payload clamp **and** the INCRBY fail-open guard, both implemented in `subscription_engine.py` (` M`, +61/−4, sole ownership). **Never executed against real Redis** |
| What is **unproven** | Exchange-slot concurrency and release safety — 21 of 22 tests fail on absent `exchange_connection_slot_guard` / `_slot_lock_key`; `store_keys` still wraps its vault write unguarded, so the read-then-write race is live on `main` |
| Owner | quota owner for workstream A (active); slot guard is entangled in `routers/exchange.py` (`MM`, AUTH-01/CONN-01/TENANT-01) |
| Smallest next action | Run the three D5′ files against real Redis **with the current `subscription_engine.py`** — no new approval needed if CI already runs on push to the D5′ branch |
| Register correction | The scorecard's PARTIAL reason ("real-Redis UNVERIFIED") is **stale**; the narrower correct reason is "refusal-contract fix unverified; slot guard absent" |

### B5 · CD-8 — settlement crash recovery

| Field | Value |
|---|---|
| Source | `U9_CD8_AUTHORIZATION_GATE_AND_SCHEMA_FINDINGS`, `U7_CD8_CRASH_RECOVERY_READINESS` (50 KB) |
| Severity | **High — money correctness.** "recorded payment that never granted anything — worse than the failure it was recovering" (`U4_U6:259`) |
| Status | **`BLOCKED_ON_AUTHORIZATION`** — "STOPPED at the Phase 1 decision gate. Both D8(a) and D8(b) are absent" |
| Decision needed | D8(a) and D8(b) |
| Owner | billing/settlement owner — `routers/billing.py` is ` M` (+740 churn) |
| Dependencies | U9 found **four schema findings, one of which "inverts a load-bearing assumption in the U7 design"** — so U7's §5.2 design needs three mandatory changes if D8(b) is granted |
| Existing tests | T1–T10 planned against existing `FakeSupabase` seams; **none executed** |
| Remaining acceptance | "CD-8: OPEN / PROPOSED. Not accepted, not implemented, not closed. Unit-test evidence does not…" (`U9:263`). Q14 (live `library_subscriptions` schema) unresolved |
| Smallest next action | Decide D8(b); if granted, U7's design must be revised per U9's findings **before** implementation |

### B6 · W1C-02 — schema-drift classifier

| Field | Value |
|---|---|
| Source | `W1D10_CLASSIFIER_IMPLEMENTATION_READINESS` (newest, 10-11), `W1D07`, `W1D08`, `W1D06`, `W1D` |
| Severity | **Medium.** A verification tool, not a runtime path |
| Status | **`READY_FOR_IMPLEMENTATION_REVIEW`** — the most implementation-ready blocker in the register: spec complete (W1D-09), plan complete (W1D-10), "there is no remaining analysis that would change the implementation" |
| Decision needed | **AP-W1D-3** plus D1/D2/D3 — W1D-10 §6.2 contains a ready-to-paste grant template |
| Owner | "Code owner" — unnamed. AP-W1D-1/2/4/5 need DB + coordinator |
| Dependencies | AP-W1D-1/2 (ledger rows) and AP-W1D-5 (`pgserver` install) are separate and also NOT GRANTED |
| Existing tests | Invariant A **already covered**, 6 tests, mutation-proven (W1D-08). Invariant B covered only against a *test-local* declaration |
| Remaining acceptance | 4-way classification + 10 regression tests; production key map forbidden until AP-W1D-3 |
| Smallest next action | Grant AP-W1D-3 with D1/D2/D3 using W1D-10's own template — **lowest-friction grant in the register** |

### B7 · Production watchdog integration

| Field | Value |
|---|---|
| Source | `D5_WATCHDOG_REPAIR_REPORT` |
| Severity | **High for live trading, low for paper** |
| Status | **`IMPLEMENTATION_IN_PROGRESS` / partially `CLOSED_WITH_EVIDENCE`** |
| Evidence | **W-1, W-2, W-2b FIXED** with 18 new tests in `tests/test_d5_watchdog_adjudication_repair.py`; `order_watchdog.py` +143/−9. Scorecard: `stale_order_recovery_watchdog` = MET |
| Open | **W-1b FOUND AND NOT FIXED** — "it blocks the benefit of W-1 and is outside this phase's authorisation". W-4, W-5 blocked; venue-side duplicate rejection unverified on all six venues |
| Owner | watchdog owner (demonstrably active and shipping) |
| Smallest next action | Authorize W-1b in the same file and by the same owner — a **narrow, proven-safe extension** of work already landed |

### B8 · Database / schema verification

| Field | Value |
|---|---|
| Source | `PHASE11A_READINESS_SCORECARD.json`, `W1D*`, `TENANT01_*`, `DATABASE_PROVISIONING_RUNBOOK` |
| Severity | **Blocks the production track entirely** |
| Status | **`BLOCKED_ON_AUTHORIZATION`** (D7 / AP-W1D-5) |
| Evidence | "Every database figure is LOCAL PostgreSQL 16.2. Production schema state is unverified." `prod_dsn_verify` NOT_DONE — "no production DSN in any phase"; `migration_state_in_production` NOT_DONE |
| Smallest next action | Decide D7 (isolated PostgreSQL lane). **Do not** point any tooling at a production DSN without a separate explicit grant |

### B9 · Deployment, rollback, monitoring, release certification

| Field | Value |
|---|---|
| Source | `PHASE11A_READINESS_SCORECARD.json` (`live_track`, `production_track`), `deployment_go_no_go.md`, `PHASE11A_SANDBOX_CERTIFICATION_REPORT` |
| Severity | **Release gate** |
| Status | **`BLOCKED_ON_AUTHORIZATION` + `BLOCKED_ON_CREDENTIAL`** |
| Evidence | `live_track`: **0%** evidence-backed, "TRADING LIVE — OFF / NOT CERTIFIED". `production_track`: **0%**, "PRODUCTION — NOT CLASSIFIED" |
| Blocking gates | live: `paper_sandbox_e2e_evidence` (BLOCKED_ON_CREDENTIAL), `recon_operational_for_paper` (OPEN), `tenant_force_rls` (OPEN), `live_disable` (correctly OFF). production: `prod_dsn_verify`, `tenant_force_rls`, `migration_state_in_production` |
| Smallest next action | Supply a venue sandbox credential; decide whether `ExchangeReconciliationService` starts in `main.py` lifespan |

### B10 · Paper-sandbox E2E evidence — **the largest single launch gate**

| Field | Value |
|---|---|
| Source | scorecard `paper_track.paper_sandbox_e2e_evidence`; `EXCHANGE_SANDBOX_CERTIFICATION`; `PHASE11A_SANDBOX_CERTIFICATION_REPORT` |
| Severity | **Highest weight of any single unmet paper gate (0.15)** |
| Status | **`BLOCKED_ON_AUTHORIZATION`** (credential provision) |
| Evidence | "no `AERORA_SANDBOX_*` credential; lifecycle suite skips naming exactly what it needs; **0 sandbox orders placed**" |
| Dependencies | None technical — the suite already names what it needs |
| Smallest next action | Provide one venue sandbox credential. **No code change required** |

### B11 · Reconciliation service not running

| Field | Value |
|---|---|
| Source | scorecard `recon_operational_for_paper` + `exchange_reconciliation_service_WIRED_AND_RUNNING` = **NOT_STARTED** |
| Severity | 0.10 paper-track weight; also a live-track blocking gate |
| Status | **`BLOCKED_ON_AUTHORIZATION`** |
| Evidence | "`main.py` lifespan does not start `ExchangeReconciliationService`; no leader election". The service itself is **MET** for implementation and fail-safe tested |
| Dependencies | `main.py` is ` M` (+325 churn) — concurrent edits; leader election is an open design question |
| Smallest next action | Decide whether to start it for paper-only scope; the code already exists and refuses safely |

### Register summary

| Status | Blockers |
|---|---|
| `BLOCKED_ON_AUTHORIZATION` | **B1, B2, B3, B5, B8, B9, B10, B11** (8) |
| `READY_FOR_IMPLEMENTATION_REVIEW` | **B6** (1) |
| `IMPLEMENTATION_IN_PROGRESS` | **B7** (1, partially closed with evidence) |
| `AWAITING_VERIFICATION` | **B4** workstream A (1) |
| `CLOSED_WITH_EVIDENCE` | **none in full.** B7's W-1/W-2/W-2b are the only closed items |

---

## 3. Redis decision packet

Per §3, this closes the loop without reopening the investigation. The four requested approvals,
with status:

| # | Approval | Owner | Status |
|---|---|---|---|
| 1 | Changes 1–4 in `backend_app/backend/redis_manager.py` + new lifecycle test file | **UNIDENTIFIED** (`A23R:20`: file "recorded as **UNOWNED**") | **REQUESTED ×6, UNRESOLVED** |
| 2a | `close()`'s changed contract becomes visible at `core/cache/redis_manager.py:619` | owner of `core/cache/redis_manager.py` | **REQUESTED, UNRESOLVED** |
| 2b | Option A — health invalidation in ~25 handlers at `core/cache:753-1016` | same | **REQUESTED, UNRESOLVED** (`A22V` row 3) |
| 3 | Rebuild semantics for the five `Depends(get_redis_manager)` handlers in `routers/exchange.py` (318, 534, 670, 772, 1138) | AUTH-01/CONN-01/TENANT-01 owner | **REQUESTED, UNRESOLVED** |
| 4 | May `aclose()` be standardised on (redis-py floor)? | release owner | **RESOLVED by `A23R:23`** — no floor change needed; redis 5.0.8 has `aclose` on both `Redis` and `ConnectionPool` |

**No approval in this area is GRANTED. None is REJECTED. Item 4 is the only one resolved.**

### A design conflict that must be settled *before* approval

`PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` raises two objections that, if correct, change what
should be approved:

1. **The verification criterion is unsatisfiable as stated** (L11, L229): the closed-loop failure
   is *not* reproduced by `test_r6_d`, and in the CI environment "no loop is closed; no second loop
   exists" in the way the design assumes (`A23C:127`).
2. **Placement is too narrow** (L152): B-2′ inside `get_redis_manager()` "would leave the kill
   switch, audit trail… unprotected", because those paths do not resolve through that accessor —
   they go through `SharedRedisManager` or the compat proxy.

Objection 2 is the serious one and is consistent with my own A23 §4a finding: only five
`Depends(get_redis_manager)` sites plus four direct awaiters resolve through that function, while
the ~25 core-cache handlers do not. **Approving option B at `get_redis_manager()` alone would buy
less than the reports imply.**

**Recommendation:** before granting item 1, ask the (to-be-named) owner to choose between
*accessor-only* B (narrow, independently shippable, admittedly partial) and *accessor + proxy* B
(wider, needs owner 2a). `A23C:325` records this same question as **D-A23C-3** and recommends
accessor-only now with the proxy later. I agree — but it must be an explicit, recorded choice
rather than an unstated assumption.

---

## 4. Safe parallel work

### Parallel now — no unresolved dependency, no shared-file edit

| # | Task | Why safe |
|---|---|---|
| P1 | Run the three D5′ files against real Redis in CI **as they stand**, including the already-fixed `subscription_engine.py` | Closes B4's `AWAITING_VERIFICATION`. CI already runs on push to the D5′ branch; no new approval if no new commit is required |
| P2 | Read-only confirmation of which `main.py` lifespan entries exist today | Informs B11 without editing `main.py` |
| P3 | Re-run `tests/test_redis_debounce.py` (9 passed, 1 XFAIL, server-free) | No shared state, no server. Already green in A22 |
| P4 | Enumerate exactly which `AERORA_SANDBOX_*` variables the lifecycle suite names in its skip reasons | Turns B10 into a concrete credential request. Read-only |
| P5 | Name owners for the 9 files in §6's owner table | Zero engineering cost, unblocks the most items |

### Owner decision required — source/test changes

B1, B2, B3 (D1/D4/D5/D7), B5 (D8a/D8b), B6 (AP-W1D-3 + D1/D2/D3), B7 (W-1b), B11 (`main.py`).

### Sequential dependency

| Task | Waits for |
|---|---|
| B2 (option A) | B1 landing; F3/F4 are prerequisites |
| B6 full closure | AP-W1D-1/2 (ledger) and AP-W1D-5 (`pgserver`) |
| B5 implementation | U7 design revision per U9's four findings |
| B3 D5 (`FORCE RLS`) | D1 + prerequisites |
| B8 production verification | D7 lane decision |

### Release gate — evidence required before certification

B10 (sandbox E2E), B11 (reconciliation operational), B3 (`tenant_force_rls`), B8
(`prod_dsn_verify`, `migration_state_in_production`), and live-trading certification as a whole.

### Commands NOT to run without separate approval

Destructive PostgreSQL setup; anything pointed at a production DSN; live exchange operations;
starting a local Redis (W1A **A-3**, not held); `pgserver` install (AP-W1D-5); CI dispatch;
full-suite runs that mutate shared state concurrently with other sessions.

---

## 5. Execution plan to 2026-10-25

The date does not override a safety gate. Where a gate cannot be met, the fallback is **scope
reduction, not evidence relaxation**.

### Track 1 — limited SaaS launch (paper/retail, live trading OFF)

Current evidence-backed readiness: **53%**. The 47% gap is 22 points PARTIAL + 25 points
BLOCKED/OPEN.

| # | Deliverable | Prerequisite / owner | Validation evidence | Blocks | Fallback if unavailable |
|---|---|---|---|---|---|
| T1 | **One venue sandbox credential** (`AERORA_SANDBOX_*`) | credential holder | lifecycle suite executes; **> 0 sandbox orders placed** | limited SaaS launch | Launch with sandbox-dependent features disabled and the gate recorded OPEN — **do not** mark it MET |
| T2 | **Start `ExchangeReconciliationService`** for paper scope | service owner; `main.py` is ` M` | service starts in lifespan; reconciliation observed operational | limited SaaS launch | Launch with reconciliation documented as not operational, and an explicit manual-reconciliation runbook |
| T3 | **Verify the quota refusal fix against real Redis** | none — P1 | all 3 D5′ files; refusal usage within `[0, limit]`; zero skips | QUOTA-01 evidence | Keep QUOTA-01 PARTIAL |
| T4 | **Grant AP-W1D-3 + D1/D2/D3** | code owner; template ready in W1D-10 §6.2 | 4-way classification + 10 tests | schema-drift confidence | Keep W1C-02 OPEN; drift detection stays coarse |
| T5 | **Resolve TENANT-01 D1** | O1/O3 | GUC wired at one endpoint, test-proven | tenant depth for production | Paper launch can proceed with RLS enabled-not-forced **recorded as a known gap** |

### Track 2 — live automated trading certification

**Current evidence-backed readiness: 0%. This is not achievable by 2026-10-25 and should not be
attempted.**

Reasons, each independently sufficient: `paper_sandbox_e2e_evidence` BLOCKED_ON_CREDENTIAL;
`recon_operational_for_paper` OPEN; `tenant_force_rls` OPEN; B7's **W-1b not fixed**, which blocks
the benefit of the W-1 fix; venue-side duplicate rejection unverified on all six venues; QUOTA-01's
exchange-slot concurrency UNPROVEN with the `store_keys` race live on `main`; production schema
state unverified.

**Recommendation: decouple the tracks formally.** Target a limited SaaS launch (paper only, live
trading hard-OFF) for Oct 25 and set a separate, later date for live certification once T1/T2 and
B7's W-1b are evidenced.

---

## 6. Answers to the completion criteria

### 1. Which blockers require owner decisions now?

Eight, in descending launch leverage:

| Priority | Decision | Owner | Cost |
|---|---|---|---|
| **1** | **Name owners** for `backend/redis_manager.py`, `core/cache/redis_manager.py`, `routers/exchange.py`, `routers/billing.py`, `main.py`, the W1C-02 code, the watchdog, the PostgreSQL lane, and the credential store | coordinator | **zero engineering** — unblocks 8 of 11 blockers |
| **2** | Provide one `AERORA_SANDBOX_*` credential (B10) | credential holder | zero code |
| **3** | Start `ExchangeReconciliationService` for paper scope (B11) | service owner | small, `main.py` |
| **4** | AP-W1D-3 + D1/D2/D3 (B6) | code owner | template ready |
| **5** | W-1b (B7) | watchdog owner — already shipping | narrow extension |
| **6** | D8(a)/D8(b) (B5) + accept U7 redesign | billing owner | medium |
| **7** | TENANT-01 D1, then D4/D5/D7 (B3, B8) | O1/O3/O4/O5 | medium |
| **8** | Redis B1 — **after** settling the accessor-only vs proxy design conflict (§3) | unidentified | medium |

### 2. Which independent tasks can safely progress immediately?

P1–P5 in §4. The highest value are **P1** (real-Redis verification of the already-fixed quota
path, no new approval needed) and **P4** (turn B10 into a precise credential request). Neither
touches a blocked file.

### 3. Shortest evidence-backed path to a limited SaaS launch

**T1 + T2.** Those two gates carry 25 of the 47 non-evidenced points. Both are authorization or
credential problems, not engineering problems — the reconciliation service is already implemented
and fail-safe tested; the sandbox suite already names what it needs. With T1 and T2 evidenced, the
paper track moves from 53% to roughly 78%, and the residual 22 points are the two PARTIAL items
(quota real-Redis depth, tenant RLS depth) which are defensible as *recorded known gaps* for a
paper-only launch with live trading hard-OFF.

**No Redis, CD-8, or schema-drift work is on this path.**

### 4. What still prevents live-trading certification?

Seven independent blockers, listed in §5 Track 2. The two that no amount of local work can clear
are the **venue sandbox credential** and **venue-side duplicate-rejection verification across all
six venues**. Live certification should be given its own date.

### 5. Next three concrete actions

| # | Action | Dependency | Owner |
|---|---|---|---|
| **1** | **Publish an owner map** for the nine files/areas in §6.1. Nothing else unblocks more per unit of effort, and `A23R:20`'s "UNOWNED" finding makes it the actual root blocker | none | coordinator |
| **2** | **Supply one venue sandbox credential** and run the lifecycle suite | none | credential holder |
| **3** | **Run the three D5′ files against real Redis** with the current `subscription_engine.py`, to convert B4 from `AWAITING_VERIFICATION` to evidenced | none technical; confirm no new commit is needed | D5′ / quota owner |

---

## 7. Preservation and status

| Invariant | State |
|---|---|
| Files changed | **0** outside this report |
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` |
| Index content digest | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` — unchanged |
| Staged / unstaged / untracked | 22 / 102 / 139 — unchanged |
| Other sessions' reports | **none modified** |
| Tests executed this phase | **none.** No test result is claimed as this phase's |
| Database / Redis / exchange / CI contact | **none** |
| **QUOTA-01** | **OPEN** |
| **TENANT-01** | **OPEN** |
| **W1C-02** | **OPEN** |
| **CD-8** | **OPEN / PROPOSED** |
| **R6 / Redis recovery** | **OPEN** |
| **Production Redis reliability** | **OPEN** |
| **Limited SaaS launch** | **NOT READY** — 53% evidence-backed; T1/T2 outstanding |
| **Live trading** | **OFF / NOT CERTIFIED** — 0% evidence-backed |
| **Production track** | **NOT CLASSIFIED** — 0% |

No approval was inferred from this prompt, from any report's recommendation, from a clean file,
from a grant template, or from silence. No readiness percentage was revised; the figures quoted are
`PHASE11A_READINESS_SCORECARD.json`'s own, with one stale *reason* flagged in B4 and the file left
untouched.

Artifacts: `p11b24_discover.{py,txt}`, `p11b24_extract.{py,txt}`, `p11b24_entry_status.txt`.
