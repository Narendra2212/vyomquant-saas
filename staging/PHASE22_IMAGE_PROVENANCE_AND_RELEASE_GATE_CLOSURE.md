# Phase 22 — Image Provenance Enforcement, Required-Job Release Gates, Critical-Path Execution

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Nothing overwritten.

> ## DISPOSITION: **DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · DEPLOYMENT BLOCKED**
> **0 files modified.** No implementation grant exists for lanes A–J.

Differential report. Phase 21 is not restated. Phase 22 contributes **one complete source-verified
finding chain** and **one correction to my own Phase 21 emphasis**.

| # | New this phase | Severity |
|---|---|---|
| 1 | **The digest-provenance machinery is structurally unreachable** — nothing invokes `02-build.yml` via `workflow_call`, and `workflow_run` does not carry job outputs, so `image_digest` is computed and discarded | **HIGH — new** |
| 2 | **ECR tag immutability is declared nowhere** — no `aws_ecr_repository` / `image_tag_mutability` in the Terraform root, and ECR defaults to MUTABLE. Combined with #1, validation is tag-based on tags not provably immutable | **HIGH — new** |
| 3 | **A fourth mutable-tag consumer: `ecs-task-definition-full.json:18`** pins a fully-qualified `…/vyomquant-api:latest` | **MEDIUM — new** |
| 4 | **Correction:** `03-deploy.yml`'s digest handling is **fail-closed and correct**; only `02-build.yml`'s is fail-open. Phase 21 implied the weakness was general | **correction** |

---

## 1. Entry and exit manifest

`VER` Byte-identical at entry and exit.

| Measurement | Value |
|---|---|
| `HEAD` / divergence | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** / **0 / 0** |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — unchanged |
| staged / unstaged / unmerged | **0 / 2 / 0** |
| stash | **2** — neither inspected, applied, dropped, popped nor rewritten |

**Concurrent work preserved byte-for-byte:** `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`
(`0c9981b2…`), `tests/test_drift_classification.py` (`99a2917a…`).

### 1.1 Authorization matrix

| Lane | Targets | Decision ID | Status |
|---|---|---|---|
| **A** image provenance | `02-build.yml`, `ecs_deploy_and_diagnose.py`, `ecs.tf`, `ecs-task-definition-full.json` | **P21-D1 / P22-A1** | **NOT GRANTED** |
| **B** verifier required-jobs | `scripts/require_security_scan.py` | **P22-B1** | **NOT GRANTED** |
| **C** correctness route | `01-pr-check.yml` / branch protection | **P16-B1/B2** | **OWNER DECISION REQUIRED** |
| **D** frontend gate | `06-frontend-deploy.yml` + test | **P16-A1…A4** | **NOT GRANTED** |
| **E** Bandit | 3 migrations + gate test | — | **NOT GRANTED** |
| **F** D7-1 | `market.py`, `orders.py` + tests | **P17-C1** | **NOT GRANTED** |
| **G** D7-2 | `portfolio_cache_updater.py` + test | **P17-G1** | **NOT GRANTED** |
| **H** W-6 | `exchange_executor.py` + tests | — | **NOT GRANTED** |
| **I** Terraform / Trivy | Terraform roots | — | **NOT GRANTED**; scanner **BLOCKED BY ENVIRONMENT** |
| **J** tenant / Redis | — | — | **NOT GRANTED** + **BLOCKED BY ENVIRONMENT** |

`VER` W1D ledger `:800-804` all `NOT GRANTED`; no row covers a workflow, script, Terraform file,
JSON task definition, migration, router or test. Phase instructions and technical findings are not
grants, and none was treated as one.

---

## 2. Lane A — the image lifecycle, traced end to end

### 2.1 The chain, from source SHA to running container

```
source commit ecd23a9e
   │
   ├─ 02 Build  (on: push — NO GATE)                    .github/workflows/02-build.yml
   │    IMAGE_TAG = github.sha
   │    docker build -t …:$IMAGE_TAG -t …:latest        :72
   │    docker push …:$IMAGE_TAG                        :75
   │    docker push …:latest          ◄── MUTABLE ALIAS ADVANCED PRE-GATE   :76
   │    DIGEST=$(aws ecr describe-images … || echo "$IMAGE_TAG")   ◄── tag-as-digest  :78
   │    outputs: image_tag, image_digest   ◄── DISCARDED (§2.2)    :80-81
   │
   └─ 03 Deploy (on: workflow_run["02 Build"])          .github/workflows/03-deploy.yml
        pre-deployment-validation
          require_security_scan.py  ?head_sha=<DEPLOY_SHA>   ◄── correct, SHA-pinned
        ecs-deployment  [needs: gate]
          IMAGE_TAG = workflow_run.head_sha || github.sha    :165,:179
          DIGEST=$(… || echo "")  ; [ -z ] || [ = None ] → exit 1  ◄── FAIL-CLOSED  :168-174
          python scripts/ecs_deploy_and_diagnose.py
            IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")   ◄── FAIL-OPEN DEFAULT  :94
            image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"  ◄── TAG, not digest  :901
            → fetches the RUNNING task definition, mutates ONLY the image
```

### 2.2 The digest machinery is structurally unreachable (NEW)

`VER` `02-build.yml` declares `on: workflow_call:` with outputs `image_tag` (`:7-9`) and
`image_digest` (`:10-12`), surfaced from `jobs.build-push.outputs`.

**Nothing invokes it.** A search for `uses: ./.github/workflows` across every workflow file
returns **zero** matches. `03-deploy.yml` chains via `workflow_run`, and **a `workflow_run` payload
does not carry the upstream job outputs.**

> **So `image_digest` is computed on every build and then discarded.** The provenance output exists,
> is never read, and therefore cannot be verified against anything. This also explains why the
> fail-open `|| echo "$IMAGE_TAG"` at `:78` has gone unnoticed for so long — no consumer ever sees
> the value it corrupts.

**Consequence:** the validated-image chain is **tag-based end to end**. No digest is carried from
build to validation to deployment.

### 2.3 Tag immutability is declared nowhere (NEW)

`VER` No `aws_ecr_repository` resource and no `image_tag_mutability` setting appears anywhere in
the `terraform/` root. The repository therefore contains **no evidence that `:$github.sha` tags
cannot be overwritten**, and ECR's default repository setting is `MUTABLE`.

Combined with §2.2:

> **"The validated image equals the deployed image" is not establishable from this repository.**
> `03-deploy.yml` resolves a digest purely to prove the tag *exists*, logs it, and then
> `ecs_deploy_and_diagnose.py` deploys the **tag** (`:901`). Between verification and the ECS pull
> the tag may resolve elsewhere, and nothing in-tree constrains that. This is a stronger statement
> than Phase 21's: the gap is not imperfect pinning, it is absent pinning.

I cannot inspect the live ECR repository setting (no AWS access, none authorized), so this is a
**source-configuration** finding, not a registry-state finding — see §6.

### 2.4 Mutable-tag consumer census — 4 sources, 9 references

| # | Consumer | Site | Note |
|---|---|---|---|
| 1-6 | `terraform/ecs.tf` | `:28`, `:126`, `:198`, `:284`, `:378`, `:478` | 6 task definitions; **not** fully-qualified URIs (`# ECR URL goes here`) |
| 7 | `scripts/ecs_deploy_and_diagnose.py` | `:94` | `os.getenv("IMAGE_TAG", "latest")` — **silent fail-open default** |
| **8** | **`ecs-task-definition-full.json`** | **`:18`** | **NEW** — `273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:latest`, **fully qualified**, so directly usable |
| 9 | `ecs-task-definition-full.json` | `:211` | `questdb/questdb:latest` — third-party sidecar, different risk class |

**Which definition is authoritative?** `VER` Answered from the deploy script's own documentation:
it **fetches the running task definition and mutates only the container image**, explicitly to stop
*"stale configuration from ecs-task-definition-full.json from overwriting production settings"*.
So the JSON is a **seed/fallback, not the live source** — but it is fully qualified and pins
`:latest`, so any path that registers from it pins the mutable alias. `ecs.tf`'s six references
lack a registry prefix, which suggests that root is not what created the live services.

**I make no claim about the deployed state.** Terraform source, the JSON, Terraform state and live
ECS are four distinct evidence classes (§6).

### 2.5 Correction to Phase 21

Phase 21 highlighted the `|| echo "$IMAGE_TAG"` digest fallback without distinguishing the two
call sites. `VER` They differ, and one is correct:

| Workflow | Fallback | Behaviour |
|---|---|---|
| `02-build.yml:78` | `\|\| echo "$IMAGE_TAG"` | **FAIL-OPEN** — substitutes the tag for the digest |
| `03-deploy.yml:168-174` | `\|\| echo ""` then `[ -z "$DIGEST" ] \|\| [ "$DIGEST" = "None" ] → exit 1` | **FAIL-CLOSED** — *"Refusing to deploy untested or missing build artifact."* |

**The deploy-side handling is right and should not be changed.** Only the build-side fallback is
defective — and currently inert because of §2.2. Stating this precisely matters: a remediation that
"fixed" both would risk weakening a working control.

### 2.6 P22-A1 — owner request (NOT applied)

1. `02-build.yml`: push **`:$github.sha` only**; remove the `:latest` tag and push.
2. Add a **promotion job** that advances `:latest`/`:release` and `needs:` the security gate,
   requiring success for that exact SHA.
3. `02-build.yml:78`: delete `|| echo "$IMAGE_TAG"`; fail the step when the digest is unresolved or
   malformed. **Leave `03-deploy.yml:168-174` alone — it is already fail-closed.**
4. `ecs_deploy_and_diagnose.py:94`: **reject** unset/empty/invalid `IMAGE_TAG` instead of
   defaulting to `latest`; prefer deploying by **digest** (`…@sha256:…`) rather than tag (`:901`).
5. Declare the ECR repository with `image_tag_mutability = "IMMUTABLE"`, or carry the digest
   through so mutability stops mattering.
6. Replace `:latest` in the 6 `ecs.tf` definitions and `ecs-task-definition-full.json:18` with the
   approved image-selection contract.

Owners: release/workflow owner (1–4) + infrastructure owner (5–6). **No image was built, pushed,
pulled, retagged, promoted or deleted; no Terraform applied; no deployment performed.**

---

## 3. Lane B — required-job evidence

`require_security_scan.py` unchanged (`a1c8dd2d…`). Its run-level-only gate stands, as does the
staging prototype's identical limitation (Phase 21 §3.1). The reachable hole is unchanged and
remains the key defect: `01 PR Check`'s `frontend-tests` job is conditional on
`steps.changes.outputs.frontend == 'true'`, so a backend-only commit yields run-level `success`
with the mandatory frontend tests **skipped**, which a run-level gate admits as correctness
evidence.

> **P22-B1 (NOT applied).** Add paginated `/actions/runs/{id}/jobs` verification asserting each
> owner-approved mandatory job concludes `success`, with `skipped` / `cancelled` / `failure` /
> missing / ambiguous-duplicate all refusing. Preserve newest-run selection (a newer failure
> already overrides an older success — verified Phase 21) and every existing fail-closed path. Add
> a negative control proving the test fails if the job-level gate is removed or a skipped mandatory
> job is admitted.

**The prototype suite was not run.** It lives under `staging/` and is **not collected by the
project's default test command** — a point the brief raises and that matters: those 42 cases are
not regression protection until the implementation and its tests live under `tests/`.

---

## 4. Lanes C–J — status

| Lane | State | Note |
|---|---|---|
| **C** correctness route | **OWNER DECISION REQUIRED** | `01 PR Check` still `pull_request`-only. **Prerequisite for D**, because the prototype's `REQUIRED_WORKFLOWS` includes a workflow that cannot run on a push |
| **D** frontend gate | **NOT GRANTED** | `06-frontend-deploy.yml` unchanged, still ungated; publication correctly left blocked rather than half-gated |
| **E** Bandit | **NOT GRANTED** | 9 × B608 unchanged; gate test scope divergence unchanged. Per the brief, stale `nosec` cleanup **not** pursued |
| **F** D7-1 | **NOT GRANTED** | 3 invalid `access_token=` — `market.py:53`, `orders.py:792`, `:1098`. **`exchange.py:993` correct — unchanged, untouched** |
| **G** D7-2 | **NOT GRANTED** | `key_manager` absent + class is `APIKeyVault`. Two errors; a path-only fix still fails |
| **H** W-6 | **NOT GRANTED** | `retryable` = 2 writes + 1 log read. **No retry introduced or implied** |
| **I** Terraform / Trivy | **NOT GRANTED** | Trivy **NOT RUN** — unavailable. 5 findings outstanding; Redis egress alone does not close the gate |
| **J** tenant / Redis | **BLOCKED BY ENVIRONMENT** | PostgreSQL 5432 and Redis 6379 closed; docker absent. **FORCE RLS not enabled.** Stashes untouched. W-4 not invented; W-5 blocked |

---

## 5. Commands run

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `stash list` | 0 | §1 |
| grep `uses: ./.github/workflows` across all workflows | 0 | **zero matches** → §2.2 |
| grep `aws_ecr_repository\|image_tag_mutability` in `terraform/*.tf` | 0 | **zero matches** → §2.3 |
| grep image refs in `ecs-task-definition-full.json`, `03-deploy.yml` | 0 | §2.4, §2.5 |
| existence checks: `ecs-task-definition*.json` | 0 | `-full.json` present |

**NOT RUN and not claimed:** pytest, the staging prototype suite, Bandit, Trivy, Terraform
`fmt`/`validate`, CI dispatch, PR creation, AWS/ECR/ECS query, deployment, database, Redis,
network. Expensive scans were not repeated — all governing hashes are byte-identical to Phases
15–21.

---

## 6. Evidence classes, kept separate

| Class | Status |
|---|---|
| **Source configuration** | **VERIFIED** this phase — §2.2, §2.3, §2.4, §2.5 |
| **Registry (ECR) state** — actual `:latest` contents, tag mutability setting | **NOT RUN** — no AWS access, none authorized |
| **Terraform state** — which root owns the live services | **NOT RUN** — no state access |
| **Live cloud (ECS) state** — running task definition and image | **NOT RUN** — not authorized |
| Unit / mock evidence | none executed this phase |
| Integration evidence | **BLOCKED** — PostgreSQL, Redis, docker all absent |
| **Release certification** | **NONE** |

No skipped test, unavailable scanner or mocked check is reported as a pass. Source-level
verification is **not** evidence of registry or cloud state, and is not presented as such.

---

## 7. Owner decisions — smallest action each

| # | Blocker | Owner | Smallest action |
|---|---|---|---|
| **1** | Correctness route unresolved — **gates everything** | coordinator + release | PR-only advancement of `main`, **or** grant `push: [main]` on `01-pr-check.yml` |
| **2** | `:latest` advances pre-gate; 9 references; deploy defaults to it; digest output discarded; immutability undeclared | release/workflow + infrastructure | **P22-A1** (§2.6) — note item 4: leave `03-deploy.yml` alone |
| **3** | Gate admits conditionally-skipped mandatory jobs | workflow owner | **P22-B1** (§3) |
| **4** | Frontend publishes ungated | workflow / release | Grant the gate + `needs:` from every publishing path (after #1) |
| **5** | `05 Security` RED | code + infrastructure | Bandit: widen gate test, then 9 annotations. Trivy: 2 Redis removals + 3 documented accepts |
| 6–9 | D7-1 · D7-2 · W-6 · TENANT-01 / A23 / W1C-02 / ledger `:802` | respective owners | Unchanged from Phase 20 §8 |

**Highest leverage: #1, then #2.** #1 unblocks every evidence claim and is a hard prerequisite for
#3 and #4. #2 is independent of all gate work and is the fail-open path most likely to be exercised
accidentally.

---

## 8. Final diff review and posture

`VER` **Files changed: 0.** Source · test · workflow · Terraform · JSON · script · migration ·
governance — all zero. `git write-tree` identical at entry and exit, so the tracked tree did not
move. Both pre-existing modifications byte-identical. Both stashes untouched.

No git write command (no stage, commit, push, merge, rebase, reset, checkout, clean, revert,
history rewrite, stash apply/drop/pop). No CI dispatch, no PR. No deploy, rollback, CDN purge, DNS
change, `terraform apply`, infrastructure provisioning, image build/push/pull/retag/promote/delete,
production database or Redis mutation, FORCE RLS, exchange credential use, live order, or
live-trading change. No secret, token, DSN or decrypted key material read or printed. No scanner
threshold weakened, no finding hidden, no skipped test converted to a pass. No concurrent report,
prototype, log or artifact overwritten.

### DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · DEPLOYMENT BLOCKED

Every one of the brief's disqualifying conditions is present: image provenance is **not**
fail-closed (§2), mandatory job evidence is **missing** (§3), the correctness route is
**unresolved** (§4), security gates are **red**, and tenant isolation is **unverified**.

**Continuation point.** Newly surfaced here and needing action next: the unreachable digest output
(§2.2), undeclared tag immutability (§2.3), and the fourth `:latest` consumer (§2.4). Lanes A–I are
decision-ready with exact paths; Lane C is the gating decision; Lane J is environment-blocked. The
staging prototype must move under `tests/` to become regression protection (§3). No lane requires
repeating the investigation.
