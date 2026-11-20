# Phase 18 — Authorization-Aware Release Gate Closure and Parallel Critical-Path Execution

**Destination verified ABSENT before writing.** A concurrent session's
`staging/PHASE18_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` is a **different file and was not touched**.
**0 production files · 0 files under `tests/` · 0 workflows · 0 Terraform · 0 migrations ·
0 governance records · 0 approvals created · 0 CI dispatched · 0 deployments.**

> ## OUTCOME
>
> **No grant exists for any lane.** Re-measured, and corroborated by three concurrent sessions —
> PHASE19 records *"`GRANTED` occurrences anywhere in the ledger: **0**"*; PHASE20: *"0 files
> modified. No implementation grant exists for any of lanes A–I."*
>
> **TWO NEW FINDINGS, neither reported by any concurrent session.**
>
> **(1) Lane D — the release gate is bypassable by artifact, not by workflow.** `02-build.yml:76`
> pushes the **mutable `latest`** ECR tag *before any gate*, and it did so for the security-red
> `ecd23a9e`. `terraform/ecs.tf` and **both** deploy scripts default to `latest`. The gated CI path
> is SHA-pinned and correctly refused — but the artifact it refused is sitting on the tag the
> infrastructure references.
>
> **(2) Lanes A/C — my own Phase 17 gate is unsafe.** It checks workflow-**run** conclusions only.
> A run can conclude `success` with a mandatory **job** skipped, and this repository's workflows are
> shaped exactly that way. Demonstrated by executing the Phase 17 module, then fixed in a Phase 18
> proposal: **28 tests passed**, including two mutation checks proving non-vacuity.
>
> **DO NOT RELEASE. LIVE TRADING OFF / NOT CERTIFIED. TENANT ISOLATION NOT CERTIFIED.**

---

## 1. Entry / exit manifest

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` · ahead/behind | `ecd23a9e…` · `0 0` | unchanged |
| Latest reflog | `ecd23a9e commit: launch phase 1` (15:13:47) | unchanged |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked (`-uall`) | 99 | 106 (+7, all `staging/phase18_*` + this report) |
| Worktrees | 1 | 1 |
| **Stash entries** | **2** | **2 — both preserved, neither inspected nor applied** |

Pre-existing stashes, left untouched: `stash@{0}: filter-branch: rewrite`,
`stash@{1}: WIP on main: c673409 Release v0.1.0…`.
Other workstreams' two unstaged files untouched: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`,
`tests/test_drift_classification.py`.

### 1.1 Target-file hashes — every one unchanged, entry and exit

`06-frontend-deploy.yml` `285cc2d1…` · `01-pr-check.yml` `91d55a37…` · `02-build.yml` `42c7696e…` ·
`03-deploy.yml` `eb8078ea…` · `05-security.yml` `e2d9ccc6…` · `require_security_scan.py`
`a1c8dd2d…` · `test_bandit_sast_gate_is_green.py` `a7deff99…` · migrations `bb9b6298…` /
`ac12c2d9…` / `5e9f85ce…` · `redis_manager.py` `d7b0e811…` · `portfolio_cache_updater.py`
`a74d7644…` · Phase 17 artifacts `4c38728f…` / `13b5e6cd…`.

`terraform/ecs.tf` = **`7d05ee95c77731dce1f88f4be1a080e5d22272d2`** — recorded at exit for the next
session's baseline. It was **not** in this phase's 13-file preflight set, so no entry comparison is
claimed for it; it is **clean against `HEAD`** (absent from `git diff --name-only`), which is what
matters for the Lane D finding.

**Nothing has been implemented by any session.** Because every hash is byte-identical to Phase 17,
the Bandit scan, W-6 probe, D7-1 bind probe and quota runs were **not repeated** — per the brief's
reuse rule. `tests/test_a23_redis_pool_lifecycle.py` still ABSENT.

**No new CI activity.** `gh run list` is identical to the A23E17 capture: newest is
`38132697285` (`05 Security`, schedule, **failure**). No new push, build or deploy.

---

## 2. Grant matrix

| Lane | Workstream | Target files | Grant |
|---|---|---|---|
| A | frontend gate | `06-frontend-deploy.yml` | **`D-P15-4` NOT GRANTED** (request only) |
| B | correctness evidence | `01-pr-check.yml` | **`D-P15-3` NOT GRANTED** |
| C | verifier | `scripts/require_security_scan.py` | **NOT GRANTED** |
| D | image provenance | `02-build.yml`, `ecs.tf`, deploy scripts | **NOT GRANTED — no decision ID exists yet (§4)** |
| E | Bandit parity | gate test + 3 migrations | **`D-P15-1` NOT GRANTED** |
| F | D7-1 vault | `orders.py`, `market.py` | **NOT GRANTED** |
| G | D7-2 updater | `portfolio_cache_updater.py` | **NOT GRANTED** |
| H | tenant / RLS | tenant context, RLS migration | **NOT GRANTED** + no PostgreSQL |
| I | Redis / quota / workers | `redis_manager.py` | **NOT GRANTED** + no Redis |
| J | Terraform / Trivy | `alb.tf`, `security_groups.tf` | **NOT GRANTED** |

Rejected as authorization: requests, preservation ticks, test artifacts, reports, repository write
access, and this prompt. Also checked and rejected: PHASE19/20/21 (0 grants between them) and the
concurrent `PHASE18_RELEASE_GATE_AND_CRITICAL_CLOSURE.md`.

---

## 3. Lane A/C — the Phase 17 gate is unsafe, demonstrated and superseded

### 3.1 Self-audit of the Phase 17 suite, as the brief requires

The 42 Phase 17 tests were re-run at unchanged hashes: **42 passed**. The mock correction described
in Phase 17 §A3 **is present** in the current artifact. No accidental network call remains — the
one that existed was the bug Phase 17 recorded and fixed.

But the suite was auditing the wrong surface. **The gate it validates has a structural hole.**

### 3.2 The defect

`phase17_frontend_release_gate.py` checks the **workflow-run** conclusion only. A run can conclude
`success` while a mandatory **job** inside it was skipped or soft-failed, and this repository's
workflows are shaped precisely that way:

| Evidence | Source |
|---|---|
| `continue-on-error: true` at three places | `01-pr-check.yml:96, :116, :120` |
| The workflow's own warning | `01-pr-check.yml:47` *"`format-check` — black and isort, `continue-on-error: true`. Nothing depends on it."*; `:57` *"`continue-on-error`, and the ratchet is back"* |
| Six jobs gated on `needs: static-audit`, so an unmet `needs:` leaves them **skipped**, not failed | `:126, :286, :384, :568, :673` |
| Three gating jobs plus an `if: always()` reporter | `05-security.yml:16, :47, :108, :158` |

### 3.3 Demonstrated by execution, not asserted

`staging/phase18_test_release_gate.py::test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`
imports the **Phase 17 module** and shows it **admits** a candidate whose required runs are green
but whose mandatory jobs are absent entirely. `test_phase18_refuses_the_same_run` shows the
superseding gate refuses the identical evidence.

### 3.4 The Phase 18 gate

`staging/phase18_release_gate.py` — **not installed**, Phase 17's file left byte-identical so the
two can be diffed. Policy: for each required workflow, a run must exist for the exact SHA, the
**newest** such run must have concluded `success`, **and every job in `MANDATORY_JOBS` must exist
and have concluded `success`**. `skipped`, `cancelled`, `failure`, `neutral`, unfinished and absent
are all refusals. Requiring an explicit `success` — rather than "not failure" — is deliberate: it is
robust to however `continue-on-error` surfaces in the API and to any conclusion value GitHub adds.

Mandatory vs advisory is **explicit and reviewable**. `format-check` and
`provisioning-and-isolation` are listed in `ADVISORY_JOBS` with reasons: the former is
`continue-on-error` with nothing depending on it; the latter provisions PostgreSQL and **whether it
is release-mandatory is an owner decision this module must not assume**.

Also added for pagination on the **jobs** listing, which Phase 17 had only for runs.

### 3.5 A design weakness the tests surfaced, and the fix

Two tests failed on first run. Both were my own expectations, but diagnosing them exposed a real
usability flaw: the gate **failed at the first problem**, so an operator learns one blocker per CI
cycle. Fixed — `verify()` now **collects every workflow-level problem and reports them together**,
while infrastructure refusals (API, pagination) still raise immediately because in that state the
remaining checks cannot be evaluated at all. `test_reports_every_blocker_in_one_pass` pins it.

### 3.6 Non-vacuity

Two mutation checks. `test_the_mandatory_job_assertion_is_load_bearing` neuters
`_assert_mandatory_jobs_passed` and requires the refusal to disappear;
`test_the_run_conclusion_assertion_is_load_bearing` does the same for the run check. If either
passes without its assertion, the corresponding green tests are meaningless — these fail loudly in
that case.

```
pytest staging/phase18_test_release_gate.py -q   EXIT=0 — 28 passed in 1.86s
pytest staging/phase17_test_frontend_release_gate.py -q   EXIT=0 — 42 passed in 1.58s
```

**Still a reference implementation. Neither gate protects anything until `D-P15-4` is granted and
the workflow is wired. A mocked suite proves logic, not release configuration.**

The `workflow_run` checkout trap from Phase 17 §A2.3 remains load-bearing and is restated in the
module docstring: the checkout must pin `ref: ${{ github.event.workflow_run.head_sha }}` or the gate
validates one commit while the job publishes another.

---

## 4. Lane D — NEW: the gate is bypassable by artifact

No concurrent session reports this; a repo-wide search of `PHASE15`–`PHASE19` for mutable-tag or
ECR-provenance language returns **zero matches**. The concurrent PHASE18 report states `DEPLOY_SHA`
is *"never branch or latest"* — true, and about the **gate**. This finding is about the **artifact**.

### 4.1 The gated path is sound

`03-deploy.yml:165, :179` set `IMAGE_TAG: ${{ github.event.workflow_run.head_sha || github.sha }}`
— SHA-pinned. `:167-174` resolves the image digest for that exact tag and **fails closed** when
absent (*"CRITICAL: No ECR image artifact found for commit SHA"*). `:93` feeds the same SHA to
`require_security_scan.py` as `DEPLOY_SHA`. That path refused `ecd23a9e` correctly.

### 4.2 The ungated writer

`02-build.yml` builds and pushes **two** tags:

```
:69   IMAGE_TAG: ${{ github.sha }}
:72   docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG -t $ECR_REGISTRY/$ECR_REPOSITORY:latest …
:75   docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
:76   docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
```

`02 Build` triggers on push and **succeeded** on `ecd23a9e` (run `38129699471`) while `05 Security`
**failed** (`38129699454`). There is no gate between the build and the `latest` push. So
**`latest` now resolves to the security-red, correctness-unevidenced `ecd23a9e` image.**

### 4.3 Who consumes `latest`

| Consumer | Line | Nature |
|---|---|---|
| `terraform/ecs.tf` | `:28` api, `:126` tee, `:198` mds, `:284` websocket, `:378` simulator, **`:478` worker-`${each.key}`** | task-definition image is `…:latest` |
| `scripts/ecs_deploy_and_diagnose.py` | `:94` | `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")` — **defaults to `latest`** |
| `scripts/deploy.sh` | `:9` | `IMAGE_TAG=${2:-latest}` — **defaults to `latest`** |
| `DEPLOYMENT.md` | `:53-54` | documented manual procedure pushes `latest` |

### 4.4 What is and is not established — stated precisely

Probe `staging/phase18_tfstate_images.py` → `…_images.txt`, reading only `"image"` fields:

| State file | ECS task definitions | Images |
|---|---|---|
| `terraform/terraform.tfstate` (current, 25,008 B) | **0** | **0** |
| `terraform/terraform.tfstate.backup` (50,546 B) | 6 | 5, **all `:latest`** — api, mds, simulator, tee, websocket |

**ESTABLISHED:** `latest` points at an unvalidated image; `ecs.tf` declares six task definitions on
`latest`; two live deploy scripts default to `latest`; the backup state proves those task
definitions **were** applied at some point.

**NOT ESTABLISHED:** that ECS is *currently* serving from them. Current Terraform state contains
**zero** ECS resources. That could mean torn-down infrastructure, a different state backend, or
state drift — **the repository cannot distinguish these, and I am not claiming it can.** Both
`ecs.tf` and an identically-sized `ecs.tf.disabled` are tracked, which adds to the ambiguity.

**The risk is therefore conditional but real:** if any ECS task restarts, scales out, or is deployed
through either script's default, it pulls the image the security gate refused. The gate blocked the
CI lane; it does not bind the artifact.

### 4.5 Owner action required — no decision ID exists for this

> **Smallest safe fix:** stop `02-build.yml` pushing `latest` on an ungated build. Either drop
> `:72`/`:76`'s `latest` tag entirely (the deploy path never reads it), or move the `latest` push
> into an explicit post-gate promotion step that runs only after `05 Security` passes for that SHA.
> Then pin `ecs.tf` and both scripts to an immutable identity — a digest, or the SHA tag.
>
> **Not executed:** this touches `.github/workflows/02-build.yml` and `terraform/ecs.tf`, neither
> granted. **No ECR image was deleted, no tag moved, no task definition changed, no Terraform
> applied.** Owner: whoever owns the release pipeline — **UNIDENTIFIED**, no `CODEOWNERS` exists.

---

## 5. Lanes E–J — reconciled, not re-proved

Source hashes unchanged, so prior measurements stand and expensive checks were not repeated.

| Lane | Status | Basis |
|---|---|---|
| **E Bandit** | **Divergence stands.** CI `bandit -r backend_app -ll` → exit **1**, 9 MEDIUM B608 across 3 migrations; `tests/test_bandit_sast_gate_is_green.py` → exit **0**, 3 passed, because its scan is scoped to one module (`GATED_MODULE = …/transactional_execution_manager.py`) while the findings are in `alembic/versions/`. All four hashes unchanged → **not re-scanned**. `D-P15-1` NOT GRANTED, so no `# nosec`, no gate-test change, no negative control added |
| **F D7-1** | 3 invalid `access_token=` sites vs a 2-positional signature; `exchange.py:993` correct, so the count is 3. The concurrent PHASE18 adds a sharper impact statement for `market.py:50-54` — **authenticated users served anonymous market data** — which I adopt. No edit |
| **G D7-2** | `portfolio_cache_updater.py` clean `a74d7644…`. Unexamined beyond the hash this phase; carried to the concurrent sessions holding it |
| **H Tenant/RLS** | **NOT CERTIFIED.** Static findings stand (context unwired on the request path; `FORCE ROW LEVEL SECURITY` in zero migrations; every `ENABLE`/`CREATE POLICY` wrapped in `try/except` + `print`, so source proves "attempted" not "achieved"; app role is table owner). **No PostgreSQL** → no runtime evidence. `FORCE RLS` **not** proposed as an isolated change. No gitignored DSN opened |
| **I Redis/quota/workers** | **No Redis** (6379 unreachable, 0 `REDIS*` env vars, Docker absent). QUOTA-01's 5 real-Redis tests remain **skipped, not passed**. Atomicity **not claimed** from in-process tests. W-4 `UNKNOWN`-exit policy left undefined — **no exit rule invented**, no W-5 recovery implemented |
| **J Terraform/Trivy** | **Deferred to the concurrent PHASE18 report**, which has the better disposition: 5 CRITICAL / 2 rules — `AWS-0054` (`alb.tf:52`) a justified false positive behind a `lifecycle.precondition` that refuses the prod apply; `AWS-0104` ×2 accept-and-document (ALB, and `ecs_tasks` egress which is load-bearing for ECR/Secrets Manager/venues); `AWS-0104` ×2 on `aws_security_group.redis` the only safe tightening. Trivy is **not installed locally** → unverified independently. I did **not** duplicate this |

---

## 6. Commands and results

| Command | Exit | Result | Artifact |
|---|---|---|---|
| `pytest staging/phase18_test_release_gate.py -q` | **0** | **28 passed**, 1.86 s | `staging/phase18_gate_tests.txt` |
| same, first attempt | 1 | 25 passed / 2 failed — my expectations; drove the §3.5 fix | superseded |
| `pytest staging/phase17_test_frontend_release_gate.py -q` | **0** | **42 passed**, 1.58 s | `staging/phase18_phase17_rerun.txt` |
| `python staging/phase18_tfstate_images.py` | 0 | 0 ECS resources current / 6 in backup, 5 `:latest` | `staging/phase18_tfstate_images.txt` |
| `gh run list` (read-only) | 0 | no new runs since A23E17 | — |

**Blocked / unavailable:** PostgreSQL (port closed), Redis (port closed, no Docker), Trivy (not
installed), AWS credentials, venue credentials, `01 PR Check` (undispatchable — `pull_request`-only,
and dispatching CI is unauthorized). **No skip was counted as a pass.**

Not repeated, by design: Bandit whole-tree (≈96 s), W-6 probe, D7-1 bind probe, QUOTA-01 suite — all
inputs byte-identical.

---

## 7. Release disposition and owner decisions

**DO NOT RELEASE.** `ecd23a9e` has a failing security workflow, **no** correctness evidence
obtainable, an ungated frontend publication path that already published, and a mutable image tag
pointing at the refused artifact.

| # | Decision needed | Smallest action | Owner |
|---|---|---|---|
| **1** | **`latest` tag provenance (§4) — NEW, no ID assigned** | Remove the ungated `latest` push from `02-build.yml:72,76`, or gate it behind a post-gate promotion; pin `ecs.tf` and both scripts to a digest | release-pipeline owner — **UNIDENTIFIED** |
| **2** | `D-P15-4` **+** `D-P15-3` together | Grant both. Implementation is written and **70 gate tests** pass across the two proposals; landing is a copy plus the `ref:` pin. Use the **Phase 18** gate — Phase 17's is unsafe (§3.2) | **UNIDENTIFIED** |
| **3** | `D-P15-1` | The 4-part change: whole-tree scope, negative control, keep the AST guard, 9 rule-specific dispositions | migrations' author |
| 4 | Redis A23 P1 | Add a register Owner row, then grant in that role (`W1D-12` precedent) | **UNIDENTIFIED** |
| 5 | D7-1, D7-2, W-6 | Independent grants; all have measured evidence and written specs | respective owners |
| 6 | TENANT-01 | Grant **plus** an isolated PostgreSQL. Wire context **before** `FORCE RLS` | DB + code owner |
| 7 | QUOTA-01 | An authorized dedicated Redis instance or CI lane | coordinator |
| 8 | Trivy `AWS-0104` redis egress | The one safe tightening, per the concurrent PHASE18 §6 | infra owner |

---

## 8. Preservation audit

| Check | Result |
|---|---|
| `HEAD` / branch / divergence / worktrees | unchanged — `ecd23a9e…` / `main` / `0 0` / 1 |
| Staged / unmerged | **0 / 0** |
| Tracked files modified | **0** |
| All 13 target hashes (§1.1) | **unchanged at exit** |
| Stash entries | **2, both preserved** — not applied, not dropped, not inspected |
| Phase 17 artifacts | byte-identical (`4c38728f…`, `13b5e6cd…`); suite re-run green |
| Concurrent reports | none overwritten; the concurrent `PHASE18_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` untouched |
| `commit`/`push`/`stage`/`reset`/`checkout`/`stash`/`clean`/`revert`/branch change | **none used** |
| CI dispatch / deploy / publish / Terraform apply / SG change / ECR mutation / production DB / live trading | **none** |
| Credentials | none opened or printed. The gate suites' token is the literal `"fake-token-not-a-credential"`. The tfstate probe prints only `"image"` fields |

**Files created — 7, all new, all `staging/phase18_*` plus this report:** `phase18_release_gate.py`,
`phase18_test_release_gate.py`, `phase18_tfstate_images.py`, `phase18_tfstate_images.txt`,
`phase18_gate_tests.txt`, `phase18_phase17_rerun.txt`, and this file.

**Distinctions kept explicit:** source evidence (§4.3, §5) · unit/mock tests (§3, logic only) ·
integration evidence (**none this phase**) · prior-session findings (§5, reused at unchanged hashes)
· **actual release certification: NONE**.

**LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · DO NOT RELEASE.**
