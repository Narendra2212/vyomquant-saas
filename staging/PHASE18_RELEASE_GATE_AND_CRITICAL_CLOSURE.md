# Phase 18 — Close Frontend Release Bypass, Enforce Same-SHA Gates, Parallel Critical Closure

**Destination verified ABSENT immediately before writing.** **0 production files · 0 test files ·
0 workflows · 0 Terraform · 0 migrations · 0 registers or ledgers changed. 0 CI runs dispatched.
No stage, commit, push, reset, stash, clean, revert, deploy, provisioning, or database mutation.**

**LIVE TRADING: OFF / NOT CERTIFIED · TENANT-01: NOT CERTIFIED · PostgreSQL: UNVERIFIED.**

---

## 1. Executive summary and release-risk statement

Four results, two of them corrections to prior phases including my own.

| # | Result |
|---|---|
| 1 | **The frontend publish path is still ungated — re-verified at current HEAD.** `06-frontend-deploy.yml` is clean, 615 lines, **one** job, `needs:` = 0, `workflow_run` = 0, and **zero references to `scripts/require_security_scan.py`** — a fail-closed same-SHA gate that already exists in this repository and is already used by the backend |
| 2 | **CORRECTION to my Phase 16: `03-deploy.yml:73` is NOT a dispatch bypass.** On `workflow_dispatch` the validation job *runs* and still executes the security gate. The backend is correctly gated on **every** reachable path. The only override is the narrow, logged `allow_unscanned` input, which **cannot** bypass a *failed* scan — only a *missing* one |
| 3 | **The Bandit gate/test divergence is now demonstrated by execution, not inferred.** In the same tree at the same moment: `tests/test_bandit_sast_gate_is_green.py` → **3 passed**; the exact CI command → **exit 1, 9 B608 MEDIUM findings**. The suite is green while the gate is red |
| 4 | **This bypass class has already caused a production security incident here.** `require_security_scan.py`'s own docstring records that **PyJWT 2.13.0 (CVE-2026-102268, a key-confusion signature bypass allowing token forgery) shipped to production while `05 Security` was red.** That gate was then built for the backend. The frontend was never given one |

**Release risk: the frontend is the open instance of a bypass class this repository has already been
burned by, and the remedy already exists in-tree, unused.**

**No implementation grant exists.** Ledger `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804`
reads `NOT GRANTED` on all five rows; no ledger row authorizes any workflow, Terraform, migration,
test or source change in this phase. Per the brief's §1.7–1.8 I therefore implemented nothing,
completed every read-only and server-free workstream, and recorded exact blockers.

---

## 2. Preservation manifest — before and after identical

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **unchanged — no commit, reset or checkout** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Untracked | 73 | 75 (+2, both mine, both `staging/`) |
| Worktrees | 1 | 1 |

**File manifest — every governed target verified `clean` against HEAD, entry and exit:**

| Hash (16) | Path |
|---|---|
| `285cc2d19b2b77ac` | `.github/workflows/06-frontend-deploy.yml` |
| `eb8078eac0626844` | `.github/workflows/03-deploy.yml` |
| `e2d9ccc6f951d39d` | `.github/workflows/05-security.yml` |
| `91d55a379115c88e` | `.github/workflows/01-pr-check.yml` |
| `a1c8dd2d48fbc744` | `scripts/require_security_scan.py` |
| `a7deff99c9e54bee` | `tests/test_bandit_sast_gate_is_green.py` |
| `cdead9289f209c58` | `terraform/alb.tf` |
| `87c9d131051d5e51` | `terraform/security_groups.tf` |
| `48b9471035b77d4f` | `terraform/redis-only/main.tf` |
| `e6069946039186ff` | `staging/provisioning_snapshot.json` — **baseline NOT regenerated** |
| `df4fd42a15101512` | `backend_app/backend/exchange_executor.py` |
| `74a0059411467442` | `backend_app/backend/api_key_vault.py` |
| `d7b0e811b615ee13` | `backend_app/backend/redis_manager.py` |
| `a3849d4acd7afcf4` | `backend_app/main.py` |
| `7e238620bc22f0ad` | `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` — `:802` still `NOT GRANTED` |

**Concurrent-session work preserved byte-for-byte, not touched, not staged, not reverted:**

```
 M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md
 M tests/test_drift_classification.py      (worktree 99a2917a…, 808 lines vs HEAD 665)
```

Also noted and left alone: concurrent reports `PHASE15_SECURITY_GATE_CLOSURE_AND_MAIN_VERIFICATION.md`
(20:17), `PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md` (20:14), `PHASE11B_A23E17_…` (20:15).
**Dispositive check: no tracked source, workflow, Terraform or migration file is modified** — so no
concurrent session has implemented any gate fix either.

---

## 3. Deployment trigger graphs (current source)

```
FRONTEND — FAIL-OPEN
  push to main (paths: algo22-terminal/**, .github/workflows/06-frontend-deploy.yml)
  workflow_dispatch
        │
        ▼
  deploy-frontend            ← the ONLY job. needs: 0. workflow_run: 0.
        │                      require_security_scan refs: 0
        ├─ Build frontend
        ├─ Reject implausible release artifacts in dist/      (artifact shape only)
        ├─ Sync build artifacts to S3            ◄── PUBLISHES
        ├─ Invalidate CloudFront cache           ◄── SERVES IT
        └─ Verify the WebSocket upgrade reaches the origin

BACKEND — FAIL-CLOSED (correct)
  workflow_run: ["02 Build"] completed, branches [main, master]
  workflow_dispatch (input: allow_unscanned, boolean, default false)
        │
        ▼
  pre-deployment-validation
     :73 if  workflow_run.conclusion == 'success'  OR  event == workflow_dispatch
     :90 step "Require the security scan to have passed for this commit"
            DEPLOY_SHA      = github.event.workflow_run.head_sha || github.sha
            ALLOW_UNSCANNED = inputs.allow_unscanned
            run: python scripts/require_security_scan.py      ◄── RUNS ON BOTH PATHS
        │
        ▼
  ecs-deployment   needs: pre-deployment-validation
        IMAGE_TAG = github.event.workflow_run.head_sha || github.sha   ← same SHA as the gate

CORRECTNESS — STRUCTURALLY UNREACHABLE FROM A PUSH
  01 PR Check: on: pull_request ONLY   (workflow_dispatch: 0, push: 0)
     └─ frontend-tests  "Frontend Build & Unit Tests" (:695, vitest, ~150 files)
        and itself conditional on steps.changes.outputs.frontend == 'true'
```

---

## 4. Exact-SHA gate: design, bypass analysis, implementation status

### 4.1 Correction — the backend has no dispatch bypass

My Phase 16 §4.4 asserted that `03-deploy.yml:73` "admits dispatch unconditionally … a pre-existing
hole." **That was wrong, and the error mattered**, because it mischaracterized a working control as
a defect.

Line 73 is the `if:` that decides whether the **validation job runs at all**. On
`workflow_dispatch` the job *does* run, and inside it `:90` still executes
`require_security_scan.py`. `ecs-deployment` then `needs: pre-deployment-validation`. So dispatch
reaches the gate rather than skipping it.

The genuine, deliberate override is the `allow_unscanned` boolean input (`:10-16`, default
`false`). Read at source in `require_security_scan.py`, it is consulted **only** inside the
`if not runs:` branch — i.e. when *no* `05 Security` run exists for the SHA. A run that concluded
`failure`, `cancelled` or `skipped` returns 1 **regardless of the override**.

> **`allow_unscanned` can deploy an *unscanned* commit. It cannot deploy a *failed* one.**

That is a narrow, documented, self-logging escape hatch ("This override is recorded here on
purpose"), exactly what the brief §4.6 says to preserve. **Workstream B needs no change.**

### 4.2 The existing gate already satisfies most of §3.A2

`scripts/require_security_scan.py` (`a1c8dd2d…`), read in full:

| Required behaviour | Actual implementation |
|---|---|
| Canonical SHA capture | `DEPLOY_SHA` env, queried as `?head_sha=<sha>` — **exact pin, never branch or "latest"** |
| How results are consumed | `GET /repos/{repo}/actions/runs?head_sha=…&per_page=100`, filtered to `name == "05 Security"` |
| `success` | `return 0` |
| `failure` | `return 1` with the run URL |
| `cancelled` / `skipped` | `return 1` — *"nothing was verified"* |
| **Missing run** | `return 1` — *"Nobody scanned this commit, which is not evidence that it is safe"* |
| Still running | polls every 20s up to `SECURITY_GATE_WAIT_SECONDS` (default **900**), then `return 1` — *"A deploy that outruns its own security scan is an ungated deploy"* |
| **API error** | `HTTPError` → `return 1` — *"refusing rather than guessing"* |
| Missing token/repo/SHA | `return 1` |
| **Duplicate runs for one SHA** | `sorted(runs, key=run_started_at)[-1]` — **deterministic: newest start wins** |
| Required token scope | `permissions: actions: read` declared explicitly at `03-deploy.yml`, with the 403 incident documented in-file |

**This is a correctly fail-closed, same-SHA gate.** The frontend fix is therefore mostly *wiring*,
not design.

### 4.3 The one gap the existing script does not close

The script gates on **`05 Security` only**. It does not consider `01 PR Check`. And `01 PR Check` is
`pull_request`-only — **verified at current HEAD: `workflow_dispatch` = 0, `push` = 0.**

So for a commit that reaches `main` by direct push:

- security evidence **is** obtainable (`05 Security` runs on push), and
- correctness evidence is **structurally unobtainable** — the job that runs vitest
  (`01-pr-check.yml:695 frontend-tests`) cannot fire on a push, and is additionally conditioned on
  `steps.changes.outputs.frontend == 'true'`.

**Consequence for the design: the frontend gate splits into two parts with different costs.**

| Part | Change | Closes |
|---|---|---|
| **G1 — security gate (small, mechanical)** | Add a `security-gate` job to `06-frontend-deploy.yml` running `require_security_scan.py` with `DEPLOY_SHA: ${{ github.sha }}`, `ALLOW_UNSCANNED: 'false'`, plus `permissions: actions: read`; make `deploy-frontend` `needs: security-gate` | The already-realized bypass class (PyJWT precedent). **Reuses a verified mechanism, invents nothing** |
| **G2 — correctness gate (a process decision, not just YAML)** | Either (a) extend `01 PR Check` to run on `push: branches: [main]`, or (b) require that `main` only advance through PRs via branch protection, or (c) add a frontend-correctness job to a push-triggered workflow | The structural gap in §4.3. Each option has consequences beyond this file and needs an owner |

**G1 must not be presented as closing correctness.** Gating the frontend on `05 Security` alone
would make it *as* safe as the backend, not fully validated.

### 4.4 Residual bypass analysis for the proposed G1

| Vector | Disposition under G1 |
|---|---|
| `workflow_dispatch` on the frontend workflow | **Must pass the same gate** — put the gate in a job that `deploy-frontend` `needs:`, not in a trigger condition. `ALLOW_UNSCANNED` hardcoded `'false'` so no input can relax it |
| Stale success for a different SHA | Impossible — `?head_sha=${{ github.sha }}` |
| `05 Security` skipped by a path filter | Already handled: `skipped` → refuse |
| Publish in an earlier job | **Verified impossible today**: there is exactly **one** job, and `Sync build artifacts to S3` / `Invalidate CloudFront cache` are the only publishing steps. A gate job placed before it has no alternate path to bypass |
| Forked PR | Not applicable — the frontend workflow has no `pull_request` trigger |
| `workflow_run` trust boundary | **Deliberately avoided.** G1 keeps the `push` trigger and adds an in-workflow gate, so no privileged `workflow_run` context is introduced |

### 4.5 Implementation status

**NOT IMPLEMENTED — grant ABSENT.** Required: an explicit grant for
`.github/workflows/06-frontend-deploy.yml` plus a new test file. Ledger rows `:800-804` are all
`NOT GRANTED` and no row covers workflows. Copy-ready request in §16.

### 4.6 Proposed regression tests (not written — grant absent)

The repository already has a workflow-policy test convention — `tests/test_nightly_audit_workflow.py`
reads the workflow with `pathlib.Path(...).read_text(encoding="utf-8")` and asserts on content. The
proposed `tests/test_frontend_deploy_is_gated.py` would follow it exactly:

| # | Assertion |
|---|---|
| 1 | `06-frontend-deploy.yml` contains a job invoking `require_security_scan.py` |
| 2 | The publishing job declares `needs:` on that gate job |
| 3 | `DEPLOY_SHA` is `${{ github.sha }}` — not a branch, not `latest` |
| 4 | `ALLOW_UNSCANNED` is literal `'false'` and is not wired to any input |
| 5 | `permissions:` includes `actions: read` |
| 6 | No `aws s3 sync` or `cloudfront create-invalidation` appears in any job lacking the gate dependency |
| 7 | `03-deploy.yml` still routes dispatch through the gate (**regression lock for the §4.1 correction**) |

State-handling cases (success / failure / missing / cancelled / skipped / API error / wrong SHA /
duplicate runs) are **already implemented and fail-closed in the script**; they are best covered by
unit tests against `require_security_scan.py` with a stubbed `_api`, which is a second, separable
test file. **No test asserts any of this today** — including for the backend.

---

## 5. Security workflow results for the exact tested commit

All rows below are for `headSha = ecd23a9e934ec58a7b1f0e5db638da8e865e8187`.

| Workflow | Run ID | Event | Conclusion |
|---|---|---|---|
| `02 Build` | 38129699471 | push | success |
| **`06 Frontend Deploy`** | **38129699497** | push | **success — PUBLISHED to S3 + CloudFront** |
| `07 Desktop Build` | 38129699516 | push | success |
| **`05 Security`** | **38129699454** | push | **failure** |
| **`05 Security`** | 38132697285 | schedule | **failure (retry)** |
| **`03 Deploy`** | **38130273012** | workflow_run | **failure — ECS deploy SKIPPED** |
| **`01 PR Check`** | — | — | **NOT RUN at this SHA** |

`05 Security` on `main`: **2 failures** at `ecd23a9e`; **6 consecutive successes** at parent
`074b772a`. **`NOT RUN` is not a pass.** No result from any other SHA is attributed to this one.

---

## 6. Trivy finding-by-finding disposition

Scanner: `trivy-action@v0.36.0`, `scan-ref: terraform`, `scanners: misconfig,secret`,
`severity: CRITICAL`, `exit-code: 1`. **5 CRITICAL, 2 rules, 0 secret findings.** Trivy is not
installed locally and `terraform plan` needs AWS credentials, so these are not independently
re-scanned this phase; the Terraform source was re-read and all three files are `clean`.

| # | Rule | Resource · site | Disposition | Evidence |
|---|---|---|---|---|
| 1 | `AWS-0054` | `aws_lb_listener.http` · `alb.tf:52` | **Justified false positive — do not suppress blind** | `:52 protocol="HTTP"`, `:55/:70` mutually exclusive `dynamic default_action`, `:61 HTTP_301`, and a `lifecycle.precondition` refusing the apply for `prod`/`production` without `enable_custom_domain`. Trivy cannot evaluate `dynamic` blocks or preconditions. **Exploitability in prod: none — the apply is refused** |
| 2 | `AWS-0104` | `aws_security_group.alb` · `security_groups.tf:26` | **Accept + document** | conventional; ALB forwards to targets |
| 3 | `AWS-0104` | `aws_security_group.ecs_tasks` · `security_groups.tf:56` | **Accept + document — do NOT tighten blind** | load-bearing: tasks must reach ECR, Secrets Manager, Supabase, venues. Tightening requires VPC endpoints + a venue allowlist, i.e. a dependency map this phase does not have |
| 4 | `AWS-0104` | `aws_security_group.redis` · `security_groups.tf:77` | **FIX — safe** | ElastiCache initiates no outbound connections; ingress already restricted to 6379 from `ecs_tasks` |
| 5 | `AWS-0104` | `aws_security_group.redis` · `redis-only/main.tf:62` | **FIX — safe** | same reasoning, standalone stack |

`cidr_blocks = ["0.0.0.0/0"]` also appears at `security_groups.tf:11` and `:19`; those are **ALB
ingress** on 80/443, correct for a public load balancer, and are **not** among the findings.

**Why the gate flipped:** `ecd23a9e` committed **4 `terraform/` files**. Before it the blocking IaC
scan had no `terraform/` tree to evaluate. **The misconfigurations are not new; their visibility to
the gate is.**

**Status: unresolved, 0 of 5 fixed, infrastructure grant ABSENT.** Fixing #4 and #5 clears 2 of 5 at
near-zero risk. No CRITICAL suppressed, no `.trivyignore` created, no threshold lowered, no
`terraform apply`, no AWS mutation.

---

## 7. Bandit — whole-tree parity demonstrated by execution

### 7.1 The divergence, measured in one sitting

| Command | Result |
|---|---|
| `pytest tests/test_bandit_sast_gate_is_green.py -q` | **3 passed in 112.92s**, exit **0** |
| `python -m bandit -r backend_app -ll -f json -o staging/p18_bandit_citest.json` | **exit 1** — `9 results, B608×9, MEDIUM×9` |

`bandit 1.9.4`, matching CI's `bandit-1.9.4`. **Same tree, same moment: the repository's own gate
test is green while the exact CI command is red.** This is no longer an inference.

### 7.2 All nine findings, from my own JSON output

| File (`backend_app/alembic/versions/`) | Lines |
|---|---|
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | **212, 222** |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | **224, 262** |
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | **193, 198, 209, 467, 475** |

All `B608`, all `MEDIUM`. **Disposition: nine false positives on safety grounds, legitimately red.**

Safety proven at source, not assumed — `d4e5f6a7b8c9…:155-157`:

```python
if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
    return s
raise ValueError(f"not a SQL identifier: {s!r}")
```

**A strict allowlist that raises rather than sanitising.** No quote, semicolon, whitespace,
parenthesis or comment marker survives it. `_validate_table_name` is referenced **21 / 5 / 16** times
across the three files, so it governs rather than decorates. The only user-influenced value at these
sites is bound as a parameter (`{"re": UUID_REGEX}`). SQL identifiers cannot be bound parameters, so
validated interpolation is the correct construction — precisely the shape B608 cannot distinguish.
`nosec` count in all three files: **0**, so they are genuinely unsuppressed.

### 7.3 Root cause: the test's scope is narrower than the gate's

| Test | Scope | Proves | Blind to |
|---|---|---|---|
| `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` | **one file** (`transactional_execution_manager.py`) | Bandit silent **on that module** | every other file CI scans |
| `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | whole tree, AST | interpolations are **constrained** | `# nosec`, and Bandit's own verdict |

The nine alembic sites **pass** the AST test — `SQL_SAFE_VALIDATORS` already contains
`_validate_table_name` — and are **invisible** to the one-file Bandit test.

### 7.4 Correction to a prior phase, and a new observation

Phase 14 reported the stale suppression as `B104` at `backend_app/ws_server.py:72`. **My run reports
`B608` at `backend_app/test_perf.py:26`** — *"nosec encountered (B608), but no failed test on
file"*. Different rule, different file, different line. Mine is the current measurement; both may
have existed, but I report only what I observed.

### 7.5 Disposition

**Unresolved; grant ABSENT.** Proposed, in dependency order:

1. **P-B2 first** — widen the gate test to run CI's actual command (`-r backend_app`), marked slow;
   measured cost ≈ 76s for the scan itself.
2. **P-B1 second** — `# nosec B608` with per-site reasons on the nine lines, matching the existing
   `transactional_execution_manager.py` precedent.
3. **P-B3** — remove the stale `# nosec` at `backend_app/test_perf.py:26`.

**P-B1 is not acceptable without P-B2:** suppression without detection is what the gate test's own
docstring warns against. A structural refactor is *not* preferable here — identifiers cannot be
parameterized, so the validator-plus-interpolation shape is already the correct construction.
**No threshold lowered, no exclusion broadened, no `-lll`, no `|| true`, no `--baseline`.**

---

## 8. Migration-to-dimension map — recalculated from migration operations

Recomputed this phase by classifying DDL in each file; counts were not inherited. Snapshot
`e6069946…`, 16 keys, no `_provenance`, captured **10-06 20:14:20**. `verify_schema_drift.py:81`
points `DEFAULT_BASELINE` at it, `:334` loads it, and `:212`/`:367` iterate `SNAPSHOT_QUERIES` —
never the JSON's keys. A search for `provenance` in that file returns **0 matches**, so **missing
`_provenance` is metadata only and cannot fail the gate.**

| Migration | Dimensions it can affect |
|---|---|
| `022_fix_library_subscriptions_updated_at` | columns, unique_and_check |
| `023_remediate_privilege_escalation` | columns, unique_and_check, **policies, privileges** |
| `024_remediate_referral_double_commission` | columns, primary_keys, unique_and_check, indexes, **privileges**, functions |
| `025_work03_bot_ownership_claims` | tables, columns, primary_keys, unique_and_check, indexes, **row_level_security, policies, privileges** |
| `026_fee_records_row_level_security` | tables, columns, primary_keys, foreign_keys, unique_and_check, indexes, **row_level_security, policies, privileges**, enum_types |
| `027_referral_ledger_currency` | columns, unique_and_check |
| `028_safety_control_audit` | tables, columns, primary_keys, foreign_keys, unique_and_check, indexes, **row_level_security, policies, privileges** |
| `029_referral_obligations` | tables, columns, primary_keys, foreign_keys, unique_and_check, indexes, **row_level_security, policies, privileges**, functions |
| `030_execution_venue_order_id` | columns, foreign_keys, indexes |
| `031_fee_records_fee_pct_nullable` | columns |
| `032_referral_payouts_terminal_evidence` | unique_and_check |

**Verified aggregate — 11 of 16 dimensions.** The brief's hypothesis is confirmed on every count I
could check: `columns` **10 of 11** ✔ · `unique_and_check` **9** ✔ · `privileges` **6** ✔ ·
`policies` **5** ✔. Also measured: indexes 6, primary_keys 5, tables/foreign_keys/row_level_security
4 each, functions 2, enum_types 1, **extensions/sequences/triggers 0**.

**One honest caveat:** the classification is pattern-based over SQL text. `triggers` scoring 0
deserves a second look, since `022` concerns an `updated_at` column — a behaviour often implemented
with a trigger. I did not confirm it either way.

**Alteration-vs-creation split, which is the classifier discriminator:** 022, 023, 027, 030, 031,
032 alter *existing* objects and should surface as **`MODIFIED`**. If they instead appear as
missing+added pairs, the classifier is at fault, not the baseline.

**Server-free classifier evidence obtained:** `140 passed in 2.23s`, exit 0
(`test_drift_classification.py` + `test_snapshot_dimension_invariants.py`). These assert the
4-way classification including the modified-not-missing-plus-added property on synthetic data.
**No test yet drives the map itself** — that would be the new test the brief §7 asks for, and it
needs a grant for `tests/`.

**Static analysis only. No drift run has occurred.** Baseline preserved at `e6069946…`; no
`provision_database.py`, no `DROP`/`CREATE DATABASE`, no severity change. Recapture is `AP-W1D-4`
(owner *DB owner + code owner*), **NOT GRANTED**.

---

## 9. PostgreSQL / CI authorization status

**UNVERIFIED. No run dispatched.**

`01-pr-check.yml` at current HEAD: **`workflow_dispatch` = 0, `push` = 0** — `pull_request`-only, so
there is nothing to dispatch. The only route is a PR, and `main` already carries the SHA, so it
needs a branch plus a PR. That route runs
`provision_database.py --apply --create-substrate` **twice**, which the brief classifies as
infrastructure provisioning requiring recorded authorization. **None exists, so none was triggered.**

Isolation evidence gathered for whoever authorizes it: `PROVISION_DATABASE_URL` is a **loopback**
job-scoped service (`…@localhost:5432/postgres`, `:593`); `--i-understand-this-is-remote` is
**deliberately withheld** so a non-loopback URL fails rather than proceeds;
`AERORA_TEST_DATABASE_URL` is set (`:592`) so PostgreSQL-gated suites would un-skip.
**Establishing isolation is not permission to run.**

The double provisioning appears **intentional** — the second invocation is labelled an idempotency
regression guard for a defect where a substrate `GRANT ALL` re-granted 787 privilege rows on the
second run while migrations' own `REVOKE`s were correctly skipped, leaving run 2 *less* secure than
run 1. **It should not be "optimized away."** That answers the brief's §8.2/§8.4: the smallest safe
change is **none**.

Historical lane figures (**11 passed, 228 skipped, 1 xfailed**) are retained as historical only.
**228 skips are not passes.**

---

## 10. Redis and quota — current measured results

| Suite | Result | Exit |
|---|---|---|
| 5 quota suites | **60 passed, 5 skipped** in 41.62s | 0 |
| `tests/test_redis_debounce.py` | **9 passed, 1 xfailed** in 1.21s | 0 |

**Discrepancy recorded rather than reconciled silently:** the brief cites "18 passed / 2 xfailed" for
Redis debounce. My measurement at this HEAD is **9 passed / 1 xfailed**, which matches A23E14's
record. I report what I measured and did not convert any xfail into a pass.

- **QUOTA-01 OPEN.** `backend_app/core/subscription_engine.py` is committed and clean. The 5 skips
  require real Redis: locally `AERORA_TEST_REDIS_URL`/`REDIS_URL`/`REDIS_HOST`/`REDIS_PORT` are
  unset and 6379 is unreachable. CI **does** provision `redis:7-alpine`, job-exclusive and
  ephemeral, with the committed `/15`→`/0` fix. **Real-Redis lane UNEXECUTED at this SHA.** No URL
  fabricated, no service started, no fixture altered, no enforcement weakened.
- **A23 Redis P1 NOT STARTED — 0 of 8 gates.** A23E6 form clean and still blank (12 ☐ / 0 marked /
  6 blank fields). `redis_manager.py` clean `d7b0e811…`; `tests/test_a23_redis_pool_lifecycle.py`
  **ABSENT**. Per A23E14 §3, W1D-12's in-session grant worked because the `Code owner` role
  **pre-existed** in ledger `:802`'s Owner column; **A23 has no such row**, so the role row must be
  established first and the grant issued second. `test_r6_d` remains `XFAIL(strict)`, untouched.

---

## 11. W-6 and D7-1

| WS | State | Authorization |
|---|---|---|
| **W-6** | `exchange_executor.py` clean `df4fd42a…`. `_handle_ccxt_error` `:1225-1257` is an ordered substring ladder: symbol/market at `:1246` **precedes** network/timeout at `:1249`, so a timeout naming a symbol — or a message quoting a `/market/` path — classifies as `InvalidSymbolError`. Bare `"key"` at `:1252` matches `monkey`, `keyspace`, `keyword`. **Repository-wide, the only production read of `ExchangeError.retryable` is the log line at `:1687`** — so this is **misclassification and a corrupted retry contract, not demonstrated retry suppression**, and I make no retry-suppression claim | **ABSENT** — no edit, no test added |
| **D7-1** | `api_key_vault.py` clean `74a00594…`. `load_decrypted_keys(self, user_id, exchange_id)` takes two positionals. **Three** sites pass `access_token=`: `orders.py:789-793` (unguarded → 500), `orders.py:1095-1099` (masked → `[]`), `market.py:50-54` (masked → **authenticated users served anonymous market data**). `exchange.py:993-996` is correct, so the count is three. Masked by `tests/test_market_none_keys_fix.py:13`, whose double declares `access_token=None` — matching the broken callers while the real class does not. Git history shows **one signature form ever**, with method and broken callers entering in the same commit `6c5660a1`, so deleting the kwarg **loses no behaviour** | **ABSENT** — no source or test edited |

The market-data policy question (missing keys vs vault outage) is kept **separate** and unaddressed,
per the brief.

---

## 12. TENANT-01 — static evidence only

`VER` this phase: **`FORCE ROW LEVEL SECURITY` has 0 matches** in `backend_app/migrations/*.sql` and
`backend_app/alembic/versions/*.py` outside comments. It exists as prose in reports and as a comment
at `026_fee_records_row_level_security.sql:75`.

- `tenant_db_context.py` is **unwired** — no references from `core/dependencies.py`, `main.py` or
  any router.
- The only GUC write in the application is an inline `SET LOCAL app.current_tenant_id` at
  `transactional_execution_manager.py:225` — one module doing it directly, not the shared helper.
  `SET LOCAL` is transaction-scoped, which is the correct primitive for a pooled connection; but one
  call site does not establish repository-wide propagation.
- Prior records (not re-verified here): the app connects as owner `postgres` with `rolbypassrls`,
  live catalogue showed `relrowsecurity = true, relforcerowsecurity = false`, and the scorecard
  carries `tenant_force_rls: OPEN`.

**Separation the brief requires:** schema-level control = static fact; **isolated integration
evidence = NONE** (the four tenant lane files are 92 local skips and have never run in CI);
**production evidence = NONE** — no connection made, none sought.

**`FORCE ROW LEVEL SECURITY` was not enabled, correctly** — doing so before proving context
propagation would convert a context gap into empty result sets or broken workflows.
**TENANT-01 NOT CERTIFIED.**

---

## 13. Worker lifecycle — corrected framing retained

`backend_app/main.py` clean `a3849d4a…`.

| Component | State |
|---|---|
| `ReconciliationScheduler` | **STARTED** — `main.py:456` |
| `OrderWatchdog` | **STARTED** — `main.py:432` (interval 30s / stale 60s / alert 300s) |
| `ExchangeReconciliationService` | **NOT started** — no reference in `main.py`; two guard tests assert exactly this and pass |
| `MarketplaceExpiryWorker` / `expiry_sweep` | **NOT started** — 0 matches in `main.py` |
| Portfolio cache | **silently dead** — `portfolio_cache_updater.py:151` imports `backend_app.backend.key_manager`, which **does not exist**; the `ImportError` is swallowed per user while `/health/services` reports `ACTIVE` — a **false-green** signal |

The earlier framing that "no reconciliation component runs" was wrong; the **scheduler** does.
**W-5 was not implemented and no `UNKNOWN` exit semantics were inferred** — W-4's decision is
absent, and `main.py` has three contending workstreams with no coordinator. **No edit.**

---

## 14. Files changed and authorizing grants

**Changed: 2 artifacts, both new, both untracked, both under `staging/`:**

| File | Grant |
|---|---|
| `staging/PHASE18_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` | this report |
| `staging/p18_bandit_citest.json` | Bandit output, deliberately written to `staging/` so it cannot collide with CI's `reports/` path |

`reports/` is a **pre-existing, tracked** directory (in HEAD, contents dating to 08-14). I neither
created nor wrote into it; my Bandit JSON landed in `staging/` at 20:29:50.

**Authorizations exercised: none.** Every governed file in §2's manifest is `clean` at exit. No
approval was inferred from this brief's framing, from the commit or its push, from the frontend
deploy's success, from passing local tests, from the existence of `require_security_scan.py`, from
role labels, from commit authorship, or from silence.

---

## 15. Commands run, exit codes, limitations

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status` / `reflog` / `ls-files` / `worktree list` / `hash-object` ×15 | 0 | §2 manifest |
| `git ls-tree HEAD -- scripts/require_security_scan.py` | 0 | **present in HEAD** |
| Read `require_security_scan.py`, `03-deploy.yml:1-135`, `06-frontend-deploy.yml`, `01-pr-check.yml` | 0 | §3, §4 |
| `pytest tests/test_bandit_sast_gate_is_green.py -q` | **0** | **3 passed in 112.92s** |
| `python -m bandit -r backend_app -ll -f json -o staging/p18_bandit_citest.json` | **1** | **9 results, B608×9, MEDIUM×9** |
| `pytest` 5 quota suites | 0 | **60 passed, 5 skipped** in 41.62s |
| `pytest tests/test_redis_debounce.py` | 0 | **9 passed, 1 xfailed** in 1.21s |
| `pytest test_drift_classification + test_snapshot_dimension_invariants` | 0 | **140 passed** in 2.23s |
| Migration DDL classification over 11 files | 0 | §8 map |
| `Select-String` FORCE RLS over migrations + alembic | 0 | **0 matches** |

**Environmental limitations:** no PostgreSQL (all four DSN env vars unset); no Redis (6379
unreachable); **no Trivy installed**; no AWS credentials; no venue credentials; no production
access; `01 PR Check` undispatchable. **No credential, DSN or secret value appears in this report or
in any log I produced.**

---

## 16. Unresolved blockers, owners, and smallest next actions

| # | Blocker | Owner | Smallest next action |
|---|---|---|---|
| **1** | **Frontend publishes with no security gate** — the realized bypass class | workflow / release owner | Grant G1 (§4.3). *"I authorize adding a `security-gate` job to `.github/workflows/06-frontend-deploy.yml` that runs `scripts/require_security_scan.py` with `DEPLOY_SHA: ${{ github.sha }}` and `ALLOW_UNSCANNED: 'false'`, adds `permissions: actions: read`, and makes `deploy-frontend` depend on it; plus `tests/test_frontend_deploy_is_gated.py` with the §4.6 assertions. No other workflow change."* |
| **2** | **Frontend correctness structurally unreachable on push** | coordinator + release owner | Choose G2 (§4.3): extend `01 PR Check` to `push: [main]`, **or** require PR-only advancement of `main` via branch protection, **or** add a frontend-correctness job to a push-triggered workflow |
| **3** | `05 Security` red → `main` undeployable | code owner (Bandit) + infrastructure owner (Trivy) | Grant **P-B2 then P-B1** (§7.5); grant Trivy #4/#5 removal + #1–#3 documented acceptance (§6) |
| **4** | No correctness/PostgreSQL evidence at `ecd23a9e` | coordinator | Authorize opening a PR carrying the SHA, confirming `PROVISION_DATABASE_URL` is the disposable loopback service. **Do not "fix" the double provisioning** — it is an intentional idempotency guard (§9) |
| **5** | TENANT-01 uncertified | tenancy owner | Approve a disposable PostgreSQL service and run the 4 tenant lane files; **do not enable FORCE RLS first** |
| **6** | Portfolio cache dead behind false-green health | portfolio-cache owner | Decide among the three recorded options; false health is worse than visible outage |
| **7** | D7-1 three invalid call sites | router owner | Grant deletion of 3 kwargs + 1 signature-binding test + align the double |
| **8** | W-6 misclassification | execution-state owner + coordinator | Grant ladder reorder + bounded auth matching + regression tests; keep separate from any retry-policy change |
| **9** | A23 Redis P1 | code owner | Add the role-establishing register row **first**, then complete the A23E6 §3 form |
| **10** | W1C-02 scope + criterion 6 uncommitted + D5 sign-off + ledger `:802` | code owner | One reply: scope (`compare()` only vs all three paths), authorize committing the +143-line addition, resolve D5's condition, authorize the one-line ledger correction |

---

## 17. Confirmations

**No deployment, production mutation, infrastructure provisioning, database or Redis mutation,
unauthorized CI dispatch, commit, push, merge, revert, reset, stash, clean or stage occurred.**

No security control was weakened: no CRITICAL suppressed, no severity threshold lowered, no scanner
excluded, no required check removed, no failing check replaced with a cosmetic success, no `nosec`
added, no baseline regenerated, no xfail converted to a pass, and no skipped test reported as
passing.

Concurrent-session work (`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`,
`tests/test_drift_classification.py`) was preserved byte-for-byte and neither staged nor reverted.

| Final status | |
|---|---|
| Frontend release path | **FAIL-OPEN — unchanged, grant ABSENT** |
| Backend release path | **FAIL-CLOSED on every reachable path — correction recorded (§4.1)** |
| `05 Security` | **FAILING** — 9 Bandit B608 reproduced locally at exit 1; 5 Trivy CRITICAL |
| Bandit test parity | **ABSENT, now demonstrated** — 3 passed while the CI command exits 1 |
| `01 PR Check` at `ecd23a9e` | **NOT RUN**, PR-only, undispatchable |
| **PostgreSQL** | **UNVERIFIED** |
| Drift baseline | **DEMONSTRABLY STALE** — 11 migrations, 11 of 16 dimensions, map recalculated |
| W1C-02 | **OPEN** — criterion 6 uncommitted, criterion 10 unexecuted, scope undecided |
| QUOTA-01 | **OPEN** — 60 passed / 5 skipped; real-Redis UNEXECUTED |
| Redis A23 P1 | **NOT STARTED — 0 of 8 gates** |
| D7-1 / W-6 / portfolio cache / workers | **confirmed defects, grants ABSENT, unmodified** |
| **TENANT-01** | **NOT CERTIFIED** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
