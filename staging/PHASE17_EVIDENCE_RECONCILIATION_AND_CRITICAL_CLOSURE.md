# Phase 17 — Evidence Reconciliation, Security Gate Recovery, Parallel Critical-Blocker Closure

**0 production files · 0 test files · 0 workflows · 0 Terraform · 0 migrations · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 services started.**
Destination verified absent before writing. 1 new file: this report.

**LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED · DEPLOYMENT: BLOCKED.**

---

## 1. Executive summary

Every implementation item remains **ungranted**, so this phase did read-only verification and
grant preparation. Four results are new, and two of them correct the brief's own framing.

| # | Finding | Status |
|---|---|---|
| **1** | **The Bandit parity gap has a root cause, and it is not "the test is narrower".** The gate test is *deliberately* one file (`GATED_MODULE`), with an AST test meant to cover the rest — but the AST test asserts a **different property** (interpolated values are validator-constrained) than B608 detects (string-building into SQL). **Both can be true at once**, which is exactly what is happening. | **MEASURED** |
| **2** | **The frontend release path has no gate at all.** `06-frontend-deploy.yml` has **`needs:` count = 0** across the whole file, yet runs `aws s3 sync` ×3 and `aws cloudfront create-invalidation`. Backend has `needs: pre-deployment-validation` → `require_security_scan.py`. Asymmetry revalidated as **still live**. | **MEASURED** |
| **3** | **Trivy count discrepancy, and a correction to the brief.** I measure **3** unrestricted egress rules in the blocking-scanned `terraform/` tree; Phase 15 recorded **4** `AWS-0104`. Trivy is **not installed locally**, so I cannot reconcile. Also: there is **ONE** Redis egress rule, not two. | **DISCREPANCY — OPEN** |
| **4** | **`AWS-0054` false-positive claim verified first-hand** against current Terraform: `alb.tf` carries both a conditional :80→:443 redirect and a `lifecycle.precondition` that **refuses the apply** for prod without a custom domain. Trivy evaluates neither `dynamic` blocks nor preconditions. | **VERIFIED** |

All four Phase 13 defect lanes **revalidated as still present and uncorrected**. Zero grants,
independently restated by Phase 19 (*"Authorizations exercised: none"*) and A23E17
(*"0 implementations authorized, 0 production files changed, 0 test files created"*).

Vocabulary: **OBSERVED** (read from source) · **MEASURED** (a probe produced a number this
pass) · **TESTED** (a test executed this pass) · **REUSED** (prior evidence, inputs verified
unchanged) · **APPROVED** · **IMPLEMENTED** · **DEPLOYED**.

---

## 2. Fresh baseline and preservation manifest

Measured at entry and re-measured at exit. **Prior conditions were not assumed.**

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| Branch · `origin/main` | `main` · `ecd23a9e…` (identical) | unchanged |
| Ahead / behind | **0 / 0** | 0 / 0 |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **`4b45a763…` — identical** |
| Staged entries | 0 | **0** |
| **Files differing from `HEAD`** | **2** | **2 — the same two** |
| Unmerged paths · worktrees | 0 · 1 | 0 · 1 |
| Porcelain | 82 | 83 (this report) |

`git write-tree` is byte-identical at entry and exit — **the strongest available statement
that this phase changed no tracked content.**

### 2.1 Concurrent modifications, preserved

| Path | State | Disposition |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | modified vs `HEAD` | **not touched** |
| `tests/test_drift_classification.py` | modified vs `HEAD` (+143/−0) | **not touched** — the `AP-W1D-3` authorized workstream's output |

The historical baseline in the brief is confirmed accurate: HEAD is still `ecd23a9e`.

### 2.2 A concurrent session is already executing Phase 17

These artifacts existed **before** this phase began and are **another session's**:

| Artifact | Nature |
|---|---|
| `staging/phase17_bandit_wholetree.json` · `phase17_bandit_stderr.txt` | whole-tree Bandit scan output |
| `staging/phase17_bandit_gate_tests.txt` | `tests/test_bandit_sast_gate_is_green.py` → **3 passed in 96.75s** |
| `staging/phase17_frontend_release_gate.py` · `phase17_test_frontend_release_gate.py` | frontend-gate **prototype** |
| `staging/phase17_gate_tests.txt` | prototype suite → **42 passed in 7.17s** |
| `staging/PHASE19_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` · `p18_bandit_citest.json` | later-phase sessions |

**All read read-only and left exactly as found.** None was edited, moved or overwritten. My
destination `PHASE17_EVIDENCE_RECONCILIATION_AND_CRITICAL_CLOSURE.md` was **verified absent**
before writing, so no name collision with the concurrent session's `phase17_*` files.

**Two further Phase 17 reports appeared from other sessions while this one was being written**
— `PHASE17_D_P15_4_RELEASE_GATE_AND_PARALLEL_CLOSURE.md` and
`PHASE17_RELEASE_GATE_AND_CRITICAL_DEFECT_CLOSURE.md`. **Three sessions have now written a
Phase 17 report under three distinct filenames.** None overwrote another; all `phase17_*`
artifact timestamps are intact. A reader reconciling this phase should expect three
independent accounts, and should treat agreement between them as corroboration rather than
assuming one supersedes the others. This report does not claim precedence over either sibling.

**One point of care about the 42-passing prototype:** both gate files live under `staging/`,
not `tests/`. `pytest.ini` sets `testpaths = tests`, so **the prototype does not run in the
default lane** and the 42 passes are not suite coverage. It is a design artifact pending a
grant to land in `tests/`. Reported as such, not as a closed gate.

### 2.3 Shared resources preserved

| Resource | State | Action |
|---|---|---|
| PostgreSQL | port **51815 OPEN**; 51805 **CLOSED** | none |
| `staging/pg_dsn.txt` | reads `…:51805/postgres` — **stale** | **NOT EDITED.** The brief prohibits repointing it; every PG-gated test and concurrent session reads it |
| Redis | `AERORA_TEST_REDIS_URL` **unset**; port 6379 **CLOSED** | none — not started |

---

## 3. Reports reconciled

| Report | Reconciliation |
|---|---|
| `PHASE13_CRITICAL_DEFECT_CLOSURE_AND_TENANT_SAFETY.md` | All four defect lanes **revalidated as still present** (§6-§9). RLS and FORCE evidence **REUSED** — inputs verified unchanged. |
| `PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` | Bandit 9×B608 and Trivy dispositions **confirmed**; `01 PR Check` never ran at this HEAD — **still true** (§11). |
| `PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md`, `PHASE15_SECURITY_GATE_CLOSURE_AND_MAIN_VERIFICATION.md` | Bandit 9 findings **confirmed exactly**; 3-test gate pass **confirmed**; frontend asymmetry **confirmed**. **One count not reproduced:** 4 × `AWS-0104` vs my measured 3 (§10.3). |
| `PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md`, `PHASE16_SECURITY_RELEASE_AND_CRITICAL_CLOSURE.md` | Agree: no grant supplied; `AP-W1D-4` NOT GRANTED; snapshot must not be regenerated to force green. |
| `PHASE19_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` | *"Authorizations exercised: none."* Ledger `:802` still `NOT GRANTED`. **Corroborates §4.** |
| `PHASE11B_A23E17_EXECUTED_EVIDENCE_AND_W6_MEASUREMENT.md` | *"0 implementations authorized."* Also: the P2 tripwire **must stay XFAIL(strict)** — preserved, untouched. |

### 3.1 Contradictions resolved

1. **"Two Redis egress rules"** (brief §9) — **incorrect.** `terraform/security_groups.tf`
   defines exactly three security groups with one egress rule each; `aws_security_group.redis`
   has **one** (`:73-78`). Corrected in §10.3.
2. **4 × `AWS-0104` vs 3 measured** — **unresolved, and recorded as unresolved.** Trivy is not
   installed locally and `aerora_quant_platform/infrastructure/terraform/main.tf` contains **no
   `0.0.0.0/0` match**, so the fourth finding's location is not established. Not papered over.
3. **"Bandit test is narrower than CI"** — true but **insufficient as a diagnosis.** §9.2 gives
   the actual mechanism.

---

## 4. Grant ledger — 0 grants for any Phase 17 item

| ID | Scope | Status | Source |
|---|---|---|---|
| `AP-W1D-1` (ledger 800) | DB owner, read access | **NOT GRANTED** | `A23E16:77` |
| `AP-W1D-2` (801) | DB owner | **NOT GRANTED** | `A23E16:78` |
| `AP-W1D-3` (802) | Code owner — four-file classifier scope | **GRANTED in W1D-12, ledger row still `NOT GRANTED`** | `W1D12:19`; `A23E16:79,83` |
| `AP-W1D-4` (803) | DB + code owner — snapshot recapture | **NOT GRANTED** | `A23E16:80`; `PHASE16:508` |
| `AP-W1D-5` (804) | Coordinator + DB owner | **NOT GRANTED** | `A23E16:81` |
| `D1`–`D4` | drift-classification behaviour | **GRANTED** | `W1D12:20-23` |
| `D5` | preserve `check_drift()` severity routing | **CONDITIONALLY GRANTED — condition NOT satisfied** | `W1D12:24` |
| `A23E6` form | Redis lifecycle | **12 ☐ empty, 0 marked, 6 blank** | `A23E14:29` |
| `D-P15-1`, `D-P15-3`, `D-P15-4` | security gate / main validation / frontend gate | **NO GRANT RECORDED** | §4.1 |
| `P11-B08` | backend deploy gate | **CLOSED — no regression found** (§11.2) | this phase |

**`AP-W1D-3` is not transferable.** Its own text: *"No other files are authorized."* It covers
drift classification in `scripts/verify_provisioning.py` — which is why
`tests/test_drift_classification.py` is legitimately modified in the working tree. It confers
nothing on the vault, classifier, RLS, Redis, workflows or Terraform.

`A23E17:75` adds a reading caution worth preserving: *"A29's three bare `GRANTED` tokens all
resolve to `AP-W1D-3`"* — a token search alone over-counts grants.

### 4.1 `D-P15-*` status

`D-P15-1`, `D-P15-3` and `D-P15-4` exist as **decision records**, not implementation grants.
Per the brief, *"A decision record is not automatically an implementation grant."* No approver,
no checked approval box, no scope statement authorizing a workflow edit was found for any of
the three. `PHASE16:524` quotes the intended narrow scope — *"`03-deploy.yml:73`. No other
workflow change is authorized."* — attributed to a **workflow / release owner** who has not
signed. **I reused `D-P15-4` rather than creating a duplicate ID**, as instructed.

---

## 5. Workstream status at a glance

| # | Lane | Initial | Action taken | Final |
|---|---|---|---|---|
| 1 | Bandit parity | 9 findings, 3 tests green | root cause identified (§9.2) | **OPEN — regression test ungranted** |
| 2 | Trivy IaC | 5 CRITICAL | AWS-0054 verified FP; egress enumerated | **OPEN — 1 count unreconciled** |
| 3 | D7-2 updater | import broken | revalidated; health contract read | **OPEN — ungranted** |
| 4 | D7-1 signature | 3 bad call sites | revalidated, all 3 present | **OPEN — ungranted** |
| 5 | W-6 classifier | 10/20 misclassified | inputs verified unchanged → **REUSED** | **OPEN — ungranted** |
| 6 | Tenant context / RLS | 0 consumers | revalidated: **0 external consumers** | **NOT CERTIFIED** |
| 7 | D6-D vault import | bare name, mock fallback | revalidated | **OPEN — ungranted** |
| 8 | Deployment gates | frontend ungated | `needs:`=0 measured | **DEPLOYMENT BLOCKED** |
| 9 | PostgreSQL / D6-C / Redis | fixtures verified | PG port checked, DSN preserved | **PRODUCTION EVIDENCE NOT SUPPLIED** |
| 10 | W-3 / W-4 / W-5 | `UNKNOWN` has no exit | order constraints reconfirmed | **OPEN — ungranted** |

---

## 6. Priority 2 — D7-1 key-vault call-site mismatch (REVALIDATED)

Real signature: `api_key_vault.py:177` —
`def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict`.

All three invalid calls **still present, uncorrected**:

| # | Site | Guard | Runtime consequence |
|---|---|---|---|
| 1 | `routers/orders.py:792` (`get_open_orders`) | **none** — first statement of body | `TypeError` → **unhandled 500 on every request** |
| 2 | `routers/orders.py:1098` (trade history) | `except Exception` → `logger.warning`; `return []` | **empty history masquerades as success** |
| 3 | `routers/market.py:53` (`_get_data_engine`) | `except Exception` → `ConnectionEngine` | **silent fallback to unauthenticated public market data** |

**The patch must stay surgical.** `access_token=user.get("access_token")` is **valid** at
`routers/risk.py:313,325,407` and `routers/paper_trading.py:88,103,141,161,193,250,282,305` —
`paper_trading_service` methods accept it. A sweep removing all occurrences would break paper
trading. Only the three `load_decrypted_keys` calls are wrong.

**Not a removal of an auth control:** the method never accepted a token; it scopes by
`user_id`, still passed. Authentication remains at `Depends(get_current_user)`.

**Behaviour change requiring explicit approval:** site 3 currently falls back to public on
*every* call because its own `except Exception` swallows the `TypeError`. After the fix it
would use real keys — changing what the venue sees (authenticated rate limits, user key used
for market data). The `market.py:56` `None`/partial-key guard must be **preserved**.

**Status: NOT GRANTED. Source and tests unchanged.**

---

## 7. Priority 3 — D7-2 cache updater (REVALIDATED)

`portfolio_cache_updater.py:151` — `from backend_app.backend.key_manager import KeyVault`.
**Still the only `key_manager` reference in `backend_app/`: the module does not exist.**

The call at `:159` is `load_decrypted_keys(user_id, exchange_id)` — **already the correct
2-argument form**. So D7-2 is purely the missing module, and is a **different failure path**
from D7-1 despite sharing a method name.

**False-green mechanism, re-read this pass:**

1. The broken import sits **inside `_update_user_portfolio`**, not at module scope, so
   `start_portfolio_cache_updater()` genuinely succeeds and `main.py:466` records `"ACTIVE"` —
   truthful about *startup*.
2. The per-user `ImportError` **is logged at ERROR** (`portfolio_cache_updater.py:131-133`).
3. **`/health/services` (`main.py:1077-1082`) reports only four services** —
   `consistency_checker`, `order_watchdog`, `pnl_engine`, `reconciliation_scheduler`.
   **`portfolio_cache_updater` is not among them**, so it contributes to neither
   `summary.*` nor overall `status`. It appears only in the `raw` passthrough as `"ACTIVE"`.
4. `main.py:1092-1095` already documents the staleness honestly (OBS-01) and exposes
   `_captured_at` / `as_of` / `_age_seconds`.

So the gap is **not** the documented staleness — it is **exclusion from the status
computation**, plus the fact that even a correct "did it start" answer is `ACTIVE` while the
loop fails every cycle for every user. Overall `status` can read `"ok"` indefinitely.

**Per the brief, scopes stay split:** (i) the import fix is small, needs confirmation that
`APIKeyVault`'s constructor and error semantics are compatible — *import compatibility is not
credential-access success*; (ii) the health-contract change needs separate owner approval of
heartbeat semantics, because the four services are not `WorkerBase` subclasses and have no
heartbeat. **No replacement credential module will be created to satisfy the import.**

**Status: NOT GRANTED for both scopes.**

---

## 8. Priorities 4 and 6 — D6-D vault import, tenant context

### 8.1 D6-D (REVALIDATED)

`backend_app/core/state.py:67` — `APIKeyVault = safe_import("APIKeyVault", "api_key_vault")`,
a **bare top-level name**. Phase 13 measured it unresolvable under both the repo-root path and
`pytest.ini`'s `pythonpath`; only `backend_app/backend/` on `sys.path` resolves it. Source
unchanged → **REUSED**. `safe_import` returns `MockEngine("MockAPIKeyVault")` on failure
(`state.py:63-64`), so **mock is the expected outcome**, and
`master_executor.py:255-257 keys["api_key"]` would raise `TypeError` **on the live order path**.

**This remains an import-path finding, not proof of what production loads.** Redacted evidence
request, unchanged and still **NOT SUPPLIED** — for staging and production separately:

1. Did `Failed to import APIKeyVault from api_key_vault` appear at boot? (boolean)
2. Did `DEV_MODE: APIKeyVault initialization failed, using mock` appear? (boolean)
3. Concrete type of `app_state.vault` — `APIKeyVault` or `MockEngine`.
4. Is `backend_app/backend` on the process `sys.path`? (boolean)

Rendered strings, from a direct enumerated read — `state.py:63` and `state.py:108`, both
WARNING. **Must be read from the boot log, not probed:** `MockEngine.__getattr__`
(`state.py:51-55`) answers any attribute with a no-op. **No credential, key, token or
decrypted material is to be queried, printed or attached.**

### 8.2 Tenant context (REVALIDATED — Priority 0)

A repo-wide search of `backend_app/**/*.py` for `tenant_db_context` and
`apply_tenant_context` returns hits **only inside `tenant_db_context.py` itself**.
**Zero external production consumers — confirmed still true.** `apply_tenant_context` is wired
into **no** request or worker path.

Phase 13's measured non-superuser experiment is **REUSED** (source unchanged): ENABLE + unset
GUC → **2 rows**; ENABLE+FORCE + unset GUC → **0 rows**, silently; FORCE + correct GUC → **1
row**; cross-tenant INSERT refused with SQLSTATE `42501`.

> **`D-13-4` (FORCE RLS) REMAINS BLOCKED.** Its prerequisites — context wiring, fail-closed
> behaviour, non-superuser read/write tests, rollback criteria — are **all unmet**.
> **A local superuser run must never be used as evidence that FORCE is safe:** local
> `postgres` is `rolsuper=True`/`rolbypassrls=True` and bypasses RLS unconditionally, so such
> a run returns a **false "safe"**. Supabase's `postgres` is not a superuser.

**Unresolved identity decision, blocking any wiring** — three expressions that agree only
because no token carries a `tenant_id` claim:

| Site | Expression |
|---|---|
| `core/dependencies.py:318` | `payload["tenant_id"] or app_metadata["tenant_id"] or sub` |
| `routers/orders.py:819`, `:940` | `UUID(user["id"])` — ignores `user["tenant_id"]` |
| `routers/distributed_execution.py:296` | `user["tenant_id"]` |

Also unresolved before FORCE: the **two permissively OR-combined policy families** (GUC-based,
role `public`; and `auth.uid()`-based, `authenticated`/`service_role`), and the
**`execution_records` predicate disagreement** — Family B treats `user_id` and `tenant_id` as
interchangeable (`auth.uid() = user_id OR auth.uid() = tenant_id`) while Family A compares only
`tenant_id::text` to the GUC.

**TENANT ISOLATION: NOT CERTIFIED.** Catalog results were measured on the **test cluster, not
production**.

---

## 9. Priority 5 — Bandit structural gate

### 9.1 Current findings (REUSED — identical command, unchanged source)

CI command, `.github/workflows/05-security.yml:36`:
`bandit -r backend_app -ll -f json -o reports/bandit-report.json` — **no `continue-on-error`
anywhere in `05-security.yml`**, so a non-zero exit fails the job.

**9 findings, all `B608` MEDIUM, all in `backend_app/alembic/versions/` (219,319 LOC scanned):**

| File | Lines |
|---|---|
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |

`tests/test_bandit_sast_gate_is_green.py` → **3 passed in 96.75s**. So **the suite is green
while CI is red.**

### 9.2 Root cause — the actual mechanism

Two things combine, and only together do they produce the gap.

**(a) The scanner test is scoped to one file, by design and with its reason stated.**
`tests/test_bandit_sast_gate_is_green.py:68`:

```python
GATED_MODULE = BACKEND_APP / "backend" / "transactional_execution_manager.py"
```

Its docstring: *"Same tool and same severity threshold as `05-security.yml`, **scoped to one
file** so it runs in about a second instead of the 5m44s a full `-r backend_app` sweep takes.
The AST test below is what covers the rest of the tree."*

**(b) The AST test asserts a different property than B608 detects.**
`test_every_interpolation_into_sql_is_constrained_before_it_gets_there` proves every value
interpolated into SQL passes a validator that **raises** (`SQL_SAFE_VALIDATORS = {json_text,
_validate_table_name}`). That is a **safety** property. B608 fires on **string-building into
SQL at all**, regardless of validation. **So the code can be safe and bandit can report 9
findings simultaneously** — which is the present state.

The intended coverage handoff therefore **cannot work as designed**: the AST test is not a
substitute for scanner parity, because it is not measuring the scanner's condition.

**(c) The suppression convention never reached `alembic/versions/`.** `phase17_bandit_stderr.txt`
shows ~60 `# nosec B608` markers already present across `dashboard_aggregation_service.py`,
`metrics_service.py`, `telemetry_engine.py`, `transactional_execution_manager.py`,
`database_scaling.py`, `execution_record.py`, `portfolio.py`, `risk.py`, `test_perf.py` — all
reported as *"nosec encountered (B608), but no failed test on file"*, i.e. reviewed sites. The
three migration files are the ones the convention missed.

### 9.3 Disposition of all 9 findings

| Disposition | Count | Basis |
|---|---|---|
| **Still blocking CI** | **9** | measured exit-1 condition; no suppression; in a blocking job |
| Safety-reviewed by the AST test | 9 | identifiers validator-constrained; values parameter-bound |
| Suppressed | **0** | — |
| Accepted by an authorized owner | **0** | no owner record exists |

**Safety-reviewed is not the same as dispositioned.** The gate is red and no owner has accepted
the risk.

### 9.4 Proposed structural repair (NOT APPLIED — ungranted)

1. Add a whole-tree gate test running **the exact CI command and threshold**
   (`-r backend_app -ll`), asserting exit 0. Mark it slow; the full sweep is ~5m44s.
2. Add a **negative control**: a fixture with a representative unsuppressed B608 that the
   regression test must detect — otherwise the parity test could pass vacuously.
3. **Retain** both existing AST/validator tests; they complement parity, they do not replace it.
4. Only *after* that coverage exists, consider narrowly justified per-site suppressions with
   explicit authorization.
5. **Prohibited and not proposed:** lowering severity, disabling B608, blanket exclusions,
   `|| true`.

**Status: NOT GRANTED.** Writing a test file is a test-file change.

---

## 10. Priority 6 — Trivy IaC gate

**Trivy is not installed locally** (`Get-Command trivy` → false), so the scan was **not
re-run**. Dispositions below rest on Phase 15's recorded output plus **first-hand reading of
current Terraform**, as the brief requires.

Scan configuration (`05-security.yml`): two blocking steps — `scan-ref: 'terraform'` (`:124`)
and `scan-ref: 'aerora_quant_platform/infrastructure'` (`:135`), both
`severity: CRITICAL`, `exit-code: 1`, pinned `aquasecurity/trivy-action@v0.36.0`; plus a
non-blocking CRITICAL,HIGH,MEDIUM report (`:144-153`).

### 10.1 `AWS-0054` — VERIFIED FALSE POSITIVE with an enforced compensating control

`terraform/alb.tf`, `aws_lb_listener.http`:

- `dynamic "default_action"` with `for_each = var.enable_custom_domain ? [1] : []` → **:80 →
  :443 `HTTP_301` redirect** when a custom domain exists.
- A second `dynamic "default_action"` with the inverse condition → plaintext forward,
  documented *"for local and bootstrap use only"*.
- `lifecycle { precondition { condition = var.enable_custom_domain || !contains(["prod",
  "production"], var.environment) } }` — **the apply is refused** for prod/production without a
  custom domain, with an error message naming the exact risk (Authorization headers and broker
  credentials in clear text).

Trivy evaluates neither `dynamic` blocks nor `lifecycle.precondition`. **Exploitability in
prod: none — the apply is refused.** Needs authorized-owner acceptance to clear the gate.

### 10.2 `AWS-0104` — enumerated per security group

`terraform/security_groups.tf` defines **exactly three** security groups, each with **one**
egress rule, all `protocol = "-1"`, `cidr_blocks = ["0.0.0.0/0"]`:

| Resource | Egress lines | Assessment |
|---|---|---|
| `aws_security_group.alb` | `:22-27` | **Needed** — must reach target groups. Conventional. |
| `aws_security_group.ecs_tasks` | `:52-57` | **Needed** — ECR, Secrets Manager, exchange APIs. Conventional. |
| `aws_security_group.redis` | `:73-78` | **Candidate for tightening** — ingress is only `tcp/6379` from `ecs_tasks`; a cache node has no evident outbound need. |

**Correction to the brief:** it refers to *"the two Redis egress rules"*. There is **one**.

**I do not assert the Redis rule is unnecessary.** Whether an ElastiCache node needs
node-initiated egress depends on cluster mode, replication topology and backup destinations,
none of which I verified. It is a **candidate requiring owner confirmation**, not a
safe-by-inspection deletion. Tightening it alone would also **not** clear the gate.

### 10.3 Count discrepancy — OPEN

Phase 15 recorded **4 × `AWS-0104`**; I measure **3** unrestricted egress rules in the
blocking-scanned `terraform/` tree. `aerora_quant_platform/infrastructure/terraform/main.tf` —
the second blocking target — contains **no `0.0.0.0/0` match**.

**Unreconciled, and reported as unreconciled.** Possible explanations not tested: Trivy may
emit a finding per `cidr_blocks` element or per-resource-attribute rather than per block, the
pinned version's rule set may differ, or a fourth rule may be expressed in a form my text
search missed. **Resolving this requires running the pinned Trivy version** — not available
locally and `trivy` installation was not authorized.

### 10.4 Disposition of all five CRITICALs

| Finding | Disposition | Blocking? |
|---|---|---|
| `AWS-0054` ALB not HTTPS | **False positive**, compensating control verified first-hand (§10.1) | **Yes** — until an owner accepts |
| `AWS-0104` — `alb` egress | Real, conventional, needed | **Yes** |
| `AWS-0104` — `ecs_tasks` egress | Real, conventional, needed | **Yes** |
| `AWS-0104` — `redis` egress | Real; **candidate** for tightening, needs owner confirmation | **Yes** |
| `AWS-0104` — 4th instance | **Location not established** (§10.3) | **Yes** |

**0 of 5 cleared. No Terraform modified. No Terraform applied. No suppression added. No
severity lowered. DEPLOYMENT remains BLOCKED.** A partial fix is not presented as a green gate.

---

## 11. Priority 7 — deployment-gate parity

### 11.1 Measured gate topology

`needs:` / `continue-on-error` / `require_security_scan` across all seven workflows:

| Workflow | Gate evidence |
|---|---|
| `01-pr-check.yml` | `needs: static-audit` ×5 (`:126,286,384,568,673`); `continue-on-error: true` ×3 (`:96,116,120`) |
| `02-build.yml` | **none** |
| `03-deploy.yml` | `run: python scripts/require_security_scan.py` (`:95`); `needs: pre-deployment-validation` (`:130`) |
| `04-nightly-audit.yml` | `continue-on-error: true` ×7 — non-blocking by design |
| `05-security.yml` | **none** — no `continue-on-error`, so its own steps are blocking |
| **`06-frontend-deploy.yml`** | **`needs:` count = 0** |
| `07-desktop-build.yml` | `needs: build` (`:327`) |

**`06-frontend-deploy.yml`** triggers on `push: branches: [main, master]` with path filters
plus `workflow_dispatch`, and performs `aws s3 sync dist/ s3://…` at `:289`, `:299`, `:307` and
`aws cloudfront create-invalidation` at `:329`. Its 20 matches for `security|gate|bandit|trivy`
are **incidental** — CSP checks and header probes — **not a gate**.

**Phase 15's asymmetry finding is REVALIDATED: the frontend can publish to S3 and invalidate
CloudFront with no security-gate dependency, while the backend cannot deploy without one.**

### 11.2 `P11-B08` — stays CLOSED

The backend gate is intact: `require_security_scan.py` runs in `pre-deployment-validation`, and
the deploy job declares `needs:` on it. **No regression found**, so `P11-B08` is not reopened.

### 11.3 Proposed minimum change (NOT APPLIED — `D-P15-4`, ungranted)

Add the smallest fail-closed dependency so the frontend cannot publish without security
evidence **for the exact candidate SHA**. Regression coverage must include: missing run, stale
SHA, cancelled, skipped, failed, API error, and success. The concurrent session's prototype
(§2.2) already encodes 42 such cases but lives in `staging/` and **does not run in the default
lane**; landing it in `tests/` requires a grant.

**No frontend asset was published, no CloudFront invalidation issued, no ECS deploy, no CI
dispatched.** A green workflow-structure test is **not** an executed release.

**Backend and frontend release decisions are documented separately**, per the brief. I reused
`D-P15-4` and created no duplicate ID.

### 11.4 CI evidence at this HEAD

`01 PR Check` has **never run** at `ecd23a9e` (Phase 14, confirmed unchanged). **There is no
test, flake8, Redis or PostgreSQL CI evidence for this baseline at all.** Any readiness claim
resting on "CI is green" is unsupported here. Per `D-P15-3`, whether main is validated through
PRs or a main-branch correctness workflow remains an **open policy decision with no grant**.

---

## 12. Priority 1 — W-6 classifier (REUSED)

Inputs verified unchanged this pass: `exchange_executor.py` and the matrix probe are
byte-identical to Phase 13, which measured **21 cases, 20 asserted, 10 misclassified**, and
reproduced Phase 12's 8-case result identically. Per the brief's instruction not to re-run
unchanged diagnostics, **the matrix was not re-executed.**

Standing results: transport failures shadowed by `symbol`/`market` in URLs (**5 of 5** venue
paths); `"key"` shadowing **8 of 8** non-auth words (`monkey`, `monkeypatch`, `keyword`,
`keyspace`, `turkey`, `keychain`, `donkey`, `whiskey`); a genuine **signature** auth failure
classified as generic `ExchangeError`; and ccxt `RateLimitExceeded` missed because the test
requires `"rate limit"` **with a space**. **Redaction verified intact** — must stay first at
`:1236`. `retryable` re-measured as **log-only**: 15 production occurrences, **0 control flow**.

Default-lane regression coverage for the ladder remains **zero** (the sandbox file skips 16/16
without credentials). **Skipped tests are not passes.**

**W-3 must not consume `retryable` until the classifier contract is corrected and separately
authorized.** No retry behaviour was introduced.

**Status: NOT GRANTED.** No fix applied; no test added.

---

## 13. Priority 8 / 9 — D6-C, Redis, worker lifecycle

### 13.1 D6-C — three separate statuses, kept separate

| Level | Status |
|---|---|
| **Query validity** | **ESTABLISHED.** Original fails SQLSTATE `42883` (`uuid = text`); corrected `EXISTS` form plans. |
| **Non-production fixture verification** | **ESTABLISHED.** 7/7 scenarios exact, 0 mismatches, on real `uuid` types — incl. multi-venue tenants, tenants without keys, empty execution table. Returns **counts only**, all integers. |
| **Production evidence** | **NOT SUPPLIED.** |

Query and fixtures unchanged → **REUSED**, not re-run. The request to the **production DB
owner** is for three integers from the counts-only `EXISTS` query; only
`tenants_without_keys = 0` is load-bearing, and it is **necessary but not sufficient**.

The **token-claim divergence count** is a **separate request to the authentication/platform
owner**: among tokens issued in the last 30 days, the count of distinct subjects whose
`tenant_id` claim is present and differs from `sub`. A count, not a list. **No application-table
query can answer this** — the divergence lives in the token. **NOT SUPPLIED.**

No production database was accessed. No local or shared database state was mutated.

### 13.2 Redis / QUOTA-01

`AERORA_TEST_REDIS_URL` **unset** · port 6379 **CLOSED** · `redis-server` absent · Docker
absent (`A23E17:216`: *"cannot provision one even if authorized"*). The 5 gated tests
**SKIP**, with the gate's own words: *"**A skip here is NOT a pass.**"*

`tests/real_redis_gate.py` correctly separates three states — unset → SKIP, configured but
unreachable → **FAIL**, reachable → RUN — so a broken CI Redis cannot read as green.
`A23E13:181` notes CI **does** provision an approved isolated Redis; that is a CI-side
capability and does not make one available here.

**No Redis started. No URL invented. No skip reported as a pass.** Strict XFAIL behaviour
preserved — `A23E17:190` records the P2 tripwire must stay `XFAIL(strict)` until P2 is
authorized; it was not touched.

### 13.3 W-3 / W-4 / W-5 ordering

| Item | Constraint |
|---|---|
| **W-4** | **`UNKNOWN` has no authorized exit policy.** The recovery CAS is pinned `AND upper(status::text) = 'EXECUTING'` (`execution_record.py:1488`), so an `UNKNOWN` row is unreachable by `resolve_stale_execution`. |
| **W-5** | **Must not be implemented before W-4 defines and tests the transition rules.** |
| **W-3** | **Must not wire to `retryable` while W-6 is incorrect** (§12). |
| `venue_order_id` | **External identifier. No foreign key** — migration `030_execution_venue_order_id.sql`'s postflight explicitly aborts if an FK is added. |

No state-machine semantics were inferred and no worker startup ownership changed.

---

## 14. Remaining blockers and smallest unblocking actions

| # | Blocker | Approver | Smallest next action |
|---|---|---|---|
| 1 | **FORCE RLS must stay blocked** | — | **No action.** The only thing that can make this worse is acting. |
| 2 | D6-C production counts | **Production DB owner** | Run one counts-only query, return **3 integers** |
| 3 | D6-D boot evidence | **Platform/deployment owner** | **4 booleans/strings × 2 environments**, redacted |
| 4 | Token-claim divergence | **Auth/platform owner** | **One integer** |
| 5 | D7-1 (`D-13-1`) | **Code owner** | One decision → **3 line deletions** |
| 6 | W-6 (`D-13-2`) | **Code owner** | One decision; must precede W-3 |
| 7 | Bandit parity test | **Code owner** | One decision → 1 test file + negative control |
| 8 | Trivy 4th `AWS-0104` | **Security owner** | Authorize running pinned Trivy, or supply the CI scan artifact |
| 9 | Trivy acceptances | **Security owner** | Accept `AWS-0054` + 2 conventional egress; decide the Redis rule |
| 10 | Frontend gate (`D-P15-4`) | **Workflow/release owner** | One decision → smallest fail-closed `needs:` |
| 11 | Tenant identity | **Code owner** | **Choose one of three expressions.** Costs no code; blocks all wiring |
| 12 | Main-branch validation (`D-P15-3`) | **Release owner** | Decide PR-gated vs main-branch workflow |
| 13 | Isolated Redis (`D-13-10`) | **Coordinator / infra** | One decision + an instance |
| 14 | `UNKNOWN` exit policy | **Execution-state owner** | Define the rule before W-5 |

---

## 15. Confirmation that prohibited operations were not performed

| Prohibited | Performed? |
|---|---|
| Commit, push, stage, reset, stash, clean, revert, forced checkout | **No.** `git write-tree` identical at entry and exit; 0 staged |
| Production deployment / infrastructure provisioning | **No** |
| Production database mutation or credential access | **No** |
| Live-trading activation | **No** — remains OFF / NOT CERTIFIED |
| CI dispatch | **No** |
| Starting shared services | **No** — Redis not started; PostgreSQL not restarted this phase |
| Modifying `staging/pg_dsn.txt` | **No** — stale (51805 vs live 51815) and deliberately left |
| Modifying another session's files | **No** — the 7 `phase17_*`/Phase 19 artifacts read-only |
| Overwriting a report | **No** — destination verified absent first |
| Adding a replacement credential-vault module | **No** |
| Claiming skipped tests passed | **No** — 5 Redis tests reported as skips |
| Claiming a scanner passed without executing it | **No** — Trivy explicitly **not run**; Bandit results reused with command and inputs stated |
| Claiming tenant isolation certified | **No** — **NOT CERTIFIED** |
| Weakening an XFAIL(strict) contract | **No** |
| Lowering a scanner threshold / blanket suppression / `\|\| true` | **No** |

---

*Phase 17 closed. 0 production files · 0 test files · 0 workflows · 0 Terraform · 0 migrations ·
0 commits · 0 staged entries · 0 CI dispatched. Tree hash unchanged.*

***LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED · DEPLOYMENT: BLOCKED ·
PRODUCTION EVIDENCE: NOT SUPPLIED.***
