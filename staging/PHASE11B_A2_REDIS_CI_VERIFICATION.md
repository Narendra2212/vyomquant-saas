# PHASE 11B-A2 — D5′ REAL-REDIS CI VERIFICATION AND GATE REVIEW

## Final classification: **BLOCKED**

CI **cannot be run** with the D5′ change. Every D5′-attributable path is uncommitted, the
workflow has **no `workflow_dispatch`** trigger and fires only on `pull_request`, and
committing, pushing or opening a PR are remote state-changing actions this workstream is not
authorized to perform. No run was triggered and no CI result is simulated anywhere below.

**QUOTA-01 remains OPEN.** D9 (atomic lock release), D3 (production over-limit population) and
D2 (database backstop) are untouched. Live trading remains OFF.

**Label key.** **VERIFIED** = established here by a recorded command. **NOT VERIFIED** = not
established; a skip is not a pass. **UNRESOLVED** = investigated and not conclusively
determined.

---

## 1. Baseline and scope

Revalidated at the start of this workstream (`staging/p11b_a2_baseline.txt`), not carried from
the prior report:

```
branch                            main
HEAD                              074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main                       074b772ab16cece62cbfca0f2b78497be2ae56d5
HEAD == origin/main               True
ahead / behind origin/main        0 / 0
git status --short                253 entries
```

### Working-tree state of every D5′-attributable path (**VERIFIED**)

| Path | State | Tracked |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | MODIFIED | yes |
| `tests/real_redis_gate.py` | **UNTRACKED** | no |
| `tests/test_quota01_exchange_slot_is_atomic.py` | **UNTRACKED** | no |
| `tests/test_quota01_metered_reservation_is_hard.py` | **UNTRACKED** | no |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | **UNTRACKED** | no |
| `tests/test_api04_store_failure_after_startup.py` | **UNTRACKED** | no |
| `tests/conftest.py` | MODIFIED (other tabs' work; **not** D5′) | yes |

**7 of 7 paths are absent from every commit on every branch.** GitHub Actions runs committed,
pushed code, so by construction no existing or future run can contain the D5′ change until it
is committed and pushed.

### Scope

This workstream performed **read-only** verification. It changed no workflow, no test, no
application code and no gate logic — only added evidence files under `staging/`. See §8.

---

## 2. Exact D5′ diff review

All six required confirmation points were checked against the **current** file contents.
**21 of 21 static checks passed** (`staging/p11b_a2_diff_review.txt`).

### The attributable diff

`.github/workflows/01-pr-check.yml` — `git diff --numstat` is `170 / 0`, of which **28 lines
are D5′** (one `env:` entry plus its explanatory comment) and **142 are another tab's
`provisioning-and-isolation` job**, which predates D5′ and is preserved intact. The functional
part of the D5′ diff is a single line:

```diff
       - name: Run Unit Tests
         run: pytest tests/ -v --tb=short -k "not chaos and not load" -m "not frontend_bundle"
         env:
           REDIS_URL: redis://localhost:6379
+          AERORA_TEST_REDIS_URL: redis://localhost:6379/15
           ENV: test
           AERORA_MODE: safe
           PYTHONPATH: .
```

`tests/real_redis_gate.py` is new (8,003 bytes). The two quota test files had their local
gate predicates replaced by imports from it and their newly-unused `import os` removed. These
three files are untracked, so git holds no baseline to diff against; their content was reviewed
directly.

### The six confirmation points

| # | Point | Result |
|---|---|---|
| 1 | CI unit-test job provides `AERORA_TEST_REDIS_URL` → its own ephemeral service | **VERIFIED** — `redis://localhost:6379/15`; service `redis:7-alpine`, ports `['6379:6379']`, i.e. the job's own container, not a managed endpoint |
| 2 | Gate reads the deliberate test-only variable, verifies reachability, fails when unavailable | **VERIFIED** — `OPT_IN_VAR = "AERORA_TEST_REDIS_URL"`; reachability by real `.ping()`; `pytest.fail(GATE_REASON)` on a configured-but-dead endpoint; `skip`/`fail`/`run` are distinct |
| 3 | Non-loopback denied unless the explicit remote opt-in is set | **VERIFIED** — `ALLOW_REMOTE_VAR = "AERORA_TEST_REDIS_ALLOW_REMOTE"`, enforced against `_LOOPBACK_HOSTS` |
| 4 | No production `REDIS_URL` fallback reintroduced | **VERIFIED** by AST, not by text search: zero code-level string constants `"REDIS_URL"` in the gate or either quota file (remaining occurrences are prose in docstrings) |
| 5 | CI service health check and concurrent workflow changes intact | **VERIFIED** — `--health-cmd "redis-cli ping" --health-interval 10s --health-timeout 5s --health-retries 5` retained; `provisioning-and-isolation` still present; 8 jobs; `REDIS_URL` retained on the step so unrelated lanes are unchanged |
| 6 | Tests do not flush shared Redis databases or rely on unsafe global cleanup | **VERIFIED** — no `FLUSHDB`/`FLUSHALL` in the gate or any of the three test files; keys are namespaced `q01-slot-*` / `q01-real-*`; clients closed in `finally` |

No file was edited to make the diff look cleaner.

---

## 3. Actual CI run identity and overall job result

### No run contains D5′ (**VERIFIED**)

`staging/p11b_a2_ci_history.txt`, from read-only `gh run list` / `gh run view`:

| Created | Conclusion | Head SHA | Run ID | Event |
|---|---|---|---|---|
| 2026-10-05T17:13:50 | **failure** | `6c5660a1` | 37346853502 | pull_request |
| 2026-10-05T16:47:41 | failure | `56297a57` | 37343548204 | pull_request |
| 2026-10-05T16:42:38 | failure | `979eb35f` | 37342904270 | pull_request |
| 2026-10-05T16:32:49 | failure | `630a33c5` | 37341664120 | pull_request |
| 2026-10-05T15:59:01 | failure | `1dfba58c` | 37337211390 | pull_request |
| 2026-10-05T15:52:06 | failure | `e7816f4f` | 37336280698 | pull_request |
| 2026-10-05T15:48:28 | failure | `0264450c` | 37335793537 | pull_request |
| 2026-10-05T14:38:53 | failure | `90318820` | 37326385750 | pull_request |
| 2026-10-05T11:39:12 | failure | `f1376280` | 37304309325 | pull_request |
| 2026-09-26T19:39:00 | failure | `d8ca1794` | 36266778219 | pull_request |

**0 of the last 10 runs succeeded.** Local `HEAD` (`074b772a`) appears in none of them.

### Most recent run — job breakdown (run `37346853502`, SHA `6c5660a1`, outcome **failure**)

| Job | Conclusion |
|---|---|
| Code Quality & Static Audit | success |
| Format Backlog (report only) | success |
| Frontend Build & Unit Tests | success |
| **Unit & Integration Tests** | **failure** ← the job D5′ modifies |
| Execution Tests (production posture) | success |
| Docker Build Validation | success |
| Database Tests (PostgreSQL) | success |

### Two findings that matter more than the blocker

**(a) The CI Redis service does become healthy, and before the tests start.** From the job log
(`staging/p11b_a2_unit_job_failure.txt`):

```
17:17:25  Starting redis service container
17:17:25  /usr/bin/docker pull redis:7-alpine
          ... --health-cmd "redis-cli ping" --health-interval 10s --health-timeout 5s --health-retries 5
17:17:29  redis service is starting, waiting 2 seconds before checking again.
17:17:31  redis service is starting, waiting 3 seconds before checking again.
17:17:35  redis service is starting, waiting 8 seconds before checking again.
17:17:43  redis service is healthy.
```

The `Run Unit Tests` step begins after that. So **D5′'s premise is sound**: the endpoint the new
variable points at genuinely exists, comes up, and is health-gated. This is the strongest
evidence available short of an actual run, and it is **not** a substitute for one.

**(b) The host job is already red for reasons unrelated to D5′.** **39 `FAILED` lines**, the
first actionable ones being:

```
tests/property/test_tenant_isolation_matrix.py::test_the_tenant_isolation_matrix_refuses_every_foreign_identifier FAILED
tests/test_ml_deployment_guards.py::TestMLDeploymentGuards::test_the_legacy_connection_layer_training_route_is_retired FAILED
tests/test_no_credential_in_api_response_body.py::... FAILED   (x ~13)
E   AssertionError: Requirement 21.8: 4 of the 150 attempted cells failed one of the three assertions.
```

So even once D5′ is pushed, the `Unit & Integration Tests` job will still be red, and a green
job cannot be the acceptance signal. **Acceptance must be per-test**, read off the log for the
three quota tests and six webhook tests specifically.

### Why no run could be triggered (**VERIFIED**)

| Factor | Finding |
|---|---|
| Remote | `https://github.com/Narendra2212/vyomquant-saas.git` |
| `gh` CLI | **present and AUTHENTICATED** (`gh auth status` exit 0, account `Narendra2212`, keyring) |
| `credential.helper` | `manager` |
| Workflow triggers | `{'pull_request': {'branches': ['main','master'], 'types': ['opened','synchronize','reopened']}}` — **no `workflow_dispatch`** |

The blocker is **authorization, not capability.** The tooling to push and open a PR is present
and working; this workstream is instructed not to use it. Stated plainly so the coordinator is
not told something is impossible when it is merely not permitted.

### Exact instructions to close the gate

Requires coordinator authorization for a commit, a push and a PR.

```bash
# 1. a branch, so main is untouched
git checkout -b ci/d5-prime-real-redis-gate

# 2. stage ONLY the D5'-attributable paths. The tree holds ~253 entries of
#    concurrent work; `git add -A` would sweep it in.
git add .github/workflows/01-pr-check.yml \
        tests/real_redis_gate.py \
        tests/test_quota01_exchange_slot_is_atomic.py \
        tests/test_quota01_metered_reservation_is_hard.py \
        tests/test_quota01_metered_reservation_is_hard.py
#    NOTE: tests/test_webhook_duplicate_delivery_real_redis.py is also untracked and
#    MUST be included for its six tests to run at all -- see §4.
git add tests/test_webhook_duplicate_delivery_real_redis.py

# 3. commit and push
git commit -m "Enable the real-Redis gates against CI's existing redis service (D5')"
git push -u origin ci/d5-prime-real-redis-gate

# 4. open the PR -- 01-pr-check.yml fires only on pull_request
gh pr create --base main --title "D5': enable real-Redis gates in CI" --body "..."

# 5. read the per-test result off the Unit & Integration Tests job
gh run list --workflow 01-pr-check.yml --limit 1
gh run view <RUN_ID> --log --job <JOB_ID> > run.log
grep -E "real_redis|quota01_exchange_slot|quota01_metered|webhook_duplicate_delivery" run.log
```

---

## 4. Per-test evidence

### The three gated quota tests — **NOT VERIFIED**

| Test | Collected in CI | Executed | Result |
|---|---|---|---|
| `test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` | **No** | No | **NOT VERIFIED** |
| `test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` | **No** | No | **NOT VERIFIED** |
| `test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` | **No** | No | **NOT VERIFIED** |

Corroborated two independent ways: the files are untracked (§1), and the most recent CI job log
contains **0 mentions** of `quota01_exchange_slot`, `quota01_metered`, `real_redis` or
`AERORA_TEST_REDIS_URL`.

**Whether the configured Redis URL was consumed by the gate: NOT VERIFIED** — the variable has
never been present in any CI run. **Whether the tests reached their Redis assertions: NOT
VERIFIED.**

### The six webhook tests — **NOT VERIFIED**

`tests/test_webhook_duplicate_delivery_real_redis.py` is also **untracked**, and the CI log
contains **0 mentions** of `webhook_duplicate_delivery`. All six are enumerated here so the
coordinator has the acceptance checklist:

| # | Test |
|---|---|
| 1 | `test_the_credential_gate_refuses_test_keys` |
| 2 | `test_a_redelivered_webhook_is_ignored` |
| 3 | `test_the_lock_is_recorded_in_redis_under_the_documented_key` |
| 4 | `test_a_different_event_id_is_not_deduplicated` |
| 5 | `test_an_in_flight_delivery_blocks_a_concurrent_one` |
| 6 | `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` |

Each is **NOT VERIFIED** in CI: not collected, not executed, no result, no cleanup observed.

This corrects an assumption in the D5′ report, which treated the webhook file as something that
"will also begin running" once the env var lands. It will not — **it has to be committed too**,
and it is currently untracked. That is now step 2 of the §3 instructions.

#### The gate inconsistency, documented and deliberately not changed

| Condition | Quota gates (`tests/real_redis_gate.py`) | Webhook file (its own `_redis_reachable()`) |
|---|---|---|
| opt-in unset | skip | skip |
| configured, reachable | run | run |
| **configured, unreachable** | **FAIL** | **SKIP** |

Both read `AERORA_TEST_REDIS_URL` only, and both PING — so points 2 and 4 hold for the webhook
file as well (**VERIFIED**). The divergence is the unreachable case: a dead CI Redis would make
the six webhook tests report as skips, i.e. a broken service would look like "nobody asked".

**Proposed separate, narrowly scoped correction — NOT performed here:** replace the webhook
file's module-level `pytest.mark.skipif(not _redis_reachable(), …)` with the shared
`real_redis_required` marker plus a `require_real_redis()` call, deleting its local
`_redis_reachable()`. That is a ~6-line change to one file outside this workstream's ownership
and needs its owner's approval. Webhook semantics would be untouched.

### Quota regression suite — **VERIFIED locally, NOT in CI**

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       tests/test_quota01_enforcement_is_wired.py \
       tests/test_quota01_hard_enforcer_is_deprecated_in_code.py \
       -p no:randomly -q --no-header -p no:warnings --timeout=300

-> 50 passed, 3 skipped in 71.83s        EXIT=0
```

The 3 skips are the real-Redis gates (opt-in unset locally). **A skip is not a pass.** These
suites have never run in CI either, because three of the four files are untracked.

---

## 5. API04 failure — ownership and status

`tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated`

| Question | Answer |
|---|---|
| Selected in the actual CI job? | **No.** The file is untracked, and the CI log has 0 mentions of `api04_store_failure` |
| Would the selector pick it up if committed? | **Yes** — neither `chaos` nor `load` appears in its node id |
| Does it block CI today? | **No** — it is not in CI |
| D5′-attributable? | **No** (**VERIFIED**) |
| Exact local trigger | **UNRESOLVED** |

### Attribution evidence (not assertion)

* **Airtight D5′ exclusion.** `tests/real_redis_gate.py` was moved aside and the test re-run:
  **still `1 failed`, exit 1**, then the helper was restored
  (`staging/d5prime_api04_without_helper.txt`). The failure does not depend on anything D5′
  wrote. D5′ also changed nothing at all this workstream.
* **The raise is in committed code.** `backend_app/core/database_pool.py` is `clean` per
  `git status --porcelain`. Lines 137-142:
  ```python
  _env_mode = get_vyomquant_mode("safe").lower()
  DATABASE_URL = os.getenv("DATABASE_URL")
  if not DATABASE_URL:
      if _env_mode in ["paper", "live"]:
          raise RuntimeError("DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.")
      DATABASE_URL = "sqlite:///./algo22.db"
  ```
* **The concurrent changes are not implicated by the evidence.**
  `backend_app/core/safety_config.py` (`119/12`): its diff touches mode resolution only in
  **docstring prose** — no behavioural line. `backend_app/routers/billing.py` (`700/40`):
  **zero** `limiter.limit` lines changed, and the failure is an *import* error rather than the
  assertion that test makes. Per instruction, neither is claimed responsible.
* **The exact trigger is UNRESOLVED, and I am saying so rather than guessing a third time.**
  Two hypotheses were tested and both failed: (i) deleting `VYOMQUANT_MODE` (the module's
  autouse `clean_env` fixture does delete it, **confirmed** `autouse=True`) does **not** by
  itself produce the raise; (ii) a paper/live mode in the local `.env` is not the cause — `.env`
  declares **no** `VYOMQUANT_MODE` or `AERORA_MODE` key. Reproducing the test's exact
  environment in a subprocess resolved `get_vyomquant_mode('safe') -> 'safe'` and imported
  `database_pool` **OK** (`staging/p11b_a2_api04_mode.txt`). So something in the pytest process
  differs from that reconstruction and has not been identified.

### Ownership and approval needed

Owner: whoever owns `tests/test_api04_store_failure_after_startup.py` (an untracked artefact of
an earlier phase) together with the `core/safety_config.py` mode resolver. Required: a separate
investigation of why `database_pool`'s module-level mode check resolves to paper/live inside a
pytest session when it resolves to `safe` outside one.

**No database fallback was introduced, no concurrent change was modified, and no assertion was
weakened to make it green.** It is recorded as a local-only red test that will enter CI the
moment the file is committed.

---

## 6. Failures, skips, and their reasons

| Item | Count | Reason |
|---|---|---|
| D5′ real-Redis tests executed in CI | **0** | Files uncommitted; no run contains them |
| Quota real-Redis tests skipped locally | 3 | `AERORA_TEST_REDIS_URL` unset — the one legitimate skip; reason text states "A skip here is NOT a pass" |
| Webhook real-Redis tests executed in CI | **0** | File uncommitted |
| Quota regression locally | 50 passed, 3 skipped | Pass; the 3 skips are the gates above |
| CI `Unit & Integration Tests` | **39 FAILED** | **Pre-existing, unrelated to D5′** — tenant-isolation matrix, ML deployment guards, ~13 credential-response-body tests, Requirement 21.8 (4 of 150 cells) |
| API04 locally | 1 failed | Local-only; trigger UNRESOLVED; not in CI; not D5′ |

The red host job is reported here rather than hidden: it is unrelated to D5′, and it means a
green job can never be D5′'s acceptance signal.

---

## 7. Safety and concurrent-work preservation

| Statement | Status |
|---|---|
| **Live trading OFF** | **VERIFIED** — no `LIVE*`/`TRADING*` variable set; no execution or exchange code ran |
| No production Redis, production database, exchange credential or live endpoint accessed | **VERIFIED** — no Redis was contacted at all this workstream; the only remote calls were read-only `gh run list` / `gh run view` against CI metadata |
| No remote state changed | **VERIFIED** — no commit, push, merge, PR, dispatch or re-run. `HEAD` still `074b772a`, `HEAD == origin/main`, 0 ahead |
| No local infrastructure or dependency installed | **VERIFIED** |
| `tests/conftest.py` not modified | **VERIFIED** — its ` M` status is other tabs' work; this workstream wrote nothing to it |
| No application code modified | **VERIFIED** — nothing outside `staging/` was written this workstream |
| D5′ implementation unchanged this workstream | **VERIFIED** — workflow numstat still `170 / 0`; `tests/real_redis_gate.py` still 8,003 bytes |
| No assertion weakened, no failure converted to a skip, no timeout raised | **VERIFIED** |
| Launch scorecard / finding registry untouched | **VERIFIED** — no write issued to `staging/FINAL_LAUNCH_SCORECARD.md` or `staging/full_audit_results.json` |
| Concurrent work preserved | **VERIFIED** — 253 tree entries, nothing removed; the workflow still carries another tab's full `provisioning-and-isolation` job |
| No credential or token printed | **VERIFIED** — probes redact userinfo, withhold token lines from `gh auth status`, and report `.env` mode keys only (never `DATABASE_URL`'s value) |

---

## 8. Files changed by this workstream

Evidence and probes only — **no implementation, workflow, test or application file was
touched.**

```
new:  staging/PHASE11B_A2_REDIS_CI_VERIFICATION.md        (this document)
new:  staging/probe_p11b_a2_baseline.py          + p11b_a2_baseline.txt
new:  staging/probe_p11b_a2_ci_history.py        + p11b_a2_ci_history.txt
new:  staging/probe_p11b_a2_unit_job_failure.py  + p11b_a2_unit_job_failure.txt
new:  staging/probe_p11b_a2_diff_review.py       + p11b_a2_diff_review.txt
new:  staging/probe_p11b_a2_api04_mechanism.py   + p11b_a2_api04_mechanism.txt
new:  staging/probe_p11b_a2_api04_mode.py        + p11b_a2_api04_mode.txt
new:  staging/p11b_a2_gh_run_list.json, staging/p11b_a2_gh_last_run.json
```

---

## 9. Classification

```
D5': BLOCKED
```

Against §6's rules: not **VERIFIED**, because no real-Redis test executed against the CI service
and none passed there. Not **PARTIAL**, because that requires CI to have run with the change —
it has not, and cannot until the paths are committed. **BLOCKED** is the correct class: CI
cannot be run under the available authorization.

What the evidence *does* support, stated without inflation:

* the CI Redis service exists, starts, and is **health-gated before the test step** (**VERIFIED**
  from a real job log);
* the gate's six required properties hold in the current code (**21/21 static checks**);
* the quota regression passes locally with the gate closed (50 passed, 3 skipped).

None of that is real-Redis verification, and this document does not claim it is.

---

## 10. The smallest justified next action

**Request authorization for one commit, one push and one PR**, confined to the six
D5′-attributable paths listed in §3 step 2 — explicitly **including**
`tests/test_webhook_duplicate_delivery_real_redis.py`, which §4 shows is untracked and would
otherwise still not run.

Then read acceptance **per test**, not from the job's colour, because the host job carries 39
pre-existing failures:

```
gh run view <RUN_ID> --log --job <JOB_ID> \
  | grep -E "real_redis|quota01_exchange_slot|quota01_metered|webhook_duplicate_delivery"
```

Acceptance requires all three quota real-Redis tests to **execute and pass**, and all six
webhook tests to be **accounted for**.

Two items to decide alongside, neither of which should expand this patch:

1. the webhook gate's skip-on-unreachable inconsistency (§4) — a separate ~6-line change needing
   its owner's approval;
2. the 39 pre-existing `Unit & Integration Tests` failures, which are an independent blocker on
   that lane and are owned elsewhere.

**Do not close QUOTA-01 on the strength of a D5′ verification.** D9, D3, D2 and the remaining
quota-enforcement gaps are separate decisions.
