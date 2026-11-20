# Phase 26 — Frontend Incident Containment, Exact-SHA Release Gates, Parallel Critical Closure

**Destination verified ABSENT before writing.** **0 production files · 0 test files ·
0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 deployments · 0 stashes mutated.**
3 new files, all under `staging/`: this report + 2 test logs.

> ## DISPOSITION: **DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED**
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**

---

## 1. Executive summary — four new measurements, two of which change decisions

Phase 23 left two evidence gaps explicitly open: the published artifact's identity, and the
triage of the failed PR runs. **Both are now closed**, and the results change the containment
analysis and locate the correctness blocker precisely.

| # | New result | Effect |
|---|---|---|
| **1** | **The published artifact identity is recovered.** `index-EYjpdKQA.js`, SHA256 `6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312`, 509,689 bytes; 156 files / 3.7 MiB synced to `s3://vyomquant-frontend/`. | Incident evidence gap **closed** |
| **2** | **A build→deploy integrity check exists and passed.** The workflow re-echoes `Deployed Bundle SHA256` = the build-time checksum. The bytes deployed match the bytes built. | **Improves** the posture; narrows the risk |
| **3** | **The rollback candidate is NOT a distinct artifact.** `6c5660a1` (security GREEN) and `eee73265` (security RED) published the **identical** bundle — `index-DPALkFK6.js`, SHA256 `13dd072f…`. | **Weakens Option B's premise** |
| **4** | **The correctness blocker is ONE test, and it does not reproduce locally.** `01 PR Check`: 6 of 6 recent runs failed; in each, exactly one job (`Unit & Integration Tests`) and one test — `test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`. **It passes locally twice**, including with a fresh Hypothesis corpus. | Triage **narrowed to CI environment** |

Authorization is unchanged: **0 grants.** Phases 24 and 25 independently restate it — *"0 files
modified. No implementation was authorized"* and *"`AP-W1D-1` … `AP-W1D-5` all `NOT GRANTED`"*.

Legend used strictly: **PASS · FAIL · BLOCKED · NOT RUN · UNKNOWN**.

---

## 2. Entry and exit integrity manifests

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Branch · divergence | `main` · **0/0** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees · stashes | 1 · **2** | 1 · **2 — untouched** |
| `git reflog -1` | `ecd23a9e HEAD@{0}: commit: launch phase 1` | **identical** |
| Porcelain | 122 | 125 (+3 = this report + 2 logs) |

`git write-tree` is byte-identical at entry and exit and to the value recorded in Phases 17,
21, 22 and 23. **No tracked file has changed across Phases 17 → 26.**

### 2.1 Concurrent modifications — hashes recorded and unchanged

| Path | Blob hash (entry **and** exit) |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

**Neither touched.** 15 concurrent Phase 20–25 reports (multiple variants per phase number)
were **read only**; none overwritten. Destination verified absent before writing.

### 2.2 Stashes — read-only, unchanged

`stash@{0}` = `filter-branch: rewrite` (a filter-branch backup, not a stash). `stash@{1}` =
`99ceb9b0…`, 18 files, base `c673409` **not an ancestor of HEAD** — orphaned pre-rewrite WIP.
**Neither applied, dropped, popped, renamed nor rewritten.**

### 2.3 Tooling rechecked this phase (not assumed from prior phases)

| Tool | State |
|---|---|
| `gh` | present, **authenticated** |
| `bandit` | present in `.venv` — **1.9.4**, Python **3.12.10** |
| `trivy` | **ABSENT** → IaC scan **NOT RUN** |
| `docker` | **ABSENT** |
| Redis | `AERORA_TEST_REDIS_URL` unset · 6379 **CLOSED** |
| PostgreSQL | **51815 OPEN**; shared `pg_dsn.txt` says 51805 — **not edited** |
| AWS / Terraform state | **no access, not authorized** |

---

## 3. Authorization matrix

| Lane | Target paths | Decision | Approver | Grant | Ops used |
|---|---|---|---|---|---|
| A incident containment | S3 / CloudFront / DNS | — | release owner | **NOT GRANTED** | read-only CI queries |
| B frontend gate | `06-frontend-deploy.yml`, `tests/` | `D-P15-4` | workflow/release owner | **NOT GRANTED** (a *request*) | read-only |
| C verifier hardening | `scripts/require_security_scan.py` | `P22-B1` | security owner | **NOT GRANTED** | read-only |
| D correctness route | `01-pr-check.yml`, branch protection | `D-P15-3` | release owner | **UNDECIDED** | read-only |
| E image provenance | `02/03-*.yml`, `scripts/*`, `terraform/ecs.tf` | — | release + infra owner | **NOT GRANTED** | read-only |
| F Bandit parity | gate test, 3 migrations | — | code owner | **NOT GRANTED** | read-only |
| G security evidence | `05-security.yml` | `P23-K1` | security owner | **NOT GRANTED** | read-only |
| H runtime defects + tenant | routers, vault, classifier, RLS | `D-13-1/2/3/5` | code / DB / platform | **NOT GRANTED** | read-only + 2 local test runs |

**Files actually changed: none.** Ledger rows `:800-804` (`AP-W1D-1` … `AP-W1D-5`) all
**`NOT GRANTED`**; no row covers a workflow, script or Terraform path. `AP-W1D-3` remains
confined to its own classifier files. Phase 24/25 already assigned proposal IDs `P22-B1` and
`P23-K1`; I reference them rather than creating duplicates.

---

## 4. Frontend publication timeline and same-SHA evidence

### 4.1 Timeline (re-measured — CI history is time-dependent)

60 `06 Frontend Deploy` runs returned, **all `push`-triggered**, `needs:` count **0**.

| Published | SHA | Frontend | `05 Security` same SHA | Bundle SHA256 |
|---|---|---|---|---|
| `2026-10-11T09:48:45Z` | **`ecd23a9e`** | success | **failure, failure** | **`6f12b52c…4312`** ← **LIVE** |
| `2026-10-05T17:46:29Z` | `6c5660a1` | success | success, success | `13dd072f…620f` |
| `2026-10-04T09:40:39Z` | **`eee73265`** | success | **failure, failure** | **`13dd072f…620f`** |
| 11 earlier | … | success | success | — |

`ecd23a9e` is the **newest observed publication**; nothing superseded it.

### 4.2 Published artifact identity — RECOVERED (new)

From run `38129699497`'s log (`Record build artifact metadata`, step id `build-metadata`):

```
Bundle filename:        index-EYjpdKQA.js
Bundle SHA256:          6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312
Bundle size:            509689 bytes
Deployed Bundle SHA256: 6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312
aws s3 sync dist/ s3://vyomquant-frontend/   →  156 files, 3.7 MiB
```

**Two things this establishes.** The published artifact has a **recoverable identity**. And the
workflow performs its own **build→deploy integrity check** — the deployed checksum is re-derived
and matches the built checksum. That is a real control, it passed, and it means the bytes
deployed are the bytes built.

**One thing it does not establish.** `gh api /actions/runs/38129699497/artifacts` returns
**`total_count = 0`** — the workflow **retains no uploaded artifact**. The identity survives only
in a run log subject to retention. **There is no immutable stored copy to diff against or to
roll back to.**

### 4.3 The rollback candidate is not a distinct artifact (new, and it matters)

| Run | SHA | Security | Bundle |
|---|---|---|---|
| `37350937230` | `6c5660a1` | **GREEN** | `index-DPALkFK6.js` / `13dd072f…620f` |
| `37192914960` | `eee73265` | **RED** | `index-DPALkFK6.js` / `13dd072f…620f` |

**The frontend bundle did not change between `eee73265` and `6c5660a1`.** The identical bytes
were published twice — once under a red SHA, once under a green one.

> **Consequence for the containment decision:** "roll back to the last green SHA" would restore
> bytes that were **also** published under a failed-security SHA. It does not restore an artifact
> that ever passed a frontend security gate — because **no frontend security gate has ever
> existed.** Option B buys a different bundle, not a verified one.

### 4.4 What remains UNKNOWN

- **The exact bytes served at every CloudFront edge now.** A successful sync and invalidation
  prove the steps ran; they do not prove current edge state, nor that nothing else wrote to the
  bucket afterwards. **S3/CloudFront inspection is not authorized.**
- **Whether the published bundle is exploitable.** `05 Security` runs Bandit on `backend_app`,
  Trivy on the container image, and Trivy on IaC. **It does not scan the frontend bundle.** Its
  failure is therefore **not** evidence of a frontend defect, and its passing would **not** have
  been evidence of frontend safety. The checks that *do* inspect the bundle — artifact
  plausibility, Supabase config, API-host allow-list, CSP/header, WebSocket origin — **all
  passed**.

---

## 5. Owner containment decision — PREPARED, NOT AUTHORIZED

No rollback, republish, workflow freeze, S3 change, CloudFront invalidation, DNS change or
asset mutation was performed. **None is authorized.**

| Option | Evidence for | Risk / cost |
|---|---|---|
| **A — Forward-fix, no rollback** | `05 Security` does not scan the frontend bundle (§4.4). All bundle-inspecting checks passed. Build→deploy integrity verified (§4.2). | Leaves a build published without gate evidence in place. |
| **B — Roll back to `6c5660a1`** | That SHA's security workflow was green. | **Premise weakened by §4.3** — restores bytes also published under a red SHA. 6 days of frontend work reverted. `6c5660a1` **also failed `01 PR Check`** (§6.1). Requires authorized S3 + CloudFront ops. |
| **C — Investigate first** | Artifact identity is now known (§4.2), so a targeted review of `index-EYjpdKQA.js` is possible. | Slowest. Needs either the retained S3 object or a rebuild at `ecd23a9e`. |

**Smallest decision required — one question to the release owner:**

> *"Given that `05 Security` does not scan the frontend bundle, that all bundle-level checks
> passed, that build→deploy integrity was verified, and that the last green-security SHA
> published byte-identical content to a red-security SHA — does the frontend at `ecd23a9e`
> require rollback, or is forward-fix acceptable while the gate is closed?"*

**The incident is NOT resolved.** A sound future gate design does not close it. Status:
**OPEN**, affected build **LIVE**.

---

## 6. Correctness route — blocker located precisely

### 6.1 `01 PR Check` is failing on one test, not broadly (new)

| Run | SHA | Conclusion |
|---|---|---|
| `38061973739` | `f5b99e57` | failure |
| `38047483583` | `d07014d3` | failure |
| `38046625040` | `a624ce89` | failure |
| `38044138718` | `49e70da2` | failure |
| `38043513862` | `b43e990d` | failure |
| `37346853502` | **`6c5660a1`** | **failure** ← the Option-B rollback candidate |

**6 of 6 failed.** Job breakdown for the two most recent — **identical shape**:

| Job | `f5b99e57` | `d07014d3` |
|---|---|---|
| Frontend Build & Unit Tests | success | success |
| Code Quality & Static Audit | success | success |
| Format Backlog (report only) | success | success |
| **Unit & Integration Tests** | **failure** | **failure** |
| D5' Real-Redis Diagnostics (non-blocking) | success | success |
| Database Tests (PostgreSQL) | success | success |
| Execution Tests (production posture) | success | success |
| Docker Build Validation | success | success |

**Exactly one job fails, and within it exactly one test:**

```
tests/property/test_tenant_isolation_matrix.py::
  test_the_tenant_isolation_matrix_refuses_every_foreign_identifier   FAILED [2%]
```

Every sibling in that file passed — including `p41` cross-tenant read, `p42` cross-tenant
write, `p43` foreign-vs-absent indistinguishability, `p44` paper channel, `p45` supplied
identity, `p46` row ownership.

**The correctness gate for every release candidate is red because of one tenant-isolation
property test.** That is a notable convergence with Lane H, which is independently
NOT CERTIFIED.

### 6.2 It does not reproduce locally — two attempts (new)

| Attempt | Command | Result |
|---|---|---|
| 1 | `pytest "…::test_the_tenant_isolation_matrix_refuses_every_foreign_identifier" -p no:randomly --tb=short -q` | **1 passed**, 37.89s, **EXIT=0** |
| 2 | same, `-o addopts=""`, `HYPOTHESIS_DATABASE_FILE` → fresh empty dir | **1 passed**, 47.41s, **EXIT=0** |

Logs: `staging/p26_tenant_matrix.txt`, `staging/p26_tenant_fresh.txt`.

**Reproduction FAILED.** The test passes here with the local Hypothesis corpus and again with a
fresh corpus, so a stale example database is **not** the explanation.

**Honest conclusion: the failure is CI-environment-dependent and is NOT reproducible in this
environment.** I am not claiming the test or the code is sound — only that the failure does not
occur here. Candidate causes, **none verified**: test-ordering interference (both local runs
used `-p no:randomly`; CI's ordering may differ), platform difference (CI Linux vs local
Windows), environment variables or service availability present in CI, or a Hypothesis
falsifying example found under CI's different generation budget.

**Smallest next action:** the code owner reads the traceback from job `114242427268`
(`Run Unit Tests`, step 6) — the `--log-failed` output retrieved here contains the progress
listing but the traceback section was not within the retrievable portion.

### 6.3 Route options — unchanged, undecided

| Route | Requirement | Status |
|---|---|---|
| **1 — PR-only advancement of protected `main`** | An authorized repository-settings decision. **Branch protection was NOT inspected and `main` is NOT assumed protected.** | **UNDECIDED** |
| **2 — Push-candidate correctness workflow** | Mandatory checks against the exact push candidate, exposing same-SHA evidence. | **UNDECIDED** |

Neither was implemented; **branch protection was not modified.** Under either route, §6.1 shows
a skipped or failing mandatory job must not yield acceptable workflow-level evidence — which is
exactly the verifier gap in §7.2.

---

## 7. Frontend gate and verifier — status

### 7.1 Gate topology (re-measured)

| Workflow | Trigger | `needs:` | Gate |
|---|---|---|---|
| `02-build.yml` | `push [main, master]` | **none** | **none** |
| `03-deploy.yml` | `workflow_run` ← 02, `workflow_dispatch` | `needs: pre-deployment-validation` | `require_security_scan.py` |
| `05-security.yml` | `push`, `schedule` | none | own steps blocking |
| **`06-frontend-deploy.yml`** | `push`, `workflow_dispatch` | **none** | **none** |

`06-frontend-deploy.yml` grants AWS credentials at step 11 and publishes at steps 12–13 with
**no gate upstream**. **No gate was implemented — `D-P15-4` is NOT GRANTED.** A gate is not
implemented merely by existing in YAML; its dependency edges, triggers, conditions, permissions
and artifact identity must all enforce the policy, and none of that was authorized here.

### 7.2 Verifier — live job-level gap confirmed again

`scripts/require_security_scan.py` is strong on exact-SHA binding, missing-scan refusal,
`cancelled`/`skipped` refusal, API-error refusal, newest-run selection, bounded wait, and the
property that **`ALLOW_UNSCANNED` cannot override a failed scan**. **All preserved; nothing
weakened.**

Its gap is that it reads only the **workflow** conclusion. Measured job breakdown of
`05 Security` run `38132697285` at `ecd23a9e`:

| Job | Conclusion |
|---|---|
| Trivy Container Vulnerability Scanner | **success** |
| Bandit SAST & Dependency Security Scan | **failure** |
| Trivy IaC Misconfiguration & Secret Scan | **failure** |

**The verifier's documented rationale is therefore inaccurate.** Its docstring asserts the
workflow *"is red only for CRITICAL findings"* and that it inherits *"CRITICAL blocks, HIGH and
below report without blocking."* In fact the Bandit job fails on **9 × B608 MEDIUM** and turns
the workflow red. The gate is **more** conservative than documented — it fails safe — but the
stated design cannot be expressed by workflow-conclusion alone. Correcting that documentation is
part of proposal `P22-B1`. **NOT GRANTED.**

Pagination remains **latent, not live**: `total_count = 6` at this SHA against a `per_page=100`
cap. Reported as latent, not exaggerated.

---

## 8. Image provenance — unchanged, re-confirmed

| # | Stage | Behaviour |
|---|---|---|
| 1 | `02-build.yml:72` | builds `:$IMAGE_TAG` **and** `:latest` together |
| 2 | `:75-76` | pushes **both** — `:latest` advances **before any gate** |
| 3 | `:78` | `… \|\| echo "$IMAGE_TAG"` — **substitutes the tag for a digest** on lookup failure |
| 4 | `03-deploy.yml:168-173` | `… \|\| echo ""` then `exit 1` on empty/`None` — **genuinely fail-closed; PRESERVED** |
| 5 | `:174` | digest is **`echo`ed only** — never exported |
| 6 | `ecs_deploy_and_diagnose.py:901` | `image_uri = …:{IMAGE_TAG}` — **deploys by TAG** |

**The chain verifies a digest then discards it.** No `aws_ecr_repository` resource exists in any
Terraform root, so **tag immutability is UNKNOWN, not assumed** — and a SHA-named tag is not
inherently immutable.

Honest mitigations: `IMAGE_TAG` on the CI path is the **commit SHA**, not `latest`; and
`ecs_deploy_and_diagnose.py:94`'s `latest` default is **not reached** via `03-deploy.yml`, which
always sets `IMAGE_TAG` — it is reachable only by manual invocation. **Latent on the CI path.**

**No image was built, pushed, pulled, retagged, promoted, deleted or deployed.** Terraform's six
task-definition image references and `ecs-task-definition-full.json` were **not modified**.

---

## 9. Parallel lanes

| Lane | Status | Evidence |
|---|---|---|
| **F · Bandit parity** | **NOT GRANTED** | CI `bandit -r backend_app -ll`, no `continue-on-error`. **9 × B608 MEDIUM** in 3 alembic migrations. **bandit 1.9.4 / Python 3.12.10** (rechecked). Gate test scoped to **one file** (`GATED_MODULE`); the AST companion proves validator-constrained identifiers — a **different property** than B608 detects, so both hold at once. All 9 reviewed: validator-constrained identifiers or module literal constants, values parameter-bound. **9 safe constructions · 9 still findings · 0 suppressed · 0 owner-accepted.** Scan **NOT re-run** — command, source and version unchanged. Stale-`nosec` removal **NOT GRANTED**. |
| **G · security evidence** | **NOT GRANTED** | `05-security.yml` has **no `continue-on-error`**, so its steps are blocking. Two independent blocking IaC scans (`scan-ref: terraform`, `scan-ref: aerora_quant_platform/infrastructure`) plus a non-blocking CRITICAL,HIGH,MEDIUM report. Proposal already filed as `P23-K1` (Phase 23/24) — `if: always()` on uploads; Safety mandatory-vs-advisory undecided. Referenced, not duplicated. |
| **H · D7-1** | **NOT GRANTED** | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`), `market.py:53` (`except` → **unauthenticated public fallback**). `routers/exchange.py` **correct — unchanged**. Legitimate at `risk.py:313,325,407` and `paper_trading.py` ×8 — no global replacement. `market.py:56` guard preserved. Keys-absent policy **not invented**. |
| **H · D6-D → D7-2** | **NOT GRANTED** | Ordering re-confirmed: `APIKeyVault.__init__(self)` takes **zero args** but **fail-fast raises** `EnvironmentError`. A naive `KeyVault`→`APIKeyVault` swap raises **inside the same `try`** — identical user-visible outcome. Correct target is the initialized `app_state.vault`, which per D6-D is a `MockEngine`. **D7-2 must not precede D6-D.** No second vault; no per-cycle key derivation. |
| **H · W-6** | **NOT GRANTED** | **10 of 20 asserted misclassified** (source hash unchanged → REUSED). `retryable` **log-only** — 15 production occurrences, **0 control flow**. **No retries, backoff, duplicate submission or new order-control flow introduced.** Redaction intact at `:1236`. Default-lane coverage **zero**. |
| **H · tenant isolation** | **NOT CERTIFIED** | `apply_tenant_context` has **zero external consumers**. Three conflicting identity expressions agree only because no token carries a `tenant_id` claim — **owner choice still required**. Two **permissively OR-combined** policy families; `execution_records` predicate open. FORCE experiment REUSED: ENABLE+unset GUC → 2 rows; **ENABLE+FORCE+unset GUC → 0 rows silently**; FORCE+correct GUC → 1 row. **FORCE NOT enabled or prepared.** A local superuser run is not evidence — local `postgres` is `rolsuper`/`rolbypassrls`. **New link: the CI correctness blocker is a tenant-isolation property test (§6).** |
| **H · Redis / workers / migrations** | **BLOCKED / NOT GRANTED** | Redis env unset, 6379 **CLOSED**, no `redis-server`, no Docker → 5 tests **SKIP, not passes**; strict XFAIL preserved. `UNKNOWN` has **no authorized exit policy** (CAS pinned `'EXECUTING'`, `execution_record.py:1488`) → **W-5 behind W-4; W-3 behind W-6**. Baseline **not recaptured** (`AP-W1D-4` NOT GRANTED). No migration executed. |
| **H · Terraform / Trivy** | **NOT GRANTED · NOT RUN** | 4 unrestricted egress blocks: `security_groups.tf:22/52/73` + `redis-only/main.tf:58`. Redis SG names **identical by default**; **collision conditional on same VPC — UNVERIFIED** (main root *creates* `aws_vpc.main`; `redis-only` adopts hardcoded `vpc-0f3e7b3f243fbc133`). `redis-only` has **no backend block → local state**, 0 variables. **`AWS-0054` false positive** — `alb.tf` `dynamic` redirect + `lifecycle.precondition`; **compensating control preserved**. **Trivy ABSENT → NOT RUN. 0 of 5 accepted. No egress rule removed.** |

---

## 10. Commands, versions, exit codes

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `write-tree` / `status` / `stash list` / `reflog -1` / `hash-object` | 0 | §2; tree + both blob hashes unchanged |
| `gh run list --workflow 06-frontend-deploy.yml --limit 100` | 0 | 60 runs; `ecd23a9e` newest |
| `gh api /actions/runs/38129699497/artifacts` | 0 | **`total_count = 0`** |
| `gh run view 38129699497 --log` | 0 | **bundle identity + integrity check (§4.2)** |
| `gh run view 37350937230 --log` | 0 | `6c5660a1` bundle `13dd072f…` |
| `gh run view 37192914960 --log` | 0 | `eee73265` bundle **identical** (§4.3) |
| `gh run list --workflow 01-pr-check.yml --limit 6` | 0 | **6 of 6 failed** |
| `gh api /actions/runs/{38061973739,38047483583}/jobs` | 0 | one failing job each (§6.1) |
| `gh run view 38061973739 --log-failed` | 0 | failing test identified; traceback not in retrievable portion |
| `pytest …refuses_every_foreign_identifier -p no:randomly --tb=short -q` | **0** | **1 passed, 37.89s** |
| same, fresh `HYPOTHESIS_DATABASE_FILE`, `-o addopts=""` | **0** | **1 passed, 47.41s** |
| `python -m bandit --version` | 0 | **1.9.4 / Python 3.12.10** |
| `Get-Command trivy` / `docker` | — | **both ABSENT → NOT RUN** |

**Negative controls: none implemented** — every lane requiring a test file is ungranted.
**No timeouts occurred.** No CI dispatched; all `gh` calls were `list` / `view` / `GET`.

### 10.1 Evidence classes kept separate

| Class | Status |
|---|---|
| Source inspection | workflows, verifier, provenance chain, Terraform, 9 Bandit sites — **done** |
| Unit tests of unchanged code | **2 runs, both PASS** (§6.2) — tests of code I did not modify |
| Integration | **BLOCKED** — no Redis, no Docker, no non-superuser DB role |
| Registry (ECR) | **NOT INSPECTED** — unauthorized; tag immutability **UNKNOWN** |
| Terraform state | **NOT INSPECTED** — would settle the VPC question |
| Live cloud state (S3/CloudFront) | **NOT INSPECTED** — unauthorized; current edge bytes **UNKNOWN** |
| CI run history | **MEASURED** — §4, §6, §7.2 |
| Scanners | Bandit **reused**; **Trivy NOT RUN** |
| Release certification | **NONE** |

---

## 11. Outstanding decisions — copy-ready requests

**1 — Release owner, frontend incident (highest priority):**
> *"Given that `05 Security` does not scan the frontend bundle; that all bundle-level checks
> (artifact plausibility, Supabase config, API-host allow-list, CSP/header, WebSocket origin)
> passed; that build→deploy integrity was verified (deployed SHA256 = built SHA256
> `6f12b52c…4312`); and that the last green-security publication `6c5660a1` shipped
> byte-identical content (`13dd072f…620f`) to the red-security SHA `eee73265` — does the
> frontend at `ecd23a9e` require rollback, or is forward-fix acceptable while the gate is
> closed? I authorize / do not authorize an S3 + CloudFront rollback."*

**2 — Workflow/release owner, `D-P15-4`:**
> *"I authorize modifying `.github/workflows/06-frontend-deploy.yml` and adding tests under
> `tests/` to gate every publication-capable job on exact-SHA security and correctness
> evidence, with AWS credentials granted only after the gate succeeds, covering
> `workflow_dispatch`. No other workflow change is authorized."*

**3 — Security owner, `P22-B1`:**
> *"I authorize modifying `scripts/require_security_scan.py` and adding tests under `tests/` to
> check per-job conclusions against this mandatory-job inventory: [owner supplies]. Current
> factual inventory of `05 Security`: `Trivy Container Vulnerability Scanner`,
> `Bandit SAST & Dependency Security Scan`, `Trivy IaC Misconfiguration & Secret Scan`."*

**4 — Code owner, correctness blocker:**
> *"Triage `tests/property/test_tenant_isolation_matrix.py::test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`, which fails in CI (job `114242427268`, step 6) but passes locally twice (§6.2)."*

**5 — Code owner, tenant identity:** choose **one** of `dependencies.py:318`,
`routers/orders.py:819/940`, or `routers/distributed_execution.py:296`. Costs no code; unblocks
all context wiring.

**6 — Release owner:** `D-P15-3` route; and whether ungated builds may advance `:latest`.
**7 — Infra owner:** which root owns the ECR repo and `aws_vpc.main`'s identity; whether
`redis-only` was ever applied and where its state lives.
**8 — Security owner:** disposition of all 5 Trivy CRITICALs; `P23-K1`.
**9 — Code owner:** D7-1; **D6-D before D7-2**; W-6; Bandit parity.
**10 — DB / platform / auth owners:** D6-C 3 integers; D6-D 4 redacted fields; 1 claim-divergence integer.
**11 — Execution-state owner:** `UNKNOWN` exit policy. **12 — Coordinator:** isolated Redis.
**13 — Code owner:** disposition of orphaned `stash@{1}`.

### 11.1 Continuation plan

**Reuse, do not repeat:** the 9 Bandit findings; W-6's 10/20 matrix; D6-C's 7/7 fixtures; the
RLS FORCE experiment; the verifier source analysis; the provenance trace; and **this phase's
artifact identities and job breakdowns** — all valid while the tree hash stays `4b45a763…`.
**Re-measure** only CI run history (time-dependent) and anything whose source hash moves.

---

## 12. Final diff review and unauthorized-operation checklist

**There is no diff.** `git write-tree` = `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` at entry and
exit. Both concurrent files retain their entry blob hashes. Three files created, all new, all
under `staging/`.

| Prohibited operation | Performed? |
|---|---|
| Stage, commit, push, merge, rebase, reset, clean, revert, rewrite history | **No** — tree hash identical, 0 staged |
| **Mutate stashes** | **No** — 2 at entry, **2 at exit** |
| **Dispatch CI**, open PRs, **change branch protection** | **No** — all `gh` calls read-only; branch protection not even inspected |
| Roll back, republish, **freeze workflows**, modify S3, invalidate CloudFront, change DNS | **No** |
| Build, push, pull, retag, promote or delete images | **No** |
| Apply Terraform, provision infrastructure, read private state | **No** |
| Mutate production databases / execute migrations | **No** |
| **Enable or prepare FORCE RLS** | **No** |
| Start, restart or reconfigure shared services; edit `pg_dsn.txt` | **No** |
| Alter exchange credentials / place live orders / enable live trading | **No** |
| Print secrets, tokens, DSNs, keys or decrypted material | **No** |
| Weaken the backend gate, suppress findings, remove egress rules, lower thresholds, add `\|\| true` | **No** — `03-deploy.yml` digest refusal, the ALB precondition and the XFAIL contract all **preserved** |
| Overwrite a report or concurrent work | **No** — destination verified absent |
| Claim a skipped/unexecuted test or unavailable scanner passed | **No** — 5 Redis tests are skips; **Trivy NOT RUN**; the 2 local passes are explicitly tests of **unchanged** code |
| Claim tenant isolation certified | **No** — **NOT CERTIFIED** |

---

## 13. Final release recommendation

| Gate | Evidence for `ecd23a9e` | Status |
|---|---|---|
| Correctness | `01 PR Check` never ran at this SHA; **6 of 6 recent runs failed** on one tenant-isolation test | **ABSENT · RED** |
| Security scan | `05 Security` **failure** ×2; jobs: Container ✓, **Bandit ✗, IaC ✗** | **RED** |
| Frontend gate | `needs:` = 0; **published anyway; bundle `6f12b52c…` is LIVE** | **ABSENT · BREACHED** |
| Backend gate | `03 Deploy` **refused** | **WORKING** |
| Artifact provenance | verify-by-digest, **deploy-by-tag**; build-side tag-as-digest fallback; ECR immutability **UNKNOWN** | **WEAK · RED** |
| Terraform / Trivy | 5 CRITICAL, **0 accepted**, scanner **NOT RUN** | **RED / NOT RUN** |
| D7-1 · D6-D/D7-2 · W-6 | all open; D7-2 gated behind D6-D | **OPEN** |
| Tenant isolation | 0 context consumers; identity unchosen; non-owner matrix unexecuted | **NOT CERTIFIED** |
| Redis / quota | 5 tests **SKIPPED** | **UNPROVEN** |

**No release certification.** No mandatory gate is evidenced as passing for this release
identity. One gate was not merely absent — it was **bypassed, and the resulting bundle is still
serving**. The incident is **not** resolved by the soundness of a future gate design.

---

*Phase 26 closed. Tree hash unchanged at entry and exit. Both stashes untouched. 0 of 8 lanes
authorized; 0 files changed; 4 new measurements delivered (published artifact identity and
build→deploy integrity; rollback-candidate byte-identity; correctness blocker localized to one
test; two local non-reproductions of that test).*

***DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED.***
