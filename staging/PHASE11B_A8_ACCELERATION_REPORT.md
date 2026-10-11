# Phase 11B-A8 — Accelerated Launch Blocker Closure

**Live trading: OFF / NOT CERTIFIED.** No venue was contacted, no order was placed, no
production database or secret was accessed, no CCXT change was made. No commit, push, PR,
branch, merge, workflow trigger or infrastructure change was created. No shared workflow
file was edited.

Executed 2026-10-10, 16:17–17:4x local, against `main` at `074b772a`.

---

## 1. Baseline Git and ownership snapshot

Measured before any change, and re-verified after.

| Property | At start | At end |
|---|---|---|
| Branch | `main` | `main` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **unchanged** |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **unchanged** |
| Ahead / behind | `0 / 0` | `0 / 0` |
| Staged (shared index) | 22 entries | **22 entries** |
| `.git/index` SHA-256 | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` | **byte-identical** |
| Working-tree entries | 254 (W1B's count) | +4, all mine, all untracked |

The index hash matching W1B's independently recorded baseline is the proof that nothing
was staged, unstaged, reset, stashed or cleaned. `git reflog` had no entry for today at
baseline and has none now.

### 1.1 Concurrent work and ownership

**This repository has at least one other agent session actively writing during this
one.** Observed directly, not inferred: `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py`
measured 23,442 bytes at my first directory listing and 25,760 bytes
(mtime 15:58:57) minutes later, when Workstream 1B's collection guard appeared in it.
Every ownership decision below treats that as live.

| Area | Status | Owner | Action taken |
|---|---|---|---|
| 22 staged entries — `tenant_db_context.py`, `dependencies.py`, `auth.py`, `exchange.py`, `terraform/*`, `docs/adr/*`, `scripts/verify_rls*`, 5 `tests/test_tenant*`/`test_sc3*`/`test_verify_rls*` | staged by another workstream | Stream F — Security / Stream E — SRE | **untouched** |
| `.github/workflows/01-pr-check.yml` | ` M`, 170 added / 0 removed, two hunks (D5′ real-Redis env + D-series provisioning job) | Stream D | **untouched**; numstat still `170 0` |
| `.github/workflows/05-security.yml` | `M ` staged | Stream E — SRE | **untouched** |
| `.github/workflows/03-deploy.yml` | clean | Stream E — SRE | **untouched** — patch proposed only, §3.3 |
| `tests/conftest.py`, `pytest.ini` | ` M` by other workstreams | Stream D / Platform | **untouched** |
| `backend_app/core/rate_limit.py` | ` M` by another workstream | Platform | **untouched** — the fix was test-side, §3.1 |
| `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` | `??`, rewritten mid-session by W1B | Workstream 1B (live) | **untouched** |
| `tests/test_api03_rate_limit_storage_is_fail_closed.py` | `??`, mtime 10-08 02:26, no concurrent edit | API-03 / Stream D | **edited** — §3.1 |

---

## 2. Findings reproduced versus stale

Each reported failure was re-run in the current tree. One is stale, one reproduced
exactly, one reproduced with its recorded detail wrong.

| # | Finding | Register ID | Current reproduction | Root-cause evidence | Verdict |
|---|---|---|---|---|---|
| A | `test_p10i_probe_pg_find_uses_shared_dsn_contract.py` aborts collection | P11-H15 (first half) · W1A-01 | **DOES NOT REPRODUCE.** `pytest <file> --collect-only` → `collected 0 items / 1 skipped`, exit 5. Full `pytest tests/ --collect-only` → **14,467 collected, 0 errors, no `Interrupted`**, exit 0 | Closed by Workstream 1B before this phase: a conditional module-level `pytest.skip(..., allow_module_level=True)` fires only while `staging/probe_pg_find.py` is absent. `staging/PHASE11B_W1B_BASELINE.md` §3 | **STALE — already closed by another workstream.** Residual is suspended coverage, not a collection abort |
| B | `test_api03_…::test_the_module_exposes_the_decision_it_actually_applied` is order-dependent | P11-H15 (second half) · TEST-04 | **REPRODUCED.** `pytest test_rate_limiter_fails_closed_in_production.py test_api03_….py -p no:randomly` → **1 failed, 51 passed**, exit 1 | `assert isinstance(rl.config, RateLimitConfig)` → `assert False` against `RateLimitConfig(env='testing', …)`. The polluting file re-imports `backend_app.core.rate_limit`, minting a new `RateLimitConfig` **class object**; the asserting file still holds the class bound at its own import | **REPRODUCED — fixed in §3.1** |
| C | No test gate between a push to `main` and production | P11-B08 | **REPRODUCED**, with one sub-fact corrected | §2.2 | **REPRODUCED — patch proposed, not applied** |

### 2.0 Phase 0 baseline table — owner, minimum fix, authorization

| # | Reproduces? | Affected files | Owner | Proposed minimum fix | Authorization required |
|---|---|---|---|---|---|
| A | **No — stale** | `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` (`??`, live-edited by W1B); `staging/probe_pg_find.py`, `staging/pg_keepalive_r1.py` (both absent) | Stream D / Platform; W1B-01 residual is **Phase 10 Workstream I** | None for the abort — already closed. Add the enforced regression the fix never had (§3.2). **Do not** author the two DSN-writing modules | **none needed** — new test file only, no shared file touched |
| B | **Yes** | `tests/test_api03_rate_limit_storage_is_fail_closed.py` (`??`, no concurrent edit); polluter `tests/test_rate_limiter_fails_closed_in_production.py` (` M`, not edited); `backend_app/core/rate_limit.py` (` M`, not edited) | Stream D | Resolve the config class from the module under test; assert the one-definition property structurally; add a reload regression test. **Test-side only** | **none needed** — the only edited file is untracked and uncontended |
| C | **Yes** | `.github/workflows/01-pr-check.yml` (` M`, 170 lines of two other workstreams), `02-build.yml`, `03-deploy.yml` (clean), `scripts/pre_deployment_validation.py`, `scripts/require_security_scan.py` | **Stream E — SRE** (+ Stream D, + repo admin) | New `scripts/require_pr_check.py` + one step in `03-deploy.yml`, reusing the security gate's fail-closed ladder (§3.3) | **A8-1…A8-4 — ABSENT.** Patch prepared, **not applied** |

### 2.1 Where the prior reports are inaccurate

Three corrections. None of them is cosmetic.

1. **The audit quotes the wrong assertion for finding B.** `PHASE11_LIVE_TRADING_READINESS_AUDIT.md` §6.2 and the P11-H15 register entry both attribute the failure to
   `assert rl.config.storage_uri in (MEMORY_URI, rl.config.redis_url)`. The observed
   failure is on the preceding line, `assert isinstance(rl.config, RateLimitConfig)`. The
   `storage_uri` assertion passes in every order. `REGRESSION_ORDER_ISOLATION.md` §9 has
   it right (class identity after a reload); the register and audit do not. A fix aimed at
   the quoted assertion would have changed a correct line and left the defect in place.
2. **`02-build.yml` is no longer `on: push` only.** P11-B08 records
   `02-build.yml:19-20` as `on: push: branches: [main, master]`. It now declares
   **both** `workflow_call` (lines 5-12) **and** `push: branches: [main, master]`
   (lines 13-14). No workflow in the tree calls it — a grep for
   `uses: ./.github/workflows` returns nothing — so the effective trigger is still the
   push, and **the finding stands.** The line numbers in the register are stale.
3. **`P11-D01` does not exist.** `PHASE11_LAUNCH_BLOCKER_REGISTER.md:529` names
   "P11-D01 (the collection error)" as P11-B08's dependency. No `P11-D01` entry exists
   anywhere in `staging/`. The real ID is `P11-H15`. That dependency is now **discharged**
   (§2, finding A).

### 2.2 Finding C — the deployment path, traced end to end

| Step | Evidence | Gates? |
|---|---|---|
| Change lands on `main` | — | — |
| `01-pr-check.yml` — every test job (`unit-tests`, `paper-mode-tests`, `database-tests`, `provisioning-and-isolation`, `docker-check`, `frontend-tests`) | `on: pull_request: branches: [main, master], types: [opened, synchronize, reopened]` — lines 4-7. **No `push`. No `workflow_dispatch`.** | **does not run on a push to `main`** |
| `02-build.yml` → ECR | `on: workflow_call` + `on: push: branches: [main, master]`, lines 4-14; nothing calls it | builds, does not test |
| `03-deploy.yml` | `on: workflow_run: workflows: ["02 Build"], types: [completed], branches: [main, master]` + `workflow_dispatch`, lines 4-16 | — |
| `pre-deployment-validation` | `if: workflow_run.conclusion == 'success' || workflow_dispatch` (line 73) | build success only |
| → security gate | `scripts/require_security_scan.py` (line 95) | **yes, fail-closed — CRITICAL only** |
| → validation engine | `scripts/pre_deployment_validation.py` (line 125). **Grep for `pytest` in that script: 0 matches** (re-measured) | **no test run** |
| `ecs-deployment` | `needs: pre-deployment-validation` (line 130) | inherits the above |

**Established: deployment can proceed without a successful required test run.** A direct
push to `main` with a red suite reaches production, gated only by a successful container
build, a CRITICAL-severity Trivy result, five non-pytest validation steps and an
ECR-artifact-exists check.

Whether GitHub branch protection closes the direct-push half is **still not determinable
from the repository**; it needs `GET /repos/{owner}/{repo}/branches/main/protection`.

What is well built and must not be disturbed: `require_security_scan.py` is genuinely
fail-closed — it refuses on missing token, missing run, HTTP error, non-success,
`cancelled` and `skipped`, and waits rather than guessing when the scan is concurrent.
It is the correct template for the missing gate.

---

## 3. Changes and exact paths

Two files edited or added under `tests/`. **No production code, no shared workflow, no
`conftest.py`, no `pytest.ini`, no `staging/` tooling, no DSN, no data directory.**

### 3.1 `tests/test_api03_rate_limit_storage_is_fail_closed.py` — order dependence fixed

Untracked (`??`). Three edits:

* `import dataclasses` added to the stdlib import block.
* New helper `_assert_one_config_definition(module)`.
* `test_the_module_exposes_the_decision_it_actually_applied` now resolves the config class
  **from the module under test** instead of from this file's import-time binding:

```python
live_config_class = _assert_one_config_definition(rl)
assert isinstance(rl.config, live_config_class)
assert rl.limiter is not None
assert rl.config.storage_uri in (MEMORY_URI, rl.config.redis_url)
```

**No threshold was raised and no assertion was removed or weakened.** The old
`isinstance(rl.config, RateLimitConfig)` bundled two properties: that `rl.config` is a
well-formed config (real), and that this file's class and the module's class are the same
*object* (an artifact of which file pytest imported first). Only the second is dropped, and
the property it carried as a side effect — that there is **one** definition of
`RateLimitConfig` — is now asserted structurally, which a reload cannot neutralise:

```python
assert live.__name__ == RateLimitConfig.__name__
assert live.__module__ == RateLimitConfig.__module__ == "backend_app.core.rate_limit"
assert [f.name for f in dataclasses.fields(live)] == [...]   # field order too
assert live.__dataclass_params__.frozen is True
```

A genuinely divergent second definition still fails. The fail-closed `storage_uri` and
`limiter` assertions are untouched.

**Regression test added** — `test_the_wiring_assertion_is_not_neutralised_by_a_reload`.
It performs the polluting reload *inside one test*, so the property no longer depends on
collection order, and it carries two self-checks so it cannot rot into a vacuous pass:

```python
assert after.RateLimitConfig is not stale_config_class
assert not isinstance(after.config, stale_config_class)   # the old assertion WOULD fail here
```

It states `ENV=testing` explicitly because the file's autouse `clean_env` blanks the
environment for every case, and an undeclared environment makes the re-import fail closed
by design — which is section 1's subject and is asserted there. The variable under test is
the reload, not the environment.

> Note for the record: writing this regression test is what surfaced that the re-import
> raises `RuntimeError` under a blanked environment. That is `rate_limit.py` behaving
> correctly, not a defect — but it means the original test was only ever green because the
> module already in `sys.modules` was imported before `clean_env` ran.

### 3.2 `tests/test_p11b_a8_collection_integrity.py` — new, closes W1B-02

W1B fixed the one occurrence of the collection abort and raised **W1B-02** to say the shape
is structural rather than a one-off, recommending "the same conditional-skip guard shape
wherever a suite loads a `staging/` path at import time" — with no way to enforce it.
`staging/` is not a package, so module-scope by-path loading is the established pattern
across the P10H/I/J suites, and any of them would take the whole session down the same way
if its target vanished. This file is that enforcement, and it is the regression test finding
A never had.

The invariant: for every module-scope by-path load under `tests/**`, either the target
exists, or the module guards itself with `pytest.skip(..., allow_module_level=True)`
*before* the load — so the outcome is a named skip, never a session-wide abort.

Four tests, including both controls, because a gate that has never been observed failing is
not a gate:

| Test | Role |
|---|---|
| `test_the_sweep_actually_sees_the_suites_it_is_meant_to_police` | positive control — fails if the path resolver stops understanding `REPO / "staging" / "x.py"` |
| `test_the_sweep_detects_an_unguarded_load_of_a_missing_path` | **negative control** — builds the exact P11-H15 shape under `tmp_path`, requires it classified unguarded, then adds the W1B guard and requires reclassification to guarded, then restores the dependency and requires both lists empty |
| `test_no_test_file_aborts_collection_on_a_missing_by_path_dependency` | the P11-H15 assertion |
| `test_collection_blocker_guards_are_reported_and_not_silent` | a guard must name the file it stands in for — the difference between a documented suspension and a quiet mask |

Derived from `staging/probe_p11b_w1b_import_time_paths.py` (W1B, read-only), with one
correction: that probe counted loads inside test bodies as collection-fatal. They are not —
only module-scope loads abort collection — so only those are asserted on, and in-body loads
are reported as deferred. AST only: imports no repository code, starts no server, contacts
no database, writes no file. A substring prefilter plus an `lru_cache` brought the same
three tests from **71.90 s to 15.31 s** (measured) by not re-parsing all 508 files under
`tests/` once per test; the fourth test adds the `tmp_path` negative control, for 25.72 s.

### 3.3 `.github/workflows/03-deploy.yml` — PROPOSED ONLY, NOT APPLIED

Owner is **Stream E — SRE**. This phase's authorization does not name that owner, so per
the brief the patch is prepared and the approval is requested instead. The file is clean in
the working tree and was not touched.

**A naive version of this gate would break every deploy, and that is the main finding
here.** `01 PR Check` runs on `pull_request`, so GitHub records its run against the **PR
head SHA**, not the merge commit that lands on `main`. A gate that queries
`head_sha=<the deploying main SHA>` would therefore find no `01 PR Check` run and refuse
**every** deploy, including correctly reviewed ones — failing closed for the wrong reason.
Two viable designs:

* **(a) resolve the PR** — `GET /repos/{repo}/commits/{sha}/pulls`, then require
  `01 PR Check` success on that PR's head SHA. No change to `01-pr-check.yml`. Does not
  cover a direct push, which branch protection must cover instead.
* **(b) add `push: branches: [main, master]` to `01-pr-check.yml`** so the tests also run
  on the merge commit, then gate on the deploying SHA directly. Simpler and it covers the
  direct-push hole, but it roughly doubles CI cost on `main` and **`01-pr-check.yml` is
  owned by Stream D and currently carries 170 lines of two other workstreams' unstaged
  work** — it must not be edited from here.

Smallest proposed diff for design (a), inserted after the existing security-gate step
(after line 95), reusing `require_security_scan.py`'s fail-closed shape verbatim:

```yaml
      # Tests must have passed for what is being deployed. 01-pr-check.yml is
      # `on: pull_request` only, so a direct push to main reaches this workflow with no
      # test run anywhere in its history (P11-B08).
      #
      # Fail-closed on the same terms as the security gate: a missing run, an API error,
      # `cancelled` and `skipped` all refuse. "Nobody tested it" is not evidence.
      - name: Require the PR test suite to have passed for this commit
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DEPLOY_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}
          REQUIRED_WORKFLOW: '01 PR Check'
          ALLOW_UNTESTED: ${{ inputs.allow_untested }}
        run: python scripts/require_pr_check.py
```

plus a new `scripts/require_pr_check.py` (PR resolution + the existing script's refusal
ladder) and a `workflow_dispatch` input `allow_untested` mirroring `allow_unscanned`.

**Authorization required before any of this is written:**

| # | Approval | Owner |
|---|---|---|
| A8-1 | Edit `.github/workflows/03-deploy.yml` (+1 step, +1 dispatch input) | Stream E — SRE |
| A8-2 | Add `scripts/require_pr_check.py` | Stream E — SRE |
| A8-3 | Decide design (a) vs (b); (b) additionally needs Stream D's consent to edit `01-pr-check.yml` while their 170-line diff is in flight | Stream D + E |
| A8-4 | Enable branch protection on `main` requiring `01 PR Check` and disallowing direct pushes — a GitHub setting, not a repository change | repo admin |

A8-4 is the half of P11-B08 that no file in this repository can close.

### 3.4 Evidence logs written

`staging/p11b_a8_collect.txt` (full-suite collection), `staging/p11b_a8_drift.txt` (schema
drift). Both new and untracked. No prior report was altered or deleted.

---

## 4. Test commands and accurate results

Every command run from the repository root, Python 3.12.10, pytest 9.1.1,
`configfile: pytest.ini`. Exit codes as observed.

### 4.1 Finding B — the three required orders

| # | Command | Before | After | Exit |
|---|---|---|---|---|
| 1 | `pytest tests/test_api03_rate_limit_storage_is_fail_closed.py -p no:randomly` | 34 passed | **35 passed** | 0 |
| 2 | `pytest tests/test_rate_limiter_fails_closed_in_production.py tests/test_api03_….py -p no:randomly` ← **the previously failing order** | **1 failed**, 51 passed | **53 passed** | 0 |
| 3 | `pytest tests/test_api03_….py tests/test_rate_limit_backend.py tests/test_rate_limiter_….py -p no:randomly` (alphabetical / repo order) | 52 passed | **59 passed** | 0 |
| 4 | `pytest tests/test_rate_limiter_….py tests/test_rate_limit_backend.py tests/test_api03_….py -p no:randomly` (reversed, all three reloaders) | not previously measured | **59 passed** | 0 |

Counts rise by the one added regression test; order dependence is gone in both directions.

### 4.2 Finding A — collection

| # | Command | Result | Exit |
|---|---|---|---|
| 5 | `pytest tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py --collect-only -q -p no:randomly` | `collected 0 items / 1 skipped` | 5 |
| 6 | `pytest tests/ --collect-only -q -p no:randomly` | **14,467 collected in 172.61 s, 0 errors, no `Interrupted`** | 0 |
| 6b | same, re-run as the final check after every edit in §3 | **14,467 collected in 189.75 s, 0 errors, no `Interrupted`** — log `staging/p11b_a8_collect_final.txt` | 0 |

Command 6 satisfies P11-H15's first acceptance criterion. The 14,467 figure differs from the
register's 14,400 and W1B's 14,440 purely by other sessions' churn.

6b additionally confirms the five added node IDs are really in the collected set, not merely
passing when selected directly — verified by name in the log: the four
`test_p11b_a8_collection_integrity.py` functions and
`test_api03_…::test_the_wiring_assertion_is_not_neutralised_by_a_reload`. Grep for `^ERROR`,
`errors during collection` and `Interrupted` over that log returns **nothing**.

### 4.3 New and regression batches

| # | Command | Result | Exit |
|---|---|---|---|
| 7 | `pytest tests/test_p11b_a8_collection_integrity.py -p no:randomly -rfs` | **4 passed in 25.72 s** (incl. the negative control) | 0 |
| 8 | Security batch — 8 files: hs256, placeholder secrets, rate-limit ×3, worker deployment, credential redaction, test-token, + the new file | **158 passed, 1 xfailed** | 0 |
| 9 | `pytest tests/test_p10*.py tests/test_p11b_a8_collection_integrity.py -rfsxX` | **63 passed, 20 skipped** | 0 |
| 10 | `pytest tests/test_api*.py tests/test_rate*.py -p no:randomly` (10 files, all three reloaders) | **285 passed in 188.94 s** | 0 |

The one xfail in #8 is `test_known_unhosted_workers_are_still_unhosted`, **strict and
expected** — `MarketplaceExpiryWorker` and `ReconciliationWorker` remain unhosted. It is not
a pass and not a new failure. The 20 skips in #9 are PostgreSQL-gated
(`AERORA_TEST_DATABASE_URL` unset) plus the one W1B module-level skip. **None of them is
counted as a pass.**

**No full-suite run.** 14,467 tests is many hours; the prior audit called running it blindly
out of bounds and that judgement is unchanged. Collection is clean, so the run is now
*possible* — it has not been *performed*, and nothing here claims otherwise.

### 4.4 Phase 3 — infrastructure evidence

Target confirmed before anything else, read-only:

```
python staging/bootstrap_pg.py verify
→ TARGET: PROVISIONED  public_tables=89
  data_directory=C:/aerora_quant_backend_updated_final1/staging/pgdata_r1   exit 0
```

The authorized isolated loopback instance, listening on 127.0.0.1:51805. **Nothing was
initialized, started, stopped, deleted, renamed or overwritten. `staging/pg_dsn.txt` was
read, never written.**

| Check | Command | Result |
|---|---|---|
| Alembic heads | `alembic heads` (DSN pinned explicitly, not from `.env`) | **`d4e5f6a7b8c9 (head)` — single head.** A3-05 stays closed |
| Applied revision | `alembic current` | **`d4e5f6a7b8c9 (head)`** — the cluster is at head, matching W1A's required value |
| Schema drift | `scripts/verify_schema_drift.py` | **`RESULT: DRIFTED`**, exit 1. Read-only confirmed by the script (`default_transaction_read_only` verified `on` before reading) |

**The drift is a stale baseline, not a tampered database — and the direction matters.**
Every single delta is "NOT IN this repository", i.e. the live cluster has *more* than the
baseline, with **0 missing** in every category: tables 85→89, columns 1135→1193, policies
188→195, functions 36→38, enum types 6→8, recorded migrations 50→56. The 4 extra tables are
`bot_ownership_claims`, `fee_records`, `referral_obligations`, `safety_control_audit`, and
the 6 extra ledger entries are migrations `026`–`031` — **all six of which are present on
disk under `migrations/`, and all six untracked.** The baseline snapshot
`staging/provisioning_snapshot.json` is dated 10-06 20:14 and predates them.
`alembic_version` itself is reported `same`.

The drift checker states the two hypotheses and says to decide which, "because the two have
opposite fixes". **Decided: stale baseline.** The correcting action is to re-baseline via
`scripts/verify_provisioning.py` — which issues `DROP DATABASE` / `CREATE DATABASE` and was
therefore **not run**, and is not this phase's to authorize.

**The PostgreSQL-gated lane (32 files / ~632 tests) was NOT run.** Three independent
reasons, in order of weight:

1. **W1A's own abort criterion forbids it.** `PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md`
   Stage 1: *"Abort if … drift reports hard failures (shape has moved — do not run tests
   against a drifted schema and call it evidence)."* Drift reports hard failures.
2. **The cluster is shared with live concurrent sessions.** W1A-05 records it as shared and
   holding real data; §1.1 above confirms a concurrent session writing *during this phase*.
   The lane writes and some tests commit. W1A requires coordinator approval **A-2** for that
   interference window; this phase does not hold it.
3. **W1A-03 means a pass there would prove nothing about isolation anyway** — the lane
   connects as table owner `postgres` with `FORCE ROW LEVEL SECURITY` on 0 of 89 tables.

**The real-Redis lane was NOT run.** `AERORA_TEST_REDIS_URL` unset; nothing listening on
6379 or 6399; no `staging/redis/redis-server.exe`. No test Redis exists to verify, and
installing one needs separate authorization. The 9 gated tests remain **unexecuted — not
passed.**

---

## 5. Remaining blockers, owners and dependencies

| ID | Blocker | State after A8 | Owner | Dependency |
|---|---|---|---|---|
| **P11-H15 (a)** | Full-suite collection aborts | **CLOSED** by W1B; now **regression-guarded** by `test_p11b_a8_collection_integrity.py` (§3.2) | Stream D / Platform | — |
| **P11-H15 (b)** | Suite is order-dependent | **CLOSED** — fixed and regression-tested, green in 4 orders (§4.1) | Stream D | — |
| **W1B-01** | `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` absent, never committed. 23 test functions / 27 node IDs suspended | **OPEN — unchanged, and deliberately so** | **Phase 10 Workstream I** | §5.1 |
| **W1B-02** | One missing by-path dependency could abort collection repository-wide | **CLOSED** — now an enforced invariant with a negative control | Stream D | — |
| **W1B-04 / W1A-06** | The whole P10H/I/J toolchain and 32 of 38 PG-gated test files are untracked; a second filesystem slip loses them with no recovery path. One realised loss already | **OPEN** — needs a commit, which this phase is not authorized to make | Coordinator | authorization |
| **P11-B08** | No test gate between a push to `main` and production | **OPEN** — reproduced, patch prepared, 4 approvals requested (§3.3). Its stale `P11-D01` dependency is **discharged** | Stream E — SRE (+ Stream D for design (b), repo admin for branch protection) | A8-1…A8-4 |
| **W1A-02** | `staging/dsn.py` has no production host guard | **OPEN** — unchanged, not in scope | Platform | — |
| **W1A-03** | Lane connects as table owner; `FORCE RLS` on 0 of 89 tables | **OPEN** — blocks any TENANT-01 / P11-B09 evidence claim | Security + DB | — |
| **W1A-04** | `alembic env.py` reads `DATABASE_URL` from `.env` unguarded | **OPEN** — worked around here by pinning the DSN explicitly, not fixed | DB owner | — |
| **A8-01 (new)** | `staging/provisioning_snapshot.json` is stale, so the drift checker cannot certify "no drift" and the PG lane cannot be cited as evidence | **OPEN** | DB owner / Coordinator | needs `verify_provisioning.py` authorization (DROP/CREATE DATABASE) |
| **A8-02 (new)** | Register and audit misquote finding B's failing assertion; `P11-D01` is a dangling ID; `02-build.yml` line refs stale | **OPEN** — documentation accuracy | Audit owner | — |
| **PG lane / Redis lane** | 632 + 9 gated tests | **UNEXECUTED — not passed** | Coordinator | A8-01, W1A A-2, A-3 |

### 5.1 W1B-01 was left open on purpose

The brief asks for the collection failure to be resolved "without weakening assertions or
masking missing files". It is resolved, and W1B's guard does neither: all 23 assertions are
byte-identical, the skip reason names both missing files in capitals as NOT a pass, and
`staging/PHASE11B_W1B_BASELINE.md` §5 ledgers the suspended coverage test by test. That is
documentation, not masking — and §3.2 now makes the shape enforceable instead of advisory.

Authoring the two missing modules was considered and rejected, on W1B's reasoning and on
this phase's own constraints:

* There is nothing to restore, only to author — `git log --all`, `git ls-files`, every tree
  in `git rev-list --all`, and `git stash list` are all empty for both paths. Only the
  `.pyc` files survive (12,600 and 4,848 bytes), and bytecode is a build artifact.
* **The only available verifier is the test that demands it.** Code written until that file
  goes green is evidence the code was shaped to the assertions, not evidence that the DSN
  safety contract holds.
* **Decisive here:** both modules write the shared DSN and one calls
  `pgserver.get_server(...)`. Introducing newly authored, server-starting, DSN-writing
  scripts is exactly what this phase is forbidden from doing — it is the Workstream H
  incident class (a wrong cluster silently recorded as "the" instance) in exchange for
  regression coverage on *tooling*.

Nothing is operationally at risk while they are absent: they were the only non-helper
writers of `staging/pg_dsn.txt`, so nothing can mis-record the DSN through them. The gap
becomes live the moment either file is re-authored, and at that point W1B's guard stops
skipping and all 27 node IDs resume with no edit. The shared contract itself —
`bootstrap_pg.write_dsn_if_safe` — is still covered: `test_p10h_bootstrap_pg_selection_is_explicit.py`
passed inside command #9.

---

## 6. Two separate assessments

These are different numbers and must not be collapsed.

### 6.1 Implementation completeness — improved, narrowly

| Item | Before | After |
|---|---|---|
| Full-suite collection | aborts (`Interrupted`) | **14,467 collected, 0 errors** |
| Rate-limit wiring assertion | neutralisable by collection order | **order-independent, 4 orders green** |
| Recurrence prevention for the collection-abort class | an untracked probe and a written recommendation (W1B-02) | **4 enforced tests with positive and negative controls** |
| Regression node IDs added | — | **+5** (1 reload-order, 4 collection-integrity) |
| Alembic | 1 head, state unverified this phase | **1 head, cluster confirmed at `d4e5f6a7b8c9`** |

### 6.2 Evidence-backed readiness — materially unchanged

**No launch-readiness number should move because of this phase.** What changed is CI
integrity: the suite can now be collected and the fail-closed rate-limit assertion can no
longer be silenced by file order. What did **not** change:

* **632 PostgreSQL-gated tests and 9 real-Redis tests remain unexecuted.** Still the
  critical evidence gap. Skips are not passes anywhere in this report.
* **No venue has received an order.** P11-B01…B07, B09, B10 are untouched.
* **Deployment is still ungated by tests** — reproduced, not fixed; 4 approvals outstanding.
* **Schema drift is now *explained* but not *cleared*.** A stale baseline still means the
  drift checker cannot certify the shape, so the PG lane cannot be cited as release
  evidence even once it is approved to run.
* **The whole verification toolchain is still untracked**, so results from it are produced
  by unreviewed code and are not citable as release evidence (W1A-06 / W1B-04).

The audit's 70% implementation / **39% evidence-backed** split stands. The honest reading of
this phase is that it removed two obstacles *to producing evidence* and produced none of the
missing evidence itself. **P11-H15 closed; two CI-integrity items discharged; everything
that governs live-trading readiness is where it was.**

**Live trading: OFF / NOT CERTIFIED.** Paper launch: unchanged. TENANT-01: OPEN.
QUOTA-01: OPEN.

---

## 7. The single highest-value next action

**Grant W1A approval A-2 and authorize a re-baseline, then run the 632-test PostgreSQL lane
— in that order, and after committing the toolchain.**

Why this one, over the alternatives:

* It is the **largest untouched evidence gap on the board.** 632 tests across 32 files have
  *never executed*. Their failure count is genuinely unknown, and the audit puts a 20%
  failure rate at ~5 engineer-days of triage. Every estimate downstream of it is unfounded
  until it runs once.
* **The blockers that used to stand in front of it are gone.** Collection is clean (§4.2),
  Alembic is at a single head and the cluster matches it (§4.4). What remains in the way is
  authorization and a stale snapshot, not code.
* Sequencing matters and is cheap to get wrong: re-baselining calls
  `verify_provisioning.py`, which issues `DROP DATABASE` / `CREATE DATABASE`. Doing that on
  a cluster other sessions are actively using destroys their work. **A-2 (the interference
  window) must be agreed with the concurrent sessions first, and the toolchain committed
  first (W1B-04), or the lane's results are produced by code that can vanish the way
  `probe_pg_find.py` did.**

Not chosen, and why: P11-B08 is 0.5-1 day and genuinely critical, but it is blocked on four
approvals this phase cannot grant, one of which (branch protection) is not a repository
change at all — and the patch as proposed needs a design decision first (§3.3), not more
code. W1B-01 is Workstream I's to own and would, if rushed, re-introduce a DSN-writing
script with no independent verifier.

---

Live trading OFF / NOT CERTIFIED · No production target contacted · No venue contacted ·
Skips are NOT passes · A local PostgreSQL result is NOT production schema verification ·
PostgreSQL lane NOT RUN · real-Redis lane NOT RUN · TENANT-01 OPEN · QUOTA-01 OPEN ·
W1B-01 OPEN (Workstream I) · P11-B08 OPEN pending A8-1…A8-4
