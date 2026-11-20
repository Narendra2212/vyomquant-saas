# Phase 16 — Security Gate Recovery, Release Pipeline Hardening, PostgreSQL Evidence, Critical Defect Closure

**Destination verified ABSENT immediately before writing.** Read-only on every production file, test,
migration, workflow, Terraform file, register and existing report. **0 production files · 0 test
files · 0 workflows · 0 Terraform · 0 registers or ledgers changed. 0 CI runs dispatched. No commit,
push, merge, revert, reset, stash, clean or stage. No database, Redis, exchange, AWS, DSN, credential
or production access.**

**LIVE TRADING: OFF / NOT CERTIFIED.**

---

## 0. Headline — a fail-open release path that has already published unvalidated code

> **`06 Frontend Deploy` published `ecd23a9e` to production S3 + CloudFront with no dependency on
> any correctness or security check.** It ran on `push` to `main`, and at the same minute
> `05 Security` **failed** on that same commit while `01 PR Check` had **never run**.
>
> Verified step-by-step from run `38129699497`: `Sync build artifacts to S3` **success** ·
> `Invalidate CloudFront cache` **success** · `Verify deployment` **success**.
>
> The backend path refused the same commit. **`03 Deploy` blocked at "Require the security scan to
> have passed for this commit" and the ECS deploy was SKIPPED.** So backend and frontend are now at
> **unequal validation levels for the same SHA**, and the frontend is the live one.

This is a new finding. Prior phases recorded `06 Frontend Deploy: success` in their run matrices
without examining whether it was gated. It is not.

---

## 1. Baseline and preservation audit

| Property | Entry | Exit |
|---|---|---|
| Branch | `main` | `main` |
| **`HEAD`** | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** | **unchanged** |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| `origin/main` | `ecd23a9e…` | unchanged |
| Divergence (ahead behind) | **`0 0`** — re-measured | `0 0` |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Untracked | 56 | 57 (+1 = this report) |
| Worktrees / stash | 1 / 2 | 1 / 2 |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **unchanged — no commit, reset or checkout** |

**`ecd23a9e` is still HEAD.** I did not assume this; it was re-verified, and no newer commit or run
has appeared.

**Concurrent work preserved, untouched:**

```
 M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md
 M tests/test_drift_classification.py        (worktree 99a2917a…, 808 lines — another session's)
```

**Reports read this phase:** `PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` (19:59, the
newest), `PHASE11B_A23E15_POST_COMMIT_CI_AND_BLOCKER_REDUCTION.md`,
`PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md`. Also present and noted:
`PHASE12_PARALLEL_BLOCKER_RESOLUTION.md`, `PHASE11B_A23E16/A23E17`, and a second, differently-named
`A23E15` from a concurrent session. **A correction to my own method:** my first scan used
`-Filter 'PHASE1[2-6]*'`, and PowerShell's `-Filter` does not support regex classes, so it wrongly
reported no Phase 12–16 reports. Re-run with a proper enumeration, Phase 12 and Phase 14 both exist.

**Authorization status for this phase: no grant exists for any source, test, workflow, Terraform,
migration or governance change.** The brief gates every implementation workstream on an explicit
grant ("If authorized…", "If no grant exists for a file, do not edit it") and supplies none.
Accordingly **nothing was implemented**, and every remediation below is a patch plan. Read-only
analysis, local test execution and CI log inspection were performed in full.

---

## 2. Workstream A — the five Trivy IaC CRITICAL findings

Scanner configuration, `.github/workflows/05-security.yml:120-128`: `trivy-action@v0.36.0`,
`scan-ref: terraform`, `scanners: misconfig,secret`, `severity: CRITICAL`, `exit-code: 1`.
**5 CRITICAL, two distinct rules, zero secret findings.** Per-file counts from the run log: `alb.tf`
1, `security_groups.tf` 3, `redis-only/main.tf` 1; `cloudfront.tf`, `cloudtrail.tf`, `ecs.tf`,
`secrets.tf`, `vpc.tf` all 0.

| # | Rule | Resource | Site | Disposition |
|---|---|---|---|---|
| 1 | **`AWS-0054`** — ALB listener does not use HTTPS | `aws_lb_listener.http` | `terraform/alb.tf:52` | **False positive, compensating control enforced** |
| 2 | **`AWS-0104`** — unrestricted egress | `aws_security_group.alb` | `security_groups.tf:26` | Real, conventional — **accept + document** |
| 3 | `AWS-0104` | `aws_security_group.ecs_tasks` | `security_groups.tf:56` | Real, conventional, **load-bearing** — accept + document |
| 4 | `AWS-0104` | `aws_security_group.redis` | `security_groups.tf:77` | **Real and unnecessary — safe to tighten** |
| 5 | `AWS-0104` | `aws_security_group.redis` (standalone) | `redis-only/main.tf:62` | **Real and unnecessary — safe to tighten** |

### 2.1 `AWS-0054` verified at source

`VER` Read directly this phase, not inferred from the filename:

```
alb.tf:52   protocol          = "HTTP"
alb.tf:55   for_each = var.enable_custom_domain ? [1] : []      # redirect branch
alb.tf:61   status_code = "HTTP_301"
alb.tf:68   # bootstrap use only — the precondition below keeps it out of production.
alb.tf:70   for_each = var.enable_custom_domain ? [] : [1]      # forward branch
alb.tf:77   # SC-7: `enable_custom_domain` defaults to false (variables.tf) …
```

Two mutually exclusive `dynamic "default_action"` blocks, plus a `lifecycle.precondition` that
**refuses the apply** when `environment` is `prod`/`production` without `enable_custom_domain`. With
a domain, :80 is a 301 to HTTPS. Trivy evaluates the static `protocol = "HTTP"` attribute and cannot
resolve a `dynamic` block or a precondition.

**Exploitability in production: none — the apply is refused.** The compensating control is stronger
than the rule it trips.

### 2.2 `AWS-0104` verified at source

`VER` `cidr_blocks = ["0.0.0.0/0"]` occurs at `security_groups.tf:11, 19, 26, 56, 77` and
`redis-only/main.tf:62`. **Lines 11 and 19 are ALB *ingress*** (80/443 from the internet), which is
correct for a public load balancer and is **not** among the findings. The four findings are the
egress rules at `:26`, `:56`, `:77` and `main.tf:62`, all `protocol = "-1"`, ports 0–0.

**The two Redis rules are the genuine win.** ElastiCache initiates no outbound connections and
ingress is already correctly restricted to `6379` from `ecs_tasks` only, so `0.0.0.0/0` egress
serves nothing. Removing it clears **2 of 5 CRITICALs at near-zero risk.**

`ecs_tasks` egress is load-bearing — tasks must reach ECR, Secrets Manager, Supabase and the
venues. Restricting it properly needs VPC endpoints plus a venue allowlist: a real infrastructure
project, not a config tweak. The residual risk of all four is **post-compromise exfiltration
latitude**, not inbound exposure.

### 2.3 Introduced-by-commit analysis

`ecd23a9e` committed **4 `terraform/` files**. Before it, the blocking Trivy IaC scan had no
`terraform/` tree to evaluate — which is why `05 Security` passed six consecutive times on
`074b772a` (§5.2). **The misconfigurations are not new code; their *visibility to the gate* is new.**

### 2.4 Disposition

**No Terraform file was edited.** Local validation was impossible — Trivy is not installed and
`terraform plan` needs AWS credentials. **I did not** disable a CRITICAL, add a `.trivyignore`, lower
the severity threshold, or make the ALB redirect unconditional (which would break non-domain
deploys by pointing :80 at a :443 listener that does not exist).

**Patch plan, requiring the infrastructure owner:**

| Item | Change | Risk |
|---|---|---|
| P-A1 | Delete the egress block at `security_groups.tf:73-78`, or narrow it to the VPC CIDR | near-zero |
| P-A2 | Same for `redis-only/main.tf:58-63` | near-zero |
| P-A3 | Per-resource `trivy:ignore:AWS-0054` on `aws_lb_listener.http` citing the `lifecycle.precondition` and control SC-7 | documentation only |
| P-A4 | Per-resource ignores for `:26` and `:56` citing the VPC-endpoint prerequisite, tracked as a follow-up | documentation only |

P-A1/P-A2 clear 2 of 5. P-A3/P-A4 are the honest route for the remaining 3 — a scoped, justified,
per-resource annotation, **not** a blanket suppression.

---

## 3. Workstream B — the Bandit failure

CI command, `05-security.yml:35-36`:

```
bandit -r backend_app -ll -f json -o reports/bandit-report.json
```

`-ll` reports MEDIUM and above; no `|| true`, no `--baseline`, no exclusions, so a single MEDIUM
fails the step. **Phase 14 reproduced this locally and exactly** — `bandit 1.9.4`, exit code **1**,
**9 results, 9 MEDIUM, 0 HIGH**, confidence 8 LOW / 1 MEDIUM, `nosec` applied to 0 of these sites.
I did not re-run it; I verified its claims at source instead.

**All 9 findings are `B608` — possible SQL injection through string-based query construction:**

| File (`backend_app/alembic/versions/`) | Lines |
|---|---|
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |

### 3.1 Verified at source this phase

`VER` The validator, `d4e5f6a7b8c9…:155-157`:

```python
if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
    return s
raise ValueError(f"not a SQL identifier: {s!r}")
```

**A strict allowlist that raises rather than sanitises.** No quote, semicolon, whitespace,
parenthesis or comment marker can pass. Usage density confirms it is the governing pattern, not an
occasional guard: `_validate_table_name` is referenced **21 / 5 / 16** times in the three files
respectively. The only user-influenced value at these sites (`UUID_REGEX`) is a **bound parameter**.

`VER` **`nosec` occurrences in all three files: 0** — the sites are genuinely unsuppressed, unlike
the four earlier `transactional_execution_manager.py` sites which did receive `# nosec B608` with
reasons.

SQL identifiers cannot be bound parameters, so validated interpolation is the correct construction —
precisely the shape B608 cannot distinguish from concatenated user input. These are operator-driven
Alembic migrations over a module-level literal target list, not request input.

**Assessment: 9 false positives on safety grounds, but legitimately red. Zero real vulnerabilities.**

### 3.2 Does the full-tree CI gate now pass? **No.**

It has not been re-run and nothing has changed to make it pass. The 9 findings are present at HEAD,
unsuppressed. **`05 Security` remains failing.**

### 3.3 The structural root cause, and why it matters more than the nine lines

Phase 14 §3.3 established this and I adopt it: `tests/test_bandit_sast_gate_is_green.py` holds the
premise two ways, and neither can see these findings.

| Test | Scope | Proves | Blind to |
|---|---|---|---|
| `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` | **one file** — `transactional_execution_manager.py` | Bandit is silent **on that module** | every other file CI scans |
| `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | whole tree, AST | every interpolation is **constrained** | `# nosec` and Bandit's own verdict |

The 9 alembic sites **pass** the AST test (its `SQL_SAFE_VALIDATORS` already contains
`_validate_table_name`) and are **invisible** to the one-file Bandit test. **So the suite is green
while the gate is red, and the suite structurally cannot detect it.**

### 3.4 Patch plan — no file edited, grant absent

| Item | Change | Why |
|---|---|---|
| P-B1 | `# nosec B608` with a per-site reason on the 9 lines, matching the `transactional_execution_manager.py` precedent | Makes the gate green honestly; the safety premise is proven by §3.1 and the AST test |
| **P-B2** | Widen the Bandit gate test to run CI's **actual** command (`-r backend_app`), or add a second test that does, marked slow (~5m44s per its own docstring) | **The one that matters.** Without it the next unsuppressed MEDIUM anywhere outside that one module goes unnoticed again |
| P-B3 | Remove the stale `# nosec B104` at `backend_app/backend/ws_server.py:72` — CI logs *"nosec encountered (B104), but no failed test"*. Informational, not a failure | hygiene |

P-B1 without P-B2 is suppression without detection — exactly what the gate test's own docstring
warns against. I am **not** proposing `-lll`, `|| true` or `--baseline`, all of which that docstring
rules out. **Authorization needed:** 3 migration files + `tests/test_bandit_sast_gate_is_green.py`
(+ `ws_server.py` for P-B3).

---

## 4. Workstream C — release dependency graph and the fail-open path

### 4.1 Measured dependency graph

```
                         commit ──► push to main
                                      │
        ┌─────────────────────────────┼───────────────────────────────┐
        │                             │                               │
   01 PR Check                   02 Build                     06 Frontend Deploy
   trigger: pull_request ONLY    trigger: push                 trigger: push (paths:
   workflow_dispatch: 0          │                               algo22-terminal/**)
   push: 0                       │                             workflow_dispatch: yes
        │                        ▼                                     │
   ►► NEVER RAN AT              03 Deploy                              │
      ecd23a9e ◄◄          trigger: workflow_run["02 Build"]           │
                           :73  if workflow_run.conclusion            │
                                == 'success'                           │
                           :93  DEPLOY_SHA = workflow_run.head_sha     │
                           :90  "Require the security scan to have     │
                                 passed for this commit"              │
                                 (GET runs?head_sha=…  → 05 Security)  │
                           :127 ecs-deployment needs:                  │
                                 pre-deployment-validation             │
                                      │                                │
                                 ►► FAILED, ECS SKIPPED ◄◄        ►► PUBLISHED ◄◄
                                    (fail-CLOSED, correct)          S3 + CloudFront
                                                                    (fail-OPEN)
   05 Security
   trigger: push + schedule
   ►► FAILED (Bandit + Trivy IaC) ◄◄
```

### 4.2 The frontend path has no gate — verified by enumeration

`VER` Measured against the committed `06-frontend-deploy.yml` (615 lines):

| Probe | Result |
|---|---|
| `needs:` occurrences | **0** |
| `workflow_run` occurrences | **0** |
| References to `05 Security` / `check-runs` / a conclusion gate | **0 real** — the 2 textual matches are the word *"conclusion"* inside `echo` strings at `:511` and `:596` |
| Triggers | `push: branches:[main, master], paths:['algo22-terminal/**', '.github/workflows/06-frontend-deploy.yml']` + `workflow_dispatch` |

So the only precondition for publishing to production S3/CloudFront is **"a push to main touched the
frontend."** Correctness and security results for the same SHA are never consulted.

The workflow does contain real internal safety checks — `Reject implausible release artifacts in
dist/` (which catches placeholder installers), Supabase/API configuration verification, a bundle
checksum, and a blocking WebSocket-upgrade probe. **Those validate the artifact's shape, not the
commit's correctness or security posture.**

### 4.3 It already happened

`VER` Run `38129699497`, `headSha = ecd23a9e…`, event `push`, conclusion **success**. Step outcomes:

```
success  Build frontend
success  Reject implausible release artifacts in dist/
success  Sync build artifacts to S3            ◄── PUBLISHED
success  Invalidate CloudFront cache           ◄── CACHE PURGED TO SERVE IT
success  Verify deployment
success  Verify the WebSocket upgrade reaches the origin
```

At the same timestamp window: `05 Security` (`38129699454`, same SHA, event `push`) **failure**;
`01 PR Check` at this SHA **never ran**. **Production frontend assets are live from a commit with a
failing security scan and zero test evidence.**

### 4.4 Fail-closed design — proposed, not implemented

The brief's four conditions, each mapped to a mechanism already proven in this repository by
`03-deploy.yml`:

| Must block when | Mechanism |
|---|---|
| Required checks have not run | Query `GET /repos/{repo}/actions/runs?head_sha=$SHA` for **both** `01 PR Check` and `05 Security`; **absence of a run is a failure**, not a pass |
| A check failed or was cancelled | Require `conclusion == "success"`; treat `cancelled`, `timed_out`, `action_required`, `skipped` and `null` as failure |
| A success belongs to a different commit | Pin to `head_sha` exactly, as `03-deploy.yml:93` already does. **Never match by branch or by latest run** |
| The artifact was not built from the validated commit | Build and publish in the same job from `github.sha`, and record the bundle checksum — the workflow already computes one at `Record build artifact metadata`; assert it in the verify step |

**Two design cautions.** First, converting the frontend to `workflow_run: ["02 Build"]` would
introduce the same trust boundary `03-deploy.yml` carries: `workflow_run` runs with repository write
scope on the **default branch's** workflow definition, so the SHA pin is what keeps it honest —
copy the pin, not just the trigger. Second, a gate job must not be bypassable by
`workflow_dispatch`; `03-deploy.yml:73` admits dispatch unconditionally (`|| github.event_name ==
'workflow_dispatch'`), which is a pre-existing hole worth closing in the same change.

**Validation plan:** the repository supports workflow-policy tests — a static test asserting that
every deploy-capable workflow either declares a SHA-pinned gate job or has no publish step would
pin this invariant. **No workflow file was edited; no deploy, rollback, CDN purge or DNS change was
performed.**

---

## 5. Workstream D — CI evidence for the candidate SHA

### 5.1 No run was dispatched, and why

`VER` `01-pr-check.yml` at HEAD declares **`workflow_dispatch`: 0** and **`push`: 0** — it is
`pull_request`-only, so there is literally nothing to dispatch. The only supported route is a pull
request carrying the SHA, and `main` is already at it, so that requires creating a branch and PR.

**That route provisions infrastructure:** `provisioning-and-isolation` runs
`provision_database.py --apply --create-substrate` **twice**. The brief is explicit that this is an
infrastructure-provisioning action and must not be triggered without confirmed authority. **No
authority was supplied, so no PR was opened and no run dispatched.**

**Isolation evidence gathered in the gate's favour, for whoever authorizes it:**
`01-pr-check.yml:593` sets `PROVISION_DATABASE_URL` to a **loopback** job-scoped service
(`…@localhost:5432/postgres`), and the workflow deliberately withholds
`--i-understand-this-is-remote` so that *"if this URL ever stops being loopback, this step must fail
rather than proceed."* `AERORA_TEST_DATABASE_URL` is also set (`:592`), so the PostgreSQL-gated
suites would un-skip. **Verifying a design is not authorization to run it.**

### 5.2 Run matrix at `ecd23a9e` — exact SHAs

| Workflow | Run ID | Event | `headSha` | Conclusion |
|---|---|---|---|---|
| `02 Build` | 38129699471 | push | `ecd23a9e` | **success** |
| `06 Frontend Deploy` | **38129699497** | push | `ecd23a9e` | **success — PUBLISHED (§4.3)** |
| `07 Desktop Build` | 38129699516 | push | `ecd23a9e` | success |
| **`05 Security`** | **38129699454** | push | `ecd23a9e` | **failure** |
| **`05 Security`** | 38132697285 | schedule | `ecd23a9e` | **failure (retry)** |
| **`03 Deploy`** | **38130273012** | workflow_run | `ecd23a9e` | **failure — ECS SKIPPED** |
| `04 Nightly Audit` | 38123569435 | schedule | `074b772a` | success — **different SHA** |
| **`01 PR Check`** | — | — | — | **NOT RUN at this SHA** |

`05 Security` history on `main`: **failure, failure** at `ecd23a9e`; **six consecutive successes** at
`074b772a`. A regression whose cause is §2.3 and §3.

### 5.3 Test totals — what exists and what does not

| Lane | Status at `ecd23a9e` |
|---|---|
| Unit/integration (~11,000) | **NOT RUN** |
| PostgreSQL `database-tests` | **NOT RUN** |
| `provisioning-and-isolation` (drift, tenant isolation, ORM, security probe) | **NOT RUN** |
| real-Redis | **NOT RUN** |
| `static-audit` / flake8 | **NOT RUN** |
| Bandit | **RAN — FAILED**, 9 findings |
| Trivy IaC | **RAN — FAILED**, 5 CRITICAL |
| Container / frontend / desktop builds | **RAN — PASSED** |

**`NOT RUN` is not `PASS`, `SKIPPED` or `XFAIL`.** No CI validation is claimed for this SHA, and no
result from `f5b99e57` or `074b772a` is attributed to it.

### 5.4 Local evidence obtained this phase

Deterministic, no database, no network, `-p no:cacheprovider` throughout.

| Command | Result |
|---|---|
| `pytest tests/test_drift_classification.py tests/test_snapshot_dimension_invariants.py -q` | **140 passed in 6.41s**, exit 0 |
| 12-file PostgreSQL lane, combined | **11 passed, 228 skipped, 1 xfailed in 48.34s**, exit 0 |
| 5 quota suites | **60 passed, 5 skipped in 28.17s**, exit 0 |

Per-file lane results, measured individually (local, PostgreSQL absent — **not** a CI result):

| Test file | Local |
|---|---|
| `test_tenant_isolation_rls_postgres` | 29 skipped |
| `test_cross_tenant_read_sweep_postgres` | 6 skipped |
| `test_billing_data_isolation_postgres` | 27 skipped |
| `test_tenant_referential_integrity_postgres` | 30 skipped |
| `test_pgcrypto_preflight_postgres` | 5 skipped |
| `test_signup_integration_postgres` | 12 skipped |
| `test_position_lifecycle_postgres` | 6 skipped |
| `test_referral_money_lifecycle_postgres` | 12 skipped |
| `test_orm_models_match_provisioned_schema` | **4 passed**, 1 xfailed |
| `test_bug049_identity_types_postgres` | 76 skipped |
| `test_schema_drift_checker_is_read_only_postgres` | 12 skipped |
| `test_production_security_probe_rolls_back_postgres` | **7 passed**, 13 skipped |

`AERORA_TEST_DATABASE_URL`, `PROVISION_DATABASE_URL`, `DATABASE_URL` and `AERORA_TEST_REDIS_URL` are
**all unset** locally. **228 skips are not passes.**

---

## 6. Workstream E — migration-to-dimension analysis for the stale baseline

`staging/provisioning_snapshot.json`: 16 top-level keys, **no `_provenance`**, 396,325 B, captured
**10-06 20:14:20**, blob `e6069946…`. `scripts/verify_schema_drift.py:81` points `DEFAULT_BASELINE`
at it; `:334` loads it; `:212` and `:367` iterate `SNAPSHOT_QUERIES` and **never** the JSON's own
keys. A search for `provenance` in that file returns **0 matches** — **missing `_provenance` is
metadata only and cannot fail the gate.**

**Eleven migrations postdate the capture.** DDL classified by pattern match over each file:

| Migration | Snapshot dimensions it can affect |
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

**Aggregate expected legitimate drift — 11 of 16 dimensions:**

| Dimension | Migrations affecting it |
|---|---|
| columns | **10** |
| unique_and_check_constraints | **9** |
| privileges | **6** |
| indexes | **6** |
| policies | **5** |
| primary_keys | **5** |
| tables, foreign_keys, row_level_security | **4** each |
| functions | 2 |
| enum_types | 1 |
| extensions, sequences, triggers | **0** (pattern-based; `triggers` deserves a second look since 022 concerns an `updated_at` column) |
| alembic_version, recorded_migrations | advisory; would move only if these migrations were ledgered |

### 6.1 How to separate stale baseline from classifier defect

The analysis gives a concrete expectation to compare against, which is the point of doing it before
the run rather than after.

| Observed drift signature | Diagnosis |
|---|---|
| Drift confined to the 11 dimensions above, concentrated in `columns`/`privileges`/`policies` | **stale baseline — expected, not a defect** |
| A **modified** object reported as **1 missing + 1 added** | **classifier defect** — would contradict W1D-13 and the 140 local tests |
| A nonzero verdict printing no subject (`columns: 0 missing, 0 unexpected`) | **classifier defect** — the original W1C-02 symptom |
| Drift in `extensions` or `sequences` | **unexplained** — investigate; no post-snapshot migration touches them |

**The highest-signal discriminator:** migrations that *alter existing* objects — 022, 023, 027, 030,
031, 032 — should surface as **`MODIFIED`** rows. If they instead appear as missing+added pairs, the
classifier is at fault, not the baseline.

**The baseline was not regenerated.** No `provision_database.py`, no `DROP`/`CREATE DATABASE`, no
severity change, no edit to the production-audit path. Recapture is `AP-W1D-4` (owner *DB owner +
code owner*), **NOT GRANTED**, and `verify_production_schema.py` remains byte-identical at
`6e194f43…`.

---

## 7. Workstreams F–L — status

Every target hash was verified **CLEAN vs HEAD** before reusing prior source findings.

| WS | Item | Current state | Authorization | Action taken |
|---|---|---|---|---|
| **F** | **W1C-02 / criterion 6** | `tests/test_drift_classification.py` HEAD `fcffbe8e…` **665 lines**; worktree `99a2917a…` **808 lines**. The +143-line legacy-invariant addition is **uncommitted** and belongs to a concurrent session. **140 passed locally.** Criterion 10's suite is committed (`f1f5c02c…`) but has never executed — its lane never ran. Scope question (`compare()` only vs all three paths) **undecided**; `diff_snapshots()` still uses `[r for r in a if r not in b]` at `verify_provisioning.py:513`; `check_drift()` imports no classifier (`verify_production_schema.py:100`) | **ABSENT** | **file left untouched; not staged, not committed.** Decision request in §9 |
| **G** | **D7-1 vault signature** | `load_decrypted_keys(self, user_id, exchange_id)` — 2 params. **3** invalid `access_token=` sites: `orders.py:789-793` (unguarded → 500), `orders.py:1095-1099` (masked → `[]`), `market.py:50-54` (masked → **authenticated users served anonymous market data**). `exchange.py:993-996` is correct. Phase 14: git history shows **one signature form ever**, and method + broken callers entered in the same commit `6c5660a1`, so deleting the kwarg **loses no behaviour** | **ABSENT** | no source or test edited |
| **H** | **W-6 error classification** | `exchange_executor.py:1225-1257` (CLEAN `df4fd42a…`). symbol/market branch at `:1246` precedes timeout at `:1249`; bare `"key"` at `:1252`. Phase 14's repository-wide read: the **only** production read of `ExchangeError.retryable` is the log line at `:1687` — so this is **misclassification and a corrupted retry contract, not demonstrated retry suppression** | **ABSENT** | no edit; **no retry-suppression claim made** |
| **I** | **QUOTA-01** | `backend_app/core/subscription_engine.py` CLEAN `256f0399…` and **committed** — the "fix is uncommitted" blocker is resolved. **Local: 60 passed, 5 skipped.** The 5 skips need real Redis. Locally `REDIS_URL`/`AERORA_TEST_REDIS_URL`/`REDIS_HOST`/`REDIS_PORT` unset, 6379 unreachable. CI **does** provision `redis:7-alpine`, job-exclusive and ephemeral, with the committed `/15`→`/0` fix at `AERORA_TEST_REDIS_URL: redis://localhost:6379/0` | tests ok | suites run; **real-Redis UNEXECUTED at this SHA.** No URL fabricated, no service started, no enforcement weakened |
| **J** | **TENANT-01 / D6-C** | `VER` **`FORCE ROW LEVEL SECURITY`: 0 matches** in `backend_app/migrations/*.sql` and `backend_app/alembic/versions/*.py` outside comments — prose only, plus a comment at `026_…:75`. `tenant_db_context.py` **unwired**; the only GUC write is an inline `SET LOCAL app.current_tenant_id` at `transactional_execution_manager.py:225`. Prior records: app connects as owner `postgres` with `rolbypassrls`, live `relrowsecurity=true, relforcerowsecurity=false`; scorecard `tenant_force_rls: OPEN` | n/a | **static only. NOT CERTIFIED.** No DB connection. **FORCE RLS not enabled** — correctly, since context propagation and rollback are undemonstrated |
| **K** | **Portfolio cache / workers / health** | `VER` `backend_app/backend/key_manager.py` **does not exist**; `portfolio_cache_updater.py:151` imports it, the `ImportError` is swallowed per user, so the cache is **silently dead while `/health/services` reports `ACTIVE`** — a false-green signal. Phase 14, verified against current `main.py` (CLEAN `a3849d4a…`): `ReconciliationScheduler` **is** started at `:456` and `OrderWatchdog` at `:432`; `ExchangeReconciliationService` **not started**; `MarketplaceExpiryWorker`/`expiry_sweep` **0 matches** | **ABSENT**, and `main.py` has **3 contending workstreams with no coordinator** | no edit. This corrects earlier phases that implied no reconciliation component runs — the *scheduler* does |
| **L** | **A23 Redis governance** | A23E6 form CLEAN `7b206850…`, committed, **still blank: 12 ☐ / 0 marked / 6 blank fields → 0 of 8 gates**. `redis_manager.py` CLEAN `d7b0e811…`; `global_safety.py` CLEAN `4b2a1fc5…`; `test_redis_debounce.py` CLEAN `5a8db7a8…`; `tests/test_a23_redis_pool_lifecycle.py` **ABSENT**. A23E14 §3's decisive finding: W1D-12's in-session grant worked because the `Code owner` role **pre-existed** in ledger `:802`'s Owner column — **A23 has no such row**, so the role must be established first | **ABSENT** | nothing created or edited |

---

## 8. Files, hashes and authorizations

**Changed by this phase: 1** — this report. New, untracked, under `staging/`, destination verified
ABSENT immediately before writing.

**Deliberately untouched — no hash moved:**

| File | Hash | Reason |
|---|---|---|
| `terraform/alb.tf`, `security_groups.tf`, `redis-only/main.tf` | — | infrastructure grant ABSENT |
| 3 alembic migrations (`a1b2c3d4e5f6…`, `b2c3d4e5f6a7…`, `d4e5f6a7b8c9…`) | — | P-B1 grant ABSENT |
| `tests/test_bandit_sast_gate_is_green.py` | — | P-B2 grant ABSENT |
| `.github/workflows/01/03/05/06-*.yml` | `91d55a37…`, `eb8078ea…`, `e2d9ccc6…`, — | workflow grant ABSENT; **and never modify a gate to make CI pass** |
| `backend_app/routers/orders.py`, `market.py` | `ff2d34bf…`, `16f0377c…` | D7-1 ABSENT |
| `backend_app/backend/exchange_executor.py` | `df4fd42a…` | W-6 ABSENT |
| `backend_app/backend/redis_manager.py` | `d7b0e811…` | `D-A23C-1` unresolved |
| `tests/test_a23_redis_pool_lifecycle.py` | — | `D-A23C-7` unresolved; still ABSENT |
| `backend_app/main.py`, `portfolio_cache_updater.py` | `a3849d4a…`, `a74d7644…` | 3 contending workstreams, no coordinator |
| `scripts/verify_production_schema.py` | `6e194f43…` | D5 sign-off ABSENT |
| `staging/provisioning_snapshot.json` | `e6069946…` | **baseline NOT regenerated**; `AP-W1D-4` NOT GRANTED |
| `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` | `7e238620…` | **`:802` still `NOT GRANTED`**; no maintainer established |
| `tests/test_drift_classification.py`, `staging/PHASE11B_A23E11_…md` | worktree `99a2917a…` | **another session's work — preserved** |

**Authorizations exercised: none.** No approval was inferred from this brief's framing, from the
commit or its push, from the frontend deploy's success, from passing local tests, from role labels,
from commit authorship, from file cleanliness, or from silence.

---

## 9. Owner decisions — copy-ready

> **1 · Frontend release gate (NEW, highest priority).** *"I authorize editing
> `.github/workflows/06-frontend-deploy.yml` to add a SHA-pinned pre-publish gate requiring
> `01 PR Check` and `05 Security` to have concluded `success` for `github.sha`, treating a missing
> run as failure, and to close the unconditional `workflow_dispatch` bypass in
> `03-deploy.yml:73`. No other workflow change is authorized."* — **workflow / release owner**
>
> **2 · Bandit gate.** *"I authorize P-B1 (`# nosec B608` with per-site reasons on the 9 lines in
> the 3 alembic migrations) **and** P-B2 (widening `tests/test_bandit_sast_gate_is_green.py` to run
> `-r backend_app`). P-B1 is not authorized without P-B2."* — **code owner**
>
> **3 · Trivy IaC.** *"I authorize P-A1/P-A2 (remove the unnecessary `0.0.0.0/0` egress from both
> Redis security groups) and P-A3/P-A4 (per-resource documented ignores for `AWS-0054` and the two
> conventional `AWS-0104` rules, with VPC endpoints tracked as a follow-up). No `.trivyignore`
> file, no severity-threshold change."* — **infrastructure owner**
>
> **4 · PR for CI evidence.** *"I authorize opening a pull request carrying `ecd23a9e` so
> `01 PR Check` runs, and I confirm `PROVISION_DATABASE_URL` resolves to the disposable job-scoped
> loopback service."* — **coordinator** (the only route; the workflow has no `workflow_dispatch`)
>
> **5 · W1C-02 scope + criterion 6.** *"W1C-02's authoritative subject is ⟨`compare()` only⟩ /
> ⟨all three call paths⟩, and I authorize committing the +143-line legacy-invariant addition in
> `tests/test_drift_classification.py`."* — **code owner**. D5's conditional grant stays separate.
>
> **6 · D7-1.** *"I authorize deleting the 3 invalid `access_token=` kwargs in `routers/orders.py`
> and `routers/market.py`, adding one signature-binding test, and aligning the double in
> `tests/test_market_none_keys_fix.py`."* — **router owner**
>
> **7 · A23.** Two steps per A23E14 §4: add the role-establishing register row to
> `PHASE11B_A3_BLOCKER_REGISTER.md` §3, **then** complete the A23E6 §3 form.

Items 1–4 need no application-code change and together move `main` from *"published, unverified and
red"* to *"gated, verified, or honestly red with evidence."*

---

## 10. Release recommendation

**DO NOT DEPLOY THE BACKEND. DO NOT PUBLISH FURTHER FRONTEND CHANGES UNTIL DECISION 1 IS IN PLACE.
DO NOT REVERT `ecd23a9e`.**

Supported by current evidence: `05 Security` failing on two independent scanners at this SHA;
`03 Deploy` self-blocked at its own gate with ECS skipped; **no** unit, integration, PostgreSQL,
Redis or flake8 evidence at this SHA; and **frontend assets already published from this commit
without any gate** (§4.3).

The commit is **unverified, not known-bad** — which is why reverting 3,723 files would be the wrong
instrument: it would destroy work and produce no evidence. The correct sequence is decisions 1→2→3→4:
gate the frontend, make the security gates honestly green, then obtain test evidence via a PR.

**A frontend publish that bypassed the gates is not evidence of a healthy release path — it is the
defect.**

| Final status | |
|---|---|
| Frontend release path | **FAIL-OPEN — published unvalidated code (§4)** |
| Backend release path | **FAIL-CLOSED and working — refused correctly** |
| `05 Security` | **FAILING** — 9 Bandit B608 + 5 Trivy CRITICAL, all diagnosed, none fixed |
| `01 PR Check` at `ecd23a9e` | **NOT RUN**, and PR-only |
| PostgreSQL integration | **UNVERIFIED** |
| Drift baseline | **DEMONSTRABLY STALE** — 11 migrations, 11 of 16 dimensions (§6) |
| W1C-02 | **OPEN** — criterion 6 uncommitted, criterion 10 unexecuted, scope undecided |
| D5 | **CONDITIONALLY GRANTED, sign-off ABSENT** |
| D7-1 / W-6 / portfolio cache / workers | **confirmed defects, grants ABSENT, unmodified** |
| QUOTA-01 | **OPEN** — fix committed, 60 passed / 5 skipped, real-Redis UNEXECUTED |
| TENANT-01 | **OPEN — NOT CERTIFIED** |
| A23 Redis P1 | **NOT STARTED — 0 of 8 gates** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |

No security gate was weakened, no severity threshold lowered, no CRITICAL suppressed, no secret
exposed, and no infrastructure, database, CDN or DNS was mutated.
