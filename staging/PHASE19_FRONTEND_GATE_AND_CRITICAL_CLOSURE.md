# Phase 19 — Frontend Release Gate Enforcement, Bandit/Trivy Recovery, Parallel Critical Closure

**Destination verified ABSENT immediately before writing. Phase 18 and all concurrent reports
untouched.**

**0 production files · 0 test files · 0 workflows · 0 Terraform · 0 migrations · 0 governance
records changed.** No commit, push, stage, reset, stash, clean, revert, merge, CI dispatch, PR,
deployment, `terraform apply`, infrastructure or cloud mutation, database or Redis connection, live
order, or FORCE RLS enablement. No secret, token or DSN value was read or printed.

**LIVE TRADING: OFF / NOT CERTIFIED · TENANT-01: NOT CERTIFIED · PostgreSQL: UNVERIFIED.**

---

## 1. Executive summary — what was actually executed

No implementation grant exists (§2), so no governed file was edited. Rather than stopping there,
I executed the two **negative controls** the brief requires as its evidence standard, and resolved
three questions it flagged for revalidation. All of this is newly executed, not inherited.

| # | Deliverable | Result |
|---|---|---|
| 1 | **Frontend gate assertions — executed negative control** | **VALID, 7/7 fixtures.** The assertions FAIL against the real current workflow, PASS against a synthetic gated one, and FAIL on each of 4 targeted mutants. The backend regression lock PASSES |
| 2 | **Bandit parity — executed negative control** | **VALID, 4/4 checks.** The scope gap is now *measured*: the single module the current test gates is **exit 0 / 0 findings**; the CI scope is exit 1 / 9 |
| 3 | **Migration 022 trigger question — RESOLVED** | 022's trigger block is a **read-only backstop**; it creates/alters/drops nothing. My Phase 18 `triggers = 0` caveat is **confirmed correct** |
| 4 | **D7-1 blast radius — quantified, and protective** | 3 invalid `access_token=` sites vs **9 legitimate** ones. A naive bulk fix would break all nine |
| 5 | **W-6 `retryable` — revalidated** | **Zero control-flow reads.** 3 occurrences total: 2 assignments, 1 log f-string. A classification fix cannot alter retry behaviour |
| 6 | **D7-2 remedy identified** | The established singleton accessor is `dependencies.py:659 get_vault() → app_state.vault`. No second key manager needed |

---

## 2. Authorization matrix — the controlling constraint

Verified at source this phase, not inherited:

| Evidence | Measurement |
|---|---|
| Ledger `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` rows 800–804 | `AP-W1D-1` … `AP-W1D-5`, owners `DB owner (read access)` / `DB owner` / `Code owner` / `DB owner + code owner` / `Coordinator + DB owner` — **all `NOT GRANTED`** |
| `**GRANTED**` occurrences anywhere in the ledger | **0** |
| `PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | *"no authorization arrived"*; **0 of 12** boxes, `D-A23C-1` and `D-A23C-7` both unchecked |

| Lane | Files it would touch | Owner | Grant | Scope permitted |
|---|---|---|---|---|
| **A** G1 frontend gate | `.github/workflows/06-frontend-deploy.yml`, new `tests/test_frontend_deploy_is_gated.py` | workflow / release owner | **NONE** | none |
| **B** G2 correctness gate | `01-pr-check.yml` **or** branch protection | release owner + coordinator | **NONE** — and option 1 is a *repository-settings* decision, not a code change | none |
| **C** Bandit parity | `tests/test_bandit_sast_gate_is_green.py`, 3 alembic migrations, `backend_app/test_perf.py` | code owner | **NONE** | none |
| **D** Trivy / Terraform | `terraform/security_groups.tf`, `terraform/redis-only/main.tf` | infrastructure owner | **NONE** | none |
| **E** D7-1 | `routers/orders.py`, `routers/market.py`, `tests/test_market_none_keys_fix.py` | router owner | **NONE** | none |
| **F** D7-2 | `backend_app/backend/portfolio_cache_updater.py`, health contract | portfolio-cache owner | **NONE** | none |
| **G** W-6 | `backend_app/backend/exchange_executor.py` | execution-state owner + coordinator | **NONE** | none |
| **H** TENANT-01 / Redis | tenant context, RLS migrations | tenancy owner / code owner | **NONE** | none |
| **I** drift / workers | `tests/`, `backend_app/main.py` | code owner | **NONE** | none |

**No authorization was inferred** from this brief's framing, a prior recommendation, the existence
of `scripts/require_security_scan.py`, the frontend deployment's success, green local tests, a role
title, commit authorship, or silence.

---

## 3. Preflight baseline and preservation manifest

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **unchanged** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Untracked | 86 | 90 (+4, all mine, all `staging/`) |

**All 16 target files `clean` against HEAD at entry and exit:**

| Hash (16) | Path |
|---|---|
| `285cc2d19b2b77ac` | `.github/workflows/06-frontend-deploy.yml` |
| `eb8078eac0626844` | `.github/workflows/03-deploy.yml` |
| `e2d9ccc6f951d39d` | `.github/workflows/05-security.yml` |
| `91d55a379115c88e` | `.github/workflows/01-pr-check.yml` |
| `a1c8dd2d48fbc744` | `scripts/require_security_scan.py` |
| `a7deff99c9e54bee` | `tests/test_bandit_sast_gate_is_green.py` |
| `1c836dad6c235efb` | `tests/test_nightly_audit_workflow.py` |
| `87c9d131051d5e51` | `terraform/security_groups.tf` |
| `48b9471035b77d4f` | `terraform/redis-only/main.tf` |
| `cdead9289f209c58` | `terraform/alb.tf` |
| `74a0059411467442` | `backend_app/backend/api_key_vault.py` |
| `ff2d34bf030a3086` | `backend_app/routers/orders.py` |
| `16f0377cb9f6c85e` | `backend_app/routers/market.py` |
| `df4fd42a15101512` | `backend_app/backend/exchange_executor.py` |
| `a74d76448a15150f` | `backend_app/backend/portfolio_cache_updater.py` |
| `a3849d4acd7afcf4` | `backend_app/main.py` |

**Concurrent work preserved byte-for-byte, neither staged nor reverted:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (` M`) and `tests/test_drift_classification.py`
(` M`, worktree `99a2917a…`, 808 lines vs HEAD 665).

**Environment capabilities measured:** Python **3.12.10** · pytest available · bandit **1.9.4** ·
PyYAML **6.0.3** · **terraform ABSENT** · **trivy ABSENT** · `AERORA_TEST_DATABASE_URL` unset ·
`AERORA_TEST_REDIS_URL` unset.

---

## 4. Lane A — frontend security gate: design validated by executed negative control

### 4.1 Current state re-verified by YAML parse, not substring match

`06-frontend-deploy.yml` is `clean` at `285cc2d1…`. Parsed with PyYAML:

| Assertion | Current value |
|---|---|
| A1 gate script invoked | **gate jobs = NONE** |
| A2 publish job depends on gate | publish job `deploy-frontend`, **`needs = []`** |
| A3 `DEPLOY_SHA` is `${{ github.sha }}` | **ABSENT** |
| A4 `ALLOW_UNSCANNED` literal `'false'` | **ABSENT** |
| A5 `permissions` includes `actions: read` | **`permissions` ABSENT ENTIRELY** |
| A6 no ungated publish path | **ungated publishers = `['deploy-frontend']`** |

**New detail:** the workflow declares **no `permissions:` block at all**, so it inherits the
repository default — which `03-deploy.yml`'s own in-file comment records as *not* including
`actions`, producing `GitHub API returned 403`. So a gate added here **must** declare
`actions: read` explicitly or it will fail closed for the wrong reason.

### 4.2 Executed negative control — `staging/probe_p19_frontend_gate_negative_control.py`

```
negative control: 7/7 fixtures behaved as expected
RESULT: negative control VALID              exit 0
```

| Fixture | Expected | Observed | Which assertions caught it |
|---|---|---|---|
| **[1] REAL current `06-frontend-deploy.yml`** | FAIL | **FAIL** | **all six** A1–A6 |
| **[2] SYNTHETIC gated workflow** | PASS | **PASS** | — |
| [3] MUTANT M1 `needs:` removed | FAIL | **FAIL** | **A2 + A6** |
| [3] MUTANT M2 `ALLOW_UNSCANNED` wired to an input | FAIL | **FAIL** | **A4** |
| [3] MUTANT M3 `DEPLOY_SHA: main` (branch, not SHA) | FAIL | **FAIL** | **A3** |
| [3] MUTANT M4 `actions: read` dropped | FAIL | **FAIL** | **A5** |
| **[4] REAL current `03-deploy.yml`** | PASS | **PASS** | B1 gate job `pre-deployment-validation`; B2 `ecs-deployment needs:` it; B3 gate `if:` admits `workflow_dispatch` |

Each mutant fails on **exactly** its targeted assertion and no other, so no assertion is vacuous
and none is doing another's work. Fixture [1] failing proves the assertions **detect** the current
ungated workflow; [2] proves the proposed gate **satisfies** them.

**Fixture [4] is the executed regression lock for Phase 18's correction:** backend manual dispatch
still routes through the security gate. The `allow_unscanned` override remains as designed — it
applies only when *no* scan exists, never when one failed.

### 4.3 Implementation status

**NOT IMPLEMENTED — grant ABSENT.** The validated change is 10 added lines plus one `needs:`; it
reuses `require_security_scan.py` and introduces no `workflow_run` trust boundary. Copy-ready
request in §12.

**I do not claim the historical incident is remediated.** Production remediation requires an
authorized release and verified evidence from the deployed SHA. None occurred.

---

## 5. Lane B — frontend correctness gap: a governance decision, recorded not taken

`01-pr-check.yml` is `clean` at `91d55a37…` and remains **`pull_request`-only**: `workflow_dispatch`
= 0, `push` = 0. Its `frontend-tests` job ("Frontend Build & Unit Tests", vitest, ~150 files) is
additionally conditioned on `steps.changes.outputs.frontend == 'true'`.

So on the direct-push path that publishes the frontend, correctness evidence is **structurally
unobtainable** — a security-only gate cannot close this.

| Option | Mechanism | Assessment |
|---|---|---|
| **1 — PR-only advancement of `main`** | branch protection / ruleset | **Smallest and most robust**, and it closes the gap for *both* frontend and backend at once. **But it is a repository-settings change owned by the release owner, not a code change.** Per the brief I did not take it silently |
| 2 — extend `01 PR Check` to `push: [main]` | workflow edit | Runnable as code, but double-runs the full suite per PR-plus-merge and still needs the path-filter skip treated as not-success |
| 3 — add a frontend correctness job to a push-triggered workflow | workflow edit | Narrowest code change; duplicates vitest config and risks drifting from the PR lane |

**Recommendation: Option 1**, with Option 3 as the code-only fallback. Either way the consuming
gate must treat **missing / cancelled / skipped / different-SHA** as failure — which
`require_security_scan.py` already does for its own workflow and which a correctness equivalent
would need to replicate.

**Status: owner decision required. Not implemented.**

---

## 6. Lane C — Bandit parity: scope gap measured, both negative controls executed

Per the brief's reuse rule, the expensive full-tree scan was **not** repeated: HEAD, file hashes and
bandit version are identical to Phase 18's run (`bandit 1.9.4`, exit 1, 9 findings). Reused
evidence is labelled as such below.

### 6.1 `staging/probe_p19_bandit_parity_negative_control.py` — 4/4 checks, exit 0

| # | Check | Measured |
|---|---|---|
| **[1] SCOPE GAP** | bandit on the single module the current test gates | **exit 0, 0 findings, 2.2s** — `backend_app/backend/transactional_execution_manager.py` |
| | full tree `-r backend_app -ll` | **exit 1, 9 findings, `B608`** — *[REUSED Phase 18 evidence, identical hashes + version]* |
| **[2] DETECTION** | unsuppressed interpolated-SQL fixture | **exit 1, 1 finding, `B608`, 1.4s** |
| **[3] SUPPRESSION** | validated allowlist + site-specific `# nosec B608` | **exit 0, 0 findings, 1.7s** |
| **[4] FALSE-GREEN GUARD** | suppressed site **plus** an unrelated unsuppressed finding | **exit 1, 1 finding, 1.1s** |

**[1] converts the divergence from inference to measurement.** The module the current test gates is
*genuinely clean*, which is precisely why `test_bandit_sast_gate_is_green.py` passes (3 passed,
112.92s, Phase 18) while CI fails on the same tree. The gap is structural, not luck.

**[2]–[4] are the two negative controls the brief requires:** the proposed subprocess-based parity
test fails on an introduced finding, goes green **only** when the site carries its own justified
suppression, and **cannot** be made falsely green by a suppression elsewhere.

All fixtures were written to a system temp directory and deleted. **No `reports/` path was written**
(`reports/` is a pre-existing tracked directory; my outputs went to `staging/`).

### 6.2 The nine B608 findings — disposition unchanged, grant absent

`a1b2c3d4e5f6…:212,222` · `b2c3d4e5f6a7…:224,262` · `d4e5f6a7b8c9…:193,198,209,467,475`. All
`B608`/`MEDIUM`, `nosec` count **0** in all three files. Safety rests on
`_validate_table_name` — `^[a-zA-Z0-9_]{1,64}$`, **raises rather than sanitising**
(`d4e5f6a7b8c9…:155-157`), referenced 21/5/16 times across the three files — with the only
user-influenced value bound as a parameter. Identifiers cannot be bound parameters, so validated
interpolation is the correct construction; a structural refactor is **not** available here.

**Mandatory order: B1 (parity test) before B2 (suppressions).** Suppression without detection is
what the gate test's own docstring warns against. No threshold lowered, no exclusion broadened, no
`|| true`, no baseline, no `nosec` added.

---

## 7. Lane D — Trivy / Terraform: no fresh scan, dispositions from re-read source

**`trivy` and `terraform` are both ABSENT locally, so no scan and no `validate` was executed. I
claim no fresh scan.** Reproducible CI-equivalent command, for an environment that has it:

```
trivy config terraform --scanners misconfig,secret --severity CRITICAL --exit-code 1
# CI: aquasecurity/trivy-action@v0.36.0, scan-ref: terraform
```

All three Terraform files re-read and `clean`. Dispositions, each with residual risk:

| # | Rule | Resource · site | Disposition | Residual risk |
|---|---|---|---|---|
| 1 | `AWS-0054` | `aws_lb_listener.http` · `alb.tf:52` | **Justified false positive.** `:55`/`:70` mutually exclusive `dynamic default_action`; `:61` `HTTP_301`; a `lifecycle.precondition` **refuses the apply** for `prod`/`production` without `enable_custom_domain`; `:77` records control SC-7. Trivy cannot evaluate `dynamic` blocks or preconditions | **None in prod** — the apply is refused. Non-prod bootstrap may serve :80 plaintext by design |
| 2 | `AWS-0104` | `aws_security_group.alb` · `security_groups.tf:26` | **Accept + document** | post-compromise egress latitude |
| 3 | `AWS-0104` | `aws_security_group.ecs_tasks` · `security_groups.tf:56` | **Accept + document — do NOT tighten blind** | Load-bearing: ECR, Secrets Manager, Supabase, venues. Tightening needs VPC endpoints + a venue allowlist and a dependency map this environment cannot produce |
| 4 | `AWS-0104` | `aws_security_group.redis` · `security_groups.tf:77` | **FIX — safe** | ElastiCache initiates no outbound connections; ingress already 6379 from `ecs_tasks` only |
| 5 | `AWS-0104` | `aws_security_group.redis` · `redis-only/main.tf:62` | **FIX — safe** | same |

`cidr_blocks = ["0.0.0.0/0"]` also appears at `security_groups.tf:11` and `:19` — those are **ALB
ingress** on 80/443, correct for a public load balancer, and **not** findings.

**0 of 5 fixed; infrastructure grant ABSENT.** No `.trivyignore`, no severity change, no
`terraform apply`, no AWS mutation.

---

## 8. Lanes E–G — defect revalidation with two consequential findings

### 8.1 D7-1 — the blast radius is 3, and 9 sites must be left alone

`api_key_vault.py` `clean`; `load_decrypted_keys(self, user_id, exchange_id)` takes two positionals.

| Class | Sites |
|---|---|
| **INVALID — must be fixed** | `market.py:53`, `orders.py:792`, `orders.py:1098` — all `access_token=user.get("access_token")` into a 2-arg method |
| **LEGITIMATE — must NOT be touched** | `paper_trading.py:88, 103, 141, 161, 193, 250, 282, 305` and `risk.py:313` |

The legitimate sites target methods that **genuinely declare** the parameter:
`paper_trading_service.py:249 persistence_client(self, access_token=None)` and
`:266 _client(self, access_token=None)`; `dependencies.py:148/242 create_request_supabase(access_token)`.

**This validates the brief's §6 caution with numbers: a naive "remove `access_token=user.get(...)`"
sweep would break 9 working call sites.** Any authorized fix must target the three
`load_decrypted_keys` callers only. The masking double is `tests/test_market_none_keys_fix.py:13`,
which declares `access_token=None` — matching the broken callers while the real class does not.

**Grant ABSENT. No edit.** The missing-keys-vs-vault-outage policy question stays unresolved, and
the `market.py:53` anonymous-fallback risk is recorded, not changed.

### 8.2 W-6 — `retryable` is log-only, now measured

Repository-wide in `backend_app`, `.retryable` has **exactly 3 occurrences**:

```
exchange_executor.py:92    self.retryable = retryable          (assignment)
exchange_executor.py:1687  f"retryable={error.retryable}"      (LOG f-string)
exchange_simulator.py:67   self.retryable = retryable          (assignment)
```

**Zero control-flow reads** — no `if`, no branch, no loop consumes it. So Phase 18's claim is
revalidated by independent measurement: correcting the classification ladder **cannot** change
retry behaviour, because nothing decides anything from this property.

The defects stand: the `symbol`/`market` branch at `:1246` precedes `network`/`timeout` at `:1249`
(so a timeout naming a symbol, or a message quoting a `/market/` path, classifies as
`InvalidSymbolError`), and bare `"key"` at `:1252` matches `monkey`, `keyspace`, `keyword`. The harm
is a corrupted classification and `error_code`, **not** demonstrated retry suppression — and I make
no such claim. **Grant ABSENT. No edit, no test added, no retry behaviour introduced.**

### 8.3 D7-2 — the correct remedy identified

`backend_app/backend/key_manager.py` **does not exist** (`Test-Path` → False), yet
`portfolio_cache_updater.py:151` executes `from backend_app.backend.key_manager import KeyVault`,
inside a block whose `except Exception` at `:132` swallows it per user. `/health/services` continues
to report `ACTIVE`, a **false-green**.

**The established accessor already exists and should be reused rather than reinvented:**

```
dependencies.py:659  def get_vault():
dependencies.py:662      return app_state.vault
ws_routes.py:813     vault = get_vault()
fleet_manager.py:74  app_state.vault.get_user_tier(user_id)
master_executor.py:127  self.vault = global_state.vault
```

So the fix is to consume the initialized singleton — **not** to create a second key manager or
instantiate an unconfigured vault, exactly as the brief cautions. Health-state semantics are a
**separate** concern needing its own owner.

**Adjacent latent defect, recorded:** `order_watchdog.py:1083` carries a comment stating
`app_state.vault.get_exchange_client` does not exist, while `:1105` calls it — the same
missing-attribute family. **Grant ABSENT. No edit.**

---

## 9. Lanes H–I — drift, tenant, Redis, workers

### 9.1 Migration 022 trigger question — RESOLVED, my Phase 18 caveat confirmed correct

Phase 18 flagged `triggers = 0` for a second look because 022 concerns an `updated_at` column. Read
in full with SQL semantics rather than string matching, 022 has two `DO $$` blocks:

1. **Block 1** — `ALTER TABLE public.library_subscriptions ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, guarded by `information_schema` existence checks → affects **`columns`** (and `unique_and_check` via `NOT NULL`/`DEFAULT`).
2. **Block 2** — a **read-only backstop**: it iterates `pg_trigger` joined to `pg_proc`, inspects `pg_get_functiondef`, and `RAISE EXCEPTION` if any trigger assigns `NEW.<column>` for a column its own table lacks (the BUG-056 condition, 42703 on first update).

**Block 2 creates, alters and drops nothing.** It only *validates* existing triggers. So the
`triggers` dimension count of **0 is correct**, and the map stands as published: **11 of 16
dimensions**, `columns` 10/11, `unique_and_check` 9, `privileges` 6, `policies` 5.

Server-free classifier evidence **reused** (identical hashes, same session state): `140 passed` for
`test_drift_classification.py` + `test_snapshot_dimension_invariants.py`. **No test yet drives the
dimension map itself** — that needs a `tests/` grant. Baseline preserved at `e6069946…`; no
recapture, no provisioning, no drift run.

### 9.2 TENANT-01 — static only

`FORCE ROW LEVEL SECURITY`: **0 matches** in `backend_app/migrations/*.sql` and
`backend_app/alembic/versions/*.py` outside comments. `tenant_db_context.py` unwired; the only GUC
write is an inline `SET LOCAL app.current_tenant_id` at `transactional_execution_manager.py:225`.
`SET LOCAL` is the correct transaction-scoped primitive for a pooled connection, but **one call site
is not repository-wide propagation**.

**Schema-level control = static fact. Isolated integration evidence = NONE** (the four tenant lane
files are 92 local skips and have never run in CI). **Production evidence = NONE.** No database
connection; FORCE RLS **not** enabled — correctly, since enabling it before proving context
propagation would convert a context gap into empty result sets. **TENANT-01 NOT CERTIFIED.**

### 9.3 Redis / QUOTA-01 / A23

Reused same-SHA evidence, no re-run: quota **60 passed / 5 skipped**; `test_redis_debounce.py`
**9 passed / 1 xfailed**. The 5 quota skips require real Redis; `AERORA_TEST_REDIS_URL` is unset and
6379 unreachable. **Real-Redis lane UNEXECUTED.** `9 passed / 1 xfailed` is **not** an all-pass
suite, and `test_r6_d` remains `XFAIL(strict)`, untouched. No service started, no URL fabricated, no
enforcement weakened.

**A23: the role-establishing register row does not exist.** A23E6's form is still blank (0 of 12),
and per A23E14 the W1D-12 precedent does not transfer because `Code owner` **pre-existed** in ledger
`:802`'s Owner column while A23 has no such row. **0 of 8 gates. Nothing requested, nothing
exercised.**

### 9.4 Workers

`main.py` `clean`. `ReconciliationScheduler` **started** (`:456`); `OrderWatchdog` **started**
(`:432`); `ExchangeReconciliationService` **not started**; `MarketplaceExpiryWorker` **not started**.
**W-4's `UNKNOWN` exit-semantics decision is absent, so W-5 was not implemented and no exit
semantics, retry, finality or idempotency behaviour was inferred.** No worker was started merely
because it exists.

---

## 10. Commands, versions, exit codes, timings

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status` / `reflog` / `ls-files` / `hash-object` ×16 | 0 | §3 manifest; all targets `clean` |
| Ledger + A23E11 grant scan | 0 | **0 `**GRANTED**`**; 5 rows `NOT GRANTED`; 0 of 12 boxes |
| `python --version` / `bandit --version` / PyYAML / `terraform` / `trivy` | 0 | 3.12.10 / 1.9.4 / 6.0.3 / **ABSENT** / **ABSENT** |
| **`python staging/probe_p19_frontend_gate_negative_control.py`** | **0** | **7/7 fixtures as expected — negative control VALID** |
| **`python staging/probe_p19_bandit_parity_negative_control.py`** | **0** | **4/4 checks — parity negative control VALID** |
| ↳ bandit on single gated module | 0 | **exit 0, 0 findings, 2.2s** |
| ↳ bandit on unsuppressed fixture | 1 | exit 1, 1 × `B608`, 1.4s |
| ↳ bandit on suppressed fixture | 0 | exit 0, 0 findings, 1.7s |
| ↳ bandit on mixed fixture | 1 | exit 1, 1 finding, 1.1s |
| Read 022 full DDL; classify both `DO $$` blocks | 0 | §9.1 — trigger block is read-only |
| `access_token=` / `def …access_token` / `.retryable` enumeration | 0 | §8.1, §8.2 |
| `Test-Path key_manager.py`; vault accessor enumeration | 0 | §8.3 |

**Reused (not re-executed), justified by identical HEAD, file hashes and tool versions:** full-tree
bandit (exit 1, 9 findings); `140 passed` drift/invariants; quota `60 passed / 5 skipped`; debounce
`9 passed / 1 xfailed`; the gate test `3 passed in 112.92s`.

**Not executed:** any CI run or dispatch; any PR; any deployment; `terraform validate`/`plan`/
`apply`; any Trivy scan; any PostgreSQL or Redis connection; the 4 tenant lane files; the real-Redis
quota lane; any live exchange call.

**One PowerShell defect hit and corrected:** `Select-String -Recurse` is not a valid parameter
(`NamedParameterNotFound`); re-run as `Get-ChildItem -Recurse | Select-String`.

---

## 11. Files written

| File | Nature |
|---|---|
| `staging/probe_p19_frontend_gate_negative_control.py` | gate-assertion negative control |
| `staging/p19_frontend_gate_negative_control.txt` | its output |
| `staging/probe_p19_bandit_parity_negative_control.py` | Bandit parity negative control |
| `staging/p19_bandit_parity_negative_control.txt` | its output |
| `staging/PHASE19_FRONTEND_GATE_AND_CRITICAL_CLOSURE.md` | this report |

All new, all untracked, all under `staging/`. **No governed file touched; no grant exercised.**

---

## 12. Remaining blockers, owners, and copy-ready next actions

| # | Blocker | Owner | Next action |
|---|---|---|---|
| **1** | **Frontend publishes with no security gate** — design now validated 7/7 | workflow / release owner | *"I authorize adding a `security-gate` job to `.github/workflows/06-frontend-deploy.yml` that runs `scripts/require_security_scan.py` with `DEPLOY_SHA: ${{ github.sha }}` and `ALLOW_UNSCANNED: 'false'`, adds an explicit `permissions:` block including `actions: read`, and makes `deploy-frontend` depend on it via `needs:`; plus `tests/test_frontend_deploy_is_gated.py` holding assertions A1–A6 and the B1–B3 backend regression lock. No other workflow change."* |
| **2** | **Frontend correctness unreachable on push** | release owner + coordinator | Choose Option 1 (PR-only `main` via branch protection — **recommended**, closes both paths), Option 2, or Option 3 (§5) |
| **3** | `05 Security` red → `main` undeployable | code owner (Bandit) + infrastructure owner (Trivy) | *"I authorize B1 (parity test over `-r backend_app -ll` via subprocess, marked slow) **then** B2 (`# nosec B608` with per-site justification on the 9 lines) and B3 (remove the stale suppression at `backend_app/test_perf.py:26`)."* · *"I authorize removing the two Redis egress rules (`security_groups.tf:73-78`, `redis-only/main.tf:58-63`) and documented per-resource acceptance for findings 1–3."* |
| **4** | No correctness/PostgreSQL evidence at `ecd23a9e` | coordinator | Authorize a PR carrying the SHA; confirm `PROVISION_DATABASE_URL` is the disposable loopback service. **Do not "optimize" the double provisioning** — it is an intentional idempotency guard |
| **5** | D7-1 three invalid call sites | router owner | Authorize editing **only** the 3 `load_decrypted_keys` callers plus the test double — explicitly **excluding** the 9 legitimate `access_token` sites in §8.1 |
| **6** | D7-2 false-green health | portfolio-cache owner | Authorize consuming `dependencies.py:659 get_vault()`; health semantics as a separate grant |
| **7** | W-6 misclassification | execution-state owner + coordinator | Authorize ladder reorder + bounded auth matching + regression tests; **no retry change** (`retryable` is log-only, §8.2) |
| **8** | TENANT-01 uncertified | tenancy owner | Approve a disposable PostgreSQL service and a non-owner, non-superuser role; run the 4 tenant lane files. **Do not enable FORCE RLS first** |
| **9** | A23 Redis P1 — 0 of 8 | code owner | Establish the register Owner row **first**, then the A23E6 §3 grants |
| **10** | W1C-02 scope, criterion 6 uncommitted, D5 sign-off, ledger `:802` | code owner | One reply resolving all four |
| **11** | W-4 `UNKNOWN` exit semantics | execution-state owner | Decide before any W-5 work |

---

## 13. What was NOT executed, stated explicitly

No CI run was dispatched and no PR created. No deployment of any artifact. No `terraform`
validate/plan/apply and **no Trivy scan at all** — both tools are absent, so no scan is claimed to
have passed. No PostgreSQL or Redis connection; the 12-file PostgreSQL lane and the real-Redis quota
lane were **not run** this phase. No production access, no exchange call, no live order. No FORCE
RLS. No commit, push, stage, reset, stash, clean, revert or history rewrite. No governed file
edited, so no diff to review beyond the five `staging/` artifacts in §11.

No scanner finding was suppressed, no threshold lowered, no test deleted, and **no skip or xfail was
converted into a pass**. Nothing is reported as passing that did not run.

| Final posture | |
|---|---|
| Frontend release path | **FAIL-OPEN** — gate design validated 7/7, **grant ABSENT** |
| Backend release path | **FAIL-CLOSED on every reachable path** — executed regression lock passed |
| Bandit parity | **gap measured** (single module exit 0 vs tree exit 1); parity test + both negative controls validated, **grant ABSENT** |
| Trivy | 5 CRITICAL, individually dispositioned; **no scan executed**, **0 fixed**, grant ABSENT |
| D7-1 / D7-2 / W-6 | revalidated with new protective detail; **grants ABSENT**, unmodified |
| **PostgreSQL** | **UNVERIFIED** |
| **TENANT-01** | **NOT CERTIFIED** |
| QUOTA-01 / A23 | **OPEN** / **NOT STARTED (0 of 8)** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
