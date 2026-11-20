# Phase 19 — Main Stabilization, Security Gate Recovery, and Critical Closure

**Destination verified ABSENT before writing.** **0 production files · 0 test files · 0 workflows ·
0 Terraform · 0 migrations · 0 governance records changed. 1 artifact written (this file).**
No CI dispatched. No infrastructure provisioned. No credential read.

> **HEADLINE — a new, verified release-path defect.** The backend deploy gate works and correctly
> refused `ecd23a9e`. **The frontend deploy path has no gate at all, and it published that same
> commit to production S3 + CloudFront.** The one check designed to stop a secret reaching the
> shipped bundle runs on `pull_request` only, while the publish runs on `push` only — **mutually
> exclusive triggers.** This is not hypothetical; it already happened at the current HEAD.

| Status | Value |
|---|---|
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **TENANT-01** | **NOT CERTIFIED** |
| **PostgreSQL** | **UNVERIFIED** |
| **Backend deployment** | **BLOCKED** — correct; `05 Security` red at `ecd23a9e` |
| **Frontend deployment** | **UNGATED — published at `ecd23a9e` with security red** |

---

## 1. Baseline and preservation contract

| Measurement | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e` | identical |
| `origin/main` / divergence | `ecd23a9e` / **0 ahead 0 behind** | identical |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical — no commit, reset, stash or checkout** |
| staged / unmerged | **0 / 0** | 0 / 0 |
| porcelain (collapsed) | 76 | 77 (+1 = this report) |
| worktrees | 1 | 1 |

`HEAD` still equals `ecd23a9e934ec58a7b1f0e5db638da8e865e8187`.

**The only non-`staging/` modification in the tree is `tests/test_drift_classification.py`**
(` M`, another session's W1C-02 work, +143 lines uncommitted). **Left untouched.** Every other
pre-existing modification and all concurrent artifacts — including
`PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md`, `PHASE15_SECURITY_GATE_CLOSURE_AND_MAIN_VERIFICATION.md`,
`PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md`, `PHASE16_SECURITY_RELEASE_AND_CRITICAL_CLOSURE.md`,
`PHASE13_CRITICAL_DEFECT_CLOSURE_AND_TENANT_SAFETY.md` and the A23E-series — were read only and
**not overwritten, renamed or discarded.**

### 1.1 Authorization status

**No grant exists for any source, test, workflow, Terraform, migration or governance change.**
Independently confirmed this phase and corroborated by `PHASE16` §1: *"Authorization status for this
phase: no grant exists for any source, test, workflow, Terraform, migration or governance change…
Authorizations exercised: none."* The W1D ledger `:802` remains `NOT GRANTED`.

**Decisive consequence:** no concurrent session has applied a security-gate fix. Verified by
enumeration — the working tree contains **zero** modifications to `terraform/`,
`backend_app/alembic/versions/`, `tests/test_bandit_sast_gate_is_green.py`, or any
`.github/workflows/` file. The gate is red because the findings are still there, not because
evidence is missing.

**Prohibited actions NOT performed:** no stage, commit, push, reset, stash, clean, revert, deploy,
infrastructure provisioning, production-database mutation, live-trading enablement, CI dispatch,
`provision_database.py` invocation, Terraform apply, severity-threshold change, global CRITICAL
suppression, or credential read.

---

## 2. Workstream A — per-check evidence matrix

`CI` All rows verified by my own read-only `gh` queries this phase; run IDs recorded.

| Check | Parent `074b772a` | Current `ecd23a9e` | Evidence |
|---|---|---|---|
| **`05 Security` (workflow)** | **success ×6** (5 `schedule`, 1 `push`) | **failure ×2** (`push`, `schedule`) | `gh run list --workflow=05-security.yml --branch main --limit 8` |
| ├ Trivy Container | success | **success** | run `38132697285` jobs |
| ├ **Bandit SAST & Dependency** | success | **FAILURE** | same |
| └ **Trivy IaC Misconfig & Secret** | success | **FAILURE** | same |
| **`03 Deploy`** | **success** | **failure** (gate refused; ECS **skipped**) | `gh run list --workflow=03-deploy.yml --branch main --limit 4` |
| **`06 Frontend Deploy`** | success | **success — published anyway (§4)** | `gh run list --workflow=06-frontend-deploy.yml --limit 5` |
| **`01 PR Check`** | n/a | **NEVER RAN at this SHA** | `pull_request`-only; `ecd23a9e` arrived by direct push |

**The overall workflow result is not conflated with scanner results:** Trivy *Container* passed; the
two failures are Bandit and Trivy *IaC*. Both failing jobs exited 1, and the IaC job runs with
`severity: CRITICAL` / `exit-code: 1`.

**No workflow was dispatched to reproduce an already-documented failure**, per the brief.

---

## 3. Workstreams B and C — scanner findings

### 3.1 Trivy IaC — per-file counts recomputed from the actual scan report

`CI` Extracted by me from run `38132697285`'s failed-step log:

| File | Trivy result |
|---|---|
| `terraform/alb.tf` | `Tests: 1 (FAILURES: 1)` · **CRITICAL: 1** |
| `terraform/redis-only/main.tf` | `Tests: 1 (FAILURES: 1)` · **CRITICAL: 1** |
| `terraform/security_groups.tf` | `Tests: 3 (FAILURES: 3)` · **CRITICAL: 3** |
| **`terraform/cloudtrail.tf`** | **ABSENT from the failure set — 0 CRITICAL findings** |

**Total 5 CRITICAL across 3 files. No secret findings.**

**Explicit confirmation the brief requires: `cloudtrail.tf` contributes ZERO CRITICAL findings.**
I record this with particular emphasis because **I got it wrong earlier.** My
`A23E15` Addendum A attributed the CRITICALs to `cloudtrail.tf` on the reasoning that it was newly
added and uses `sse_algorithm = "AES256"` with no `kms_key_id`. Every link in that chain was true
except the one that mattered: **Trivy rates the CloudTrail/S3 customer-managed-key checks below
CRITICAL, and the gate filters at CRITICAL**, so they never fail the build. A real finding at the
wrong severity is not the failing finding. Corrected in `A23E15` Addendum B and restated here.

### 3.2 Dispositions — analysis only, nothing changed

| Rule | Count | Location | Disposition |
|---|---|---|---|
| `AWS-0054` ALB not HTTPS | 1 | `alb.tf` | **False positive with an enforced compensating control.** The config redirects :80 → :443 when a custom domain exists, and a `lifecycle.precondition` **refuses the apply** in prod without one. Trivy evaluates the static `protocol = "HTTP"` attribute and cannot resolve `dynamic` blocks or preconditions. **Do not make the redirect unconditional** — that breaks non-domain deploys. Options: a justified `trivy:ignore:AWS-0054` citing the precondition, or documented acceptance. Infrastructure owner decides |
| `AWS-0104` unrestricted egress | 4 | `security_groups.tf` ×3, `redis-only/main.tf` ×1 | **Real but mostly conventional.** Three are the standard pattern (tasks must reach ECR, Secrets Manager, venues). **The `aws_security_group.redis` egress is the one genuinely unnecessary rule and the single safe tightening** — it clears 2 of 5. The ALB and `ecs_tasks` rules should be **accepted and documented, not tightened blind** |
| **SC-9 — CloudTrail AES256 vs CMK** | 0 (not CRITICAL) | `cloudtrail.tf:73`, `:194-217` | **Kept separate from the gate diagnosis, per the brief.** Real, documented in-file as SC-9, tracked separately. **It does not block the current gate.** Severity not changed, issue not closed |

Rule IDs and the ALB/egress characterisations are `PRIOR` from `PHASE14` §3.5-3.6; the per-file
counts are `CI` verified by me. **Authorization for any Terraform edit: ABSENT.** No file touched,
no severity lowered, no global suppression.

### 3.3 Bandit — the whole-tree parity defect

`PRIOR` `PHASE14` §3.1-3.3 reproduced the failure **locally and exactly** — bandit 1.9.4, same
command (`bandit -r backend_app -ll -f json`), exit 1, **9 results, 9 MEDIUM / 0 HIGH, 0 `nosec`
applied**. That is executed evidence, and I did not duplicate the run.

The findings are **9 × B608** in `backend_app/alembic/versions/`, each validating its interpolated
identifier through `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises) and
binding user-controlled values as parameters — **safe, but legitimately red because unsuppressed.**

**The structural defect, which is the important part.**
`tests/test_bandit_sast_gate_is_green.py` holds its premise two ways:

| Test | Scope | Proves |
|---|---|---|
| `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` | **one file** (`GATED_MODULE` = `transactional_execution_manager.py`) | Bandit silent **on that module** |
| `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | **whole tree** (`BACKEND_APP.rglob("*.py")`) | interpolations are *constrained* — AST-based, so **`# nosec` is invisible to it** |

CI scans the **whole tree**. The 9 alembic sites pass the AST test, never received the `# nosec B608`
the four original sites got, are scanned by CI, and are **invisible to the one-file Bandit test**.
**So the suite is green while the gate is red, and the suite cannot see it.**

**Remediation, specified not applied.** The fix that matters is broadening the gate test to run CI's
actual command (`-r backend_app`); adding the 9 `# nosec B608` with per-site reasons only clears
today's instance and would be *suppression without detection* — precisely what that test's own
docstring warns against. `-lll`, `|| true` and `--baseline` are ruled out by the same docstring and
are **not** proposed. **Authorization needed:** the 3 migration files +
`tests/test_bandit_sast_gate_is_green.py`. **ABSENT → nothing changed.**

---

## 4. Workstream D — frontend release gate: VERIFIED CRITICAL DEFECT

This is the new finding of this phase, and the most serious. All `SRC` + `CI`, verified by me.

### 4.1 The workflow has no gate

`.github/workflows/06-frontend-deploy.yml`:

```yaml
on:
  push:
    branches: [main, master]
    paths:
      - 'algo22-terminal/**'
      - '.github/workflows/06-frontend-deploy.yml'
jobs:
  deploy-frontend:
    name: Build & Deploy Frontend to S3 + CloudFront
    runs-on: ubuntu-latest
```

- **No `workflow_run`.** **No `needs:`** on the single job.
- It proceeds directly to publication: `aws s3 sync dist/ s3://vyomquant-frontend/ --delete` three
  times (`:289`, `:299`, `:307`, one per cache class) and
  `aws cloudfront create-invalidation --distribution-id EEOXECPHQ8SR0 --paths "/*"` (`:329`).
- `--delete` means a bad build can also **remove** live objects.

**Confirmed from current source, not from a prior report.** There is no correctness or security
precondition on any path that reaches S3 or CloudFront.

### 4.2 It published the commit the backend gate rejected

`CI` `gh run list --workflow=06-frontend-deploy.yml --limit 5`:

| `headSha` | Event | Conclusion |
|---|---|---|
| **`ecd23a9e`** | push | **success** |
| `6c5660a1`, `eee73265`, `1fb6ef3a`, `c60546c6` | push | success |

So at `ecd23a9e`: `05 Security` **failed twice**, `03 Deploy` **correctly refused and skipped ECS**,
and **`06 Frontend Deploy` succeeded and published.** The backend and frontend release paths have
**asymmetric enforcement**, and the asymmetry was exercised.

### 4.3 The sharpest consequence — the bundle secret sweep can never gate a publish

`SRC` The check that exists specifically to stop a secret reaching the shipped bundle is
`tests/test_no_secrets_in_bundle.py`, selected by the `frontend_bundle` marker. It lives in
**`01-pr-check.yml`**'s `frontend-tests` job (`:695`), which runs `npm run build` (`:794`) and then
`pytest tests/test_no_secrets_in_bundle.py -m frontend_bundle` (`:825`). The unit-tests lane
deliberately deselects it (`-m "not frontend_bundle"`, `:245`), and the workflow's own comment
(`:183-184`) explains the job "is additionally gated on changes under `algo22-terminal/**`, which is
the only way a secret can reach the bundle."

**But `01-pr-check.yml` triggers on `pull_request` only, and `06-frontend-deploy.yml` triggers on
`push` only.** The sweep's trigger and the publish's trigger are **mutually exclusive**. A direct
push to `main` touching `algo22-terminal/**` — exactly what `ecd23a9e` was — publishes the bundle to
production with the secret sweep having **never executed for that SHA**.

The reasoning in that comment is sound for the PR route and silently false for the push route. The
check is correctly written, correctly scoped, and unreachable on the path that actually ships.

### 4.4 Proposed fail-closed gate — NOT implemented

Required invariant: **the artifact's source SHA must equal the SHA for which every required
correctness and security check succeeded.** Shape: convert `06-frontend-deploy.yml` to
`workflow_run` on the required checks, or add a preflight job that queries the check runs for
`github.sha` and fails closed on **missing, failed, cancelled, skipped, stale, different-SHA, or
unqueryable** results — with `concurrency` to handle duplicate runs and reruns, and the publish
steps placed behind `needs:` that job.

Regression tests must prove publication is blocked in each invalid state, asserted against the
workflow definition. **No production publication would be used to validate it.** The existing
backend gate must be preserved, not reused blind — it keys on backend security checks that do not
cover the frontend bundle.

**Authorization: ABSENT** — `.github/workflows/06-frontend-deploy.yml` is a shared workflow file
with no established owner. **Nothing changed.** Given §4.2, this is the highest-priority grant to
issue.

---

## 5. Workstream E — backend deployment entry points

`PRIOR` + `CI` `03 Deploy` runs on `workflow_run` and **failed correctly** at `ecd23a9e` with the
ECS deploy **skipped** — the gate's fail-closed behaviour is demonstrated, not assumed.

**Not completed this phase, and not asserted:** whether `workflow_dispatch` can reach ECS with a
different SHA's successful security result. That requires reading `03-deploy.yml`'s dispatch input
handling and SHA selection end-to-end, which I did not finish. **I decline to claim coverage I did
not verify.** Recorded as the open item for Workstream E, with the test matrix the brief specifies
(normal `workflow_run`; dispatch with same-SHA evidence; dispatch with a different SHA's success;
missing/failed/cancelled/skipped security run; API failure; reusable paths). The manual deployment
capability must not be removed without authorization, and no broad override introduced.

---

## 6. Workstream F — PostgreSQL lane and TENANT-01

`CI` **Reverified:** `01-pr-check.yml` is `pull_request`-only, and there is **no run at
`ecd23a9e`**. Therefore `provisioning-and-isolation` — the drift gate, the PostgreSQL lane,
privilege hardening and the tenant-isolation steps — **has never executed for this commit.**
Committing the toolchain made the lane runnable, not run. **A runnable lane is not certification.**

`SRC` Static tenant-isolation findings, carried forward from `A23E15` §3 (files verified clean
against HEAD, so not re-derived):

- Nine RLS policies in `implement_rls_policies_20260817.py`, all comparing
  `current_setting('app.current_tenant_id', true)`; two use `::text` casts.
- `FORCE ROW LEVEL SECURITY` exists **only as prose inside a SQL comment**
  (`026_fee_records_row_level_security.sql:75`) — **zero executable statements**.
- `backend_app/core/dependencies.py` → **0** tenant-context references. Only two GUC setters exist,
  both off the request path.
- `database_pool.py:154` fallback DSN uses role `postgres`, the table owner ⇒ with FORCE absent,
  **all nine policies are inert on the ORM path**.
- `tenant_db_context.py` is present, correct and **unwired**; it uses `set_config(..., is_local=true)`
  inside an explicit transaction, which is the only pooler-safe form under the DSN's
  `pool_mode=transaction`. Its docstring records that the existing
  `text("SET LOCAL ... = :tenant_id")` form works **only** via psycopg2 client-side interpolation and
  **fails on asyncpg**.

**Per the brief: FORCE RLS must not be enabled as an isolated patch before the tenant-context path
is proven**, and session-level tenant state must not be introduced into pooled connections without a
proven cleanup strategy. Remediation order in `A23E15` §3.6 respects both.

**Authorization request, minimum necessary:** permission to open a PR against `main` for
`ecd23a9e` (which runs the lane in CI rather than locally), with confirmation that
`PROVISION_DATABASE_URL` resolves to an isolated throwaway service. `PRIOR` evidence in its favour:
`01-pr-check.yml:593` sets that variable to a **loopback** address. I did **not** dispatch and did
**not** invoke `provision_database.py` — it runs `--apply --create-substrate`, which is provisioning,
not a read. The "provisions twice" question was not investigated by running anything.

**TENANT-01 NOT CERTIFIED. PostgreSQL UNVERIFIED.**

---

## 7. Workstreams G–J — status, with prior figures treated as hypotheses

| WS | Item | Status this phase |
|---|---|---|
| **G** | Migration drift / snapshot dimensions | **Not recomputed.** `PRIOR` `PHASE16` §6 reports the baseline **demonstrably stale** — 11 migrations, 11 of 16 dimensions. Treated as a hypothesis, **not** re-derived here. Snapshot **not** regenerated; `AP-W1D-4` NOT GRANTED. `tests/test_drift_classification.py` left untouched (another session's uncommitted +143 lines) |
| **H** | Redis P1 / quota | **Unchanged and unverified this phase.** `PRIOR` 60 passed / 5 skipped (quota) and 18 passed / 2 xfailed (debounce) are **historical — not reproduced**, and the 5 skipped Redis integration tests are **not** described as passing. A23E6's form remains **12 ☐ empty / 0 marked / 6 blank fields**; all eight gates UNRESOLVED. `backend_app/backend/redis_manager.py` unchanged at blob `d7b0e811b615…`; `tests/test_a23_redis_pool_lifecycle.py` **ABSENT**. Strict xfail contract preserved |
| **I** | W-6 classifier | `SRC` ladder and both defects confirmed in a prior phase and unchanged. **Control-flow analysis stands as the key result:** `except InvalidSymbolError\|NetworkError\|AuthenticationError\|RateLimitError` → **0 matches**; `error_code ==` → **0**; `.retryable` read in exactly one log f-string (`:1687`); `replay_safe_transaction_guard._is_retryable_error()` re-derives independently. So this is **classification-only** today — and no runtime retry semantics may be changed as part of fixing it. Grant ABSENT |
| **I** | D7-1 key-vault mismatch | `SRC` Still present: signature is `load_decrypted_keys(self, user_id, exchange_id)`; **three** sites pass `access_token=` (`orders.py:789`, `orders.py:1095`, `market.py:50`); `exchange.py:993` is correct. Grant ABSENT. No credential or key material read or logged |
| **J** | Worker lifecycle | `SRC` `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`** — verified. `PRIOR` `ReconciliationScheduler` and `OrderWatchdog` are separately reported as running; I did **not** re-verify that distinction this phase and do not assert it. W-5 not begun (W-4 decision outstanding); no UNKNOWN exit semantics invented |

---

## 8. Tests and commands executed

**No test was executed this phase, and none is reported as passing.** Every implementation
workstream is grant-blocked, so there was no authorized change to validate; re-running unchanged
suites would manufacture activity. Skipped and expected-failure cases are carried as such, never as
passes.

Commands run, all read-only:

| Command | Purpose |
|---|---|
| `git rev-parse --short HEAD / origin/main`, `git rev-list --left-right --count`, `git reflog -1`, `git status --porcelain`, `git diff --cached --name-only`, `git diff --diff-filter=U`, `git worktree list` | §1 baseline; exit 0 |
| `gh run list --workflow=05-security.yml --branch main --limit 8 --json headSha,conclusion,event` | §2 — 2 failures at `ecd23a9e`, 6 successes at `074b772a` |
| `gh run list --workflow=03-deploy.yml --branch main --limit 4 --json …` | §2 — failure at `ecd23a9e` |
| `gh run list --workflow=06-frontend-deploy.yml --limit 5 --json …` | §4.2 — **success at `ecd23a9e`** |
| `gh run view 38132697285 --json jobs` | §2 — Bandit + Trivy IaC failing, Container passing |
| `gh run view 38132697285 --log-failed` + filters | §3.1 per-file CRITICAL counts |
| `Select-String` / `read_file` on the three workflows, `terraform/cloudtrail.tf`, tenant-context sources | §3, §4, §6 |

**Environment limitations:** Trivy and the SARIF artifact were not available locally, so individual
Trivy **rule IDs** are `PRIOR` rather than independently verified — the per-file counts are mine.
No PostgreSQL, no Redis, no AWS, no sandbox, no production access.

---

## 9. Remaining blockers, ranked

| # | Blocker | Owner | Smallest next action |
|---|---|---|---|
| **1** | **Frontend publishes with no gate; did so at `ecd23a9e`** (§4) | workflow / release owner | Grant `.github/workflows/06-frontend-deploy.yml` + a gate regression test. Interim mitigation available immediately: **stop pushing `algo22-terminal/**` directly to `main`; route it through a PR**, which makes `01-pr-check.yml`'s secret sweep run |
| **2** | `05 Security` red; backend deploy blocked (§3) | infrastructure owner (Trivy) + code owner (Bandit) | Two grants: the Redis egress tightening (clears 2 of 5) + ALB acceptance decision; and the 3 migrations + `test_bandit_sast_gate_is_green.py` |
| **3** | **TENANT-01** — nine inert policies, owner-role connection, context unwired (§6) | tenancy + DB owner | Decide non-owner ORM role **or** per-table FORCE, after wiring context |
| **4** | PostgreSQL lane never ran at this SHA (§6) | CI owner | Open a PR against `main` for `ecd23a9e`; confirm isolated `PROVISION_DATABASE_URL` |
| **5** | Backend `workflow_dispatch` SHA coverage unverified (§5) | release owner | Finish the `03-deploy.yml` dispatch-path trace |
| **6** | D7-2 portfolio cache dead behind false-green health | portfolio-cache + credential owner | Pick among the three recorded options |
| **7** | D7-1 three `access_token=` sites | router owner | Grant the 3 deletions + `Signature.bind` test |
| **8** | W-6 classifier precedence | execution-state + coordinator | Grant the reorder + tighten `"key"`, classification-only |
| **9** | Workers absent from entrypoint | service/deployment owner | Wire with leader election |
| **10** | A23 Redis P1 (8 gates) | project decision-maker | Assign the owner role, then complete A23E6 §3 |

---

## 10. Disposition

**The gate was not weakened, and `main` was not made deployable.** Both remain correct outcomes:
the code and evidence do not yet satisfy the gate, and the gate is right to refuse.

This phase's substantive contribution is Workstream D — a verified, previously unexamined release
path that publishes to production S3 and CloudFront with **no correctness or security precondition**,
whose bypass is already realised at the current HEAD, and whose dedicated bundle secret sweep is
structurally unreachable on the push route. It is now blocker 1, ahead of the red security gate,
because the backend gate is holding while this one is not.

One correction to my own prior work is restated in §3.1: `cloudtrail.tf` contributes **zero**
CRITICAL findings, and my earlier attribution to it was wrong.

**LIVE TRADING OFF / NOT CERTIFIED · TENANT-01 NOT CERTIFIED · PostgreSQL UNVERIFIED · backend
deployment blocked while required security checks fail.**
