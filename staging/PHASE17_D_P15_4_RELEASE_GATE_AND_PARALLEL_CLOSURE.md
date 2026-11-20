# Phase 17 — D-P15-4 Frontend Release Gate, Same-SHA Validation, and Parallel Closure

**Destination verified ABSENT before writing.** **0 production files · 0 test files under `tests/` ·
0 workflow files · 0 registers · 0 approvals created · 0 CI runs dispatched · 0 deployments.**
All new files are under `staging/phase17_*`.

> ## OUTCOME
>
> **`D-P15-4` is NOT GRANTED.** It exists only as a *request* (`PHASE15:313-316`). No
> implementation authorization exists in any record, so `.github/workflows/06-frontend-deploy.yml`
> was **not modified**.
>
> **What was delivered instead of a recommendation: a tested reference implementation.** A
> same-SHA release gate plus a **42-case failure-mode suite, 42 passed**, which refuses the actual
> historical incident. On a grant this is a copy, not a design exercise.
>
> **Two new findings that change the D-P15-4 design.** (1) A `workflow_run` conversion done the
> obvious way would **publish the wrong commit** — the checkout trap, §A2.3. (2) **`D-P15-4` cannot
> be landed alone**: requiring `01 PR Check` would refuse every direct-push release, because that
> workflow is `pull_request`-only (§B).
>
> **Bandit divergence measured on both sides at the current HEAD:** the CI command exits **1 with 9
> findings** while the repository's own gate suite exits **0 with 3 passed**. The suite cannot
> detect what is blocking every backend deploy. **`D-P15-1` NOT GRANTED**, so unchanged.
>
> **LIVE TRADING: OFF / NOT CERTIFIED. TENANT ISOLATION: NOT CERTIFIED. RELEASE: BLOCKED.**

---

## 1. Preflight snapshot and preservation manifest

Captured before any file was written.

| Property | Entry | Exit |
|---|---|---|
| Branch | `main` | `main` |
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| `origin/main` | `ecd23a9e…` | unchanged |
| Divergence (ahead behind) | `0 0` | `0 0` |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | **2** | 2 |
| Untracked files (`-uall`) | 68 | 71 (+3, all `staging/phase17_*`) |
| Worktrees | 1 | 1 |

The brief correctly flagged the last reported HEAD as historical. **Re-measured: it is still
current.** The two unstaged files are other workstreams' and were **not touched**:
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`.

### 1.1 Hashes of every file in scope — all verified unchanged at exit

| Blob (entry = exit) | Path |
|---|---|
| `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | `.github/workflows/06-frontend-deploy.yml` — **matches `PHASE15:49`, no concurrent edit** |
| `eb8078eac06268447d60f26ef7a97a537ceed988` | `.github/workflows/03-deploy.yml` |
| `91d55a379115c88edd2fd6be01871b82a75ef498` | `.github/workflows/01-pr-check.yml` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `42c7696eb7cb993b8942b7f0053bf7490e3e5edd` | `.github/workflows/02-build.yml` |
| `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` | `scripts/require_security_scan.py` |
| `d7b0e811b615ee13ba089b5cbe9d790161176de2` | `backend_app/backend/redis_manager.py` |
| `df4fd42a151015124ae7eb3743733107106c413e` | `backend_app/backend/exchange_executor.py` |

`tests/test_a23_redis_pool_lifecycle.py`: **still ABSENT.**

---

## 2. Authorization status — every workstream

Read from the current records before any work. **Zero implementation grants exist.**

| ID | Scope | Status | Evidence |
|---|---|---|---|
| **`D-P15-4`** | frontend release gate | **NOT GRANTED** | `PHASE15:313-316` is the request; repo-wide search finds **no** `GRANTED` occurrence for it |
| **`D-P15-3`** | `01 PR Check` on `main` | **NOT GRANTED** | `PHASE15:398` |
| **`D-P15-1`** | 9 × B608 `# nosec` | **NOT GRANTED** | `PHASE15:401`, `:530` |
| **`D-P15-2`** | Trivy IaC CRITICAL | **NOT GRANTED** | `PHASE15:400` |
| Redis A23 P1 | `redis_manager.py` | **NOT GRANTED** | A23E6 §3 re-measured: **12 ☐, 0 marked, 6 blank** |
| W-6 | classifier | **NOT GRANTED** | A23E13:170-173 |
| D7-1 | vault signature | **NOT GRANTED** | D11 §4 |
| TENANT-01 / D6-C | tenant context | **NOT GRANTED** + no PG | D10 §178; §G |
| QUOTA-01 | real-Redis | no lane | §H |
| D7-2 / W-3 / W-4-5 | worker lifecycle | **NOT GRANTED** | D10 §7 |

Checked and rejected as authorization: the two newest reports
(`PHASE15_SECURITY_GATE_CLOSURE_AND_MAIN_VERIFICATION.md` 20:17, `PHASE16_…` 20:16). Both declare
0 production files modified; PHASE16's single bare `GRANTED` is `D5 | CONDITIONALLY GRANTED,
sign-off ABSENT` — the unrelated W1D drift item. Nothing was inferred from repository write access,
commit authorship, or the existence of `D-P15-4`.

---

## 3. Workstream A — D-P15-4

### A1. Current state re-validated, not assumed

`06-frontend-deploy.yml` at `285cc2d1…`: `on: push` (`branches: [main, master]`,
`paths: ['algo22-terminal/**', …]`) plus `workflow_dispatch`; **one** job `deploy-frontend`;
**0 `needs:`**, **0 `workflow_run`** occurrences. **No gate.** Unchanged since PHASE15 recorded it.

Reference pattern re-confirmed: `03-deploy.yml:4-7` triggers on `workflow_run: ["02 Build"]` with
`if: workflow_run.conclusion == 'success'` (`:73`) and `needs: pre-deployment-validation` (`:130`);
`scripts/require_security_scan.py:58` queries `?head_sha=`. **P11-B08 is not re-opened** — closed
and functioning per A23E17 §12, and its hash is unchanged.

### A2. The proposal, built and tested

Two new artifacts, both under `staging/`, neither installed:

| File | Role |
|---|---|
| `staging/phase17_frontend_release_gate.py` | the gate — a generalisation of the backend gate from 1 required workflow to N |
| `staging/phase17_test_frontend_release_gate.py` | **42-case** failure-mode suite, deterministic in-process fakes, no network, no secret |

```
.venv\Scripts\python.exe -m pytest staging/phase17_test_frontend_release_gate.py -q
EXIT=0 — 42 passed in 7.17s        → staging/phase17_gate_tests.txt
```

`pytest.ini` sets `testpaths = tests`, so this file is **not collected by the default lane** and
cannot disturb a concurrent workstream.

#### A2.1 Policy, stated so it can be disagreed with

Every workflow in `REQUIRED_WORKFLOWS` must have ≥1 run for the exact SHA, and the **newest** such
run (by `run_started_at`) must have concluded `success`. Fails closed on: missing/malformed SHA,
missing token or repo, HTTP error, unreachable API, unparseable or non-object payload, absent
`workflow_runs`, no run for a required workflow, any non-`success` conclusion, `None` conclusion,
`status != "completed"`, `head_sha` mismatch, branch outside the allow-list, ambiguous pagination,
and an empty policy. **No override exists** — unlike the backend gate's `ALLOW_UNSCANNED` — and a
test asserts structurally that none is ever added.

#### A2.2 Newest-wins is the subtle rule

`any(success)` would release a commit carrying an older green run and a newer red one. The real
`ecd23a9e` has exactly this shape: `05 Security` ran twice (`38129699454` push, `38132697285`
schedule), **both failed**. `test_a_newer_failure_overrides_an_older_success_for_the_same_sha`
pins the refusal; `test_a_newer_success_does_override_an_older_failure` pins the converse, so a
re-run can still unblock a release.

#### A2.3 NEW — the checkout trap, which `D-P15-4`'s recommendation does not mention

`D-P15-4` says to *"trigger on `workflow_run` … and verify `head_sha`"*. Done the obvious way that
**publishes the wrong commit**.

A `workflow_run`-triggered run uses the repository's **default branch**, and `actions/checkout` in
that context defaults to the default branch rather than the commit that triggered the upstream run
([actions/checkout discussion #1637](https://github.com/actions/checkout/discussions/1637);
[workflow_run event semantics](https://stackoverflow.com/questions/63343937/how-to-use-the-new-event-workflow-run-of-gtihub-action-added-recently/)).
So a gate that validates SHA X can sit in front of a build of `main`'s tip and publish Y **while
attesting X** — strictly worse than no gate, because it looks verified. The checkout must pin:

```yaml
- uses: actions/checkout@v4
  with:
    ref: ${{ github.event.workflow_run.head_sha }}
```

and the gate must receive that same SHA, so validated = built = published. Relatedly, a commit
reachable from several branches can persist as `head_sha`
([observed behaviour](https://stackoverflow.com/questions/79941021/github-actions-workflow-run-trigger-keeps-firing-with-old-commit-sha-even-after)),
which is why the gate checks `head_branch` against an allow-list rather than assuming it.
*(Sources read and paraphrased; content was rephrased for compliance with licensing restrictions.)*

#### A2.4 Brief requirements 1-12, mapped

1 exact SHA ✓ `_SHA_RE` + `head_sha` equality · 2 security ✓ `05 Security` required · 3 correctness
✓ `01 PR Check` required (**see §B — blocked**) · 4 missing/failed/cancelled/skipped/stale/ambiguous
✓ all tested · 5 branch/commit mismatch ✓ · 6 fail-closed on API/permission/metadata/pagination ✓
(403 = missing `actions: read`) · 7 no dispatch bypass ✓ structurally asserted · 8-11 bundle hygiene,
WebSocket probe, least privilege, SHA correspondence — **preserved by construction**: the proposal
adds a gate job and the `ref:` pin, and removes nothing · 12 no duplicate builds ✓ gate is an API
query, not a rebuild.

### A3-A4. Failure modes proven, nothing deployed

42 cases: happy path (1), historical incident (2), stale/mismatched evidence (4), non-success
conclusions (9 parametrized), branch scoping (2), malformed input (10), API failures (12),
pagination (2), no-override (1). **No deployment workflow was dispatched; the real S3/CloudFront
path was never executed.**

**One test-authoring defect found and fixed in-pass, recorded rather than hidden.**
`test_a_newer_failure_overrides_an_older_success_for_the_same_sha` built its run list but never
installed the mock, so the first run made an **unauthenticated GET to api.github.com** and asserted
on a 401 instead of the policy. No credential was involved (the token is the literal
`"fake-token-not-a-credential"`) and nothing was mutated. Fixed by adding the missing `api(...)`
call; the suite then went 41→42 passed. Worth recording because it is exactly the vacuous-pass
class this engagement has repeatedly guarded against — it failed loudly instead of passing green.

---

## 4. Workstream B — correctness evidence, and why D-P15-4 cannot land alone

Measured at source:

| Workflow | Triggers | Can produce evidence for a direct-push SHA? |
|---|---|---|
| `01-pr-check.yml` | **`pull_request` only**, `branches: [main, master]` | **NO** |
| `05-security.yml` | `push`, `pull_request`, `schedule` | **YES** — run `38129699454` exists for `ecd23a9e` |
| `02-build.yml` | (hash `42c7696e…`) | ran on the push — success `38129699471` |

`01-pr-check.yml` has **no `push`, no `workflow_dispatch`, no `workflow_call`**. So for
`ecd23a9e` — a direct push — there is **no correctness evidence and no way to produce any** without
a workflow change. That is `D-P15-3`, confirmed independently.

**The coupling, which PHASE15 lists as two separate CRITICAL items without stating the direction:**
if `D-P15-4` is implemented with `01 PR Check` required, it refuses **every** direct-push release —
the same shape as the trap the original P11-B08 audit identified. So either:

- **(a)** land `D-P15-4` **and** `D-P15-3` together, or
- **(b)** land `D-P15-4` scoped to `05 Security` only, and add `01 PR Check` when it can run on the
  candidate, or
- **(c)** adopt release-by-PR, so `01 PR Check` always has a run.

`test_refuses_when_pr_check_never_ran_for_the_candidate` encodes this: with only `05 Security`
green, the gate refuses and names `01 PR Check`. **The suite will tell you which option you are in.**

---

## 5. Workstream C — Bandit divergence, measured on both sides

### 5.1 The CI command, re-run at this HEAD

```
.venv\Scripts\python.exe -m bandit -r backend_app -ll -f json -o staging\phase17_bandit_wholetree.json
EXIT=1 · bandit 1.9.4 · 219,319 LOC scanned
```

**9 results · 9 MEDIUM, 0 HIGH · confidence 8 LOW / 1 MEDIUM · all `B608`**, in three Alembic
migrations — **identical to PHASE14's list**:

| File (under `backend_app/`) | Lines |
|---|---|
| `alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |
| `alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |

`05-security.yml:35-36` runs this with no `|| true`, no `--baseline`, no exclusions, so a single
MEDIUM fails the job → `03 Deploy`'s gate refuses → **nothing reaches ECS**.

### 5.2 The repository's own gate suite, re-run at this HEAD

```
.venv\Scripts\python.exe -m pytest tests/test_bandit_sast_gate_is_green.py -q
EXIT=0 — 3 passed in 96.75s        → staging/phase17_bandit_gate_tests.txt
```

**The divergence, now measured rather than inferred: CI exits 1, the suite exits 0.**

The cause is scope, and it is **deliberate and documented** in the test itself:
`GATED_MODULE = BACKEND_APP / "backend" / "transactional_execution_manager.py"`, and
`test_bandit_reports_nothing_in_the_module_the_gate_failed_on` runs
`bandit -ll -f json {GATED_MODULE}` — **one file**. Its docstring: *"scoped to one file so it runs
in about a second instead of the 5m44s a full `-r backend_app` sweep takes. The AST test below is
what covers the rest of the tree."*

The consequence the author could not have anticipated: **the 9 current findings are in
`alembic/versions/`, a path the single-file test never scans.** And the whole-tree AST guard
`test_every_interpolation_into_sql_is_constrained_before_it_gets_there` **also** passes — correctly,
because those 9 sites interpolate `_validate_table_name(...)` results, which is its RULE 2. So both
tests are right about what they assert, and **neither can see the condition blocking every deploy.**

Verified at source, not inherited: each of the 9 sites validates its interpolated identifier through
`_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, **raises**, never sanitises — the test file's
`SQL_SAFE_VALIDATORS` admission criterion) and binds user-controlled values as parameters. The
findings are **false positives on safety grounds but legitimately red**: `nosec` count on these
sites is **0**, so Bandit has nothing to suppress them with.

**`D-P15-1` is NOT GRANTED**, so no `# nosec` annotation, no gate-test change, no AST-test change,
and no evaluation of `re.match` vs `re.fullmatch` was applied. The grant would need: extend the gate
test to the whole-tree command and threshold; keep the AST guard independent; add a
regression test proving an introduced unsuppressed finding is detected; annotate only the 9 verified
sites, rule-specific, with true reasons. **No blanket suppression, exclusion, baseline, threshold
weakening or ignored exit code** — and the gate must end green because the code is right, not
because the scanner was silenced.

---

## 6. Workstreams D-I — status, with reuse justified by hashes

Source hashes are unchanged from A23E17 (§1.1), so findings are **reused, not re-derived**, per the
brief's reuse rule. No workstream was abandoned for lack of a grant elsewhere; each was carried to
the limit its own authorization and this host's environment allow.

| WS | Item | Status this phase | Blocker |
|---|---|---|---|
| **D** | Redis A23 P1 | **OBSERVED.** A23E6 re-measured 12 ☐ / 0 marked / 6 blank. `redis_manager.py` `d7b0e811…` clean; lifecycle test ABSENT. Preservation ticks **not** treated as authorization | no owner row; see A23E17 §2.1 — add a register Owner row, then grant in that role per the `W1D-12` precedent |
| **E** | W-6 classifier | **TESTED** (A23E17 §3.1: 3/8 misclassified, measured) + third balance-shadowing defect. No suite created | W-6 grant |
| **F** | D7-1 vault | **OBSERVED, reproduced** (7 sites / 4 bind / 3 `TypeError`) | D7-1 grant |
| **G** | TENANT-01 / D6-C | **OBSERVED.** Chain mapped; `FORCE RLS` absent from all 4 migration trees; every `ENABLE`/`CREATE POLICY` wrapped in `try/except` + `print`, so source proves "attempted" not "achieved"; app role is table owner → policies inert. D6-C `EXISTS` correction already applied by a concurrent session | grant **and** an approved isolated PostgreSQL. **Port 5432 closed** → unexecutable here. **NOT CERTIFIED** |
| **H** | QUOTA-01 | **TESTED** in-process (43 passed / 5 skipped). Re-measured this phase: **0 `REDIS*` env vars, port 6379 closed, Docker absent** | no approved Redis lane. **Atomicity is NOT claimed from in-process tests.** Still **OPEN** |
| **I** | D7-2 / W-3 / W-4-5 | **OBSERVED**, carried forward. W-3 `coalesce-never-swap` and migration 030's no-FK rule preserved. **W-4/W-5 `UNKNOWN` exit semantics remain undefined — no exit rule was invented, and no W-5 recovery was implemented** | owner decisions |

---

## 7. Commands, exit codes, artifacts

| Command | Exit | Result | Artifact |
|---|---|---|---|
| `bandit -r backend_app -ll -f json` | **1** | 9 MEDIUM B608, 219,319 LOC | `staging/phase17_bandit_wholetree.json`, `…_stderr.txt` |
| `pytest tests/test_bandit_sast_gate_is_green.py -q` | **0** | **3 passed**, 96.75 s | `staging/phase17_bandit_gate_tests.txt` |
| `pytest staging/phase17_test_frontend_release_gate.py -q` | **0** | **42 passed**, 7.17 s | `staging/phase17_gate_tests.txt` |
| `pytest …` (first attempt, pre-fix) | 1 | 41 passed / 1 failed — my own mock bug (§A3) | superseded |
| `gh run list` (read-only) | 0 | CI provenance table, §4 | — |

No PostgreSQL lane, no Redis lane, no sandbox/venue lane, no CI dispatch, no deploy. **Zero skips
were counted as passes.**

---

## 8. Outstanding dependencies and the smallest next action

| # | Blocker | Smallest next action |
|---|---|---|
| **1** | **`D-P15-4` + `D-P15-3` together** (§4) | Grant both. `D-P15-4`'s implementation is written and 42-case tested; landing is a copy of `staging/phase17_frontend_release_gate.py` → `scripts/`, a gate job, **and the `ref:` pin of §A2.3**. Alternatively grant `D-P15-4` scoped to `05 Security` only. **Owner UNIDENTIFIED — `.github/` has no owner row and there is still no `CODEOWNERS`** |
| **2** | `D-P15-1` — 9 × B608 | Grant the 4-part change in §5.2. It is the only thing between the backend and a green security gate, and the suite currently **cannot see** the redness |
| **3** | Redis A23 P1 | Add a register Owner row, then grant in that role (`W1D-12` precedent) |
| 4 | `D-P15-2` Trivy IaC | Retrieve that job's log or run Trivy locally |
| 5 | W-6, D7-1 | Independent grants; both have measured evidence and written specifications |
| 6 | TENANT-01 | Grant **plus** an isolated PostgreSQL. Wire context **before** `FORCE RLS`, or owner-path reads silently return empty |
| 7 | QUOTA-01 | An authorized dedicated Redis instance or CI lane |

**Risks not fixed:** frontend publishes with no gate (`D-P15-4`); released commit has zero
correctness evidence (`D-P15-3`); backend deploy blocked by 9 B608 while the suite reads green
(`D-P15-1`); Redis pool leak and reconnect-after-shutdown; W-6 persisting indeterminate timeouts as
determinate rejections; D7-1 three guaranteed `TypeError`s on live order and market paths;
TENANT-01 inert RLS under an owner role; QUOTA-01 unverified atomicity.

---

## 9. Final preservation verification

| Check | Result |
|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e…` — **unchanged** |
| Staged / unmerged | **0 / 0** |
| Tracked files modified by this phase | **0** |
| All 8 in-scope hashes (§1.1) | **unchanged at exit** |
| `tests/test_a23_redis_pool_lifecycle.py` | still ABSENT |
| Other workstreams' work | 2 unstaged files untouched; no existing report or artifact overwritten |
| `stage`/`commit`/`push`/`merge`/`rebase`/`reset`/`stash`/`clean`/`revert`/branch change | **none used** |
| Deploy / rollback / CloudFront / DNS / Terraform / production DB / exchange / live trading | **none** |
| Credentials | none opened, none read, none echoed. The gate suite's token is the literal `"fake-token-not-a-credential"` |

**Files created — 6, all new, all `staging/phase17_*`:** `phase17_frontend_release_gate.py`,
`phase17_test_frontend_release_gate.py`, `phase17_bandit_wholetree.json`,
`phase17_bandit_stderr.txt`, `phase17_bandit_gate_tests.txt`, `phase17_gate_tests.txt`, plus this
report. No credentials, tokens, DSNs or tenant data in any of them.

**RELEASE REMAINS BLOCKED.** The mandatory gates do not pass on `ecd23a9e`, the frontend
publication path does not enforce them, and no authorized release process permits publication.
**LIVE TRADING: OFF / NOT CERTIFIED.**
