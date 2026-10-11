# Phase 37B — Verifier Identity Contract (P18-3 Demonstrated), Release-Gate Recovery, Digest Provenance, and Security Closure

**Destination selection.** `staging/PHASE37_CONTINUOUS_RELEASE_RECOVERY_AND_ENGINEERING_CLOSURE.md`
**already existed** (30 601 B, a concurrent session's artifact) and was **preserved, not
overwritten**. Per §13 this report uses the unique path above, verified absent before writing.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 tests ·
0 governance records changed. 1 artifact written (this file).** No stage, commit, push, stash
operation, worktree mutation, CI dispatch, PR, deploy, publish, rollback, ECR/S3/CloudFront
mutation, task-definition registration, Terraform apply, database change, `kubectl`, or trade. No
secret, token, DSN or credential read or printed.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Executive outcome

**P18-3 is no longer an inference. It is demonstrated by execution**, using a probe built from the
**real workflow YAML** rather than from the verifier's own policy — exactly the method §3.1
requires.

A controlled A/B against `staging/phase18_release_gate.py`, holding runs, conclusions, SHA and
branch constant and varying **only** how jobs are named:

| Fake API built from | `gate.verify()` result |
|---|---|
| configured IDs — *what the existing fixtures do* | **ADMITTED** `[('05 Security', 1000, 3), ('01 PR Check', 1001, 6)]` |
| **real YAML `name:` fields** | **REFUSED** — *"2 of 2 required workflow(s) did not provide admissible evidence"* |

Verbatim refusal (truncated to two entries):

```
2 of 2 required workflow(s) did not provide admissible evidence for aaaaaaaa:
  - Run 1000 of '05 Security' contains no job named 'sast-scan'. The run concluded success, but
    the mandatory job is absent, so the check it represents did not happen. Refusing.
    (jobs present: ['Bandit SAST & Dependency Security Scan',
                    'Trivy Container Vulnerability Scanner',
                    'Trivy IaC Misconfiguration & Secret Scan'])
  - Run 1001 of '01 PR Check' contains no job named 'static-audit'. … Refusing.
    (jobs present: ['Code Quality & Static Audit', 'Database Tests (PostgreSQL)',
                    'Docker Build Validation', 'Execution Tests (production posture)',
                    'Frontend Build & Unit Tests', 'Unit & Integration Tests'])
```

Two things make this decisive. *"The run concluded success"* confirms the runs passed — only
**identity resolution** failed. And the verifier's own diagnostic **prints the real names it found**
beside the ID it was looking for, so the mismatch is self-evident in the refusal text.

**CORRECTED count.** Phase 36 recorded 7 mandatory + 2 advisory. The real mandatory set is **9**
— `05 Security` 3, `01 PR Check` **6** (including `docker-check` and `frontend-tests`, which Phase 36
omitted). **All 9 mismatch.** `format-check` and `provisioning-and-isolation` are the advisory two.

| Workflow | Configured ID | Real API `name` |
|---|---|---|
| `05 Security` | `sast-scan` | `Bandit SAST & Dependency Security Scan` |
| `05 Security` | `container-security` | `Trivy Container Vulnerability Scanner` |
| `05 Security` | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` |
| `01 PR Check` | `static-audit` | `Code Quality & Static Audit` |
| `01 PR Check` | `unit-tests` | `Unit & Integration Tests` |
| `01 PR Check` | `paper-mode-tests` | `Execution Tests (production posture)` |
| `01 PR Check` | `database-tests` | `Database Tests (PostgreSQL)` |
| `01 PR Check` | `docker-check` | `Docker Build Validation` |
| `01 PR Check` | `frontend-tests` | `Frontend Build & Unit Tests` |

**0 of 9 identical.** The verifier fails closed — but unconditionally, so it is unusable as written.

---

## 2. Entry / exit integrity

| Measurement | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **identical** |
| upstream divergence | 0 ahead / 0 behind | identical |
| staged / unmerged | **0 / 0** | 0 / 0 |
| stashes | **2 — identified, not applied, popped or dropped** | 2 |
| worktrees | 1 (main only) | 1 |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical** |
| porcelain | 165 | 166 (+1 = this report) |
| concurrent modification | `tests/test_drift_classification.py` ` M` | **untouched** |

**Preserved, not overwritten:** `PHASE37_CONTINUOUS_RELEASE_RECOVERY_AND_ENGINEERING_CLOSURE.md`
(30 601 B), `PHASE36_…` and all earlier reports, both `staging/` gate prototypes, every probe and
log. Re-verified: **no workflow, script, Terraform file, migration, router, test or governance
record is modified anywhere in the tree** — no session has implemented any lane.

**Probe hygiene.** The demonstration ran from `%TEMP%\p37_probe.py`, **outside the repository**, and
both temp files were deleted afterwards. No repo file was added beyond this report.

**Tools:** `python` 3.12.10 · `pytest` · `bandit` 1.9.4 · **PyYAML 6.0.3** · `gh` · `node`/`npm` —
present. **`docker` ✘ · `trivy` ✘ · `terraform` ✘.** PostgreSQL `:5432` and Redis `:6379` closed.

**Authorization.** Rechecked this phase: ledger `:800-804` → `AP-W1D-1` … `AP-W1D-5` **all NOT
GRANTED**; `A23E6` → **0 ticked / 12 unticked**; no `CODEOWNERS` / `OWNERS` / `MAINTAINERS`. Only
live grant **`AP-W1D-3`** (four drift-classifier files) — **none a Phase 37B target.** Per §1.1
neither this prompt nor any prior recommendation is a grant.

---

## 3. P18-1 and P18-3 — diagnosis and repair status

### 3.1 Both defects, both confirmed

| ID | Defect | Status |
|---|---|---|
| **P18-1** | `01-pr-check.yml:4-5` is `on: pull_request:` **only**; `02-build.yml:14` is `on: push`; `03-deploy.yml` runs on `workflow_run` of the build. No `01 PR Check` run can exist for a push SHA, so the gate refuses | **CONFIRMED** (source) |
| **P18-3** | Policy uses job IDs; API returns display names; 0/9 match | **CONFIRMED BY EXECUTION** (§1) |
| **P18-2** | Pagination **refuses** when a listing fills a 100-item page rather than fetching further pages — fail-closed but brittle | **CONFIRMED** (source) |

The prototype's docstring states the correct rule — *"Job names must match the API's `name`, i.e.
the `name:` field if present, otherwise the job id"* — and the policy table then contradicts it. A
spec/implementation divergence, not a misunderstanding.

### 3.2 Why the 28-test suite cannot see it

`CURRENT SOURCE` `phase18_test_release_gate.py:49-63`:

```python
def _job(name, conclusion="success", status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}

def _all_jobs_pass():
    for i, (workflow, mandatory) in enumerate(gate.REQUIRED_WORKFLOWS.items()):
        jobs[1000 + i] = [_job(n) for n in mandatory]      # API "name" := configured ID
```

The fake API is derived **from the policy under test**, so the mapping is never exercised. `:229`
asserts `"sast-scan" in message`, encoding the wrong contract. The advisory test at `:168`
(`_job("format-check", "failure")` must not gate) is vacuous for the same reason — a job absent from
the mandatory tuple is ignored whatever its name.

**So the 70 passing staging tests are not evidence the gate works.** My §1 probe is the
counter-example: identical inputs, real names, unconditional refusal.

### 3.3 Repair — NOT IMPLEMENTED (unauthorized), specification recorded

The minimum robust repair, per §3.3, requires resolving expected identity **from the workflow YAML
at the candidate SHA** (not from HEAD, and not from the policy):

1. Fetch each required workflow's YAML **at the candidate SHA**; fail closed if it cannot be
   retrieved or authenticated.
2. Build `job_id → expected API name` as `spec.get("name", job_id)`.
3. Match API jobs on that expected name; refuse duplicate, ambiguous, unexpected, missing or
   renamed mandatory jobs.
4. **Distinguish identity-resolution failure from a genuinely failed job** — today both surface as
   "contains no job named …".
5. Preserve exact-SHA binding, deterministic run selection, branch allow-list, bounded waiting,
   API-error refusal, empty-policy refusal, and no override.
6. Keep `ADVISORY_JOBS` explicit and name-resolved, so a newly added job cannot silently become
   mandatory *or* advisory.

Per §3.4 the fixtures must be replaced by real-workflow contract tests that load the YAML and assert
the mapping — **renaming the fixtures to the new expected strings would reproduce the same masking.**
Per §3.5 promotion into `scripts/`/`tests/` remains a separate governed change, and `pytest.ini` must
**not** be broadened to include `staging/` (188 reports, probes, generated evidence).

**P18-1 and P18-3 must be fixed together.** Fixing identity alone leaves the trigger deadlock; fixing
the trigger alone leaves a verifier that refuses every successful run.

---

## 4. Candidate-SHA correctness architecture — owner decision

`CURRENT SOURCE` event-to-SHA matrix:

| Event | Workflow | SHA the run is recorded against | Usable as release evidence? |
|---|---|---|---|
| `pull_request` | `01 PR Check` | **PR head SHA** | **No** for a post-merge `main` SHA |
| `push` to `main` | `02 Build`, `05 Security` | the pushed SHA | yes, for those two |
| `workflow_run` | `03 Deploy` | `github.event.workflow_run.head_sha` | yes, if pinned (it is) |
| `workflow_dispatch` | `03 Deploy` | `github.sha` of the dispatched ref | yes, same-SHA verified |
| `workflow_call` | `02 Build` declares it | — | **no caller exists** |

| Option | Exact-SHA property | Cost / risk |
|---|---|---|
| **A — PR / merge-queue** | **Weak as-is** — PR-head evidence does not certify the merge commit | Needs merge queue or squash/fast-forward so head ≡ deploy SHA. Cheapest; subtlest to get right |
| **B — dedicated push-path workflow** | **Strong** | Must enumerate sufficient jobs without dropping coverage; two pipelines to keep consistent |
| **C — extend `01 PR Check` to `push`** | **Strong** | All jobs on every push, including **`provisioning-and-isolation`** (*"Provisioning, Privilege Hardening & Tenant Isolation"*, `:565-566`) which **provisions a database**; duplicate PR+push runs |
| **D — other** | — | none found |

**Not selected.** This changes release policy, required checks and infrastructure cost — an owner
decision per §4. No trigger was broadened; no permissive fallback added; no §3.4/§13 tests written,
because tests must encode an **approved** policy.

---

## 5. Image provenance — reconciled, unchanged

`REUSED EVIDENCE` at identical `HEAD`; classifications re-read, not regenerated.

| Link | Status |
|---|---|
| source SHA → build | present, **ungated** |
| build → push | pushes `:${{ github.sha }}` **and `:latest`** |
| push → digest | **LATENT/ORPHANED** — `02-build.yml:78` resolves and `:81` exports `image_digest`, but **no workflow calls `02-build.yml`** and `workflow_run` carries no job outputs, so nothing reads it. Its `\|\| echo "$IMAGE_TAG"` fallback is **not currently exploitable** — fix before wiring |
| digest → scan of that digest | **BROKEN** — `05 Security` scans a locally built `trading-platform:scan`, not the pushed ECR digest |
| scan → SHA+digest binding | **ABSENT** — `require_security_scan.py` verifies a SHA only |
| evidence → deploy input | **verified then discarded** — `03-deploy.yml:162-174` is genuinely **fail-closed** (`\|\| echo ""`, then `-z`/`= "None"` → `exit 1`); `$DIGEST` is step-local, never exported |
| deploy input → task definition | **BROKEN** — `ecs_deploy_and_diagnose.py:94` `os.getenv("IMAGE_TAG", "latest")`; `:901` builds `…:{IMAGE_TAG}` |
| Terraform declared state | **BROKEN** — `terraform/ecs.tf` six definitions on `:latest`, workers `:478` |
| root JSON | **BROKEN, reachable** — `ecs-task-definition-full.json:18` fully-qualified `:latest`; `:211` `questdb/questdb:latest`; `docs/history/ECS_DEPLOYMENT_GUIDE.md` documents `register-task-definition --cli-input-json` |
| Kubernetes | **BROKEN, status UNKNOWN** — §7 |
| ECR immutability | **UNKNOWN** — `image_tag_mutability` / `aws_ecr_repository`: 0 matches in any `.tf`. The tree does not establish the live setting; ECR's default is **not** proof |
| runtime image identity | **UNKNOWN** — needs owner/cloud evidence |

**Active exposure: deployment-by-tag against a registry of unestablished immutability, plus ~10
`latest` references.** The empty-digest refusal was preserved; no tag-for-digest substitution added.

---

## 6. Security workflow — unchanged, all CONFIRMED

1. **IaC reachability** — `iac-scan`'s two blocking Trivy steps run sequentially with **no
   `if: always()`**; `terraform/` fails, so the second root never runs. That root
   (`aerora_quant_platform/infrastructure/terraform/main.tf`, 178 lines) declares an **S3 backend**
   and an **`aws_secretsmanager_secret`**, and the step specifies `scanners: 'misconfig,secret'`.
   Zero findings there is indistinguishable from never having run.
2. **SAST evidence** — `sast-scan`'s upload lacks `if: always()` (unlike `iac-scan`'s), so the
   Bandit report is lost exactly when it matters.
3. **Safety, two independent defects** — sequenced after Bandit in the **same job** so it is
   **skipped** on Bandit failure; and `|| echo "Safety advisory logged"` means it **cannot fail**
   when it runs. **Fixing one is insufficient.** No authoritative severity policy located →
   **owner decision**, not invented.
4. **Scan target** — a local rebuild, not the pushed digest.

---

## 7. Deployment scripts and frontend

| Surface | Finding | Status |
|---|---|---|
| `scripts/deploy.sh` | `IMAGE_TAG=${2:-latest}`; `kubectl set image … backend=$IMAGE_TAG` passes a **bare tag** with no registry/repository; health-check loop `break`s on success but **cannot fail** after five failures, so a dead deploy prints *"Deployment complete!"*; unquoted `export $(grep … \| xargs)` | **CONFIRMED** (source). Operational status **UNKNOWN** — no workflow reference, which is not proof of disuse given documented manual runbooks. No `kubectl` run |
| `scripts/blue-green-deploy.sh` | refuses an empty tag; rollback/promotion branches **NOT RE-AUDITED** this phase | **NOT RUN** |
| `infra/deploy.sh` | appears only in a historical index | **UNKNOWN** |
| `06-frontend-deploy.yml` | `on: push` for `algo22-terminal/**`; **no `workflow_run`, no `needs:`**; publishes via 3 × `s3 sync --delete` + `/*` invalidation | **CONFIRMED** — no release-evidence gate |
| `tests/test_release_artifacts.py` | **8 passed** (Phase 30, source unchanged) — size floor and gate-before-sync ordering hold, and are **not** release authorization | **REUSED EVIDENCE** |
| published edge bytes for `ecd23a9e` | **UNKNOWN** — not provable from available evidence; no S3/CloudFront access | containment remains a production-owner decision |

---

## 8. Bandit, application, tenant, Redis, Terraform

| Lane | Status |
|---|---|
| **Bandit** | **REUSED EVIDENCE — not re-run.** Source hashes, `bandit` 1.9.4, config and command unchanged since the Phase 24 execution, so per §12 a rerun adds nothing: **9 × B608 MEDIUM / exit 1** tree-wide across three Alembic migrations vs **0 / exit 0** on the gate test's single module; negative control **PASS**; two stale `# nosec B608` in `backend_app/test_perf.py`. Parity fix **NOT AUTHORIZED** |
| **D7-1** | **CONFIRMED · NOT AUTHORIZED · policy-blocked.** Three `access_token=` callers vs `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` correct. Market route's broad `except` can turn a vault/auth failure into silent anonymous access. **Keys-absent policy not invented** |
| **D6-D / D7-2** | **CONFIRMED · evidence-blocked.** `portfolio_cache_updater.py:151` imports non-existent `backend_app.backend.key_manager`, **outside** the inner `try`, failing deterministically every cycle while health reports `ACTIVE`. No speculative class substituted; no `APIKeyVault()` built with guessed config. Needs **redacted platform-owner boot evidence** |
| **W-6 retries** | **CONFIRMED classification-only.** `except <Type>` → 0 matches; `error_code ==` → 0; `.retryable` read in exactly one log f-string; `replay_safe_transaction_guard` re-derives independently. **No retries wired to financial side effects; no UNKNOWN-exit policy invented** |
| **Workers** | `ExchangeReconciliationService` and `MarketplaceExpiryWorker`: **0 references in `main.py`** |
| **Tenant / PostgreSQL** | **BLOCKED · NOT CERTIFIED.** Service closed; no grant. Nine RLS policies on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` only in a SQL comment**, zero executable statements; `dependencies.py` has **0** tenant-context references; two GUC setters both off the request path; fallback DSN role owns the tables so policies are **inert** on the ORM path; `tenant_db_context.py` correct and **unwired**. **FORCE RLS not enabled in any form**; no matrix cell, assertion or anti-vacuity check altered |
| **Redis** | **BLOCKED** — closed. **No integration test run; skips are not passes.** Strict xfail preserved |
| **Terraform** | **BLOCKED** — `trivy`/`docker`/`terraform` absent: no scan, no `fmt -check`, no `validate`, no `plan`, **no result claimed**. No `init` against the S3 backend. **AWS-0054 false-positive analysis and the withdrawn VPC-collision finding both preserved, not resurrected** |

---

## 9. Commands and evidence

| Command | Class | Result |
|---|---|---|
| `git rev-parse` / `status --porcelain` / `diff --cached` / `stash list` / `reflog -1` | TESTED NOW | **PASS** — §2 |
| `python -c "import yaml"` | TESTED NOW | **PASS** — PyYAML 6.0.3 |
| `Test-Path` ×3 report destinations | TESTED NOW | **PASS** — `PHASE37_…` exists (preserved); `PHASE37B_…` absent |
| `python %TEMP%\p37_probe.py` | **TESTED NOW** | **P18-3 DEMONSTRATED** — ids ⇒ ADMITTED, real names ⇒ REFUSED (§1), exit 0 |
| `python %TEMP%\p37_probe2.py` | **TESTED NOW** | full refusal text captured (§1), exit 0 |
| Prior suites 28 / 42 / 8; Bandit 9×B608 | **REUSED EVIDENCE** | identical source hashes, tool version, command, configuration |

**Tests executed against repository targets: none** — no lane is authorized, so there was no change
to validate. The probe exercised a **staging prototype** read-only from a temp file; that is
staging-only evidence, **not CI coverage and not production verification.** Trivy / Docker /
Terraform / PostgreSQL / Redis: **BLOCKED**. CI dispatch: **NOT RUN**. No skipped, blocked or unrun
check is recorded as a pass; no synthetic-fixture success is presented as real-workflow validation;
no historical checksum is offered as proof of current deployed bytes.

---

## 10. Owner decisions and minimum grants

| # | Decision / grant | Owner | Why |
|---|---|---|---|
| **1** | Correctness-evidence design **A/B/C/D** (§4) | release owner | Prerequisite for the verifier; A must solve PR-head ≠ merge-SHA; C provisions a DB per push |
| **2** | Grant the verifier's **three-part** repair — P18-1, **P18-3 YAML-derived identity**, and real-workflow contract tests replacing the masking fixtures | release owner | §1 proves promotion today refuses everything |
| **3** | Bind the digest end to end — export `$DIGEST`, pass to the deploy helper, compare before `register-task-definition`; fix `02-build.yml:78` **before** wiring | release owner | The active provenance defect |
| **4** | Declare ECR `image_tag_mutability`, or supply the live setting | infrastructure owner | No tag is a provable anchor without it |
| **5** | Is the Kubernetes / blue-green path live? | platform owner | Decides whether §7's defects are live exposure |
| **6** | Safety: mandatory or advisory? | release owner | Both execution defects need fixing either way |
| **7** | Scan the pushed ECR digest, not a local rebuild | release owner | Closes the scan↔artifact link |
| **8** | Keys-absent market-data policy; vault boot evidence | product/security; platform owner | Blocks D7-1 / D7-2 |
| **9** | Frontend incident containment | production owner | Not performed or initiated |
| **10** | Disposable PostgreSQL + Redis; Trivy/Docker | CI / platform owners | Unblocks every certification lane |

---

## 11. Prohibited-operation checklist

Not performed: stage · commit · push · reset · stash/pop/drop · clean · revert · merge · rebase ·
branch switch · history rewrite · worktree mutation · CI dispatch · PR · repository-settings or
branch-protection change · asset publication · CloudFront invalidation · DNS change · ECR mutation ·
task-definition registration · deploy · rollback · Terraform apply/init · infrastructure
provisioning · production-database mutation · `kubectl` · exchange order · live-trading enablement ·
secret/token/DSN exposure. No threshold lowered, no suppression added, no `continue-on-error` or
`|| true` introduced, no fake digest, no skipped check reported as passing.

---

## 12. Status matrix

| Dimension | Status |
|---|---|
| Source modifications | **NOT AUTHORIZED** (0 files) |
| Release correctness evidence | **BLOCKED** — owner decision 1 |
| Verifier correctness | **FAIL — P18-3 demonstrated by execution**; P18-1 and P18-2 confirmed |
| Verifier test contract | **FAIL** — fixtures mask the defect; 28 passes are not evidence |
| Image provenance | **NOT ESTABLISHED** — 5 broken, 1 latent, 2 unknown |
| Security workflow completeness | **FAIL** — IaC root unreachable, SAST evidence lost, Safety skipped *and* non-blocking |
| Frontend publication safety | **FAIL** — no release-evidence gate; edge bytes **UNKNOWN** |
| Kubernetes deployment status | **UNKNOWN** — defects confirmed, operational status unresolved |
| ECS deployment status | **PARTIAL** — digest-existence check fail-closed; deploys by tag |
| Tenant isolation | **BLOCKED / NOT CERTIFIED** |
| Live trading certification | **NOT CERTIFIED** |
| Test coverage / execution | **PARTIAL** — 70 maintained tests outside CI; 28 validate a masked contract |
| Infrastructure validation | **BLOCKED** — no Trivy, Docker or Terraform |

**A staging-prototype probe is not CI coverage. A mocked pass is not deployment proof. A static RLS
review is not tenant-isolation certification.**
