# Phase 11B-A5 / A6 — Real-Redis Fixture Correction, D5′ Commit, PR, and CI Result

**Date:** 2026-10-10
**Scope:** (A5) correct the real-Redis webhook fixture so a configured real-Redis test cannot
silently skip; (A6) execute the previously authorized D5′ branch / commit / push / pull-request
workflow against the exact approved five-file manifest, let CI run, and report the evidence.

---

## VERDICT

| Question | Answer |
|---|---|
| A5 correction applied as specified? | **YES** |
| D5′ mechanism — do the real-Redis tests now actually run against a real server? | **VERIFIED** |
| Silent-skip defect eliminated? | **YES** — 9/9 required tests executed, **0 skipped** |
| Redis integration — do all required tests *pass*? | **FAILED** (2 of 3 required quota Redis tests failed; 2 of 6 webhook tests failed) |
| Overall CI job | **failure** — 62 failed / 12 029 passed / 21 skipped / 1 xfailed |
| PR merged? | **NO** — PR #7 left OPEN, as instructed |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

The two classifications are deliberately separate. The *mechanism* D5′ exists to fix — a healthy
`redis:7-alpine` service that the tests could never reach, so they skipped and the run stayed green —
is demonstrably fixed and proven by log evidence. The *integration* is **not** verified, because the
acceptance rule requires all three quota real-Redis tests to pass, and two of them failed.

**Honest limitation up front:** the two failing quota real-Redis tests sit inside a file that fails
**21 of its 22 tests** in CI — on its **first-ever CI execution** — while passing completely locally.
Their failure therefore **cannot be attributed to real-Redis behaviour** without tracebacks, and
GitHub truncated the pytest `FAILURES` block, so the tracebacks are not retrievable from this run.
See §8.

---

## 1. Baseline and ownership (re-verified before and after)

Per the mandatory rule, the Git state was re-measured rather than taken from the A3/A4 reports.
Every value matched.

| Measurement | Before A5 | After A6 push | Command |
|---|---|---|---|
| Checked-out branch | `main` | `main` | `git status --branch` |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **unchanged** | `git rev-parse HEAD` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged | `git rev-parse origin/main` |
| Ahead / behind | 0 / 0 | 0 / 0 | `git status --branch` |
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` | **identical** | `Get-FileHash -Algorithm SHA256 .git/index` |
| Staged entries in shared index | 22 | **22** | `git diff --cached --name-only` |
| `ci/d5-prime-real-redis` exists | no (local **and** remote) | yes, at `49e70da2…` | `git rev-parse`, `git ls-remote` |

Working-tree churn from other agent tabs, measured at report time (counts drift continuously
because other workstreams are active — the command is given so the figure is reproducible, not
because the figure is stable):

- `git diff --name-only` → **100** unstaged-modified tracked files
- `git status --porcelain | ? { $_ -match '^(.M|M.)' }` → **108** entries touching a tracked file
- `git status --porcelain | ? { $_ -match '^\?\?' }` → **134** untracked entries (directory-collapsed)

**Read-only files, owned by other concurrent workstreams — not touched:**

| File | Status | Why off-limits |
|---|---|---|
| `backend_app/core/cache/redis_manager.py` | ` M` | Concurrently modified; its fail-soft `_get_active_cache()` contract is owned elsewhere |
| `tests/conftest.py` | ` M` | Concurrently modified; explicitly excluded by the brief |
| `.github/workflows/01-pr-check.yml` hunk 2 | ` M` | `provisioning-and-isolation` job belongs to another workstream |
| any launch register | — | Explicitly excluded by the brief |

---

## 2. The A5 source change (exact diff)

**One file, one statement.** `tests/test_webhook_duplicate_delivery_real_redis.py`, inside the
`real_redis_cache` fixture, at the existing guard (now line 187 for the predicate, the disposition
at line 216).

```diff
--- a/tests/test_webhook_duplicate_delivery_real_redis.py
+++ b/tests/test_webhook_duplicate_delivery_real_redis.py
     cache = await redis_manager._get_active_cache()
     if cache is None or isinstance(cache, MockRedisClient):
-        pytest.skip(
-            f"redis_manager still resolves to {type(cache).__name__}; cannot exercise "
-            f"the lock against the real server, and the in-memory dict would prove "
-            f"nothing (see BUG-027)."
-        )
+        # FAIL, NOT SKIP (Phase 11B-A5).
+        #
+        # This was `pytest.skip(...)`, and by the time this line is reached a skip is the
+        # wrong answer. Two things are already established:
+        #
+        #   * `real_redis_required` (pytestmark) has confirmed AERORA_TEST_REDIS_URL IS
+        #     SET -- otherwise collection would have skipped and this fixture would never
+        #     run;
+        #   * `_real_redis_is_usable` (autouse, above) has confirmed the endpoint ANSWERS
+        #     PING, from a permitted host.
+        #
+        # So reaching here means: a real Redis was asked for, the shared gate reached it,
+        # and the APPLICATION cache manager still did not hand back a real cache. That is
+        # a wiring or configuration fault, not an absent server -- and the two quota files
+        # sharing this gate would FAIL on the same broken service, so reporting it as a
+        # skip here is the same false-assurance asymmetry D5' exists to remove. A required
+        # real-Redis assertion may not be satisfied by a skip.
+        #
+        # `_get_active_cache()` reaches this state via one of two branches
+        # (backend_app/core/cache/redis_manager.py:727-745), both measured in
+        # staging/probe_p11b4_cache_fallback.py:
+        #   * no `.cache` from `_ensure_manager()` and `_mock_cache_is_acceptable()` true
+        #     -> MockRedisClient  (what ENV=test produces)
+        #   * the same with the predicate false -> None
+        #
+        # The DETECTION below is unchanged and was already correct; only the disposition
+        # moves. No application code is touched: the manager's fail-soft behaviour is
+        # deliberate and is owned elsewhere.
+        pytest.fail(
+            f"the shared real-Redis gate PASSED its PING, but redis_manager resolved to "
+            f"{type(cache).__name__} instead of a real cache. AERORA_TEST_REDIS_URL is "
+            f"set and the endpoint answered, so this is a cache-manager wiring fault, not "
+            f"a missing server -- it FAILS rather than skipping, because the in-memory "
+            f"dict would prove nothing (see BUG-027) and a skip here would hide a broken "
+            f"service behind a green run. Check DEV_MODE / ENV, whether "
+            f"`_bypass_dev_mode(True)` took effect, and whether the manager could open "
+            f"its own connection to the same endpoint the gate just reached."
+        )
     return redis_manager
```

Conformance to the three Phase-1 constraints:

| Instruction | Compliance |
|---|---|
| Replace `pytest.skip(...)` with `pytest.fail(...)` | Done — the call is the only executable change |
| Error message explains that the shared real-Redis gate already passed its PING check but the application cache manager did not provide a real cache | Done — the message opens with exactly that, and names the resolved type |
| Preserve the existing detection logic | `if cache is None or isinstance(cache, MockRedisClient):` is **byte-identical**; `_get_active_cache()`, `_bypass_dev_mode(True)` and the `REDIS_URL` restore are untouched |
| Change no other application code or fixtures | No other file modified. `redis_manager.py`, `conftest.py`, `01-pr-check.yml`, launch registers: untouched by A5 |

### Alternatives considered and rejected

| Option | Rejected because |
|---|---|
| Edit `_get_active_cache()` in `redis_manager.py` to refuse the mock | File is concurrently modified by another workstream, and it would change fail-soft behaviour for **every** caller, not just tests |
| Add a "require real cache" flag to the manager | Broadens the manager's public contract for a test-only concern |
| Drop the `MockRedisClient` check entirely | An in-memory dict would then satisfy the tests — exactly the BUG-027 false assurance D5′ exists to remove |

---

## 3. Verification before committing (local)

All runs used the private-index procedure's sibling discipline: bounded invocations, no
infrastructure installed, no production endpoint contacted. The forcing harness
`staging/p11b5_force_gate.py` is a pytest plugin that patches `redis.Redis.ping` → `True` and
`redis_manager._get_active_cache`, so branch (C)/(D) could be reached **without** a Redis server.

| # | Condition | Result | Reads as |
|---|---|---|---|
| 1 | `AERORA_TEST_REDIS_URL` unset | 6 skipped, exit 0 | Correct — opt-out stays a skip |
| 2 | Dead loopback `redis://127.0.0.1:6399/15` | 6 errors, exit 1 | Correct — gate PING fails, reported loudly |
| 3a | Gate PING forced true, manager → `MockRedisClient` | 1 passed, **5 errors, 0 skipped**, exit 1 | **The A5 fix, proven.** Before the change this was a silent skip |
| 3b | Gate PING forced true, manager → `None` | errors, exit 1 | Same disposition for the `None` branch |
| 4 | Non-loopback `redis://redis.example.internal:6379/15` without `AERORA_TEST_REDIS_ALLOW_REMOTE=1` | errors; host **not** echoed | Remote guard intact, no host leak |
| 5 | All three D5′ files together, local | **26 passed, 9 skipped, 0 failed** | Clean locally — contrast this with §7 |

Note on reporting form: because the guard lives in a fixture, a raised failure surfaces as pytest
`ERROR`, not `FAILED`. That trade-off was accepted in A3 and is unchanged — the point is that it is
no longer a **skip**. Logs: `staging/p11b5_case1_unset.txt`, `p11b5_case2_dead.txt`,
`p11b5_case3_mock.txt`, `p11b5_case3_none.txt`, `p11b5_case4_all.txt`.

---

## 4. Manifest and private-index verification

The brief's suggested `git add` + `git diff --cached` procedure was **not** used: the shared
`.git/index` already held 22 staged files from other workstreams, and `git add` would have folded
them into the commit. `git checkout -b` was also avoided, because it would move HEAD against a
dirty tree of ~100 modified tracked files. The commit was assembled entirely with plumbing —
`git write-tree` / `commit-tree` / `update-ref` against a private index file — so the shared index
and the checked-out branch were never written.

Approved five-file manifest, as committed:

| File | Lines | Note |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | +27 | **Hunk 1 only** (`@@ -183,10 +183,37 @@`), which carries `AERORA_TEST_REDIS_URL` |
| `tests/real_redis_gate.py` | +187 | Shared gate |
| `tests/test_quota01_exchange_slot_is_atomic.py` | +867 | |
| `tests/test_quota01_metered_reservation_is_hard.py` | +264 | |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | +416 | Contains the A5 fix |
| **Total** | **5 files, +1 761, −0** | |

- Hunk 2 of the workflow (`@@ -465,6 +492,149 @@`, +143, the `provisioning-and-isolation` job) was
  **excluded** — it belongs to another workstream. Because the change set contains **zero
  deletions anywhere**, omitting it cannot remove any of their lines.
- `git apply --check --whitespace=error` → **clean**.
- Checked-out branch after commit: still `main`. HEAD after commit: still `074b772a…`.
- Shared index SHA-256 after push: `6d965c8e…` — **byte-identical** to before; still 22 entries.
- Working-tree copies of the four test files are **byte-identical** to the committed blobs
  (`git hash-object` vs `git ls-tree -r 49e70da2`): `d40a1bb7…`, `2d7ccbbb…`, `f48103ac…`,
  `f74a0a17…` — all four match.

> Measurement caveat worth recording: `git diff --stat 49e70da2 -- tests/…` reports the four test
> files as **deleted**. That is an artifact, not a loss. The files are untracked relative to the
> shared index, and `git diff <commit>` only consults tracked paths. The blob-hash comparison above
> is the authoritative check, and it shows the files present and identical.

Artifacts: `staging/p11b5_manifest.txt`, `staging/p11b5_staged_before.txt`,
`staging/verify_p11b3_commit_manifest.py`, `staging/execute_p11b6_d5_pr.py`.

---

## 5. Commit, push, PR

| Item | Value |
|---|---|
| Commit | `49e70da23c2858ffc9a6f34d5db4521401477c82` |
| Tree | `271f13c29d3c09a205dbaae959cad002cc0fd0a6` |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (= `origin/main`) |
| Subject | `D5': point the real-Redis tests at the CI redis service` |
| Author | `Narendra2212 <nt3441449@gmail.com>` |
| Date | Sat Oct 10 15:42:41 2026 +0530 |
| Branch | `ci/d5-prime-real-redis` (new, as named in the brief) |
| Remote | `https://github.com/Narendra2212/vyomquant-saas.git` |
| **Pull request** | **https://github.com/Narendra2212/vyomquant-saas/pull/7** |
| PR state | `OPEN`, `mergedAt: null`, base `main`, head `49e70da2…`, 5 files, +1761 / −0 |

Single-parent commit on top of `origin/main`, confirmed. **PR not merged.**

---

## 6. CI run identifiers

| Item | Value |
|---|---|
| Run ID | **38044138718** |
| URL | https://github.com/Narendra2212/vyomquant-saas/actions/runs/38044138718 |
| Head SHA | `49e70da23c2858ffc9a6f34d5db4521401477c82` |
| Branch | `ci/d5-prime-real-redis` |
| Conclusion | **failure** |

| Job | ID | Conclusion |
|---|---|---|
| Code Quality & Static Audit | 114190137881 | success |
| Frontend Build & Unit Tests | 114190138024 | success |
| Format Backlog | 114190138078 | success |
| Execution Tests (production posture) | 114190594386 | success |
| Docker Build Validation | 114190594432 | success |
| Database Tests (PostgreSQL) | 114190594435 | success |
| **Unit & Integration Tests** | **114190594445** | **failure** |

`provisioning-and-isolation` is **absent** from the job list — independent confirmation that
workflow hunk 2 was correctly excluded from the commit.

### Redis service and gate wiring, from the job log

```
##[command]/usr/bin/docker pull redis:7-alpine
Status: Downloaded newer image for redis:7-alpine
redis service is healthy.
  AERORA_TEST_REDIS_URL: redis://localhost:6379/15
```

- Redis **7.4.11**, `Ready to accept connections tcp`.
- Background RDB saves logged at **10:21:38** and **10:26:39** — the tests performed **real writes**
  against the server. An in-memory mock produces no RDB activity, so this is positive proof the
  traffic reached the real service, not a dict.

Log: `staging/p11b6_unit_log.txt` (5.5 MB). **Encoding gotcha:** PowerShell `>` redirection wrote it
as UTF-16LE with a BOM. The first extractor read it as UTF-8 and falsely reported "0/9 executed".
All extractors now sniff the BOM
(`_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"`). Anyone re-reading these
logs must do the same.

---

## 7. Per-test Redis evidence — every required test individually accounted for

### The three required quota real-Redis tests

| Test | CI outcome |
|---|---|
| `tests/test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` | **FAILED** |
| `tests/test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` | **FAILED** |
| `tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` | **PASSED** |

### The six webhook tests

| Test | CI outcome |
|---|---|
| `test_the_credential_gate_refuses_test_keys` | PASSED |
| `test_a_redelivered_webhook_is_ignored` | PASSED |
| `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **FAILED** |
| `test_a_different_event_id_is_not_deduplicated` | PASSED |
| `test_an_in_flight_delivery_blocks_a_concurrent_one` | **FAILED** |
| `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | PASSED |

### Skip accounting

**Executed: 9/9. Skipped: 0/9.**

The run's 21 skips were enumerated individually and **none** is in any of the three D5′ files. They
are: `test_bandit_sast_gate_is_green.py` (1), `test_lifecycle_integration.py` (13),
`test_marketplace_subscription_period.py` (1), `test_ml_deployment_guards.py` (2),
`test_node_preview_endpoint.py` (1), `test_release_artifacts.py` (1), `test_risk_settings_api.py` (2).

This is the core D5′ claim, and it holds: a healthy Redis service is now reachable by the tests that
exist to exercise it, and not one of them silently opted out. **Mechanism: VERIFIED.**

### Per-file outcomes in CI

| File | Total | Passed | Failed |
|---|---|---|---|
| `tests/test_quota01_exchange_slot_is_atomic.py` | 22 | 1 | **21** |
| `tests/test_quota01_metered_reservation_is_hard.py` | 7 | 7 | 0 |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | 6 | 4 | 2 |

Artifacts: `staging/p11b6_evidence.txt`, `staging/p11b6_perfile.txt`, `staging/p11b6_runs.json`.

---

## 8. What the four failures do and do not tell us

### `test_quota01_exchange_slot_is_atomic.py` — 21 of 22 failed, and that changes the reading

The two failing quota real-Redis tests are **not isolated**. Nineteen **non-Redis** tests in the same
file failed alongside them, including `test_eight_concurrent_stores_admit_exactly_the_limit`,
`test_the_lock_is_released_when_the_guarded_write_raises`,
`test_store_keys_uses_the_guard_around_its_vault_write`, and
`test_an_unreachable_lock_store_refuses_rather_than_admits`. Only one test in the file passed.

Three facts constrain the interpretation:

1. This was the file's **first-ever CI execution** — it was untracked before commit `49e70da2`.
2. The same file passes **completely** locally (§3 case 5: 26 passed, 0 failed across all three files).
3. The 19 failures do not touch Redis at all, so whatever breaks them is **not** real-Redis behaviour.

The most economical hypothesis is a single environment-level cause affecting the whole file in CI,
which would also account for the 2 real-Redis tests. **That is a hypothesis, not a finding.** It
cannot be confirmed or refuted without tracebacks. Specifically: **it would be wrong to report that
"real Redis exposed a defect in the quota slot guard"** on this evidence.

### Tracebacks are not retrievable from this run

GitHub truncated the pytest tail. In `staging/p11b6_logfailed.txt` the `= FAILURES =` block begins at
line 12279 of 12324 and contains exactly **one** traceback header — for
`test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`, which is unrelated. Each of the
four D5′ test names appears exactly **once** in the log, on its per-test result line. The
`short test summary info` section yields **zero** hits.

Local reproduction is also unavailable: there is no Redis server on this machine
(`staging/redis/redis-server.exe` absent, nothing listening on 6379), and installing or starting
infrastructure is outside the authorized scope.

**Therefore the root cause of all four failures is undetermined, and no speculation is offered here.**

Artifacts: `staging/p11b6_tracebacks.txt`, `staging/p11b6_failblock.txt`, `staging/p11b6_jobend.txt`,
plus extractors `extract_p11b6_failures.py`, `extract_p11b6_tracebacks.py`,
`extract_p11b6_jobend.py`, `extract_p11b6_failblock.py`, `extract_p11b6_perfile.py`.

---

## 9. Remaining failures, skips, and limitations

Run totals: **PASSED 12 029 · FAILED 62 · SKIPPED 21 · XFAIL 1 · ERROR 0** (12 113 outcomes).
All 62 failures were reconciled to node ids — 60 parsed directly, 2 recovered from line-wrapped log
entries, summing to exactly 62.

| File | Failures | Relation to D5′ |
|---|---|---|
| `tests/test_quota01_exchange_slot_is_atomic.py` | 21 | **2 are D5′ real-Redis**; 19 are not |
| `tests/test_no_credential_in_api_response_body.py` | 13 | Unrelated |
| `tests/test_validation_sweep.py` | 9 | Unrelated |
| `tests/test_task_6_1_backtest_execute.py` | 7 | Unrelated |
| `tests/test_strategy_status_publish.py` | 4 | Unrelated |
| `tests/property/test_tenant_isolation_matrix.py` | 3 | Unrelated |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | 2 | **D5′** |
| `tests/test_ml_deployment_guards.py` | 1 | Unrelated |
| `tests/test_task_33_2_rate_limit_keys.py` | 1 | Unrelated |
| `tests/test_task_8_4_runtime_readiness_gate.py` | 1 | Unrelated |

**D5′-attributable: 4. Unrelated pre-existing or concurrent: 58.** The 58 were **not** repaired —
doing so would exceed the authorized scope and would touch files owned by other workstreams.

A green overall job was never treated as sufficient evidence, and in this run the question is moot:
the job is red. Conversely, the red job is **not** by itself grounds to reject D5′ — the per-test
accounting in §7 is what carries the mechanism verdict.

### Limitations of this report

1. Tracebacks for the four D5′ failures are unobtainable (§8); root cause undetermined.
2. No local Redis, so CI behaviour could not be reproduced on this machine.
3. The quota file had no CI history before this commit, so there is no prior run to compare against.
4. Failure counts were derived by regex over a 5.5 MB truncated UTF-16 log; the reconciliation to
   exactly 62 is the correctness check on that method.
5. Concurrent run `38043513862` (branch `ci/d5-real-redis-quota-evidence`, SHA `b43e990d…`) belongs
   to another agent tab and was deliberately left alone.

---

## 10. Unresolved QUOTA-01 items — ticket stays OPEN

Nothing in A5/A6 closes QUOTA-01, and the CI result moves it no closer. These remain outstanding and
require separate resolution:

1. **Lock release** — the release path's ownership semantics are not fully proven under contention.
2. **Database backstop** — no durable backstop behind the Redis-held slot count; a cache loss still
   loses the quota ledger.
3. **Production population** — the production quota counters are not populated/reconciled from a
   trusted source of record.
4. **Quota-limit gaps** — limit enforcement is incomplete across the metered paths.

Additionally opened by this run, and tracked under the same ticket until explained: the quota slot
file fails 21 of 22 tests in CI while passing locally.

---

## 11. Safety confirmations

| Invariant | State |
|---|---|
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` — unchanged |
| 22 pre-staged files from other workstreams | Intact |
| Checked-out branch | `main`, HEAD still `074b772a…` |
| Concurrent modified / untracked work | Untouched |
| Files committed outside the approved manifest | **None** — 5 files, +1761, −0 |
| PR #7 | **OPEN, not merged** |
| Production Redis / databases | **Not accessed** |
| Infrastructure installed or started | **None** |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 12. Recommended next action (requires separate authorization)

**Smallest step that resolves the open question:** obtain the four tracebacks, to determine whether
the quota file's CI failures share one common cause.

Proposed, scoped to evidence collection only — no production code, no merge:

1. Re-run job `Unit & Integration Tests` restricted to the three D5′ files with `--tb=long -rf`, so
   the `FAILURES` block fits inside GitHub's log budget; **or**
2. Add `--junitxml` plus an artifact upload to that job, which survives log truncation entirely.

Until a traceback exists, the correct statements are the ones in this report: **the D5′ mechanism is
VERIFIED, Redis integration is FAILED, and the cause of the four failures is undetermined.** Do not
merge PR #7 on this evidence.
