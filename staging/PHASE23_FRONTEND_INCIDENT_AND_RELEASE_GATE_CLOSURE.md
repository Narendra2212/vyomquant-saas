# Phase 23 — Frontend Release Incident Response, Exact-SHA Gate Repair, Critical Dependency Closure

**Destination verified ABSENT before writing.** **0 production files · 0 test files ·
0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 deployments · 0 stashes mutated.**
1 artifact written: this file.

> ## DISPOSITION: **DO NOT RELEASE · INCIDENT OPEN**
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED · DEPLOYMENT: BLOCKED**

---

## 1. Incident summary — scope is wider than one event, and the affected build is still live

Phase 22 established that one frontend publication succeeded at a SHA whose security scan
failed. This phase determined the **scope**, and three facts change the severity.

> ### 1. The failed-security build is the CURRENT production frontend.
> `ecd23a9e` is the **most recent** `06 Frontend Deploy` run of all 60 returned. **Nothing
> superseded it.** The assets published at `2026-10-11T09:50:13Z` are what S3 serves today.
>
> ### 2. It is not an isolated event. **2 of the 14 most recent successful publications shipped at a SHA whose security scan failed.**
> | Published | SHA | Frontend | `05 Security` at same SHA |
> |---|---|---|---|
> | `2026-10-11T09:48:45Z` | **`ecd23a9e`** | success | **failure, failure** ← **current production** |
> | `2026-10-05T17:46:29Z` | `6c5660a1` | success | success, success |
> | `2026-10-04T09:40:39Z` | **`eee73265`** | success | **failure, failure** |
> | 11 earlier runs | … | success | success |
>
> ### 3. The other 12 passed by coincidence, not by control.
> All 60 runs are `push`-triggered with **`needs:` count = 0**. Nothing ever gated them. The
> 12 clean publications are clean because their SHAs happened to be green, not because
> anything checked.

### 1.1 Confirmed facts vs unknowns

**Confirmed (read-only CI evidence, `gh run view 38129699497`):**

- Published SHA: `ecd23a9e934ec58a7b1f0e5db638da8e865e8187`.
- Run that performed publication: **`38129699497`**, conclusion `success`,
  `2026-10-11T09:48:45Z` → `09:50:13Z`.
- Step 11 `Configure AWS Credentials` → success; **step 12 `Sync build artifacts to S3` →
  success**; **step 13 `Invalidate CloudFront cache` → success**; steps 14–15 verification →
  success.
- `05 Security` at the same SHA, created `09:48:44Z` — **one second earlier** — concluded
  **failure**. Both ran concurrently; nothing made the frontend wait.
- `03 Deploy` concluded **failure** → the **backend** gate refused. No ECS deployment.
- `01 PR Check` **did not run** at this SHA.
- No later `06 Frontend Deploy` run exists.

**Explicitly NOT established:**

- **The exact bytes currently served to every client.** A successful `s3 sync` and a successful
  `create-invalidation` prove the publication steps ran. They do not prove what any given edge
  location returns now, nor that no other process has modified the bucket since.
- **Whether the published frontend contains an exploitable defect.** `05 Security` scans the
  **container image, Python dependencies and Terraform** — Bandit on `backend_app`, Trivy on
  the image and IaC. **It does not scan the frontend bundle.** So its failure is not evidence
  of a frontend vulnerability, and its passing would not have been evidence of frontend
  safety. This materially limits the blast-radius claim in both directions.
- **Build artifact identity.** Step 10 `Record build artifact metadata` ran, but its content was
  not retrieved; no immutable frontend artifact digest is established in the repository.

### 1.2 Containment / rollback decision — PREPARED, NOT AUTHORIZED

No rollback, republish, cache invalidation, DNS change or asset modification was performed.
**None is authorized**, and §1.1 shows the evidence needed to choose between options is partly
absent.

| Option | Supporting evidence | Operational risk |
|---|---|---|
| **A — No rollback; forward-fix only** | `05 Security` does not scan the frontend bundle (§1.1), so its red state is not evidence of a frontend defect. The build-time checks that *do* examine the bundle (artifact plausibility, Supabase config, API-host allow-list, CSP/header, WebSocket origin) **all passed**. | Leaves a build published without gate evidence in place. Accepts an unverified posture. |
| **B — Roll back to `6c5660a1`** (last publication whose SHA was fully green) | Known-green security at that SHA. | `6c5660a1` is 6 days older; reverts intervening frontend work. Requires an authorized S3 + CloudFront operation. May reintroduce fixed defects. |
| **C — Republish `ecd23a9e` after `05 Security` is green** | Preserves current functionality and obtains the missing evidence. | Requires Bandit (9 × B608) **and** Trivy IaC (5 CRITICAL) to be fixed first — both are themselves blocked on grants. Slowest path. |

**Smallest decision needed:** the release owner answers **one question** — *"Does the frontend
published at `ecd23a9e` require rollback, or is forward-fix acceptable given that `05 Security`
does not scan the frontend bundle?"* Everything else follows.

**The incident is NOT resolved.** A designed-but-unimplemented future gate does not close it.
Status: **OPEN**, affected build **LIVE**.

---

## 2. Entry and exit manifests

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Branch · `origin/main` · divergence | `main` · `ecd23a9e…` · **0/0** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees · stashes | 1 · **2** | 1 · **2 — untouched** |
| `git reflog -1` | `ecd23a9e HEAD@{0}: commit: launch phase 1` | **identical** |
| Porcelain | 116 | 117 (+1 = this report) |

`git write-tree` is byte-identical at entry and exit and to the value recorded in Phases 17,
21 and 22. **No tracked file has changed across Phases 17 → 23.**

### 2.1 Concurrent modifications — hashes recorded

| Path | Blob hash (entry = exit) | Disposition |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` | **not touched** |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | **not touched** |

### 2.2 Stashes — read-only, unchanged

`stash@{0}` = `filter-branch: rewrite` (a filter-branch backup, not a stash;
`refs/original/refs/stash` exists). `stash@{1}` = `99ceb9b0…`, 18 files, base `c673409`
**not an ancestor of HEAD** — orphaned pre-rewrite WIP. **Neither applied, dropped, popped,
renamed nor rewritten.**

---

## 3. Authorization matrix

| Lane | Target paths | Decision | Approver | Grant | Ops used |
|---|---|---|---|---|---|
| Incident response | S3 / CloudFront / DNS | — | release owner | **NOT GRANTED** | read-only CI queries |
| Frontend gate | `06-frontend-deploy.yml`, `tests/` | `D-P15-4` | workflow/release owner | **NOT GRANTED** (a *request*, `PHASE15:313-316`) | read-only |
| Correctness route | `01/02/03-*.yml`, branch protection | `D-P15-3` | release owner | **UNDECIDED** | read-only |
| Provenance chain | `02/03-*.yml`, `scripts/*`, `terraform/**` | — | release + infra owner | **NOT GRANTED** | read-only |
| Required-job verifier | `scripts/require_security_scan.py` | — | security owner | **NOT GRANTED** | read-only |
| A Bandit | gate test, 3 migrations | — | code owner | **NOT GRANTED** | read-only |
| B D7-1 | `routers/{orders,market}.py` | `D-13-1` | code owner | **NOT GRANTED** | read-only |
| C D6-D → D7-2 | `core/state.py`, updater | `D-13-5` | code owner + platform | **NOT GRANTED** | read-only |
| D W-6 | `backend/exchange_executor.py` | `D-13-2` | code owner | **NOT GRANTED** | read-only |
| E Terraform/Trivy | `terraform/**` | — | security + infra owner | **NOT GRANTED** | read-only |
| F tenant/Redis | `core/tenant_db_context.py` | `D-13-3/4` | code + DB owner | **NOT GRANTED** | read-only |
| G workers/migrations | `main.py`, snapshots | `AP-W1D-3/4` | code owner | **out of scope / NOT GRANTED** | read-only |

**Files actually changed: none.** `AP-W1D-3` remains confined to its own classifier files
(*"No other files are authorized"*). Workflow and script hashes are unchanged since Phase 22;
no authorization record changed.

---

## 4. The exact-SHA verifier — read for the first time, with two measured gaps

`scripts/require_security_scan.py` was cited by every prior phase and **inspected by none**.
Read in full this pass. It is **well-built**, and the following is not a criticism of its
fail-closed design — which is genuinely strong.

### 4.1 What it gets right (source-verified)

| Property | Implementation |
|---|---|
| Exact-SHA binding | `/actions/runs?head_sha=%s` — exact, not branch-relative |
| Missing scan = refuse | *"Nobody scanned this commit, which is not evidence that it is safe."* |
| `cancelled` / `skipped` = refuse | explicit branch → exit 1 |
| Non-success conclusion = refuse | explicit → exit 1 |
| API error = refuse | *"refusing rather than guessing"* |
| Still running past deadline = refuse | *"A deploy that outruns its own security scan is an ungated deploy."* |
| Newest-run selection | `sorted(runs, key=run_started_at)[-1]` |
| Waits for concurrency | 900 s default, 20 s poll |
| `ALLOW_UNSCANNED` scope | bypasses **only** the "no runs at all" case — **it cannot override a failed scan** |

Invocation is correct: `03-deploy.yml:93` `DEPLOY_SHA: ${{ github.event.workflow_run.head_sha
|| github.sha }}` — the triggering run's `head_sha`, not the default-branch SHA.

### 4.2 Gap 1 — job-level blindness. **LIVE and confirmed.**

The verifier reads only the **workflow** conclusion. It never inspects per-job conclusions.
Measured inside `05 Security` run `38132697285` at `ecd23a9e`:

| Job | Conclusion |
|---|---|
| `Trivy Container Vulnerability Scanner` | **success** |
| `Bandit SAST & Dependency Security Scan` | **failure** |
| `Trivy IaC Misconfiguration & Secret Scan` | **failure** |

**This makes the verifier's own documented rationale measurably false.** Its docstring states:

> *"`05-security.yml` already runs Trivy with `severity: CRITICAL` and `exit-code: 1`, so the
> workflow is red only for CRITICAL findings. This script therefore inherits exactly that
> threshold: CRITICAL blocks, HIGH and below report without blocking."*

The workflow is **also** red because the **Bandit** job fails on **9 × B608 MEDIUM**. So:

- The gate is currently **more** conservative than designed — it fails safe, which is the right
  direction.
- But anyone reasoning from the docstring would wrongly conclude only CRITICALs block.
- And the stated design — *"CRITICAL blocks, HIGH and below report without blocking"* — **cannot
  be expressed** by this implementation, because workflow conclusion is all-or-nothing. The
  container scan **passed**; the verifier cannot tell.

This is precisely the per-job verification gap. **It is live, not theoretical.**

### 4.3 Gap 2 — no pagination loop. **LATENT, not live.** (Correcting my own first reading.)

`_runs_for` issues one request with `per_page=100` and **no pagination**, then filters by name
client-side — so 100 is the cap across **all** workflows at that SHA, not just `05 Security`.
I expected this to be exploitable. **Measured, it is not:**

```
total_count reported by API for ecd23a9e : 6
returned in page                         : 6
05 Security among them                   : 2
```

With 7 workflows and a few reruns, current volume is far below the cap. **Reported as a latent
robustness issue, not a live defect** — it would require ~100 runs at a single SHA to bite.

### 4.4 Gap 3 — single-workflow scope

`WORKFLOW_NAME = "05 Security"` is one hardcoded name. The verifier has **no correctness-workflow
concept**, so it cannot gate on `01 PR Check` even if the release owner wanted it to. This is a
design boundary, not a defect — but it is why §5 cannot be solved by configuration alone.

### 4.5 Remediation design (NOT IMPLEMENTED — no grant)

Enforce an **owner-approved mandatory-job inventory** via
`/actions/runs/{id}/jobs`, requiring each named job to be `completed`+`success` and treating
`skipped` on a mandatory job as a refusal; add a pagination loop (`while` on `next` link);
parameterize the workflow list so a correctness workflow can be added. Preserve every
behaviour in §4.1. **The mandatory-job inventory must come from the owner — I will not invent
job identities**; §4.2 supplies the current factual inventory as input.

Tests required, with negative controls: removing the job-level check, or allowing a skipped
mandatory job, must make a regression test **fail**. The `staging/phase17_*` prototype
(42 passing) is **outside** `pytest.ini`'s `testpaths = tests` and **is not project regression
coverage** while it remains there.

---

## 5. Correctness route — trigger/job matrix

### 5.1 Measured gate topology

| Workflow | Trigger | `needs:` | Gate |
|---|---|---|---|
| `01-pr-check.yml` | `pull_request` | `needs: static-audit` ×5 | internal only |
| `02-build.yml` | `push [main, master]` | **none** | **none** |
| `03-deploy.yml` | `workflow_run` ← 02, `workflow_dispatch` | `needs: pre-deployment-validation` | **`require_security_scan.py`** |
| `05-security.yml` | `push`, `schedule` | none | own steps blocking |
| `06-frontend-deploy.yml` | `push [main, master]`, `workflow_dispatch` | **none** | **none** |
| `07-desktop-build.yml` | `push` | `needs: build` | internal only |

### 5.2 Can each publication path obtain correctness evidence for its exact SHA?

| Path | Correctness evidence available? | Security evidence enforced? |
|---|---|---|
| Direct push to `main` → `02 Build` → ECR | **No** — `01 PR Check` does not run on push | **No** |
| Direct push to `main` → `06 Frontend Deploy` → S3/CloudFront | **No** | **No** — §1 |
| `workflow_run` → `03 Deploy` → ECS | **No** correctness; **yes** security | **Yes** — refused at `ecd23a9e` |
| `workflow_dispatch` → `03 Deploy` | **No** correctness; security overridable via `allow_unscanned` (logged) | partial |
| `workflow_dispatch` → `06 Frontend Deploy` | **No** | **No** |
| PR → `01 PR Check` | yes, for the **PR head SHA** | — |

**Conclusion: no publication-capable path can obtain correctness evidence for its exact SHA
today.** `01 PR Check` runs only on `pull_request`, and its two most recent runs — `f5b99e57`
and `d07014d3` — both **failed**. So the correctness route is absent where it is needed and red
where it exists.

`D-P15-3` (PR-only advancement vs a push-triggered correctness route) is **undecided**. Per the
brief I did not select a repository policy and **did not touch branch protection.**

---

## 6. Artifact provenance chain — verify-by-digest, deploy-by-tag

Traced end to end this phase.

| # | Stage | Measured behaviour |
|---|---|---|
| 1 | `02-build.yml:72` | builds `:$IMAGE_TAG` **and** `:latest` in one command |
| 2 | `02-build.yml:75-76` | pushes **both** — `:latest` advances **before any gate** |
| 3 | `02-build.yml:78` | `DIGEST=$(aws ecr describe-images … \|\| echo "$IMAGE_TAG")` — **falls back to the TAG when the digest lookup fails**, so the "digest" output can be a tag |
| 4 | `03-deploy.yml:168` | `DIGEST=$(… \|\| echo "")` — falls back to **empty** |
| 5 | `03-deploy.yml:169-173` | `if [ -z "$DIGEST" ] \|\| [ "$DIGEST" = "None" ]` → `exit 1`. **Genuinely fail-closed. Preserved, not weakened.** |
| 6 | `03-deploy.yml:174` | the digest is **`echo`ed only** — never exported, never passed on |
| 7 | `03-deploy.yml:176-180` | deploy step sets `IMAGE_TAG: head_sha \|\| github.sha` and runs the script |
| 8 | `ecs_deploy_and_diagnose.py:901` | `image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"` — **deploys by TAG** |

**The chain verifies a digest and then discards it.** Deployment resolves the tag a second time,
so the artifact deployed is not bound to the artifact verified — a time-of-check/time-of-use
window. **No `aws_ecr_repository` resource exists in any Terraform root** (searched all `*.tf`),
so **ECR tag immutability is not declared anywhere** and the repository is unmanaged by
Terraform.

**Two honest mitigations, stated so the risk is not overstated:**

- `IMAGE_TAG` on this path is the **commit SHA**, not `latest`, so the tag is commit-specific
  and the window is narrow.
- `ecs_deploy_and_diagnose.py:94` `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")` — the `latest`
  default is **not reached** via `03-deploy.yml`, which always sets `IMAGE_TAG`. It is reachable
  only by running the script manually without that variable. **Latent, not live on the CI path.**

**Build-side step 3 is the real provenance defect:** `|| echo "$IMAGE_TAG"` silently substitutes
a mutable tag for an immutable digest, and `02 Build` concluded **success** at `ecd23a9e` with no
gate ahead of it. No image was built, pushed, pulled, retagged, promoted, deleted or deployed by
this phase.

---

## 7. Independent lanes

| Lane | Position (all revalidated or hash-checked) | Status |
|---|---|---|
| **A · Bandit** | CI `bandit -r backend_app -ll`, **no `continue-on-error`**. **9 × B608 MEDIUM** in 3 alembic migrations. **bandit 1.9.4 / Python 3.12.10.** Gate test scoped to **one file** (`GATED_MODULE`); AST test proves validator-constrained identifiers — a **different property** than B608 detects, so both hold at once. All 9 reviewed: validator-constrained identifiers or module literal constants, values parameter-bound. **9 safe constructions · 9 still findings · 0 suppressed · 0 owner-accepted.** Now also known: the Bandit job is **one of the two jobs turning `05 Security` red** (§4.2). | **NOT GRANTED** |
| **B · D7-1** | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`), `market.py:53` (`except` → **unauthenticated public fallback**). `routers/exchange.py` **correct — unchanged**. Kwarg **legitimately** used at `risk.py:313,325,407` and `paper_trading.py` ×8 — no global replacement. `market.py:56` guard preserved. Anonymous-fallback semantics **not invented**. | **NOT GRANTED** |
| **C · D6-D → D7-2** | Ordering confirmed: `APIKeyVault.__init__(self)` takes **zero args** but **fail-fast raises** `EnvironmentError` on missing `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` / `MASTER_ENCRYPTION_KEYS`. A naive `KeyVault`→`APIKeyVault` swap raises **inside the same `try`**, yielding an identical user-visible outcome. The correct target is the initialized `app_state.vault`, which per D6-D is a `MockEngine`. **D7-2 must not be attempted before D6-D.** No second credential manager; no per-cycle key derivation. | **NOT GRANTED** |
| **D · W-6** | **10 of 20 asserted misclassified** (source hash unchanged → REUSED): transport errors shadowed by `symbol`/`market` in URLs (5/5 venue paths); `"key"` shadowing 8/8 ordinary words; genuine **signature** auth → generic; ccxt `RateLimitExceeded` missed (needs `"rate limit"` **with a space**). Redaction intact at `:1236`. `retryable` **log-only** — 15 production occurrences, **0 control flow**. **No retries introduced.** Default-lane coverage **zero**. | **NOT GRANTED** |
| **E · Terraform/Trivy** | 4 unrestricted egress blocks: `security_groups.tf:22/52/73` + `redis-only/main.tf:58`. Redis SG names **identical by default** (`var.environment` default `"production"` vs hardcoded `vyomquant-redis-sg-production`); **collision conditional on same VPC — UNVERIFIED** (main root *creates* `aws_vpc.main`; `redis-only` adopts hardcoded `vpc-0f3e7b3f243fbc133`). `redis-only` has **no backend block → local state**, 0 variables. **`AWS-0054` false positive** — `alb.tf` `dynamic` redirect + `lifecycle.precondition` refusing prod without a custom domain; **compensating control preserved**. **Trivy NOT installed → NOT RUN.** **0 of 5 accepted.** | **NOT GRANTED** |
| **F · tenant/Redis** | `apply_tenant_context` has **zero external consumers**. Three conflicting identity expressions agree only because no token carries a `tenant_id` claim. Two **permissively OR-combined** policy families; `execution_records` predicate open. FORCE experiment REUSED: ENABLE+unset GUC → 2 rows; **ENABLE+FORCE+unset GUC → 0 rows silently**; FORCE+correct GUC → 1 row. Stashed `tenant_middleware.py` reviewed **read-only**, not applied, not assumed compatible. Redis: env unset, 6379 **CLOSED**, no `redis-server`, no Docker → **5 tests SKIP, not passes**; strict XFAIL preserved. | **NOT CERTIFIED / BLOCKED** |
| **G · workers/migrations** | `UNKNOWN` has **no authorized exit policy** — recovery CAS pinned `AND upper(status::text) = 'EXECUTING'` (`execution_record.py:1488`), so `UNKNOWN` rows are unreachable. **W-5 behind W-4; W-3 behind W-6.** Baseline **not recaptured** (`AP-W1D-4` NOT GRANTED). `venue_order_id` stays external; migration `030` postflight aborts on an FK. | **NOT GRANTED** |
| **D6-C** | Validity ESTABLISHED (original fails SQLSTATE `42883`); fixtures **7/7**, counts-only; **production counts NOT SUPPLIED**; token-claim divergence **NOT SUPPLIED**. | **NOT SUPPLIED** |

> **FORCE RLS was not enabled or prepared.** A local superuser run is not evidence that FORCE is
> safe — local `postgres` is `rolsuper`/`rolbypassrls`. Test-cluster and hosted evidence are
> kept separate.

---

## 8. Commands, versions, exit codes

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `write-tree` / `status` / `stash list` / `reflog -1` / `hash-object` | 0 | §2 manifests; tree and both blob hashes unchanged |
| `gh run list --workflow 06-frontend-deploy.yml --limit 100` | 0 | **60 runs; all `push`; `ecd23a9e` newest** |
| `gh run list --workflow 05-security.yml --limit 200` | 0 | 200 runs; §1 correlation |
| `gh run view 38129699497 --json jobs` | 0 | **steps 12 & 13 success** |
| `gh api /actions/runs?head_sha=ecd23a9e…&per_page=100` | 0 | **`total_count = 6`** → §4.3 latent |
| `gh api /actions/runs/38132697285/jobs` | 0 | **Container success / Bandit failure / IaC failure** |
| `python -m bandit --version` | 0 | **bandit 1.9.4, Python 3.12.10** |
| Terraform enumeration, all roots | 0 | 4 egress; **no `aws_ecr_repository`** |
| `Get-Command trivy` | — | **absent — NOT RUN** |
| TCP 6379 / 51805 / 51815 | 0 | Redis closed; PG open on **51815** (shared DSN says 51805 — **not edited**) |

**No pytest run, no scanner run, no CI dispatch.** No implementation was authorized, so there
was nothing new to validate; the brief forbids re-running diagnostics with unchanged inputs.

### 8.1 Evidence classes kept separate

| Class | Status |
|---|---|
| **Source inspection** | verifier, workflows, provenance chain, Terraform, 9 Bandit sites — **done this phase** |
| **Unit tests** | **none run.** Prior: Bandit gate 3 passed; W-6 21 cases; D6-C 7/7 — all REUSED |
| **Integration** | **BLOCKED** — no Redis, no Docker, no non-superuser DB role |
| **Registry (ECR)** | **NOT INSPECTED** — no AWS access, not authorized |
| **Terraform state** | **NOT INSPECTED** — would resolve the VPC question (§7 Lane E) |
| **Live cloud state** | **NOT INSPECTED** — S3 object listing / CloudFront config not authorized |
| **CI run history** | **MEASURED** — §1, §4.2, §8 |
| **Release certification** | **NONE. No gate is evidenced as passing for `ecd23a9e`.** |

---

## 9. Remaining blockers

| # | Blocker | Owner | Smallest decision | Next executable action |
|---|---|---|---|---|
| **1** | **Frontend published at a failed-security SHA and is LIVE** | **release owner** | Rollback or forward-fix? (§1.2) | Answer the one question in §1.2 |
| **2** | Frontend gate `D-P15-4` | **workflow/release owner** | One grant | Implement §4.5-style `needs:` + exact-SHA gate before AWS credentials |
| **3** | Keep FORCE RLS blocked | — | **No action** | — |
| **4** | Verifier job-level blindness (§4.2) | **security owner** | Approve a mandatory-job inventory | Add `/jobs` check + negative controls |
| **5** | `D-P15-3` correctness route | **release owner** | PR-only vs push-triggered | Then bind it into the verifier (§4.4) |
| **6** | `01 PR Check` red on PRs (§5.2) | **code owner** | Triage 2 failures | Read `f5b99e57` / `d07014d3` logs |
| **7** | Build-side digest→tag fallback (§6) | **release owner** | One grant | Fail instead of `\|\| echo "$IMAGE_TAG"` |
| **8** | ECR immutability undeclared (§6) | **infra owner** | Which root owns the ECR repo? | Then declare `image_tag_mutability` |
| **9** | Redis SG VPC identity | **infra owner** | One `terraform state show aws_vpc.main` | Settles collision real vs moot |
| **10** | `redis-only` local state | **infra owner** | Was it ever applied? Where is state? | — |
| **11** | Trivy dispositions (5) | **security owner** | Accept/reject each | 0 of 5 accepted |
| **12** | Bandit parity + 9 dispositions | **code owner** | One grant | Parity test + negative control |
| **13** | D7-1 | **code owner** | One grant | 3 line deletions + binding test |
| **14** | **D6-D before D7-2** | **code owner + platform** | Grant D6-D first | Fixing D7-2 first yields a silent `TypeError` |
| **15** | W-6 | **code owner** | One grant; precedes W-3 | Reorder ladder + bound tokens |
| **16** | Tenant identity contract | **code owner** | **Choose 1 of 3 expressions** | Costs no code; unblocks all wiring |
| **17** | D6-C counts · D6-D boot · claim divergence | DB / platform / auth owners | 3 integers · 4 redacted fields · 1 integer | — |
| **18** | `UNKNOWN` exit policy | **execution-state owner** | Define + test | Unblocks W-5 |
| **19** | Isolated Redis | **coordinator/infra** | One grant + instance | Enables 5 skipped tests |
| **20** | Orphaned `stash@{1}` | **code owner** | Preserve / review / discard | 18 files pre-rewrite WIP |

### 9.1 Continuation plan

Reuse, do not repeat: the 9 Bandit findings and locations, W-6's 10/20 matrix, D6-C's 7/7
fixtures, the RLS FORCE experiment, and this phase's verifier source analysis and provenance
trace — all valid while the tree hash stays `4b45a763…`. Re-measure only `gh` run history
(it changes with each push) and anything whose source hash moves.

Three blockers would unblock the most: **#1** (incident decision), **#2** (frontend gate grant),
and **#16** (tenant identity — a choice among three existing expressions, costing no code).

---

## 10. Final diff review and unauthorized-operation checklist

**There is no diff.** `git write-tree` = `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` at entry and
exit. Both concurrent files retain their entry blob hashes (§2.1). The only file created is this
report; the destination was verified absent and no report was overwritten.

| Prohibited operation | Performed? |
|---|---|
| Stage, commit, push, merge, rebase, reset, clean, revert, rewrite history | **No** — tree hash identical, 0 staged |
| **Mutate stashes** | **No** — 2 at entry, **2 at exit**, both preserved |
| **Dispatch CI**, open PRs, change branch protection | **No** — `gh run list` / `gh run view` / `gh api GET` are **read-only**; no rerun, no dispatch |
| Deploy, roll back, **republish**, **purge/invalidate caches**, change DNS, modify production assets | **No** |
| Build, push, pull, retag, promote or delete images | **No** |
| Apply Terraform / provision infrastructure / read private state | **No** — `terraform` CLI not invoked |
| Mutate production data / access production credentials | **No** — production never contacted |
| **Enable or prepare FORCE RLS** | **No** |
| Start, restart or reconfigure shared services; edit `pg_dsn.txt` | **No** |
| Alter exchange credentials / place live orders / enable live trading | **No** |
| Print secrets, tokens, DSNs, keys or decrypted material | **No** — the `gh` token appeared masked in tool output and is not reproduced |
| Weaken thresholds, suppress findings, remove security rules, add `\|\| true` | **No** — 0 suppressions; `03-deploy.yml` digest rejection and the ALB precondition **preserved** |
| Overwrite a report or concurrent work | **No** |
| Claim a skipped/unexecuted test or unavailable scanner passed | **No** — 5 Redis tests are skips; Trivy **not run**; no pytest run; prototype excluded from coverage |
| Claim tenant isolation certified | **No** — **NOT CERTIFIED** |

---

## 11. Release posture

| Gate | Evidence for `ecd23a9e` | Status |
|---|---|---|
| Correctness | `01 PR Check` **never ran**; red on 2 recent PRs | **ABSENT · RED** |
| Security scan | `05 Security` **failure** ×2; jobs: Container ✓, **Bandit ✗, IaC ✗** | **RED** |
| Frontend gate | `needs:` = 0; **published anyway; build is LIVE** | **ABSENT · BREACHED** |
| Backend gate | `03 Deploy` **refused** | **WORKING** |
| Artifact provenance | verify-by-digest, **deploy-by-tag**; build-side digest→tag fallback; ECR immutability undeclared | **WEAK · RED** |
| Terraform / Trivy | 5 CRITICAL, **0 accepted**, scanner **not run** | **RED** |
| D7-1 / D6-D / D7-2 / W-6 | all open; D7-2 gated behind D6-D | **OPEN** |
| Tenant isolation | 0 context consumers; non-owner matrix unexecuted | **NOT CERTIFIED** |
| Redis / quota | 5 tests **SKIPPED** | **UNPROVEN** |
| Workers / migrations | `UNKNOWN` exit undefined | **OPEN** |

**No release certification.** No mandatory gate is evidenced as passing for the release
identity, and one gate was not merely absent — it was **bypassed, and the resulting build is
still serving.**

---

*Phase 23 closed. Tree hash unchanged at entry and exit. Both stashes untouched. 0 of 12 lanes
authorized; 0 files changed; 6 new measurements delivered (incident scope and recurrence,
verifier source analysis, live job-level gap, latent pagination gap, provenance chain trace,
ECR immutability absence).*

***DO NOT RELEASE · INCIDENT OPEN · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · DEPLOYMENT BLOCKED.***
