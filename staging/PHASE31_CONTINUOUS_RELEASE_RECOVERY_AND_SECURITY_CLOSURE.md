# Phase 31 — Continuous Release Recovery, Correctness-Path Resolution, Image Provenance Enforcement, and Security Closure

**Destination verified ABSENT before writing.** No existing report, prototype, or artifact overwritten.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 tests ·
0 governance records changed. 1 artifact written (this file).** No stage, commit, push, stash
operation, worktree mutation, CI dispatch, deploy, publish, rollback, cloud/production mutation,
`kubectl` invocation, trade, or credential read.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 0. Headline — the provenance picture was wrong in my own prior reports, in both directions

Three source-verified corrections this phase. Together they re-grade the top-priority defect.

**(a) `03-deploy.yml`'s digest handling is fail-closed and CORRECT.** My Phase 30 provenance table
recorded the deploy→digest link as *"ABSENT — no digest is carried or compared."* That understates
it. `SRC` the "Verify ECR Immutable Artifact Exists for Commit SHA" step:

```bash
DIGEST=$(aws ecr describe-images … --output text 2>/dev/null || echo "")
if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
  echo "Deployment FAIL-CLOSED: Refusing to deploy untested or missing build artifact."
  exit 1
fi
```

Same shell idiom as the build, **opposite safety property**: `|| echo ""` produces an empty value
that is then *explicitly checked* and refused. The deploy side genuinely fails closed.

**The real defect is narrower and sharper: `$DIGEST` is step-local.** It is never written to
`$GITHUB_OUTPUT` and never passed on. The next step passes `IMAGE_TAG`, and
`ecs_deploy_and_diagnose.py:901` builds `…/vyomquant-api:{IMAGE_TAG}`. **The digest is proven to
exist, then discarded, and deployment proceeds by tag.** The step is even named "Immutable
Artifact" while ECR immutability is declared nowhere.

**(b) The build's digest machinery is dead code — so its fail-open is latent, not active.**
`SRC` A search of every `.github/workflows/*.yml` for `workflow_call`, `uses: ./.github`,
`02-build` and `image_digest` returns matches **only inside `02-build.yml` itself**. It declares
`on: workflow_call:` with `image_tag`/`image_digest` outputs, and **no workflow invokes it that
way**; `03-deploy.yml` consumes it via `workflow_run`, which **does not carry job outputs**.

So `image_digest` is computed, written to `$GITHUB_OUTPUT`, exported as a `workflow_call` output —
and **read by nothing**. Consequently `02-build.yml:78`'s `|| echo "$IMAGE_TAG"` tag-as-digest
fallback is **currently unexploitable**: there is no consumer to mislead. I called it the top
provenance defect in Phases 24, 29 and 30. **Re-graded: latent trap, dangerous the moment anyone
wires the output — not today's active exposure.** It should still be fixed, and fixed *before* any
wiring.

**(c) The mutable-tag consumer census is ~10, not 7.** My Phases 29–30 counted seven. Concurrent
Phases 22 and 25 found more, and this phase verified two directly (§3).

**Where that leaves the actual active exposure:** deployment proceeds by **tag**; ECR has no
declared `image_tag_mutability` so it defaults to MUTABLE; and roughly ten references pin `latest`.
That — not the build's dead fallback — is what makes the deployed artifact unprovable.

---

## 1. Preflight and comparison with Phase 30

| Measurement | Phase 30 | Phase 31 | Δ |
|---|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e` | `main` / `ecd23a9e` | same |
| `origin/main` divergence | 0 / 0 | **0 / 0** | same |
| staged / unmerged | 0 / 0 | **0 / 0** | same |
| stashes | 2 | **2 — identified, not applied or dropped** | same |
| worktrees | — | **1** (main only; no additional worktree exists) | clarified |
| porcelain | 140 | 143 at entry → 144 at exit | +3 concurrent, +1 this report |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **identical** | no commit/reset/checkout |
| Phase 31 destination | — | **ABSENT** before writing | — |

**Concurrency.** The only non-`staging/` modification is `tests/test_drift_classification.py`
(` M`, another session's work) — **not touched.** Re-verified: **no workflow, script, Terraform
file, migration, router, test or governance record is modified anywhere in the tree.** Phase 30's
report still corresponds to this HEAD, so its source findings remain applicable and were not
regenerated wholesale.

**Tooling (discovered):** `python` 3.12.10 ✔ · `pytest` ✔ · **`bandit` 1.9.4** ✔ · `gh` ✔ ·
`node`/`npm` ✔ · **`docker` ✘ · `trivy` ✘ · `terraform` ✘** · PostgreSQL `:5432` closed ·
Redis `:6379` closed. No credential, token, DSN or environment value was read or printed;
`staging/pg_dsn.txt` remains unopened.

**Authorization — re-read from the current checkout.** Ledger `:800-804` → `AP-W1D-1` … `AP-W1D-5`
**all NOT GRANTED**; `A23E6` → **0 ticked / 12 unticked**; no `CODEOWNERS` / `OWNERS` /
`MAINTAINERS`. Only live grant: **`AP-W1D-3`**, four drift-classifier files — **none a Phase 31
target.** Per §1 of this brief, neither this prompt nor Phase 30's recommendations constitute a
grant. **Every implementation lane is unauthorized; no governed target was edited.** Per the brief's
continuation rule, work proceeded on read-only lanes.

---

## 2. Priority 0 — correctness-evidence path: still blocking, now with the options costed

Phase 30's blocking finding **re-verified as current**: `01-pr-check.yml` is `pull_request`-only
(line 5), `02-build.yml` runs `on: push`, `03-deploy.yml` fires on `workflow_run` of the build, and
`phase18_release_gate.py` makes `01 PR Check` **mandatory** with refusal when no run exists for the
SHA. **Promoting the verifier as-is would refuse every push-triggered deployment.**

Per §3.2, the options and their costs — analysis only, **no option selected**:

| Option | Exact-SHA correctness | Cost / risk |
|---|---|---|
| **A — PR-merge-only release policy** | Weak unless addressed: `01 PR Check` runs on the **PR head**, not the post-merge `main` SHA that `02 Build` builds. Evidence for the head is **not** evidence for the merge commit | Requires either merge-queue-style evidence or squash/fast-forward so head ≡ deploy SHA. Cheapest in CI minutes; subtle to get right |
| **B — dedicated push-path correctness workflow** | **Strong** — emits evidence for the exact push SHA | Must choose which of the 8 jobs are sufficient; must **not** silently omit required tests. Risk of a second, diverging pipeline |
| **C — extend `01 PR Check` to `push`** | **Strong** | Runs all 8 jobs on every push, including **`provisioning-and-isolation`, which provisions a database**. Duplicate PR+push runs; concurrency and cost implications |
| **D — other source-backed design** | — | None found in the tree this phase |

**This changes release policy and infrastructure cost, so per §3.3 it is an owner decision and I did
not make it.** I also did not add any override or fallback permitting deployment without correctness
evidence. No regression tests were added under §3.4, because tests must encode an **approved**
policy and none is approved.

---

## 3. Priority 2 — provenance chain, link by link

| # | Link | Status | Evidence |
|---|---|---|---|
| 1 | approved source SHA → build | **present but ungated** | `02-build.yml` `on: push`; no test/scan precedes the build |
| 2 | build → image push | present | pushes `:${{ github.sha }}` **and `:latest`** |
| 3 | push → registry digest | **computed, then orphaned** | `:78` resolves it; `:81` exports it; **nothing consumes it** (§0b) |
| 4 | digest → security scan of that digest | **ABSENT** | `05 Security` scans a locally built `trading-platform:scan` image, **not the pushed ECR digest** |
| 5 | scan evidence → release record binding SHA to digest | **ABSENT** | `require_security_scan.py` verifies a **SHA**; it never sees an image identity |
| 6 | release evidence → deploy input | **digest verified, then discarded** | fail-closed existence check; `$DIGEST` step-local; next step passes `IMAGE_TAG` (§0a) |
| 7 | deploy input → task definition | **BROKEN** | `ecs_deploy_and_diagnose.py:94` `os.getenv("IMAGE_TAG", "latest")`; `:901` builds `…:{IMAGE_TAG}` |
| 8 | Terraform declared state | **BROKEN** | `terraform/ecs.tf` — six definitions on `:latest`, incl. workers at `:478` |
| 9 | root task-definition JSON | **BROKEN — operationally reachable** | `ecs-task-definition-full.json:18` fully-qualified `…/vyomquant-api:latest`; `:211` `questdb/questdb:latest`. `docs/history/ECS_DEPLOYMENT_GUIDE.md` documents `aws ecs register-task-definition --cli-input-json file://ecs-task-definition-full.json` — a **documented runbook path**. `PRIOR` Phase 22 established the deploy script mutates only the running definition's image specifically to stop this JSON's stale config overwriting production, so it is a seed/fallback — but anything registering from it pins the mutable alias |
| 10 | Kubernetes path | **BROKEN, and worse** | §3.1 |
| 11 | ECR tag immutability | **UNKNOWN / defaults MUTABLE** | `image_tag_mutability` / `aws_ecr_repository`: **0 matches in any `.tf`**. ECR defaults to MUTABLE, so a SHA tag is not a provable anchor — while `03-deploy.yml`'s step is named "Immutable Artifact" |
| 12 | runtime task image identity | **UNKNOWN** | requires owner/cloud evidence; not fetched |

**Provenance NOT ESTABLISHED.** Eight links are broken, absent, or orphaned; two are unknown.

### 3.1 `scripts/deploy.sh` — inspected in full this phase

`SRC` A Kubernetes deployment path that bypasses the ECS chain's fail-closed guard entirely. Three
defects, in increasing severity:

```bash
IMAGE_TAG=${2:-latest}                                   # (i) mutable default
kubectl set image deployment/trading-backend \
    backend=$IMAGE_TAG -n $ENVIRONMENT                   # (ii) bare tag as full image ref
for i in {1..5}; do … done                               # (iii) health check cannot fail
```

1. **`latest` default** — a 10th mutable-tag consumer, on a platform the ECS digest guard never sees.
2. **Malformed image reference** — `kubectl set image … backend=$IMAGE_TAG` passes the *tag alone*,
   with no registry or repository. `./scripts/deploy.sh production` would set the container image to
   literally `latest`. This is a latent bug independent of provenance.
3. **Non-failing health check** — the loop `break`s on success but falls through on five failures;
   nothing exits non-zero. A deploy whose health never comes up still reports
   *"Deployment complete!"*.

Also `export $(grep -v '^#' .env.$ENVIRONMENT | xargs)` — unquoted word-splitting on an env file.
No `kubectl` command was executed.

**Active status: UNRESOLVED.** No workflow references `deploy.sh`. A separate
`scripts/blue-green-deploy.sh` exists with its own `deploy <image-tag>` interface, and
`infra/deploy.sh` appears in a historical index. Per §5.2 the script was **preserved** and the owner
question recorded: is the Kubernetes path live, or is it dead alongside the ECS migration?

---

## 4. Priorities 1, 3–8 — status

| Lane | Status | Position |
|---|---|---|
| **1 — verifier audit/promotion** | **AUDITED · NOT PROMOTED (unauthorized, and policy-blocked)** | Safety properties re-confirmed: exact-SHA binding, explicit `success` for every mandatory workflow **and job**, fail-closed on absent/unfinished/skipped/cancelled/failed/neutral/malformed, HTTP+URL error refusal, empty-policy refusal, branch allow-list, **no override**, `workflow_run` checkout trap documented, reviewable `ADVISORY_JOBS`. Two defects stand: **P18-1** the `01 PR Check` deadlock (§2); **P18-2** pagination **refuses** on a full 100-item page rather than fetching further pages — fail-closed but brittle, and §4.2 asks for genuine bounded pagination. Promotion requires P18-1 fixed first |
| **3 — security coverage** | **INVESTIGATED — unauthorized** | Three defects unchanged: two sequential blocking IaC steps without `if: always()`, so `terraform/`'s failure prevents the second root from ever being scanned — and that root (`aerora_quant_platform/infrastructure/terraform/main.tf`, 178 lines, S3 remote backend) declares an **`aws_secretsmanager_secret`** while the step specifies `scanners: 'misconfig,secret'`; `sast-scan`'s upload lacks `if: always()` so Bandit evidence is discarded on failure; `Run Safety Check` sits behind Bandit in the same job so it is **skipped** when Bandit fails, and `\|\| echo` means it **cannot fail** when it runs. **Safety blocking/advisory remains an owner policy decision — not invented, not changed** |
| **4 — diagnostic fidelity** | **INVESTIGATED — unauthorized** | `require_security_scan.py` tells the operator a failed `05 Security` means *"a CRITICAL container vulnerability is present. Fix the pin."* At `ecd23a9e` the Container job **passed**; Bandit and Trivy IaC failed. The script reads only run-level conclusion, so it holds no job-level evidence. The refusal is correct and must be preserved; only the explanation is wrong. **Note the scan-target gap this exposes:** `05 Security` builds and scans a **local** image, not the pushed ECR digest (chain link 4) |
| **5 — Bandit parity** | **NOT RE-RUN — prior evidence reused** | Source hashes, `bandit` 1.9.4, config and command all unchanged since Phase 24's execution, so per §13 the evidence is reusable and re-running would add nothing: **9 × B608 MEDIUM / exit 1** tree-wide vs **0 / exit 0** on the single module the gate test inspects; negative control **PASS**; two stale `# nosec B608` in `backend_app/test_perf.py`. Parity fix and per-site dispositions remain unauthorized |
| **6 — D7-1** | **INVESTIGATED — unauthorized, policy-blocked** | Three `access_token=` callers against `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` is the correct reference. The market route's broad `except` can turn a vault/auth failure into silent anonymous market access. **The keys-absent policy is unresolved and was not invented** |
| **6 — D7-2** | **INVESTIGATED — unauthorized, dependency-blocked** | `portfolio_cache_updater.py:151` imports the non-existent `backend_app.backend.key_manager`, **outside** the inner `try`, so it fails deterministically for every user every cycle while health reports `ACTIVE`. Per §9.2 I did **not** substitute a speculative class or instantiate `APIKeyVault` with guessed configuration. Authoritative vault identity needs **redacted platform-owner boot evidence** |
| **7 — tenant / PostgreSQL** | **NOT CERTIFIED · BLOCKED** | PostgreSQL closed; no database grant. Static position unchanged: nine RLS policies on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` only in a SQL comment**, zero executable statements; `dependencies.py` has **0** tenant-context references; two GUC setters, both off the request path; fallback DSN role is the table owner so policies are **inert** on the ORM path; `tenant_db_context.py` correct and **unwired**. **FORCE RLS not enabled in any form.** The permissive-`OR` composition and missing-tenant-predicate hypotheses are **NOT RUN** and remain open |
| **7 — Redis / workers** | **BLOCKED / NOT VERIFIED** | Redis closed. **No integration test run; skips are not passes.** Historical counts not assumed current. `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`**. **No retries wired, no UNKNOWN-exit policy invented**, strict xfail preserved |
| **8 — Terraform** | **BLOCKED** | `trivy`, `docker`, `terraform` all absent. **No scan, no `init`/`validate`/`plan`, no result claimed.** Egress rules untouched; AWS-0054 evidence retained, not suppressed; `terraform/redis-only/main.tf` (3 937 B) present and **not** modified; no backend or state change |
| **9 — frontend publication** | **PARTIAL PASS / NOT CERTIFIED** | `tests/test_release_artifacts.py` **8 passed** (Phase 30, source unchanged) — size and gate-before-sync ordering hold. But **no security-evidence gate exists on the publish path**, and the live `ecd23a9e` artifact identity is **UNKNOWN** from local evidence. No S3/CloudFront inspection or mutation; containment remains an unresolved production-owner decision |
| **10 — test discovery** | **INVESTIGATED — unauthorized** | 28 + 42 = **70 passing gate tests** outside CI (`testpaths = tests`). Fix is to **promote genuine suites into `tests/`**, not to broaden `testpaths` into `staging/` (188 reports, probes, generated evidence) |

---

## 5. Commands executed

All read-only. Repository state unchanged by every command.

| Command | Outcome |
|---|---|
| `git rev-parse` / `rev-list --left-right --count` / `status --porcelain` / `diff --cached` / `stash list` / `worktree list` / `reflog -1` | **PASS** — §1 |
| `Test-Path` ×4 (report destination, `deploy.sh`, `ecs-task-definition-full.json`, `redis-only/main.tf`) | **PASS** — all located |
| `read_file scripts/deploy.sh` (full) | **PASS** — 3 defects found (§3.1) |
| `grep ecs-task-definition-full.json` for image refs | **PASS** — `:18`, `:211` on `:latest` |
| `grep` repo-wide for `deploy.sh` / `ecs-task-definition-full` references | **PASS** — runbook path found; no workflow reference |
| `read_file 03-deploy.yml:158-184` | **PASS** — digest check is **fail-closed**; `$DIGEST` step-local (§0a) |
| `grep .github/workflows/*.yml` for `workflow_call` / `image_digest` | **PASS** — **no consumer of `02-build.yml`** (§0b) |

**Tests executed this phase: none.** Every implementation lane is unauthorized, so there was no
change to validate; Phase 30's suite results stand at identical source and are cited, not restated
as fresh runs.

| Test lane | Disposition |
|---|---|
| `staging/phase18_test_release_gate.py` (28) · `phase17_test_frontend_release_gate.py` (42) · `tests/test_release_artifacts.py` (8) | **PASS in Phase 30 at this HEAD; NOT RE-RUN** — source unchanged |
| Bandit CI-equivalent scan | **NOT RE-RUN** — prior evidence reusable (§4) |
| Trivy / Docker / Terraform | **BLOCKED** — tools absent |
| PostgreSQL / tenant integration · real Redis | **BLOCKED** — services closed |
| Frontend build + bundle sweep | **NOT RUN** — no authorized lane requires it |
| CI dispatch | **NOT RUN** — not authorized |

**Evidence classes:** `SRC` current-source inspection (all of §0, §3) · `PRIOR` other sessions'
findings, labelled (Phase 22/25 census; Phase 30 suite results) · `CI` prior read-only run queries
at an unchanged HEAD · owner/cloud evidence: **none received**. No static inspection or local test
is presented as production verification.

---

## 6. Owner decisions, in dependency order

| # | Decision | Owner | Why here |
|---|---|---|---|
| **1** | **Choose the correctness-evidence design — A, B, C or D (§2)** | release owner | **Prerequisite for the verifier.** Promotion before this deadlocks all deploys. Option A additionally needs the PR-head ≠ merge-SHA problem solved |
| **2** | **Bind the deployed artifact to a digest** — write `$DIGEST` to `$GITHUB_OUTPUT`, pass it to the deploy script, compare before `register-task-definition`. Fix `02-build.yml:78` **before** wiring its output | release owner | This is the **active** provenance defect (§0a/§0c), ahead of the dead-code fallback |
| **3** | **Declare ECR `image_tag_mutability`**, or supply authoritative evidence of the live setting | infrastructure owner | Without it no tag is a provable anchor and link 11 cannot close |
| **4** | **Is the Kubernetes path live?** (`scripts/deploy.sh`, `blue-green-deploy.sh`, `infra/deploy.sh`) | platform owner | Determines whether §3.1's three defects are live exposure or dead code |
| **5** | **`05-security.yml` repairs + Safety blocking policy** | release owner + policy | Restores three checks that silently do not run, one covering a Secrets Manager declaration |
| **6** | **Scan the pushed ECR digest, not a locally rebuilt image** (chain link 4) | release owner | Today's scan subject is not provably the deployed artifact |
| **7** | **Promote the verifier** (after 1), fixing P18-1 and P18-2 with regression tests | release owner | 70 tests already green |
| **8** | **Bandit parity · diagnostics · D7-1 · D7-2** | code / router / platform owners | D7-1 needs the keys-absent policy; D7-2 needs boot evidence |
| **9** | **Frontend incident containment** | production owner | Not performed or initiated |
| **10** | **Disposable PostgreSQL + Redis; Trivy/Docker** | CI / platform owners | Unblocks the certification lanes entirely |

---

## 7. Status matrix

| Lane | Classification |
|---|---|
| Provenance corrections (§0a/§0b/§0c) | **Source-verified this phase** |
| `deploy.sh` three defects · `ecs-task-definition-full.json` | **Source-verified this phase** |
| Release verifier (70 tests) | **Tested with mocks/fixtures only — NOT PROMOTED, NOT AUTHORIZED** |
| `tests/test_release_artifacts.py` | **Tested locally (Phase 30, unchanged source)** |
| Bandit findings | **Tested locally (prior evidence reused)** |
| Correctness-path design | **Requires owner decision** |
| ECR immutability · runtime image identity · live artifact identity | **Requires production-owner evidence** |
| Trivy / Docker / Terraform validation | **Blocked by missing tools** |
| Tenant isolation · PostgreSQL · real Redis | **Blocked by missing services — NOT CERTIFIED** |
| All implementation lanes | **NOT AUTHORIZED** |
| Release readiness · deployment safety · provenance · frontend containment · tenant isolation | **NOT CERTIFIED** |

---

## 8. Integrity

**Files changed: 1 — this report.** No governed target edited; no governance record altered to
manufacture authorization; no report, prototype, stash, worktree or concurrent modification touched.
No secret, credential, token, DSN or key material was read or written into this report — the ECS
task definition's `valueFrom` Secrets Manager **references** were observed to be the correct pattern
(references, not literals) and no value was resolved or printed.

No threshold lowered, no finding suppressed, no exclusion or baseline added, no `|| true`
introduced, no fake digest or tag-as-digest fallback created, no skipped/blocked/unrun check
recorded as a pass, no workflow dispatched, no `kubectl` or cloud call made, and no production
certification claimed from local evidence. `03-deploy.yml`'s fail-closed refusal — now confirmed
correct — is intact.

**Continuation point.** Decision 1 is unchanged and remains policy, not code. The most useful new
input this phase gives the owner is decision 2's re-ordering: the active provenance exposure is
**deployment-by-tag against a MUTABLE-by-default registry**, not the build's orphaned digest
fallback. Fixing the fallback alone would change nothing observable; binding the digest through to
`register-task-definition` is what closes the chain.
