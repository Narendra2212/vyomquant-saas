# Phase 34 — Gate Identity, Deployment Provenance, and Release Assurance

**Candidate commit:** `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` ("launch phase 1")
**Branch:** `main`, level with `origin/main` (0 ahead / 0 behind)
**Working-tree digest:** `git write-tree` = `4b45a7633f76cd8ab5be023c1e45af7f1a63c398`
**Source files changed by this phase:** **0**
**Release posture:** **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

Status vocabulary: `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, `UNKNOWN`, `NOT CERTIFIED`.

> **Report destination note.** The brief suggested
> `staging/PHASE34_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_ASSURANCE.md`. That path was **ABSENT**
> at my preflight and **PRESENT** by the time I was ready to write — a concurrent session created
> it mid-phase (31,753 bytes), alongside `PHASE34_CONTINUOUS_BLUE_GREEN_AND_RELEASE_RECOVERY.md`.
> I did not overwrite it. This report was written to a path verified absent immediately beforehand.

---

## 1. Preflight and final repository-integrity snapshots

Both snapshots are identical; no value drifted during the phase.

| Property | Preflight | Final |
| --- | --- | --- |
| branch | `main` | `main` |
| `HEAD` | `ecd23a9e…` | `ecd23a9e…` |
| `origin/main` | `ecd23a9e…` | `ecd23a9e…` |
| ahead / behind | 0 / 0 | 0 / 0 |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| tracked files | 5705 | 5705 |
| staged | 0 | 0 |
| unmerged | 0 | 0 |
| unstaged | 2 | 2 |
| stash entries | 2 | 2 |
| worktrees | 1 | 1 |
| untracked | 154 | **198** |

The untracked count rose from 142 (Phase 30) to 154 (my preflight) to 198 (final). That is
concurrent agent sessions writing their own artifacts. Nothing of theirs was modified, moved, or
removed. The two long-standing unstaged files are byte-identical to every prior phase:

| Path | Blob |
| --- | --- |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

**Environment.** Python 3.12.10, pytest 9.1.1, Bandit 1.9.4, `gh` 2.100.0, PyYAML 6.0.3.
**`trivy`, `docker`, `terraform`, `kubectl` all ABSENT.** The `aws` CLI **is** present at
`C:\Users\aa\aws_cli_extract\…\aws.exe`; it was **never invoked against any AWS resource**.
PostgreSQL 5432 and Redis 6379 are unavailable — evidenced below by mass skips, not assumed.

**PowerShell note.** Long inline commands were `^C`-mangled twice and a `>` redirect produced
UTF-16LE that broke a JSON parse. Both were handled with helper scripts written **outside** the
repository (`%TEMP%`) and multi-encoding decode. No cleanup operation touched repository paths.

---

## 2. Authorization ledger status

Authoritative register: `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, blob
`7e238620bc22f0ad246f0ebc373b7032c6b28ef0` — **unchanged at preflight and at close**. Rows 800–804:

| ID | Request | Owner | Status |
| --- | --- | --- | --- |
| `AP-W1D-1` | Per-statement completeness predicates for 022–025, read-only session | DB owner | **NOT GRANTED** |
| `AP-W1D-2` | Insert 4 ledger rows for FULLY APPLIED migrations | DB owner | **NOT GRANTED** |
| `AP-W1D-3` | 4-way drift classification + 10 regression tests | Code owner | **NOT GRANTED** |
| `AP-W1D-4` | `_provenance` envelope + snapshot recapture | DB + code owner | **NOT GRANTED** |
| `AP-W1D-5` | Install `pgserver`; isolated cluster + `aerora_app` role | Coordinator + DB owner | **NOT GRANTED** |

`NOT GRANTED` occurs exactly 5 times — once per row.

I ran a repository-wide scan for grant language (`I authorize`, `GRANTED:`, `APPROVED:`,
`Status…GRANTED`) across all `staging/*.md`, excluding `NOT GRANTED`. Every hit resolves to one of
four non-grants:

1. **Request templates authored by the requesting session**, in blockquotes — Phases 16, 23, 24,
   25, 26, 28, 29, 30, 31 all carry `> *"I authorize …"*` copy-ready text.
2. **Reports recording the absence of grants** — "Total granted: 0", "Grants on record | **0**",
   "GRANTED: 0. REJECTED: 0. UNRESOLVED: 6."
3. **An explicit self-disclaimer** — `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` is quoted in
   the D10 report as *"**THIS DOCUMENT IS NOT AN APPROVAL.** It was authored by the implementation
   session."*
4. **One genuine but consumed historical grant** — `AP-W1D-3` in
   `PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md:104`, scoped to four drift-classifier files and
   fully consumed at A29. Ledger line 802 is stale with respect to it and still reads
   `NOT GRANTED`. **I did not edit it.**

**Scopes granted for this phase: none. Scopes exercised: none.** Every lane below that required a
governed edit is `BLOCKED`, and no governed file was modified (§12).

---

## 3. Verified work queue and source hashes

Every file this phase inspected or considered editing, hashed at preflight and re-hashed at close.
All byte-identical across the phase.

| Path | Blob |
| --- | --- |
| `.github/workflows/01-pr-check.yml` (836 lines) | `91d55a379115c88edd2fd6be01871b82a75ef498` |
| `.github/workflows/02-build.yml` (82) | `42c7696eb7cb993b8942b7f0053bf7490e3e5edd` |
| `.github/workflows/03-deploy.yml` (193) | `eb8078eac06268447d60f26ef7a97a537ceed988` |
| `.github/workflows/05-security.yml` (161) | `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` |
| `.github/workflows/06-frontend-deploy.yml` (615) | `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` |
| `scripts/require_security_scan.py` | `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` |
| `scripts/deploy.sh` | `e06423f2b3b075add617778c662146c0c4754657` |
| `scripts/blue-green-deploy.sh` | `a2a2e15086ec48ce7633b79df45d02eab085efa5` |
| `scripts/ecs_deploy_and_diagnose.py` | `cec975b267e844e6c770a865258bae0f1e9edfc7` |
| `infra/deploy.sh` | `915dcca935826498db67eff771be8efb23e1929c` |
| `pytest.ini` | `a6f0a3d4bed34dc7b8eefccc087681f81fcb81e5` |
| `tests/test_release_artifacts.py` (285) | `5e613adb47517096daedd70a98d2796521e7c987` |
| `staging/phase18_release_gate.py` (311) | `5c70dbbfb882036badade2ff01b4bf687595ff4e` |
| `staging/phase18_test_release_gate.py` (307) | `3dd575192edbb8df26f3bcee0ba6fef7992e0483` |
| `staging/phase17_frontend_release_gate.py` | `4c38728fb360801ef9ee46a34e0b8db407095d04` |
| `staging/phase17_test_frontend_release_gate.py` | `13b5e6cd1f12aa313bbf706b8eec09fec33a1928` |
| `terraform/ecs.tf` | `7d05ee95c77731dce1f88f4be1a080e5d22272d2` |
| `terraform/redis-only/main.tf` | `48b9471035b77d4f85b9b5140dae8b37830b4a01` |
| `ecs-task-definition-full.json` | `dad405d6124d23c5bad032f58fc06ef29a31127f` |
| `new_task_def.json` | `d6b75124d0d45d786632566ebdedd1ebed646ea2` |
| `td22.json` | `a9e4e2cc4697744c7ff917bc2c6e878d93202b5e` |
| `backend_app/routers/market.py` | `16f0377cb9f6c85ef0fb21ce3df37940bd27628e` |
| `backend_app/routers/orders.py` | `ff2d34bf030a3086d2af63729c025bbef87ca045` |
| `backend_app/routers/exchange.py` | `4b40973568edfa4dc24a8416819f872eaf18d54c` |
| `backend_app/backend/api_key_vault.py` | `74a0059411467442b6aaa3d11d0d89af4d00fe65` |
| `backend_app/backend/portfolio_cache_updater.py` | `a74d76448a15150f703f05d0e43b83fd8470007b` |
| `backend_app/backend/exchange_executor.py` | `df4fd42a151015124ae7eb3743733107106c413e` |

**Reuse discipline.** The seven runtime-correctness files above are byte-identical to the hashes
Phase 30 recorded, so that analysis is reused under the identity rule. Bandit was **re-run from
scratch** rather than reused (§8). Priority A was **re-derived from YAML and then confirmed against
live API payloads** rather than reused, because the earlier finding came from a concurrent session
whose expected names were themselves YAML-derived.

**Premises I revalidated and found changed:** `terraform/ecs.tf` now hashes
`7d05ee95…`; `scripts/blue-green-deploy.sh` and `infra/deploy.sh` exist (Phase 30 never inspected
them); `.github/workflows/blue-green-deploy.yml` named in the brief is **ABSENT**;
`new_task_def.json` and `td22.json` are tracked `:latest` carriers Phase 30's census omitted.

---

## 4. Priority A — the gate identity defect, independently confirmed and corrected

### 4.1 Confirmed: 9 of 9 configured identities cannot match

`staging/phase18_release_gate.py` configures (lines ~60–70):

```python
REQUIRED_WORKFLOWS = {
    "05 Security": ("sast-scan", "container-security", "iac-scan"),
    "01 PR Check": ("static-audit", "unit-tests", "paper-mode-tests",
                    "database-tests", "docker-check", "frontend-tests"),
}
```

`_assert_mandatory_jobs_passed()` builds `by_name = {job["name"]: job}` and looks each configured
string up in it. I parsed both workflows with PyYAML. **Every job in both files carries an explicit
`name:`**, so the API reports a display name and the YAML key is never exposed:

| Workflow | job id | API display name |
| --- | --- | --- |
| 05 Security | `sast-scan` | `Bandit SAST & Dependency Security Scan` |
| 05 Security | `container-security` | `Trivy Container Vulnerability Scanner` |
| 05 Security | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` |
| 01 PR Check | `static-audit` | `Code Quality & Static Audit` |
| 01 PR Check | `format-check` | `Format Backlog (report only)` (`continue-on-error: true`) |
| 01 PR Check | `unit-tests` | `Unit & Integration Tests` |
| 01 PR Check | `paper-mode-tests` | `Execution Tests (production posture)` |
| 01 PR Check | `database-tests` | `Database Tests (PostgreSQL)` |
| 01 PR Check | `provisioning-and-isolation` | `Provisioning, Privilege Hardening & Tenant Isolation` |
| 01 PR Check | `docker-check` | `Docker Build Validation` |
| 01 PR Check | `frontend-tests` | `Frontend Build & Unit Tests` |

**Configured identities: 9. Matched: 0. Unmatched: 9.** `PASS`-equivalent verification of a `FAIL`
condition.

### 4.2 Confirmed against the live API, which the prior analysis had not done

I fetched real job payloads (read-only `gh api`) for `01 PR Check` run `38061973739` and
`05 Security` run `37350937170`:

- **7 of 8** YAML-derived display names appear verbatim in the `01 PR Check` payload; **2 of 3** in
  the `05 Security` payload. The two absences are explained in §4.4, not by a naming error.
- **Zero** configured job IDs appear as an API `name`.
- The job object's keys are
  `['check_run_url','completed_at','conclusion','created_at','head_branch','head_sha','html_url','id','labels','name','node_id','run_attempt','run_id','run_url','runner_group_id','runner_group_name','runner_id','runner_name','started_at','status','steps','url','workflow_name']`.
  `id` is a **numeric** job id. **The YAML job key appears nowhere in the payload**, so matching by
  YAML key is not merely wrong — it is impossible.

### 4.3 The failure direction is fail-closed, and that changes the severity

Because every lookup returns `None`, `_assert_mandatory_jobs_passed()` raises `GateRefusal` on the
first mandatory job. The gate therefore **refuses every candidate, including a fully green one.**

This is **not** a security hole — it never admits an unsafe run. It is a correctness and
availability defect: as configured the gate would block all releases, and its own refusal message
(`jobs present: [...]`) would have printed the real display names on first contact with the API.
A concurrent session's negative control
(`staging/phase30_gate_jobname_negative_control.txt`) reaches the same conclusion, and my
independent derivation agrees with it on all nine identities and on the direction.

Its passing tests cannot detect this because their fixtures set the API `name` field to the
configured identifier — fixture and configuration share the same error, so they agree with each
other and not with GitHub.

### 4.4 New finding: the mandatory job set is commit-dependent

This is the part no prior report establishes, and it changes the required design. Comparing the
workflow YAML at two commits via `git show`:

| Workflow | at `6c5660a1` | at `ecd23a9e` (candidate) |
| --- | --- | --- |
| `05-security.yml` | **2 jobs** (`sast-scan`, `container-security`) | **3 jobs** (adds `iac-scan`) |
| `01-pr-check.yml` | **7 jobs** | **8 jobs** (adds `provisioning-and-isolation`) |

The two "missing" API names in §4.2 are fully explained: `iac-scan` did not exist at `6c5660a1`,
and `provisioning-and-isolation` did not exist at the commit behind run `38061973739`.

I initially hypothesised that `05 Security` run `37350937170` concluding **success** with only two
jobs was evidence of a skipped mandatory job — **that hypothesis was wrong and I discarded it.**
The workflow genuinely had two jobs at that commit, so the run is accurate evidence for its own
commit.

The real consequence is a design requirement: **a hardcoded mandatory-job list is only correct for
commits whose workflow matches it.** A gate pinned to today's list would wrongly refuse a valid
historical commit, and a gate pinned to an older list would wrongly admit a commit whose newer
mandatory job never ran. The expected job set must be **derived from the workflow YAML at the
candidate SHA** and then matched against that SHA's API evidence.

### 4.5 Corrected premise: the `D5'` job has no current role

The brief asked me to reinspect `D5' Real-Redis Diagnostics (non-blocking)` and obtain an owner
policy decision before changing its classification. Evidence:

- It **is** a real API job — it appears in run `38061973739` with conclusion `success`.
- It exists in the workflow at **neither** `6c5660a1` **nor** `ecd23a9e`.
- A text search for `Redis.*Diagnostic` across all seven workflow files returns **nothing**. The
  only `D5'` token is a comment at `01-pr-check.yml:186` about the `AERORA_TEST_REDIS_URL`
  environment variable.

So the job existed on a branch whose workflow version is not at HEAD, and **has since been
removed**. There is nothing at HEAD to classify and no owner decision is needed for it. Recorded
as **`UNKNOWN` → resolved to "not present at HEAD"**.

`provisioning-and-isolation` is different: it **does** exist at the candidate commit, it is listed
in the prototype's `ADVISORY_JOBS` with a written reason, and whether it is release-mandatory
remains a genuine **owner decision**. I did not change its classification.

Status: **`FAIL`** (defect confirmed, fail-closed direction). Repair: **`BLOCKED` — no grant.**

---

## 5. Priority B — promotion, discovery, and mutation-resistant tests

Revalidated: `pytest.ini` is unchanged (`a6f0a3d4…`) with `testpaths = tests`,
`python_files = test_*.py *_test.py`, and `norecursedirs` **not** including `staging`.
`tests/test_release_artifacts.py` is tracked, in default discovery, and **8 passed** (§9).

The discovery constraint the brief sets — add promoted tests to normal discovery **without**
broadening `testpaths` into `staging/` — is satisfied by file placement alone, and
`tests/test_release_artifacts.py` is the working precedent: it already asserts workflow structure
and step ordering from a tracked module under `tests/`. Promotion to
`scripts/require_release_gate.py` + `tests/test_release_gate.py` needs **zero** config change.

The mutation matrix the brief requires (skipped, absent, renamed display name, failed, cancelled,
wrong SHA, missing run, ambiguous run, API failure, incomplete pagination, mandatory-misclassified-
as-advisory) is specified and ready. I did **not** write it to `tests/`, because creating the
promoted production verifier it is meant to exercise is itself the ungranted act, and a test suite
for a module that does not exist is not a deliverable.

Status: **`BLOCKED` — no grant.** Mechanism proven; no file created.

---

## 6. Priority C — frontend publication, with the insertion point now identified

`06-frontend-deploy.yml` revalidated end to end. Trigger and topology:

```
:4   on:
:5     push:
:6       branches: [main, master]
:7       paths: ['algo22-terminal/**', '.github/workflows/06-frontend-deploy.yml']
:10    workflow_dispatch:
:18  jobs:
:19    deploy-frontend:        # the only job
```

A scan for `needs:`, `workflow_run`, `05 Security`, and `require_security_scan` across all 615
lines returns **nothing**. One job, no dependencies, **no binding to the commit's security
verdict**. The `paths:` filter explains why it fired on `ecd23a9e`: the commit touched
`algo22-terminal/**`.

**Step ordering, established this phase:**

| Step | Line |
| --- | --- |
| Checkout / Node / deps / env validation | 27, 30, 37, 41 |
| Build frontend | 80 |
| Reject implausible release artifacts in `dist/` | 92 (refuses at 134) |
| Verify Supabase config embedded in build | 139 |
| Verify production API config embedded in build | 179 |
| Record build artifact metadata | 237 |
| **Configure AWS Credentials** | **254** |
| `aws s3 sync dist/` ×3 | 261 → 289, 299, 307 |
| CloudFront invalidation | 323 |

This is a materially useful result for the proposed remedy: **credentials are already acquired as
late as possible** — after every build-content check and immediately before the first sync. A
commit-bound authorization step inserted **before `:254`** would be fail-closed, would precede all
cloud credentials, and would require **no reordering** of the existing steps, no change to the
three sync passes and their deliberate `--delete` and cache-control semantics, and no change to
the artifact-size gate.

The size gate at `:92` is real and fail-closed: it enumerates `dist` artifacts, applies 20 MiB
floors for `.exe`/`.dmg` and 10 MiB for `.AppImage`/`.deb`, and `exit 1`s at `:134`. One bounded
limitation: its `CHECKED` counter can be 0 and the gate still passes. That is correct for the
hazard it targets (a placeholder *present*) but gives no assurance that advertised installers
*exist* — a separate property held by the component-side tests in the same module and by vitest.

The two concerns must stay separate: artifact plausibility and release authorization protect
different properties.

Status: **`FAIL`** (no commit-bound release policy on a surface that publishes to production).
Remedy: **`BLOCKED` — no grant.** No live S3 or CloudFront state was inspected; repository
evidence cannot establish which bytes are currently served at the edge.

---

## 7. Priority D — image identity across every deployment surface

### 7.1 Producer

`02-build.yml`: `IMAGE_TAG: ${{ github.sha }}` (`:69`); builds and pushes **both**
`:$IMAGE_TAG` **and** `:latest` (`:72`, `:75`, `:76`) **before any gate**. Line 78:

```
DIGEST=$(aws ecr describe-images … --output text 2>/dev/null || echo "$IMAGE_TAG")
```

On lookup failure this **substitutes the tag for the digest**. Confirmed; still the one genuine
producer-side defect.

**Refinement of Phase 30.** `02-build.yml:5` **does** declare `workflow_call`, with `image_tag`
and `image_digest` as workflow-level outputs at `:6-12`. But a repository-wide search finds **no**
`uses: ./.github/workflows/…` caller anywhere. So the workflow is *callable* and *nothing calls
it* — the digest remains consumed by nothing. Independently, `03-deploy.yml:168` performs **its
own** `describe-images` lookup rather than reading the build's output, which is why the producer's
digest is dead weight.

### 7.2 `03-deploy.yml` — correct, and deliberately untouched

```
:168  DIGEST=$(aws ecr describe-images … 2>/dev/null || echo "")
:169  if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
:172    exit 1
```

Fail-closed on both empty and the literal `None`. **Not edited.** Blob `eb8078ea…` verified
unchanged at close. On the candidate commit, run `38130273012` failed at the security-scan gate and
the ECS job was **skipped** — correct behaviour.

### 7.3 Consumers — census now 7 tracked sources, 13 references

| Source | Refs | Detail |
| --- | --- | --- |
| `terraform/ecs.tf` | 6 | `:28`, `:126`, `:198`, `:284`, `:378`, `:478` — `vyomquant-{api,tee,mds,websocket,simulator}:latest` |
| `ecs-task-definition-full.json` | 2 | `:18` `273709947018.dkr.ecr…/vyomquant-api:latest`; `:211` `questdb/questdb:latest` |
| `td22.json` | 1 | `:7` `273709947018.dkr.ecr…/vyomquant-api:latest` — **new, tracked** |
| `new_task_def.json` | 1 | `:194` `questdb/questdb:latest` — **new, tracked** |
| `scripts/ecs_deploy_and_diagnose.py` | 1 | `:94` `os.getenv("IMAGE_TAG","latest")`; registry hardcoded `:95` |
| `scripts/deploy.sh` | 1 | `IMAGE_TAG=${2:-latest}` → `kubectl set image` |
| `scripts/blue-green-deploy.sh` | 1 | tag-based `kubectl set image`; see §7.4 |

All seven are **tracked**. `infra/deploy.sh` contains **no** image reference at all (only AWS auth
failure handling) — it is **not** an image-consuming surface, which narrows the census rather than
widening it. `.github/workflows/blue-green-deploy.yml` named in the brief is **ABSENT**, so the
blue-green path has no workflow entry point in this repository.

**ECR tag immutability: NONE FOUND.** No `aws_ecr_repository` resource and no
`image_tag_mutability` declaration exists in any `.tf` file. Immutability is **undeclared**, so
nothing in the repository prevents `:latest` from being repointed.

### 7.4 New finding — the blue-green promote path can set the stable deployment to `unknown`

`scripts/blue-green-deploy.sh`:

```
:53  get_green_image() {
:54    kubectl get deployment $GREEN_DEPLOYMENT … -o jsonpath='…' 2>/dev/null || echo "unknown"
:56  }
…
     promote() {
       local ACTIVE_COLOR=$(get_active_color)
       local GREEN_IMAGE=$(get_green_image)
       if [ "$ACTIVE_COLOR" != "green" ]; then   # guards the COLOUR
         log_error "Green is not active. Switch to green first."; exit 1
       fi
:247   kubectl set image deployment/$BLUE_DEPLOYMENT \
:248     backend=$GREEN_IMAGE                      # NOT guarded
```

`promote()` validates `ACTIVE_COLOR` but **never validates `GREEN_IMAGE`**. If the lookup fails,
`GREEN_IMAGE` is the literal string `unknown`, which is a syntactically valid image reference
(`docker.io/library/unknown:latest`), so `kubectl` would accept it and the **stable blue**
deployment would be repointed at a non-existent image during a promotion.

The asymmetry is the tell: `deploy()` at `:66-69` **does** validate, refusing an empty
`IMAGE_TAG`. The defect is structurally identical to `02-build.yml:78` — a `||` fallback
substituting a sentinel where a real identity is required.

Status: **`NOT CERTIFIED`** — `IMAGE PROVENANCE NOT ESTABLISHED` stands. Repairs: **`BLOCKED`**.
No deployment command was invoked, no ECR call made, no image pulled, pushed, or retagged, and no
cluster contacted. The production role of both Kubernetes scripts remains **`UNKNOWN`**.

---

## 8. Priorities E, F, G — security evidence, diagnostics, and Bandit parity

### 8.1 Priority E — evidence retention (`05-security.yml`)

Three independent jobs: `sast-scan` (`:16`), `container-security` (`:47`), `iac-scan` (`:108`).

| Finding | Evidence |
| --- | --- |
| SAST report upload has **no** `if:` condition | `:41-45` `Upload Security Reports` / `path: reports/`. Bandit fails at `:36`, so the report is lost exactly when it matters. The IaC upload at `:156` **does** carry `if: always()` — the asymmetry proves oversight. |
| Safety can never fail | `:39` `safety check … \|\| echo "Safety advisory logged"` |
| Second IaC root never executes | `:120` scans `terraform` with `exit-code: 1`; `:131` scans `aerora_quant_platform/infrastructure` (directory **PRESENT**) as a later step in the **same job**. First-step failure means `:131` and the MEDIUM report at `:144` never run. |
| `pip-audit` comment references a step that does not exist | `:91` discusses `pip-audit`; `:30` installs only `bandit safety`. Confirmed. |

The brief's two guardrails are respected: adding `if: always()` to an **upload** preserves
artifacts without touching the scan's exit status, and for the unexecuted second root the correct
label is **`NOT RUN`** — I did not fabricate an empty report. Whether Safety should block is an
**owner decision**; I left the behaviour unchanged.

### 8.2 Priority F — gate diagnostics

`scripts/require_security_scan.py` carries two claims unsupported by the evidence the gate has:

- `:14-16` — *"05-security.yml already runs Trivy with severity: CRITICAL and exit-code: 1, so the
  workflow is red only for CRITICAL findings… CRITICAL blocks, HIGH and below report without
  blocking."* True of the **container** step only. Bandit at `:36` runs `-ll` and fails the
  `sast-scan` job on **MEDIUM**, so the workflow is red for MEDIUM findings.
- `:123-124` — *"…so this means a CRITICAL container vulnerability is present. Fix the pin and push
  again."*

I falsified the second against **job-level API evidence for the candidate run** `38129699454`:

| Job | status | conclusion |
| --- | --- | --- |
| `Trivy Container Vulnerability Scanner` | completed | **success** |
| `Trivy IaC Misconfiguration & Secret Scan` | completed | **failure** |
| `Bandit SAST & Dependency Security Scan` | completed | **failure** |

The message names the **one job that passed**, and prescribes fixing a pin that was not the cause.
This is exactly the inference the brief prohibits — deriving a job-level verdict from a run-level
conclusion.

Gate properties verified **sound** and not to be weakened: `WORKFLOW_NAME = "05 Security"` module
constant (`:41`); exact-SHA binding via `?head_sha=` (`:58`); name-filtered run selection (`:59`);
explicit `ALLOW_UNSCANNED` override (`:66`); refusal when no run exists (`:100`); refusal on
`cancelled`/`skipped` (`:118-119`). One real limitation: `per_page=100` is **unpaginated**.

### 8.3 Priority G — Bandit, re-run not reused

```
python -m bandit -r backend_app -ll -f json   ->  exit 1
files 404 | LOC 219,319 | HIGH 0 | MEDIUM 9 | LOW 160 | results 9
all 9 = B608 MEDIUM
```

| Migration | B608 lines |
| --- | --- |
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |

Identical to Phase 30 on every figure, established by a fresh scan rather than reuse. All nine
interpolate **identifiers** validated by `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`), with data
bound via `:re` parameters; parameterization does not apply to an identifier position.

The structural problem persists: the suite is green while the gate is red.
`test_bandit_reports_nothing_in_the_module_the_gate_failed_on` is scoped to a **single** module
(`transactional_execution_manager.py`) and structurally cannot observe the migration findings; the
companion AST test scans `rglob("*.py")`, is `# nosec`-immune, and already lists
`_validate_table_name` in `SQL_SAFE_VALIDATORS`, so the migrations pass it legitimately. The gap is
the first test's scope.

Status: Bandit gate **`FAIL`**; suite **`PASS` but not probative**; rescoping **`BLOCKED`**.
Still rejected: `continue-on-error`, `|| true`, `-lll`, `--baseline`, blanket `# nosec`, severity
lowering, migration exclusion, skip-to-pass conversion.

---

## 9. Tests and scans actually executed

| # | Command | Result | Duration |
| --- | --- | --- | --- |
| 1 | `pytest tests/test_release_artifacts.py -q` | **8 passed**, exit 0 | 9.94 s |
| 2 | `pytest tests/test_tenant_isolation_library_paper.py -q` | **56 passed**, exit 0 | 89.33 s |
| 3 | `pytest tests/property/test_tenant_isolation_matrix.py -q` | **11 passed**, exit 0 | 190.78 s |
| 4 | release/CI policy layer, 7 modules (below) | **122 passed**, exit 0 | 202.06 s |
| 5 | `bandit -r backend_app -ll` | **exit 1**, MEDIUM 9 | ~132 s |
| 6 | `pytest -q` (full default suite) | **DID NOT COMPLETE** | see §9.1 |
| 7 | `gh api …/jobs?per_page=100` ×4 runs | exit 0, read-only | — |

Layer 4 modules: `test_release_artifacts.py`, `test_nightly_audit_workflow.py`,
`test_bandit_sast_gate_is_green.py`, `test_cicd_deployment_integrity.py`,
`test_no_secrets_in_bundle.py`, `test_stale_duplicate_frontend.py`, `test_drift_classification.py`
(the last being my A29 contribution, 92 cases, still unstaged and still green).

Prerequisites: layers 1–4 require no database or Redis. Layer 5 requires Bandit only.

### 9.1 The full default suite did not complete — stated plainly

Two attempts. The first reached **13%** and the process exited **without a summary line** (no
`passed`/`failed` totals were ever written). The second was restarted, reached ~1%, and I
terminated it deliberately because CPU contention was causing my verification shell calls to time
out and return empty output — I would rather have reliable evidence for twenty lanes than an
unreliable run of one.

**I therefore do not claim the full suite passes.** Its status is **`NOT RUN` (incomplete)**. What
the partial run did establish, and it is worth keeping, is the shape of the database-dependent
skips:

| Module | Skips |
| --- | --- |
| `test_bug049_identity_types_postgres.py` | 76 |
| `test_bug057_listing_moderation_authz_postgres.py` | 40 |
| `test_billing_data_isolation_postgres.py` | 27 |

These are real, measured evidence that PostgreSQL-dependent certification is `NOT RUN` in this
environment, not an assumption drawn from a closed port.

---

## 10. Negative-control results

| Control | Outcome |
| --- | --- |
| Feed the Phase 18 gate an **all-green** run using the **real** API job names | **REFUSED** on the first mandatory job, for both workflows |
| Feed the same gate the same run with names forced to the **configured** identifiers | **ADMITTED** |

The pair isolates the fault precisely: the matcher logic is sound, and the **configuration** is
wrong. Had only the first control been run, "the gate refuses" would not have distinguished a
broken matcher from a broken config.

Two further controls already in the repository and still valid: the prototype's
`test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped` demonstrates the Phase 17
skipped-job defect by executing against the Phase 17 module, which is why Phase 17 is retained as
**negative-control evidence only and never promoted**; and
`test_the_comment_stripper_keeps_what_renders` in `tests/test_release_artifacts.py` is an
anti-vacuity control proving that module's scanner has not been silently gutted.

No negative control was created by mutating production source.

---

## 11. Remaining failures, blocked lanes, and the decisions that unblock them

### 11.1 Consolidated status

| # | Lane | Status |
| --- | --- | --- |
| 1 | Repository integrity | `PASS` |
| 2 | Authorization `AP-W1D-1…5` | all **`NOT GRANTED`** |
| 3 | Gate job-identity defect (9/9 unmatched, fail-closed) | **`FAIL`** → `BLOCKED` |
| 4 | Commit-dependent mandatory job set | **`FAIL`** (design) → `BLOCKED` |
| 5 | `D5' Real-Redis Diagnostics` classification | resolved: **not present at HEAD** |
| 6 | `provisioning-and-isolation` mandatory status | **`UNKNOWN`** — owner |
| 7 | Promoted-test discovery mechanism | `PASS` (proven) |
| 8 | Gate promotion + mutation suite | `BLOCKED` |
| 9 | `03-deploy.yml` ECS gate | `PASS` — do not edit |
| 10 | `06-frontend-deploy.yml` release policy | **`FAIL`** → `BLOCKED` |
| 11 | `06-frontend-deploy.yml` artifact size gate | `PASS` |
| 12 | Frontend credential ordering | `PASS` — insertion point exists |
| 13 | `02-build.yml:78` tag-as-digest | **`FAIL`** → `BLOCKED` |
| 14 | Build digest consumed by nobody | **`FAIL`** → `BLOCKED` |
| 15 | ECR tag immutability | **`UNKNOWN`** (undeclared) |
| 16 | Blue-green `promote()` unvalidated image | **`FAIL`** → `BLOCKED` |
| 17 | `scripts/deploy.sh` production role | **`UNKNOWN`** — owner |
| 18 | SAST evidence retention | `BLOCKED` |
| 19 | Second IaC root | **`NOT RUN`** → `BLOCKED` |
| 20 | Safety blocking vs advisory | **`UNKNOWN`** — owner |
| 21 | Gate diagnostics (`:14-16`, `:123-124`) | **`FAIL`** → `BLOCKED` |
| 22 | Bandit gate | **`FAIL`** (MEDIUM 9) |
| 23 | Bandit gate-test scope | `PASS` but not probative → `BLOCKED` |
| 24 | D7-1 invalid call sites | **`FAIL`** → `BLOCKED` |
| 25 | D7-2 absent-module import | **`FAIL`** → `BLOCKED` |
| 26 | W-6 error classification | **`FAIL`** → `BLOCKED` |
| 27 | Tenant isolation, in-process | `PASS` (67 tests) |
| 28 | Tenant isolation, RLS | **`NOT CERTIFIED`** |
| 29 | Trivy IaC, local | **`NOT RUN`** (tool absent) |
| 30 | Terraform validate/plan | **`NOT RUN`** (tool absent) |
| 31 | `redis-only` local state | risk → owner |
| 32 | Full default suite | **`NOT RUN`** (incomplete) |
| 33 | Live trading | **`OFF` / `NOT CERTIFIED`** |

### 11.2 Runtime correctness (Priority H), revalidated

**D7-1.** Real signature at `api_key_vault.py:177` is `load_decrypted_keys(self, user_id, exchange_id)`
— two parameters. Three invalid sites pass `access_token=user.get("access_token")`:
`market.py:50` (kwarg at `:53`), `orders.py:789`, `orders.py:1095`. `exchange.py:993` is
**correct** (two positional) — this corrects my own Phase 16 error, as Phase 17 established.
Independent corroboration found this phase: `strategy_operations.py:3196` documents the contract as
``load_decrypted_keys(user_id, exchange_id)``. The 14 legitimate `access_token` call sites in
`risk.py` and `paper_trading.py` target `paper_trading_service` and **must survive** any fix — a
global text replacement would break all 14. Live impact: `market.py:49-58` wraps the invalid call
in `try/except Exception` and falls back to an **unauthenticated public connection**, so
authenticated market data is silently anonymous for every user; permissive mocks at
`tests/test_market_none_keys_fix.py:13,:19` accept a signature the real object rejects. The
keys-absent policy is an **owner decision** and I preserved the existing code rather than inventing
one.

**D7-2.** `portfolio_cache_updater.py:151` imports `KeyVault` from
`backend_app.backend.key_manager`. The module is **absent repo-wide** and the real class is
**`APIKeyVault`** (`api_key_vault.py:62`). The import sits at method scope **before** the `try:` at
`:157`, so the failure is unprotected. Refinement this phase: the call at `:159` is
`vault.load_decrypted_keys(user_id, exchange_id)` — **already correct**, so D7-2 is purely
absent-module plus wrong class name, not a signature defect. Per the brief I did **not** simply
swap in `APIKeyVault`: the real class fails fast without configuration, and the application-state
vault path is unresolved, so the correct fix is dependency injection from initialized application
state — a design decision requiring a grant.

**W-6.** `_handle_ccxt_error` precedence read directly:

```
:1239  "insufficient"/"balance"                 -> InsufficientFundsError
:1242  "rate limit"/"too many requests"         -> RateLimitError
:1245  "symbol"/"market"                        -> InvalidSymbolError  (retryable=False)
:1248  "network"/"timeout"/"connection"         -> NetworkError        (retryable=True)
:1251  "authentication"/"apikey"/"key"          -> AuthenticationError
:1255  retryable = any(w in ["timeout","network","temporarily","busy"])
```

Three confirmed defects: the **symbol/market** test precedes the network test, so a network failure
whose text contains "market" is classified non-retryable; the bare `"key"` substring also matches
*monkey*, *keyspace*, *keyword*; and — **new this phase** — because `:1248` returns first,
`"timeout"` and `"network"` in the generic list at `:1255` are **unreachable**, leaving only
`"temporarily"` and `"busy"` live.

Blast radius, measured: `ExchangeError.retryable` has two constructor writes
(`exchange_executor.py:92`, `exchange_simulator.py:67`) and its only read is the log f-string at
`:1687`. **Correction to Phase 30's phrasing:** the repository *does* contain control-flow retry
logic — `replay_safe_transaction_guard.py:302-347` has its own `_is_retryable_error()` driving
`if not …: raise`, and `paper_simulator.py` carries retry machinery including a
`_NoRetryPastTheMoney` guard at `:3146`. Those are **separate mechanisms keyed on their own
predicates**, not on `ExchangeError.retryable`. So the accurate statement is: this misclassification
affects **log accuracy only**, while genuine retry control flow lives elsewhere and is unaffected.
Per the brief I propose **no** new retry behaviour around order side effects.

### 11.3 Tenant isolation (Priority I) — green, and still not certified

Both suites pass: **56 + 11 = 67 tests**. The brief's premise of "four failures among 150 attempted
cells" **does not reproduce** at this commit.

The property matrix is rigorous on its own terms: cells are derived from `app.router.routes`, with
`len(MATRIX) == len(rows) × len(RESOURCE_KINDS)` asserted, no duplicate cells, every row and every
resource kind attempted somewhere, and unattempted cells required to carry a reason of at least 40
characters.

But passing does **not** certify isolation, and the module says so itself:

1. **RLS is not exercised.** There is no PostgreSQL here; the double answers like a service-role
   client — deliberately the **weaker** path, so an endpoint whose only scope is an unrunnable
   policy fails here rather than passing.
2. **The Requirement 21.4 audit-log half is not asserted** — the recorder is silenced because it
   needs a Redis this host lacks.
3. **`REQUIREMENT_21_4_GAPS` pins seven recorded defects** of today's Marketplace API — "not
   exemptions". Cases landing on a gap assert the pinned signature pair, so closing a gap turns the
   file red. The green result therefore **coexists with seven known open defects**.

I did not reorder production dependencies, weaken a status code, collapse a failure reason, enable
`FORCE ROW LEVEL SECURITY`, alter a policy, create a role, or apply a migration.
**`TENANT ISOLATION NOT CERTIFIED` stands.**

### 11.4 Infrastructure (Priorities J, K)

Redis 6379 and PostgreSQL 5432 unavailable; integration tests correctly **skipped**, never
reinterpreted as passes. No Redis resilience or execution-state claim is made from unit tests.

Terraform roots: `terraform/providers.tf` declares `backend "s3"`; **`terraform/redis-only/main.tf`
has no backend block at all** → local state, for a root that manages production ElastiCache. Local
state has no shared locking and no reliable shared history. The earlier security-group **name
collision premise remains withdrawn** (different VPCs; AWS scopes SG names per VPC) and I found no
new evidence to revive it. Note the main root also contains `terraform/redis.tf`, so Redis is
declared in both roots — context for the owner, not a collision claim.

The five Trivy IaC findings are carried from **CI evidence and source reading only**. `trivy` is
**ABSENT**, so there is no local scan: `AWS-0054` on `alb.tf:52` remains a documented false
positive (a `lifecycle.precondition` is present); `AWS-0104` on `security_groups.tf:26` and `:56`
are accepts, `:56` load-bearing; the two Redis egress rules (`:77`, `redis-only/main.tf:62`) are
safe to tighten but **tightening both does not close the gate**, because the two `AWS-0104` accepts
remain. No cloud state was inspected and nothing was applied.

### 11.5 Decision-ready requests

These are **requests**. Writing them here does not authorize them, and I will not treat a later
restatement of this table as a grant.

| ID | Exact change | Files | Owner decision needed |
| --- | --- | --- | --- |
| **G-1** | Choose the D7-1 route (drop the 3rd arg at 3 sites, or widen the signature) **and** state the keys-absent policy | `market.py:53`, `orders.py:792`, `orders.py:1098`, + contract-faithful mocks | correctness route + product policy |
| **G-6** | Correct two unsupported diagnostic claims | `scripts/require_security_scan.py:14-16`, `:123-124` | none beyond approval |
| **G-4** | `if: always()` on the SAST upload; schedule the 2nd IaC root independently | `05-security.yml:41`, `:131` | plus: is Safety mandatory? |
| **G-2** | Remove the tag-as-digest fallback | `02-build.yml:78` | plus: approved digest policy |
| **G-7** | Validate `GREEN_IMAGE` before `kubectl set image` | `scripts/blue-green-deploy.sh:236-248` | plus: is this path live? |
| **G-3** | Promote the Phase 18 verifier with **YAML-derived, SHA-scoped** job identities; add the mutation suite; bind a gate into the frontend workflow before `:254` | `scripts/require_release_gate.py`, `tests/test_release_gate.py`, `06-frontend-deploy.yml` | plus: is `provisioning-and-isolation` release-mandatory? |
| **G-5** | Rescope the Bandit gate test beyond one module | `tests/test_bandit_sast_gate_is_green.py` | disposition process for the 9 B608 |
| — | Declare ECR `image_tag_mutability` | new/existing `.tf` | platform owner |
| — | Accept or remediate `redis-only` local state | `terraform/redis-only/` | platform owner |
| — | State whether `scripts/deploy.sh` is a live production path | — | one sentence |

**Highest-value single decision remains G-1**: its cheapest form needs zero file edits to decide,
it is the only lane with confirmed live user impact, and it unlocks the correctness evidence G-3
depends on. **G-6 and G-7 are the lowest-risk**: two text blocks, and one guard added beside an
existing guard of the same shape.

No owner is discoverable from the repository: there is no `CODEOWNERS`, `OWNERS`, or `MAINTAINERS`
file, and all commits carry a single author. That is itself a finding, and it is why these
decisions have stayed open across many phases.

---

## 12. Prohibited-action attestation and concurrent-work preservation

**Not performed, at any point:** stage, commit, push, reset, stash, pop, drop, clean, revert,
merge, rebase, branch switch, history rewrite, worktree change; PR creation; CI dispatch;
repository-settings or branch-protection change; deploy, rollback, publish, retag, image push/pull/
delete; any ECR, S3, CloudFront, DNS, IAM, or Terraform-state mutation; `terraform apply`; any real
`kubectl` command; production database or role mutation; production migration; live trading or any
real order.

The `aws` CLI is installed on this host and was **never invoked**. The only network calls were
read-only `gh api` GETs for workflow-run and job evidence (4 runs).

**Credential hygiene.** `exchange_keys.json`, `.env`, `.env.corrupt.backup`, and
`staging/pg_dsn.txt` exist and are gitignored. None was opened and no secret value was read or
printed. Service availability was established by TCP connect probes and by observed test skips.

**Concurrent work preserved.** 198 untracked artifacts, the 2 unstaged files, 2 stash entries, and
1 worktree are all intact. Both long-standing unstaged blobs are byte-identical (`0c9981b2…`,
`99a2917a…`). Nothing under `staging/` was deleted, moved, or overwritten — including the
concurrently created `PHASE34_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_ASSURANCE.md`, which caused me
to relocate this report rather than overwrite it. No governed file changed: all 26 hashes in §3 are
identical at preflight and close. The authorization ledger is **unmodified** (`7e238620…`).

Nothing was created inside the repository by this phase except this single report. All helper
scripts were written to `%TEMP%`, outside all governed paths.

---

## 13. Final release-readiness decision

**DO NOT RELEASE.** Justified by evidence, not posture:

1. **No commit-bound mandatory-job evidence can be produced.** The only job-level verifier in the
   repository cannot match a single one of its nine configured job identities against what the
   GitHub API reports (§4.1–4.2), and the production gate inspects the run-level conclusion only.
2. **`01 PR Check` never ran for the candidate commit** — it triggers on `pull_request` only — so
   the eight checks it owns produced **no evidence at all** for `ecd23a9e`.
3. **`05 Security` failed for the candidate commit**, with SAST and IaC failing at job level
   (§8.2), and Bandit still exits 1 locally with 9 MEDIUM findings (§8.3).
4. **The frontend surface published to production anyway** and remains structurally incapable of
   consulting the commit's security verdict (§6).
5. **Image provenance is not established**: the producer can substitute a tag for a digest, the
   digest it computes is consumed by nobody, registry immutability is undeclared, 13 mutable-tag
   references span 7 tracked sources, and the blue-green promote path can repoint the stable
   deployment at a non-existent image (§7).
6. **Tenant isolation is not certified**: RLS is never exercised in this environment and seven
   Requirement 21.4 gaps are pinned as recorded defects (§11.3).
7. **Three runtime-correctness defects remain open**, one of which silently downgrades every
   user's authenticated market data to an anonymous connection (§11.2).
8. **The full default suite did not complete** (§9.1), and database-dependent certification is
   `NOT RUN`.

Local test passes do not change this. **189 distinct tests passed** in the layers I could run
(122 + 56 + 11; the 8 release-artifact tests are contained within the 122, not additional), and
that is evidence about those layers only.

**Posture reaffirmed:**

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

The limiting factor is **authorization, not analysis**. Every lane above is specified to the file
and line, with acceptance tests identified. Nothing further can be implemented without a grant.
