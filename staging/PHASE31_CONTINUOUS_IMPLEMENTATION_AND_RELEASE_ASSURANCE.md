# Phase 31 — Gate Identity Contract Proven, Second Structural Defect Found, Provenance and Evidence Lanes Reconciled

**Destination verified ABSENT before writing.** Three concurrent Phase 31 reports already exist
under other filenames; none was overwritten.

> ## DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED

**0 governed files changed.** Authorization is unchanged at zero. **2 new artifacts** under
`staging/`, both executed: a 23-case regression suite and its captured output, plus one read-only
CI evidence capture.

## Outcome

Phase 30 proved the release-gate prototype's mandatory-job identifiers are unmatchable. Phase 31
**proves the fix** and finds a **second defect of the same class in the same prototype** that
Phase 30 did not reach.

| # | Result | Class |
|---|---|---|
| **1** | **The corrected identity contract works.** An explicit job-key → display-name mapping, validated against the live YAML, admits an all-green run and refuses the real failing one. **23 tests pass, including 2 negative controls.** | **NEW — proves the fix** |
| **2** | **Two of the prototype's own settings are mutually unsatisfiable.** `REQUIRED_WORKFLOWS` requires `01 PR Check`; `ALLOWED_BRANCHES = ("main","master")` refuses every branch a `pull_request`-only workflow can report. `01 PR Check` can therefore **never** satisfy this gate, on any commit. Proven against real captured run records. | **NEW — second structural defect** |
| **3** | **`main` advances by fast-forward, not merge commits** — so PR evidence *can* bind to an on-main SHA. `6c5660a1` is the proof: an ancestor of `main` carrying both a `pull_request` and a `push` run at the same SHA. This makes §2.4's PR-only option viable **in principle** and isolates the branch allow-list as the actual blocker. | **NEW** |
| **4** | Workflow-invariant coverage for Priorities 2–3 **already exists** from a concurrent session — 5 invariants, 21 tests, 5 live violations. **Not duplicated.** | **reconciled** |
| **5** | The 9 B608 sites inspected in context: every interpolation is a **validated identifier**; every user-influenced value is **bound or absent**. | **NEW — confirms at source** |

---

## 1. Repository state, compared with Phase 30

| Invariant | Phase 30 | **Phase 31** |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **identical** |
| `origin/main` / divergence | identical / 0-0 | **identical / 0-0** |
| `HEAD^{tree}` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| staged / unmerged | 0 / 0 | **0 / 0** |
| tracked modifications | 2 | **2 — the same two** |
| stashes / worktrees | 2 / 1 | **2 / 1 — untouched** |

Concurrent work preserved byte-for-byte: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`
(`0c9981b2817e…`), `tests/test_drift_classification.py` (`99a2917ac0a6…`).

**New since Phase 30 — other sessions' artifacts, all read-only to me:** `PHASE29` ×2,
`PHASE30_RELEASE_GATE_PROVENANCE_AND_SECURITY_CLOSURE.md`, `PHASE31` ×2 (different filenames),
`PHASE27_FRONTEND_INCIDENT_AND_RELEASE_GATE_IMPLEMENTATION.md`, `PHASE28` ×2,
`phase31_workflow_invariants.py`, `phase31_test_workflow_invariants.py`,
`phase31_invariants.txt`, `phase31_invariant_tests.txt`, `phase31_gate_rerun.txt`,
`p28_phase18_tests.txt`.

### 1.1 Environment — measured this phase

| Tool / service | State |
|---|---|
| Python 3.12.10, pytest 9.1.1, PyYAML, `gh` 2.100.0 (authed), Bandit 1.9.4 | **FOUND** |
| **`safety`, `pip-audit`** | **ABSENT** |
| **`trivy`, `docker`, `terraform`** | **ABSENT** |
| **PostgreSQL** (5432, 51805, 51815) | **all closed** |
| **Redis** (6379) | **closed**; `REDIS_URL`, `AERORA_TEST_REDIS_URL`, `AERORA_TEST_DATABASE_URL` all unset |

---

## 2. Authorization by lane

Re-read live this phase. Ledger `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804`:
`AP-W1D-1` … `AP-W1D-5` **all `NOT GRANTED`**. `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md`:
**"Grants on record | 0"**. No authorization delta newer than Phase 30 alters any scope.

| Priority | Governed targets | Status | Done instead |
|---|---|---|---|
| **1** gate repair | `staging/phase18_release_gate.py`, `scripts/`, `tests/` | **NOT GRANTED** | **fix proven in a new staging artifact (§3)** |
| **2** security evidence | `.github/workflows/05-security.yml` | **NOT GRANTED** | already covered by a concurrent artifact (§5) |
| **3** image provenance | `02-build.yml`, `03-deploy.yml`, deploy scripts, `terraform/ecs.tf` | **NOT GRANTED** | **ECR documentary evidence (§6)** |
| **4** diagnostics | `scripts/require_security_scan.py` | **NOT GRANTED** | §3.4 adds a second instance of the same fault class |
| **5** Bandit parity | gate test + 3 migrations | **NOT GRANTED** | **9 sites inspected in context (§7)** |
| **6** D7-1 | `routers/market.py`, `routers/orders.py` | **NOT GRANTED** + open policy | reused, unchanged |
| **7** D7-2 | `portfolio_cache_updater.py` | **NOT GRANTED** + runtime blocker | reused, unchanged |
| **8** W-6 | `exchange_executor.py` | **NOT GRANTED** | reused, unchanged |
| **9** tenant / Redis | tenant context, migrations | **NOT GRANTED** + **BLOCKED** | §8 |
| **10** Terraform / frontend | Terraform roots, `06-frontend-deploy.yml` | **NOT GRANTED** + partly **BLOCKED** | §9 |

`AP-W1D-3`, the one live grant, is confined by its own text to four drift-classifier files. **No
Phase 31 target falls inside it.** Per §2.1 item 5 I did **not** modify
`staging/phase18_release_gate.py` — it is another session's artifact and its own path is
unauthorized. My suite loads it by `importlib` and leaves the blob unchanged
(`5c70dbbfb882036badade2ff01b4bf687595ff4e`).

---

## 3. Priority 1 — the gate identity contract

### 3.1 The fix shape, and why not the obvious one

Three options:

| Option | Verdict |
|---|---|
| (a) replace keys with display names | **Rejected as the whole answer.** Silently re-breaks when a `name:` is edited — exactly how the present defect arose |
| (b) match on job keys | **Impossible.** The `/jobs` API response carries no YAML key, only `name`. Nothing to match against |
| **(c) explicit key → expected-display-name mapping + a test resolving it against live YAML** | **Adopted.** A rename then fails a test naming both strings instead of disarming the gate |

(c) is what §2.1 item 2 asks for. Implemented as `REQUIRED_JOBS` in
`staging/phase31_test_gate_identity_contract.py`, preserving the mandatory/advisory split and the
prototype's documented reasons for `ADVISORY_JOB_KEYS`.

### 3.2 Executed: 23 passed

```
pytest staging/phase31_test_gate_identity_contract.py -q
→ 23 passed in 4.15s   EXIT 0
```

| Test group | Cases | What it establishes |
|---|---|---|
| mapping ↔ live YAML | 6 | every configured key exists; every expected display name equals the current `name:`; advisory jobs never leak into the mandatory set |
| Phase 30 regression pin | 1 | the prototype's config is **still** 9/9 unmatchable — fails loudly if someone fixes it, which is the correct signal |
| corrected behaviour | 11 | all-green **admitted** (the case Phase 30 saw refused); real `ecd23a9e` payload **refused**; `failure`/`skipped`/`cancelled`/`neutral`/`timed_out`/`stale` each refused; unfinished refused; missing refused; advisory cannot substitute |
| **negative controls** | 2 | a renamed display name **is** detected; neutering `_assert_mandatory_jobs_passed` **removes** the refusal — so the green cases are not vacuous |
| second defect (§3.3) | 3 | the branch contradiction, with its premise and its contrast |

**Real payloads, not synthetic.** The refusal case uses the captured
`/runs/38129699454/jobs` response for `ecd23a9e` — 3 jobs, `Trivy Container Vulnerability
Scanner` = `success`, `Trivy IaC Misconfiguration & Secret Scan` = `failure`, `Bandit SAST &
Dependency Security Scan` = `failure`. That is the §2.2 requirement that the previous suite's
self-referential fixtures could not meet.

### 3.3 Second structural defect — two settings that cannot both be met

`phase18_release_gate.py` sets `ALLOWED_BRANCHES = ("main", "master")`, and
`_assert_run_is_admissible` refuses any run whose `head_branch` falls outside it. It also lists
**`01 PR Check`** in `REQUIRED_WORKFLOWS`.

`01-pr-check.yml` is `on: [pull_request]` only (asserted, not assumed —
`test_pr_check_is_pull_request_only`). A `pull_request` run reports `head_branch` = **the PR's own
feature branch**, never `main`. Measured across all 6 most recent `01 PR Check` runs
(`total_count = 19`): `head_branch` ∈ {`ci/d5-real-redis-quota`, `ci/d5-real-redis-switc`,
`ci/d5-prime-real-redis`, `ci/unblock-deploy-gate`} — **never `main`**.

**So no `01 PR Check` run can ever satisfy this gate, on any commit, for a structural reason.**

Proven against real data rather than argued. The natural experiment is `6c5660a1`, which is
genuinely an ancestor of `main` **and** genuinely has a `01 PR Check` run. Its 6 runs:

| Workflow | Event | Conclusion | `head_branch` |
|---|---|---|---|
| `05 Security` | `pull_request` | success | `ci/unblock-deploy-gate` |
| **`01 PR Check`** | `pull_request` | **failure** | **`ci/unblock-deploy-gate`** |
| `06 Frontend Deploy` | `push` | success | `main` |
| `02 Build` | `push` | success | `main` |
| `05 Security` | `push` | success | `main` |
| `03 Deploy` | `workflow_run` | failure | `main` |

Feeding the real `01 PR Check` record to `_assert_run_is_admissible` raises `GateRefusal` **on the
branch check**, and the refusal message contains "branch" but **not** "failure" — asserted both
ways. The contrast isolates the cause: the `push`-triggered `05 Security` run **at the same SHA**
reports `main`, and is **admitted**. The branch allow-list is not wrong in general; it is
incompatible specifically with a `pull_request`-only required workflow.

### 3.4 The same fault class as the Phase 30 diagnostics finding

The operator sees *"on branch 'ci/unblock-deploy-gate', which is not in the allowed set"* when the
substantive problem is that the run **failed**. That is the third instance in this pipeline of a
gate refusing correctly while explaining incorrectly — alongside
`require_security_scan.py:123-124` asserting a CRITICAL container vulnerability when the container
scan is the job that passed. **Fail-closed, so not a security hole**, and I am not inflating it.

### 3.5 Preserved gate properties — re-audited, none changed

Exact-SHA matching and re-assertion · run-level **and** job-level conclusions · fail-closed on
missing / unfinished / skipped / cancelled / failed / neutral / malformed · pagination over runs
**and** jobs, refusing rather than truncating · HTTP/API refusal · empty-policy refusal · branch
allow-list (retained — §3.3 is a configuration conflict, not a reason to drop it) ·
`workflow_run` checkout-ref pinning documented · explicit advisory classification with reasons ·
no override path · non-vacuity controls. **Nothing was weakened.**

### 3.6 Correctness-evidence route — §2.4

**`main` advances by fast-forward or rebase, not merge commits.** `git log` shows a linear
single-parent chain: `ecd23a9e ← 074b772a ← 6c5660a1`. That is why `6c5660a1`'s SHA survives into
main's history with its PR evidence attached — and it means **PR evidence can bind to an on-main
SHA under this merge strategy**, which a squash-merge repository could not do.

So §2.4's PR-only option is viable **in principle**. Two things block it today:

1. **The branch allow-list contradiction (§3.3)** — a configuration fix, in the gate.
2. **`01 PR Check` fails wherever it runs.** All 6 recent runs: **failure**. `total_count = 19`.
   Of the five SHAs with PR-check runs, only `6c5660a1` is an ancestor of `main`; `f5b99e57`,
   `d07014d3`, `a624ce89`, `49e70da2` are **not** — those PRs (#6, #7, #8) were never merged.

**Owner decision, unchanged and still the top blocker:** adopt PR-only advancement (requires fixing
the gate's branch policy and triaging the PR-check failures), or add a push-triggered correctness
workflow. **I did not choose**, and added no override.

### 3.7 Test discovery

Default collection re-run: **14,693 tests collected in 52.40 s, exit 0** — consistent with Phase
30's 76.82 s. A grep of that collection for `phase31|phase30|phase17|phase18` returns **zero**, so
my suite is correctly *not* in the default lane while it lives in `staging/`. On a grant it moves
to `tests/test_release_gate_identity_contract.py`, which satisfies both `testpaths = tests` and
`python_files = test_*.py`.

---

## 4. Files written this phase

| File | Nature |
|---|---|
| `staging/phase31_test_gate_identity_contract.py` | 23-case regression suite (§3) — the §2.1/§2.2 deliverable |
| `staging/phase31_gate_identity_tests.txt` | its captured output |
| `staging/phase31_6c5660a1_runs.json` | read-only CI capture backing §3.3 |
| `staging/phase31_prcheck_runs.json` | read-only CI capture backing §3.3/§3.6 |
| `staging/PHASE31_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_ASSURANCE.md` | this report |

All new, all untracked, all under `staging/`. **No governed file touched → no governed diff.**

---

## 5. Priority 2 — security evidence, reconciled not duplicated

A concurrent session's `staging/phase31_workflow_invariants.py` already encodes five invariants and
runs **21 passing tests**, finding **5 live violations**. It parses YAML and reasons about step
position rather than grepping — the correct approach. I verified its recorded output and am **not
duplicating it**:

| Invariant | Violation found | Matches |
|---|---|---|
| INV-1 evidence-retention | `05-security.yml` `sast-scan / Upload Security Reports` — no `if: always()`, 1 earlier failing step | Phase 30 A-1 |
| INV-2 scan-not-swallowed | `Run Safety Check` — `\|\| echo` discards exit status | Phase 30 §2.3 |
| INV-3 no-tag-as-digest | `02-build.yml` — `DIGEST` falls back to `$IMAGE_TAG` | Phase 30 C-1 |
| INV-4 no-ungated-mutable-tag-push | `02-build.yml` pushes `:latest` with no gate dependency | Phase 30 C-5 |
| INV-5 digest-is-consumed | digest exported, **no workflow reads it** | Phase 30 C-3 |

**Safety threshold: still an owner decision, not taken.** Phase 30 established that `safety check`
carries no severity flag and no policy file exists, so removing `|| echo` would gate on every
severity — contradicting the preserved rationale at `require_security_scan.py:17-19`. Requirement
2.33 (`bugfix.md:541-544`) sets CRITICAL+HIGH for runtime dependencies but enforces it through a
checked-in triage record, not an exit code. **Unchanged this phase; I did not choose the
threshold.** The independent work (A-1 retention, A-3 pinning, A-4 second IaC root) needs only a
grant.

---

## 6. Priority 3 — provenance, with new documentary ECR evidence

§4.4 asked specifically not to infer ECR immutability from Terraform's silence. Searching the whole
tree rather than `*.tf`:

- `docs/history/AWS_DEPLOYMENT_GUIDE.md:24-28` — the repository's own provisioning procedure:
  `aws ecr create-repository --repository-name vyomquant-api` and four siblings
  (`vyomquant-tee`, `vyomquant-mds`, `vyomquant-workers`, `vyomquant-frontend`). **No
  `--image-tag-mutability` flag on any of the five.** ECR's default is `MUTABLE`.
- `scripts/discover_all_components.py:240` references an `aws_ecr_repository` component for
  `vyomquant-api`.
- No `aws_ecr_repository` resource and no `image_tag_mutability` setting exists in any `.tf`.

**Finding, stated at its true strength:** the documented procedure creates these repositories
**without** requesting tag immutability, so the tree's own evidence points to mutable tags. This is
**documentary, not live-state** evidence — the actual registry setting still requires AWS access
and remains **unresolved infrastructure evidence**, exactly as §4.4 requires. No ECR query or
mutation was performed.

Everything else in the provenance chain is unchanged at current blobs and reused: tag-as-digest
fallback at `02-build.yml:78`; digest resolved at `03-deploy.yml:168` and discarded (step-local
variable, never exported); `latest` defaults at `ecs_deploy_and_diagnose.py:94` and
`scripts/deploy.sh:9`; 6 Terraform image refs → **8** task definitions via `for_each` ×3 workers;
3 worker services outside the CI deploy path. `03-deploy.yml:162-174` remains **correctly
fail-closed** and must be preserved.

---

## 7. Priority 5 — the nine B608 sites, inspected in context

Inspected rather than assumed, per §6 item 1. The pattern at every site examined:

```python
table  = _validate_table_name(raw_table)      # :189
column = _validate_table_name(raw_column)     # :190
...
sa.text(f'SELECT count(*) FROM public."{table}" WHERE "{column}" IS NOT NULL '
        f'AND "{column}"::text !~* :re'), {"re": UUID_REGEX}
```

| Property | Evidence |
|---|---|
| Only **validated identifiers** are interpolated | `_validate_table_name` at `:145` / `:95` / `:138` in the three migrations |
| The validator **raises**, never sanitises | `raise ValueError(f"not a SQL identifier: {s!r}")` at `:157` / `:108` / `:151` |
| Every user-influenced **value** is bound | `{"re": UUID_REGEX}`; `UUID_REGEX` is a module literal at `:119` / `:199` |
| The orphan-check sites (`:467`, `:475`) | interpolate **identifiers only** — no value interpolation at all |

So the nine findings are **B608-correct and exploitability-negative**: Bandit fires on
string-building into SQL as such, and these build with identifiers that a raising validator has
already constrained. They are **not** equivalent to an exploitable injection, and §6's caution
against treating them as such is warranted.

This matches the convention ~49 already-suppressed sites follow (Phase 30's census: 62 `nosec`
across 18 files). **Two separate things remain true and must not be conflated:** the nine sites
are safe *and* they are legitimately red in CI, because they carry no annotation. The two inert
annotations in `backend_app/test_perf.py` suppress nothing relevant to them.

Bandit was **not re-run** — inputs byte-identical since the executed measurement (whole tree exit
1 / 9 × B608 MEDIUM; gated module exit 0; gate suite 3 passed; isolated negative control exit 1
with 1 finding detected). Basis for reuse: unchanged blobs and unchanged tool version 1.9.4.

---

## 8. Priorities 6–9 — reused, with blockers restated

| Lane | Status | Basis |
|---|---|---|
| **D7-1** | 3 invalid call sites (`market.py:50`, `orders.py:789`, `orders.py:1095`) against a 2-parameter signature at `api_key_vault.py:177`; `exchange.py:993` valid; 4 further callers valid positional. `market.py`'s `isinstance` guard at `:55-56` is unreachable because the `TypeError` fires on the call | re-verified Phase 30, blobs unchanged |
| **D7-2** | `portfolio_cache_updater.py:151` imports a module that does not exist; `:160` already has correct arity; false-green `ACTIVE` at `main.py:466`. **Runtime blocker: deployed vault object type unknown** — needs redacted platform boot logs | blobs unchanged |
| **W-6** | 3 of 8 matrix cases misclassified; `.retryable` = 2 writes + 1 log read, **zero control-flow reads**, so a classification fix cannot alter retry behaviour and none is introduced | blobs unchanged |
| **Tenant / RLS** | `FORCE ROW LEVEL SECURITY` = **0 executable statements**; policies inert on the owner-role ORM path; `tenant_db_context.py` unwired. **FORCE RLS not enabled, not prepared, not proposed** | **PostgreSQL closed — runtime BLOCKED / NOT VERIFIED** |
| **Redis / workers** | Quota, debounce and pool-lifecycle suites **not executed**; **no skip counted as a pass**; no atomicity claimed from in-process mocks. `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have 0 references in `main.py` | **Redis closed — BLOCKED** |

---

## 9. Priority 10 — Terraform and frontend

**Terraform. BLOCKED** — `terraform` and `trivy` both absent, so no `fmt`, `validate`, `plan` or
IaC scan was run and none is claimed. Phase 27's correction is **preserved and not revived**: the
alleged Redis security-group collision is **withdrawn** (separate VPCs; `redis-only` uses local
state). No state migration, backend change, provisioning or `apply`.

**Frontend.** `06-frontend-deploy.yml` unchanged at `285cc2d19b2b`: 1 job, 14 steps, `needs = None`,
`if = None`, **0** references to the security workflow or gate script, triggers `push: [main,
master]` + `workflow_dispatch`. The newest `06 Frontend Deploy` run remains **38129699497** at
`ecd23a9e`, concluded 09:50:13Z (`total_count = 60`, re-queried Phase 30); **nothing superseded
it**. No S3 or CloudFront inspection, rollback, republication or containment claim.

---

## 10. Commands and actual results

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status` / `stash list` / `worktree list` / `hash-object` | 0 | §1 — entry = exit |
| ledger + decision-packet grant recheck | 0 | **5 × NOT GRANTED; 0 grants** |
| **`pytest staging/phase31_test_gate_identity_contract.py -q`** | **0** | **23 passed in 4.15 s** |
| `pytest --collect-only -q` | **0** | **14,693 tests in 52.40 s** |
| collection grep for `phase17\|phase18\|phase30\|phase31` | 0 | **0 matches** |
| `git log -8 --format='%h parents=%p'` | 0 | **linear, single-parent — fast-forward history** |
| `git merge-base --is-ancestor` ×5 | — | `6c5660a1` **ancestor**; `f5b99e57`/`d07014d3`/`a624ce89`/`49e70da2` **not** |
| `gh api .../01-pr-check.yml/runs` | 0 | 19 runs; 6 newest all `failure`, all feature branches |
| `gh api .../runs?head_sha=6c5660a1…` | 0 | **6 runs — the §3.3 natural experiment** |
| workflow YAML parse ×7 | 0 | job keys, display names, triggers |
| B608 site + validator reads | 0 | §7 |
| ECR immutability tree-wide search | 0 | §6 |
| port probes 5432 / 6379 / 51805 / 51815 | 0 | **all closed** |
| **`git diff --check`** | **0** | **no whitespace errors** |
| secret scan of this phase's artifacts | 0 | **clean** |

**Reused, not re-run** (basis: identical blobs, identical tool versions): Bandit whole-tree and
single-module scans, the Bandit negative control, phase17's 42 cases, phase18's 28 cases, the
concurrent session's 21 invariant tests, the W-6 matrix.

**BLOCKED / NOT RUN — none counted as a pass:** Safety and pip-audit (absent), Trivy IaC,
container scan, `terraform fmt`/`validate`/`plan`, tenant-isolation runtime matrix, real-Redis
suites, W-6 sandbox tests, `01 PR Check` at `ecd23a9e` (undispatchable), published-bundle contents.

**Evidence classes.** §3.2 and §3.3 are **local tests against real captured API payloads and live
YAML** — they prove the identity contract and the configuration conflict, **not** release safety.
§3.6, §9 are **read-only CI metadata**. §6, §7 are **source inspection**, and §6 is explicitly
**documentary, not live infrastructure state**. No live integration evidence was produced.
**Release certification: NONE.**

---

## 11. Residual risks and the next owner decision

**Next highest-priority owner decision, unchanged: the correctness-evidence route (§3.6).** It
gates the frontend gate, the backend correctness requirement and the gate's own branch policy. It
now has a measured complication: the PR-only option is structurally viable under this repo's
fast-forward history, but `01 PR Check` currently **fails on every run**, so adopting it without
triaging those failures would block all releases.

| Risk | State |
|---|---|
| The gate everyone queued for production would refuse every release — **two** independent reasons (job identifiers; branch policy × PR-only workflow) | **both now proven, neither fixed in the prototype** |
| Frontend publication path has no gate and has already published `ecd23a9e` | unchanged; bundle contents **Unknown** |
| Dependency scan cannot fail even when it runs; evidence discarded on failure | unchanged; threshold is an **owner decision** |
| Image identity is tag-based end to end; digest exported and unread; documented ECR provisioning omits immutability | unchanged |
| Three refusal messages that name the wrong cause | **third instance found (§3.4)** |
| Tenant isolation, PostgreSQL, Redis | **BLOCKED / NOT VERIFIED** |

### Ranked requests

> **① Fix the gate's identity contract and branch policy together.** *Both defects are proven and
> a 23-case suite with 2 negative controls is written.* *"I authorize correcting the release gate's
> mandatory-job configuration to an explicit key → display-name mapping, resolving the
> `ALLOWED_BRANCHES` conflict with the `pull_request`-only required workflow, and installing
> `tests/test_release_gate_identity_contract.py`."*
>
> **② Correctness-evidence route** — the decision in §3.6, plus triage of the `01 PR Check`
> failures.
>
> **③ A-1 / A-3 / A-4 — security evidence retention, scanner pinning, second IaC root.** No policy
> decision required; the concurrent invariant suite supplies the regression tests.
>
> **④ Safety severity threshold** — owner policy (§5).
>
> **⑤ C-1 build digest fail-open**, then **⑥ D7-1**, then **⑦ Bandit annotations + widened gate**.

---

## 12. Final integrity review

| Check | Result |
|---|---|
| every changed path authorized | **yes — 0 governed paths changed; all writes under `staging/`** |
| `git diff --check` | **exit 0**, no whitespace errors |
| new regression tests run | **23 passed** |
| default collection | **14,693 collected, exit 0** |
| branch / `HEAD` / `origin/main` / divergence | `main` / `ecd23a9e…` / identical / **0-0** |
| `HEAD^{tree}` | `4b45a763…` — **unchanged** |
| staged / unmerged / stashes / worktrees | **0 / 0 / 2 / 1** |
| concurrent modifications, stashes, worktrees, reports, prototypes | **all intact; nothing overwritten** |
| `staging/phase18_release_gate.py` | **`5c70dbbf…` unchanged** — loaded by `importlib`, never written |
| secrets in diffs or report | **none** — scanned clean |
| prohibited Git op / CI dispatch / deploy / cloud mutation / DB change / live trading | **none** |

No test was removed or weakened to obtain a green result. No scanner threshold lowered, no finding
suppressed, no skipped or blocked check reported as a pass.

---

## 13. Release posture

| Item | Status |
|---|---|
| **Frontend publication** | **BLOCKED** — no gate; `ecd23a9e` assets live since 09:50:13Z, nothing superseded |
| **Already-live incident** | **OWNER CONTAINMENT DECISION REQUIRED** — bundle contents **Unknown** |
| **Backend deployment** | **BLOCKED** — `05 Security` red; gate correctly refused; no correctness evidence required |
| **Release gate** | **NOT INSTALLED — and the queued prototype has two proven reasons it would refuse every release** |
| **Dependency evidence** | Safety **non-blocking by construction**; blocking container scan of runtime deps **passed** at the SHA |
| **Image provenance** | **NOT ESTABLISHED** — tag-based end to end; digest exported and unread; documented ECR provisioning omits immutability |
| **Tenant isolation** | **NOT CERTIFIED** |
| **PostgreSQL / Redis** | **UNVERIFIED — services closed** |
| **Live trading** | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

---

*Phase 31 closed. Tree unchanged at entry and exit. 0 of 10 priorities authorized, so 0 governed
files changed. Phase 30 proved the gate defect; Phase 31 proves the fix with 23 tests on real
payloads and finds a second, independent reason the same prototype would refuse every release.*

***A fail-closed gate that refuses everything is not a working gate. A refusal that names the
wrong cause is not a usable diagnostic. A skipped scan is not a pass, and absent tools are not
passed scans.***
