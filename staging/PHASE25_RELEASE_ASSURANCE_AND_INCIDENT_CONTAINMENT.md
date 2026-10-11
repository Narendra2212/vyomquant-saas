# Phase 25 — Frontend Incident Containment, Gate Integration, Dependency Evidence, and End-to-End Release Assurance

**Destination verified ABSENT before writing.** No Phase 13–24 or concurrent artifact was
overwritten.

**0 production files · 0 tests · 0 workflows · 0 scripts · 0 Terraform · 0 migrations ·
0 governance records changed.** No commit, stage, push, reset, stash operation, branch switch, CI
dispatch, deploy, publish, rollback, registry mutation, or database contact.

> **DO NOT RELEASE · FRONTEND PUBLICATION BLOCKED · OWNER CONTAINMENT DECISION REQUIRED ·
> LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED**

## What is new in this phase

Five results are new, and two of them correct prior reports.

1. **Lane C — the dependency audit is not a gate and never was.** `05-security.yml:39` ends in
   `|| echo "Safety advisory logged"`. Even on the runs where it executes, a vulnerability finding
   **cannot fail the job**. The same line is present at the parent commit, so this is not a
   regression — the dependency audit has never been capable of blocking a release.
2. **Lane C — the two scanner failures at `ecd23a9e` have different causes, and one is a new
   check, not new code.** Parent `074b772a` had jobs `['sast-scan', 'container-security']`; HEAD
   has `['sast-scan', 'container-security', 'iac-scan']`. **The entire Trivy IaC job was added by
   `ecd23a9e`**, and all four Terraform files it flags existed at the parent. Bandit is the
   opposite: all three finding-bearing migrations are **absent** at the parent.
3. **Lane A — this makes "roll back to the last green SHA" a trap.** `074b772a` is green in part
   because it was not looking. Rolling back to it un-installs the IaC scanner.
4. **Lane B — Phase 23's job-level conclusion is wrong as stated.** `staging/phase18_release_gate.py`
   carries **5** job-level references and closes the gap; Phase 23 compared the production verifier
   against the *Phase 17* prototype and generalised to "both".
5. **Lane C — PyJWT is pinned at `2.14.0`**, so the CVE-2026-102268 vector is bumped. But there is
   **no lockfile, no hashes, and zero dependency-audit evidence for the current set.**

---

## 1. Entry and exit manifests

### 1.1 Repository state — identical at entry and exit

| Invariant | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` | `ecd23a9e…` | unchanged |
| divergence (ahead / behind) | **0 / 0** | 0 / 0 |
| `HEAD^{tree}` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **unchanged** |
| staged / unmerged | **0 / 0** | 0 / 0 |
| tracked files modified | 2 | **2 — the same two** |
| worktrees | 1 | 1 |
| stash entries | 2 | **2 — neither applied, dropped, nor inspected** |
| `git reflog -1` | `ecd23a9e … commit: launch phase 1` | unchanged |

Stashes left untouched: `stash@{0}: filter-branch: rewrite`, `stash@{1}: WIP on main: c673409
Release v0.1.0…`.

### 1.2 Concurrent work preserved

| Path | State | Disposition |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | ` M` | **not touched** |
| `tests/test_drift_classification.py` | ` M` | **not touched** — another session's `AP-W1D-3` work |

Sibling artifacts read but not modified: both Phase 21 reports, `PHASE22` ×2, `PHASE23`,
`PHASE24`, `PHASE18`–`PHASE20`, `staging/phase17_frontend_release_gate.py`,
`staging/phase17_test_frontend_release_gate.py`, `staging/phase18_release_gate.py`,
`staging/phase18_test_release_gate.py`.

### 1.3 Target blobs — all clean, all unchanged since Phase 21

`api_key_vault.py` `74a005941146` · `routers/orders.py` `ff2d34bf030a` ·
`routers/market.py` `16f0377cb9f6` · `exchange_executor.py` `df4fd42a1510` ·
`redis_manager.py` `d7b0e811b615` · `test_bandit_sast_gate_is_green.py` `a7deff99c9e5` ·
`terraform/alb.tf` `cdead9289f20` · `terraform/security_groups.tf` `87c9d131051d` ·
`terraform/redis-only/main.tf` `48b9471035b7` · `scripts/require_security_scan.py` `a1c8dd2d48fb` ·
`01-pr-check.yml` `91d55a379115` · `02-build.yml` `42c7696eb7cb` · `03-deploy.yml` `eb8078eac062` ·
`05-security.yml` `e2d9ccc6f951` · `06-frontend-deploy.yml` `285cc2d19b2b` ·
`terraform/ecs.tf` `7d05ee95c777` · `scripts/ecs_deploy_and_diagnose.py` `cec975b267e8`.

**No source, workflow, Terraform, migration, test or governance file has changed since Phase 21.**

### 1.4 Tooling — discovered, not assumed

| Tool | State | Consequence |
|---|---|---|
| Python 3.12.10, `.venv` | FOUND | all local lanes runnable |
| `gh` 2.100.0, authenticated | FOUND | read-only CI queries available |
| bandit 1.9.4, PyYAML | FOUND | Lane F reusable (not re-run, inputs unchanged) |
| **`safety`** | **ABSENT** | Lane C dependency scan cannot be executed locally |
| **`pip-audit`** | **ABSENT** | no substitute audit available |
| `trivy`, `docker`, `terraform` | ABSENT | IaC / container / plan all BLOCKED |
| PostgreSQL, Redis, AWS credentials | ABSENT | Lanes I/J runtime BLOCKED |

One correction to my own earlier method: a `Get-ChildItem -Recurse -Filter` sweep for
`portfolio_cache_updater.py` returned **nothing** while the file exists at
`backend_app/backend/portfolio_cache_updater.py`. The traversal failed silently under
`-ErrorAction SilentlyContinue`. All inventory in this report uses `git grep` / `git ls-tree` /
indexed search instead. Three copies of that filename exist; only the `backend_app/backend/` one
carries the defect.

---

## 2. Authorization matrix

Independently re-read this phase. `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804` →
`AP-W1D-1` … `AP-W1D-5` **all NOT GRANTED**.
`PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` → **0 ticked / 12 unticked**. No `CODEOWNERS`,
`OWNERS` or `MAINTAINERS` exists anywhere in the tree.

**The only live grant is `AP-W1D-3`**, confined to four drift-classifier files by its own text
(*"No other files are authorized"*). **No Phase 25 target falls inside it.**

| Lane | Exact files that an implementation would touch | Grant | Permitted here |
|---|---|---|---|
| **A** incident containment | S3 / CloudFront / workflow disablement | **NONE** | read-only CI queries; decision package |
| **B** frontend gate integration | `06-frontend-deploy.yml`; `scripts/`; `tests/test_frontend_release_gate.py` | **NONE** (`D-P15-4` is a *request*) | isolated prototype validation |
| **C** dependency audit | `.github/workflows/05-security.yml` | **NONE** | source trace; manifest inspection |
| **D** backend verifier | `scripts/require_security_scan.py`, `03-deploy.yml` | **NONE** | read-only review |
| **E** image provenance | `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, `scripts/deploy.sh`, `terraform/ecs.tf`, 3 root JSON files | **NONE** | consumer census; contract design |
| **F** Bandit / Trivy | 3 migrations, `tests/test_bandit_sast_gate_is_green.py`, `terraform/*.tf` | **NONE** | reused scan evidence; source disposition |
| **G** D7-1 | `routers/orders.py`, `routers/market.py`, test double | **NONE** + open policy decision | source re-verification |
| **H** D7-2 | `portfolio_cache_updater.py`, `core/state.py` | **NONE** | source re-verification |
| **I** tenant / RLS | tenant context, migrations | **NONE** + **BLOCKED** (no PostgreSQL) | static analysis |
| **J** Redis / quotas / workers | `redis_manager.py`, `main.py` | **NONE** + **BLOCKED** (no Redis) | static analysis |

Rejected as authorization, explicitly: this prompt; a report titled "AUTHORIZED…"; a decision ID
that records a request; a passing prototype suite; an unmodified tree; commit authorship; role
labels; another session's activity; silence. **`PHASE22_AUTHORIZED_RELEASE_EXECUTION…`'s title is
prompt framing — its own §3 matrix records all ten lanes `NOT GRANTED`.**

**No lane is implemented. Per the brief, that did not stop the phase:** Lanes A, B, C, E and G
produced new verified results.

---

## 3. Lane A — current incident status, re-established

### 3.1 Re-queried live, not carried forward

`GET /actions/workflows/06-frontend-deploy.yml/runs` → `total_count = 60`, newest five inspected:

| Run | SHA | Event | Conclusion | Started → Concluded |
|---|---|---|---|---|
| **38129699497** | **`ecd23a9e`** | push | **success** | 09:48:45Z → **09:50:13Z** |
| 37350937230 | `6c5660a1` | push | success | 2026-10-05T17:46:29Z |
| 37192914960 | `eee73265` | push | success | 2026-10-04T09:40:39Z |
| 37144737367 | `1fb6ef3a` | push | success | 2026-10-03T18:34:59Z |
| 37117547584 | `c60546c6` | push | success | 2026-10-03T10:47:17Z |

`GET /actions/runs?per_page=30` → the newest run **in the entire repository** is `05 Security`
(`schedule`) at `ecd23a9e`, started 10:38:58Z. **No repository activity of any kind since then.**

| Required determination | Finding | Class |
|---|---|---|
| Newest frontend publication run | **38129699497** | **Confirmed** |
| SHA it published | **`ecd23a9e`** | **Confirmed** |
| Conclusion / publication time | **success**, concluded **09:50:13Z** (88 s elapsed) | **Confirmed** |
| Did a later run publish a replacement? | **No** — next-newest is 6 days earlier at `6c5660a1` | **Confirmed** |
| Later security/correctness evidence for the published SHA? | **No** — `05 Security` red twice; `01 PR Check` never ran | **Confirmed** |
| Evidence of rollback, hotfix or mitigation? | **None found** — no new runs, no new commits, no new frontend run | **Not evidenced** |
| What the published bundle actually contains now | **requires production S3 / CloudFront inspection** | **Unknown** |
| Exploitation, compromise, secret leakage | **no evidence exists and none was sought** | **Not evidenced** |

### 3.2 The eight-minute gap, verified rather than copied

Re-derived from `run_started_at` / `updated_at` at the exact SHA:

- `06 Frontend Deploy` concluded **09:50:13Z**.
- `05 Security` (push, `38129699454`) concluded **09:58:34Z**.
- Difference: **8 min 21 s.** **Confirmed.**

The separate `05 Security` run at 10:38:58Z is `event=schedule`, not a retry — `05-security.yml`
declares `on: [push, pull_request, schedule]` with `cron: '0 4 * * *'`. The push-triggered verdict
and the scheduled verdict are kept distinct throughout this report.

### 3.3 New: `05 Security` was green at the parent and on three PRs

| Workflow | SHA | Event | Conclusion | Started |
|---|---|---|---|---|
| `05 Security` | **`074b772a`** (parent of `ecd23a9e`) | schedule | **success** | 2026-10-10T10:27:06Z |
| `05 Security` | `f5b99e57` | pull_request | **success** | 2026-10-10T15:01:59Z |
| `05 Security` | `d07014d3` | pull_request | **success** | 2026-10-10T11:10:41Z |
| `05 Security` | `a624ce89` | pull_request | **success** | 2026-10-10T10:55:57Z |
| `01 PR Check` | `f5b99e57` / `d07014d3` / `a624ce89` | pull_request | **failure ×3** | 2026-10-10 |

So the security workflow turned red **at `ecd23a9e` specifically**, and the correctness workflow is
red everywhere it runs. §5.3 explains why the security transition is only half what it looks like.

### 3.4 Containment decision package — nothing executed

| Option | Action | Prerequisites | Risk | Verification criteria |
|---|---|---|---|---|
| **1. Suspend publication** | Disable `06 Frontend Deploy`, or require PRs for `algo22-terminal/**` via branch protection | Repo-admin access | **Lowest.** Stops recurrence; does **not** address live assets | No further `06 Frontend Deploy` runs appear on push |
| **2. Verify in place** | Retrieve the published bundle and audit its dependency manifest offline | Read access to the S3 bucket, or an owner-supplied manifest | **None** — non-mutating | Converts §3.1's `Unknown` into evidence; produces the audit `ecd23a9e` never had |
| **3. Roll back** | Point S3/CloudFront at the last SHA with green security evidence | A known-good SHA; **see the trap below** | **Production mutation.** May regress intended UI changes | Served asset hash matches the approved SHA's build |
| **4. Re-publish gated** | Fix findings, then publish through an exact-SHA gate | Lane B grant + Lane C/F closure | Correct end state | Gate refuses the old SHA and admits the new one |

**The rollback trap, new this phase.** The obvious target for option 3 is `074b772a`, the parent,
whose `05 Security` was green. But that run was green **partly because the IaC scanner did not
exist yet** (§5.3). Rolling back to it would restore a commit that cannot fail a check it does not
contain. If option 3 is chosen, the justification must be "last SHA whose *frontend bundle* is
acceptable", not "last SHA with a green security workflow" — those are different claims and only
the first supports a frontend rollback.

**Recommended order, for the owner to decide:** 1 → 2 → then 3 or 4 as that evidence dictates.

**Not performed:** no rollback, no S3 or CloudFront mutation, no workflow disablement, no branch
protection change, no credential retrieval, no cloud-state mutation of any kind.

### 3.5 Why it published — re-measured

`06-frontend-deploy.yml`, 615 lines, parsed with PyYAML: **1 job** (`deploy-frontend`, 14 steps,
`needs = None`, `if = None`), triggers `push: [main, master]` + `workflow_dispatch`, **0
occurrences of `needs:`**, **0 references** to `05 Security` / `require_security_scan` /
`security-scan` / `ALLOW_UNSCANNED`. Publication steps: `aws s3 sync` ×3 (`:289`, `:299`, `:307`),
`aws cloudfront create-invalidation` (`:329`).

The workflow's own inline checks did run and did pass — service-role key absent (`:168`), no
localhost endpoints (`:188-202`), API-host allowlist (`:217`). They cover bundle secret leakage and
endpoint misdirection; **none of them is a security-scan gate.**

### 3.6 `01 PR Check` cannot be made to evaluate this commit

`on: [pull_request]` only — no `push`, no `workflow_dispatch`. Its 8 jobs (`static-audit`,
`format-check`, `unit-tests`, `paper-mode-tests`, `database-tests`, `provisioning-and-isolation`,
`docker-check`, `frontend-tests`) cannot run against a commit already on `main` without a PR
containing it. Additionally, `ecd23a9e` **added 202 lines to this very workflow** — so the commit
extended the correctness suite that has never been executed against it.

---

## 4. Lane B — gate integration, validated in isolation

### 4.1 Both prototypes executed this phase

```
pytest staging/phase18_test_release_gate.py -q   →  28 passed in 3.75s   EXIT 0
pytest staging/phase17_test_frontend_release_gate.py -q →  42 passed in 2.56s   EXIT 0
```

### 4.2 Property validation — measured against source, per the brief's checklist

| Required property | `require_security_scan.py` (installed) | `phase17_…gate.py` | `phase18_release_gate.py` |
|---|---|---|---|
| Exact candidate-SHA matching | query filter `head_sha=` (`:58`) | yes + re-asserted | yes + re-asserted |
| Required-workflow identity | **1 workflow**, `WORKFLOW_NAME` (`:41`) | 2, tuple | **2, dict → per-workflow mandatory jobs** (`:62`) |
| Deterministic newest-run selection | `sorted(…run_started_at)[-1]` (`:105`) | yes | yes |
| Refusal when no evidence exists | yes (`:95`) | yes | yes |
| Refusal on failed/cancelled/skipped/inconclusive | `cancelled`/`skipped` named; else falls through to `return 1` | enumerated frozenset | enumerated frozenset |
| **Mandatory-job conclusions** | **NO — 0 refs** | **NO — 0 refs** | **YES — 5 refs**; `_jobs_for_run()` `:149-151`, `_assert_mandatory_jobs_passed()` `:199` |
| Pagination refuses incomplete evidence | **NO page-walk** | runs only | **runs *and* jobs** |
| API / permission failures fail closed | yes | yes | yes |
| No unsafe override | `ALLOW_UNSCANNED` (narrow, missing-scan only) | none | none |
| `workflow_run` SHA vs checkout ref | n/a (already in deploy) | documented in docstring | documented in docstring |
| Bounded waiting | `WAIT_SECONDS=900`, `POLL=20` (`:43-44`) | none | none |

**Correction to Phase 23 §5.1.** It reports the job-level gap as *"open in BOTH implementations…
`/jobs` refs = 0"*. Measured this phase: `require_security_scan.py` **0**,
`phase17_frontend_release_gate.py` **0**, **`phase18_release_gate.py` 5**. Phase 23 compared
production against the *Phase 17* prototype and generalised. Phase 18's gate closes the gap, and
its suite contains the demonstration — `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`.

**Correction to Phase 23 §5.2b.** It states *"`testpaths` is empty — so the directory is not the
obstacle; the filename is."* `pytest.ini` sets `testpaths = tests`. There are **two** independent
reasons neither prototype is collected: the filename matches neither `test_*.py` nor `*_test.py`,
**and** the file sits outside `testpaths`. Both must be addressed, not one.

### 4.3 Recommended architecture — smallest maintainable form

**Use `phase18_release_gate.py` as the single verifier for both release paths.** Rationale from
measurement, not preference: it is a strict superset of the Phase 17 behaviour, it is the only
implementation that closes the job-level gap, and its `REQUIRED_WORKFLOWS` dict generalises the
production verifier's single hard-coded workflow name without changing its decision logic. Merging
it into `require_security_scan.py` preserves one code path for backend and frontend instead of
three.

Non-negotiables for the integration, each traced to a measured defect:

1. **A workflow-level `success` must not satisfy the gate.** `01-pr-check.yml` carries
   `continue-on-error: true` ×3 and six jobs behind `needs: static-audit`, so an unmet `needs:`
   yields `skipped`, not `failure`. Mandatory-job conclusions must be required explicitly as
   `success` — not "not failure".
2. **Pin the checkout to the validated SHA.** Under `workflow_run`, `actions/checkout` defaults to
   the default branch, so a gate can validate X while the job publishes Y. `ref: ${{
   github.event.workflow_run.head_sha }}` plus the same SHA into the gate.
3. **Declare `permissions: actions: read` explicitly.** `03-deploy.yml` documents the requirement
   in a header comment only; neither of its jobs has a `permissions:` block (§6.2).
4. **Every `aws s3 sync` and CloudFront step downstream of the gate via `needs:`.**
5. **No override input on the frontend path.**

### 4.4 Correctness-evidence route

`01 PR Check` is `pull_request`-only, so it supplies **no** push-time correctness evidence and
cannot be made to. Pretending otherwise is the failure this phase must avoid. Two testable routes,
both owner decisions:

- **Require PRs to advance `main`** via branch protection. Closes frontend *and* backend together,
  needs no workflow change, and binds evidence to the merge commit. Caveat: `01 PR Check` is
  currently **failing on all three recent PRs**, so this route is blocked until that is triaged.
- **Add a push-triggered correctness workflow** reusing `01-pr-check.yml`'s jobs via
  `workflow_call`, then require it by name in the gate. More moving parts; does not depend on repo
  settings.

---

## 5. Lane C — dependency audit execution

### 5.1 The decisive finding: it is advisory by construction

`.github/workflows/05-security.yml`, job `sast-scan`:

```yaml
:30   run: pip install bandit safety
:36   - name: Run Bandit Scan
:37     run: bandit -r backend_app -ll -f json -o reports/bandit-report.json
:38   - name: Run Safety Check
:39     run: safety check --json --output reports/safety-report.json || echo "Safety advisory logged"
:40   - name: Upload Security Reports
:41     uses: actions/upload-artifact@v4
```

Four defects in this job, all confirmed from source:

| # | Defect | Consequence |
|---|---|---|
| **C-1** | **`\|\| echo "Safety advisory logged"`** on `:39` | A dependency vulnerability **cannot fail the job**. The audit is advisory, always. |
| **C-2** | `Run Safety Check` has no `if:` | When `Run Bandit Scan` fails, the default fail-fast **skips** it — which is what happened at `ecd23a9e`. |
| **C-3** | `Upload Security Reports` has no `if: always()` | Reports are discarded exactly when a scan fails. Contrast `iac-scan`'s `Upload IaC Scan Report`, which **does** carry `if: always()`. The asymmetry is in one file. |
| **C-4** | `pip install bandit safety` is **unpinned** | The scanner version in CI floats. Local bandit 1.9.4 matching CI is an observation about one point in time, not a guarantee. |

**C-1 is the headline.** Every prior report treated the dependency gap as "the step was skipped".
Measured at the parent commit, `074b772a:.github/workflows/05-security.yml` contains the **same**
`Run Safety Check … || echo "Safety advisory logged"` line. So this is not a regression introduced
by the release — **the dependency audit has never been able to block anything.** The skip at
`ecd23a9e` removed evidence that was already non-binding.

### 5.2 Tooling viability — reported, not reproduced

`safety` is **absent locally** and `pip-audit` is absent, so the scan could not be executed here. Two
source facts bear on whether it works in CI at all:

- **No authentication is configured.** Workflow-wide search for `SAFETY_API_KEY` / `SAFETY_`
  returns 1 match, and it is `SafetyMonitor` in `01-pr-check.yml` — unrelated application code.
- Upstream, the `check` subcommand is deprecated in favour of `scan`, with `check` slated to invoke
  `scan` by default, and `safety scan` requires an account and authentication
  ([pipenv docs PR](https://github.com/pypa/pipenv/pull/6398/files),
  [pyupio/safety issue #663](https://github.com/pyupio/safety/issues/663)). *Content was rephrased
  for compliance with licensing restrictions.*

Combined with C-4 (unpinned install) and C-1 (failure suppressed), an unauthenticated deprecated
invocation would fail silently and the job would still report success. **Classification: the
`|| echo` suppression is Confirmed from source; the deprecation/authentication interaction is
Reported, not reverified — it needs one CI run with the suppression removed to settle.**

### 5.3 New: the two scanner failures have different causes

| | Bandit SAST | Trivy IaC |
|---|---|---|
| Did the **check** exist at parent `074b772a`? | **Yes** — `sast-scan` present | **No** — parent jobs were `['sast-scan', 'container-security']`; HEAD adds `iac-scan` |
| Did the **finding-bearing files** exist at parent? | **No** — parent has 17 migration files; all three finding-bearing migrations are **absent** | **Yes** — `alb.tf`, `security_groups.tf`, `redis-only/main.tf`, `ecs.tf` all present |
| Therefore | **New code broke an existing check** | **A new check surfaced pre-existing debt** |

`git diff --stat 074b772a ecd23a9e` over the workflow set: `05-security.yml` **+90**,
`01-pr-check.yml` **+202**, `alb.tf` **+25**; `02-build.yml`, `03-deploy.yml`,
`06-frontend-deploy.yml`, `require_security_scan.py`, `security_groups.tf`, `redis-only/main.tf`
**unchanged**.

The `iac-scan` job's own comment anticipated this outcome: it notes that gating a brand-new scanner
at a lower severity would fail the pipeline on pre-existing findings, and asks that a first-run
failure be read as a finding to triage rather than a regression. That is exactly the situation. It
does not make the findings acceptable; it means the remediation owner differs (infrastructure debt
vs. this commit's new migrations) and the rollback reasoning in §3.4 must account for it.

### 5.4 Required outcome — what a correct job must distinguish

The brief's five states, mapped to what the current job can express:

| State | Expressible today? |
|---|---|
| Scan passed | Yes |
| Scan failed | **No for Safety** (C-1 converts failure to success) |
| Scan was skipped | **Not in the artifact** — C-3 discards reports on failure |
| Tooling/dependency unavailable | **No** — indistinguishable from "passed" under C-1 |
| Report missing / not uploaded | **No** — C-3 makes this the normal case on failure |

Minimal change set (**unauthorized, not applied**): remove `|| echo` from `:39`; add
`if: always()` to `:40`; add `if: always()` to `Run Safety Check` so a Bandit failure does not
erase dependency evidence; pin `bandit==` and `safety==` at `:30`; and supply authentication or
switch to a tool that does not require it. Independent collection with a preserved overall failure
is achievable with `if: always()` on each scan step plus a final gate step that fails if any
mandatory scan did not conclude `success` — this collects results without converting a failure to a
pass.

### 5.5 PyJWT — pin bumped, evidence absent

| Item | Finding |
|---|---|
| Constraint | **`requirements-base.txt:61` → `PyJWT==2.14.0`.** The CVE-2026-102268 vector was `2.13.0`, so the pin **has been bumped.** |
| Manifest shape | `requirements.txt` → `-r requirements-base.txt` + `torch==2.3.1`; `requirements-cpu.txt` adds the CPU wheel index. |
| Pinning completeness | `requirements-base.txt`: **64 requirement lines, 64 pinned with `==`, 0 unpinned.** |
| **Lockfile** | **None.** No `*.lock`, no `poetry.lock`, no `requirements*.hash`. |
| **Hashes** | **None** — `--hash` appears nowhere, so installs are not pinned to artifact digests and transitive versions float. |
| Audit tooling | CI: unpinned `safety`, unauthenticated, failure-suppressed (§5.1–5.2). Local: absent. |
| **Audit evidence for `ecd23a9e`** | **NONE** — the step was skipped, and had it run it could not have failed. |

**Disposition: the specific PyJWT vector is closed in the manifest; the dependency-audit *control*
is not.** Per the brief, the incident is **not** declared resolved: direct pins are exact but
unhashed and unlocked, and no audit result exists for the current dependency set at the released
SHA.

---

## 6. Lane D — backend verifier and release policy

### 6.1 Verified behaviour — preserve all of it

`scripts/require_security_scan.py`, 130 lines. Properties confirmed at source and in production:
`WORKFLOW_NAME = "05 Security"` (`:41`); exact-SHA query (`:58`); bounded wait
`WAIT_SECONDS=900` / `POLL_SECONDS=20` (`:43-44`); newest-run selection (`:105`); missing-scan
refusal (`:95`); explicit refusal on `cancelled`/`skipped`; refusal while still running;
`ALLOW_UNSCANNED` **defaults to false when unset** (`:66`).

**Production proof:** at `ecd23a9e`, run `38130273012`, job `Pre-Deployment Validation Gate` failed
at step `Require the security scan to have passed for this commit`, and
`ECS Fargate Deploy & Self-Healing Diagnostics` concluded **`skipped`**. No reproducible defect was
found in any of the above, so **none of it is proposed for change.**

### 6.2 Open weaknesses

| # | Weakness | Evidence |
|---|---|---|
| **D-1** | **Mandatory-job conclusions not checked.** A run-level `success` with a skipped mandatory job is admitted. | 0 `/jobs` references; `01-pr-check.yml` has `continue-on-error: true` ×3 |
| **D-2** | **No pagination.** `per_page=100` with no page-walk, for runs *and* jobs. | `:58` |
| **D-3** | **One required workflow.** Security only; **no correctness evidence is required for a backend deploy.** | `WORKFLOW_NAME` is a single string (`:41`) |
| **D-4** | **`ALLOW_UNSCANNED` has no approval control.** The default is safe (`false`, `workflow_dispatch` input `type: boolean, default: False`) and its scope is narrow — it excuses only a *missing* scan, never a failed one. But **neither job declares an `environment:`**, so no GitHub Environment protection rule gates the dispatch. Its safety rests on repository dispatch permissions alone. | parsed: `environment = None` on both jobs |
| **D-5** | **`actions: read` is required by comment, not declared.** **Neither job has a `permissions:` block.** It works today on default token permissions; tightening repo defaults would break it. | parsed: `permissions = None` on both jobs |
| **D-6** | **Artifact-to-SHA association is broken** — see Lane E. The gate verifies a SHA; the deploy consumes a tag. | `03-deploy.yml:168` vs `:179` |

A YAML description asserting approval protection is not enforcement. **D-4 must not be treated as
safe on the basis of its default value or its description text.**

---

## 7. Lane E — end-to-end image provenance

### 7.1 Nothing in this repository deploys by digest

`@sha256:` appears in **no production file.** A digest is resolved in two places and discarded in
both.

| Link | Evidence | Behaviour |
|---|---|---|
| Build pushes a mutable alias **pre-gate** | `02-build.yml:72` builds `-t …:$IMAGE_TAG -t …:latest`; `:75`/`:76` push both. Job `build-push` has **no `needs:`**; triggers are `workflow_call` + `push: [main, master]` | `:latest` advances on every push to `main` before any scan |
| **Digest substituted by a tag** | `:78` `DIGEST=$(aws ecr describe-images … 2>/dev/null \|\| echo "$IMAGE_TAG")`, published as `image_digest` at `:81` and exported at `:10-12` | **fail-open**: a tag can masquerade as a digest |
| That output has no consumer | 0 `uses: ./.github/workflows` matches repo-wide; `03-deploy.yml` chains by `workflow_run`, which carries no job outputs | the corrupted value is never read, so the fail-open has no symptom |
| Deploy resolves a digest, then drops it | `03-deploy.yml:162-174` — correctly **fail-closed** if no image exists for the SHA — but `$DIGEST` is step-local, never written to `$GITHUB_OUTPUT` | proven to exist, then discarded |
| Deploy consumes a **tag** | `:179` `IMAGE_TAG: ${{ …head_sha \|\| github.sha }}` → `ecs_deploy_and_diagnose.py:901` `image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"` | scanned artifact ≠ provably deployed artifact |
| **Two scripts default to a mutable tag** | `ecs_deploy_and_diagnose.py:94` `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")`; **`scripts/deploy.sh:9` `IMAGE_TAG=${2:-latest}`** | unset/empty/typo'd → deploys `:latest` |
| Deploy validates config but **not the image** | `validate_critical_config()` `:169-224` checks `REDIS_URL`, 7 secrets, port 8000, cpu/memory — **never `container["image"]`**; the image is written in afterwards at `:258-263` | the script cannot confirm it deploys what was validated |
| **No ECR immutability declared** | `aws_ecr_repository` / `image_tag_mutability`: **0 matches in every `.tf` file** | ECR defaults to `MUTABLE`; a SHA tag is not a provable anchor. `03-deploy.yml:162`'s step name says "Immutable" |

All five prior findings the brief asked about **still hold**, and two are extended: there is a
**second** mutable-default script (`deploy.sh:9`), and the digest fail-open is in the *build*, while
the *deploy*'s digest check is fail-closed but discards its result.

### 7.2 Terraform consumers — 6 source refs, 8 concrete task definitions

| # | file:line | image | task definition → service |
|---|---|---|---|
| 1 | `terraform/ecs.tf:28` | `vyomquant-api:latest` | `api` → `vyomquant-api-service-*`, desired 2 |
| 2 | `:126` | `vyomquant-tee:latest` | `tee` → desired 1 |
| 3 | `:198` | `vyomquant-mds:latest` | `mds` → desired 1 |
| 4 | `:284` | `vyomquant-websocket:latest` | `websocket` → desired 1 |
| 5 | `:378` | `vyomquant-simulator:latest` | `simulator` → desired 0 |
| 6 | `:478` | `vyomquant-api:latest` *"Workers use the backend image"* | `workers`, **`for_each` over 3** (`backtest`, `dag`, `command`, `:456-462`) → 3 services, desired 1 each |

**6 → 8 at apply time.** The three worker services are **entirely outside the CI deploy path**:
`ecs_deploy_and_diagnose.py` touches only `ECS_SERVICE` (`:91`) and rewrites only the container
named `vyomquant-api`/`api` (`:261`). Nothing advances the workers' image; they pick up whatever
`:latest` resolves to at restart. Caveat: these six lack the ECR registry prefix (`# ECR URL goes
here`), so whether this root created the live services is **unresolved from source** — it is still
class (b) because applying it would retarget live services.

Additional fully-qualified mutable-tag consumers at repo root:
`ecs-task-definition-full.json:18` and `:211`, `td22.json:7`, `new_task_def.json:194`.
`new_task_def.json:9` pins a SHA-named tag — still a tag, not a digest.

**Totals: 14 references in executable paths, 10 in infrastructure.**

### 7.3 Fail-closed immutable-image contract — designed, not implemented

| # | Requirement | Where it binds | Test to write |
|---|---|---|---|
| 1 | Missing/empty/whitespace image identity refuses | `ecs_deploy_and_diagnose.py:94`, `deploy.sh:9` | unset, `""`, `"  "`, `"latest"` → non-zero exit |
| 2 | Resolution yields a verifiable digest | `02-build.yml:78` — remove `2>/dev/null` and `\|\| echo` | API error, empty result, `None`, auth failure → refuse |
| 3 | Same digest validated and deployed | carry digest build → gate → deploy; compare before `register-task-definition` | mismatched digest → refuse |
| 4 | All gated services follow it, **including the 3 workers**, or carry a justified exception | `terraform/ecs.tf` ×6 | assert no `image` attribute ends in `:latest` |
| 5 | An ungated build cannot change a gated service's artifact | stop promoting `:latest` pre-gate (`02-build.yml:72,76`) | `latest` updated between validation and deploy → refuse |
| 6 | Terraform cannot silently restore a mutable reference | `image_tag_mutability` + the ×6 pins | `terraform plan` assertion across all 8 definitions |
| 7 | Rollback targets an approved immutable artifact | `rollback_to_task_def` | rollback resolves to an approved digest, never `:latest` |

### 7.4 Existing tests describe this contract and do not enforce it

`tests/test_cicd_deployment_integrity.py:70` `test_deployment_identity_verification` and `:138`
`test_ecr_image_artifact_verification` assert exactly the SHA-to-digest binding production lacks —
but both define local stub classes (`DeploymentIdentity`, `ECRImageVerifier`) inside the test
bodies and import nothing from `scripts/`, `terraform/`, or `.github/`.
`tests/test_ecs_deploy_rollback.py` is the only test importing the real script and it **mocks
`deploy_new_image` away**, asserting nothing about the image URI.

**Executed this phase:**

```
pytest tests/test_security_gate_blocks_deploy.py tests/test_cicd_deployment_integrity.py -q
→ 20 passed in 1.96s   EXIT 0
```

**20 green tests, zero provenance protection.** The gap is covered by tests that look like they
cover it.

---

## 8. Lane F — Bandit and IaC scan consistency

### 8.1 Bandit

Scope mismatch **unchanged and re-confirmed by blob identity**:
`tests/test_bandit_sast_gate_is_green.py` (`a7deff99c9e5`, clean) sets
`GATED_MODULE = backend_app/backend/transactional_execution_manager.py` — one file — while CI runs
`bandit -r backend_app -ll` (`05-security.yml:37`), the whole tree.

Prior measurements are **reused, not re-run**, because every input is byte-identical: whole tree →
**exit 1, 9 findings, 9 MEDIUM, all B608**; the single gated module → **exit 0, 0 findings**; the
gate's own suite → **3 passed, exit 0**; an isolated negative control → **exit 1, 1 × B608**
detected (Phase 24 §4.2), so a widened gate would be a real regression detector. Scanner identity
bandit 1.9.4 / Python 3.12.10 matches the local toolchain — but note C-4: CI installs `safety` and
`bandit` **unpinned**, so this match is not guaranteed for future runs.

Per-site review, all nine: `_validate_table_name` matches `^[a-zA-Z0-9_]{1,64}$`, **raises** on
mismatch and never sanitises; `UUID_REGEX` is bound as a parameter (`{"re": UUID_REGEX}`). Sites:
`d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py:193,198,209,467,475` ·
`b2c3d4e5f6a7_positions_canonical_shape.py:224,262` ·
`a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py:212,222`. **9 of 9 safe constructions · 9 of 9 still
legitimate scanner findings · 0 suppressed · 0 owner-accepted.** Safe-on-review is not gate-passed.

**New context (§5.3):** all three files are **absent at the parent commit**. The Bandit failure is
debt introduced by the released commit, and its owner is that commit's author.

### 8.2 Trivy IaC

**BLOCKED — `trivy` and `docker` both absent. No scan was run and no result is claimed.** The five
CRITICALs are read from CI output (`staging/p14_sec_failed.log`).

| Rule | Location | Disposition |
|---|---|---|
| `AWS-0054` | `terraform/alb.tf` | Compensating control present: `:80→:443 HTTP_301` under `enable_custom_domain`, plus a `lifecycle.precondition` refusing a production apply. Trivy evaluates neither `dynamic` blocks nor preconditions. **Exploitability in prod: none established. Not owner-accepted, so still blocking.** `alb.tf` was modified **+25** by `ecd23a9e`, so this disposition should be re-derived against the current file before acceptance. |
| `AWS-0104` ×2 | `security_groups.tf` (alb, ecs_tasks) | Load-bearing egress — ECR, Secrets Manager, Supabase, venues, monitoring. Accept-and-document. |
| `AWS-0104` ×2 | `security_groups.tf` (redis), `redis-only/main.tf` (redis) | **Candidates only.** Redis cluster mode, replication topology, failover and backup destinations were **not** inspected. **No egress rule is asserted unnecessary, and none should be removed on a static assumption.** |

Tightening both Redis rules would still leave three blocking findings. All four files **existed at
the parent** — this is pre-existing infrastructure debt newly surfaced (§5.3).

---

## 9. Lanes G–J — independent critical investigations

### 9.1 G — D7-1 vault callers: re-verified at current source

Signature, `backend_app/backend/api_key_vault.py:177`:
`def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:` — **two parameters, no
`access_token`.**

Full call-site census (`git grep`, all of `backend_app/`):

| Call site | Form | Verdict |
|---|---|---|
| `routers/orders.py:789` | `(user["id"], exchange_id, access_token=user.get("access_token"))` | **INVALID** — unguarded → `TypeError` → **500** on `GET /api/orders/open` |
| `routers/orders.py:1095` | same | **INVALID** — inside `try`, `except Exception` → `return []` (silent empty history) |
| `routers/market.py:50` | same | **INVALID** — inside `try`, `except Exception` → **public `ConnectionEngine` fallback**; the `isinstance` guard at `:55-56` is **unreachable** because the `TypeError` fires on the call |
| `routers/exchange.py:993` | `(user["id"], body.exchange_id,)` | **VALID** |
| `api_ws/ws_routes.py:814`, `api_key_vault.py:299`, `master_executor.py:252`, `portfolio_cache_updater.py:160` | 2 positional args | **VALID** |

**Exactly 3 invalid sites.** This is **not** a repo-wide `access_token` removal — other APIs
legitimately accept that argument, and `tests/test_market_none_keys_fix.py`'s double declares
`access_token=None`, which is what hides the defect. **The keys-absent policy for market data is a
product/security decision and was not invented here.** No key material was read, logged or printed.

### 9.2 H — D7-2 portfolio cache updater

`backend_app/backend/portfolio_cache_updater.py:151`
`from backend_app.backend.key_manager import KeyVault`; `:160`
`keys = vault.load_decrypted_keys(user_id, exchange_id)` — **arity already correct.** No
`key_manager` module and no `class KeyVault` exists anywhere; this is the sole repo-wide reference.
The import is inside the per-user path, so `main.py:466` still publishes
`service_status["portfolio_cache_updater"] = "ACTIVE"` — **the worker fails every cycle for every
user while health reports ACTIVE.** Confirmed false-green.

A sibling session (`PHASE22 §8.1`) adds a dependency I had not recorded and which I have not
independently verified: the correct fix reuses the initialized singleton, but if that singleton is a
`MockEngine` in the deployed environment, wiring to it converts a logged `ImportError` into a silent
`TypeError` on `keys["api_key"]`. **Required evidence: redacted platform boot logs showing the
deployed vault object's type.** Until then the minimal import fix must stay separate from the
health-reporting redesign. **No second vault was constructed and no import was redirected.**

### 9.3 I — tenant isolation and database

`FORCE ROW LEVEL SECURITY` re-swept across tracked files excluding `staging/`: **20 occurrences,
zero executable SQL.** Every hit is prose, a docstring, a markdown document, a verifier script's
explanatory text, a test comment, or — in the one migration
(`backend_app/migrations/026_fee_records_row_level_security.sql:75`) — a **SQL comment**. Also
`backend_app/core/tenant_db_context.py:13` is a docstring.

Nine RLS policies key on `app.current_tenant_id`; `tenant_db_context.py` is correct in form
(`set_config(..., is_local=true)` inside an explicit transaction, the only pooler-safe shape under
`pool_mode=transaction`) and **has no call sites**. The live instance was previously recorded as
`relrowsecurity=true, relforcerowsecurity=false`, and the application connects as the table owner,
which is exempt from non-forced policies — so the policies are **inert on the ORM path**.

**`FORCE ROW LEVEL SECURITY` was not enabled, proposed as an isolated change, or prepared in any
form.** Enabling it before tenant context is wired would make legitimate reads return zero rows.
Owner/superuser tests would not constitute proof. **PostgreSQL unavailable → runtime verification
BLOCKED. NOT CERTIFIED.**

### 9.4 J — Redis, quotas, workers

Redis unreachable (6379 closed); `REDIS_URL`, `AERORA_TEST_REDIS_URL`, `REDIS_HOST`, `REDIS_PORT`
all unset; Docker absent. The four QUOTA-01 suites exist and were **not executed**; **no skip is
counted as a pass** and no atomicity claim is made from in-process tests. `redis_manager.py` is
unchanged at `d7b0e811b615` and remains **UNOWNED** per `A10:244,:367`; `A23E6` is **0/12 ticked**.
Strict xfail contracts preserved.

Workers started in `main.py`: `ConsistencyChecker` (`:415`), `OrderWatchdog` (`:432`),
`ReconciliationScheduler` (`:456`), `PortfolioCacheUpdater` (`:466`). **Not started:**
`ExchangeReconciliationService`, `MarketplaceExpiryWorker` / `expiry_sweep` — 0 references in
`main.py`. `ExchangeReconciliationService` is **not** `ReconciliationScheduler`. The W-4
`UNKNOWN`-exit policy is **undefined and was not invented**; W-5 not begun; no retry semantics
introduced.

### 9.5 W-6 (carried)

`_handle_ccxt_error` (`exchange_executor.py:1225-1257`) orders symbol/market (`:1246`) **before**
authentication/apikey/key (`:1252`), and matches the literal substrings `symbol`/`market`, which
ccxt places in URLs as `?symbol=`. Repository-wide the only production read of
`ExchangeError.retryable` is the log line at `:1687`, so a classification fix **cannot** alter retry
behaviour and none is introduced. Confirming tests are gated behind
`AERORA_EXCHANGE_SANDBOX_CERT` plus live credentials — unavailable.

---

## 10. Executed, blocked, and skipped — kept separate

### 10.1 Executed this phase

| Check | Result |
|---|---|
| `pytest staging/phase18_test_release_gate.py -q` | **28 passed**, 3.75 s, **exit 0** |
| `pytest staging/phase17_test_frontend_release_gate.py -q` | **42 passed**, 2.56 s, **exit 0** |
| `pytest tests/test_security_gate_blocks_deploy.py tests/test_cicd_deployment_integrity.py -q` | **20 passed**, 1.96 s, **exit 0** |
| `gh api …/runs?head_sha=ecd23a9e` | exit 0 — 6 runs, timeline §3.2 |
| `gh api …/workflows/06-frontend-deploy.yml/runs` | exit 0 — `total_count=60`, newest is `38129699497` |
| `gh api …/runs?per_page=30` | exit 0 — no repository activity after 10:38:58Z |
| `git diff --stat 074b772a ecd23a9e -- <workflow+tf set>` | exit 0 — §5.3 |
| `git show 074b772a:…/05-security.yml` → YAML parse | exit 0 — **parent jobs lack `iac-scan`** |
| `git ls-tree -r 074b772a -- backend_app/alembic/versions` | exit 0 — 17 files, all 3 finding-bearing migrations **absent** |
| Requirements pin/lock analysis | exit 0 — 64/64 pinned, 0 hashes, 0 lockfiles |
| `git grep "FORCE ROW LEVEL SECURITY"` (excl. `staging/`) | exit 0 — 20 hits, **0 executable SQL** |
| `load_decrypted_keys` call-site census + 4 targeted reads | exit 0 — 3 invalid, confirmed at source |
| Workflow trigger/job/needs/permissions/environment parse ×5 | exit 0 — §3.5, §4.4, §6.2 |
| `pytest.ini` + prototype collection analysis | exit 0 — **two** independent non-collection reasons |

### 10.2 Reused without re-running (inputs byte-identical)

Bandit whole-tree (exit 1, 9 × B608), single-module (exit 0), gate suite (3 passed), the isolated
Bandit negative control, and the W-6 classification matrix. Justified by unchanged blob hashes
(§1.3) and unchanged tool version.

### 10.3 Blocked / not run — none counted as a pass

| Check | Reason |
|---|---|
| **Dependency audit (`safety`)** | **tool ABSENT locally; `pip-audit` also absent.** Would not have been conclusive anyway — §5.1 |
| Trivy IaC, container scan | `trivy` and `docker` absent |
| `terraform validate` / `plan` | `terraform` absent |
| Tenant-isolation runtime matrix | PostgreSQL closed |
| Real-Redis quota / debounce suites | Redis closed |
| W-6 sandbox tests | `AERORA_EXCHANGE_SANDBOX_CERT` + venue credentials absent |
| `01 PR Check` at `ecd23a9e` | `on: [pull_request]` only — undispatchable, and dispatch is unauthorized |
| Published-bundle contents | requires production S3/CloudFront access |

**No production behaviour is claimed from any synthetic fixture.** The three suites in §10.1 are
mock/stub-based: they prove logic, not release configuration. **No security assurance is claimed
from `05 Security`, which skipped mandatory steps at this SHA.**

---

## 11. Exact file-level grants required for the next step

Each entry names the complete file set and the first verifiable action.

| # | Grant request | Files | First action |
|---|---|---|---|
| **1** | **Dependency audit must be able to fail** | `.github/workflows/05-security.yml` | Delete `\|\| echo "Safety advisory logged"` (`:39`); add `if: always()` to `Run Safety Check` and `Upload Security Reports`; pin `bandit==`/`safety==` at `:30`. **Smallest change, largest assurance gain in this report.** |
| **2** | **Digest fail-open** | `.github/workflows/02-build.yml` | Remove `2>/dev/null` and `\|\| echo "$IMAGE_TAG"` at `:78`; fail closed |
| **3** | **Frontend gate** | `06-frontend-deploy.yml`, `scripts/require_security_scan.py`, new `tests/test_frontend_deploy_is_gated.py` | Merge `phase18_release_gate.py`'s job-level + pagination logic into the production verifier; add a gate job; `needs:` on `deploy-frontend`; `permissions: actions: read`; pin `ref:` to the validated SHA |
| **4** | **Collect the verifier's tests** | move `staging/phase18_test_release_gate.py` → `tests/test_release_gate.py` | Both the filename pattern **and** `testpaths` must be satisfied (§4.2) |
| **5** | **Mutable-tag defaults** | `scripts/ecs_deploy_and_diagnose.py`, `scripts/deploy.sh` | Make image identity required; reject empty/`latest` |
| **6** | **Terraform image pins** | `terraform/ecs.tf` (6 refs → 8 definitions) | Pin to an immutable identity; add `image_tag_mutability`; decide the 3 worker services explicitly |
| **7** | **Bandit parity** | `tests/test_bandit_sast_gate_is_green.py` + the 3 migrations | Widen the gate test to CI's scope **first**, then 9 per-site `# nosec B608` justifications |
| **8** | **Verifier hardening** | `scripts/require_security_scan.py`, `03-deploy.yml` | D-1 … D-5: job-level, pagination, required-workflow list, `environment:` for `allow_unscanned`, explicit `permissions:` |
| **9** | **D7-1** | `routers/orders.py`, `routers/market.py`, `tests/test_market_none_keys_fix.py` | Remove `access_token=` at **exactly 3 sites**; keep the 9 legitimate callers. Requires the keys-absent policy decision |
| **10** | **D7-2** | `backend_app/backend/portfolio_cache_updater.py` | Import fix only, after the boot-log evidence in §9.2 |
| **11** | W-6 | `backend_app/backend/exchange_executor.py` | Reorder the classification ladder; must precede W-3 |
| **12** | Tenant / Redis / Trivy | per §8.2, §9.3, §9.4 | Grants **plus** environments: disposable PostgreSQL, disposable Redis, Trivy + Docker |

---

## 12. Owner decisions required for incident containment

| # | Decision | Owner | Minimum input |
|---|---|---|---|
| **1** | **Suspend frontend publication** (§3.4 option 1) | release owner | Yes/no. No production mutation; stops recurrence immediately |
| **2** | **Was the `ecd23a9e` frontend publish intended, and is rollback required?** | release owner | Yes/no + target. **Read the §3.4 rollback trap first** — `074b772a` was green partly because it had no IaC scanner |
| **3** | **Verify in place** (§3.4 option 2) | release owner + infra | Supply the published bundle or its dependency manifest. Converts the only `Unknown` in §3.1 into evidence |
| **4** | ECR `imageTagMutability` for `vyomquant-api` | infrastructure owner | One value. Determines whether a SHA tag is a valid provenance anchor at all |
| **5** | Redis topology: is either `AWS-0104` egress rule removable? | infrastructure owner | Cluster mode, replication, failover, backup destinations |
| **6** | `01 PR Check` is failing on all three recent PRs | code owner | Triage. Blocks the branch-protection correctness route (§4.4) |
| **7** | Keys-absent policy for market data | product + security owner | Should an authenticated user with no keys get anonymous market data, or an error? |
| **8** | Deployed vault object type | platform owner | 4 redacted boot fields × 2 environments (§9.2) |

---

## 13. Release disposition and prioritized next actions

| Item | Status |
|---|---|
| **Frontend publication** | **BLOCKED** — no exact-SHA mandatory security or correctness evidence is enforced; `needs:` count is 0 |
| **Already-live frontend incident** | **OWNER CONTAINMENT DECISION REQUIRED** — `ecd23a9e` assets published 09:50:13Z, **nothing superseded them**, no rollback or mitigation evidenced. Exposure **Unknown, not disproven** |
| **Dependency security evidence for `ecd23a9e`** | **DID NOT EXECUTE** — step skipped behind the Bandit failure. And per §5.1 it **could not have failed even if it had run.** PyJWT is pinned at `2.14.0`; no lockfile, no hashes, no audit result |
| **Backend deployment** | **BLOCKED** — `05 Security` red; the gate correctly refused (`38130273012`, ECS `skipped`). Additionally: **no correctness evidence is required**, and image provenance is not established |
| **Image provenance** | **NOT ESTABLISHED.** Remaining mutable-tag consumers: **14 executable + 10 infrastructure**, including 6 Terraform refs → **8 task definitions** (3 worker services outside the deploy path entirely) and **2 scripts defaulting to `latest`**. **The deployed digest is not verifiably the approved digest** — `03-deploy.yml:168` resolves it and discards it; no `image_tag_mutability` exists anywhere |
| **Tenant isolation** | **NOT CERTIFIED** — policies inert on the ORM path; `FORCE ROW LEVEL SECURITY` has 0 executable statements; no runtime evidence |
| **PostgreSQL** | **UNVERIFIED** — service unavailable |
| **Real Redis** | **UNVERIFIED** — service unavailable; skips not counted as passes |
| **Live trading** | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

### Prioritized next-action sequence

1. **Containment decisions 1 and 3** (§12) — the only items addressing assets already serving
   traffic. Decision 1 mutates nothing.
2. **Grant 1** (§11) — one deletion makes the dependency audit capable of failing. Highest
   assurance per character changed in this report.
3. **Grant 2** — one line closes the digest fail-open.
4. **Grants 3 + 4** — wire the verifier that already passes 28 tests, and put its tests where pytest
   will collect them.
5. **Grant 7** — Bandit parity; the reproduction, the negative control and the per-site review are
   all complete and waiting.
6. **Grants 5, 6, 8** — image-identity contract and verifier hardening.
7. **Grants 9–11** — D7-1, D7-2, W-6; each has measured evidence and a written patch scope.
8. **Grant 12 plus environments** — tenant, Redis and Trivy lanes cannot progress on source alone.

---

## 14. Prohibited actions — confirmed not performed

- No exploitation, compromise or secret leakage claimed; none evidenced (§3.1).
- **`FORCE ROW LEVEL SECURITY` not enabled, not prepared, not proposed as an isolated change.**
- No rollback, S3 mutation, CloudFront invalidation, workflow disablement, branch-protection change,
  Terraform apply, ECR retag, deployment or publication.
- No commit, stage, push, reset, revert, merge, rebase, history rewrite, branch switch, clean, or
  stash apply/drop/pop — **2 stashes at entry, 2 at exit.**
- No CI dispatch, no re-run, no PR. All `gh` calls were read-only `GET`s.
- No finding silenced: 0 suppressions, 0 severity changes, 0 exclusions, 0 baselines, 0 `|| true`,
  0 `# nosec` added. All 9 Bandit and all 5 Trivy findings reported.
- No skipped, blocked or unexecuted check reported as a pass.
- No `KeyVault` invented, no import redirected, no second vault constructed.
- No egress rule removed on a static assumption; no Redis rule asserted unnecessary.
- No credential, token, DSN or key material read or printed. `staging/pg_dsn.txt` not opened.
- No concurrent artifact overwritten; destination verified absent; the two tracked ` M` files and
  all sibling `staging/` artifacts byte-identical.

### Files written this phase

`staging/PHASE25_RELEASE_ASSURANCE_AND_INCIDENT_CONTAINMENT.md` (this report) ·
`staging/phase25_frontend_runs.json` · `staging/phase25_all_runs.json` — all new, all untracked,
all under `staging/`. **No governed file touched, so there is no diff to review.**

---

*Phase 25 closed. `HEAD` and tree unchanged at entry and exit. 0 of 10 lanes authorized; 0 files
changed; 3 test suites executed green; 5 new findings delivered, 2 of which correct prior reports.*

***DO NOT RELEASE · FRONTEND PUBLICATION BLOCKED · OWNER CONTAINMENT DECISION REQUIRED ·
BACKEND DEPLOYMENT BLOCKED · TENANT ISOLATION NOT CERTIFIED · LIVE TRADING OFF / NOT CERTIFIED***
