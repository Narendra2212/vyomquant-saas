# Phase 23 — Release Authorization, Image Provenance, Gate Enforcement, Parallel Defect Closure

**Destination verified ABSENT before writing. No existing report overwritten.**

**0 production files · 0 test files · 0 workflows · 0 Terraform · 0 migrations · 0 governance
records changed.** No stage, commit, push, merge, rebase, reset, clean, revert, history rewrite,
stash mutation, CI dispatch, PR, deployment, `terraform apply`, provisioning, image promotion,
production database access, or live order.

**DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED ·
DEPLOYMENT BLOCKED.**

---

## 1. True starting point — enumerated, not assumed

The brief's filenames are not a reliable guide to completed work. Measured: **Phase 16–22 reports
all exist**, several with multiple same-numbered variants from concurrent sessions.

| Report | Written |
|---|---|
| `PHASE22_IMAGE_PROVENANCE_AND_RELEASE_GATE_CLOSURE.md` | 21:14:18 |
| `PHASE22_AUTHORIZED_RELEASE_EXECUTION_AND_CRITICAL_CLOSURE.md` | 21:14:18 |
| `PHASE18_AUTHORIZATION_AWARE_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` | 21:10:27 |
| `PHASE21_RELEASE_SAFETY_EXECUTION_AND_CRITICAL_CLOSURE.md` | 21:07:48 |
| `PHASE21_RELEASE_GATE_IMPLEMENTATION…` / `PHASE21_CROSS_PHASE…` | 20:51:27 |
| `PHASE19_FRONTEND_GATE_AND_CRITICAL_CLOSURE.md` (mine) | 20:48:40 |
| `PHASE17_EVIDENCE_RECONCILIATION…` / `PHASE17_RELEASE_GATE…` | 20:38–20:39 |

**Consequence for this phase:** Lane D (image provenance) and Lane F (Redis SG collision) are
**already on record** in the two Phase 22 reports — which also found a **fourth** mutable-tag
consumer I had not (`ecs-task-definition-full.json:18`) and correctly *refined and partly
downgraded* an earlier collision claim using the same per-VPC reasoning I derived independently.
**I therefore do not restate them.** §4 and §5 below contain only what those reports do not have.

---

## 2. Authorization matrix — still empty

| Evidence | Measurement |
|---|---|
| Ledger `**GRANTED**` occurrences | **0** |
| Ledger rows 800–804 (`AP-W1D-1`…`-5`) | **all `NOT GRANTED`** |
| `PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | *"no authorization arrived"* |
| Phase 22's own matrix (independent session) | lanes **A–J all `NOT GRANTED`**; *"no row covers a workflow, script, Terraform file"* |

| Lane | Paths | Decision ID on file | Grant |
|---|---|---|---|
| A correctness route | `01-pr-check.yml`, branch protection | — | **NONE** (and option 1 is a repo-settings decision, not code) |
| B mandatory-job verifier | `scripts/require_security_scan.py` | `P22-B1` | **NONE** |
| C frontend gate | `06-frontend-deploy.yml` + test | `P16-A1…A4` | **NONE** |
| D image provenance | `02-build.yml`, `ecs_deploy_and_diagnose.py`, `ecs.tf`, `ecs-task-definition-full.json` | `P21-D1 / P22-A1` | **NONE** |
| E Bandit | 3 migrations + gate test | — | **NONE** |
| F Terraform / Trivy | both roots | — | **NONE**; scanner **absent** |
| G D7-1 | `market.py`, `orders.py` + tests | `P17-C1` | **NONE** |
| H D7-2 | `portfolio_cache_updater.py` | `P17-G1` | **NONE** |
| I W-6 | `exchange_executor.py` | — | **NONE** |
| J tenant / Redis / workers | — | — | **NONE** + environment-blocked |

**No governed file was modified.** No approval was inferred from this brief, a decision ID that
records a *request*, a prior recommendation, the prototype's existence, role titles, commit
authorship, or silence.

---

## 3. Entry manifest

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **unchanged** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees | 1 | 1 |
| Stash | 2 entries — `filter-branch: rewrite`, `WIP on main: c673409` | **unmutated** |

**Concurrent work preserved byte-for-byte:** `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (` M`)
and `tests/test_drift_classification.py` (` M`, worktree `99a2917a…`). Neither staged nor reverted.

---

## 4. NEW — the `latest` fail-open is confirmed empirically, not just at source

Prior phases established the three source-level links. **What was missing was CI-observed proof
that the mutable alias actually advanced.** Obtained this phase without dispatching anything.

### 4.1 The build step that pushes `latest` succeeded on the failed commit

`02-build.yml` (82 lines, `clean`), trigger `push` at `:13`:

```
:69  IMAGE_TAG: ${{ github.sha }}
:72  docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG -t $ECR_REGISTRY/$ECR_REPOSITORY:latest -f Dockerfile .
:75  docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
:76  docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
```

`VER` Run **38129699471**, `headSha = ecd23a9e…`, event `push`, job *"Build & Push Docker Image to
ECR"* = **success**, with step-level detail:

```
success   Login to Amazon ECR
success   Build, tag, and push API image to ECR     ◄── pushes BOTH $IMAGE_TAG and :latest
success   Complete job
```

Against the same SHA: **`05 Security` = failure** (runs 38129699454 push, 38132697285 schedule) and
**`01 PR Check` = never ran**.

> **So `latest` was advanced to a commit that failed its security scan and has zero correctness
> evidence, and the push step reported success. The `latest` tag is pushed in the *build* job on
> *push*, with no `needs:` on any gate — it cannot be otherwise ordered as written.**

**Honest limit:** this proves the push was *issued and reported successful*. I hold no AWS
credentials, so **I cannot verify what `latest` resolves to in ECR right now**, and I do not claim
a particular image is currently running.

### 4.2 Why that matters for deployment selection

`scripts/ecs_deploy_and_diagnose.py:94` — `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")`, consumed
at `:901` as `image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"`. A deploy invoked **without**
an explicit `IMAGE_TAG` therefore selects `latest` — which §4.1 shows tracks the newest *unvalidated*
push. Combined with `02-build.yml:78`'s `|| echo "$IMAGE_TAG"` digest fallback (a **tag** substituted
for a **digest**, with `2>/dev/null` hiding the cause), the chain is fail-open at three independent
points.

`terraform/ecs.tf` pins `:latest` in **all six** task-definition image references (`:28`, `:126`,
`:198`, `:284`, `:378`, `:478`) — re-verified this phase.

**This is the same fail-open class as two prior incidents in this repository:** the PyJWT
CVE-2026-102268 token-forgery image that shipped while `05 Security` was red (recorded in
`require_security_scan.py`'s own docstring), and the ungated frontend publish. **Third instance,
same root cause: a release path with no same-SHA gate.**

---

## 5. NEW — prototype reconciliation, as §1.8 requires

Two independent gate implementations exist. Nobody had reconciled them. **I ran the prototype:**

```
python -m pytest staging/phase17_test_frontend_release_gate.py -q
→ 42 passed in 4.57s          exit 0
```

`staging/phase17_frontend_release_gate.py` is 280 lines; its test file is 263 lines / 42 cases,
including `test_refuses_the_actual_ecd23a9e_incident` — a regression test for the real event.

### 5.1 Capability comparison, measured

| Property | `scripts/require_security_scan.py` (production) | `staging/phase17_frontend_release_gate.py` (prototype) |
|---|---|---|
| Workflows gated | **`05 Security` only** (`WORKFLOW_NAME = "05 Security"`) | **`REQUIRED_WORKFLOWS = ("05 Security", "01 PR Check")`** — both |
| Refusing conclusions | fail-closed by final `return 1`, but only `cancelled`/`skipped` named | **enumerated frozenset** incl. `timed_out`, `stale`, `action_required`, `neutral` — *"so a new GitHub conclusion value fails closed instead of silently passing an `!= "failure"` test"* |
| `head_sha` re-verification | **query filter only**, not re-asserted | `_assert_run_is_admissible` — *"defence against API filtering drift"* |
| `head_branch` allow-list | **0 references** | yes — a SHA reachable from several branches |
| Pagination page-walk | **NO** — `per_page=100`, no loop | paginated (`_PER_PAGE`) |
| Manual-dispatch bypass | `allow_unscanned` (narrow, logged, missing-scan only) | explicitly refuses |
| **Job-level conclusions** | **NO — `/jobs` refs = 0** | **NO — `/jobs` refs = 0** |

### 5.2 Two findings from this comparison

**(a) The Lane B job-level gap is open in BOTH implementations.** Measured: neither queries
`/jobs` or `check-runs`. Both read only the run-level `conclusion`. So a workflow that concludes
`success` while a **mandatory job was skipped** would be admitted by either. This confirms the
brief's §4 premise and shows the prototype does **not** already solve it — a point no prior report
makes.

**(b) The prototype's 42 tests never run, for a precise and trivially fixable reason.** `pytest.ini`
sets `python_files = test_*.py *_test.py`. The file is named
`phase17_test_frontend_release_gate.py`, which matches **neither** pattern:

```
'phase17_test_frontend_release_gate.py' -like 'test_*.py'  -> False
'phase17_test_frontend_release_gate.py' -like '*_test.py'  -> False
```

`norecursedirs` does not exclude `staging/`, and `testpaths` is empty — so the directory is not the
obstacle; **the filename is.** Additionally `tests/test_frontend_release_gate.py` does **not**
exist, and `01-pr-check.yml` contains **0** references to `phase17`. **A fully-tested verifier is
sitting inert: 42 green tests that no CI run has ever executed.**

### 5.3 Recommended layering

The two assertion sets test **different things** and both are needed:

| Layer | Source | Tests |
|---|---|---|
| **Verifier runtime behaviour** | **Phase 17 prototype — supersedes my Phase 19 verifier assertions** | 42 cases: conclusions, SHA attribution, branch allow-list, pagination, HTTP/JSON/payload failure modes, newest-run both directions |
| **Workflow structure** | **My Phase 19 YAML assertions remain complementary** | A1–A6 + B1–B3, negative control **7/7** — gate job present, `needs:` wired, `DEPLOY_SHA` is `${{ github.sha }}`, `ALLOW_UNSCANNED: 'false'`, `actions: read`, no ungated publish path, backend regression lock |

Neither layer substitutes for the other: the prototype cannot detect a workflow that never calls
it, and my structural assertions cannot detect a verifier that mis-decides.

---

## 6. Lanes A–J — status, reusing valid same-SHA evidence

Every governed target re-hashed `clean` this phase. Evidence marked **[reused]** is unchanged in
source, tool version and environment since the cited phase.

| Lane | Status | Evidence |
|---|---|---|
| **A** correctness route | **BLOCKED — owner decision.** `01-pr-check.yml` remains `pull_request`-only (`workflow_dispatch` 0, `push` 0); `frontend-tests` additionally conditional on change detection, so a *workflow-level* green does not prove the frontend job ran | **[reused Phase 19]** |
| **B** mandatory-job verifier | **BLOCKED — grant absent.** Job-level gap open in **both** implementations (§5.2a). Production also lacks pagination, `head_branch`, and gates only 1 of 2 required workflows | **NEW this phase** |
| **C** frontend gate | **BLOCKED — grant absent.** Design validated; workflow still has no gate, no `needs:`, no `permissions:` block | **[reused Phase 19, 7/7]** |
| **D** image provenance | **BLOCKED — grant absent.** Source links on record in Phase 22; **CI-observed confirmation NEW** (§4) | **NEW + [Phase 22]** |
| **E** Bandit | **BLOCKED — grant absent.** Single gated module **exit 0 / 0 findings**; full tree **exit 1 / 9 B608 MEDIUM**. Parity test + both negative controls validated **4/4** | **[reused Phase 19]** |
| **F** Terraform / Trivy | **BLOCKED — grant absent; `terraform` and `trivy` both ABSENT locally, so no scan was run and none is claimed.** SG name collision and 5 CRITICAL dispositions on record in Phase 22 | **[Phase 22]** |
| **G** D7-1 | **BLOCKED — grant absent.** 3 invalid sites (`market.py:53`, `orders.py:792`, `orders.py:1098`) vs **9 legitimate** `access_token` callers that must not be touched | **[reused Phase 19]** |
| **H** D7-2 | **BLOCKED — grant absent.** `key_manager` module absent; remedy is the existing singleton `dependencies.py:659 get_vault()`. `order_watchdog.py` vault-method discrepancy kept **separate** | **[reused Phase 19]** |
| **I** W-6 | **BLOCKED — grant absent.** `.retryable`: 3 occurrences, 2 assignments + 1 log f-string at `:1687`, **zero control-flow reads** — a classification fix cannot alter retry behaviour, and I introduce none | **[reused Phase 19]** |
| **J** tenant / Redis / workers | **BLOCKED — grant absent + environment.** `FORCE ROW LEVEL SECURITY` **0 matches** in executable SQL; FORCE RLS **not** enabled. Quota **60 passed / 5 skipped**; debounce **9 passed / 1 xfailed** — **not** an all-pass suite; real-Redis **UNEXECUTED**. W-4 undecided so **W-5 untouched**; no `UNKNOWN` semantics inferred. Snapshot and drift baseline preserved | **[reused Phase 19]** |

---

## 7. Commands, exit codes, environment

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status` / `reflog` / `stash list` / `ls-files` / `hash-object` | 0 | §3 manifest; stash unmutated |
| Ledger + A23E11 grant scan | 0 | **0 `**GRANTED**`**; 5 rows `NOT GRANTED` |
| Report enumeration | 0 | Phase 16–22 all exist (§1) |
| `gh run view 38129699471 --json jobs` | 0 | **`Build, tag, and push API image to ECR` = success** (§4.1) |
| `git show HEAD:.github/workflows/02-build.yml` | 0 | `:69/:72/:76/:78` (§4) |
| `Select-String scripts/ecs_deploy_and_diagnose.py` | 0 | `:94` default `latest`; `:901` URI |
| `Select-String terraform/ecs.tf` | 0 | **6** `:latest` image refs |
| **`pytest staging/phase17_test_frontend_release_gate.py -q`** | **0** | **42 passed in 4.57s** |
| Prototype vs production capability diff | 0 | §5.1 — `/jobs` refs **0 in both** |
| `pytest.ini` + filename pattern check | 0 | prototype **not collected** (§5.2b) |

**Environment:** Python 3.12.10 · bandit 1.9.4 · PyYAML 6.0.3 · **terraform ABSENT** · **trivy
ABSENT** · no PostgreSQL · no Redis · no AWS credentials · `01 PR Check` undispatchable.

**One command aborted:** a full `pytest --collect-only -q` exceeded the 120 s limit on an
~11,000-test tree; replaced with a targeted `pytest.ini` + filename-pattern check, which answered
the collection question deterministically.

**Not executed:** any CI dispatch, PR, deployment, image build/push/promotion, `terraform`
validate/plan/apply, Trivy scan, PostgreSQL or Redis connection, tenant lane, real-Redis quota lane,
live exchange call. No secret, token, DSN or signed payload was read or printed.

---

## 8. Files written

| File | Nature |
|---|---|
| `staging/PHASE23_RELEASE_EXECUTION_AND_CRITICAL_CLOSURE.md` | this report |

All new, untracked, under `staging/`. **No governed file touched → no diff to review.**
`git write-tree` not computed, because computing it writes objects into the shared database that a
concurrent session is actively using; the tracked tree is provably unchanged instead by
`staged = 0`, `unmerged = 0`, `tracked = 5705`, an unchanged reflog head, and the two pre-existing
` M` paths being the *only* tracked modifications — the same set recorded at entry.

---

## 9. Owner decisions — copy-ready

> **1 · Collect the verifier that already works** *(smallest high-value action in this phase)*
> *"I authorize moving `staging/phase17_frontend_release_gate.py` to `scripts/` and its tests to
> `tests/test_frontend_release_gate.py` so the 42 cases are collected by `python_files = test_*.py`,
> and wiring the module as the release verifier. No behaviour change."*
>
> **2 · Close the job-level gap in both verifiers (Lane B).**
> *"I authorize extending the release verifier to query each required workflow run's `/jobs` and
> require every mandatory job to conclude `success`, rejecting missing, skipped, cancelled, failed
> or malformed job evidence, with pagination and a negative control per property."*
>
> **3 · Frontend gate (Lane C).** *"I authorize adding a `security-gate` job to
> `06-frontend-deploy.yml` running the verifier with `DEPLOY_SHA: ${{ github.sha }}` and no override,
> an explicit `permissions:` block including `actions: read`, and `deploy-frontend` depending on it;
> plus `tests/test_frontend_deploy_is_gated.py`."*
>
> **4 · Image provenance (Lane D).** *"I authorize: removing the `:latest` tag/push from
> `02-build.yml`, or gating it behind the required checks; replacing the `|| echo "$IMAGE_TAG"`
> digest fallback with a hard failure; making a missing/empty `IMAGE_TAG` a hard error in
> `ecs_deploy_and_diagnose.py:94`; and replacing the six `:latest` pins in `terraform/ecs.tf` plus
> `ecs-task-definition-full.json:18` via an approved image-selection contract."*
>
> **5 · Correctness route (Lane A).** PR-only advancement of `main` via branch protection
> **(recommended — closes frontend and backend together)**, or a main-branch correctness workflow.
> Must treat a skipped mandatory job as not-success.
>
> **6 · Bandit (Lane E).** Parity test **first**, then per-site `# nosec B608` justifications, then
> the stale suppression at `backend_app/test_perf.py:26`.
>
> **7 · Terraform (Lane F).** Resolve the Redis SG **ownership boundary** before any rule edit;
> removing the two egress rules does not close the gate while `AWS-0054` and the two conventional
> `AWS-0104` findings remain.
>
> **8 · D7-1 / D7-2 / W-6 / tenant / A23 / W-4.** As previously scoped; D7-1 must name **only** the
> three `load_decrypted_keys` callers.

---

## 10. Release recommendation

**DO NOT RELEASE.** Evidence-based, every item measured this phase or reused with provenance:

1. **Image provenance is fail-open and the alias has already moved** — `latest` was advanced to
   `ecd23a9e` by a successful build step while that commit's security scan failed and its
   correctness lane never ran (§4). A tag is not a digest, and the digest step falls back to the tag.
2. **`05 Security` is red** — 9 Bandit B608 + 5 Trivy CRITICAL, 0 fixed.
3. **No correctness evidence exists for the candidate SHA** — `01 PR Check` never ran and is
   PR-only.
4. **The frontend publish path has no gate**, and has already published this commit.
5. **A working verifier exists but is inert** — 42 green tests, collected by nothing, wired to
   nothing (§5.2b).
6. **Tenant isolation has no isolated integration evidence.**

The backend ECS path is the one control working as designed: it refused this commit and the deploy
was skipped. **Nothing was weakened to change that.**

| Final posture | |
|---|---|
| Release | **DO NOT RELEASE · DEPLOYMENT BLOCKED** |
| Image provenance | **FAIL-OPEN** — `latest` advanced pre-gate, CI-confirmed |
| Frontend release path | **FAIL-OPEN** — grant absent |
| Backend release path | **FAIL-CLOSED and working** |
| Job-level gate evidence | **ABSENT in both verifier implementations** |
| `05 Security` | **FAILING** |
| PostgreSQL | **UNVERIFIED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |

No scanner finding suppressed, no threshold lowered, no test deleted, no skip or xfail converted to
a pass, no governed file modified, and nothing reported as passing that did not run.
