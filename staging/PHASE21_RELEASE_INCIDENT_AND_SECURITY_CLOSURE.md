# Phase 21 — Release Incident Disposition, Exact-SHA Gate Enforcement, and Security Closure

Read-only investigation. No production code, workflow, migration, database state, role, grant,
or launch-gate register was modified in this phase. No commit, stage, push, or CI dispatch was
performed.

---

## 1. Entry / exit manifest

### 1.1 Authorization — rechecked, unchanged

| Source | Rows | State |
|---|---|---|
| `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804` | `AP-W1D-1` … `AP-W1D-5` | all **NOT GRANTED** |
| `staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` | 12 decision boxes | **0 ticked / 12 unticked** |
| `CODEOWNERS` / `OWNERS` / `MAINTAINERS` | — | **absent** (none exist in the tree) |

**Zero implementation grants exist.** Every remediation lane below is therefore classified
`INVESTIGATED — OWNER DECISION REQUIRED` or `BLOCKED`, never `IMPLEMENTED`. A grant is not
inferable from this phase's prompt, from commit authorship, from a role label, from a clean
working tree, from a passing test, or from silence.

### 1.2 Repository baseline — entry and exit identical

| Invariant | Value |
|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| `origin/main` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` (HEAD == remote) |
| `HEAD^{tree}` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` |
| Staged entries | 0 |
| Unmerged entries | 0 |
| Stashes | 2 (`filter-branch: rewrite`, `WIP on main: c673409`) |
| Worktrees | 1 (`C:/aerora_quant_backend_updated_final1 ecd23a9e [main]`) |

`ecd23a9e` — "launch phase 1", Narendra2212 <nt3441449@gmail.com>, 2026-10-11 15:13:47,
3723 files, parent `074b772ab16cece62cbfca0f2b78497be2ae56d5`.

### 1.3 Concurrent-session work preserved byte-for-byte

Tracked files dirty on entry, unchanged on exit:

| File | Blob (entry == exit) |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

Sibling-session untracked artifacts overlapping this phase's scope, read but **not modified**:

| File | Blob |
|---|---|
| `staging/phase17_frontend_release_gate.py` | `4c38728fb360801ef9ee46a34e0b8db407095d04` |
| `staging/phase17_test_frontend_release_gate.py` | `13b5e6cd1f12aa313bbf706b8eec09fec33a1928` |
| `staging/PHASE20_FRONTEND_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` | `68e58b7b6656ad188586c6300b95feefd453bdd6` |
| `staging/PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md` | `67c1802e8f0b405f1bd67711a75d72d741f0dbf5` |

Working tree: 88 `git status --porcelain` lines on entry; this phase adds only its own report
and four read-only evidence captures (§9.2).

### 1.4 Target blobs — all clean at `ecd23a9e`

`api_key_vault.py` `74a005941146` · `routers/orders.py` `ff2d34bf030a` ·
`routers/market.py` `16f0377cb9f6` · `exchange_executor.py` `df4fd42a1510` ·
`redis_manager.py` `d7b0e811b615ee13ba089b5cbe9d790161176de2` ·
`tests/test_bandit_sast_gate_is_green.py` `a7deff99c9e5` ·
`portfolio_cache_updater.py` `a74d76448a15` · `terraform/alb.tf` `cdead9289f20` ·
`terraform/security_groups.tf` `87c9d131051d` · `terraform/redis-only/main.tf` `48b9471035b7`.

### 1.5 Environment

Python 3.12.10, `gh` 2.100.0 (authenticated), bandit 1.9.4, Hypothesis 6.165.10, pytest 9.1.1.
No Trivy binary. No Redis (6379 unreachable; `REDIS_URL`, `AERORA_TEST_REDIS_URL`, `REDIS_HOST`,
`REDIS_PORT` all unset). No PostgreSQL. No AWS or exchange-sandbox credentials.

---

## 2. Priority A — frontend release incident: disposition

### 2.1 What is live in production right now

`06 Frontend Deploy` run **38129699497** is the **most recent run of that workflow in the
repository's history**. Measured against the workflow's own run list (`total_count = 60`,
newest 20 inspected): the run immediately preceding it is `37350937230` at `6c5660a1`, six days
earlier, on 2026-10-05.

**Nothing superseded it.** The frontend assets currently served from production S3 and
CloudFront are the assets built from `ecd23a9e` — the commit whose `05 Security` workflow is
red and which has never been evaluated by `01 PR Check`.

### 2.2 Corrected timeline

Re-queried live this phase from `GET /repos/:owner/:repo/actions/runs?head_sha=ecd23a9e…`
(`total_count = 6`, all 6 returned). Times are `run_started_at` → `updated_at`:

| Time (UTC) | Event |
|---|---|
| 09:48:44 | `05 Security` **38129699454** starts (`event=push`) |
| 09:48:45 | `02 Build` **38129699471**, `06 Frontend Deploy` **38129699497**, `07 Desktop Build (native, unsigned)` **38129699516** start (`event=push`) |
| **09:50:13** | **`06 Frontend Deploy` concludes `success` — frontend is live. Elapsed: 88 s.** |
| 09:58:29 | `02 Build` concludes `success` |
| 09:58:31 | `03 Deploy` **38130273012** starts (`event=workflow_run`) |
| **09:58:34** | **`05 Security` concludes `failure`** |
| 09:58:44 | `03 Deploy` concludes `failure` — gate refused; ECS job `skipped` |
| 10:38:58 → 10:49:33 | `05 Security` **38132697285** (`event=schedule`) concludes `failure` |

**Two corrections to Phase 16/20 reporting, both material:**

1. I previously recorded `05 Security` as concluding at **10:38:58Z, 50 minutes after**
   publication. That is wrong. `10:38:58Z` is the **start** of run `38132697285`, which is a
   separate `schedule`-triggered run (`05-security.yml` carries `on: [push, pull_request,
   schedule]`), not a retry. The **push**-triggered security verdict landed at **09:58:34Z** —
   **8 min 21 s after the frontend went live**, not 50 minutes.
2. I previously recorded `06 Frontend Deploy` as having 5 jobs. Parsed: it has **one** job,
   `deploy-frontend`, with **14 steps**.

Correction 1 makes the finding stronger, not weaker. The gap a correctly-ordered gate had to
absorb was ~8 minutes — comfortably inside the existing backend gate's default
`WAIT_SECONDS = 900` (`scripts/require_security_scan.py:43`). A gate of the already-written
kind would have polled, seen `failure`, and refused. The release was not a close call that
better timing would have fixed; it was an absent control.

### 2.3 Why it published — measured, not inferred

`.github/workflows/06-frontend-deploy.yml`, 615 lines, parsed with PyYAML:

- `needs:` occurrences in the entire file: **0**
- references to `05 Security`, `require_security_scan`, `security-scan`, or `ALLOW_UNSCANNED`: **0**
- job `deploy-frontend`: `needs = None`, `if = None`
- triggers: `push: [main, master]` and `workflow_dispatch`

The publishing steps run unconditionally once the build succeeds: `Configure AWS Credentials`
(`:254`), three `aws s3 sync dist/ s3://…` invocations (`:289`, `:299`, `:307`),
`aws cloudfront create-invalidation` (`:329`).

There is no bypass to find and no misconfiguration to correct. The workflow was never wired to
the security verdict at all.

### 2.4 What the frontend workflow *did* check

The inline checks are real and they passed. Recorded so the disposition is not overstated:

- service-role key absent from the bundle (`:168`)
- no `localhost:8000` / `ws://localhost:8000` endpoints in built output (`:188-202`)
- API-host allowlist enforced (`:217`)

These cover frontend-bundle secret leakage and endpoint misdirection. They do **not** cover
anything `05 Security` evaluates.

### 2.5 Explicit non-claims

**No exploitation is claimed. No secret leak is claimed. Neither is evidenced.**

- The two failing scanners at this SHA are `Bandit SAST` and `Trivy IaC` (§4). Both findings
  are in **backend Python** and **Terraform**. Neither is shipped in a frontend bundle.
- `Trivy Container Vulnerability Scanner` **passed** at this SHA.
- The frontend workflow's own secret-exposure check passed (§2.4).

The defect is a **missing control**, demonstrated by a release that occurred without
verification. It is not a demonstrated compromise. The correct characterisation: an
**unverified artifact is serving production traffic**, and the repository cannot presently state
what its security posture is.

### 2.6 The posture is partly unknown, not merely partly bad

New finding this phase. Job-level inspection of `05 Security` **38129699454** shows failures
truncated their own jobs, leaving later checks **unexecuted** at this SHA:

| Job | Conclusion | Detail |
|---|---|---|
| Trivy Container Vulnerability Scanner | `success` | — |
| Trivy IaC Misconfiguration & Secret Scan | `failure` | failed at step `Trivy IaC scan — terraform/ (blocking, CRITICAL)`; **skipped**: `Trivy IaC scan — aerora_quant_platform/infrastructure/ (blocking, CRITICAL)`, `Trivy IaC report — all severities to MEDIUM (non-blocking)` |
| Bandit SAST & Dependency Security Scan | `failure` | failed at step `Run Bandit Scan`; **skipped**: `Run Safety Check`, `Upload Security Reports` |

So for `ecd23a9e` there is **no dependency-vulnerability evidence at all** (`Run Safety Check`
never ran) and **no scan of the second infrastructure tree**
(`aerora_quant_platform/infrastructure/`). Three security evaluations have no result for the
commit that is live.

This matters directly against the incident the backend gate was written to prevent: that one
was a *dependency* CVE (§5.1), and the dependency check is precisely one of the three that did
not execute.

### 2.7 `01 PR Check` never evaluated this commit, and cannot be made to

`total_count = 6` for `ecd23a9e`; no `01 PR Check` run is among them. The cause is structural:
`01-pr-check.yml` declares `on: [pull_request]` — **no `push`, no `workflow_dispatch`**. Its
8 jobs (`static-audit`, `format-check`, `unit-tests`, `paper-mode-tests`, `database-tests`,
`provisioning-and-isolation`, `docker-check`, `frontend-tests`) cannot be run against a commit
already on `main` without opening a pull request that contains it.

3723 files reached production-serving infrastructure without the repository's test suite ever
executing against them.

---

## 3. Priority B — gate design for the frontend path

### 3.1 Required policy

For a candidate SHA, every mandatory workflow must have at least one run recorded against
**that exact SHA**, and the **newest** such run must have concluded `success`. Anything else
refuses. Newest-wins is load-bearing: a commit can carry an older green run and a newer red
one, and `any(success)` would release on evidence a later run contradicted.

### 3.2 A sibling session already drafted this

`staging/phase17_frontend_release_gate.py` (blob `4c38728fb360801ef9ee46a34e0b8db407095d04`)
is a proposal artifact for D-P15-4, deliberately parked under `staging/` and **not installed**.
I did not modify it and I am not duplicating it. Reviewed, it is sound: exact-SHA matching,
newest-by-`run_started_at` selection, enumerated refusing conclusions, pagination treated as a
refusal rather than silently truncated, no override input, and — importantly — it documents the
`workflow_run` checkout trap, where a gate validates SHA X while `actions/checkout` builds the
default branch, producing a release that looks verified and is not.

One citation correction: it refers to `require_security_scan.py:106` for the sort-and-take-last
logic. The file is **130 lines** and that statement is at **`:105`**. The behaviour it describes
is present and correct; only the line number is off by one.

### 3.3 Residual gap neither artifact closes

A gate in front of `deploy-frontend` prevents publishing an *unverified* commit. It does not
retract an *already published* one. Rollback of the live CloudFront distribution to the last
commit with a green `05 Security` is a separate owner decision, and no rollback was performed
or attempted in this phase.

---

## 4. Security-gate findings — dispositions

### 4.1 Bandit SAST — root cause is the gate test's scope

Executed locally twice, reproducing CI exactly:

```
.venv\Scripts\python.exe -m bandit -r backend_app -ll -f json -o staging\phase15_bandit_wholetree.json
→ exit 1 · 9 findings · 9 MEDIUM · all B608 · 67.2 s · bandit 1.9.4
```

Sites: `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py:193,198,209,467,475` ·
`b2c3d4e5f6a7_positions_canonical_shape.py:224,262` ·
`a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py:212,222`.

All nine are safe on inspection: `_validate_table_name` matches `^[a-zA-Z0-9_]{1,64}$`, raises
on mismatch, and never sanitises; `UUID_REGEX` is passed as a bound parameter
(`{"re": UUID_REGEX}`).

**Root cause.** `tests/test_bandit_sast_gate_is_green.py` sets
`GATED_MODULE = backend_app/backend/transactional_execution_manager.py` — a **single file** —
while CI runs `-r backend_app`, the **whole tree**. The companion AST test
`test_every_interpolation_into_sql_is_constrained_before_it_gets_there` (`:143`, walks
`BACKEND_APP.rglob("*.py")`) asserts *safety*, not *Bandit silence*, and
`SQL_SAFE_VALIDATORS = frozenset({"json_text", "_validate_table_name"})` (`:79`) admits exactly
the validator these nine sites use. Measured simultaneously: `pytest
tests/test_bandit_sast_gate_is_green.py` → **3 passed, exit 0, 87.79 s** while the CI gate is
**red**. The suite is green *because* its scope is one file.

**Recommended remediation, unauthorized:** A2 (broaden the gate test to the whole tree) before
or with A3 (`# nosec B608` with per-site justification, ×9). Rejected: `-lll`, `|| true`,
`--baseline`, excluding Alembic, lowering severity — the test's own docstring forbids all of
them.

### 4.2 Trivy IaC — 5 CRITICAL across 2 rules

| Rule | Location | Disposition |
|---|---|---|
| `AWS-0054` | `terraform/alb.tf:49-93` | **False positive.** The `dynamic default_action` is a :80→:443 `HTTP_301` redirect under `enable_custom_domain`; `lifecycle.precondition` refuses a production apply. Control SC-7. |
| `AWS-0104` | `terraform/security_groups.tf:26` (alb) | Load-bearing (public ingress) |
| `AWS-0104` | `terraform/security_groups.tf:56` (ecs_tasks) | Load-bearing |
| `AWS-0104` | `terraform/security_groups.tf:77` (redis) | **Unnecessary** — candidate for removal |
| `AWS-0104` | `terraform/redis-only/main.tf:62` (redis) | **Unnecessary** — candidate for removal |

Per-file: `alb.tf` 1, `security_groups.tf` 3, `redis-only/main.tf` 1. Zero findings in
`cloudtrail.tf`, `cloudfront.tf`, `ecs.tf`, `secrets.tf`, `vpc.tf`. No Trivy binary is available
locally, so these are read from CI output (`staging/p14_sec_failed.log`, 965 lines) rather than
re-executed.

---

## 5. Priority C — the backend gate is sound, and the frontend lacks what the backend learned

### 5.1 `scripts/require_security_scan.py` — assessed, 130 lines

| Property | Evidence |
|---|---|
| Targets the right workflow | `WORKFLOW_NAME = "05 Security"` (`:41`) |
| Matches the **exact** SHA | `…/actions/runs?head_sha=%s&per_page=100` (`:58`) |
| Waits rather than guessing | `WAIT_SECONDS = 900` (`:43`), `POLL_SECONDS = 20` (`:44`) |
| Newest run wins | `newest = sorted(runs, key=…run_started_at…)[-1]` (`:105`) |
| Missing scan = refusal | `if not runs:` → return 1 (`:95`) |
| Override defaults **safe** | `allow_unscanned = os.environ.get("ALLOW_UNSCANNED","").lower() == "true"` (`:66`) — unset is false |
| Refuses `cancelled` / `skipped` | explicit, "nothing was verified" |
| Refuses still-running | "A deploy that outruns its own security scan is an ungated deploy" |

Wired at `03-deploy.yml:90-95`, step `Require the security scan to have passed for this
commit`, with `DEPLOY_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}` (`:93`) and
`ALLOW_UNSCANNED: ${{ inputs.allow_unscanned }}` (`:94`), inside job
`pre-deployment-validation`; `ecs-deployment` carries `needs: pre-deployment-validation`.

**It worked.** At `ecd23a9e` the gate is the step that failed in run `38130273012`, and
`ECS Fargate Deploy & Self-Healing Diagnostics` concluded `skipped`. The backend was correctly
refused.

### 5.2 The point

The script's own docstring records a **realized** incident, not a hypothetical one: `PyJWT
2.13.0` (CVE-2026-102268, a key-confusion signature bypass permitting token forgery) reached
production while `05 Security` was red, and stayed until the pin was bumped
(`require_security_scan.py:8`). The gate exists because that happened.

The frontend path has the **identical** gap the backend already paid for and fixed. The remedy
is not a design problem — a correct, tested, in-repo implementation is sitting at
`scripts/require_security_scan.py`, and a generalisation of it for this exact purpose is sitting
at `staging/phase17_frontend_release_gate.py`. What is missing is authorization to wire it.

And per §2.6, the specific check that never executed at `ecd23a9e` — `Run Safety Check`, the
dependency audit — is the same class of check whose failure produced the PyJWT incident.

---

## 6. Parallel critical lanes — status

| ID | Finding | Status |
|---|---|---|
| **D7-1** | `api_key_vault.py:177` `load_decrypted_keys(self, user_id, exchange_id)` — one signature form in all history. Three call sites pass an invalid `access_token=`: `orders.py:789-793` (unguarded → **500** on `GET /api/orders/open`), `orders.py:1095-1099` (masked by `except Exception` `:1110` → `[]`), `market.py:50-54` (masked by `except Exception` `:67` → **anonymous `ConnectionEngine`**, making the `:56` guard unreachable). `exchange.py:993-996` is valid. All masked in dev by `state.py:97` `_safe_init(APIKeyVault,…)` → `MockEngine.__getattr__` noop. Method and broken callers landed in the same commit `6c5660a1`, so dropping `access_token=` loses nothing. | OWNER DECISION REQUIRED |
| **D7-2** | `portfolio_cache_updater.py:151` `from backend_app.backend.key_manager import KeyVault`; `:159` `vault = KeyVault()`; `:160` `vault.load_decrypted_keys(user_id, exchange_id)` (**arity correct**). No `class KeyVault` and no `key_manager*.py` exist anywhere in the tree. Verified executably: `ModuleNotFoundError: No module named 'backend_app.backend.key_manager'`. The import is lazy, inside the per-user path, so `main.py:466` still reports `service_status["portfolio_cache_updater"] = "ACTIVE"`. Recommended fix: reuse the initialized `app_state.vault` singleton. Rejected: aliasing `APIKeyVault as KeyVault` — the method API matches but the constructor does not (`APIKeyVault.__init__` raises `EnvironmentError` without `MASTER_ENCRYPTION_KEYS`). | OWNER DECISION REQUIRED |
| **W-6** | `_handle_ccxt_error` (`exchange_executor.py:1225-1257`) ladder orders symbol/market (`:1246`) **before** authentication/apikey/key (`:1252`), and the trigger is the literal substring `symbol`/`market` — which ccxt puts in URLs as `?symbol=`. Repository-wide, the only production read of `ExchangeError.retryable` is the log line at `:1687`; `replay_safe_transaction_guard.py:304` and `paper_simulator.py:1691` use their own predicates. Confirming tests `test_09a`/`test_09b` are gated behind `AERORA_EXCHANGE_SANDBOX_CERT` (`harness.py:30`, `conftest.py:57-76`) plus live credentials — unavailable here. | OWNER DECISION REQUIRED |
| **TENANT-01** | `FORCE ROW LEVEL SECURITY` appears as **zero executable SQL** tree-wide (prose only). `tenant_db_context.py` has no call sites. Live instance: `relrowsecurity=true, relforcerowsecurity=false`. The application connects as the table owner, which bypasses non-forced RLS. **NOT CERTIFIED.** Forcing RLS before context wiring and the required runtime tests pass would break tenant access paths; it must not be enabled first. | BLOCKED — NOT CERTIFIED |
| **QUOTA-01 / Redis A23 P1** | No Redis locally; 6379 unreachable; all four env vars unset. CI `01-pr-check.yml` provisions ephemeral `redis:7-alpine` with `AERORA_TEST_REDIS_URL: redis://localhost:6379/0`. Suites exist (`test_quota01_enforcement_is_wired.py`, `…_exchange_slot_is_atomic.py`, `…_hard_enforcer_is_deprecated_in_code.py`, `…_metered_reservation_is_hard.py`) and are unexecuted here. `redis_manager.py` remains **UNOWNED** per `A10:244,:367`. | BLOCKED (no Redis) |
| **Worker lifecycle** | Started: `ReconciliationScheduler` (`main.py:456`), `OrderWatchdog` (`:432`), `ConsistencyChecker` (`:415`), `PortfolioCacheUpdater` (`:466`). **Not started**: `ExchangeReconciliationService`, `MarketplaceExpiryWorker` / `expiry_sweep` (0 matches in `main.py`). `ExchangeReconciliationService` is **not** `ReconciliationScheduler`. | OWNER DECISION REQUIRED |
| **CD-8** | Settlement crash recovery. Recommended Option A (derive completion from existing rows) over B (reconciliation sweep) and C (PostgreSQL function). A progress column on `marketplace_settlements` is architecturally impossible: `trg_settlements_append_only` plus `REVOKE UPDATE, DELETE … FROM anon, authenticated, service_role`. | OWNER DECISION REQUIRED |

---

## 7. Lane classification

| Lane | Classification |
|---|---|
| Bandit A2 / A3 | INVESTIGATED — OWNER DECISION REQUIRED |
| Trivy B1 (`AWS-0054` false positive) / B2 (`AWS-0104` ×4) | INVESTIGATED — OWNER DECISION REQUIRED |
| Frontend release gate (Priority A/B) | INVESTIGATED — OWNER DECISION REQUIRED |
| Frontend rollback of `ecd23a9e` assets | OWNER DECISION REQUIRED — not attempted |
| D7-1, D7-2, W-6, worker lifecycle, CD-8 | INVESTIGATED — OWNER DECISION REQUIRED |
| TENANT-01 / PostgreSQL enforcement | BLOCKED — NOT CERTIFIED, UNEXECUTED |
| Redis A23 P1 / QUOTA-01 | BLOCKED — no Redis |
| `01 PR Check` at `ecd23a9e` | BLOCKED — `on: [pull_request]` only, not dispatchable |

---

## 8. Prohibited actions — confirmed not performed

- No vulnerability was claimed exploited; no secret was claimed leaked (§2.5).
- `FORCE ROW LEVEL SECURITY` was **not** enabled, anywhere, in any form.
- No production code, workflow, migration, database state, role, grant, or launch-gate register
  was modified.
- No findings were silenced: no global severity lowering, no broad file exclusion, no gate
  disablement, no `# nosec` annotation added.
- No commit, no stage, no push, no CI dispatch, no rollback, no deploy.
- No `KeyVault` implementation was invented and no import was redirected.
- No sibling-session file was edited; the four overlapping artifacts in §1.3 are byte-identical.

---

## 9. Commands and evidence

### 9.1 Executed this phase (all read-only)

```
git rev-parse HEAD / origin/main / "HEAD^{tree}"
git status --porcelain=v1 ; git stash list ; git worktree list
git hash-object <target and sibling files>
gh api "repos/:owner/:repo/actions/runs?head_sha=ecd23a9e…&per_page=100"
gh api "repos/:owner/:repo/actions/workflows/06-frontend-deploy.yml/runs?per_page=20"
gh api "repos/:owner/:repo/actions/runs/38129699454/jobs"
gh api "repos/:owner/:repo/actions/runs/38130273012/jobs"
python -c "yaml.safe_load(...)"   # 01,02,03,05,06 workflow trigger/job/needs structure
```

### 9.2 Artifacts written by this phase (new, untracked, `staging/` only)

`staging/phase21_runs.json` · `staging/phase21_frontend_runs.json` ·
`staging/phase21_sec_jobs.json` · `staging/phase21_deploy_jobs.json` · this report.

### 9.3 Pre-existing evidence relied on

`staging/p14_sec_failed.log` (965 lines, CI `05 Security` log) ·
`staging/phase15_bandit_wholetree.json` · `staging/phase15_bandit_gatetest.txt` ·
`staging/phase15_bandit_stderr.txt` · `staging/p14_bandit_local.json`.

Note: `staging/p15_runs.json` is UTF-16-encoded and contains 8 runs, **none** at `ecd23a9e` —
it predates the commit. It cannot be used as evidence for this incident, which is why the run
data above was re-queried live.

---

## 10. Release disposition

**DO NOT RELEASE.**

- **Frontend publication — BLOCKED.** Additionally: the assets from `ecd23a9e` are **already
  live** and were published without security verification. Containment is an owner decision.
- **Backend deployment — BLOCKED.** The existing gate already enforces this correctly.
- **TENANT-01 — NOT CERTIFIED.**
- **PostgreSQL enforcement — UNVERIFIED / UNEXECUTED.**
- **LIVE TRADING — OFF / NOT CERTIFIED.**

Minimum to clear the security gate at a new SHA: close Bandit (§4.1) and Trivy IaC (§4.2), then
confirm `Run Safety Check` and the second IaC tree scan actually **execute** rather than being
skipped behind an earlier failure (§2.6). Minimum to make the frontend path safe: wire an
exact-SHA gate ahead of `deploy-frontend`, pinning the checkout ref to the validated SHA
(§3.2). Every one of these requires a grant that does not exist.
