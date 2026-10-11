# Phase 11B-A23E15 — Post-Commit CI Verification, W1C-02 Scope & Blocker Reduction

**Destination verified ABSENT immediately before writing.** Read-only on every source file, test,
migration, workflow, register and existing report. **0 production files modified · 0 test files
modified · 0 registers or ledgers edited · 0 approvals created or inferred · 0 CI runs dispatched ·
1 artifact written (this file).**

> ## OUTCOME
>
> **The new PostgreSQL lane has produced no evidence, and cannot from the current commit.**
> `01 PR Check` triggers on `pull_request` only; `ecd23a9e` reached `main` by direct push, so
> `provisioning-and-isolation` never ran and **will not run until a PR is opened against `main`**.
>
> **The commit caused a security regression.** `05 Security` passed six consecutive times on
> `074b772a` and **fails on `ecd23a9e`** — 5 CRITICAL Trivy IaC failures in `terraform/` plus a
> Bandit SAST failure. `03 Deploy` then **correctly refused** and the ECS deploy was **skipped**.
>
> **The drift baseline is demonstrably stale: 11 migrations postdate it.** But missing
> `_provenance` is metadata only — it cannot cause a validation failure.
>
> **Live trading: OFF / NOT CERTIFIED. PostgreSQL: UNVERIFIED. TENANT-01 / QUOTA-01: OPEN.**

---

## 1. Baseline — re-measured, nothing reused

| Property | Value |
|---|---|
| Repository root | `C:/aerora_quant_backend_updated_final1` |
| Branch | `main` |
| `HEAD` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| `origin/main` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| Divergence (ahead behind) | **`0 0`** — re-measured, not carried over |
| `HEAD == ecd23a9e…` | **True** — still the relevant commit |
| staged / unstaged / unmerged / untracked / tracked | **0 / 2 / 0 / 20 / 5705** |
| Worktrees / stash | **1 / 2** |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` |

**The 2 unstaged changes, both belonging to concurrent sessions and left untouched:**

```
 M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md
 M tests/test_drift_classification.py
```

### 1.1 Baseline hashes (HEAD blobs)

| Blob | Path |
|---|---|
| `91d55a379115c88edd2fd6be01871b82a75ef498` | `.github/workflows/01-pr-check.yml` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `eb8078eac06268447d60f26ef7a97a537ceed988` | `.github/workflows/03-deploy.yml` |
| `f853c1482ca295440c78e4f25a8989438fab041e` | `scripts/verify_schema_drift.py` |
| `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | `scripts/verify_provisioning.py` |
| `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | `scripts/verify_production_schema.py` — **still untouched** |
| `46eebf6f2e9f3cf3eafeff458542a5ff547be84b` | `scripts/provision_database.py` |
| `fcffbe8e01367aa7f99fffe2677267821fb8424d` | `tests/test_drift_classification.py` — **665 lines** |
| `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | `tests/test_snapshot_dimension_invariants.py` |
| `f1f5c02c1c8866b297118445b3928adcc8315c9c` | `tests/test_schema_drift_checker_is_read_only_postgres.py` |
| `e6069946039186ff2219ce7b881f1c4675aac90c` | `staging/provisioning_snapshot.json` |
| `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` |
| `1402bdc5a1b030430d6e192989a851b4a21ce9a8` | `staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` |

### 1.2 New artifacts since the attached report

`staging/PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` — read in full. It is a
**report, not a decision**: it states *"no authorization arrived"*, 0 approvals created. Its §3
contains a governance finding I adopt and cite in §7.3.

---

## 2. Commit and remote verification

| Field | Value |
|---|---|
| Commit | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| Author | `Narendra2212 <nt3441449@gmail.com>` — repository owner |
| Date | **Sun Oct 11 15:13:47 2026 +0530** |
| Subject | `launch phase 1` |
| Files changed | **3,723** |
| Pushed | **yes** — `origin/main` identical |

**Composition**, measured with `git diff-tree`:

| Area | Files |
|---|---|
| `staging/` | **3,456** |
| `tests/` | 136 |
| `backend_app/` | **76** |
| `scripts/` | 11 |
| `terraform/` | **4** |

### 2.1 Credential hygiene — verified clean

`VER` **No credential file was committed.**

| Path | In HEAD? | Ignored |
|---|---|---|
| `exchange_keys.json` | **no** | `.gitignore:309` |
| `.env` | **no** | `.gitignore:18` |
| `.env.corrupt.backup` | **no** | — |
| `staging/pg_dsn.txt` | **no** | `.gitignore:375` |
| `.env.example`, `.env.staging.example` | yes | intended templates |

A name scan over all 5,705 tracked files returned only legitimate source/test/IaC files
(`secret_validation.py`, `scan_diff_secrets.py`, `terraform/secrets.tf`,
`tests/regression/baseline/credential_vault.json`, four `test_*secret*` files,
`vyomquant-secrets-policy.json`). **No DSN or credential value is reproduced in this report.**

---

## 3. CI verification — runs inspected, and why none was dispatched

### 3.1 The decisive finding: the new lane cannot run from this commit

`VER` Committed `.github/workflows/01-pr-check.yml`, lines 1–6:

```yaml
name: 01 PR Check
on:
  pull_request:
    branches: [main, master]
    types: [opened, synchronize, reopened]
```

**`pull_request` is the only trigger.** `ecd23a9e` arrived on `main` by **direct push**, which does
not match it. Consequence, confirmed by enumeration rather than inference:

| Query | Result |
|---|---|
| `01 PR Check` runs with `headSha = ecd23a9e…` | **NONE** |
| Most recent `01 PR Check` runs | 10-10, on `ci/d5-*` **PR branches** (`f5b99e57`, `d07014d3`, `a624ce89`) |

**So `provisioning-and-isolation` — the drift gate, the 12-file PostgreSQL lane, the privilege
hardening and tenant isolation steps — has not executed once.** Committing the toolchain made the
lane *runnable*; it did not make it *run*. **A runnable lane is not PostgreSQL certification.**

### 3.2 Runs that do exist for `ecd23a9e`

| Workflow | Event | Conclusion |
|---|---|---|
| `02 Build` | push | **success** |
| `06 Frontend Deploy` | push | **success** |
| `07 Desktop Build (native, unsigned)` | push | **success** |
| **`05 Security`** | push | **FAILURE** |
| **`05 Security`** | schedule | **FAILURE** |
| **`03 Deploy`** | workflow_run | **FAILURE** |
| `01 PR Check` | — | **NOT RUN** |

### 3.3 `05 Security` is a regression caused by this commit

`VER` `05 Security` history on `main`:

```
failure   ecd23a9e  schedule
failure   ecd23a9e  push
success   074b772a  schedule   (×5)
success   074b772a  push
```

**Six consecutive passes on the previous commit; both runs fail on this one.** Job-level detail:

| Job | Conclusion | Failing step |
|---|---|---|
| Trivy Container Vulnerability Scanner | success | — |
| **Trivy IaC Misconfiguration & Secret Scan** | **failure** | `Trivy IaC scan — terraform/ (blocking, CRITICAL)` |
| **Bandit SAST & Dependency Security Scan** | **failure** | `Run Bandit Scan` |

**Trivy**, from the run log — `exit-code: 1`, `severity: CRITICAL`, three result groups:

```
Tests: 1 (SUCCESSES: 0, FAILURES: 1)   Failures: 1 (CRITICAL: 1)
Tests: 1 (SUCCESSES: 0, FAILURES: 1)   Failures: 1 (CRITICAL: 1)
Tests: 3 (SUCCESSES: 0, FAILURES: 3)   Failures: 3 (CRITICAL: 3)
```

**5 CRITICAL IaC misconfigurations.** The commit added **4 `terraform/` files**, which is the
proximate cause: the blocking scan had no `terraform/` to fail on before.

**Bandit** runs `bandit -r backend_app -ll -f json -o reports/bandit-report.json` and exited **1**
after completing (`Working... 100%`, `JSON output written to file: reports/bandit-report.json`). It
is a findings failure, not a crash, at medium-and-above severity over the **76 newly committed
`backend_app/` files**. One incidental defect surfaced in the log: a stale suppression —
`nosec encountered (B608), but no failed test on file backend_app/test_perf.py:18`.

### 3.4 `03 Deploy` failed safely — the gate held

| Job | Conclusion | Detail |
|---|---|---|
| Pre-Deployment Validation Gate | **failure** | failing step: **`Require the security scan to have passed for this commit`** |
| ECS Fargate Deploy & Self-Healing Diagnostics | **skipped** | never started |

**No deployment occurred.** The deploy pipeline consumed the `05 Security` failure and refused.
This is the designed behaviour working correctly, and it is the single most reassuring result of the
pass.

### 3.5 No CI run was dispatched, and why

**This session has no authority to dispatch.** The brief states the test explicitly — *"A request to
investigate CI is not automatically authorization to trigger infrastructure provisioning"* — and it
grants none. `provisioning-and-isolation` would issue
`provision_database.py --apply --create-substrate` **twice**, so a dispatch is an
infrastructure-provisioning act, not a read.

**Precise missing permission:** authority to dispatch `01 PR Check` (or open a pull request against
`main`) for commit `ecd23a9e`, together with confirmation that `PROVISION_DATABASE_URL` resolves to
an isolated throwaway service. **Isolation evidence already on file, in the workflow's favour:**
`01-pr-check.yml:593` sets `PROVISION_DATABASE_URL` to a **loopback** address
(`…@localhost:5432/postgres`, a job-scoped service container), and the workflow's own comment
records that `--i-understand-this-is-remote` is **deliberately withheld** so that *"if this URL ever
stops being loopback, this step must fail rather than proceed."* That is a sound fail-closed design,
but **verifying a design is not authorization to run it.** No dispatch was made.

---

## 4. Drift gate — analysed independently

### 4.1 Missing `_provenance` cannot cause a validation failure

`VER` Traced through `scripts/verify_schema_drift.py` (`f853c148…`):

| Line | Behaviour |
|---|---|
| `:81` | `DEFAULT_BASELINE = ROOT / "staging" / "provisioning_snapshot.json"` |
| `:316` | `--baseline` defaults to it |
| `:321-322` | missing file ⇒ prints `no baseline at …` |
| `:334` | `baseline = json.loads(baseline_path.read_text(encoding="utf-8"))` |
| `:212`, `:367` | iterate **`for key in SNAPSHOT_QUERIES`** — never the JSON's own keys |

A search for `provenance` in the file returns **zero matches**. The checker never reads, requires or
validates it.

**Conclusion: `_provenance` is pure metadata. Its absence cannot fail the gate.** This **corrects my
own speculation** in `A28B §9.4`, which floated a stale baseline "plus a hard gate" as a plausible CI
failure without separating the two mechanisms. The staleness risk is **content**, not provenance.

### 4.2 The baseline *is* demonstrably stale — by content, and measurably

`VER` `staging/provisioning_snapshot.json`: **16 top-level keys** (matching the 16
`SNAPSHOT_QUERIES`), **no `_provenance`**, 396,325 B, mtime **10-06 20:14:20**.

**Eleven migrations postdate the capture:**

```
10-06 23:41  022_fix_library_subscriptions_updated_at.sql
10-07 12:28  023_remediate_privilege_escalation.sql
10-07 12:31  024_remediate_referral_double_commission.sql
10-07 22:25  025_work03_bot_ownership_claims.sql
10-09 11:36  026_fee_records_row_level_security.sql
10-09 12:13  027_referral_ledger_currency.sql
10-09 22:11  028_safety_control_audit.sql
10-09 22:46  029_referral_obligations.sql
10-09 23:35  030_execution_venue_order_id.sql
10-10 04:23  031_fee_records_fee_pct_nullable.sql
10-10 05:00  032_referral_payouts_terminal_evidence.sql
```

38 migrations exist; the baseline predates eleven of them, including `023` (privilege revocations)
and `026` (fee-records RLS) — both of which change dimensions the snapshot captures (`privileges`,
`policies`, `row_level_security`).

**Expectation, stated as a prediction to be measured and not as a result:** the lane provisions from
all 38 migrations and compares against this baseline, so the drift step is **likely to fail on
genuine content drift**. I did **not** run it and I report no outcome.

### 4.3 If the drift step does fail — how to tell the two causes apart

| Signature | Diagnosis |
|---|---|
| Drift concentrated in `privileges` / `policies` / `row_level_security` / `columns`, consistent with migrations 022–032 | **stale baseline** — expected, not a defect |
| A **modified** object reported as `1 missing + 1 unexpected` | **classifier defect** — would contradict W1D-13 and the 140 local tests |
| A nonzero verdict printing no subject (`columns: 0 missing, 0 unexpected`) | **classifier defect** — the exact W1C-02 symptom, now covered by tests |

**The snapshot must not be regenerated to make CI green.** The authorized path is **`AP-W1D-4`**
("Add the `_provenance` envelope and recapture the snapshot", owner *DB owner + code owner*) — and
`AP-W1D-4` is **`NOT GRANTED`** at ledger `:803`, which §7.4 confirms is *accurate*. A recapture also
requires a verified source database, and `verify_provisioning.py` issues `DROP DATABASE`/
`CREATE DATABASE`. **No baseline change was made.**

---

## 5. PostgreSQL lane — per file and per concern

**Environment measured, not assumed:** `AERORA_TEST_DATABASE_URL`, `PROVISION_DATABASE_URL`,
`DATABASE_URL`, `AERORA_TEST_REDIS_URL` are **all unset** in this shell. `tests/conftest.py:37`
reads `AERORA_TEST_DATABASE_URL` as the deliberate opt-in. So this is a **local, PostgreSQL-absent**
measurement — **not** the CI lane result, which does not exist.

Combined run: **11 passed, 228 skipped, 1 xfailed, exit 0, 48.34s.** Per file:

| # | Test file | Concern | Local result | CI result |
|---|---|---|---|---|
| 1 | `test_tenant_isolation_rls_postgres` | tenant isolation / RLS | **29 skipped** | **NOT RUN** |
| 2 | `test_cross_tenant_read_sweep_postgres` | cross-tenant reads | **6 skipped** | **NOT RUN** |
| 3 | `test_billing_data_isolation_postgres` | billing-data isolation | **27 skipped** | **NOT RUN** |
| 4 | `test_tenant_referential_integrity_postgres` | referential integrity | **30 skipped** | **NOT RUN** |
| 5 | `test_pgcrypto_preflight_postgres` | `pgcrypto` preflight | **5 skipped** | **NOT RUN** |
| 6 | `test_signup_integration_postgres` | signup | **12 skipped** | **NOT RUN** |
| 7 | `test_position_lifecycle_postgres` | position lifecycle | **6 skipped** | **NOT RUN** |
| 8 | `test_referral_money_lifecycle_postgres` | referral lifecycle | **12 skipped** | **NOT RUN** |
| 9 | `test_orm_models_match_provisioned_schema` | ORM/schema conformance | **4 passed, 1 xfailed** | **NOT RUN** |
| 10 | `test_bug049_identity_types_postgres` | identity types | **76 skipped** | **NOT RUN** |
| 11 | `test_schema_drift_checker_is_read_only_postgres` | drift read-only behaviour | **12 skipped** | **NOT RUN** |
| 12 | `test_production_security_probe_rolls_back_postgres` | security-probe rollback | **7 passed, 13 skipped** | **NOT RUN** |

Skip reasons are explicit and legitimate, e.g. *"`default_transaction_read_only` and SQLSTATE 25006
do not exist in SQLite"* and *"the script measures RLS behaviour as the anon/authenticated roles,
none of which exist in SQLite."*

**No concern in this table has PostgreSQL evidence.** The 11 local passes are the non-gated subsets
of files 9 and 12 (static/ORM assertions), and **228 skips are not passes.** I do not infer any lane
outcome from `02 Build`, `database-tests`, or the existence of the committed tests.

---

## 6. W1C-02 — ten-criterion acceptance matrix

Authoritative criteria (ledger `L837`/`L932`): **implementation + regression evidence.** The ten
named tests are ledger §3.6, scoped by `AP-W1D-3` at `:802`.

Local run at current hashes: **`140 passed in 6.41s`, exit 0.**

| # | Ledger §3.6 criterion | Implemented | Executed locally | Executed in CI | Verdict |
|---|---|---|---|---|---|
| 1 | identical rows ⇒ unchanged | yes | **yes** | no | **COVERED** |
| 2 | changed attribute ⇒ 1 modified, names it | yes | **yes** | no | **COVERED** |
| 3 | removed ⇒ missing, not modified | yes | **yes** | no | **COVERED** |
| 4 | added ⇒ added, not modified | yes | **yes** | no | **COVERED** |
| 5 | whole-tuple dimensions never modified | yes | **yes** | no | **COVERED** |
| 6 | **counts reduce to the legacy output** | **yes — worktree only** | **yes** | **NO — not committed** | **COVERED LOCALLY, NOT IN CI** |
| 7 | every query has a total `ORDER BY` | yes | **yes** | no | **COVERED** |
| 8 | every dimension declares a key width | yes | **yes** | no | **COVERED** |
| 9 | mutated migration checksum ⇒ modified | yes | **yes** | no | **COVERED** |
| 10 | **read-only enforcement unchanged** | yes | **no — 12 skipped** | **NO — lane never ran** | **NOT EXECUTED** |

### 6.1 Criterion 6 is implemented but **uncommitted** — a new finding

`VER` `tests/test_drift_classification.py`:

| Where | Blob | Lines |
|---|---|---|
| **HEAD** | `fcffbe8e01367aa7f99fffe2677267821fb8424d` | **665** |
| **Worktree** | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | **808** |

The +143 lines carrying `_legacy_membership_diff`, `test_counts_reduce_to_the_legacy_output` (14
parametrised cases), `test_the_legacy_arithmetic_cases_cover_every_classification_category` and
`test_the_legacy_reconstruction_is_membership_not_multiset` are **in the working tree only.** The
`launch phase 1` commit captured the **665-line** version.

**Consequence: criterion 6 would not run in CI even once the lane executes.** It is present and
green locally, absent from every ref. This refines `A28B §7.3`, which recorded it as closed without
checking its commit state.

### 6.2 Criterion 10 — status corrected

`tests/test_schema_drift_checker_is_read_only_postgres.py` **is committed** (`f1f5c02c…`, 12 tests)
and CI **does** set `AERORA_TEST_DATABASE_URL` (`01-pr-check.yml:592`), so the module gate would be
satisfied. **But the job that would run it has never run** (§3.1). So criterion 10 is **obtainable
and still unobtained** — not skipped-by-design, simply never executed.

**W1C-02 is NOT closed, and I record no closure.** Nine criteria have executed local evidence, one
of those nine is uncommitted, and the tenth has none. Closure additionally requires the scope
decision (§7) and D5 sign-off, which are governance acts.

---

## 7. W1C-02 scope decision and D5 authorization state

### 7.1 The three call paths, traced at source

| Path | Location | Diff mechanism | State |
|---|---|---|---|
| **`compare()`** | `verify_schema_drift.py` | classifier-routed — `:69-74` imports `COMPARISON_KEY`/`classify_dimension`; `:202 classify_all`; `:213 classify_dimension(...)`; consumed `:303`, `:357` | **FIXED** |
| **`diff_snapshots()`** | `verify_provisioning.py:507` | **`only_a = [r for r in a if r not in b]`** at `:513` — whole-tuple membership | **DEFECTIVE** |
| **`check_drift()`** | `verify_production_schema.py:217` | imports only `SNAPSHOT_QUERIES, _norm` at `:100` — **no classifier**; prints `MISSING from production` at `:247` | **DEFECTIVE** |

**Caution recorded for future greps:** `scripts/apply_migrations.py:81,163,262` and
`scripts/migration_preflight.py:432,545` define and call an **unrelated `classify_all`** over
migration file status. It is a name collision, **not** a drift-classifier consumer. A naive search
for `classify_all` overcounts the classifier's consumers by two files.

### 7.2 No authoritative record resolves the scope

Evidence is genuinely two-sided and no record adjudicates it:

- **Narrow** — W1C-02's origin names `verify_schema_drift.py`; ledger §3.6 states the change
  *"touches only `compare()`"*; `AP-W1D-3` is scoped to the classification plus the 10 tests.
- **Wide** — `L837`/`L932` scope W1C-02 to *"12 of 16 dimensions"*, and both siblings carry the same
  defect across those dimensions.

**The implementation existing in a commit does not resolve it**, and I did not treat the commit, the
140 passing tests, or `A23E14`'s existence as a decision.

### 7.3 Why an in-session grant worked for `AP-W1D-3` and cannot be assumed here

`A23E14 §3` establishes a distinction I verified against the ledger and adopt: W1D-12's grant was
valid because the **`Code owner` role pre-existed in ledger `:802`'s Owner column** *before* any
grant, so the user acted **in a role already on record**. The role was not created by the grant.

**This is the test any further grant must pass.** For the W1C-02 scope decision the same mechanism
**is** available — `AP-W1D-3`'s row already names `Code owner`. For a **ledger edit** it is not:
no Owner column anywhere names a ledger maintainer (§7.4).

### 7.4 D5 and the ledger row

| Item | State |
|---|---|
| **D5** | **CONDITIONALLY GRANTED, condition NOT satisfied** — production-audit stakeholder sign-off **ABSENT** |
| Gating role | appears in **no** authoritative register (ledger scan: `stakeholder`/`production audit`/`sign-off` = **0** occurrences) |
| `check_drift()` / severity routing / `diff_snapshots()` | **NOT MODIFIED.** `verify_production_schema.py` committed byte-identical at `6e194f43…` |
| **Ledger `:802`** | **`NOT GRANTED` — unchanged**, now committed at `7e238620…` |

`VER` Rows 800–804 all still read `NOT GRANTED`, and I re-verified the two that are physically
checkable are **accurate**, not stale: `:803` (`AP-W1D-4`) — snapshot has no `_provenance` and an
unchanged 10-06 mtime; `:804` (`AP-W1D-5`) — `pgserver` absent, `staging/pgdata_w1c_iso` absent.
**`:802` remains the only demonstrably stale row.**

**No ledger edit was made.** A commit records content; it does not resolve the governance decision
that content represents.

### 7.5 Copy-ready decision request

> **One reply, acting in the `Code owner` role named at
> `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`:**
>
> 1. **W1C-02 scope** — *"W1C-02's authoritative subject is ⟨`compare()` only⟩ / ⟨all three call
>    paths⟩."* **Free, needs no database, and determines whether D5 gates W1C-02 at all.**
> 2. **Criterion 6** — *"I authorize committing the +143-line legacy-invariant addition already
>    present in `tests/test_drift_classification.py` (worktree `99a2917a…`)."* Without this,
>    criterion 6 can never run in CI.
> 3. **Ledger `:802`** — *"W1D-12 is authoritative for `AP-W1D-3`; ⟨role/person⟩ maintains the
>    ledger; update **line 802 only** to `GRANTED (W1D-12)`, leaving 800/801/803/804 untouched."*
> 4. **D5's condition** — ⟨identify a holder⟩ / ⟨assume it as Code owner⟩ / ⟨**withdraw** it, since
>    D5's substance is byte-identical preservation⟩ / ⟨defer⟩.

---

## 8. A23 Redis lifecycle — gate matrix, no work performed

All four protected files verified **CLEAN** against HEAD this pass:

| Path | Blob | State |
|---|---|---|
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13…` | **clean, unchanged** |
| `backend_app/core/global_safety.py` | `4b2a1fc52748f3e9…` | clean |
| `tests/test_redis_debounce.py` | `5a8db7a8afbd57a0…` | clean, untouched |
| `staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` | `7b206850c7251966…` | committed, **still blank** |
| `tests/test_a23_redis_pool_lifecycle.py` | — | **ABSENT** (`Test-Path` → False) |

**Gate matrix** — `A23E14 §5` re-measured the A23E6 response form after the commit by codepoint
count: **12 × U+2610 empty, 0 marked of any form, 6 blank `____` fields.**

| Gate | Status |
|---|---|
| `E-1` owner + scope | **UNRESOLVED — no grantor on record** |
| `D-A23C-1` implementation | **UNRESOLVED** |
| `D-A23C-7` test file | **UNRESOLVED**, path ABSENT |
| `D-A23C-3` proxy Option A/B | **UNRESOLVED** |
| `D-A23C-8a` core-cache ack | **UNRESOLVED** |
| `D-A23C-9` ack + attribution | **UNRESOLVED** |
| `E-5 / D-A23C-12` concurrency | **UNRESOLVED** |
| `E-6` real-Redis lane | **UNEXECUTED, no disposition** |

**Granted 0 · Refused 0 · Unresolved 8.** Per §7.3, A23 has **no** Owner-column row to act in, so
W1D-12's procedure cannot be reused without manufacturing the authority it should rest on.

**Authorized work completed: none, correctly.** No Redis source, test or register was touched. **No
classifier grant authorizes any Redis lifecycle edit**, and the commit — which captured
`redis_manager.py` *unchanged* — authorizes nothing.

---

## 9. Parallel workstreams

Target hashes verified **CLEAN vs HEAD** before reusing `A23E14`/`D10`/`D11` source findings, per the
brief's reuse rule. `apply` columns say what I did, not what could be done.

### 9.1 D7-1 — vault signature · defect real, **authorization ABSENT**, no patch

`backend_app/backend/api_key_vault.py` CLEAN `74a00594…`; `routers/orders.py` CLEAN `ff2d34bf…`;
`routers/market.py` CLEAN `16f0377c…`.

`load_decrypted_keys(self, user_id, exchange_id)` takes two positionals. **Three** sites pass
`access_token=`: `orders.py:789-793`, `orders.py:1095-1099`, `market.py:50-54`. A fourth,
`exchange.py:993-996`, is **correct** — so the count is firmly **three**. Masked by
`tests/test_market_none_keys_fix.py:13`, whose double declares `access_token=None`.

**Authorization ABSENT** (D8 Track C, D10 §5, D11 §4, A23E14 §7.A). **No file touched.** Blocking
check for any future patch is **flake8**; `black`/`isort` are `continue-on-error`.

### 9.2 W-6 — error classification · both risks real, **authorization ABSENT**, no tests added

`backend_app/backend/exchange_executor.py` CLEAN `df4fd42a…`. The `_handle_ccxt_error` ladder
(`:1224-1256`) places `symbol`/`market` **above** `network`/`timeout`/`connection`, so a timeout
naming a symbol returns `InvalidSymbolError` with `retryable=False`. `A23E14 §7.B` adds that
`"market"` also matches market-data URL paths literally, widening the trigger; and `"key"` matches
bare substrings. **Registered P0, owner execution-state + coordinator, open. Zero default-lane
coverage. NOT GRANTED → no regression tests added, no reordering performed.**

### 9.3 QUOTA-01 — a genuine status change, plus first-hand test evidence

`VER` **`backend_app/core/subscription_engine.py` is now committed** — CLEAN, HEAD == worktree ==
`256f03994b395d38…`. That blob is the one prior reports recorded as the *uncommitted* fix. **A26
§8's blocker — "the fix is uncommitted; no CI run has ever executed the fixed quota
implementation" — is half resolved: the fix is now in pushed history.**

Ran this pass:

```
pytest tests/test_quota01_enforcement_is_wired.py tests/test_quota01_exchange_slot_is_atomic.py
       tests/test_quota01_hard_enforcer_is_deprecated_in_code.py
       tests/test_quota01_metered_reservation_is_hard.py tests/test_a20_quota_refusal_semantics.py
→ 60 passed, 5 skipped, 8 warnings in 28.17s   (exit 0)
```

**Still OPEN.** `AERORA_TEST_REDIS_URL` is **unset** and no authorized isolated Redis service
exists, so the **real-Redis lane remains UNEXECUTED** and the **5 skips are not passes**. No service
URL was fabricated, nothing started, no CI dispatched. The only slot-guard call site is in
`routers/exchange.py`, another workstream's file — untouched.

### 9.4 TENANT-01 / D6-C — `FORCE RLS` absent as executable SQL

`VER` **Verified by me this pass:** `FORCE ROW LEVEL SECURITY` across
`backend_app/migrations/*.sql` and `backend_app/alembic/versions/*.py`, excluding comment lines →
**0 matches.** It exists only as prose in reports and as a comment at
`026_fee_records_row_level_security.sql:75`. `PHASE11A_READINESS_SCORECARD.json` carries
`tenant_force_rls: OPEN`.

**Separation the brief requires, kept strictly:**

| Layer | Evidence |
|---|---|
| Schema-level control | **static source fact** — RLS enabled, never forced |
| Isolated integration-test evidence | **NONE** — files 1–4 of the lane are 92 skips locally and have never run in CI (§5) |
| Production evidence | **NONE** — no connection made, none sought; D6-C's query remains **NOT GRANTED** |

**Tenant isolation is NOT certified.** No production database was accessed and no credential or DSN
was read or printed.

### 9.5 Workers, watchdogs and cache readiness

`backend_app/main.py` CLEAN `a3849d4a…`; `portfolio_cache_updater.py` CLEAN `a74d7644…`.

| Item | State |
|---|---|
| Reconciliation worker | **NOT STARTED** — `A23E14 §7.E` finds **no reference** to `ExchangeReconciliationService` in `main.py`; corroborated by the scorecard's `exchange_reconciliation_service_WIRED_AND_RUNNING: NOT_STARTED` |
| Marketplace expiry worker | **NOT STARTED** — no reference in `main.py` |
| **D7-2 portfolio cache** | **silently dead — verified by me:** `portfolio_cache_updater.py:151` imports `backend_app.backend.key_manager`, and `Test-Path backend_app/backend/key_manager.py` → **False**. The `ImportError` is swallowed per user, while `/health/services` still reports `ACTIVE` — a **false-green health signal** |
| W-3 / W-4-W-5 | **NOT GRANTED** (D8 §445-449). `order_id` carries an FK; `venue_order_id` deliberately does not; required behaviour is *coalesce, never swap*. No `UNKNOWN` exit rule exists, so W-5 must not precede W-4 |

**Nothing wired, nothing started, no edits.** `D-A25-C1` is preserved as a decision request: the
scorecard has **no acceptance-criteria field** for `recon_operational_for_paper` (11 top-level keys,
none of them criteria), so the gate's meaning is unanswerable from the artifact.

---

## 10. Commands actually run

All read-only except the four test invocations, which write no repository file (`-p no:cacheprovider`
throughout). Outcomes as measured, including the two that corrected me.

| Command | Real outcome |
|---|---|
| `git rev-parse --show-toplevel / HEAD / origin/main / --abbrev-ref` | root, `ecd23a9e…`, `ecd23a9e…`, `main` |
| `git rev-list --left-right --count HEAD...origin/main` | **`0 0`** (re-measured) |
| `git status --porcelain`, `git ls-files`, `git worktree list`, `git stash list`, `git reflog -1` | 0 / 2 / 0 / 20 / 5705; 1; 2; `ecd23a9e commit: launch phase 1` |
| `git log -1 --format=…`, `git diff-tree --no-commit-id --name-only -r HEAD` | owner commit; **3,723** files; composition in §2 |
| `git ls-tree HEAD -- <credential paths>`, `git check-ignore -v` | **no credential file in HEAD**; 3 ignore rules confirmed |
| `git rev-parse HEAD:<path>` + `git hash-object <path>` ×16 | §1.1 and §9 hashes; `test_drift_classification.py` **DIFFERS**, all others CLEAN |
| `git show HEAD:.github/workflows/01-pr-check.yml` | trigger = **`pull_request` only** |
| `gh run list --limit 12 --json …` | **no `01 PR Check` for `ecd23a9e`**; 6 other runs (§3.2) |
| `gh run list --workflow=05-security.yml --branch main --limit 8` | **2 failures on `ecd23a9e`, 6 successes on `074b772a`** |
| `gh run view 38129699454 --json jobs` / `--log-failed` | Trivy IaC + Bandit failing steps; **5 CRITICAL**; `bandit … exit 1` |
| `gh run view 38130273012 --json jobs` | `Require the security scan to have passed` **failed**; ECS deploy **skipped** |
| `Select-String` over `verify_schema_drift.py` for baseline/provenance | `:81`, `:334`, `:212`, `:367`; **0 provenance matches** |
| `ConvertFrom-Json` on the snapshot; migration mtimes vs 10-06 20:14 | 16 keys, no `_provenance`; **11 migrations postdate it**; 38 total |
| `Get-Item env:…` ×4 | `AERORA_TEST_DATABASE_URL`, `PROVISION_DATABASE_URL`, `DATABASE_URL`, `AERORA_TEST_REDIS_URL` **all unset** |
| `pytest` drift + invariants | **140 passed in 6.41s**, exit 0 |
| `pytest` 12-file lane, combined | **11 passed, 228 skipped, 1 xfailed in 48.34s**, exit 0 |
| `pytest` 12-file lane, per file | §5 table |
| `pytest` 5 quota files | **60 passed, 5 skipped in 28.17s**, exit 0 |
| `Select-String` FORCE RLS over migrations + alembic | **0 matches outside comments** |
| `Test-Path` ×2 | `test_a23_redis_pool_lifecycle.py` **False**; `key_manager.py` **False** |

**Environmental limitations:** no PostgreSQL, no Redis, no venue credentials, no production access,
no CI dispatch authority. Two PowerShell quirks hit and worked around: `-split '\s+'` inside a
`git` argument list was parsed as a path (`fatal: '\s+' is outside repository`) — replaced with
`git rev-parse HEAD:<path>`; and an earlier `gh run list` formatting attempt printed
`System.Object[]` before being rewritten with explicit field access.

---

## 11. Remaining blockers, ranked by risk

| # | Blocker | Risk | Minimum next action |
|---|---|---|---|
| 1 | **`05 Security` fails on `main`; `03 Deploy` blocked** | **Highest, and new.** 5 CRITICAL IaC findings in `terraform/` + Bandit findings. `main` is currently undeployable | Triage the 5 Trivy CRITICALs and the Bandit report. **The blocked deploy is correct behaviour — do not bypass the gate** |
| 2 | **TENANT-01** — RLS enabled, never forced; app connects as table owner; `tenant_db_context.py` unwired | **Highest standing.** Money-table isolation rests on application `WHERE` clauses | Wire the GUC per request *or* connect as a non-owner role, then a FORCE-RLS migration behind a flag. Tenancy owner; high blast radius |
| 3 | **The PostgreSQL lane has never run** | High. Drift, tenant isolation, billing isolation, integrity, `pgcrypto`, rollback all have **zero** evidence | Open a PR against `main` (or authorize a dispatch) for `ecd23a9e`. Expect a **stale-baseline** drift failure (§4.2) and triage it with §4.3 |
| 4 | **D7-2** — portfolio cache silently dead behind a false-green `/health/services` | High. A false health signal is worse than a visible outage | Portfolio-cache owner picks among D8 §4.3's three options |
| 5 | **D7-1** — 3 `access_token=` sites raise `TypeError` on live paths | High, narrow, fully specified: 3 deletions + 1 `Signature.bind` test, flake8-neutral | Router owner grants Packet 6 |
| 6 | **W-6** — timeout relabelled a determinate rejection; broad `key` match | P0 registered, zero default-lane coverage | Execution-state owner + coordinator authorize the reorder plus 3 tests |
| 7 | **Criterion 6 uncommitted** | Medium. The legacy-invariant test can never run in CI as things stand | Authorize committing the +143 lines (§7.5 item 2) |
| 8 | **W1C-02 scope + ledger `:802` + D5** | Medium, but **free to answer** | §7.5 — one reply resolves all four |
| 9 | **Workers not started**; **A23 Redis P1**; **E-6 real Redis**; **QUOTA-01 real-Redis** | Medium | Respective owners; A23 needs the §7.3 role row first |

---

## 12. Preservation audit

| Check | Entry | Exit |
|---|---|---|
| Branch / `HEAD` / `origin/main` / divergence | `main` / `ecd23a9e…` / `ecd23a9e…` / `0 0` | **identical** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **identical** |
| untracked (collapsed) | 20 | **45 — see note** |
| Worktrees / stash | 1 / 2 | **identical** |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical — no commit, reset or checkout** |
| **Ledger** `…W1D_LEDGER…md` | `7e238620…` | **identical; `:802` still `NOT GRANTED`** |
| `…W1D12_CLASSIFIER_DECISION_RECORD.md` | `1402bdc5…` | **identical** |
| `scripts/verify_production_schema.py` | `6e194f43…` | **identical — still untouched** |
| `staging/provisioning_snapshot.json` | `e6069946…` | **identical — baseline NOT regenerated** |
| `backend_app/backend/redis_manager.py` | `d7b0e811…` | **identical, clean** |
| `tests/test_drift_classification.py` (worktree) | `99a2917a…` | **identical — concurrent edit preserved, not reverted** |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | ` M` | **left exactly as found** |
| A23E6 response form | 12 ☐ / 0 marked / 6 blank | **unchanged** |

**Files created by this pass: 1** — this report. New, untracked, under `staging/`, destination
verified ABSENT immediately before writing.

**Untracked count note.** 20 → 45 is **+25, of which exactly 1 is mine.** The other 24 are
concurrent sessions' artifacts. The count is also now *granular* where it previously was not:
before `ecd23a9e`, `staging/` held **0 tracked files**, so `git status --porcelain` collapsed the
whole directory into a single `?? staging/` entry. The commit tracked `staging/`, so every new
report there now appears as its own entry. **This is a counting-mode change plus concurrent
activity, not drift introduced here** — `tracked` is unchanged at 5,705 and `staged`/`unmerged`
remain 0.

**Confirmed NOT done:** production, test, migration, workflow, register, ledger or scorecard edit;
baseline regeneration; report overwrite; dependency change; stage, commit, push, reset, `--hard`,
clean, stash, checkout, branch switch, index mutation; CI trigger, dispatch or re-run; deployment;
Redis or PostgreSQL start; database, exchange or payment call; DSN or credential read; approval
created, recorded or inferred; owner contacted.

---

## 13. Final disposition

| Item | State |
|---|---|
| **PostgreSQL integration** | **UNVERIFIED** — lane runnable, **never run**; 228 of 240 lane tests skip locally |
| **Drift baseline** | **DEMONSTRABLY STALE** — 11 migrations postdate it. Missing `_provenance` is metadata only |
| **W1C-02** | **OPEN** — 9/10 criteria have local evidence, 1 of those uncommitted, criterion 10 unexecuted; scope undecided |
| **D5** | **CONDITIONALLY GRANTED, sign-off ABSENT.** Second change NOT STARTED |
| **Ledger `:802`** | **`NOT GRANTED`, unchanged** — the only demonstrably stale row; `:803`/`:804` verified accurate |
| **A23 Redis P1** | **NOT IMPLEMENTED** — 0 granted, 8 unresolved |
| **D7-1 / W-6 / W-3 / W-4-W-5 / D7-2** | **verified defects, all NOT GRANTED, none modified** |
| **QUOTA-01** | **OPEN** — fix now committed; 60 passed / 5 skipped; real-Redis **UNEXECUTED** |
| **TENANT-01 / D6-C** | **OPEN — NOT CERTIFIED.** FORCE RLS absent as executable SQL; no isolated or production evidence |
| **`main` deployability** | **BLOCKED** — `05 Security` failing, `03 Deploy` gate correctly refusing |
| **Live trading** | **OFF / NOT CERTIFIED** |

A committed toolchain is not a successful CI run. A runnable PostgreSQL lane is not PostgreSQL
certification. 140 passing unit tests are not governance sign-off. A commit is not a ledger
correction, and a ledger correction would not be implementation authorization.

No approval was inferred from this brief's framing, from the `ecd23a9e` commit, from any passing
test, from the clean state of a target file, from a role label, from commit authorship, from a
concurrent session's report, or from silence.
