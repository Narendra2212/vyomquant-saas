# Phase 24 — Release-Gate Recovery, Verifier Integration, Image Provenance, Critical Closure

**Destination verified ABSENT before writing. Phase 23 and every other report preserved.**

> ## IMPLEMENTATION WAS BLOCKED — stated plainly, per §11.12
>
> **The ledger contains zero granted implementation lanes.** Verified this phase:
> `**GRANTED**` occurrences in the ledger = **0**; rows `:800-804` all `NOT GRANTED`;
> `A23E11` still reads *"no authorization arrived"*.
>
> **0 governed files changed.** No lane below is described as implemented, and no test of an
> unchanged prototype is presented as a fix. What this phase delivers is **six source-verified
> findings, five of them new**, plus one correction to my own Phase 19 recommendation.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED.**

No stage, commit, push, merge, rebase, reset, clean, revert, history rewrite, stash mutation, CI
dispatch, PR, settings change, deployment, rollback, image promotion or retag, `terraform apply`,
provisioning, production database access, migration execution, or live exchange call. No secret,
DSN, token, signed payload or credential was read, echoed or reproduced.

---

## 1. Entry / exit integrity manifest

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | **unchanged** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |

**Concurrent modifications preserved byte-for-byte, neither staged nor reverted:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (` M`), `tests/test_drift_classification.py` (` M`).

**`git write-tree` deliberately not computed** — it writes objects into a shared object database that
concurrent sessions are actively using. The tracked tree is instead proven unchanged by
`staged = 0`, `unmerged = 0`, `tracked = 5705`, an unchanged reflog head, and the two pre-existing
` M` paths being the *only* tracked modifications, identical to entry.

**Environment:** Python 3.12.10 · pytest · bandit 1.9.4 · PyYAML 6.0.3 · GitHub CLI authenticated
(read-only use) · **terraform ABSENT** · **trivy ABSENT** · **no Docker** · no PostgreSQL · no Redis
· no AWS credentials · no exchange credentials.

---

## 2. Authorization decision for every attempted implementation

| Lane | Paths it would touch | Grant |
|---|---|---|
| A verifier integration | `staging/phase17_*` → `scripts/` + `tests/`, `pytest.ini` | **NOT GRANTED** |
| B frontend publication gate | `06-frontend-deploy.yml` + test | **NOT GRANTED** |
| C correctness route | `01-pr-check.yml` **or** branch protection | **NOT GRANTED** (and option 1 is a settings action, not code) |
| D image provenance | `02-build.yml`, `ecs_deploy_and_diagnose.py`, `terraform/ecs.tf`, `ecs-task-definition-full.json` | **NOT GRANTED** |
| E security evidence | `05-security.yml` | **NOT GRANTED** |
| F Bandit parity | gate test + 3 migrations | **NOT GRANTED** |
| G D7-1 / D7-2 / W-6 | `market.py`, `orders.py`, `portfolio_cache_updater.py`, `exchange_executor.py` | **NOT GRANTED** |
| H Terraform / tenant / Redis / workers | both roots, tenant context | **NOT GRANTED** + environment-blocked |

Nothing was inferred from a decision ID recording a *request*, a prior recommendation, a report, the
prototype's existence, role titles, commit authorship, or silence.

---

## 3. NEW — Lane E: an entire IaC root has never been scanned

All four claims the brief lists are **confirmed at source**, and claim 2 is more serious than
described. `05-security.yml` is 161 lines, 3 jobs, `clean`.

### 3.1 `aerora_quant_platform/infrastructure/` is permanently shadowed

Verbatim from the committed workflow, lines 114–160 — **the only `if:` in the whole job is at `:158`**:

```
120  - name: Trivy IaC scan — terraform/ (blocking, CRITICAL)
124      scan-ref: 'terraform'
127      exit-code: '1'
131  - name: Trivy IaC scan — aerora_quant_platform/infrastructure/ (blocking, CRITICAL)
135      scan-ref: 'aerora_quant_platform/infrastructure'
138      exit-code: '1'
144  - name: Trivy IaC report — all severities to MEDIUM (non-blocking)
148      scan-ref: 'terraform'
151      output: 'reports/trivy-iac-report.json'
152      exit-code: '0'
156  - name: Upload IaC Scan Report
158    if: always()
```

Step `:131` carries **no `if:`**, so under GitHub Actions' default step semantics it is **skipped
once `:120` fails** — and `:120` *does* fail (5 CRITICAL, CI run `38129699454`).

`VER` The target exists and is substantial: **`aerora_quant_platform/infrastructure/` contains 37
scannable files**, including `docker-compose.production.yml`,
`docker-compose.redis-architecture.yml`, `docker-compose.backend-scaling.yml`,
`docker-compose.websocket.yml`, `docker-compose.workers.yml`, `docker-compose.staging.yml` and
`Dockerfile`.

> **So a second infrastructure root — including production compose definitions — has never received
> any misconfiguration or secret scan.** Its blocking gate is unreachable, and because the job
> already reports failure from the *first* root, the second root's non-execution is invisible.
> Structurally identical to the Bandit scope gap: a check that exists but cannot run.

**The non-blocking report at `:148` also only covers `terraform`**, so even the advisory evidence
has zero coverage of root 2.

### 3.2 SAST evidence is destroyed by the failure it should document

```
35  - name: Run Bandit Scan
36    run: bandit -r backend_app -ll -f json -o reports/bandit-report.json
42    uses: actions/upload-artifact@v4          ← no `if: always()`
```

Bandit exits 1 (9 × B608), so the upload at `:42` is **skipped** and `bandit-report.json` is never
collected. The one run where the report matters most is the one where it is discarded.

### 3.3 Safety is fail-open and mislabelled

```
38  - name: Run Safety Check
39    run: safety check --json --output reports/safety-report.json || echo "Safety advisory logged"
```

`|| echo` forces exit 0, so a dependency vulnerability **can never fail the job** — while the step
sits in a job named *"Bandit SAST & Dependency Security Scan"* and is named *"Run Safety Check"*,
both of which read as mandatory. It is advisory in behaviour and mandatory in presentation.
**Whether Safety is mandatory is an owner decision (§8 item 4), not something to infer.**

### 3.4 The IaC artifact is uploaded without its report

`:156-158` uploads `iac-scan-report` under **`if: always()`**, but its generating step `:144` is
itself skipped when `:120` fails. The upload therefore runs for a report that was never produced —
yielding an empty or missing artifact that *appears* to exist.

**Net Lane E state:** of the IaC coverage the workflow advertises, **one root is scanned, one root
is never scanned, the SAST report is discarded on failure, the dependency check cannot fail, and
the artifact is uploaded empty.** `continue-on-error` is used nowhere — the defects are ordering and
missing `if: always()`, not forced success. **Grant absent; no edit made.**

---

## 4. NEW — Lane D: the correct digest guard already exists in-repo, one line apart

The brief asks me to preserve the distinction between the build's fallback and the deploy's refusal.
I located both and the contrast is exact: **the same AWS query, opposite failure semantics.**

**`03-deploy.yml:163-174` — FAIL-CLOSED and correct. Do not weaken this:**

```
165    IMAGE_TAG: ${{ github.event.workflow_run.head_sha || github.sha }}
168    DIGEST=$(aws ecr describe-images … --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "")
169    if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
170      echo "CRITICAL: No ECR image artifact found for commit SHA: $IMAGE_TAG"
171      echo "Deployment FAIL-CLOSED: Refusing to deploy untested or missing build artifact."
172      exit 1
173    fi
```

**`02-build.yml:78-82` — FAIL-OPEN, identical command:**

```
78    DIGEST=$(aws ecr describe-images … --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "$IMAGE_TAG")
81    echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
```

| File | On query failure | Result |
|---|---|---|
| `03-deploy.yml:168` | `\|\| echo ""` → explicit `exit 1` | **refuses** |
| `02-build.yml:78` | `\|\| echo "$IMAGE_TAG"` | **emits a mutable tag as `image_digest`** |

The entire difference is `""` versus `"$IMAGE_TAG"`. Both discard the AWS error with `2>/dev/null`.

**Two consequences, stated with their limits:**

1. **The remediation for Lane D item 3 is already demonstrated in this repository** — adopt
   `03-deploy.yml`'s pattern in the build. No new design is required.
2. **Severity is bounded, honestly:** `03-deploy.yml` **re-queries** ECR rather than consuming
   `02-build.yml`'s `image_digest` output, so the corrupted value does not reach the backend
   deployment path. The exposure is to any other consumer of that step output, and to anyone reading
   the `=== Pushed Image Digest: ===` log line as provenance.

`scripts/ecs_deploy_and_diagnose.py` has **0 digest references** — so the empty-digest refusal is
**not** in that script; it is in `03-deploy.yml`. Its own defect is unchanged and separate:
`:94 IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")`, consumed at `:901`.

`terraform/ecs.tf` still pins `:latest` in **all six** API/worker image references (`:28`, `:126`,
`:198`, `:284`, `:378`, `:478`). The third-party QuestDB image decision is kept **separate** and
untouched. **Live ECR and running-ECS state remain UNVERIFIED — no AWS credentials, and none sought.**

---

## 5. NEW — correction to my own Phase 19 D7-2 recommendation

In Phase 19 I wrote that the D7-2 remedy was to consume the established singleton,
`dependencies.py:659 get_vault() → app_state.vault`. **The brief's caution is correct and my
recommendation was unsafe as stated.** Verified verbatim in `backend_app/core/state.py`:

```
45  class MockEngine:
46      """Placeholder engine that does nothing but won't crash"""
51      def __getattr__(self, name):
52          """Return a no-op function for any method call"""
53          def noop(*args, **kwargs):
54              return None
55          return noop
57  def safe_import(class_name, module_name):
58      """Try to import an engine, return MockEngine if it fails"""
67  APIKeyVault = safe_import("APIKeyVault", "api_key_vault")
95  vault: Any = field(default_factory=lambda: _safe_init(APIKeyVault, "APIKeyVault"))
```

`MockEngine.__getattr__` answers **any** attribute with a callable returning `None`. So
`app_state.vault` may legitimately be a `MockEngine`, and `get_vault()` would hand that back
indistinguishably.

> **Had `portfolio_cache_updater.py` simply swapped its broken
> `from backend_app.backend.key_manager import KeyVault` for `get_vault()`, a deployment whose vault
> import or construction had fallen back would turn a *caught and logged* `ImportError` into a
> *silent* `None` that is indistinguishable from "this user has no keys."** That is strictly worse
> for observability than the current defect.

**Therefore D6-D must be resolved before D7-2 is implemented** — the deployment must first be shown
to hold a real `APIKeyVault` rather than a `MockEngine`. The minimum evidence is the redacted boot
signal: `type(app_state.vault).__name__` plus whether `Failed to import APIKeyVault` or
`APIKeyVault initialization failed, using mock` was emitted. **No secret is required to answer that.**
`order_watchdog.py`'s separate vault-method discrepancy stays out of scope.

---

## 6. Lane status matrix

Evidence marked **[reused]** is unchanged in source, tool version, commit and environment.

| Lane | Status | Evidence |
|---|---|---|
| **A** verifier integration | **Blocked by missing grant.** Prototype **42 passed / 4.57s**; collection defect persists — `phase17_test_frontend_release_gate.py` matches neither `test_*.py` nor `*_test.py`, so its tests are collected by nothing and referenced by no workflow. **Job-level verification absent in BOTH implementations** (`/jobs` refs = 0 each) | **[reused Phase 23]** |
| **B** frontend gate | **Blocked by missing grant.** Still no gate, no `needs:`, no `permissions:` block. Structural assertions validated **7/7** with negative controls | **[reused Phase 19/23]** |
| **C** correctness route | **Requires owner decision.** `01-pr-check.yml` remains `pull_request`-only; `frontend-tests` additionally change-detection-gated, so a workflow-level green does not prove the job ran | **[reused]** |
| **D** image provenance | **Blocked by missing grant. NEW §4** — the correct guard exists in `03-deploy.yml`; the build's fallback is one token different; blast radius bounded because the deploy re-queries | **NEW** |
| **E** security evidence | **Blocked by missing grant. NEW §3** — second IaC root never scanned; SAST report discarded; Safety fail-open; artifact uploaded without its report | **NEW** |
| **F** Bandit parity | **Blocked by missing grant.** Single gated module **exit 0 / 0 findings**; full tree **exit 1 / 9 × B608 MEDIUM**, bandit 1.9.4. Parity design + both negative controls validated **4/4** | **[reused]** |
| **G** D7-1 | **Blocked by missing grant.** 3 invalid sites (`market.py:53`, `orders.py:792`, `orders.py:1098`); **9 legitimate** `access_token` callers must not be touched | **[reused]** |
| **G** D7-2 / D6-D | **Blocked by missing grant AND by a prerequisite — NEW §5.** D6-D must precede D7-2 | **NEW** |
| **G** W-6 | **Blocked by missing grant.** `.retryable`: 3 occurrences — 2 assignments, 1 log f-string at `:1687`; **zero control-flow reads**. No retry, backoff or new control flow introduced | **[reused]** |
| **H** Terraform / Trivy | **Blocked by missing grant and by environment** — `terraform` and `trivy` both ABSENT, so **no scan was run and none is claimed**. SG ownership boundary unresolved; 5 CRITICAL dispositioned in Phase 22 | **[Phase 22]** |
| **H** tenant isolation | **Source-verified only. NOT CERTIFIED.** `FORCE ROW LEVEL SECURITY` **0 matches** in executable SQL; FORCE RLS **not** enabled; tenant context unwired beyond one inline `SET LOCAL` | **[reused]** |
| **H** Redis / workers | **Tests passed / skipped, honestly separated.** Quota **60 passed / 5 skipped**; debounce **9 passed / 1 xfailed** — **not** an all-pass suite; real-Redis **UNEXECUTED**. W-4 undecided ⇒ **W-5 untouched**, no `UNKNOWN` semantics inferred. Snapshot and drift baseline preserved | **[reused]** |

---

## 7. Commands, versions, exit codes, timeouts

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status` / `reflog` / `stash list` / `ls-files` / `worktree list` | 0 | §1 |
| Ledger + A23E11 grant scan | 0 | **`**GRANTED**` = 0**; 5 rows `NOT GRANTED` |
| `git show HEAD:.github/workflows/05-security.yml` + line-exact extraction | 0 | §3 — 161 lines, 3 jobs, single `if:` at `:158` |
| `Get-ChildItem aerora_quant_platform/infrastructure -Recurse` | 0 | **37 scannable files** |
| `git show HEAD:.github/workflows/02-build.yml` 77-82 | 0 | §4 fail-open fallback |
| `git show HEAD:.github/workflows/03-deploy.yml` digest block | 0 | §4 fail-closed guard at `:168-172` |
| `Select-String scripts/ecs_deploy_and_diagnose.py` digest | 0 | **0 digest refs** |
| `Get-Content backend_app/core/state.py` 45-60 | 0 | §5 MockEngine noop semantics |
| Report/prototype enumeration | 0 | Phase 16–23 present; Phase 24 destination absent |

**Reused, not re-executed** (source, commit, tool version and environment unchanged): prototype
42 passed; full-tree bandit exit 1 / 9 findings; frontend-gate negative control 7/7; Bandit parity
negative control 4/4; quota 60/5; debounce 9/1; drift 140 passed.

**Not executed:** CI dispatch, PR, deployment, rollback, image build/push/promote, `terraform`
validate/plan/apply, any Trivy scan, PostgreSQL or Redis connection, tenant lane, real-Redis quota
lane, live exchange call, AWS query.

**Timeout recorded from Phase 23 and deliberately not repeated:** an unbounded
`pytest --collect-only -q` over the ~11,000-test tree exceeded 120 s. Collection questions were
answered deterministically from `pytest.ini`'s `python_files` patterns instead.

---

## 8. Owner decisions — copy-ready

> **1 · Security-evidence preservation (Lane E — new, highest value).**
> *"I authorize editing `.github/workflows/05-security.yml` to: add `if: always()` to the SAST
> report upload; make the `aerora_quant_platform/infrastructure/` IaC scan run independently of the
> `terraform/` scan outcome so both roots are always evaluated; make the non-blocking IaC report and
> its upload conditional on the report existing; and add workflow-structure tests with negative
> controls. Mandatory failure semantics must not change and `continue-on-error` must not be added."*
>
> **2 · Safety disposition (Lane E).** *"`safety check` is ⟨**mandatory** — remove the `|| echo` and
> let it fail the job⟩ / ⟨**advisory** — rename the step and record it as advisory in the release
> contract⟩."*
>
> **3 · Collect the verifier that already works (Lane A).** *"I authorize moving
> `staging/phase17_frontend_release_gate.py` to `scripts/` and its tests to
> `tests/test_frontend_release_gate.py` so the 42 cases are collected, preserving decision
> semantics."*
>
> **4 · Job-level verification (Lane A).** *"I authorize extending the release verifier to inspect
> each mandatory workflow run's `/jobs` and require every explicitly-listed mandatory job to
> conclude `success`, rejecting missing, skipped, cancelled, failed or malformed job evidence, with
> pagination and fail-closed API handling, plus a negative control per property."*
>
> **5 · Frontend gate (Lane B).** As previously scoped: gate job + `needs:` + `DEPLOY_SHA: ${{ github.sha }}`
> + no override + explicit `permissions: actions: read` + structural tests.
>
> **6 · Image provenance (Lane D).** *"I authorize replacing `02-build.yml:78`'s
> `|| echo "$IMAGE_TAG"` with the fail-closed pattern already used at `03-deploy.yml:168-172`;
> making a missing/empty `IMAGE_TAG` a hard error at `ecs_deploy_and_diagnose.py:94`; gating or
> removing the `:latest` push; and replacing the six `terraform/ecs.tf` API image pins plus the API
> image in `ecs-task-definition-full.json` under an approved immutable-artifact contract. The
> QuestDB image policy is excluded."*
>
> **7 · Correctness route (Lane C).** PR-only advancement of `main` via branch protection
> **(recommended)**, or a push-candidate correctness workflow. Either must treat a skipped mandatory
> job as not-success.
>
> **8 · D6-D before D7-2 (Lane G — new prerequisite).** *"Redacted per-environment boot evidence:
> `type(app_state.vault).__name__`, plus whether `Failed to import APIKeyVault` or
> `APIKeyVault initialization failed, using mock` was logged."* No secret required.
>
> **9 · Bandit (F), D7-1 (G), W-6 (G), Terraform ownership (H), tenant isolation (H), A23, W-4.**
> As previously scoped; D7-1 must name **only** the three `load_decrypted_keys` callers.

---

## 9. Release recommendation

**DO NOT RELEASE.** Based on evidence measured or re-verified this phase:

1. **An entire IaC root has never been scanned** — 37 files including production compose
   definitions, behind a permanently shadowed blocking step (§3.1).
2. **Security evidence is destroyed exactly when it matters** — the SAST report upload is skipped by
   the Bandit failure it would document (§3.2).
3. **The dependency check cannot fail** — `|| echo` on `safety check`, while presented as mandatory
   (§3.3).
4. **Image provenance is fail-open** in the build, with the correct guard sitting one token away in
   the deploy workflow (§4); `latest` was already advanced to this commit pre-gate.
5. **`05 Security` is red** — 9 Bandit B608 + 5 Trivy CRITICAL, **0 fixed**.
6. **No correctness evidence exists for the candidate SHA**, and the frontend path has no gate.
7. **Tenant isolation has no isolated non-owner integration evidence.**
8. **A working verifier remains inert** — 42 green tests collected by nothing, and job-level
   verification is missing from both implementations.

The backend ECS path remains the one control working as designed — SHA-pinned, digest-refusing, and
it declined this commit. **Nothing was weakened to change that.**

| Final posture | |
|---|---|
| Release | **DO NOT RELEASE · DEPLOYMENT BLOCKED** |
| IaC scan coverage | **1 of 2 roots; second root never scanned** |
| Security evidence collection | **lost on failure** |
| Image provenance | **FAIL-OPEN in build; FAIL-CLOSED in deploy** |
| Frontend release path | **FAIL-OPEN** |
| Backend release path | **FAIL-CLOSED and working** |
| Job-level gate evidence | **ABSENT in both verifiers** |
| PostgreSQL | **UNVERIFIED** |
| Live ECR / ECS state | **UNVERIFIED — no cloud access** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |

No gate weakened, no finding suppressed, no threshold lowered, no test deleted, no skip or xfail
converted to a pass, no governed file modified, and nothing reported as passing that did not run.
