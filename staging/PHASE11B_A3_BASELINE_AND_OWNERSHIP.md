# PHASE 11B-A3 — BASELINE AND OWNERSHIP

**Scope:** baseline only. **No application code was changed.** No commit, push, PR, workflow
dispatch, infrastructure change, production access or live-trading enablement occurred.

**This document does not define readiness.** It indexes the registers that do, and records
where they disagree. See `staging/PHASE11B_A3_BLOCKER_REGISTER.md` for the reconciliation and
`staging/PHASE11B_A3_EXECUTION_PLAN.md` for sequencing.

**Status vocabulary:** VERIFIED (executed against the intended target, evidence recorded) ·
PARTIAL · BLOCKED · FAILED · NOT CLASSIFIED.

---

## 1. Revision and working tree (VERIFIED)

```
branch            main
HEAD              074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main       074b772ab16cece62cbfca0f2b78497be2ae56d5
HEAD == upstream  true    (ahead 0 / behind 0)
remote            https://github.com/Narendra2212/vyomquant-saas.git
git status        252 entries
```

| Status code | Count | Meaning |
|---|---|---|
| `??` | **132** | untracked |
| ` M` | **98** | modified, unstaged |
| `A ` | 12 | added, staged |
| `M ` | 8 | modified, staged |
| `MM` | 1 | staged **and** unstaged (`backend_app/routers/exchange.py`) |
| `AM` | 1 | added then modified (`docs/security-reviews/...`) |

**The single most consequential baseline fact:** a large share of the Phase 10/11 remediation
— including 11 numbered SQL migrations, ~100 new test files, the entire `staging/` evidence
corpus, `tests/exchange_sandbox/`, and the whole D5′ real-Redis change set — **exists only in
the working tree and is on no remote ref.** CI therefore cannot see any of it, and several
findings recorded OPEN in older registers are fixed only in uncommitted files.

---

## 2. Authoritative registers, and which are superseded (VERIFIED by content + mtime)

| Rank | Document | Role |
|---|---|---|
| 1 | `staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md` + `staging/PHASE11_READINESS_EVIDENCE.json` | **Authoritative** finding register. Newest, broadest (`P11-B01..B10`, `P11-U01..U07`, 10 critical / 19 high / 11 medium / 2 low). Supersedes the legacy ID set for anything it carries. |
| 2 | `staging/PHASE11_LIVE_TRADING_READINESS_AUDIT.md` | The narrative for rank 1. Holds the hard-gate verdicts. |
| 3 | `staging/PHASE11A_LAUNCH_GATE_REGISTER.md` + `PHASE11A_READINESS_SCORECARD.json` | **Authoritative for per-track GATES** (paper / live / production). Carries gate states rank 1 does not (RECON-01, QUOTA-HARD-LIMIT, CONN-01, FEE-02, MONEY-08, REFERRAL-PAYOUT-SCOPE). |
| 4 | `staging/FINAL_LAUNCH_SCORECARD.md` | **Partly superseded.** Its "CURRENT VERIFIED STATE" block is Phase 7-era; Phase 11 found **4 of 6** of its carried-forward findings stale. Still the authoritative record of Phases 1–7. |
| 5 | `staging/EXCHANGE_SANDBOX_CERTIFICATION.md`, `PHASE11B_SANDBOX_SCENARIO_MATRIX.md`, `PHASE11B_EXCHANGE_PREREQUISITES.md` | **Authoritative for venue certification.** |
| — | `PHASE11A_NEXT_ACTIONS.md`, `PHASE11B_A_NEXT_ACTIONS.md`, `PHASE11B_NEXT_FIXES.md` | Action plans, **not** registers. `PHASE11B_NEXT_FIXES.md` self-declares subordinate. |

**No new register is created by A3.** The blocker register deliverable is a *reconciliation
index* over the above.

### Readiness figures that already exist (the brief forbids combining tracks)

| Source | Figure | Per-track? |
|---|---|---|
| `PHASE11_READINESS_EVIDENCE.json` | **70 % implementation / 39 % evidence-backed** | No — a single weighted pair across 8 categories |
| `PHASE11A_READINESS_SCORECARD.json` | **paper 53 % · live 0 % · production 0 %** | **Yes**, and it explicitly refuses a blended number |

These do not conflict; they measure different things. **The 70/39 pair must not be quoted as a
launch readiness percentage for any single track** — its own text says "the second number
governs" and that a high average conceals a critical blocker.

---

## 3. Separate readiness determinations (as the registers state them today)

| Track | Status | Source |
|---|---|---|
| **Application / software release** | **NOT CLASSIFIED** — `01 PR Check` is red (§5) and the release gate does not test (A3-01) | this document |
| **Paper trading** | **CONDITIONAL** (Phase 11: 4–6 operational blockers) / **NOT CLASSIFIED** (scorecard) / **NO-GO** (Phase 7 block) | contradiction C-7 |
| **Exchange sandbox, per venue** | binance **OFFLINE MECHANISM VERIFIED** · bybit same · okx same + library-blocked. **0 of 12 scenarios executed, 0 sandbox orders ever placed, on any venue** | `PHASE11B_SANDBOX_SCENARIO_MATRIX.md` §6 |
| **Production infrastructure / data integrity** | **NOT CLASSIFIED** — no production DSN in any phase; migrations 026–032 applied locally only | `PHASE11A_LAUNCH_GATE_REGISTER.md` §C |
| **Live trading** | **OFF / NOT CERTIFIED** — unchanged in every phase | all registers agree |

---

## 4. Local test services, and what silently skips without them (VERIFIED)

| Variable | State |
|---|---|
| `AERORA_TEST_DATABASE_URL`, `DATABASE_URL` | unset |
| `AERORA_TEST_REDIS_URL`, `REDIS_URL` | unset |
| `AERORA_EXCHANGE_SANDBOX_CERT`, `AERORA_SANDBOX_VENUE` | unset |
| `MASTER_ENCRYPTION_KEYS` | unset — `APIKeyVault.__init__` raises without it |

Reachability, one attempt each, no retries:

| Endpoint | Result |
|---|---|
| `127.0.0.1:6379` (Redis) | **closed** |
| `127.0.0.1:5432` (Postgres default) | **closed** |
| `127.0.0.1:51805` (local bootstrap per `staging/pg_dsn.txt`) | **OPEN** — loopback, non-production |

Gated inventory counted from source:

| Lane | Files | Tests | Consequence |
|---|---|---|---|
| PostgreSQL-gated | **38** | ~632 (Phase 11 count) | all skip; holds the proofs for `P11-U01..U03, U05..U07` |
| real-Redis-gated | **3** | 3 required + 6 webhook | all skip; holds `P11-U04` |
| exchange-sandbox | 2 | 16 scenario + 11 contract | 16 skip for want of a credential |

**A skip is not a pass.** Every gated file in this repository says so in its own skip reason —
that convention is real and was verified.

---

## 5. CI: triggers, job dependencies, and recent history (VERIFIED)

| Workflow | Triggers | Jobs |
|---|---|---|
| `01-pr-check.yml` "01 PR Check" | **`pull_request` only** | 8 — `static-audit`, `format-check`, `unit-tests` (needs static-audit, **redis service**), `paper-mode-tests` (redis), `database-tests` (postgres), `provisioning-and-isolation` (postgres), `docker-check`, `frontend-tests` |
| `02-build.yml` "02 Build" | **`push` → main/master**, `workflow_call` | `build-push` |
| `03-deploy.yml` "03 Deploy" | **`workflow_run` ← "02 Build"** on main/master, `workflow_dispatch` | `pre-deployment-validation` → `ecs-deployment` |
| `04-nightly-audit.yml` | schedule, dispatch | 1 |
| `05-security.yml` | push, pull_request, schedule | 3 |

### Recent run history — 0 of 10 succeeded

| Created | Conclusion | Head SHA | Run ID |
|---|---|---|---|
| 2026-10-05T17:13:50 | **failure** | `6c5660a1` | 37346853502 |
| …8 more… | failure | — | — |
| 2026-09-26T19:39:00 | failure | `d8ca1794` | 36266778219 |

Job breakdown of the newest run (`37346853502`): 6 jobs **success**, and
**`Unit & Integration Tests` FAILURE** with **39 `FAILED` tests** — chiefly
`test_no_credential_in_api_response_body.py` (~13), `property/test_tenant_isolation_matrix.py`,
`test_ml_deployment_guards.py`, and `Requirement 21.8: 4 of the 150 attempted cells failed`.

**The Redis service itself works in CI** (`redis service is healthy` at 17:17:43, after a
health-gated start). That is a genuine positive for D5′'s premise.

Local `HEAD` appears in **no** run, and no run contains any D5′ path.

---

## 6. Database target, schema classification, migration head, safeguards (VERIFIED / PARTIAL)

| Item | Finding |
|---|---|
| Target identity | Only a **local loopback bootstrap cluster** (`127.0.0.1:51805`, db `postgres`) is reachable. Non-production by construction. |
| Production target | **Never connected in any phase.** Production is Supabase via Supavisor pooler :6543, `pool_mode=transaction`, connecting as role **`postgres` (the table owner — the reason RLS is inert)**. From repository artefacts only; **no AWS call was made in any phase.** |
| Alembic revisions | 12 files. **Head NOT resolved by this probe** — my down-revision scan computed 0 heads, which means the regex missed a form (e.g. a tuple or `None`). Recorded as **PARTIAL**, not asserted. Phase 11 separately records that the `execution_records` uniqueness DDL in `d97ffff9c3bb_execution_schema_rebuild.py` **was never read** (`P11-U02`). |
| `backend_app/migrations/*.sql` | 38 files, **11 UNTRACKED** — `022`–`032`. Includes **`026_fee_records_row_level_security.sql`** (the `P11-U06` / TENANT-02 fix: written, untracked, unapplied) and `025_work03_bot_ownership_claims.sql` (the WORK-03 backstop). |
| `migrations/*.sql` | 11 files, 0 untracked. |
| Data-dir safeguards | `.gitignore` covers `pgdata`, `redis/`, `*.log`, `pg_dsn.txt`, `.env`, and `*.tfstate` / `*.tfstate.*` / `**/.terraform/`. **0 tfstate files tracked** — verified by `git ls-files`. *(An earlier pass of my own probe reported tfstate uncovered; that was a too-narrow substring check and is corrected here.)* |

---

## 7. Release and rollback procedures (VERIFIED)

| Artefact | State |
|---|---|
| `DATABASE_PROVISIONING_RUNBOOK.md` | present, substantial; documents a loopback-only provisioning path and a local Redis pattern |
| `deployment_go_no_go.md` | present |
| `.github/workflows/03-deploy.yml` | present — `pre-deployment-validation` → `ecs-deployment` |
| `docs/adr/` | present, 7 ADRs |
| Postgres readiness/safety | `PHASE10_WSH_POSTGRES_SAFETY.md`, `WSI_POSTGRES_DSN_CLOSEOUT.md`, `WSJ_POSTGRES_READINESS_REPORT.md` |

### A3-01 — the release gate tests security, not correctness (NEW, VERIFIED)

The deploy chain is `push → 02 Build → (workflow_run) → 03 Deploy`. `03 Deploy`'s
`pre-deployment-validation` **does** gate, fail-closed, on **`05 Security`** having passed for
the head SHA (`scripts/require_security_scan.py`, documented in the workflow's own comments
including a real 403-scope incident). It does **not** consult `01 PR Check`.

And `01 PR Check` triggers on **`pull_request` only**. So a direct push to `main` produces no
`01 PR Check` run at all, and the deploy gate has nothing to look for.

**Consequence:** a commit can reach ECS with the test suite never having run on it. This is
Phase 11's `P11-B08`, independently confirmed here with the exact mechanism — and it is more
precisely stated than "no gate": there *is* a fail-closed security gate; there is **no test
gate**. Fix is cheap (add a test-result check, or a `push` trigger to `01 PR Check`), and it is
a shared-workflow change requiring approval.

---

## 8. Concurrent workstream ownership map

The tree carries work from many tabs. Files relevant to the P0 workstreams, with owners:

| File | State | Owner / contents | A3 may edit? |
|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | `M ` + ` M` | **SHARED, two unrelated changes**: D5′'s `AERORA_TEST_REDIS_URL` env line (~28 lines) **and** another tab's `provisioning-and-isolation` job (~142 lines) | **No** without approval |
| `tests/conftest.py` | ` M` | **NOT D5′.** A rate-limiter leak guard (see `REGRESSION_ORDER_ISOLATION.md`), appending at ~line 269+. The two lines D5′ depends on (`REDIS_URL=""`, `VYOMQUANT_MODE="safe"`) are **already committed on origin/main**. | **No** |
| `backend_app/routers/exchange.py` | `MM` | CONN-01 eviction + AUTH-01 `require_aal2` + SC-27 error classification | mine (WS-C) |
| `backend_app/core/subscription_dependencies.py` | ` M` | QUOTA-01 slot guard + `_release_slot_lock` (`+253/−0`, purely additive) | mine (WS-C) |
| `backend_app/backend/connection_engine.py` | ` M` | CONN-01 pool credential identity (`+188/−18`) | mine (WS-C) |
| `backend_app/core/cache/redis_manager.py` | ` M` | **SHARED** (`+185/−5`) — another tab | **No** |
| `backend_app/main.py` | ` M` | **SHARED** — lifespan; D5′/WS-C added an 8-line pool drain, other tabs added more | coordinate |
| `backend_app/core/safety_config.py`, `routers/billing.py` | ` M` | **Other tabs.** safety_config's diff is docstring-only on mode resolution; billing changed **0** `limiter.limit` lines | **No** |
| `backend_app/core/exchange_certification.py`, `routers/support.py` | ` M` | **Other tabs** — they fixed EXCH-04 / EXCH-05 mid-flight | **No** |
| `backend_app/backend/exchange_websocket_listener.py`, `exchange_reconciliation.py` | ` M` | **Other tabs** — FILL-01 partial-fill fix landed here | **No** |
| `tests/real_redis_gate.py`, `tests/test_quota01_*.py` | `??` | D5′ / WS-C | mine |
| `tests/exchange_sandbox/` | `??` | Workstream E | **No** |
| `staging/PHASE11B_A3_CI_RUN_EVIDENCE.md` | `??` | **A concurrent tab has already done the A3 CI-evidence task.** Decision: **UNVERIFIED**. | **Do not duplicate** |

### Overlap hazards

1. **`01-pr-check.yml`** is the single highest-contention file: two workstreams' changes, both
   uncommitted, and the file both the D5′ fix and the A3-01 test gate must touch. **Needs one
   owner before any edit.**
2. **`main.py` lifespan** — WS-C (pool drain), reconciliation wiring (RECON-01) and readiness
   (OBS-01) all land in the same block.
3. **`core/execution_engine.py`** — Phase 11 records that `P11-B03` (daily-loss/drawdown) and
   `P11-B04` (equity reconciliation) both edit `_execute_trade_internal` and **must be
   serialised**.
4. **Migration numbering** — 11 untracked SQL files already occupy `022`–`032`; a new migration
   must not collide.

---

## 9. Safety confirmation for this baseline

| Statement | Status |
|---|---|
| Live trading OFF | **VERIFIED** — no `LIVE*`/`TRADING*` variable set; no execution code ran |
| No production DB / Redis / exchange / payment endpoint accessed | **VERIFIED** — only loopback reachability probes; the single DB endpoint touched is a loopback bootstrap cluster, and no connection with credentials was made by this probe |
| No commit, push, PR, dispatch, infra change | **VERIFIED** — `HEAD` unchanged, 0 ahead of upstream |
| No reset / clean / stash / revert | **VERIFIED** — no git write command issued |
| No application code changed | **VERIFIED** — A3 wrote only `staging/` files |
| Concurrent work preserved | **VERIFIED** — 252 entries, nothing removed |
| No credential or DSN printed | **VERIFIED** — probes report names, hosts and ports only |
| Read-only CI access | **VERIFIED** — `gh run list` / `gh run view` only |

### Files written by A3

```
staging/PHASE11B_A3_BASELINE_AND_OWNERSHIP.md   (this file)
staging/PHASE11B_A3_BLOCKER_REGISTER.md
staging/PHASE11B_A3_EXECUTION_PLAN.md
staging/probe_p11b_a3_baseline.py  + p11b_a3_baseline_probe.txt
```
