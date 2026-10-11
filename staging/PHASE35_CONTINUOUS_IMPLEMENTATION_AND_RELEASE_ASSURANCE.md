# Phase 35 — Continuous Implementation & Release Assurance: Executed Verification

**Destination verified ABSENT before writing. No prior report, prototype, stash or worktree altered.**

> ## Outcome
>
> `VER` **Grants: zero.** Ledger `7e238620…`, `**GRANTED**` count **0**, `:802` = `NOT GRANTED`.
> Per §15 I did **not** manufacture edits — **0 governed files changed**.
>
> Instead this phase executed real verification: **280 tests run across four suites, all green**,
> plus **four new source findings** and **one self-correction made inside this phase**.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Preflight and final integrity — identical

| Property | Entry | Exit |
|---|---|---|
| Root / branch / `HEAD` | `C:/aerora_quant_backend_updated_final1` / `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / ahead-behind | `ecd23a9e…` / **`0 0`** | unchanged |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** | unchanged |
| `git reflog -1` | `ecd23a9e` | unchanged |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |
| Ledger hash | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | unchanged |

**Write-tree method, stated because digests must not be compared across serialisations:** computed
with `GIT_OBJECT_DIRECTORY` redirected to a temp directory and `GIT_ALTERNATE_OBJECT_DIRECTORIES`
pointing at `.git/objects`. **`isolated_objects_written = 0`** — the tree object already existed, so
**nothing was written to the shared object database** that concurrent sessions are using. The value
matches Phase 14's independently recorded `4b45a763…`.

**Concurrent modifications preserved byte-for-byte, with hashes:**

| Path | Hash |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

**Environment:** Python 3.12.10 · pytest · bandit 1.9.4 · PyYAML 6.0.3 · GitHub CLI (read-only) ·
**Docker ABSENT · Trivy ABSENT · Terraform ABSENT · PostgreSQL ABSENT · Redis ABSENT**.

---

## 2. Authorization — every lane NOT GRANTED

| Lane | Governed paths | Grant |
|---|---|---|
| Correctness-workflow architecture | `01-pr-check.yml` / branch protection | **NOT GRANTED** + owner decision |
| Verifier promotion (Phase 18) | `staging/phase18_*` → `scripts/`, `tests/`; `pytest.ini` | **NOT GRANTED** |
| Frontend publication gate | `06-frontend-deploy.yml` | **NOT GRANTED** |
| Security scan reachability | `05-security.yml` | **NOT GRANTED** |
| Image provenance | `02-build.yml`, `ecs_deploy_and_diagnose.py`, `deploy.sh`, `terraform/ecs.tf`, `ecs-task-definition-full.json` | **NOT GRANTED** |
| Gate diagnostics | `scripts/require_security_scan.py` | **NOT GRANTED** |
| Bandit parity | gate test + 3 migrations | **NOT GRANTED** |
| D7-1 / D7-2-D6-D / W-6 | `market.py`, `orders.py`, `portfolio_cache_updater.py`, `exchange_executor.py` | **NOT GRANTED** |
| Tenant / RLS / Redis / Terraform | tenant context, both roots | **NOT GRANTED** + services absent |

No authority was inferred from this prompt, a request template, a prior recommendation, a report, or
silence. The ledger was not read for modification and not modified.

---

## 3. Tests actually executed — 280 tests, four suites, all green

Every row below was run in this phase. Nothing is reported as passing that did not run.

| # | Command | Exit | Result | Duration |
|---|---|---|---|---|
| 1 | `pytest staging/phase18_test_release_gate.py -q --tb=line` | **0** | **28 passed**, 28 collected | 6.81s |
| 2 | `pytest staging/phase17_test_frontend_release_gate.py -q --tb=no` | **0** | **42 passed** | 3.29s |
| 3 | `pytest tests/test_release_artifacts.py -q --tb=short` | **0** | **8 passed**, 8 collected | 6.34s |
| 4 | Bounded regression, 8 tracked files *(release artifacts, nightly-audit workflow, drift classification, snapshot invariants, 3 × quota01, redis debounce)* | **0** | **202 passed · 5 skipped · 1 xfailed** | 75.52s |

**Suite 1 is new evidence** — I had run Phase 17's prototype in earlier phases but never Phase 18's.

**Honest labelling of suite 4:** the **5 skips** are the real-Redis quota cases (`AERORA_TEST_REDIS_URL`
unset, 6379 unreachable) and the **1 xfail** is the pre-existing strict expected-failure. **Skips and
xfails are not passes**, and no Redis or PostgreSQL integration behaviour is certified by this run.

**Not run, with reasons:** the full ~11,000-test default suite (timed out at 120 s in an earlier
phase; bounded selection used instead per §14); Trivy, Terraform, Docker, PostgreSQL and Redis
checks (**tooling/services ABSENT — NOT RUN**, never labelled passed); every implementation-dependent
acceptance test (**no grant, so no change exists to validate**).

### 3.1 Prototype comparison, measured

| Prototype | Tests | Policy |
|---|---|---|
| `phase17_frontend_release_gate.py` | **42 passed** | run-level only — **admits a skipped mandatory job** |
| `phase18_release_gate.py` | **28 passed** | **run-level AND job-level**, rejects skipped mandatory jobs, paginates, refuses on ambiguity |

Phase 18 has **fewer** tests but a **stronger** policy. The count asymmetry is worth recording so
nobody promotes Phase 17 on test volume: **Phase 18 remains the correct foundation, and Phase 17 is
retained only as negative-control evidence** of the skipped-job defect.

---

## 4. Correctness-workflow architecture — decision matrix, owner decision outstanding

`VER` `01-pr-check.yml`: `pull_request` = 1, `push` = **0**, `workflow_dispatch` = **0**.
`VER` Both verifiers require `01 PR Check` evidence.

> **Therefore a SHA arriving on `main` by direct push can never satisfy either verifier. Promoting
> one into the push path would refuse every push deployment — a deadlock, not a gate.** Confirmed
> again this phase from current source.

| Option | Evidence producer | Trigger | Candidate SHA | SHA checked out | Failure behaviour | Duplicate/ambiguous runs | Works for actual release events? |
|---|---|---|---|---|---|---|---|
| **1 — PR-only `main`** | `01 PR Check` on the PR head | `pull_request` | merge commit | PR head ≠ merge commit | fail-closed | one run per head | **Only if evidence is bound to the merge commit** — the gap is that the approved run covers the PR head, not the commit that lands |
| **2 — dedicated push-triggered correctness workflow** | new workflow | `push` | the pushed SHA | **exactly the candidate** | fail-closed | one run per push | **Yes** — cleanest SHA semantics; cost is a new workflow and duplicated job definitions |
| **3 — extend `01-pr-check.yml` to pushes** | existing workflow | `pull_request` + `push` | the pushed SHA | the candidate | fail-closed | **duplicate runs per PR-then-merge** | Yes, but doubles the lane that **provisions a database twice** per run |

**I did not choose.** Options 1 and 3 change release policy or operational cost; option 1 additionally
requires solving PR-head-versus-merge-commit binding. **This is the owner decision in §7 item 1.**

**Carried forward and re-confirmed:** `phase18_release_gate.py:42-44` records that a
`workflow_run`-triggered run checks out the **default branch** unless pinned to
`${{ github.event.workflow_run.head_sha }}` — without that pin the gate validates one commit while
the job publishes another. Any chosen design must carry the pin.

---

## 5. NEW findings this phase

### 5.1 `blue-green-deploy.yml` does not exist

`VER` `.github/workflows/blue-green-deploy.yml` → **ABSENT**. The brief named it as a target; there
is no blue-green *workflow*. Only `scripts/blue-green-deploy.sh` exists. No blue-green promotion is
wired into CI, so there is no automated path by which a manual input could select an unapproved
image **through a workflow**.

### 5.2 `blue-green-deploy.sh` is fail-closed on an empty tag — a favourable contrast

`VER`

```
:66   local IMAGE_TAG=$1
:68   if [ -z "$IMAGE_TAG" ]; then
:69     log_error "Image tag required. Usage: deploy <image-tag>"
:86   kubectl set image deployment/$GREEN_DEPLOYMENT …
```

**It refuses an empty image tag**, unlike `scripts/deploy.sh:9` (`IMAGE_TAG=${2:-latest}`) which
silently defaults to the mutable alias. So the two Kubernetes-adjacent scripts have **opposite**
failure semantics, and the correct pattern again already exists in-repo.

**Limit of what I verified:** `:50`, `:54`, `:58` use `|| echo "unknown"` on `kubectl get` reads. I
confirmed those are read/display helpers and that the write at `:86` uses the **validated** argument,
not `"unknown"`. **I did not fully trace the `rollback` and `promote` branches**, so I make no claim
about whether `"unknown"` can reach a write on those paths. It still accepts a **tag**, not a digest.

### 5.3 All three shell deploy scripts have zero automated references

`VER` Across **191** files in `.github/workflows`, `scripts` and `infra`:

| Script | External references |
|---|---|
| `scripts/blue-green-deploy.sh` | **0** |
| `infra/deploy.sh` | **0** |
| `scripts/deploy.sh` | **0** |

So none is invoked by any workflow or by another script — they are **operator-invoked only**.
**Scope limit, stated because the brief warns against assuming disuse:** I scanned only those three
directories. Operator runbooks, documentation or external automation outside that scope could still
reference them, and **their live production role remains an owner question.**

`infra/deploy.sh` (140 lines) self-describes as *"Master Orchestrator & Pre/Post-Flight Verification
Engine"* and pins `EXPECTED_ACCOUNT`, `ECS_CLUSTER`, `API_SERVICE`, `DOMAIN_NAME` — it is a real
deployment orchestrator, not a stub, which is why its zero-reference status matters as a question
rather than a dismissal.

### 5.4 Self-correction made inside this phase — `ecs-task-definition-full.json` has no consumer

Mid-phase I reported that this file "**is** referenced by a workflow, making its `:latest` pin live
config." **That was wrong and I corrected it before writing.** The single hit is a **comment**:

```
01-pr-check.yml:263   # Production is not in that posture. `ecs-task-definition-full.json` sets
```

`VER` So the file has **0 functional workflow consumers**. Its image lines:

```
:18    "image": "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:latest"
:211   "image": "questdb/questdb:latest"
```

`:18` is a fully-qualified **mutable** API tag; `:211` is the third-party QuestDB image, which the
brief correctly keeps as a **separate** decision. **Whether this file is applied operationally is
unresolved from the repository** — the same class of question as §5.3.

---

## 6. Status carried forward (hash-verified, reused with provenance)

Inputs unchanged, so these were **not** re-derived or re-run:

| Item | State | Provenance |
|---|---|---|
| Second IaC root never scanned; declares `backend "s3"` and `aws_secretsmanager_secret`; scan configured `misconfig,secret` | **open** | Phase 27/28 |
| Safety defective twice — skipped after Bandit failure **and** `\|\| echo` non-blocking | **open** | Phase 28 |
| SAST upload lacks `if: always()` — evidence discarded on failure | **open** | Phase 24 |
| `02-build.yml:78` tag-as-digest fallback; `03-deploy.yml:168-172` fail-closed guard is correct | **open / preserve** | Phase 24 |
| Frontend published at `ecd23a9e` with `05 Security` red | **historical fact** | CI-observed |
| Bandit: 9 × B608 MEDIUM, bandit 1.9.4; 2 inert `# nosec: B608` in `test_perf.py` | **open** | Phase 24/27 |
| `require_security_scan.py` diagnostics misattribute cause | **open** | Phase 26 |
| D7-1 three invalid sites vs nine legitimate callers | **open** | Phase 19 |
| D6-D precedes D7-2 (`MockEngine.__getattr__` noop) | **open** | Phase 24 |
| W-6 — `.retryable` has no control-flow consumer | **open** | Phase 19/24 |
| Terraform Redis SG collision | **premise withdrawn** | Phase 26/27 |
| ECR immutability | **UNKNOWN** — `aws_ecr_repository` and `image_tag_mutability` both 0 refs | Phase 28 |
| Tenant isolation | **NOT CERTIFIED** | source-only |

**Tenant matrix note:** the brief cites 4 failures of 150 cells caused by entitlement/quota
dependencies short-circuiting before the ownership check. **I could not reproduce or refute this
phase** — PostgreSQL is absent, so the matrix is **NOT RUN**. I therefore neither confirm nor dispute
it, and I did not reorder any production dependency.

---

## 7. Owner decisions, in dependency order

> **1 · Correctness-workflow architecture — FIRST, because it gates and de-risks everything else.**
> *"Releasable commits obtain correctness evidence via ⟨**Option 1** PR-only `main`, and here is how
> approved PR evidence binds to the merge commit⟩ / ⟨**Option 2** a dedicated push-triggered
> correctness workflow⟩ / ⟨**Option 3** extend `01-pr-check.yml` to pushes, accepting duplicate runs
> and double database provisioning⟩."* **Until answered, promoting any verifier deadlocks the push
> release path (§4).**
>
> **2 · G-1 security scan reachability + evidence retention** — `05-security.yml` +
> `tests/test_security_workflow_structure.py`. Make the `aerora_quant_platform/infrastructure/` scan
> reachable independently; add `if: always()` to the SAST upload; make Safety run independently of
> Bandit; condition the IaC report upload on the report existing. **Prohibited:**
> `continue-on-error`, `|| true`, severity reduction, scope narrowing, fabricated reports.
>
> **3 · Safety policy** — *"`safety check` is ⟨mandatory⟩ / ⟨advisory⟩, **and** must run
> independently of the Bandit result either way."*
>
> **4 · G-3 build digest** — `02-build.yml:78` only; adopt the `03-deploy.yml:168-172` pattern.
> Preserve the deploy guard.
>
> **5 · Verifier promotion** — **Phase 18**, after decision 1. Repair its API display-name identity
> mapping from the real workflow YAML; promote into `scripts/` + `tests/test_*.py`; keep prototypes
> in `staging/`; do not broaden `pytest.ini` to collect `staging/`.
>
> **6 · Image provenance + two operational questions** — the authorized set plus
> `scripts/deploy.sh:9`. **Separately:** *"Are `scripts/deploy.sh`, `scripts/blue-green-deploy.sh`,
> `infra/deploy.sh` and `ecs-task-definition-full.json` operationally live, and is ECR tag
> immutability enabled?"* None is answerable from the repository (§5.3, §5.4).
>
> **7 · Remaining** — Bandit parity (parity test before any suppression); D7-1 (only the three
> `load_decrypted_keys` callers); D6-D before D7-2; W-6 classification-only with no retry; tenant
> isolation on disposable non-owner services, **FORCE RLS not first**.

---

## 8. Prohibited-operation confirmation

**None occurred.** No stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase,
branch switch, history rewrite, or worktree change. No CI dispatch, PR, branch-protection or
repository-settings change. No publish, deploy, rollback, or mutation of ECR, S3, CloudFront, DNS,
IAM, ECS or Kubernetes. No `kubectl` executed. No `terraform apply`, no state change. No production
database or role change. No live trade or real order. No credential, secret file, token, DSN,
decrypted key or secret value was opened or printed.

No gate weakened, no threshold lowered, no test removed, no exclusion broadened, no blanket
suppression added, no skipped check reinterpreted as a pass, no report fabricated, and no
unavailable check claimed as passing.

**Preserved:** both concurrent modifications (hashes in §1), all untracked artifacts, every prior
report, both staging prototypes, 2 stashes, 1 worktree.

---

## 9. Release readiness

**DO NOT RELEASE.** Grounded in evidence, not intent:

1. **The correctness workflow can still deadlock** — `01 PR Check` is `pull_request`-only while the
   release path is push-triggered, and both verifiers demand its evidence (§4).
2. **Frontend publication has no commit-bound authorization**, and it already published `ecd23a9e`
   while `05 Security` was red.
3. **Security scans are unaccounted for** — the second IaC root, which declares secret
   infrastructure, has never been scanned; SAST evidence is discarded on failure; Safety is both
   skipped and non-blocking.
4. **Deployment identity is mutable or unresolved** — a build-side tag-as-digest fallback, three
   `latest`-defaulting or `latest`-pinned routes, and **ECR immutability UNKNOWN**.
5. **Tenant isolation evidence is absent** — PostgreSQL unavailable; matrix **NOT RUN**.

What *is* working, and was not weakened: the backend ECS deploy gate (SHA-pinned, digest-refusing,
and it declined this commit), the frontend artifact-size floor (**8/8 passing**), and
`blue-green-deploy.sh`'s empty-tag refusal.

| Final posture | |
|---|---|
| Release | **DO NOT RELEASE · DEPLOYMENT BLOCKED** |
| Tests executed this phase | **280 passed · 5 skipped · 1 xfailed · 4 suites · exit 0 throughout** |
| Governed files changed | **0** — no grant |
| Trivy / Terraform / Docker / PostgreSQL / Redis | **NOT RUN — absent** |
| **IMAGE PROVENANCE** | **NOT ESTABLISHED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
