# PHASE 35 — Full-System Implementation, Image Provenance, Worker Lifecycle, and Release Certification

**Status banner (unchanged, and not weakened by anything in this phase):**

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

**Governed files changed this phase: 0.** No file tracked by git was created, modified, renamed, or deleted. No git state-mutating command was run. All work products of this phase are untracked probes under `staging/` and are labelled **PROTOTYPE ONLY**.

**Authorization status: 0 grants exist on any governed target.** The task prompt is not an authorization grant. Historical reports, bare `GRANTED` strings found in prior documents, copied authorization templates, and proposed owner decisions are not grants. Every implementation workstream below is therefore reported as `BLOCKED (NO GRANT)` rather than attempted.

---

## 1. Preflight and integrity baseline

Entry state, re-verified at the start of this phase and again at the end (§18). Entry equals exit.

| Property | Value |
|---|---|
| HEAD | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` |
| Branch | `main` |
| Upstream divergence | 0 ahead / 0 behind |
| Staged paths | 0 |
| Unmerged paths | 0 |
| Tracked files differing from HEAD | 2 |
| Stash entries | 2 |
| Worktrees | 1 |

`git write-tree` has been byte-identical at `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` from Phase 17 through Phase 35. That hash covers the full tracked content of the index. Its stability across 19 phases is the primary evidence that no governed file has been altered by any of them.

### 1.1 Concurrent work, preserved

Two tracked files differ from HEAD. Both belong to other sessions running in parallel. Neither was touched.

| Path | Blob hash at entry | Blob hash at exit |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

Concurrent sessions have also written reports numbered through `PHASE38_*`. A file named `staging/PHASE35_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_ASSURANCE.md` already exists and belongs to a different session; it is a distinct document and was not read, modified, or overwritten. The destination of *this* report was verified absent immediately before writing.

### 1.2 Stashes — read-only, never touched

| Entry | Content | Note |
|---|---|---|
| `stash@{0}` | `filter-branch: rewrite` | Not a real stash. `refs/original/refs/stash` exists, so this entry is a history-rewrite backup that landed in the stash reflog. |
| `stash@{1}` | `99ceb9b0e9915c5be7d8d5396da4c73912cabf1f` | 18 files. Base commit `c673409` is **not an ancestor of HEAD** — orphaned pre-rewrite WIP. Includes `core/credential_vault.py` and `core/tenant_middleware.py`, which are directly relevant to the vault and tenant findings below. |

`stash@{1}` is the only place in the repository where a credential-vault implementation and a tenant-middleware implementation appear together. Recovering it is an owner decision (§17), not a local one: its base is orphaned, so applying it cannot be done without a merge.

### 1.3 Porcelain count

Entry 169 → exit 178. The delta of 9 is accounted for entirely by new untracked `staging/` probe artifacts created this phase and by concurrent sessions' artifacts. No tracked path changed status, which is confirmed independently by the unchanged `write-tree`.

### 1.4 Tool availability

| Tool | State | Consequence |
|---|---|---|
| `bash` | present | Shell syntax checks possible |
| `gh` | authenticated | CI evidence retrieval possible |
| `bandit` | 1.9.4 / Python 3.12.10 | SAST reproducible locally |
| `PyYAML` | 6.0.3 | Workflow parsing possible |
| `docker` | **ABSENT** | Image build/digest verification `BLOCKED` |
| `trivy` | **ABSENT** | Container + IaC scan reproduction `BLOCKED` |
| `terraform` | **ABSENT** | `validate`/`plan` `BLOCKED` |
| `kubectl` | **ABSENT** | Deploy-path verification `BLOCKED` |
| `safety` | **ABSENT** (`False`) | Dependency scan `BLOCKED` |
| `pip_audit` | **ABSENT** (`False`) | Alternative dependency scan `BLOCKED` |
| Redis | port 6379 **CLOSED**, env unset | Cache/lock/queue paths `BLOCKED` |
| PostgreSQL | port 51815 **OPEN** | DB-backed tests runnable |

`staging/pg_dsn.txt` records port 51805, which does not match the live listener on 51815. That file is shared with concurrent sessions and was **not edited**. The mismatch is recorded here as an observation only.

---

## 2. What this phase actually measured

Four new measurements. Two of them changed conclusions that earlier phases had recorded.

1. The Phase 31 workflow-invariant checker was run against **current source** for the first time — 5 violations, exit 1.
2. The checker's own test suite was run — 21 passed, exit 0.
3. A **third** Terraform root was discovered and its state posture measured.
4. A line-ending defect claim raised earlier in this phase was measured and then **withdrawn**.

---

## 3. The five workflow invariants — current status

Prototype: `staging/phase31_workflow_invariants.py`. Run output: `staging/p35_invariants.txt`. **EXIT=1, 5 violations.** Every one of the five invariants is currently violated by committed workflow source.

| ID | Invariant | Status | Location | Finding |
|---|---|---|---|---|
| INV-1 | evidence-retention | **FAIL** | `05-security.yml :: sast-scan / Upload Security Reports` | Upload step has no `if: always()`, and 1 earlier step (`Run Bandit Scan`) can fail the job. On that failure the upload is **SKIPPED** and the report that would explain the failure is never retained. |
| INV-2 | mandatory-scan-not-swallowed | **FAIL** | `05-security.yml :: sast-scan / Run Safety Check` | `safety check --json --output reports/safety-report.json \|\| echo "Safety advisory logged"` — exit status discarded, step reports success regardless of findings. |
| INV-3 | no-tag-as-digest | **FAIL** | `02-build.yml :: build-push / Build, tag, and push API image to ECR` | `DIGEST` is emitted as a digest output but falls back to `$IMAGE_TAG` when lookup fails, so a field named *digest* can hold a mutable tag. A real digest matches `^sha256:[0-9a-f]{64}$`. |
| INV-4 | no-ungated-mutable-tag-push | **FAIL** | `02-build.yml :: build-push / Build, tag, and push API image to ECR` | Pushes mutable tag `latest` while the workflow declares no gate dependency — no `workflow_run`, no `needs:`, no security-scan check. An unvalidated image lands on the tag infrastructure resolves. |
| INV-5 | digest-is-consumed | **FAIL** | `02-build.yml :: workflow outputs` | A digest is exported but **no** workflow reads `needs.<job>.outputs.image_digest`. The build-to-deploy identity chain is tag-based end to end; the digest output is decorative. |

INV-5 was produced by a checker written in Phase 31, before the Phase 34 architectural diagnosis existed. It reaches the same conclusion from a different direction (output-consumption analysis rather than event-type analysis). That is independent corroboration, not a restatement.

**Checker trustworthiness.** `staging/phase31_test_workflow_invariants.py` → **21 passed, EXIT=0** (`staging/p35_invariant_tests.txt`). The suite exercises each invariant against both violating and compliant synthetic workflow fixtures, so a checker that always reported violations would fail it. The 5 violations above are therefore not an artifact of a checker stuck in the failing position.

**Not weakened.** No invariant was relaxed, no threshold lowered, and the fail-closed `exit 1` was preserved.

---

## 4. Image provenance — four surfaces, every edge broken

**Status: IMAGE PROVENANCE NOT ESTABLISHED.**

The system has four independent paths by which an image identity can reach a running workload. Each was traced to current source.

### 4.1 Surface 1 — `02-build.yml` (build and push)

| Line | Source behaviour |
|---|---|
| `:72` | Builds `:$IMAGE_TAG` and `:latest` |
| `:75-76` | Pushes **both, pre-gate** |
| `:78` | Digest lookup with `\|\| echo "$IMAGE_TAG"` — the INV-3 fallback |
| `:81` → `:37` → `:12` | Output plumbing job→workflow is **correct** |

The plumbing is wired properly and is still unreachable: there is **no `uses:`** reference to this workflow anywhere in the repository. The only mention of its `workflow_call` interface is in its own header comment.

### 4.2 Surface 2 — `03-deploy.yml` (deploy)

| Line | Source behaviour |
|---|---|
| `:5-7` | `workflow_run: workflows: ["02 Build"]` |
| `:93` | `DEPLOY_SHA: ${{ github.event.workflow_run.head_sha \|\| github.sha }}` |
| `:95` | Calls `require_security_scan.py` |
| `:130` | `needs: pre-deployment-validation` |
| `:165` | **Re-derives the tag** rather than consuming a digest |
| `:168-173` | `\|\| echo ""` then `exit 1` — **fail-closed. PRESERVE.** |
| `:174` | Echoes the digest and nothing more |

**The architectural root cause.** `02-build.yml` declares a `workflow_call` output. `03-deploy.yml` consumes via `workflow_run`. A `workflow_run` event structurally **cannot carry job outputs** from the triggering workflow. The digest is therefore not "echoed and discarded" (the Phase 33 framing, now superseded) — it is **unreachable by event type**. No amount of wiring inside `03-deploy.yml` can fix this; the event contract itself is wrong.

### 4.3 Surface 3 — `ecs_deploy_and_diagnose.py`

`:94` `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")` · `:901` constructs the image URI from that tag. Defaults to the most mutable tag available.

### 4.4 Surface 4 — `scripts/deploy.sh` → `kubectl`

`:9` `IMAGE_TAG=${2:-latest}` · `:46-47` `kubectl set image deployment/trading-backend backend=$IMAGE_TAG`.

Per instruction, `infra/deploy.sh` is **not** counted as an image-bearing deployment path: current source does not show it handling image identity. Also per instruction, the fact that `scripts/deploy.sh` has zero workflow references is **not** treated as proof it is unused — it is a manually operated script, and a manual operator is exactly the actor it is written for.

### 4.5 Blue-green promotion

`scripts/blue-green-deploy.sh`: `:66-70` validates only that the tag is **non-empty** · `:86-87` uses it **unquoted** · `:247-248` the promote step **re-reads the live cluster** via `:54` `kubectl get … jsonpath … || echo`. Promotion therefore trusts whatever is currently running rather than the artifact that was validated. `blue-green-deploy.yml:71` passes `"${{ github.event.inputs.image_tag }}"` — a human-typed string.

### 4.6 Registry immutability

**UNKNOWN.** No `aws_ecr_repository` resource exists in any of the three Terraform roots, so tag immutability is neither configured nor observable from source. Without `docker` or AWS registry access, it cannot be probed. This is the control that would make a tag-based chain tolerable, and its state is unknown.

### 4.7 Digest architecture options — presented, not selected

Current policy requires owner approval for architecture selection, so **no option is chosen here**.

| Option | Mechanism | Trade-off |
|---|---|---|
| **A** | Convert `02-build.yml` to a reusable workflow actually invoked with `uses:`, and have the caller pass the digest to deploy via `needs` | Correct output semantics; requires restructuring both workflows and removing the `workflow_run` trigger |
| **B** | Keep `workflow_run`; persist the digest as a build **artifact** and have deploy download it | Smallest change to triggering; adds an artifact round-trip and a new failure mode if upload is skipped (see INV-1) |
| **C** | Keep `workflow_run`; have deploy **resolve** the digest itself from the registry by `DEPLOY_SHA` | No cross-workflow data passing; depends on a tag→SHA convention and on registry immutability (§4.6, UNKNOWN) |
| **D** | Single combined build+deploy workflow with `needs:` | Simplest identity chain; loses the separation between build and deploy authorization |

Each option also has to satisfy INV-3 (no tag fallback) and INV-4 (no ungated mutable push), which are independent of the choice.

---

## 5. Release verifier — identity contract failure

`staging/phase18_release_gate.py`, blob `5c70dbbfb882036badade2ff01b4bf687595ff4e`.

Configured requirement:

```
REQUIRED_WORKFLOWS = {
  "05 Security":   ("sast-scan", "container-security", "iac-scan"),
  "01 PR Check":   ("static-audit", "unit-tests", "paper-mode-tests",
                    "database-tests", "docker-check", "frontend-tests"),
}
ADVISORY_JOBS = ("format-check", "provisioning-and-isolation")
```

These 9 mandatory entries are job **ids**. The GitHub API returns job **display names**:

| Configured id | Live API name |
|---|---|
| `sast-scan` | `Bandit SAST & Dependency Security Scan` |
| `container-security` | `Trivy Container Vulnerability Scanner` |
| `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` |
| `static-audit` | `Code Quality & Static Audit` |
| `unit-tests` | `Unit & Integration Tests` |
| `paper-mode-tests` | `Execution Tests (production posture)` |
| `database-tests` | `Database Tests (PostgreSQL)` |
| `docker-check` | `Docker Build Validation` |
| `frontend-tests` | `Frontend Build & Unit Tests` |

**9 of 9 unmatched.** The verifier would refuse every release regardless of CI outcome.

**The Phase 28–31 recommendation to promote this verifier into the gate is therefore withdrawn.** Promoting it would install a gate that is fail-closed for the wrong reason: it would block on an identity mismatch, not on evidence of risk, and the resulting constant red would train operators to bypass it. The fail-closed direction is right; the identity mapping has to be fixed first.

**Verifier self-tests are vacuous on this exact property.** Blob `3dd575192edbb8df26f3bcee0ba6fef7992e0483` → **28 passed**. But the fixture at `:62` does `jobs[1000+i] = [_job(n) for n in mandatory]`, deriving fixture job names **from the same config under test**, and the suite performs **0 reads of `.github`**. A name-mapping error is unobservable by construction. The 28 passes are real and they do not cover the defect.

**The expected job set is SHA-dependent.** Run `38061973739` shows `D5' Real-Redis Diagnostics (non-blocking)` as live-only (present in the API, absent from YAML) and `provisioning-and-isolation` as YAML-only. A static required-job list cannot be correct for all SHAs.

Per instruction, neither `provisioning-and-isolation` nor `D5' Real-Redis Diagnostics (non-blocking)` is classified here. The YAML-side inventory is **complete**: 11 jobs, 0 unclassified, 0 dangling.

The verifier was **NOT RUN** this phase — its inputs are unchanged since the last run, and re-running unchanged inputs produces no new information.

---

## 6. Release candidate and the frontend publication incident

Published SHA `ecd23a9e` — the current HEAD.

| Workflow | Result at that SHA |
|---|---|
| `06 Frontend Deploy` run `38129699497` | **success** — step 12 `Sync build artifacts to S3` success, step 13 `Invalidate CloudFront cache` success |
| `05 Security` | **failure ×2**, created `09:48:44Z`, one second before the frontend run |
| `03 Deploy` | **failure** — backend gate refused |
| `01 PR Check` | never ran at this SHA |

Published bundle: `index-EYjpdKQA.js`, SHA256 `6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312`, 509,689 bytes; 156 files, 3.7 MiB total.

Cause: `06-frontend-deploy.yml`, blob `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9`, declares **`needs:` = 0**. It has no dependency on security or test outcomes, so a red security result cannot stop a frontend publish. `artifacts.total_count = 0` for the run, so the published bundle is not independently retrievable from CI — its identity had to be recovered from the live origin.

**Rollback is not currently a remedy.** SHA `6c5660a1` (green) and SHA `eee73265` (red) both published a **byte-identical** bundle: `index-DPALkFK6.js`, SHA256 `13dd072f9178e0d7ebeaa4b738e402e854d3c4f6de72fe39b99904a5d295620f`. Rolling back to the green SHA would deliver the same bytes that the red SHA delivered, so it would change the provenance label without changing what users execute.

**Backend blocker.** `01 PR Check` has failed on **6 of 6** recent runs, every one on a single job (`Unit & Integration Tests`) and a single test (§8).

Containment is **BLOCKED (NO GRANT)**: S3, CloudFront, and DNS mutation are all prohibited without separate authorization, and the containment decision is an owner decision (§17).

---

## 7. Security evidence and suppression posture

### 7.1 Bandit

CI runs `bandit -r backend_app -ll` (`05-security.yml:36`) with no `continue-on-error`, so findings fail the job.

**9 × B608 MEDIUM** (SQL built by string construction), all in migrations:

| File | Lines |
|---|---|
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |

Combined with INV-1 (§3), a Bandit failure also **skips the report upload**, so the evidence explaining the failure is discarded at exactly the moment it is needed.

### 7.2 The Bandit gate test scans one file

The gate test pins `GATED_MODULE = backend_app/backend/transactional_execution_manager.py`. One file. None of the 9 B608 findings is in it. The gate is green and the scan is red, and both are correct about their own scope.

### 7.3 Suppression inventory

Prototype `staging/p34_nosec_inventory.py` → `staging/p34_nosec_inventory.txt`. Per-directive, not per-file:

| Metric | Count |
|---|---|
| Total `nosec` directives | 56 |
| Files containing them | 17 |
| **Bare** (no rule id) | **54** |
| Scoped to a rule id | 2 |
| With a written rationale | 12 |
| **Without any rationale** | **44** |
| **Bare *and* unexplained** | **43** |
| In the gate test's only scanned file | 13 |

Buckets: 18 SQL-string, 5 bind-all-interfaces, 31 unclassified.

A bare `# nosec` suppresses every rule, including rules that did not exist when it was written. 43 of 56 are bare and carry no explanation, so there is no record of what was assessed.

### 7.4 Safety

INV-2 (§3) shows the Safety step cannot fail. Separately, **no Safety policy exists** in the repository: no severity threshold, no ignore list, no documented acceptance criteria. Per instruction, **no threshold was invented**. Local reproduction is `BLOCKED` — both `safety` and `pip_audit` are absent.

### 7.5 Trivy

4 Trivy surfaces confirmed (correcting an earlier 3-vs-4 error of my own). Reproduction **BLOCKED** — `trivy` absent.

### 7.6 `AWS-0054`

Remains a **confirmed false positive**. Its `lifecycle.precondition` is preserved and was not removed to obtain a cleaner scan.

### 7.7 Withdrawn claim, retained

The Redis security-group **name-collision** claim remains **WITHDRAWN** from an earlier phase. It is restated as withdrawn so that no downstream reader revives it from an intermediate report.

---

## 8. Tenant isolation

**Status: TENANT ISOLATION NOT CERTIFIED.**

`tests/property/test_tenant_isolation_matrix.py`, blob `a60cd00c4558b386de61e045b08bf9b5ebfaf69d`. Line `:2738` reports: *Requirement 21.8: 4 of the 150 attempted cells failed.*

Failing endpoints: `POST /api/library/{library_id}/clone` and `/checkout`.

Mechanism: `check_strategy_quota` is a FastAPI **dependency** declared at `library.py:2340-2344`. Dependencies resolve before the handler body, so the quota check runs **before** the ownership lookups at `:2437` and `:2445`. A caller therefore learns quota state for a library they do not own. The comment at `:2352` documents that this ordering deliberately closes a **write-bypass**, so the ordering is load-bearing: the obvious fix of moving the check after ownership would reopen the bypass it was placed there to close.

This is the single test behind all 6 `01 PR Check` failures (§6).

**It passes locally.** Twice — 37.89s and 47.41s, both `EXIT=0`, including against a fresh Hypothesis corpus. My earlier hypothesis that corpus state explained the divergence was **withdrawn**. Local pass with CI fail, on identical source, remains **UNKNOWN** and is itself a reason not to certify.

### 8.1 Tenant identity is expressed three ways

| Location | — |
|---|---|
| `dependencies.py:318` | expression 1 |
| `routers/orders.py:819` and `:940` | expression 2 |
| `routers/distributed_execution.py:296` | expression 3 |

Per instruction, **no canonical expression was selected** — that is an owner decision. `apply_tenant_context` has **0 external consumers**: the helper intended to centralize this is unused.

### 8.2 RLS FORCE — measured live, fail-open under one configuration

| Configuration | Rows returned |
|---|---|
| `ENABLE` + GUC unset | **2** |
| `ENABLE` + `FORCE` + GUC unset | **0** (silently) |
| `FORCE` + correct GUC | **1** (correct) |
| Cross-tenant `INSERT` | refused, SQLSTATE `42501` |

`ENABLE` without `FORCE` and without the GUC returns **all** rows — a fail-open posture. `FORCE` with the GUC unset returns 0 rows silently, which is safe but indistinguishable from "no data", so a misconfiguration presents as an empty result rather than an error. These were measured against a live database after an earlier superuser-context run was identified as invalid and discarded.

---

## 9. Worker lifecycle

Two services are defined, constructed, and never registered with the application.

| Service | Definition | Only external reference | In `main.py`? |
|---|---|---|---|
| `ExchangeReconciliationService` | `exchange_reconciliation.py:74` | `exchange_websocket_listener.py:694` — and nothing starts *that* | **No** |
| `MarketplaceExpiryWorker` | `workers/marketplace_expiry_worker.py:95` | instantiated at `:225` in its own module only | **No** |

Neither appears in `main.py`. Exchange reconciliation is the control that detects divergence between local order state and venue state; marketplace expiry is the control that retires expired listings. Both are silently inert in the deployed application: nothing logs their absence.

Registration is **BLOCKED (NO GRANT)** — `main.py` is governed.

---

## 10. Credential vault and cache updater

`state.py:67` calls `safe_import("APIKeyVault", "api_key_vault")` with a **bare module name**, which is unresolvable from the application's import root. The import fails, and `MockEngine.__getattr__` at `:51-55` absorbs every subsequent attribute access, so `app_state.vault` becomes a mock that answers any call. Emitters at `state.py:63` and `state.py:108` report the substitution, but nothing fails.

`APIKeyVault.__init__(self)` takes **zero arguments** and raises fail-fast on misconfiguration — the real class is written to refuse to start insecurely. The bare-name import means that refusal never happens; the mock answers instead. The fail-fast design is defeated by an import path, not by a logic error.

`portfolio_cache_updater.py:151` imports `backend_app.backend.key_manager`, which **does not exist**.

`stash@{1}` (§1.2) contains a `core/credential_vault.py`. Whether it supersedes `api_key_vault` is an owner decision; its base commit is orphaned (§1.2).

---

## 11. Error classification and execution state

**W-6:** of 20 asserted classifications, **10 are misclassified** (correcting my own earlier figure of 3/8, which was measured over a smaller sample). The `retryable` flag is **log-only**: 15 production occurrences, **0** uses in control flow. Code that reads as retry logic does not retry.

**`UNKNOWN` exit:** the compare-and-swap is pinned to `upper(status::text) = 'EXECUTING'` at `execution_record.py:1488`. An order in `UNKNOWN` cannot be transitioned by that CAS, so `UNKNOWN` is terminal in practice.

**D7-1 — three invalid `access_token=` call sites**, machine-verified:

| Location | Failure mode |
|---|---|
| `orders.py:792` | unguarded → **500** |
| `orders.py:1098` | `except` → `return []` (empty list indistinguishable from "no orders") |
| `market.py:53` | `except` → **unauthenticated public fallback** |

`exchange.py` is correct. Occurrences at `risk.py:313`, `:325`, `:407` and 8 in `paper_trading.py` are legitimate. `market.py:56` has the guard that `:53` lacks.

`market.py:53` is the most consequential: an authentication failure degrades into serving unauthenticated public data rather than refusing.

---

## 12. Infrastructure state posture — three Terraform roots, three postures

Three roots contain `.tf` files. Enumerated this phase.

| Root | Backend | Encryption | Locking |
|---|---|---|---|
| `terraform/` | `s3` — `vyomquant-terraform-state`, key `prod/terraform.tfstate`, region `ap-southeast-1` | `encrypt = true` | `dynamodb_table = "vyomquant-terraform-lock"` |
| `aerora_quant_platform/infrastructure/terraform/` | `s3` — `aerora-terraform-state`, key `production/terraform.tfstate`, region `us-east-1` | **absent** | **absent** |
| `terraform/redis-only/` | **none → local state** | n/a | n/a |

**Correction to my own in-phase note.** I earlier recorded the second root as missing `key` as well. It is not: `main.tf:12` sets `key = "production/terraform.tfstate"`. The missing settings are `encrypt` and `dynamodb_table` only. I also earlier compared both roots as if they shared a region; they do not — `us-east-1` versus `ap-southeast-1`, with different bucket names and different key paths. These are two unrelated state locations, not two configurations of one.

`terraform/providers.tf:16-19` carries a comment stating explicitly that `encrypt = true` enables SSE on the state object and that `dynamodb_table` provides the lock preventing two concurrent applies from corrupting state. The project has therefore written down why both are required, and the second root satisfies neither. State for that root is unencrypted at rest and unlocked against concurrent apply.

`terraform/redis-only/` has **no backend block** (local state), **0 variables**, and a hardcoded `vpc-0f3e7b3f243fbc133`.

`terraform validate` and `plan` are **BLOCKED** — `terraform` absent.

---

## 13. Line endings — claim raised, measured, and WITHDRAWN

This is reported in full because an intermediate conclusion of mine was wrong and a reader of partial notes could act on it.

**What I claimed.** `bash -n` fails on two committed shell scripts, therefore they cannot run as committed:

```
scripts/deploy.sh:62            $'do\r'
scripts/blue-green-deploy.sh:33 $'{\r'
```

**Why I did not report it.** The claim was self-contradicted by evidence already in hand: `infra/deploy.sh` passes `bash -n`. A repository-wide CRLF defect would not spare one shell script. I also established that my first byte-check was invalid on its own terms — it read the files through PowerShell redirection, and PowerShell **rewrites line endings**, so the check could only ever report CRLF.

**What I measured instead.** `staging/p35_eol_check.py` → `staging/p35_eol_check.txt` (**EXIT=0**), reading committed blob bytes via Python `subprocess` raw stdout, bypassing the shell entirely:

| Path | Committed blob | Working tree |
|---|---|---|
| `scripts/deploy.sh` | **LF** (82 lone LF, 0 CRLF) | CRLF (82) |
| `scripts/blue-green-deploy.sh` | **LF** (377 lone LF, 0 CRLF) | CRLF (377) |
| `scripts/require_security_scan.py` | **LF** (130 lone LF, 0 CRLF) | CRLF (130) |
| `infra/deploy.sh` | **LF** (140) | **LF** (140) |

**Every committed blob is LF-only.** Local config is `core.autocrlf=true`, which rewrites on checkout. The `bash -n` failures are a **local checkout artifact**, not a repository defect.

**The claim is WITHDRAWN.** The scripts run correctly as committed. `infra/deploy.sh` was spared because it is already LF in the working tree.

**Residual risk, which is real and separate.** `.gitattributes` is **ABSENT** while `core.autocrlf=true` is set locally. Nothing in the repository pins `*.sh eol=lf`. Correct endings therefore depend on each contributor's local git configuration rather than on a committed rule. A contributor with `autocrlf=false` on Windows could commit CRLF and break these scripts on a Linux runner. That is a latent hygiene gap, reported as such and **not** as a live failure. Adding `.gitattributes` is `BLOCKED (NO GRANT)`.

---

## 14. Commands run this phase, with exit codes

| Command | Output artifact | Exit |
|---|---|---|
| `python staging/phase31_workflow_invariants.py` | `staging/p35_invariants.txt` | **1** (5 violations — expected, fail-closed) |
| `python -m pytest staging/phase31_test_workflow_invariants.py` | `staging/p35_invariant_tests.txt` | **0** (21 passed) |
| `python staging/p35_eol_check.py` | `staging/p35_eol_check.txt` | **0** |
| `bash -n scripts/deploy.sh` | — | **non-zero** (local CRLF artifact, §13) |
| `bash -n scripts/blue-green-deploy.sh` | — | **non-zero** (local CRLF artifact, §13) |
| `bash -n infra/deploy.sh` | — | **0** |
| `git rev-parse HEAD`, `git write-tree`, `git status --porcelain`, `git stash list`, `git worktree list`, `git hash-object` | §1, §18 | **0** |
| `Get-ChildItem -Recurse -Filter *.tf` + backend-block scan | §12 | **0** / partial |

All probe scripts live under `staging/`. `pytest.ini` sets `testpaths = tests`, so none of them is in default discovery. Per instruction, **`staging/` was not added to pytest discovery**; the prototype suite was invoked by explicit path.

---

## 15. Deliberately NOT RUN

| Item | Reason |
|---|---|
| Phase 18 release verifier | Inputs unchanged since last run — no new information |
| Bandit full scan | Inputs unchanged since last run |
| Tenant isolation matrix | Inputs unchanged; already passed locally twice |

---

## 16. Blocked / skipped / unknown register

| Workstream | Status | Cause |
|---|---|---|
| Digest architecture implementation | **BLOCKED** | No grant; owner must select among Options A–D |
| INV-1 evidence retention fix | **BLOCKED** | No grant (`05-security.yml` governed) |
| INV-2 Safety gate fix | **BLOCKED** | No grant; and no Safety policy exists to encode |
| INV-3 / INV-4 build fixes | **BLOCKED** | No grant (`02-build.yml` governed) |
| INV-5 digest consumption | **BLOCKED** | No grant; depends on architecture selection |
| Release verifier identity fix | **BLOCKED** | No grant; expected job set is SHA-dependent |
| Worker registration | **BLOCKED** | No grant (`main.py` governed) |
| Vault bare-import fix | **BLOCKED** | No grant |
| Tenant identity unification | **BLOCKED** | No grant; owner must pick canonical expression |
| Tenant quota/ownership ordering | **BLOCKED** | No grant; naive fix reopens a documented write-bypass |
| `.gitattributes` | **BLOCKED** | No grant |
| Terraform state hardening (2nd root) | **BLOCKED** | No grant; `terraform` absent |
| Frontend containment | **BLOCKED** | No grant (S3/CloudFront/DNS); owner decision pending |
| Container / IaC scan reproduction | **BLOCKED** | `trivy`, `docker` absent |
| Dependency scan reproduction | **BLOCKED** | `safety`, `pip_audit` absent |
| Deploy-path verification | **BLOCKED** | `kubectl` absent |
| Redis-dependent paths | **BLOCKED** | Port 6379 closed, env unset |
| Security report upload on failure | **SKIPPED** (in CI, by design defect) | INV-1 |
| ECR tag immutability | **UNKNOWN** | No `aws_ecr_repository` in any root; no registry access |
| Local-pass / CI-fail divergence | **UNKNOWN** | Identical source, opposite outcomes |
| `provisioning-and-isolation` classification | **NOT CLASSIFIED** | Per instruction |
| `D5' Real-Redis Diagnostics (non-blocking)` classification | **NOT CLASSIFIED** | Per instruction |

---

## 17. Owner decisions required

None of these may be taken on the owner's behalf under current policy.

1. **Digest architecture** — select Option A, B, C, or D (§4.7). The `workflow_call`/`workflow_run` mismatch cannot be fixed without choosing.
2. **Frontend containment** — invalidate/roll back/leave. Note that rollback to the known-green SHA ships bytes identical to a known-red SHA (§6).
3. **Canonical tenant identity expression** — one of the three in §8.1.
4. **Tenant quota/ownership ordering** — any reordering must preserve the write-bypass closure documented at `library.py:2352`.
5. **Safety policy** — severity threshold and acceptance criteria. None exists; none was invented.
6. **Suppression triage** — disposition for 43 bare-and-unexplained `nosec` directives (§7.3).
7. **`stash@{1}` recovery** — orphaned base `c673409`; contains the only credential-vault + tenant-middleware pair.
8. **Second Terraform root** — adopt `encrypt`/`dynamodb_table`, or retire the root. It is a separate bucket in a separate region (§12).
9. **Required-job identity source** — static list versus derived-from-YAML, given SHA dependence (§5).

---

## 18. Exit integrity verification

Re-verified after writing this report.

| Property | Entry | Exit | Match |
|---|---|---|---|
| HEAD | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | same | ✓ |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | same | ✓ |
| Branch / divergence | `main`, 0/0 | same | ✓ |
| Staged paths | 0 | 0 | ✓ |
| Unmerged paths | 0 | 0 | ✓ |
| Tracked files differing from HEAD | 2 | 2 | ✓ |
| `PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2…` | `0c9981b2…` | ✓ |
| `tests/test_drift_classification.py` | `99a2917a…` | `99a2917a…` | ✓ |
| Stashes | 2 | 2 | ✓ |
| Worktrees | 1 | 1 | ✓ |

**Governed files changed: 0.** No stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase, branch switch, history rewrite, or worktree mutation. No CI dispatch, no PR, no repo-settings change, no deploy, no rollback. No S3/ECR/CloudFront/DNS mutation. No Terraform apply. No production DB change. No live Kubernetes. No real orders. No secret values printed.

All prior reports, prototypes, untracked artifacts, stashes, and worktrees intact.

---

## 19. Release certification matrix

| Gate | Status |
|---|---|
| Workflow invariants INV-1…INV-5 | **FAIL** (5/5 violated, §3) |
| Image provenance | **NOT ESTABLISHED** (§4) |
| ECR tag immutability | **UNKNOWN** (§4.6) |
| Release verifier usable | **FAIL** (9/9 identity mismatch, §5) |
| `01 PR Check` at candidate SHA | **FAIL** (6/6 runs, §6) |
| `05 Security` at candidate SHA | **FAIL** (×2, §6) |
| `03 Deploy` at candidate SHA | **FAIL** (gate refused, §6) |
| `06 Frontend Deploy` at candidate SHA | **success — and ungated** (§6) |
| Security evidence retention | **FAIL** (§3 INV-1, §7.1) |
| Dependency scan | **BLOCKED** / swallowed in CI (§7.4) |
| Container + IaC scan | **BLOCKED** (§7.5) |
| Tenant isolation | **NOT CERTIFIED** (4/150 cells, §8) |
| Worker lifecycle | **FAIL** (2 services inert, §9) |
| Credential vault | **FAIL** (mock substituted, §10) |
| Terraform state posture | **FAIL** on 1 of 3 roots (§12) |
| Line-ending defect | **WITHDRAWN**; latent gap remains (§13) |

**Verdict: DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

Nothing in this phase was weakened to reach a green result. No test, threshold, required job, fail-closed behaviour, tenant isolation control, anti-vacuity check, strict XFAIL, or provenance requirement was relaxed. The two fail-closed behaviours worth naming explicitly — `03-deploy.yml:168-173` and the invariant checker's `exit 1` — are **preserved**.

---

## 20. Claims corrected or withdrawn by this phase

Recorded so that readers of intermediate notes do not act on superseded conclusions.

| Claim | Disposition |
|---|---|
| "Shell scripts cannot run under bash as committed" (CRLF) | **WITHDRAWN** — local `core.autocrlf=true` artifact; all blobs LF (§13) |
| First byte-check via PowerShell redirection | **INVALID** — PowerShell rewrites line endings (§13) |
| Second Terraform root missing `key` | **CORRECTED** — `key` is present; `encrypt` and `dynamodb_table` are missing (§12) |
| Both s3 roots implicitly compared as one region | **CORRECTED** — `us-east-1` vs `ap-southeast-1`, different buckets (§12) |
| Phase 28–31: promote the Phase 18 verifier into the gate | **WITHDRAWN** — would refuse every release on identity mismatch (§5) |
| Digest "echoed and discarded" | **SUPERSEDED** — unreachable by event type (§4.2) |
| Redis security-group name collision | remains **WITHDRAWN** (§7.7) |
| `AWS-0054` | remains a confirmed **false positive** (§7.6) |
| W-6: 3 of 8 misclassified | **CORRECTED** to 10 of 20 over a larger sample (§11) |
| Trivy surface count 3 | **CORRECTED** to 4 (§7.5) |
| Hypothesis corpus explains CI divergence | **WITHDRAWN** — fresh corpus still passes locally (§8) |

---

*All `staging/` work products referenced in this report are **PROTOTYPE ONLY** and are not part of the governed codebase.*
