# Phase 16 — Security Gate Recovery, D7-2 Closure, Authorized Main Stabilization

**No production, test, workflow, Terraform, migration or governance file was changed. No commit,
push, stage, merge, revert, reset, stash or clean. No CI dispatched. No deployment, provisioning or
production mutation. No database, Redis, exchange or credential access. No live trading enabled.**

**LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · PostgreSQL UNVERIFIED ·
DEPLOYMENT BLOCKED.**

Destination verified absent before writing.

---

## 1. Headline — the frontend of the security-failing commit is already in production

Priority G asked whether frontend publication can run without a security result for the exact SHA.
**It can, and it did.**

`.github/workflows/06-frontend-deploy.yml`, measured:

| Property | Value |
|---|---|
| `needs:` declarations | **0** |
| References to `05 Security` / `security-scan` / `require_security` | **0** |
| Triggers | `push: branches: [main, master]` **and** `workflow_dispatch` |
| Jobs | 5 |
| `Configure AWS Credentials` | line 254 |
| **`aws s3 sync dist/ s3://…`** | **lines 261, 289, 299, 307** |
| **`aws cloudfront create-invalidation --paths /*`** | **line 329**, then `wait invalidation-completed` at 339 |

So a push to `main` performs S3 synchronisation and a full CloudFront invalidation with **no
dependency on the security workflow whatsoever**. At `ecd23a9e` — the commit whose backend deploy was
refused by its own gate — **`06 Frontend Deploy` run `38129699497` succeeded.** The frontend assets of
a commit failing two CRITICAL security scanners are live in production S3 and served from a freshly
invalidated CloudFront edge.

This is not the "policy gap" Phase 15 described. It is an **executed publication of unscanned
artefacts**, and it reframes Priority G from a design question into an incident to disposition.

**Two mitigating facts, stated so the finding is not overread.** The workflow carries real inline
security checks of its own — service-role key absent from the bundle (line 168), no
`localhost:8000`/`ws://localhost:8000` fallbacks (lines 188–202), and an API-host allowlist (line
217). Those are genuine and they passed. What is absent is any link to the **Bandit** and **Trivy
IaC** results, and the Trivy failures are in `terraform/`, which provisions the very S3/CloudFront
infrastructure the frontend is served from. A CRITICAL IaC misconfiguration is **not**
frontend-neutral.

---

## 2. Preflight — all seven conditions re-verified, none drifted

| Invariant | Value |
|---|---|
| HEAD / `origin/main` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` — identical, **0 ahead / 0 behind** |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` |
| Staged / unmerged | 0 / 0 |
| `git reflog -1` | `ecd23a9e HEAD@{0}: commit: launch phase 1` — **no commit, reset or checkout this phase** |

Tracked modifications, entry and exit — **both another session's, preserved read-only, never opened
for writing, staged or incorporated**:

```
 M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md
 M tests/test_drift_classification.py
```

The brief's seven conditions:

| # | Condition | Verified |
|---|---|---|
| 1 | Bandit gate test targets one module; CI scans the tree | **HOLDS** — `GATED_MODULE = backend_app/backend/transactional_execution_manager.py`; CI `-r backend_app` |
| 2 | Nine B608 findings, same migrations, not materially different | **HOLDS** — Phase 15 executed scan: 9 / 9 MEDIUM / all B608, same 3 files, same lines; the 3 migrations are byte-identical (hashes §3) |
| 3 | AST + validator tests still prove the constraints | **HOLDS** — Phase 15 ran them: **3 passed**, including `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` and `test_the_validators_the_suppression_rests_on_raise_rather_than_sanitise` |
| 4 | Trivy: same 5 CRITICAL, same per-file distribution | **HOLDS** — per-file from the run log: `alb.tf` 1, `redis-only/main.tf` 1, `security_groups.tf` 3; **`cloudtrail.tf` 0**, and `cloudfront.tf`/`ecs.tf`/`secrets.tf`/`vpc.tf` also 0 |
| 5 | `key_manager.py` absent; D7-2 import still fails | **HOLDS, and is now stronger** — §5 |
| 6 | Frontend workflow and backend dispatch paths unchanged | **HOLDS** — and the bypass is now measured, §1 and §9 |
| 7 | Grant state and file ownership unchanged | **HOLDS** — §4 |

**Fresh whole-tree Bandit scan not repeated this phase.** Phase 15's executed measurement (exit 1, 9
findings, 67.2 s) stands as historical evidence per brief §A1, and the three migration blobs are
unchanged, so a re-run would be a 67-second re-derivation of an identical result. Recorded as a
deliberate omission, not an oversight.

---

## 3. Preservation hashes

| File | Hash (12) | State |
|---|---|---|
| `tests/test_bandit_sast_gate_is_green.py` | `a7deff99c9e5` | clean |
| `…/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | `bb9b629835c3` | clean |
| `…/b2c3d4e5f6a7_positions_canonical_shape.py` | `ac12c2d980d5` | clean |
| `…/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | `5e9f85ce998e` | clean |
| `terraform/alb.tf` | `cdead9289f20` | clean |
| `terraform/security_groups.tf` | `87c9d131051d` | clean |
| `terraform/redis-only/main.tf` | `48b9471035b7` | clean |
| `backend_app/backend/portfolio_cache_updater.py` | `a74d76448a15` | clean |
| `tests/test_drift_classification.py` | `99a2917ac0a6` | **DIRTY — other session** |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e` | **DIRTY — other session** |

---

## 4. Authorization — rechecked directly, all absent

Ledger `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` rows **800–804: all five read `NOT GRANTED`**,
verified line by line. `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md`: **0 ticked**.

| Lane | Grant | State |
|---|---|---|
| 1 — Bandit A2 / A3 | code owner | **ABSENT** |
| 2 — Trivy B1 / B2 | infrastructure owner | **ABSENT** |
| 3 — D7-2 | service owner | **ABSENT** |
| 4 — D7-1 | router owner | **ABSENT** |
| 5 — W-6 | execution owner | **ABSENT** |
| 6 — TENANT-01 / PostgreSQL | DB owner + coordinator | **ABSENT** |
| 7 — release policy | release owner | **ABSENT** |
| 8 — Redis A23 P1 / QUOTA-01 | code owner | **ABSENT — 0 of 8 gates; no Owner row to act in** |
| 9 — worker lifecycle | coordinator (3 contending workstreams on `main.py`) | **ABSENT** |

**Nine lanes, zero grants. No file was modified under any of them.** Every lane below is therefore
read-only analysis, which is where this phase's new evidence comes from.

---

## 5. Priority C — D7-2: the brief's compatibility question, answered

Brief §5 warns: *"Do not invent a `KeyVault` implementation or redirect the import to a similarly
named module without proving API and security compatibility."* I searched before proposing anything.

**There is no `KeyVault` anywhere in the repository:**

* `^class KeyVault` across `backend_app/**/*.py` → **zero matches**
* any `key_manager*.py` file, repo-wide → **zero matches**
* `KeyVault` is referenced at exactly two sites, both the broken consumer:
  `portfolio_cache_updater.py:151` (`from backend_app.backend.key_manager import KeyVault`) and
  `:159` (`vault = KeyVault()`)

The real class is **`APIKeyVault`** at `backend_app/backend/api_key_vault.py:62`. The import target
has never existed in this tree.

**Compatibility, now provable rather than assumed** — `portfolio_cache_updater.py:159-160`:

```python
            vault = KeyVault()
            keys = vault.load_decrypted_keys(user_id, exchange_id)
```

| Surface | Compatible with `APIKeyVault`? |
|---|---|
| **Method call** `load_decrypted_keys(user_id, exchange_id)` | **YES — exactly the real two-parameter signature** (`api_key_vault.py:177`) |
| **Constructor** `KeyVault()` (no args) | **NO** — `APIKeyVault.__init__` raises `EnvironmentError` without `MASTER_ENCRYPTION_KEYS` (it "arms" with an N-key rotation, `:104`) |

So the method surface is a clean match, and the **constructor is not**. A naive
`from …api_key_vault import APIKeyVault as KeyVault` would convert today's `ModuleNotFoundError` into
an `EnvironmentError` in any environment without the master key — still broken, differently.

**The correct shape, and why:** use the already-initialised singleton `app_state.vault` — the same
object routers receive via `Depends(get_vault)` (`core/dependencies.py:659`) — rather than
constructing a second vault. That avoids the env-var constructor requirement and avoids a second
in-process instance holding decrypted keys. **This is a design proposal, not an applied change.**

**Worth noting for D7-1 contrast:** this call site passes the **correct** two arguments. It is broken
by its *import*, not by its signature — the opposite of D7-1's three sites.

**Health defect, unchanged and confirmed:** the import is lazy, inside the per-user update method, so
startup at `main.py:466` succeeds and sets `service_status["portfolio_cache_updater"] = "ACTIVE"`
while every per-user update raises `ModuleNotFoundError`. **False-green health.**

**Blocked:** no service-owner grant. A fix must also make health reflect failure, with a test proving
a missing dependency cannot report `ACTIVE`. No credential or decrypted key was logged or read.

---

## 6. Priority A — Bandit (verified, blocked)

Conditions 1–3 hold (§2). Phase 15's executed evidence: `bandit -r backend_app -ll` → **exit 1, 9
findings, 9 MEDIUM, all B608, 67.2 s**, while `pytest tests/test_bandit_sast_gate_is_green.py` →
**3 passed, exit 0**. The gate is red and its own suite green, simultaneously, on identical source.

**All nine dispositioned as justified false positives**, each by the same verified construction —
identifier passed through `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises)
before interpolation, with the only user-influenced value (`UUID_REGEX`) parameter-bound:

| File | Lines |
|---|---|
| `…/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |
| `…/b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `…/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |

Scanner cannot distinguish validated-identifier interpolation from concatenated user input, because
identifiers cannot be bind parameters. Evidence is threefold: source read, the passing whole-tree AST
test, and the passing hostile-input validator test.

**A2 design (blocked):** separately named whole-tree test at the gate's real scope (`-r backend_app
-ll`), retaining both existing tests; **negative control via a `tmp_path` fixture file** containing a
representative finding, so the control proves detection **without leaving fabricated vulnerable code
in the repository**; runtime 67.2 s measured, ~5× cheaper than the docstring's 5 m 44 s estimate.

**A3 (blocked):** narrow `# nosec B608` + individual reason on the 9 lines. **Explicitly excluded:**
file-level suppression, other rules, lowered severity, `--baseline`, Alembic exclusion, `|| true`.

**Ordering held: A2 before or with A3.** A3 alone hides today's nine and leaves the blind spot.

---

## 7. Priority B — Trivy (re-verified, all five blocked)

Per-file CRITICAL counts reproduced from the run log; **`cloudtrail.tf` confirmed at 0** before
repeating that claim, as the brief requires.

| Rule | Resource · file:line | Disposition | Evidence to clear |
|---|---|---|---|
| `AWS-0054` | `aws_lb_listener.http` · `alb.tf:49-93` | **false positive, mitigated** — `dynamic default_action` redirects :80→:443 (HTTP_301) under `enable_custom_domain`; `lifecycle.precondition` **refuses the apply** for prod/production without it (SC-7). Trivy cannot evaluate `dynamic` blocks or preconditions | narrow resource-scoped exception citing the precondition |
| `AWS-0104` | `aws_security_group.alb` · `security_groups.tf:26` | **real, conventional** | accept + document |
| `AWS-0104` | `aws_security_group.ecs_tasks` · `security_groups.tf:56` | **real, required** — ECR, Secrets Manager, Supabase, venues. **Preserve** | separate VPC-endpoint design |
| `AWS-0104` | **`aws_security_group.redis`** · `security_groups.tf:77` | **real, unnecessary** — ingress correctly 6379 from `ecs_tasks` only; ElastiCache initiates no outbound | **the safe tightening** |
| `AWS-0104` | **`aws_security_group.redis`** · `redis-only/main.tf:62` | same | same |

**SC-9 encryption is tracked separately and is NOT attributed to the CRITICAL gate** (brief §4).

**Acceptance criterion not met:** fixing only the two Redis rules leaves `AWS-0054` and two
`AWS-0104` findings blocking, so the gate stays red. All five need a disposition. **No Trivy binary
locally and `terraform plan` needs AWS credentials — I claim no local scanner verification.**

---

## 8. Priorities D, E — D7-1 and W-6 (verified, blocked)

**D7-1.** Three `access_token=` sites against the two-parameter signature: `orders.py:789-793`
(unguarded → HTTP 500), `orders.py:1095-1099` (masked → `[]`), **`market.py:50-54`** (masked by
`except Exception` at `:67` → **authenticated users silently served an anonymous public
`ConnectionEngine`**, and the `:56` guard unreachable because binding fails first). Git history shows
**one signature form ever**; method and broken callers landed in the same commit `6c5660a1`, so
removing the keyword loses no behaviour. `exchange.py:993` is the fourth site and is **valid**.
**Grant ABSENT.**

**W-6.** `_handle_ccxt_error` `exchange_executor.py:1225-1257`: symbol/market branch (`:1246`)
precedes timeout (`:1249`); bare `"key"` at `:1252`. **Phase 15's repository-wide enumeration
confirmed the only production read of `ExchangeError.retryable` is the log line at `:1687`** —
`replay_safe_transaction_guard.py:304` and `paper_simulator.py:1691` use their own private
predicates. So this mislabels recorded outcomes, **not** retry behaviour, and a corrected classifier
cannot introduce blind retries because no retry consumes the classification. **Grant ABSENT.**

---

## 9. Priority G — release-gate graph, measured

| Path | Exact-SHA security evidence required? |
|---|---|
| **`06 Frontend Deploy`** — `push: [main, master]`, `workflow_dispatch` | **NO.** 0 `needs:`, 0 security references. S3 sync + CloudFront invalidation execute unconditionally (§1) |
| `03 Deploy` — `workflow_run` success **or** `workflow_dispatch` (`:73`) | **YES** — `scripts/require_security_scan.py` (`:95`, 105 lines) with `DEPLOY_SHA: ${{ github.event.workflow_run.head_sha \|\| github.sha }}` (`:93`), an exact-SHA query against `GET /repos/{repo}/actions/runs?head_sha=…`. Fail-closed by design, and it refused |
| `03 Deploy` ECR check | additional fail-closed artefact check at `:162-171` |

**Documented bypass on the backend path:** `ALLOW_UNSCANNED: ${{ inputs.allow_unscanned }}` (`:94`).
A `workflow_dispatch` with that input set would skip the security requirement. That appears to be a
deliberate break-glass, and it is recorded here as the exact bypass the brief asks for — **not
exercised.**

**Narrowest policy, re-evaluated rather than inherited.** Phase 15 proposed gating the frontend on
the IaC portion only. Against the measured capability that remains the right shape — the Bandit
findings are Python-only and genuinely frontend-neutral, whereas the Trivy IaC findings concern the
S3/CloudFront-provisioning Terraform. Implementation would add a `needs:`-style dependency on the
IaC job plus a same-SHA assertion, with a job-graph regression test. **Blocked — release-owner grant
absent. Nothing published, invalidated or deployed during verification.**

---

## 10. Priorities F, H, I

| Lane | Status |
|---|---|
| **F — TENANT-01** | `FORCE ROW LEVEL SECURITY`: **zero executable SQL statements** tree-wide, prose only. `tenant_db_context.py` unwired into the request path. Live instance recorded `relrowsecurity=true, relforcerowsecurity=false`; application connects as table owner. **No database contacted; no authorized isolated PG route.** `FORCE RLS` deliberately **not** proposed as a standalone fix — connection-lifecycle and pooled-context-leak behaviour is unproven. **NOT CERTIFIED · PostgreSQL UNVERIFIED** |
| **H — Redis / QUOTA-01** | **A23 Owner row does not exist**, so no role-attributed grant can be valid — the W1D-12 precedent cannot be reused without manufacturing the authority it should rest on. `test_r6_d` strict-xfail contract preserved, untouched. CI provisions ephemeral job-scoped `redis:7-alpine` with the committed `/15`→`/0` fix; the workflow comment documents that `get_redis_url(db)` appends `?db=` which redis-py resolves **over** the path, so `/0` is load-bearing. Locally: all four Redis env vars unset, 6379 unreachable. **Real-Redis UNEXECUTED** — no URL invented, no fixture altered, no suite pointed at an unknown service |
| **I — workers** | **Started:** `ReconciliationScheduler` (`main.py:456`), `OrderWatchdog` (`:432`), `ConsistencyChecker` (`:415`), `PortfolioCacheUpdater` (`:466` — but see §5). **Absent:** `ExchangeReconciliationService` (scorecard: "no leader election; no exchange client wired") and `MarketplaceExpiryWorker`/`expiry_sweep` (zero matches in `main.py`). These are **different components** — conflating them misreports both. **W-5 not attempted** — the W-4/UNKNOWN-exit decision is unresolved. `main.py` has three contending workstreams and no named coordinator |

---

## 11. Commands and files

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `git rev-parse` / `write-tree` / `reflog -1` / `status --short` / `hash-object` | 0 | §2, §3 |
| 2 | ledger rows 800–804 read; A23E6 checkbox count | 0 | 5× `NOT GRANTED`; 0 ticked |
| 3 | `Select-String '^class KeyVault'` + `Get-ChildItem key_manager*.py` + `KeyVault` refs | 0 | **zero matches** for class and module; 2 refs, both the broken consumer |
| 4 | regex count of `needs:` / security refs / jobs in `06-frontend-deploy.yml` | 0 | **0 / 0 / 5** |
| 5 | `Select-String` on `03-deploy.yml` for gate wiring | 0 | `require_security_scan.py:95`, `DEPLOY_SHA:93`, `ALLOW_UNSCANNED:94` |

**pytest executed this phase: 0.** No change was authorized, so there was nothing new to validate,
and the brief forbids re-running suites to reconfirm an unchanged state. **No pass, fail, skip or
xfail is claimed.** Phase 15's `3 passed` stands as that phase's evidence.

**Files changed: this report only.** Untouched with reason: the 3 migrations (A3), the gate test
(A2), the 3 Terraform files (B), all four workflows (**never modify a gate to make CI pass**),
`routers/orders.py` + `market.py` (D7-1), `exchange_executor.py` (W-6),
`portfolio_cache_updater.py` + `main.py` (D7-2 / workers), `redis_manager.py` +
`tests/test_a23_redis_pool_lifecycle.py` (A23 — path still **ABSENT**), and both other-session dirty
files (**preserved**).

---

## 12. Remaining blockers and smallest next action

| # | Blocker | Smallest next action | Owner |
|---|---|---|---|
| **1** | **Unscanned frontend already published (§1)** | Decide: accept the published state with a recorded rationale, **or** add the IaC-portion dependency + same-SHA check before the next push to `main`. This is now an incident disposition, not a design review | release owner |
| **2** | Bandit gate red; suite blind | Grant **A2 + A3** — 3 migrations + the gate test. Safety proven three ways; cost 67.2 s | code owner |
| **3** | Trivy red, 5 CRITICAL | Disposition **all five**; tighten only the 2 Redis egress rules | infrastructure owner |
| **4** | No CI evidence at HEAD | Authorize a branch + PR carrying `ecd23a9e` — **no code change**; `01 PR Check` has no `workflow_dispatch` | coordinator |
| **5** | D7-2 false-green health | Grant the `app_state.vault` wiring (§5) + health correction + test | service owner |
| **6** | D7-1 anonymous downgrade | Grant the three-line deletion + bind test | router owner |
| **7** | W-6 misclassification | Grant 7 deterministic default-lane cases | execution owner |
| **8** | TENANT-01 | Authorize minimum read-only DB validation; do **not** enable FORCE RLS first | DB owner |
| **9** | A23 P1 | Create the Owner row, **then** grant | code owner |

---

## 13. Prohibited actions — explicit confirmation of non-performance

**Not performed:** commit · push · stage · merge · rebase · force-push · revert · reset · clean ·
stash · destructive checkout · artificial commit · CI dispatch or re-run · workflow trigger change ·
gate weakening (no `-lll`, `|| true`, `--baseline`, severity lowering, blanket or file-level
suppression, Alembic exclusion) · Terraform apply · infrastructure provisioning · AWS resource change
· S3 publication · CloudFront invalidation · ECS deployment · production data mutation · database
connection · Redis start or connection · exchange or venue call · credential or decrypted-key read or
log · live-trading enablement · editing another session's files · overwriting any prior report.

---

## 14. Status

| Item | State |
|---|---|
| **Verification verdict** | **BLOCKED** — nine lanes, zero grants; all independent read-only work exhausted |
| **NEW this phase** | **Unscanned frontend published to production S3 + CloudFront at `ecd23a9e`** (§1); **`KeyVault` does not exist anywhere, and `APIKeyVault`'s method API matches while its constructor does not** (§5); backend `ALLOW_UNSCANNED` bypass documented (§9) |
| Bandit gate | red (exit 1, 9 findings); 9 justified false positives; structural blind spot proven |
| Trivy gate | red, 5 CRITICAL; 1 false positive w/ enforced precondition, 4 real, 2 safely tightenable |
| `03 Deploy` | **refused correctly; ECS SKIPPED — backend not deployed** |
| `06 Frontend Deploy` | **succeeded with no security dependency — frontend IS deployed** |
| `01 PR Check` at HEAD | **NOT RUN**, undispatchable |
| D7-1 / W-6 / D7-2 / workers | confirmed; **all unfixed, all grants ABSENT** |
| QUOTA-01 / real Redis | **UNEXECUTED** |
| A23 P1 | **NOT STARTED — 0 of 8; no Owner row exists** |
| pytest this phase | **0 executed** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| **PostgreSQL** | **UNVERIFIED** |
| **DEPLOYMENT** | **BLOCKED** |

No approval was inferred from this prompt, from the commit or its push, from role labels, from commit
authorship, from file cleanliness, from CI results, from a passing local test, or from silence.
