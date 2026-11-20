# Phase 36 — Extended Continuous Repository Recovery, Release-Evidence Architecture, Artifact Provenance, Security Pipeline, Deployment Surface, and Runtime Certification

**Destination verified ABSENT before writing.** No existing report, prototype, test, or generated
artifact overwritten.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 tests ·
0 governance records changed. 1 artifact written (this file).** No stage, commit, push, stash
operation, worktree mutation, CI dispatch, PR, deploy, publish, rollback, cloud/ECR/S3/CloudFront
mutation, Terraform apply, database change, `kubectl` execution, or trade. No secret, token, DSN or
credential read or printed.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Executive outcome

**Nothing was implemented — no lane is authorized.** One new defect was found that invalidates the
"promote the verifier" recommendation carried by Phases 29–31, including my own.

**NEW — P18-3: the verifier's mandatory-job policy uses workflow job *IDs*, but the GitHub API
returns job *display names*. The mismatch is 100%, and its 28-test suite cannot detect it because
the fixtures synthesise the API name from the configured ID.**

`CURRENT SOURCE` `staging/phase18_release_gate.py` populates
`REQUIRED_WORKFLOWS` with YAML job IDs and then looks each one up against the API's `name` field in
`_assert_mandatory_jobs_passed()` (`by_name = {job["name"]: job}`). Every mandatory job in both
required workflows declares an explicit `name:`, so **none** of the configured identifiers can ever
match:

| Workflow | Configured value (job ID) | Actual API `name` | Match |
|---|---|---|---|
| `05 Security` | `sast-scan` | `Bandit SAST & Dependency Security Scan` (`05-security.yml:16-17`) | ✘ |
| `05 Security` | `container-security` | `Trivy Container Vulnerability Scanner` (`:47-48`) | ✘ |
| `05 Security` | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` (`:108-109`) | ✘ |
| `01 PR Check` | `static-audit` | `Code Quality & Static Audit` (`01-pr-check.yml:59-60`) | ✘ |
| `01 PR Check` | `unit-tests` | `Unit & Integration Tests` (`:123-124`) | ✘ |
| `01 PR Check` | `paper-mode-tests` | `Execution Tests (production posture)` (`:283-284`) | ✘ |
| `01 PR Check` | `database-tests` | `Database Tests (PostgreSQL)` (`:381-382`) | ✘ |
| advisory | `format-check` | `Format Backlog (report only)` (`:89-90`) | ✘ |
| advisory | `provisioning-and-isolation` | `Provisioning, Privilege Hardening & Tenant Isolation` (`:565-566`) | ✘ |

**0 of 9 identifiers match. Consequence:** even when `05 Security` runs on a push *and concludes
success*, the verifier reports all three mandatory jobs **absent** and refuses with *"contains no
job named 'sast-scan'"*. It fails in the safe direction, but it fails **always** — it is unusable
as written.

The prototype's own docstring states the correct rule — *"Job names must match the API's `name`,
i.e. the `name:` field if present, otherwise the job id"* — and the policy table then contradicts it.

**Why 28 tests pass anyway** (`CURRENT SOURCE`, `phase18_test_release_gate.py:49-63`):

```python
def _job(name, conclusion="success", status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}

def _all_jobs_pass():
    for i, (workflow, mandatory) in enumerate(gate.REQUIRED_WORKFLOWS.items()):
        jobs[1000 + i] = [_job(n) for n in mandatory]     # API "name" := configured ID
```

The fake API is built *from* the policy, so the identifier mapping is never exercised. `:229`
even asserts `"sast-scan" in message`, cementing the wrong contract. The advisory test at `:168`
(`_job("format-check", "failure")` must not gate) is likewise vacuous: a job absent from the
mandatory tuple is ignored regardless of its name.

**So the verifier has two independent deadlocks, not one:** P18-1 (`01 PR Check` is
`pull_request`-only, so no run exists for a push SHA) and P18-3 (job identity never matches). Both
must be fixed, with YAML-contract tests that parse the real workflows, before promotion is safe.
Promoting it today would block every deployment twice over.

---

## 2. Fresh preflight

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| `origin/main` divergence | **0 ahead / 0 behind** |
| staged / unmerged | **0 / 0** |
| stashes | **2 — identified, not applied, popped or dropped** |
| worktrees | **1** (main only) |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` — no commit, reset, checkout, branch switch |
| porcelain | 157 at entry → 158 at exit (+1 = this report) |
| Phase 36 destination | **ABSENT** before writing |

**Concurrent work preserved.** The only non-`staging/` modification is
`tests/test_drift_classification.py` (` M`, another session's) — **not touched**. Re-verified: **no
workflow, script, Terraform file, migration, router, test or governance record is modified anywhere
in the tree**, so no session has implemented any lane. `HEAD` is unchanged from Phase 31, so its
source findings remain applicable and were not regenerated wholesale.

**Tools:** `python` 3.12.10 ✔ · `pytest` ✔ · **`bandit` 1.9.4** ✔ · `gh` ✔ · `node`/`npm` ✔ ·
**`docker` ✘ · `trivy` ✘ · `terraform` ✘** · PostgreSQL `:5432` closed · Redis `:6379` closed.

**Authorization.** Ledger `:800-804` → `AP-W1D-1` … `AP-W1D-5` **all NOT GRANTED**; `A23E6` →
**0 ticked / 12 unticked**; no `CODEOWNERS` / `OWNERS` / `MAINTAINERS`. Only live grant:
**`AP-W1D-3`**, four drift-classifier files — **none a Phase 36 target.** Per §1.1 neither this
prompt nor any prior report is a grant.

---

## 3. Authorization matrix

| Lane | Governed targets | Grant | Classification |
|---|---|---|---|
| Correctness-evidence policy | `01-pr-check.yml` + required-check policy | none | **OWNER DECISION REQUIRED** |
| Verifier promotion/repair | `phase18_release_gate.py` + tests → `scripts/`, `tests/` | none | **NOT AUTHORIZED** (and policy-blocked) |
| Frontend gate | `06-frontend-deploy.yml` | none | **NOT AUTHORIZED** |
| Image provenance | `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, `ecs.tf`, `ecs-task-definition-full.json` | none | **NOT AUTHORIZED** |
| K8s / blue-green | `scripts/deploy.sh`, `blue-green-deploy.sh` | none | **NOT AUTHORIZED** + status UNKNOWN |
| Security workflow | `05-security.yml` | none | **NOT AUTHORIZED** + Safety policy undecided |
| Diagnostics | `require_security_scan.py` | none | **NOT AUTHORIZED** |
| Bandit parity | gate test + 3 migrations | none | **NOT AUTHORIZED** |
| D7-1 / D7-2 | routers, updater, `core/state.py` | none | **NOT AUTHORIZED** + policy/evidence-blocked |
| Tenant / PostgreSQL | dependencies, RLS migrations | none | **NOT AUTHORIZED** + **BLOCKED** (no service) |
| Redis / workers | `redis_manager.py`, `main.py` | none | **NOT AUTHORIZED** + **BLOCKED** |
| Terraform | all roots | none | **NOT AUTHORIZED** + **BLOCKED** (no tooling) |

Per §14.10, this phase delivers the read-only audit, source-linked proposed changes, and test
specifications. **No implementation was simulated.**

---

## 4. Correctness-evidence architecture

`CURRENT SOURCE` Topology re-verified: `01-pr-check.yml:4-5` is `on: pull_request:` **only**;
`02-build.yml:14` is `on: push: [main, master]` plus an **uncalled** `workflow_call`;
`03-deploy.yml:5-9` is `workflow_run` on `02 Build` plus `workflow_dispatch`. **P18-1 stands.**

| Option | Exact-SHA property | Cost / risk |
|---|---|---|
| **A — PR / merge-queue** | **Weak as-is.** `01 PR Check` runs on the PR *head*; `02 Build` builds the post-merge `main` SHA. Head evidence is **not** evidence for the merge commit | Needs merge-queue or squash/fast-forward so head ≡ deploy SHA. Cheapest CI minutes; easy to get subtly wrong |
| **B — dedicated push-path workflow** | **Strong** | Must enumerate which of the 8 jobs suffice without silently dropping required coverage; risk of two diverging pipelines |
| **C — extend `01 PR Check` to `push`** | **Strong** | All 8 jobs on every push, including **`provisioning-and-isolation`** (`:565`, *"Provisioning, Privilege Hardening & Tenant Isolation"*) which **provisions a database**. Duplicate PR+push runs, concurrency and cost implications |
| **D — other** | — | None found in the tree |

**No option selected.** This changes release policy, required checks and infrastructure cost, so
per §3.2 it is an owner decision. **No permissive fallback was added.** No §3.4/§13 regression tests
were written, because tests must encode an **approved** policy.

---

## 5. Image-provenance graph

`CURRENT SOURCE` Phase 31's corrected analysis re-verified; classifications unchanged.

| # | Link | Status |
|---|---|---|
| 1 | source SHA → build | **present, ungated** — `02-build.yml` `on: push`; no test/scan precedes it |
| 2 | build → push | present — pushes `:${{ github.sha }}` **and `:latest`** |
| 3 | push → registry digest | **LATENT/ORPHANED** — `:78` resolves, `:81` exports; **no workflow calls `02-build.yml`**, and `workflow_run` carries no job outputs, so `image_digest` is read by nothing. Its `\|\| echo "$IMAGE_TAG"` tag-as-digest fallback is therefore **not currently exploitable** — a trap to fix *before* wiring, not today's exposure |
| 4 | digest → scan of that digest | **BROKEN** — `05 Security` builds and scans a local `trading-platform:scan` image, **not the pushed ECR digest** |
| 5 | scan → SHA+digest binding | **ABSENT** — `require_security_scan.py` verifies a SHA; it never sees an image identity |
| 6 | evidence → deploy input | **digest verified, then discarded** — `03-deploy.yml:162-174` is genuinely **fail-closed** (`\|\| echo ""`, then `-z`/`= "None"` → `exit 1`), but `$DIGEST` is step-local and never written to `$GITHUB_OUTPUT` |
| 7 | deploy input → task definition | **BROKEN** — `ecs_deploy_and_diagnose.py:94` `os.getenv("IMAGE_TAG", "latest")`; `:901` builds `…:{IMAGE_TAG}` |
| 8 | Terraform declared state | **BROKEN** — `terraform/ecs.tf` six definitions on `:latest`, incl. workers `:478` |
| 9 | root task-definition JSON | **BROKEN, operationally reachable** — `ecs-task-definition-full.json:18` fully-qualified `:latest`, `:211` `questdb/questdb:latest`; `docs/history/ECS_DEPLOYMENT_GUIDE.md` documents `register-task-definition --cli-input-json` |
| 10 | Kubernetes path | **BROKEN, status UNKNOWN** — §6 |
| 11 | ECR tag immutability | **UNKNOWN** — `image_tag_mutability` / `aws_ecr_repository`: **0 matches in any `.tf`**. The tree does not establish the live setting; ECR's default is not proof. `03-deploy.yml`'s step is nonetheless named *"Verify ECR Immutable Artifact…"* |
| 12 | runtime task image identity | **UNKNOWN** — needs owner/cloud evidence |

**The active exposure remains deployment-by-tag against a registry whose immutability is
unestablished, plus ~10 `latest` references.** Fixing link 3's fallback alone changes nothing
observable.

---

## 6. Deployment-surface census

| Surface | Trigger | Default input | Guard | Status |
|---|---|---|---|---|
| `03-deploy.yml` → `ecs_deploy_and_diagnose.py` | `workflow_run` + dispatch | `IMAGE_TAG` = `head_sha \|\| github.sha`; script default **`latest`** | digest-existence **fail-closed**, result discarded | **ACTIVE** |
| `06-frontend-deploy.yml` | `push` (`algo22-terminal/**`) | n/a | **no release-evidence gate**; size/ordering checks only | **ACTIVE, ungated** |
| `ecs-task-definition-full.json` | manual runbook | `:latest` | none | **documented path** |
| `terraform/ecs.tf` (×6) | `terraform apply` | `:latest` | none | declared state |
| `scripts/deploy.sh` | manual | `${2:-latest}` | none | **UNKNOWN** |
| `scripts/blue-green-deploy.sh` | manual | `deploy <image-tag>` | not re-audited this phase | **UNKNOWN** |
| `infra/deploy.sh` | historical index only | — | — | **UNKNOWN** |

`CURRENT SOURCE` `scripts/deploy.sh` three defects (Phase 31, unchanged): `IMAGE_TAG=${2:-latest}`;
`kubectl set image … backend=$IMAGE_TAG` passes a **bare tag** with no registry/repository; the
health-check loop `break`s on success but **cannot fail** after five failures, so a dead deploy still
prints *"Deployment complete!"*. Plus unquoted `export $(grep … | xargs)`. **No `kubectl` was run.**
Operational status is **UNKNOWN** — no workflow references it, which is not proof it is dead given
the documented manual runbooks. Resolving evidence: platform owner's confirmation of whether the
cluster path is live.

---

## 7. Security workflow

`CURRENT SOURCE` All three Phase 30/31 defects unchanged:

1. **IaC reachability** — `iac-scan` runs two blocking Trivy steps sequentially with **no
   `if: always()`**. `terraform/` fails (5 CRITICALs), so the second root never executes. That root
   — `aerora_quant_platform/infrastructure/terraform/main.tf`, 178 lines — declares an **S3 remote
   backend** and an **`aws_secretsmanager_secret`**, and the step specifies `scanners:
   'misconfig,secret'`. Its zero findings are indistinguishable from never having run.
2. **SAST evidence** — `sast-scan`'s upload lacks `if: always()` (unlike `iac-scan`'s), so the
   Bandit report is discarded exactly when there is something to triage.
3. **Safety, two independent defects** — it sits *after* Bandit in the **same job**, so it is
   **skipped** when Bandit fails; and `|| echo "Safety advisory logged"` means it **cannot fail**
   when it runs. **Fixing one is insufficient.** Whether Safety is mandatory or advisory is an
   **owner policy decision** — not selected, not changed.
4. **Scan target** — `05 Security` scans a locally rebuilt image, not the pushed ECR digest
   (provenance link 4).

---

## 8. Diagnostics, Bandit, runtime, tenant, Redis, Terraform, frontend

| Lane | Status | Position |
|---|---|---|
| **Diagnostics** | NOT AUTHORIZED | `require_security_scan.py` reports *"a CRITICAL container vulnerability is present. Fix the pin"* for any failed `05 Security`. At `ecd23a9e` the Container job **passed**; Bandit and Trivy IaC failed. It reads only run-level conclusion, so it holds no job-level evidence. Refusal correct; explanation unsupported |
| **Bandit** | REUSED EVIDENCE — not re-run | Source hashes, `bandit` 1.9.4, config and command unchanged since Phase 24's execution, so per §14.4 a fresh run adds nothing: **9 × B608 MEDIUM / exit 1** tree-wide across three Alembic migrations vs **0 / exit 0** on the single module the gate test inspects; negative control **PASS**; two stale `# nosec B608` in `backend_app/test_perf.py` |
| **D7-1** | NOT AUTHORIZED + policy-blocked | Three `access_token=` callers against `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` correct. Market route's broad `except` can turn a vault/auth failure into silent anonymous market access. **Keys-absent policy not invented** |
| **D7-2** | NOT AUTHORIZED + evidence-blocked | `portfolio_cache_updater.py:151` imports non-existent `backend_app.backend.key_manager`, **outside** the inner `try`, so it fails deterministically every cycle for every user while health reports `ACTIVE`. No speculative class substituted; no `APIKeyVault()` constructed with guessed config. Needs **redacted platform-owner boot evidence** |
| **Workers** | CURRENT SOURCE | `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`**. **No retries wired; no UNKNOWN-exit policy invented** |
| **Tenant / PostgreSQL** | **BLOCKED · NOT CERTIFIED** | Service closed; no grant. Nine RLS policies on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` only in a SQL comment** (`026…:75`), zero executable statements; `dependencies.py` has **0** tenant-context references; two GUC setters both off the request path; fallback DSN role owns the tables so policies are **inert** on the ORM path; `tenant_db_context.py` correct and **unwired**. **FORCE RLS not enabled in any form.** Permissive-`OR` composition and missing-tenant-predicate hypotheses **NOT RUN** |
| **Redis** | **BLOCKED** | Service closed. **No integration test run; skips are not passes.** Historical counts not assumed current; strict xfail preserved |
| **Terraform** | **BLOCKED** | `trivy`, `docker`, `terraform` all absent — no scan, no `fmt -check`, no `validate`, no `plan`, **no result claimed**. No `init` attempted against the S3 backend. Egress untouched; **AWS-0054 false-positive analysis and the withdrawn VPC-collision finding both preserved, not resurrected** |
| **Frontend** | **PARTIAL / NOT CERTIFIED** | `tests/test_release_artifacts.py` **8 passed** (Phase 30, source unchanged) — size and gate-before-sync ordering hold, and are **not** a substitute for release authorization. No security-evidence gate on the publish path. **Published-byte identity for `ecd23a9e` remains UNKNOWN** from available evidence; containment stays with the production owner. No S3/CloudFront access |

---

## 9. Test discovery

`pytest.ini` → `testpaths = tests`. The maintained gate suites live outside it:
`staging/phase18_test_release_gate.py` (**28**) and `staging/phase17_test_frontend_release_gate.py`
(**42**) — **70 tests CI never runs.** Per §12 the remedy is promoting genuine suites into `tests/`,
**not** broadening discovery into `staging/` (188 reports, probes, generated evidence).

**Critical caveat from §1:** those 70 passing tests are **not** evidence the gate works. §1 shows
28 of them pass *because* the fixtures encode the defect. Promotion must come with YAML-contract
tests that parse the real workflows.

---

## 10. Commands and results

All read-only; repository state unchanged by every command.

| Command | Evidence class | Result |
|---|---|---|
| `git rev-parse` / `rev-list` / `status --porcelain` / `diff --cached` / `stash list` / `worktree list` / `reflog -1` | TESTED NOW | **PASS** — §2 |
| `Test-Path` Phase 36 destination | TESTED NOW | **PASS** — absent |
| `grep 05-security.yml` job IDs + `name:` | TESTED NOW | **PASS** — 3 mismatches (§1) |
| `grep 01-pr-check.yml` job IDs + `name:` | TESTED NOW | **PASS** — 6 mismatches (§1) |
| `grep phase18_test_release_gate.py` fixture construction | TESTED NOW | **PASS** — masking confirmed (§1) |

**Tests executed: none.** No lane is authorized, so there was no change to validate. Prior suite
results (28 / 42 / 8) are **REUSED EVIDENCE** at identical source, cited not restated. Bandit:
**REUSED**. Trivy / Docker / Terraform / PostgreSQL / Redis: **BLOCKED**. CI dispatch: **NOT RUN**.
No skipped, blocked or unrun check is recorded as a pass.

---

## 11. Unresolved owner decisions

| # | Question | Owner | Why it matters |
|---|---|---|---|
| **1** | Correctness-evidence design **A/B/C/D** (§4) | release owner | Prerequisite for the verifier. Option A must solve PR-head ≠ merge-SHA; Option C provisions a database per push |
| **2** | Authorize the verifier's **three-part** repair: P18-1, **P18-3 job-identity mapping**, and YAML-contract tests replacing the masking fixtures | release owner | Promotion today deadlocks deployment twice (§1) |
| **3** | Bind the digest end to end — export `$DIGEST`, pass to the deploy helper, compare before `register-task-definition`; fix `02-build.yml:78` **before** wiring | release owner | The active provenance defect (§5) |
| **4** | Declare ECR `image_tag_mutability`, or supply the live setting | infrastructure owner | Link 11; no tag is a provable anchor without it |
| **5** | Is the Kubernetes / blue-green path live? | platform owner | Determines whether §6's defects are live exposure or dead code |
| **6** | Safety: mandatory or advisory? | release owner + policy | Both execution defects need fixing either way; the policy decides the failure semantics |
| **7** | Scan the pushed ECR digest, not a local rebuild | release owner | Link 4 |
| **8** | Keys-absent market-data policy (D7-1); vault boot evidence (D7-2) | product/security; platform owner | Blocks those repairs |
| **9** | Frontend incident containment | production owner | Not performed or initiated |
| **10** | Disposable PostgreSQL + Redis; Trivy/Docker | CI / platform owners | Unblocks all certification lanes |

---

## 12. Next execution order

1. Owner answers decision 1; then 2 (verifier repair incl. P18-3) and 3 (digest binding) in parallel.
2. Decision 6 unblocks the `05-security.yml` repairs; 4 and 5 are independent and can run anytime.
3. Certification lanes (tenant, Redis, Terraform) wait on decision 10's environments.
4. D7-1/D7-2 wait on decision 8.

---

## 13. Integrity and status matrix

**Files changed: 1 — this report.** No governed target edited; no governance record altered; no
report, prototype, stash, worktree or concurrent modification touched. No secret, credential, token,
DSN or key material read or written — the task definition's `valueFrom` Secrets Manager entries were
observed to be **references**, and no value was resolved. No threshold lowered, no suppression added,
no `continue-on-error` introduced, no fake digest or tag-for-digest substitution, no unrun check
reported as passing. `03-deploy.yml`'s fail-closed refusal intact.

| Dimension | Status |
|---|---|
| Source modifications | **NOT AUTHORIZED** (0 files) |
| Release correctness evidence | **BLOCKED** — owner decision 1 |
| Verifier correctness | **FAIL** — P18-1 and **P18-3** both deadlock; tests mask P18-3 |
| Image provenance | **NOT ESTABLISHED** — 5 broken, 1 latent, 2 unknown |
| Security workflow completeness | **FAIL** — one IaC root unreachable, SAST evidence lost, Safety skipped *and* non-blocking |
| Frontend publication safety | **FAIL** — no release-evidence gate; artifact identity **UNKNOWN** |
| Kubernetes deployment status | **UNKNOWN** — defects verified, operational status unresolved |
| ECS deployment status | **PARTIAL** — digest-existence check fail-closed; deploys by tag |
| Tenant isolation | **BLOCKED / NOT CERTIFIED** |
| Live trading certification | **NOT CERTIFIED** |
| Test coverage / execution | **PARTIAL** — 70 maintained tests outside CI; 28 of them validate a masked contract |
| Infrastructure validation | **BLOCKED** — no Trivy, Docker or Terraform |

**A mocked test pass is not deployment proof. A successful build is not a security approval. A
static RLS review is not tenant-isolation certification.**
