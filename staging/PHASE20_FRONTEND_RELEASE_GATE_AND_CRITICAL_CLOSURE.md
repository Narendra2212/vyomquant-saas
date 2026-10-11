# Phase 20 — Frontend Release Containment, Gate Closure, and Parallel Critical-Path Recovery

**Destination verified ABSENT before writing.** Does not overwrite any concurrent artifact.
**0 production files · 0 tests · 0 workflows · 0 Terraform · 0 migrations · 0 governance records
changed. 1 artifact written (this file).** No CI dispatched, no infrastructure touched, no
credential or gitignored DSN read.

> **DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED**

**This phase's substantive progress is Priority B**, the `workflow_dispatch` audit that Phase 19
explicitly left unfinished. It is now **complete**, and the result is partly reassuring and partly
worse than expected: the dispatch path **cannot** reuse another SHA's green result, but the backend
deploys to ECS with **no correctness evidence at all** on the push path, and `02 Build` publishes an
unvalidated image to ECR including the `:latest` tag. Unchanged findings are reconciled briefly
rather than re-proved.

---

## 1. Entry / exit manifest

| Measurement | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e` | identical |
| `origin/main` / divergence | `ecd23a9e` / **0 ahead 0 behind** | identical |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical — no commit, reset, stash, checkout or branch switch** |
| staged / unmerged | **0 / 0** | 0 / 0 |
| **stash entries** | **2 (pre-existing)** | **2 — untouched** |
| worktrees | 1 | 1 |
| porcelain (collapsed) | 85 | 86 (+1 = this report) |

**Hypothesis revalidation:** HEAD and `origin/main` are still `ecd23a9e`; no commits, staging or
resets occurred. Confirmed, not assumed.

**Concurrency.** The only non-`staging/` modification in the tree is
`tests/test_drift_classification.py` (` M`, another session's W1C-02 work). **Not touched.** No
workflow, Terraform, migration, router or test file is modified by anyone — which is how I know no
concurrent session has applied a gate fix. All Phase 13–19 artifacts, including both differently-named
Phase 16 reports and `PHASE19_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md`, were read only.
**No concurrent work was overwritten.**

### 1.1 Grants

**Enumerated, not assumed. The only live grant in the engagement is `AP-W1D-3`** (W1D-12 §1, Code
owner role) over four drift-classifier files — **none of which is a Phase 20 target.**

| Target | Grant |
|---|---|
| `.github/workflows/06-frontend-deploy.yml`, `03-deploy.yml`, `01-pr-check.yml`, `02-build.yml` | **ABSENT** — shared workflows, no owner row |
| `scripts/require_security_scan.py` | **ABSENT** |
| 3 Alembic migrations + `tests/test_bandit_sast_gate_is_green.py` | **ABSENT** |
| `terraform/alb.tf`, `security_groups.tf`, `redis-only/main.tf` | **ABSENT** — infra owner unidentified |
| `routers/market.py`, `orders.py`, `exchange.py`, `tests/test_market_none_keys_fix.py` | **ABSENT** |
| `backend_app/backend/redis_manager.py`, `tests/test_a23_redis_pool_lifecycle.py` | **ABSENT** — A23E6 form still 12 ☐ / 0 marked / 6 blank |
| Tenant-context, pooling, RLS | **ABSENT** + no environment |

Corroborated independently by `PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md` §: *"0 files
modified — no grant exists for any source, test, workflow, Terraform, migration or governance
change."* **Every workstream below is therefore INVESTIGATED — OWNER DECISION REQUIRED or BLOCKED,
and nothing was implemented.**

### 1.2 Environment

`SRC` Verified unavailable: **docker absent**, **Trivy not installed**, Redis `127.0.0.1:6379`
**closed**, PostgreSQL `127.0.0.1:5432` **closed**, zero `REDIS*` / `AERORA_TEST_REDIS_URL`
variables. `staging/pg_dsn.txt` exists, is gitignored, and **was not opened.** So no scanner or
database run was possible locally, and none is claimed.

---

## 2. Priority B — backend deployment path: AUDIT COMPLETE

This closes the Phase 19 gap. All `SRC`, read from the current workflows end to end.

### 2.1 The full backend path graph

```
push to main/master
   └─> 02 Build          (on: push + workflow_call)   ── NO needs:, NO gate
         builds Dockerfile, pushes ECR  :${{ github.sha }}  AND  :latest
   └─> 05 Security       (on: push + schedule)        ── gates nothing itself
         └─> 03 Deploy   (on: workflow_run["02 Build"] completed  |  workflow_dispatch)
               job pre-deployment-validation
                 if: (workflow_run && conclusion=='success') || workflow_dispatch
                 DEPLOY_SHA      = github.event.workflow_run.head_sha || github.sha
                 ALLOW_UNSCANNED = inputs.allow_unscanned
                 run: scripts/require_security_scan.py
               job deploy   needs: pre-deployment-validation
                 IMAGE_TAG = github.event.workflow_run.head_sha || github.sha
                 run: scripts/ecs_deploy_and_diagnose.py
```

### 2.2 What the gate does correctly — the dispatch bypass Phase 19 feared does NOT exist

`scripts/require_security_scan.py` is a genuinely sound exact-SHA verifier:

| Property | Evidence |
|---|---|
| Queries `05 Security` runs for the **exact** commit | `GET /actions/runs?head_sha={sha}` filtered on `name == "05 Security"` |
| **Missing scan refuses** | *"Nobody scanned this commit, which is not evidence that it is safe."* |
| Waits for a concurrent scan rather than guessing | `WAIT_SECONDS` 900, `POLL_SECONDS` 20 |
| **Still-running refuses** | *"A deploy that outruns its own security scan is an ungated deploy."* |
| **`cancelled` / `skipped` refuse** | explicit branch |
| **API `HTTPError` refuses** | *"refusing rather than guessing"* |
| Deploy cannot run without it | `deploy` declares `needs: pre-deployment-validation` |

**On `workflow_run`, the SHA is pinned to the triggering run's `head_sha`, not the event default** —
exactly what the brief requires of a `workflow_run` design.

**On `workflow_dispatch`, `github.event.workflow_run.head_sha` is empty, so `DEPLOY_SHA` falls back
to `github.sha`** — the head of the dispatched ref. The verifier then demands `05 Security` success
for *that* SHA, and `IMAGE_TAG` is computed from the *same* expression. **So the SHA verified and the
SHA deployed are necessarily identical: a manual dispatch cannot reuse another SHA's green result.**
Phase 19 left this open; it is now answered, and the answer is favourable.

**`allow_unscanned` is narrower than its name suggests.** In the script the override is reachable
only inside `if not runs:` — it bypasses a **missing** scan, never a **failed** one. A commit whose
`05 Security` concluded `failure` cannot be deployed by any dispatch input. That is a meaningful
containment property worth recording explicitly, because the input's description invites the
opposite reading.

### 2.3 Four weaknesses found in this audit — all new

| # | Weakness | Severity | Evidence |
|---|---|---|---|
| **B-1** | **ECS deployment requires a passing security scan but NO passing tests.** `03 Deploy` chains off `02 Build` only, and the verifier checks `05 Security` alone. `01 PR Check` — the entire correctness lane — is `pull_request`-only, so on a direct push to `main` **no tests run at all and the deploy gate never asks for any.** | **High.** Zero correctness evidence is required for a backend production deploy on the push path | `03-deploy.yml:5-9`, verifier `WORKFLOW_NAME = "05 Security"`, `01-pr-check.yml` trigger |
| **B-2** | **`02 Build` is itself ungated and tags `:latest`.** It runs `on: push` with no `needs:`, builds, and pushes both `:${{ github.sha }}` **and `:latest`** to ECR before any scan or test. The deploy gate stops ECS, not the registry — so **`latest` points at unvalidated code**, and anything resolving `latest` (manual ops, a task definition pinned to it, local pulls) gets it | **Medium-High**, narrow blast radius | `02-build.yml` `docker push …:latest` |
| **B-3** | **The verifier checks run-level conclusion only.** It reads the newest run's `conclusion`/`status` and never inspects job-level results, so a `05 Security` run that concluded `success` while a **required job inside it was skipped** would satisfy the gate. This is precisely the "run-level success masking a skipped mandatory job" case | **Medium**, latent | `require_security_scan.py` — no `/jobs` call |
| **B-4** | **`allow_unscanned` has no approval boundary**, and the runs query does not paginate past `per_page=100`. Any actor able to dispatch can set the override; it is logged but not gated by an environment reviewer | **Medium** | `03-deploy.yml:11-14`; `_runs_for()` |

**Priority B status: INVESTIGATED — OWNER DECISION REQUIRED.** No change made; grant ABSENT.
Smallest fixes, in order of value: add `01 PR Check` (or a push-triggered correctness lane) to the
verifier's required set (B-1); stop pushing `:latest` from an ungated build, or move the tag to
post-gate (B-2); extend the verifier to assert required **jobs** concluded `success` (B-3); put
`allow_unscanned` behind a GitHub Environment with required reviewers (B-4).

---

## 3. Priority A — frontend publication bypass: UNCHANGED, still ungated

Revalidated against current source, not carried on trust.

`SRC` `.github/workflows/06-frontend-deploy.yml` — `on: push: branches:[main,master], paths:['algo22-terminal/**', …]`;
single job `deploy-frontend` with **no `workflow_run`, no `needs:`**; proceeds to
`aws s3 sync dist/ s3://vyomquant-frontend/ --delete` (×3, one per cache class) and
`aws cloudfront create-invalidation --distribution-id EEOXECPHQ8SR0 --paths "/*"`.

`CI` It **succeeded on `ecd23a9e`** — the SHA where `05 Security` failed twice and `03 Deploy`
correctly refused. The bypass is realised, not theoretical.

`SRC` The structural cause stands: the bundle secret sweep
(`tests/test_no_secrets_in_bundle.py`, marker `frontend_bundle`) lives in `01-pr-check.yml`'s
`frontend-tests` job, after that job's own `npm run build`; the unit lane deselects it with
`-m "not frontend_bundle"`. **`01-pr-check.yml` is `pull_request`-only and the publish is
`push`-only — mutually exclusive triggers**, so the sweep can never gate a publish on the path that
actually ships.

**Containment.** No already-approved operational control exists that I may apply: branch protection,
repository settings, CDN, DNS and deployment permissions are all explicitly out of bounds without
authorization. **The owner action needed is one of:** (a) require a PR for `algo22-terminal/**` via
branch protection, which makes the sweep run; or (b) temporarily disable the
`06 Frontend Deploy` workflow until a gate exists. Either prevents another ungated publication
today. I applied neither.

**Gate design — specified, not implemented.** `require_security_scan.py` is a good base but
**insufficient alone**: it verifies `05 Security` only, which does not scan the frontend bundle. A
frontend gate must require **both** the security workflow **and** the correctness workflow carrying
the `frontend_bundle` sweep, for the exact candidate SHA, with job-level assertions (per B-3),
`concurrency` to serialise reruns, artifact provenance pinned to the verified SHA, and every publish
step behind `needs:` on the gate. The 12-case regression matrix from the brief is the acceptance
criterion; mocked workflow-logic tests would prove gate behaviour only and **would not** constitute
release certification.

**Status: INVESTIGATED — OWNER DECISION REQUIRED.** Grant ABSENT; `06-frontend-deploy.yml`
unmodified. **Frontend: BLOCKED from publication.**

---

## 4. Priorities C–G — reconciliation, with corrections

Reported briefly because the underlying evidence is unchanged and the files are byte-identical.

| WS | Status | Reconciliation |
|---|---|---|
| **C — Bandit parity** | **INVESTIGATED — OWNER DECISION REQUIRED** | Defect confirmed and unchanged: the gate test asserts Bandit silence on **one module** while CI scans `-r backend_app`; the AST companion is tree-wide but blind to `# nosec`, so a green suite coexists with a red verdict and cannot see it. 9 × B608 MEDIUM in `backend_app/alembic/versions/`, each validated by `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises) with user values parameter-bound. Reproduced locally and exactly in Phase 14 (bandit 1.9.4, exit 1, 9 results, 0 `nosec`) — **not re-run here**, no new evidence would come of it. `|| true`, lowered severity, baselines and blanket `nosec` all remain excluded. Grant ABSENT |
| **D — Trivy** | **INVESTIGATED — OWNER DECISION REQUIRED** | **Correction to my own Phase 19 framing.** I implied the Redis egress tightening was the main lever. `PHASE16_FRONTEND_GATE…` states it plainly: **the gate stays RED even with both Redis egress rules tightened**, because `AWS-0054` and the two conventional `AWS-0104` findings still trip the CRITICAL blocking step. **Removing two of five findings is not gate closure.** It also records **five blocking sites, not four**. Counts I verified in Phase 19 stand (`alb.tf` 1, `redis-only/main.tf` 1, `security_groups.tf` 3; **`cloudtrail.tf` zero**), and SC-9 stays separate and non-blocking. Trivy and docker are absent locally, so **no scan was run and no rule ID was independently re-derived.** Grant ABSENT |
| **E — D7-1 vault mismatch** | **INVESTIGATED — OWNER DECISION REQUIRED** | Unchanged: signature is `load_decrypted_keys(self, user_id, exchange_id)`; three callers pass `access_token=` (`orders.py:789`, `orders.py:1095`, `market.py:50`); `exchange.py:993` correct. The masking mechanism is the test double in `test_market_none_keys_fix.py` declaring `access_token=None`. The brief's additional concern is valid and recorded: `market.py`'s broad `except` can turn a vault/auth failure into a silent unauthenticated fallback, so the fix must narrow that handler rather than only delete three arguments — and **what market data should do when keys are absent is a product/security policy question, out of scope unless separately authorized.** No credential or key material read or logged. Grant ABSENT |
| **F — Tenant / PostgreSQL** | **BLOCKED — ENVIRONMENT AND AUTHORIZATION** | Static position unchanged: nine RLS policies keyed on `app.current_tenant_id`; `FORCE ROW LEVEL SECURITY` exists **only as a SQL comment** (`026…:75`), zero executable statements; `dependencies.py` has **0** tenant-context references; only two GUC setters exist, both off the request path; the fallback DSN role is `postgres`, the table owner, so policies are inert on the ORM path; `tenant_db_context.py` is correct (`set_config(..., is_local=true)` inside an explicit transaction — the only pooler-safe form under `pool_mode=transaction`) and **unwired**. **FORCE RLS must not be enabled as an isolated patch.** PostgreSQL is closed locally and the PR-check lane has never run at this SHA, so **no runtime evidence exists.** Owner action: open a PR against `main` for `ecd23a9e` (runs the lane in CI), with `PROVISION_DATABASE_URL` confirmed isolated — `01-pr-check.yml:593` points it at loopback. **TENANT-01 NOT CERTIFIED** |
| **G — D7-2 / W-6 / workers / Redis** | **INVESTIGATED / BLOCKED** | D7-2: `portfolio_cache_updater.py:151` imports from the non-existent `backend_app.backend.key_manager`; the import sits **outside** the function's own `try`, so it is caught in the per-user loop and fails deterministically for every user every cycle while health still reports `ACTIVE`. Minimal import fix kept separate from health redesign. W-6: ladder defects confirmed; **classification-only** — `except <Type>` → 0 matches, `error_code ==` → 0, `.retryable` read in exactly one log f-string, and `replay_safe_transaction_guard` re-derives retryability independently, so **no retry wiring may be added as part of a classification fix.** Workers: `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`**; `ReconciliationScheduler`/`OrderWatchdog` are reported active by other phases and I did **not** re-verify that distinction. W-4 UNKNOWN-exit policy not invented; W-5 not begun. Redis: service closed, **5 skipped integration tests are not passes**; prior 60/5 and 18/2 counts remain historical and unreproduced. Grants ABSENT |

---

## 5. Commands and evidence classes

**No test, scanner, or database command was executed this phase** — every implementation workstream
is grant-blocked, so there was no authorized change to validate, and the local environment cannot
run Trivy, docker, Redis or PostgreSQL. Nothing is reported as passing.

| Command | Result |
|---|---|
| `git rev-parse --short HEAD / origin/main`, `rev-list --left-right --count`, `reflog -1`, `status --porcelain`, `diff --cached`, `diff --diff-filter=U`, `stash list`, `worktree list` | §1; exit 0 |
| `Test-Path` ×4 on verifier, build workflow, two report destinations | §1, §2 |
| `read_file` / `grep_search` on `require_security_scan.py`, `03-deploy.yml`, `02-build.yml`, `06-frontend-deploy.yml`, `01-pr-check.yml`, both Phase 16 reports | §2, §3, §4 |

**Evidence classes, kept distinct per the brief:** `SRC` current-source reads (all of §2, §3);
`CI` prior read-only `gh` queries from Phase 19, reused because HEAD is unchanged (§2.1, §3);
`PRIOR` other sessions' recorded findings, cited and labelled (§4). **Mock/unit evidence: none
produced. Integration evidence: none. Release certification: none — and none of the above would
constitute it.**

---

## 6. Owner decisions and minimal next actions

| # | Decision | Owner role | Minimal next action |
|---|---|---|---|
| **1** | **Stop ungated frontend publication** | release / workflow owner | Require a PR for `algo22-terminal/**`, or disable `06 Frontend Deploy`, **today** — then grant the gate implementation |
| **2** | **B-1: require correctness evidence for backend deploys** | release owner | Add the correctness workflow to the verifier's required set; today a backend ECS deploy needs a green scan and **zero** tests |
| **3** | **B-2: stop `:latest` from an ungated build** | release owner | Move the `latest` tag behind the gate, or drop it |
| **4** | **B-3: assert required jobs, not just run conclusion** | release owner | Extend `require_security_scan.py` to inspect `/jobs` |
| **5** | **B-4: approval boundary for `allow_unscanned`** | release owner | Put the dispatch behind a GitHub Environment with reviewers |
| **6** | Trivy dispositions — all five, since two fixes do not close the gate | infrastructure owner | Disposition each of `AWS-0054` ×1 and `AWS-0104` ×4 |
| **7** | Bandit — widen gate test + per-site dispositions | code owner | Grant 3 migrations + gate test |
| **8** | D7-1 — three deletions, double alignment, narrowed `except` | router owner | Grant the four files |
| **9** | TENANT-01 runtime evidence | tenancy + DB + CI owner | PR against `main` for `ecd23a9e` with isolated provisioning URL |
| **10** | D7-2, W-6, workers, A23 Redis P1 | respective owners | As recorded in §4 |

---

## 7. Final release disposition

| Item | Status |
|---|---|
| **Frontend** | **BLOCKED from publication** — no gate exists; it published `ecd23a9e` ungated |
| **Backend** | **BLOCKED** — `05 Security` red at this SHA; `03 Deploy` correctly refuses. Additionally, even when green, the path requires **no correctness evidence** (B-1) |
| **TENANT-01** | **NOT CERTIFIED** — no runtime evidence; policies inert on the ORM path |
| **PostgreSQL / real Redis** | **UNVERIFIED** — lanes never ran at this SHA; services closed locally; skipped tests are not passes |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

No gate was weakened, no finding concealed, no CI evidence fabricated, and no skipped test counted
as a pass. No concurrent work was overwritten; the two pre-existing stash entries and the other
session's `tests/test_drift_classification.py` are untouched.
