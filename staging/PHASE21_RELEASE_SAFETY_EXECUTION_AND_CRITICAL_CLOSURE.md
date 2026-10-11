# Phase 21 — Release Safety Execution, Gate Hardening, Parallel Critical-Path Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Distinct from the concurrent
`PHASE21_RELEASE_GATE_IMPLEMENTATION_AND_CRITICAL_CLOSURE.md`; nothing overwritten.

> ## DISPOSITION: **BLOCKED** · **DO NOT RELEASE**
> **0 files modified.** No implementation grant exists for lanes A–L.
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**

Differential report. Phases 16–20 are cited, not restated. Phase 21 contributes **two
source-verified results that did not previously exist**:

| # | New this phase | Lane | Severity |
|---|---|---|---|
| 1 | **A second fail-open release path: `02 Build` publishes the mutable `:latest` tag before any gate runs, and `:latest` is consumed by 6 Terraform task definitions and by the deploy script's default** | **D** | **CRITICAL — new** |
| 2 | **Review of the concurrent Phase 17 gate prototype: sound and stricter than the production verifier, with one reachable gap — it gates on run-level conclusion, so a conditionally-skipped mandatory job is admitted** | **A/C** | **HIGH — new** |

---

## 1. Entry and exit baseline

`VER` Byte-identical at entry and exit.

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| `origin/main` / divergence | `ecd23a9e…` / **0 / 0** |
| latest reflog | `ecd23a9e commit: launch phase 1` |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — entry and exit |
| staged / unstaged / unmerged | **0 / 2 / 0** |
| stash / worktrees | **2** / **1** |

**Concurrent work preserved byte-for-byte:** `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`
(`0c9981b2…`), `tests/test_drift_classification.py` (`99a2917a…`). Neither staged, edited nor
reverted.

All governed targets `clean` at exit, matching Phases 16–20: `06-frontend-deploy.yml`
`285cc2d1…`, `01-pr-check.yml` `91d55a37…`, `03-deploy.yml` `eb8078ea…`, `05-security.yml`
`e2d9ccc6…`, `require_security_scan.py` `a1c8dd2d…`, the three B608 migrations, `market.py`
`16f0377c…`, `orders.py` `ff2d34bf…`, `exchange.py` `4b409735…`, `portfolio_cache_updater.py`
`a74d7644…`, `exchange_executor.py` `df4fd42a…`, Terraform, and the W1D ledger `7e238620…`
(`:802` still `NOT GRANTED`).

### 1.1 Authorization matrix

| Lane | Targets | Status |
|---|---|---|
| A frontend gate | `06-frontend-deploy.yml` + test | **NOT GRANTED** |
| B correctness route | `01-pr-check.yml` / branch protection | **OWNER DECISION REQUIRED** |
| C verifier | `scripts/require_security_scan.py` | **NOT GRANTED** |
| **D build/image provenance** | `02-build.yml`, `ecs.tf`, `ecs_deploy_and_diagnose.py` | **NOT GRANTED** |
| E Bandit | 3 migrations, gate test, `test_perf.py` | **NOT GRANTED** |
| F D7-1 | `market.py`, `orders.py` + tests | **NOT GRANTED** |
| G D7-2 | `portfolio_cache_updater.py` + test | **NOT GRANTED** |
| H W-6 | `exchange_executor.py` + tests | **NOT GRANTED** |
| I tenant / RLS | — | **NOT GRANTED** + **BLOCKED BY ENVIRONMENT** |
| J Redis / quota / workers | — | **NOT GRANTED** + **BLOCKED BY ENVIRONMENT** |
| K Terraform / Trivy | Terraform | **NOT GRANTED**; scanner **BLOCKED BY ENVIRONMENT** |

`VER` W1D ledger `:800-804` — all five `NOT GRANTED`; no row covers a workflow, Terraform,
migration, router, script or test. Nothing in this prompt, a prototype, a report, repository write
access, or a passing test was treated as authorization.

---

## 2. Lane D — a second fail-open release path (NEW, CRITICAL)

Nobody had examined `02-build.yml`. `VER` Read this phase.

### 2.1 The build publishes a mutable tag before any gate exists

`.github/workflows/02-build.yml`, `IMAGE_TAG: ${{ github.sha }}` (`:69`), then `:72-76`:

```bash
docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG \
             -t $ECR_REGISTRY/$ECR_REPOSITORY:latest -f Dockerfile .
docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
```

`02 Build` is **`push`-triggered with no gate** — the security gate lives downstream in
`03 Deploy`, reached by `workflow_run`. So **`:latest` in ECR is advanced by every push to `main`
regardless of whether the security scan subsequently passes.**

On the current candidate that has already happened: `02 Build` (run 38129699471) **succeeded** on
`ecd23a9e`, while `05 Security` **failed** and `01 PR Check` never ran. **ECR's `:latest` for
`vyomquant-api` therefore points at an image built from a commit with a red security gate and zero
correctness evidence.**

### 2.2 `:latest` is consumed — in two independent places

`VER` This is what makes it exploitable rather than cosmetic.

| Consumer | Site | Detail |
|---|---|---|
| **`scripts/ecs_deploy_and_diagnose.py:94`** | `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")` | **Defaults to `latest`.** `:901` builds `image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"`. If `IMAGE_TAG` is ever unset, the deploy **silently** targets the ungated image — no error, no warning |
| **`terraform/ecs.tf`** | `:28`, `:126`, `:198`, `:284`, `:378`, `:478` | **Six** task definitions hardcode `:latest` — `vyomquant-api`, `-tee`, `-mds`, `-websocket`, `-simulator`, and the worker family (`:478`, *"Workers use the backend image"*) |

The gated path is correct: `03-deploy.yml` passes
`IMAGE_TAG: ${{ github.event.workflow_run.head_sha || github.sha }}`, so a normal gated deploy
uses the SHA tag. **The hazard is every path that is not that one** — an unset `IMAGE_TAG`, a
Terraform-driven service recreation, a task launched outside the script, or a DR redeploy.

> **This is structurally worse than the frontend gap.** The frontend lane has *no* gate; this lane
> has a gate that can be **silently bypassed by a missing environment variable defaulting to the
> ungated artifact.** A fail-open default is harder to notice than an absent control.

### 2.3 Digest provenance degrades silently

`02-build.yml:78`:

```bash
DIGEST=$(aws ecr describe-images ... --query 'imageDetails[0].imageDigest' --output text \
         2>/dev/null || echo "$IMAGE_TAG")
```

On **any** failure — permissions, throttling, eventual consistency — it substitutes **the tag** for
the digest and still emits `image_digest=$DIGEST` as a workflow output. A consumer believing it
holds an immutable digest may hold a mutable tag, with `2>/dev/null` discarding the reason. Digest
pinning is the one mechanism that makes "validated image == deployed image" checkable, and here it
can fail open without a signal.

### 2.4 One precision limit I will not overclaim

`terraform/ecs.tf:28` reads `image = "vyomquant-api:latest" # ECR URL goes here` — **no registry
prefix**, so as written these are not fully-qualified ECR URIs. Together with the deploy script's
documented behaviour of fetching and mutating the **running** task definition, that suggests
`ecs.tf` may not be the live source of truth. **I cannot verify the applied infrastructure state**
(no AWS access, and none authorized), so I record `ecs.tf` as a **latent** hazard and make no claim
about what is currently deployed.

> **P21-D1 — owner request (NOT applied).** Stop publishing `:latest` from `02 Build`; publish the
> immutable `:$github.sha` only, and promote `:latest` (or a `:release` alias) in a job that
> `needs:` the security gate. Make `ecs_deploy_and_diagnose.py` **fail closed** when `IMAGE_TAG` is
> unset rather than defaulting to `latest`. Remove the `|| echo "$IMAGE_TAG"` digest fallback so a
> digest lookup failure refuses instead of degrading. Pin the six `ecs.tf` definitions to a
> variable, not `:latest`. Owners: release/workflow owner (`02-build.yml`, deploy script) +
> infrastructure owner (`ecs.tf`). **No image was deleted, promoted, retagged or deployed.**

---

## 3. Lanes A and C — review of the concurrent gate prototype

`VER` `staging/phase17_frontend_release_gate.py` and `phase17_test_frontend_release_gate.py`
exist (with `.pyc`, so the suite has been executed by that session). The brief asks me to
**validate the prototype**, which is review, not duplication. It is a **staging prototype, not
production** — `06-frontend-deploy.yml` remains `clean` and ungated.

**It is well-built, and on several axes stricter than the production verifier:**

| Property | Prototype | `require_security_scan.py` |
|---|---|---|
| Required workflows | **N** — `("05 Security", "01 PR Check")`, AND enforced in-job | 1, module constant |
| **Pagination** | **Refusal condition** — refuses if `total_count != len(runs)` *or* `len(runs) >= 100`: *"the newest run for a required workflow may be unseen, so this cannot be decided"* | **unhandled** (my Phase 16 gap G3) |
| `head_sha` equality | **asserted** — "defence against API filtering drift" | implicit in the query |
| Branch identity | `ALLOWED_BRANCHES` + `head_branch` check | none |
| Empty policy | **refuses** — *"an empty policy is a misconfiguration, not a pass"* | n/a |
| Override | **none, deliberately** | `ALLOW_UNSCANNED` |
| Non-success conclusions | enumerated `_REFUSING_CONCLUSIONS`, then gated on `conclusion != "success"` — positive test, so a *new* GitHub conclusion value fails closed | similar |

**The brief's specific question — can a newer failure be masked by an older success? No.**
`_newest_run_for` selects `sorted(matching, key=lambda r: r.get("run_started_at") or "")[-1]`, so
the **newest** run is the one judged; an older success cannot shadow a newer failure. Handled
correctly in both the prototype and the production verifier.

### 3.1 The one gap, and it is reachable in normal operation

**The prototype gates on the run-level `conclusion` only. It performs no required-*job* check.**
This is my Phase 16 gap **G2**, and it is not theoretical here:

> Phase 18 established that `01 PR Check`'s `frontend-tests` job (*"Frontend Build & Unit Tests"*,
> `:695`) is **conditional on `steps.changes.outputs.frontend == 'true'`**.
>
> So for a **backend-only** commit, `01 PR Check` concludes **`success`** with the frontend tests
> **skipped** — and this gate would admit that run as frontend correctness evidence. A green gate,
> zero frontend tests executed.

Closing it needs a second API call per run — `/actions/runs/{id}/jobs`, itself paginated —
asserting each **named mandatory job** has `conclusion == "success"`, with `skipped` refused.

**Also noted:** `REQUIRED_WORKFLOWS` includes `"01 PR Check"`, which **cannot run on a direct
push**. So adopting this prototype as-is would refuse *every* push-built candidate — correct
fail-closed behaviour, but it makes Lane B's route decision a **hard prerequisite**, not a
parallel nicety. That dependency is exactly what §4 records.

**I did not copy the prototype into production** (no grant), modify it, or re-run its suite (the
owning session's `.pyc` and `staging/phase17_gate_tests.txt` record its results; re-running would
duplicate another session's evidence).

---

## 4. Lanes B, E–K — status, cited

All governing hashes identical → **REUSED FROM IDENTICAL HASH**.

| Lane | State | Key fact |
|---|---|---|
| **B** correctness route | **OWNER DECISION REQUIRED** | `01 PR Check` is `pull_request`-only (`push: 0`, `workflow_dispatch: 0`). **Prerequisite for A** (§3.1) |
| **E** Bandit | **DECISION-READY** | 9 × B608 unchanged; gate-test scope divergence unchanged. Phase 20 closed `test_perf.py`: local literals, stale `nosec`, **zero** gate effect — do not present its removal as progress |
| **F** D7-1 | **DECISION-READY** | Exactly **3** invalid `access_token=` — `market.py:53`, `orders.py:792`, `orders.py:1098`. **`exchange.py:993` CORRECT — unchanged, do not touch** |
| **G** D7-2 | **DECISION-READY** | Two independent errors: `key_manager` module absent (`ModuleNotFoundError`, executed) **and** class is `APIKeyVault` not `KeyVault` |
| **H** W-6 | **DECISION-READY** | `retryable`: 2 constructor writes + **1** log-field read (`:1687`), no control-flow consumer. **No retry added or implied** |
| **I** tenant / RLS | **BLOCKED BY ENVIRONMENT** | PostgreSQL 5432 closed. **FORCE RLS not enabled.** 33 `*postgres*.py`, **0 executed on the candidate**. **NOT CERTIFIED** |
| **J** Redis / quota / workers | **BLOCKED BY ENVIRONMENT** | Redis 6379 closed, 0 `REDIS*` vars, docker absent. In-process quota passes do **not** prove Redis atomicity. W-4 UNKNOWN-exit **not invented**; W-5 blocked |
| **K** Terraform / Trivy | **DECISION-READY**, 0 of 5 fixed | Trivy **NOT RUN** — unavailable. Redis egress remains the only disposition safe on its own evidence; **fixing it does not close a gate with three other blocking findings** |

---

## 5. Commands run

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `hash-object` / `reflog -1` / `worktree list` / `stash list` | 0 | §1; `write-tree` `4b45a763…` entry **and** exit |
| `python staging/phase20_exit.py` (exit manifest) | 0 | all targets `clean` |
| greps: `02-build.yml` tagging; `ecs.tf` + deploy-script image resolution; prototype selection/pagination | 0 | §2, §3 |

**NOT RUN and not claimed:** pytest, Bandit, Trivy, Terraform `fmt`/`validate`, CI dispatch, PR
creation, deployment, database, Redis, exchange, AWS, network. Bandit and the drift suites were
deliberately not re-run — inputs byte-identical to Phases 15–20.

Artifacts: `staging/PHASE21_RELEASE_SAFETY_EXECUTION_AND_CRITICAL_CLOSURE.md` (this report).

---

## 6. Evidence classification

| Claim | Class |
|---|---|
| `02 Build` pushes `:latest` pre-gate; 6 `ecs.tf` consumers; deploy-script `latest` default; digest fallback | **VERIFIED** (source, this phase) |
| Prototype strictness, newest-run selection, pagination refusal, run-level-only gap | **VERIFIED** (source, this phase) |
| `01 PR Check` `frontend-tests` conditional skip | **REUSED FROM IDENTICAL HASH** (Phase 18) |
| 9 × B608; D7-1 census; D7-2; W-6 matrix; 5 Trivy findings | **REUSED FROM IDENTICAL HASH** |
| Applied AWS/ECS state; `:latest` current contents | **NOT RUN — no access, none authorized** |
| Correctness evidence on `ecd23a9e` | **NOT RUN — none exists** |
| Tenant isolation; real-Redis atomicity | **NOT CERTIFIED / BLOCKED** |

No skipped test, unavailable scanner, or mocked integration is reported as a pass. A passing
prototype suite does not certify a deployed release.

---

## 7. Owner decisions — smallest action each

| # | Blocker | Owner | Smallest action |
|---|---|---|---|
| **1** | **`:latest` published pre-gate and consumed by 6 task defs + a silent deploy default** | release/workflow + infrastructure | **P21-D1** (§2.4) — SHA-only push; promote `:latest` behind the gate; fail closed on unset `IMAGE_TAG`; drop the digest fallback |
| **2** | Frontend publishes with no gate | workflow / release | Grant the `security-gate` job + `needs:` from **every** publishing job |
| **3** | Correctness unreachable on push — **prerequisite for #2** | coordinator + release | PR-only advancement of `main`, **or** grant `push: [main]` on `01-pr-check.yml` |
| **4** | Gate admits conditionally-skipped mandatory jobs | workflow owner | Add required-**job** verification (§3.1) to whichever verifier ships |
| **5** | `05 Security` RED | code + infrastructure | Bandit: widen gate test first, then 9 annotations. Trivy: 2 Redis removals + 3 documented accepts |
| 6–10 | D7-1 · D7-2 · W-6 · TENANT-01 · A23 Redis / W1C-02 / D5 / ledger `:802` | respective owners | Per Phase 20 §8 — unchanged |

**Highest leverage: #3 then #1.** #3 unblocks every evidence claim and is a prerequisite for #2.
#1 is the newly-found fail-open path and is independent of all gate work.

---

## 8. Final posture

| Item | Status |
|---|---|
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| Backend deployment | **BLOCKED** — security gate RED; plus the `:latest` bypass (§2) |
| Frontend deployment | **BLOCKED** — no gate; already published ungated |
| Production infrastructure | **NOT AUTHORIZED** |
| Correctness on `ecd23a9e` | **UNVERIFIED** |
| Files modified | **0** source · **0** test · **0** workflow · **0** Terraform · **0** script · **0** governance |
| **Overall** | **DO NOT RELEASE** |

No git write command (no stage, commit, push, merge, rebase, reset, checkout, stash, clean, revert,
branch switch, history rewrite). No CI dispatch or PR. No deploy, rollback, CDN purge, DNS change,
`terraform apply`, infrastructure provisioning, image retag/promote/delete, production database or
Redis mutation, FORCE RLS, exchange credential use, live order, or live-trading change. No secret,
token, DSN or decrypted key material read or printed. No scanner threshold weakened, no finding
hidden, no skipped test converted to a pass. No concurrent report, log or artifact overwritten.

**Continuation point.** Lanes A, C, D, E, F, G, H, K are **decision-ready** with exact file lists;
Lane B is the gating **owner decision**; Lanes I and J are **environment-blocked**. The two items
needing fresh work next phase, both newly surfaced here: required-**job** verification (§3.1) and
the `:latest` promotion split (§2.4). No lane requires repeating the investigation.
