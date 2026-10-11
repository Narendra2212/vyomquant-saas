# Phase 21 — Release Gate Implementation, Frontend Containment, and Parallel Critical-Path Closure

**Destination verified ABSENT before writing.** Does **not** overwrite the concurrent
`staging/PHASE21_RELEASE_INCIDENT_AND_SECURITY_CLOSURE.md` (24,344 B, 20:47:30) — a different
session's report at a different path, read but not modified.

**0 production files · 0 tests · 0 workflows · 0 scripts · 0 Terraform · 0 migrations ·
0 governance records changed. 1 artifact written (this file).** No commit, stage, push, CI dispatch,
deploy, publish, provisioning, database mutation, or credential read.

> **DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED**

**This phase's substantive progress is Lane C — container image provenance.** The brief required
that `latest` consumption be established from real consumers rather than inferred from the build
file. It is now established, and the result is materially worse than Phase 20 recorded: **`latest`
is the deploy script's default and is pinned in six Terraform task definitions.** Details in §3.
Unchanged findings are reconciled briefly rather than re-proved.

---

## 1. Preflight

### 1.1 Live repository state

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| `origin/main` / divergence | `ecd23a9e` / **0 ahead 0 behind** |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` — **no commit, reset, stash, checkout or branch switch** |
| staged / unmerged | **0 / 0** |
| **stash entries** | **2 (pre-existing) — untouched** |
| worktrees | 1 |
| porcelain (collapsed) | 100 at entry → 101 at exit (+1 = this report) |

**HEAD has not changed since Phase 20**, so evidence gathered against this SHA remains applicable
and expensive investigations were not repeated.

**Concurrency.** The only non-`staging/` modification in the tree is
`tests/test_drift_classification.py` (` M`, another session's W1C-02 work) — **not touched.**

**Decisive negative:** there is **no modification to any workflow, script, Terraform file, migration,
router, test or governance record** anywhere in the tree. **No concurrent session has implemented
any lane.** Nothing was duplicated, raced or overwritten.

### 1.2 Authorization matrix

Re-read this phase, not carried on trust. Independently corroborated by the concurrent Phase 21 §1:
ledger `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804` → `AP-W1D-1`…`AP-W1D-5` **all NOT
GRANTED**; `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` → **0 ticked / 12 unticked**;
`CODEOWNERS` / `OWNERS` / `MAINTAINERS` → **absent**.

**The only live grant in the engagement remains `AP-W1D-3`** (W1D-12 §1, Code owner role), scoped to
four drift-classifier files — **none of which is a Phase 21 target.**

| Lane | Target files | Status |
|---|---|---|
| A — frontend gate | `06-frontend-deploy.yml`, `scripts/` | **NOT AUTHORIZED** |
| B — backend correctness gate | `03-deploy.yml`, `01-pr-check.yml` | **NOT AUTHORIZED** |
| C — image provenance | `02-build.yml`, `scripts/ecs_deploy_and_diagnose.py`, `terraform/ecs.tf` | **NOT AUTHORIZED** |
| D — verifier hardening | `scripts/require_security_scan.py` + tests | **NOT AUTHORIZED** |
| E — Bandit parity | 3 Alembic migrations + `tests/test_bandit_sast_gate_is_green.py` | **NOT AUTHORIZED** |
| F — D7-1 vault callers | `routers/market.py`, `orders.py`, `exchange.py`, `tests/test_market_none_keys_fix.py` | **NOT AUTHORIZED** + product-policy decision open |
| G — D7-2 updater / `core/state.py` | `portfolio_cache_updater.py`, `core/state.py` | **NOT AUTHORIZED** |
| H — tenant / RLS | dependency, session, tenant-context, RLS migrations | **NOT AUTHORIZED** + **BLOCKED BY ENVIRONMENT** |
| I — Redis / quota / workers | `redis_manager.py`, `main.py`, worker modules | **NOT AUTHORIZED** + **BLOCKED BY ENVIRONMENT** |
| J — Terraform / Trivy | `alb.tf`, `security_groups.tf`, `redis-only/main.tf` | **NOT AUTHORIZED** + scanner unavailable |

**Every lane is therefore `INVESTIGATED — OWNER DECISION REQUIRED` or `BLOCKED`. None is
`IMPLEMENTED`.** No grant was inferred from this prompt, from commit authorship, from a role label,
from a clean working tree, from a passing test, or from silence. Per the brief, this did not stop
the phase: all lanes were pursued to the limit read-only work allows, and Lane C produced new
verified findings.

### 1.3 Environment

`SRC` **docker absent · Trivy not installed · Redis `127.0.0.1:6379` closed · PostgreSQL
`127.0.0.1:5432` closed · zero `REDIS*` / `AERORA_TEST_REDIS_URL` variables.**
`staging/pg_dsn.txt` exists, is gitignored, and **was not opened.** So no scanner, database or
real-Redis run was possible, and none is claimed.

**Evidence caveat adopted from the concurrent Phase 21:** `staging/p15_runs.json` is UTF-16 encoded
and holds 8 runs, **none at `ecd23a9e`** — it predates the commit and **must not** be cited as
evidence for this SHA.

---

## 2. Release workflow graph at `ecd23a9e`

`SRC` Read end to end this phase and in Phase 20.

```
push to main/master
 ├─ 02 Build         (on: push + workflow_call)      NO needs:, NO gate
 │     docker push  ECR vyomquant-api:${{ github.sha }}
 │     docker push  ECR vyomquant-api:latest          ← mutable, ungated  (§3)
 │     └─> 03 Deploy (on: workflow_run["02 Build"] | workflow_dispatch)
 │           pre-deployment-validation
 │             DEPLOY_SHA = workflow_run.head_sha || github.sha
 │             ALLOW_UNSCANNED = inputs.allow_unscanned
 │             scripts/require_security_scan.py   → checks "05 Security" for that SHA
 │           deploy  needs: pre-deployment-validation
 │             IMAGE_TAG = workflow_run.head_sha || github.sha
 │             scripts/ecs_deploy_and_diagnose.py   ← IMAGE_TAG default "latest"  (§3)
 ├─ 05 Security      (on: push + schedule)           gates nothing by itself
 └─ 06 Frontend Deploy (on: push, paths algo22-terminal/**)
       deploy-frontend   NO workflow_run, NO needs:
       3 × aws s3 sync dist/ s3://vyomquant-frontend/ --delete
       aws cloudfront create-invalidation --paths "/*"

01 PR Check  (on: pull_request ONLY)  ── correctness + frontend_bundle secret sweep
                                          NEVER runs on a push; gates nothing
```

**Exact-SHA provenance.** On `workflow_run`, `DEPLOY_SHA` and `IMAGE_TAG` are both pinned to the
triggering run's `head_sha` — the correct pattern, not the event default. On `workflow_dispatch`
both fall back to `github.sha`, and the verifier checks that same SHA, so **a dispatch cannot reuse
another SHA's green result.** `allow_unscanned` is reachable only inside the script's `if not runs:`
branch, so it **excuses a missing scan and never a failed one.** These Phase 20 conclusions were
re-read and stand.

---

## 3. Lane C — container image provenance: NEW VERIFIED FINDINGS

The brief required real consumers be identified rather than inferred. Two were found, and together
they break the gate's provenance guarantee.

### 3.1 The deploy script defaults to `latest`

`SRC` `scripts/ecs_deploy_and_diagnose.py`:

```python
:94   IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")
:901  image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"
```

`03-deploy.yml` supplies `IMAGE_TAG: ${{ github.event.workflow_run.head_sha || github.sha }}`. When
that expression resolves, the deploy is SHA-pinned. **When it resolves empty, the script silently
substitutes `latest`** — a mutable tag that `02 Build` overwrites on **every** push to `main` with
no gate. That is a **fail-open default inside the deployment path**: the gate verifies a SHA, and
the deploy can ship whatever `latest` currently is. A fail-closed default would refuse.

### 3.2 Six Terraform task definitions are pinned to `latest`

`SRC` `terraform/ecs.tf`:

| Line | Image |
|---|---|
| `:28` | `vyomquant-api:latest` |
| `:126` | `vyomquant-tee:latest` |
| `:198` | `vyomquant-mds:latest` |
| `:284` | `vyomquant-websocket:latest` |
| `:378` | `vyomquant-simulator:latest` |
| `:478` | `vyomquant-api:latest` — *"Workers use the backend image"* |

So the **Terraform-declared task definitions consume the mutable tag**, including the worker
services. The normal deploy path registers a new revision with an explicit image URI, but these
declarations remain the Terraform-managed desired state.

### 3.3 Why this matters — the gate is circumventable without touching it

**The validated image identity is not guaranteed to be the deployed image identity.** Paths that
resolve `latest` and bypass the SHA pin entirely:

1. A `terraform apply` reverting a task definition to `:latest`.
2. A service redeploy or forced new deployment from the Terraform-declared task definition rather
   than the deploy script's revision.
3. A scale-out or task replacement pulling `:latest` on a fresh host.
4. `IMAGE_TAG` resolving empty, per §3.1.

**Severity: High.** It defeats the release gate's central promise while leaving the gate itself
looking correct, and five of the six pinned definitions are services the backend gate was never
reasoned about. This is additive to Phase 20's B-2, which recorded only that `latest` was *published*
— it is now established that `latest` is also *consumed*.

### 3.4 Minimal change plan — NOT APPLIED

| # | Change | File | Rationale |
|---|---|---|---|
| C-1 | Make `IMAGE_TAG` **required**; exit non-zero when unset or empty | `scripts/ecs_deploy_and_diagnose.py:94` | Removes the fail-open default. Smallest possible diff |
| C-2 | Replace `:latest` with a variable pinned to the validated SHA/digest in all six task definitions | `terraform/ecs.tf` | Makes the Terraform desired state match the gated artifact |
| C-3 | Stop publishing `:latest` from the ungated build, **or** promote it only after the gate passes | `02-build.yml:72,76` | A mutable tag written pre-gate is the root enabler |
| C-4 | Verify the **digest**, not just the tag, across build → validation → deploy | verifier + deploy script | Tag equality is weaker than digest equality |

**Acceptance tests:** empty `IMAGE_TAG` must cause refusal, not a `latest` deploy; a Terraform plan
must contain no `:latest` for gated services; the deployed digest must equal the validated digest;
and a `latest` push must not alter any gated service's resolved image.

**Authorization: ABSENT for all four files. Nothing changed.**

---

## 4. Lanes A, B, D — reconciliation

| Lane | Status | Position |
|---|---|---|
| **A — frontend gate** | **INVESTIGATED — OWNER DECISION REQUIRED** | Re-verified: `06-frontend-deploy.yml` triggers `on: push` to `main`/`master` for `algo22-terminal/**`, has **no `workflow_run` and no `needs:`**, and publishes via three `s3 sync --delete` calls plus a `/*` CloudFront invalidation. The `frontend_bundle` secret sweep runs only in `01-pr-check.yml`'s `frontend-tests` job, after that job's own `npm run build`; the unit lane deselects it. **`01 PR Check` is `pull_request`-only and the publish is `push`-only — mutually exclusive, so the sweep can never gate a publish.** The concurrent Phase 21 adds that the `ecd23a9e` assets are **already live** and that one check which never executed is **`Run Safety Check`**, the dependency audit — the same class whose failure produced the realized PyJWT CVE-2026-102268 incident recorded in `require_security_scan.py`'s own docstring. A gate prevents publishing an unverified commit; it does **not** retract one already published. **Rollback is a separate owner decision and none was performed.** |
| **B — backend correctness** | **INVESTIGATED — OWNER DECISION REQUIRED** | Unchanged and confirmed: `03 Deploy` chains off `02 Build` only and the verifier checks `05 Security` alone, so **a backend ECS deploy requires a green security scan and zero correctness evidence** on the push path. Fix: add the correctness workflow to the verifier's required set, or create an appropriately scoped push-triggered validation route — without duplicating pipelines |
| **D — verifier hardening** | **INVESTIGATED — OWNER DECISION REQUIRED** | Fail-closed properties to preserve: exact-SHA matching, missing-evidence refusal, bounded wait, refusal on failed/cancelled/skipped, refusal on API error, `needs:` on the gate job. Gaps to close: **(D-1)** inspect job-level conclusions — the script reads only run-level `conclusion`, so a `success` run with a skipped mandatory job passes; **(D-2)** paginate the runs and jobs queries beyond `per_page=100` with deterministic multi-run selection; **(D-3)** generalise to a configured required-workflow list rather than one mutable display name, preserving existing backend behaviour; **(D-4)** put `allow_unscanned` behind a real approval control and **verify the environment protection actually enforces** rather than asserting it in YAML |

---

## 5. Lanes E–J — reconciliation

Reported briefly; source byte-identical and prior evidence applicable.

| Lane | Status | Position |
|---|---|---|
| **E — Bandit** | **INVESTIGATED — OWNER DECISION REQUIRED** | CI scans `-r backend_app` tree-wide; the gate test asserts Bandit silence on **one module**; the AST companion is tree-wide but blind to `# nosec`. **So a green suite coexists with a red verdict and cannot see it.** 9 × B608 MEDIUM in three Alembic migrations, each validating its identifier via `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises) with values parameter-bound. Reproduced exactly in Phase 14 (bandit 1.9.4, exit 1, 9 results, 0 `nosec`); **not re-run** — no new evidence would come of it. Plan: run CI's scope in the test, add a **negative control** proving a deliberately injected pattern fails the gate, and fail if an unexpected `nosec`/baseline/exclusion masks a finding. `\|\| true`, lowered severity, concealing baselines and blanket suppression all remain excluded |
| **F — D7-1** | **INVESTIGATED — OWNER DECISION REQUIRED** | Signature `load_decrypted_keys(self, user_id, exchange_id)`; three invalid callers (`orders.py:789`, `orders.py:1095`, `market.py:50`); `exchange.py:993` correct. Masking mechanism: `test_market_none_keys_fix.py`'s double declares `access_token=None`. **Not a repo-wide replacement** — paper-trading APIs may legitimately take `access_token`. The market route's broad `except` can convert a vault/auth failure into silent anonymous market access, so the fix must narrow that handler. **What market data should do when keys are absent is an unresolved product/security policy question and was not invented.** No key material read or logged |
| **G — D7-2** | **INVESTIGATED — OWNER DECISION REQUIRED** | `portfolio_cache_updater.py:151` imports from the non-existent `backend_app.backend.key_manager`; the import sits **outside** the function's own `try`, so it is caught in the per-user loop and fails **deterministically for every user every cycle** while health still reports `ACTIVE`. Per the brief, the fix must reuse the single supported initialized vault rather than constructing a second one — the real `APIKeyVault` requires configured encryption keys. `core/state.py`'s separate bare import and the deployed object's identity **cannot be established locally** and need redacted boot evidence from the platform owner. Minimal import fix kept separate from health/heartbeat redesign |
| **H — tenant / RLS** | **BLOCKED — ENVIRONMENT AND AUTHORIZATION** | Nine RLS policies keyed on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` exists only as a SQL comment** (`026…:75`), zero executable statements; `dependencies.py` has **0** tenant-context references; only two GUC setters exist, both off the request path; fallback DSN role is `postgres`, the table owner, so policies are **inert** on the ORM path; `tenant_db_context.py` is correct (`set_config(..., is_local=true)` inside an explicit transaction — the only pooler-safe form under `pool_mode=transaction`) and **unwired**. **FORCE RLS must not be enabled as a standalone patch** — without context wiring it would make legitimate reads return no rows. A table-owner/superuser test is not proof of isolation. PostgreSQL closed locally; the PR-check lane has never run at this SHA. **NOT CERTIFIED** |
| **I — Redis / quota / workers** | **BLOCKED — ENVIRONMENT** | Redis closed; **skipped integration tests are not passes**; prior 60/5 and 18/2 counts remain historical and unreproduced. A23E6 form **0 ticked / 12 unticked**, all eight Redis gates unresolved; `redis_manager.py` unchanged at blob `d7b0e811b615…`; `tests/test_a23_redis_pool_lifecycle.py` **ABSENT**; strict xfail contract preserved. Workers: `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`**; `ReconciliationScheduler`/`OrderWatchdog` are reported active elsewhere and I did **not** re-verify that distinction. **W-4 UNKNOWN-exit policy not invented; W-5 not begun; no retries wired** |
| **J — Terraform / Trivy** | **INVESTIGATED — OWNER DECISION REQUIRED** | Verified counts at this SHA: `alb.tf` 1, `redis-only/main.tf` 1, `security_groups.tf` 3 — **5 CRITICAL, and `cloudtrail.tf` contributes ZERO.** `AWS-0054` on the ALB appears to be a false positive with an enforced `lifecycle.precondition`, but **whether it mitigates under the deployed configuration is unverified**. `AWS-0104` ×4: three are conventional ECR/Secrets-Manager/venue egress; the Redis egress is the tightening candidate — **but Redis cluster mode, replication and failover connectivity were not inspected, so no egress rule should be deleted on a static assumption.** Correction carried forward: **tightening both Redis rules does not close the gate**, because `AWS-0054` and the two conventional findings still trip the CRITICAL blocking step. **Trivy and docker are absent, so no scan was run and no result is claimed.** SC-9 (CloudTrail AES256 vs CMK) stays separate and non-blocking |

---

## 6. Tests and commands

**No test, scanner, build or database command was executed.** Every lane is grant-blocked, so there
was no authorized change to validate; and docker, Trivy, Redis and PostgreSQL are all unavailable,
so the integration lanes could not run even read-only. **Nothing is reported as passing, and no
skipped check is counted as a pass.**

| Category | Result |
|---|---|
| Executed | `git` state queries; `Test-Path`; `Select-String`/`grep` and `read_file` over the five workflows, `require_security_scan.py`, `ecs_deploy_and_diagnose.py`, `terraform/ecs.tf`, and the concurrent Phase 20/21 reports. All exit 0 |
| **Failed** | none attempted |
| **Skipped / blocked** | Bandit CI-equivalent scan (**not re-run** — Phase 14 evidence applicable, source identical); Trivy (**unavailable**); frontend build + bundle sweep (**no node/dependencies verified**); PostgreSQL and Redis integration (**services closed**); CI dispatch (**not authorized**) |

**Evidence classes, kept distinct:** `SRC` current-source reads — all of §3 and the §2 graph;
`CI` read-only `gh` queries from Phases 19–20, reused because HEAD is unchanged;
`PRIOR` other sessions' findings, cited and labelled. **Mock/unit evidence: none produced.
Integration evidence: none. Release certification: none — and none of the above would constitute
it.**

---

## 7. Owner decisions and minimal next actions

| # | Decision | Owner role | Minimal action |
|---|---|---|---|
| **1** | **Contain the live ungated frontend assets** | release owner | Decide whether to roll the CloudFront distribution back to the last SHA with a green `05 Security`. Then require a PR for `algo22-terminal/**`, or disable `06 Frontend Deploy`, until a gate exists. **No rollback was performed or attempted** |
| **2** | **C-1/C-2: stop `latest` being deployable** | release + infrastructure owner | Make `IMAGE_TAG` required in the deploy script; pin the six `ecs.tf` task definitions to the validated SHA/digest |
| **3** | **C-3/C-4: stop publishing `latest` pre-gate; verify digests** | release owner | Move or drop the `latest` push; compare digests across build/validate/deploy |
| **4** | **B: require correctness evidence for backend deploys** | release owner | Add the correctness workflow to the verifier's required set |
| **5** | **D-1…D-4: verifier hardening** | release owner | Job-level checks, pagination, configured workflow list, real approval control for `allow_unscanned` |
| **6** | **A: frontend gate implementation** | release / workflow owner | Grant `06-frontend-deploy.yml` + the 12-case regression matrix |
| **7** | **E: Bandit parity + negative control** | code owner | Grant 3 migrations + gate test |
| **8** | **F: D7-1 + the keys-absent policy question** | router owner + product/security | Grant four files; decide the anonymous-fallback policy separately |
| **9** | **G: D7-2 + deployed-vault evidence** | portfolio-cache + platform owner | Grant the import fix; supply redacted boot evidence |
| **10** | **J: five Trivy dispositions** | infrastructure owner | Disposition each individually; inspect Redis connectivity before tightening |
| **11** | **H / I: isolated environments** | CI + DB + platform owner | Provide disposable PostgreSQL and Redis; open a PR at `ecd23a9e` to run the provisioning lane |

---

## 8. Final release disposition

| Item | Status | Evidence |
|---|---|---|
| **Frontend publication** | **BLOCKED** — and the `ecd23a9e` assets are **already live**, published without security verification | §4 Lane A; `06 Frontend Deploy` succeeded at a SHA where `05 Security` failed twice |
| **Backend deployment** | **BLOCKED** — `05 Security` red at this SHA; `03 Deploy` correctly refuses. Additionally, even when green it requires **no correctness evidence**, and the **validated image identity is not guaranteed to be the deployed identity** | §3, §4 Lane B |
| **TENANT-01** | **NOT CERTIFIED** — policies inert on the ORM path; no runtime evidence | §5 Lane H |
| **PostgreSQL / real Redis** | **UNVERIFIED** — lanes never ran at this SHA; services closed; skips are not passes | §5, §6 |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** | unchanged |
| **Overall** | **DO NOT RELEASE** | every mandatory gate is absent, failing, or unproven |

No gate was weakened, no finding silenced, no severity lowered, no exclusion or baseline added, no
CI evidence fabricated, and no skipped test counted as a pass. `FORCE ROW LEVEL SECURITY` was not
enabled in any form. All concurrent work is preserved: the two pre-existing stash entries, the other
session's `tests/test_drift_classification.py`, and every Phase 13–21 artifact including the
concurrent `PHASE21_RELEASE_INCIDENT_AND_SECURITY_CLOSURE.md`.

**Continuation point:** every lane's next step is a grant, except Lane J's Redis-connectivity
inspection and Lane G's deployed-vault evidence, which need infrastructure access. Lane C's §3.4
plan is the highest-value unblocked-by-analysis item and is ready to apply the moment its four
files are authorized.
