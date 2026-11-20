# Phase 11B-D3 — DUP-01 Retry Safety Fix

**Scope.** Eliminate automatic duplicate venue submission caused by retrying an execution
whose previous submission may have reached the venue.

**Live trading status.** OFF. NOT CERTIFIED. No venue call, no credential, and no
sandbox order was issued in this phase. No commit, no push, no CCXT version change, no
allowlist change.

**Tracking id used for the defect introduced by this phase's analysis.** `DUP-02`. DUP-01
is the venue-side `clientOrderId` mapping finding from 11B-C and is unchanged by this work;
DUP-02 is the client-side retry path that makes DUP-01 exploitable. Section 7 states both
verdicts.

---

## 1. Baseline git state and ownership of changes

### 1.1 Repository state

| Fact | Value |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Branch | `main` |
| Divergence from `origin/main` | `0 0` (`git rev-list --left-right --count`) — neither ahead nor behind |
| Working tree entries (`git status --porcelain`) | 256 (22 staged, 136 untracked) |
| Staged entries | 22 — **unchanged** from the Phase 0 baseline |

HEAD is byte-identical to the value recorded in 11B-D and 11B-D2. The working tree grew
from 254 to 256 entries; both additions are evidence artifacts written under `staging/`
by this phase. **No commit was created, nothing was staged, nothing was pushed.**

### 1.2 Dependency state — unchanged

| Fact | Value |
|---|---|
| Pin | `requirements-base.txt:95` → `ccxt[async]==4.3.92` |
| Installed | `ccxt 4.3.92` |

Verified after the code change. The pin and the installed build are the same ones every
measurement in 11B-C, 11B-D and 11B-D2 was taken against, so the venue findings in
section 7 remain comparable. CCXT was not upgraded.

### 1.3 Files this workstream owns

Attributable delta is `git diff --numstat` now, minus the same measurement taken at this
phase's Phase 0 checkpoint. Both files already carried large uncommitted diffs from
earlier workstreams, so the raw numstat is not the ownership figure.

| File | numstat now | Phase 0 baseline | **Attributable to 11B-D3** |
|---|---|---|---|
| `backend_app/core/models/execution_record.py` | `839 / 6` | `721 / 5` | **+118 / −1** |
| `backend_app/core/execution_engine.py` | `955 / 29` | `911 / 28` | **+44 / −1** |
| `tests/test_dup02_ambiguous_submission_is_not_retried.py` | untracked (`??`) | did not exist | **new, 469 lines, 22 tests** |
| `tests/regression/baseline/live_order_path.json` | `43 / 4` | `36 / 4` | **+7 / −0** |

Four files: two production, one test, one regression baseline. Nothing else was edited.

The baseline file is the one entry the brief did not anticipate and the one change an owner
may want to review or revert. It is a contract-drift snapshot of the live-order path, and
the fix legitimately adds a new return outcome to that path, so the snapshot had to be
re-recorded through the repository's documented procedure. Section 4.4 gives the rationale
and section 5.7 the evidence that the change is purely additive (+7 lines, 0 removed) and
that no pre-existing provenance note was lost.

Verified after every revert probe in section 5.7: the two production files hash to
`DE6C95CE…58EB` and `E1D65AA0…71CC`, the values they had immediately after the fix was
written.

### 1.4 Files confirmed NOT changed by this workstream

`git diff --numstat` for each is identical to the Phase 0 baseline, so this phase
contributed nothing to them:

| File | numstat (both measurements) |
|---|---|
| `backend_app/backend/exchange_executor.py` | `1188 / 55` |
| `backend_app/backend/exchange_websocket_listener.py` | `235 / 33` |
| `backend_app/backend/transactional_execution_manager.py` | `67 / 2` |
| `backend_app/backend/connection_engine.py` | `188 / 18` |
| `pytest.ini` | `13 / 0` |
| `tests/conftest.py` | `172 / 0` |
| `requirements-base.txt` | no diff |

Also present in the tree from other workstreams and untouched here:
`backend_app/backend/order_watchdog.py` (`292 / 2`),
`backend_app/routers/exchange.py` (`154 / 14`),
`backend_app/core/exchange_certification.py` (`16 / 4`).

No `PHASE11A_*` or `PHASE11_*` launch-gate register, readiness scorecard or evidence JSON
was edited. The only non-code files written are this report and the raw run logs under
`staging/p11bd3_*`.

### 1.5 Path corrections to the task brief

Four of the five paths in the brief do not exist. The audited files are:

| Brief | Actual |
|---|---|
| `backend_app/exchange_executor.py` | `backend_app/backend/exchange_executor.py` |
| `backend_app/transactional_execution_manager.py` | `backend_app/backend/transactional_execution_manager.py` |
| `backend_app/signal_service.py` | `backend_app/backend/signal_service.py` |
| `backend_app/order_watchdog.py` | `backend_app/backend/order_watchdog.py` |
| `backend_app/core/execution_engine.py` | correct as given |

### 1.6 One environment action taken

`staging/bootstrap_pg.py start --data-dir pgdata_r1 --expect-provisioned` was run to bring
up the local `pgserver` PostgreSQL cluster, because the PostgreSQL-gated suites — TRADE-07
among them — cannot otherwise execute. It reported `TARGET: PROVISIONED
public_tables=89`. This is local staging infrastructure on `127.0.0.1`, deliberately
separate from production, and it is reversible with
`python staging/bootstrap_pg.py stop`. Starting it rewrote `staging/pg_dsn.txt` with the
new port, which is shared state: `pgserver` allocates a fresh port on each start.
**The cluster is still running.** Section 5 explains why this mattered.

---

## 2. Confirmed call chain and state transitions

Every line reference below was read in the current tree. Pre-fix line numbers are labelled
as such where the fix moved them.

### 2.1 The submission path

```
BotRunner tick
  └─ ExecutionEngine.execute_with_idempotency        execution_engine.py:657
       ├─ STEP 1  repo.check_idempotent_execution(..., allow_failed_retry=True)
       │                                             execution_engine.py:893
       │            └─ ExecutionRecordRepository.check_idempotent_execution
       │                                             execution_record.py:331
       ├─ STEP 2  branch on the returned action
       ├─ STEP 3  claim_execution                    execution_record.py:740
       │            (or claim_execution_within_capacity, :875)
       └─ _execute_trade_internal
            └─ mark_submitted                        execution_record.py:1203
                 └─ place_order                      exchange_executor.py:1184 / :1400
                      └─ self._exchange.create_order exchange_executor.py:1645   ← the venue
```

### 2.2 The state transitions that matter

| Step | Transition | Where |
|---|---|---|
| New execution | (none) → `PENDING` inserted | `check_idempotent_execution` CASE 5 |
| Claim | `PENDING` → `EXECUTING` | `claim_execution`, `execution_record.py:740` |
| Immediately before the venue call | `submitted_at` set, **committed in its own transaction** | `mark_submitted`, `execution_record.py:1203` |
| Lost response | `place_order` returns `status="rejected"` | `_handle_ccxt_error` classifies it `NetworkError` |
| Engine records it | `EXECUTING` → `FAILED` | `execution_engine.py:1134`, `:1162` |
| Watchdog cannot determine outcome | → `UNKNOWN` | `OrderWatchdog` |

Two properties of `mark_submitted` are what make the fix possible. It commits in its **own**
transaction immediately before `create_order`, so the flag survives the process dying
mid-submission; and its `WHERE submitted_at IS NULL` predicate keeps the first instant
authoritative. `classify_stale_executions` (`execution_record.py:1312`) already draws the
same inference, splitting `failed_before_submission` from `ambiguous_submission` on exactly
this column.

### 2.3 Execution id stability — the reason a retry collides

`generate_execution_id` (`execution_record.py:80`) buckets the timestamp with
`execution_interval_minutes: int = 5`. Every tick inside the same five-minute bucket, for
the same tenant/strategy/symbol/side/qty/price, derives the **same** `execution_id` and
therefore lands on the **same** row. That is intended idempotency; it is also what turns a
single mis-classified row into a repeating resubmission.

---

## 3. Root cause and the exact conditions that produced it

### 3.1 Root cause

**An ambiguous venue outcome was recorded as a determinate failure, and the determinate
failure was then treated as retryable without ever consulting the evidence that a
submission had already been sent.**

Two independent defects compose:

**(a) Mis-classification.** A lost or timed-out response from `create_order` is classified
by `_handle_ccxt_error` as `NetworkError`; `place_order` converts that to
`status="rejected"`; the engine writes `ExecutionStatus.FAILED` (`execution_engine.py:1134`,
`:1162`). "Rejected" asserts the venue refused the order. A timeout asserts nothing. The
order may be live.

**(b) A missing branch.** `check_idempotent_execution` returns `allow_retry` from CASE 3
(FAILED, pre-fix `execution_record.py:509`) and CASE 4 (PENDING, pre-fix `:556`). Pre-fix,
`execute_with_idempotency` branched on exactly three actions — `skip_return_result`,
`skip_already_running`, `skip_failed_no_retry` — and had **no branch for `allow_retry`**.
Control therefore reached STEP 3 by fall-through. CASE 3 had additionally reset the row to
`PENDING`, which is precisely what let `claim_execution` succeed a second time.

Neither function referenced `submitted_at`. `ExecutionStatus.UNKNOWN` had no case at all
and fell through the same way.

### 3.2 The exact conditions

All five hold simultaneously in normal operation:

1. `execution_engine.py:893` passes `allow_failed_retry=True` — hard-coded, not configurable.
2. The previous attempt reached `mark_submitted` and then lost the response, so
   `submitted_at IS NOT NULL` while `status = FAILED`.
3. The next tick falls in the same five-minute bucket, so `generate_execution_id` returns
   the same id and the same row is found.
4. CASE 3 returns `allow_retry` and resets the row to `PENDING`.
5. No `allow_retry` branch exists, so STEP 3 claims the now-`PENDING` row and
   `_execute_trade_internal` calls `create_order` again.

The duplicate is only arrested if the venue honours our `clientOrderId`. Measured on ccxt
4.3.92, **coinbase overwrites it and kraken has no equivalent field** (section 7), so on
those two venues nothing downstream can refuse the second order.

### 3.3 Why the measurement in 11B-D missed this

11B-D concluded "no live retry path exists" from an AST search for loop constructs around
the submission call. The retry here is not a loop: it is the next scheduler tick landing on
the same row. The method was blind to it by construction. 11B-D2 reversed that conclusion
by reading the branch table instead, and recorded that **zero tests in the repository
referenced `allow_retry`** — the action was both unhandled and unexercised.

---

## 4. Changed files and the rationale for each change

### 4.1 `backend_app/core/models/execution_record.py` (+118 / −1)

**(a) `AMBIGUOUS_SUBMISSION_ACTION = "skip_ambiguous_submission"`, module level (`:41`).**
A distinct action rather than a reuse of `skip_failed_no_retry`. `skip_failed_no_retry`
means "policy forbids retrying this"; the new one means "this may be live at the venue".
Those demand different operator responses — a policy outcome versus a reconciliation — so
conflating them would have hidden the one that needs a human.

**(b) `submission_may_have_reached_venue(record)` (`:44`).** The single predicate that
decides whether a retry is safe.

- Returns `True` when `submitted_at IS NOT NULL`. Its absence is positive evidence the
  venue call was never reached, for the durability reasons in section 2.2.
- Returns `True` for `ExecutionStatus.UNKNOWN` **unconditionally, ignoring `submitted_at`**.
  That status is written when the watchdog *could not establish* the outcome. Resting the
  decision on `submitted_at` there would rely on evidence the status itself declares
  insufficient.
- Returns `True` for a record that is `None`, has an unreadable status, or raises on
  attribute access. A retry authorisation is the dangerous answer, so anything the function
  cannot positively clear is refused.

**Rejected alternative: a new `submission_state` column.** It would have been more
explicit, but it requires a schema migration that is not authorised in this phase, and
`submitted_at` already carries the needed fact with semantics that
`classify_stale_executions` has been relying on. Reusing it keeps **one** definition of
ambiguity in the codebase.

**Rejected alternative: treat every `FAILED` row as ambiguous.** It is the smaller diff and
it is wrong. A row with `submitted_at IS NULL` provably never reached `create_order`, and
refusing it would disable legitimate first submissions — a trading outage dressed up as a
safety improvement. The brief prohibits exactly this.

**(c) A guard ahead of CASE 3 (`:456`–`:507`).** Placed before both CASE 3 and CASE 4
because both returned `allow_retry`. It logs `critical` with the execution id, status,
`submitted_at` and `order_id`, and returns `AMBIGUOUS_SUBMISSION_ACTION`.

It deliberately **does not transition the row.** Transitioning to `EXECUTING` would have
made the row visible to `classify_stale_executions`, but it would also falsely claim the
row and make this function a second recovery owner, which the brief forbids and which is
worse than the defect. The cost of that choice is recorded as a residual risk in section 6.

**(d) The same guard in the insert-conflict fallback (`:624`–`:641`).** Reached when a
concurrent worker inserts the row between the existence check and this `INSERT`. It decides
precisely the racing case the guard most needs to cover, and pre-fix it granted
`allow_retry` too. Omitting it would have left the two-worker path unprotected.

### 4.2 `backend_app/core/execution_engine.py` (+44 / −1)

**(a) Import updated (`:22`–`:24`)** to bring in `AMBIGUOUS_SUBMISSION_ACTION`.

**(b) `elif action == AMBIGUOUS_SUBMISSION_ACTION:` (`:936`).** Returns
`status='skipped_ambiguous_submission'` with a message naming reconciliation as the
required next step. Returning here keeps the execution away from STEP 3's claim.
Resubmitting would double a position; marking it successful would invent a fill; so the
tick is skipped and the row is left exactly as it is for `OrderWatchdog`.

**(c) `elif action == 'allow_retry':` (`:960`).** An explicit branch, with a log line, for
what previously proceeded by fall-through. The fall-through *was* the structural defect.
Naming it means a future action added to `check_idempotent_execution` without a branch is
caught by `test_every_action_the_check_can_return_is_handled_explicitly` instead of
silently inheriting "proceed to submit". Reaching this branch is safe because the ambiguity
guard has already refused anything that may be live.

### 4.3 `tests/test_dup02_ambiguous_submission_is_not_retried.py` (new, 469 lines, 22 tests)

Written before the fix and confirmed red against the unfixed code. Groups: the core
refusal; `UNKNOWN`; the negative controls that keep legitimate retries working; `PENDING`;
non-regression of `EXECUTING` and `COMPLETED`; ten successive ticks; two workers over one
database; the timeout-shaped failure `place_order` actually produces; guard-level unit
tests; and the structural test that every action has a branch.

One test asserts the guard function directly rather than through a persisted row:
`execution_records.status` is `NOT NULL`, so neither SQLite nor PostgreSQL will accept a
null-status row. That constraint is itself a safety property, so the test exercises the
predicate instead of fighting the schema.

### 4.4 `tests/regression/baseline/live_order_path.json` (+7 / −0)

`tests/regression/test_baseline_unchanged.py` snapshots the live-order path's contract and
fails on any drift. `contract.returns` is a **positional list of every `return` statement**
in `execute_with_idempotency`, built by `capture_baseline._own_returns`. The new
`skipped_ambiguous_submission` branch is a 14th return where the baseline recorded 13, so
the guard failed with exactly one difference: `contract.returns: entry added`.

**The guard was right to fire.** The live-order path really did gain a new outcome. The
module documents the only legitimate response: an entry may be re-recorded where a
requirement authorises the change, and the authorisation must be written beside it as a
`_baseline_note…` member, because "a re-record with no such note is a regression that was
papered over". The file already carried three such notes, from TRADE-01, TRADE-12 and the
risk-verdict parameter change, so this is an established convention in active use rather
than an escape hatch invented here.

Re-recording was done through the official capture, not by hand, by
`staging/p11bd3_rebaseline.py` — kept as provenance. A plain
`python tests/regression/capture_baseline.py live_order_path` would have **deleted the
three existing notes**, because `capture()` never emits them and `write_baseline` rewrites
the whole file. The helper therefore collects every `_baseline_note*` member at any depth,
re-records through `capture()`, restores the notes, and adds
`_baseline_note_ambiguous_submission_refusal_dup02`. It **refuses and exits non-zero** if
the capture differs from the baseline by anything other than exactly one added return
entry, so it cannot mask a second drift.

Note that the added entry is shape-identical to the existing refusal returns
(`status`/`execution_id`/`result`/`message`). Nothing recorded was removed, renamed,
retyped or re-meaninged.

---

## 5. Regression tests run and exact results

Python `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1. Every run was written to a file
and the file was read; no result below is inferred from an exit code alone. **Skips are
reported as skips and are never counted as passes.**

### 5.1 The new suite, before and after

| Run | Result | Artifact |
|---|---|---|
| DUP-02 suite vs **unfixed** code | **12 failed, 7 passed** | `staging/p11bd3_before.txt` |
| DUP-02 suite vs **fixed** code (SQLite) | **22 passed**, EXIT=0 | `staging/p11bd3_after.txt` |
| DUP-02 suite vs fixed code (PostgreSQL) | **22 passed**, EXIT=0 | `staging/p11bd3_after_restore.txt` |

The before/after pair is the evidence that the tests bind to the defect rather than to the
implementation.

### 5.2 Idempotency and execution-engine suites

`staging/p11bd3_reg1.txt` — EXIT=0, **140 passed, 0 failed, 0 skipped** (569 s):
`test_dup02_ambiguous_submission_is_not_retried`,
`test_dup01_client_order_id_mapping`,
`test_idempotent_submission_fails_closed`,
`test_execution_paths_that_claim_an_exchange`,
`test_order_normalization_and_venue_idempotency`,
`test_single_execution_pipeline`,
`test_execution_engine_accounting_identity`.

### 5.3 Concurrency, risk gateway, kill switch, venue routing

`staging/p11bd3_reg2.txt` — EXIT=0, **174 passed, 50 skipped** (60 s). All 50 skips were
PostgreSQL-gated, each carrying an explicit "A SKIP here is NOT a pass" message. Covered
TRADE-01 concurrent exposure, TRADE-04 live-order gate at the venue boundary, KILL-03
authoritative global halt, TRADE-02 kill-switch fail-closed, EXCH-01 bitget routing block,
EXCH-01 sandbox endpoint verification, FILL-01 partial-fill accounting.

### 5.4 TRADE-07 recovery against the required isolated PostgreSQL database — gap CLOSED

This is the verification the brief singled out, and it initially could not run.

| Run | Result | Artifact |
|---|---|---|
| Before starting PostgreSQL | 13 passed, **14 skipped** | `staging/p11bd3_reg3.txt` |
| Against the provisioned local PostgreSQL | **27 passed, 0 skipped**, EXIT=0 (106 s) | `staging/p11bd3_reg3_pg.txt` |

`AERORA_TEST_DATABASE_URL` was unset and no PostgreSQL server was reachable: no docker, no
`postgres` service, no `psql`, nothing listening on 5432, and `staging/pg_dsn.txt` pointed
at a `127.0.0.1` server that was down. Rather than report the gap, the suite's own skip
message named the remedy (`staging/bootstrap_pg.py`), so the provisioned `pgdata_r1`
cluster was started (section 1.6) and the suite was run for real. **TRADE-07 stale-execution
recovery is verified against real PostgreSQL, not approximated on SQLite.**

### 5.5 All previously-skipping PostgreSQL suites, re-run green

`staging/p11bd3_pg_full.txt` — EXIT=0, **218 passed, 0 skipped, 0 failed** (334 s), with
both `AERORA_TEST_DATABASE_URL` and `DATABASE_URL` pointed at the cluster. Ten files:
the seven `*_postgres.py` suites (TRADE-01 concurrent exposure, TRADE-07 recovery, MONEY-01
referral commission idempotence, PAPER-01 ledger, PAPER-02 unfunded order state, position
lifecycle, strategy lifecycle concurrency), plus KILL-03 authoritative global halt, the
DUP-02 suite, and `test_idempotent_submission_fails_closed`.

Without the database these same files produced 130 + 50 skips. The advisory-lock races,
partial unique indexes and two-connection concurrency proofs that SQLite cannot express
are now actually exercised.

### 5.6 The broad execution, order, risk, signal and position surface

74 top-level suites, **1803 tests collected** (`staging/p11bd3_reg4_collect.txt`). Run in
two halves so a single abort could not discard the whole result. Collected totals reconcile
exactly against executed totals in both passes, so nothing was silently dropped.

| Pass | Database | Result | Artifact |
|---|---|---|---|
| 4a | SQLite | 684 passed, 69 skipped, 1 xfailed, EXIT=0 (1250 s) | `staging/p11bd3_reg4a.txt` |
| 4b | SQLite | 972 passed, **1 failed**, 75 skipped, 1 xfailed, EXIT=1 (398 s) | `staging/p11bd3_reg4b.txt` |
| 5a | PostgreSQL | 752 passed, **1 failed**, 0 skipped, 1 xfailed (1070 s) | `staging/p11bd3_reg5a.txt` |
| 5b | PostgreSQL | 1039 passed, 9 skipped, 1 xfailed, EXIT=0 (292 s) | `staging/p11bd3_reg5b.txt` |

With PostgreSQL: **1791 passed, 1 failed, 9 skipped, 2 xfailed = 1803.** Supplying the
database converted 135 skips into real passes.

The 9 residual skips: 7 require `DATABASE_URL` itself to name PostgreSQL and were covered
green in 5.5 where both variables were set; 1 cites a Supabase configuration issue; 1 cites
mocking complexity. The last two are genuinely unverified and unrelated to DUP-02.

### 5.7 Every failure investigated and attributed

Three distinct failures appeared across the targeted batches in 5.1 through 5.6 and the
contract-drift guard. One was caused by this change and was remediated; two were not and
are left as found. The broad suite's 51 failures are triaged separately in 5.9.

**`test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation`**
— fails with `Expected SERIALIZABLE isolation level, got: read committed`.

- Without `AERORA_TEST_DATABASE_URL`: **skipped** (`staging/p11bd3_cancel_nodsn.txt`). It was
  one of pass 4a's 69 skips, never a pass.
- With the database: **fails deterministically** (`staging/p11bd3_cancel_dsn.txt`).
- **With the DUP-02 change reverted to HEAD: fails identically**
  (`staging/p11bd3_revert_probe.txt`).

The revert was done by copying both files aside, `git checkout --` on them, running, then
restoring; the restored files match by SHA-256
(`DE6C95CE…58EB`, `E1D65AA0…71CC`) and the DUP-02 suite was re-run green afterwards (5.1).
The test body opens a fresh `SessionLocal()` and runs `SHOW transaction_isolation`; it
never calls cancellation code and never touches either changed file. **Pre-existing defect,
newly exposed by providing a real database.** Filed, not fixed — fixing the engine-wide
isolation level is outside this phase's authorised scope. Note also that the test asserts
the default isolation of a fresh session rather than that of the cancellation transaction,
so it is a weak test of its stated claim.

**`test_trade02_kill_switch_fails_closed.py::test_two_real_os_processes_agree_through_a_shared_store`**
— failed once in pass 4b with `assert 'ENGAGED=True' in ''`: the spawned child produced
**empty** stdout. It passed in 5.3, passed twice in isolation
(`staging/p11bd3_kill_iso1.txt`, `…iso2.txt`), and passed on the reverted-code run. The
child imports only `redis_manager` and `GlobalKillSwitch`, nothing from either changed
file, and the test's own comment records an earlier version being killed by a console
control event. **Load-dependent flake in a subprocess spawn, not attributable to this
change.** It remains a flaky test worth stabilising.

**`tests/regression/test_baseline_unchanged.py::test_baseline_unchanged[live_order_path]`
— caused by this change, and remediated.**

| State | Result | Artifact |
|---|---|---|
| With the fix, before re-recording | **1 failed**, 59 passed — `contract.returns: entry added` | `staging/p11bd3_baseline.txt` |
| Reverted to HEAD | 1 failed with **12** differences | `staging/p11bd3_baseline_reverted.txt` |
| With the fix, after re-recording | **60 passed**, EXIT=0 | `staging/p11bd3_baseline_after.txt` |

The revert probe is what makes the attribution exact, and it needs reading carefully.
`git checkout --` restores HEAD, which also discards the earlier workstreams' uncommitted
work, so the reverted tree is not "pre-DUP-02" — it is much older. At HEAD the baseline
disagrees in **12** ways (`contract.parameters.risk_approved` and `risk_reason` missing, two
return entries missing, the `order_id` collaborator argument absent, and more). The stored
baseline therefore represents the working tree **immediately before DUP-02**, and against
that tree the fix introduces **exactly one** difference. Attribution is unambiguous.

Remediation per section 4.4. Verification that it is not a papered-over regression:

- The diff against the pre-edit copy is **2 hunks, +7 lines, −0 lines** — one note line and
  the six lines of the new return entry. Nothing was removed.
- `contract.returns` went from 13 entries to 14; the helper would have refused any other
  count.
- Provenance notes before: 3. After: 4. The TRADE-01, TRADE-12 and risk-verdict notes are
  byte-identical.
- The pre-edit file is retained at `staging/p11bd3_backup/live_order_path.json.before` so
  the change can be reviewed or reverted directly.
- The two production files still hash to `DE6C95CE…58EB` and `E1D65AA0…71CC`, and the
  DUP-02 suite re-ran **22 passed** afterwards (`staging/p11bd3_final_dup02.txt`).

### 5.8 Full suite — three attempts, and what actually blocked the first two

`tests` collects **14,488 tests** (`staging/p11bd3_collect.txt`).

| Attempt | Selection | Reached | Outcome |
|---|---|---|---|
| 1 | everything, `--timeout=120` | ~1% (~145 tests) in ~18 min | aborted on a per-test timeout (`staging/p11bd3_full.txt`) |
| 2 | excl. `property`, `perf`, `e2e`, `ml`, `exchange_sandbox`; 14,311 collected; `--timeout=300` | **22%** in ~70 min | aborted on a per-test timeout (`staging/p11bd3_broad.txt`) |
| 3 | as attempt 2, with the offending test deselected, `--timeout=900` | **100%** in 49 min | **completed** — see 5.9 (`staging/p11bd3_broad2.txt`) |

Both aborts have the **same cause, and it is not a product defect**.
`test_execution_paths_that_claim_an_exchange.py::test_nothing_initialises_or_starts_the_distributed_execution_orchestrator`
AST-parses every Python file in the repository (`_call_sites`, line 234). On this machine
that takes minutes, and under concurrent load it exceeded the per-test timeout. Because
`pytest-timeout` on Windows terminates the **whole process** rather than just the test, one
slow test discards the entire run. The same test **passes** when it is not competing for CPU
— it is part of the 140 green tests in 5.2.

Attempt 1 also shows where the time goes: `tests/property/` holds only 177 of the 14,488
tests, yet consumed the whole first 18 minutes. It is hypothesis-driven and is the slowest
part of the suite by a wide margin.

**The complete 14,488-test suite was not executed and is not claimed as passing.**

### 5.9 The broad suite did complete, and every one of its 51 failures was triaged

Attempt 3 finished. `staging/p11bd3_broad2.txt`, EXIT=1:

```
51 failed, 14184 passed, 70 skipped, 6 xfailed in 2950.45s (0:49:10)
```

14,311 collected, 1 deselected. This is 14,184 of the 14,488 tests in the repository
passing — everything except `tests/property`, `tests/perf`, `tests/e2e`, `tests/ml`,
`tests/exchange_sandbox` and the one AST-scanning test, which passes separately in 5.2.

The 51 failures fall in **four files, none of them in the execution or idempotency path**:

| File | Failures |
|---|---|
| `tests/test_marketplace_deployment_subscriber_safe.py` | 25 |
| `tests/test_entitlement_matrix.py` | 24 |
| `tests/test_exchange_credential_log_redaction.py` | 1 |
| `tests/test_kill03_authoritative_global_halt.py` | 1 |

Re-running those four files **in isolation**, same interpreter, same database
(`staging/p11bd3_triage_iso.txt`):

```
1 failed, 108 passed in 29.91s
```

**50 of the 51 pass in isolation**, so they are order-dependent — shared state across a
14,000-test run against one database, not defects the fix introduced. The KILL-03 failure
is among them, and it also passed in 5.5. Attribution is further settled by
non-involvement: `grep` for `execution_engine`, `execution_record`,
`check_idempotent_execution` and `execute_with_idempotency` returns **zero matches** in
both marketplace test files.

**The one reproducible failure is pre-existing and is not a secret leak.**
`test_exchange_credential_log_redaction.py::…::test_a_connect_failure_whose_exception_embeds_the_secret_is_redacted`
expects `Failed to connect to binance` in the captured log and instead finds:

```
CRITICAL … exchange_executor.py:1337 EXCH-01: refused a sandbox connection to binance
that could not be proven to have left production.
```

The EXCH-01 sandbox-endpoint guard from an earlier workstream refuses the connection
*before* the connect-failure log line is reached, so the test never exercises the redaction
path it asserts on. No credential appears in the emitted line. Its subject,
`backend_app/backend/exchange_executor.py`, is at `1188 / 55` — identical to the Phase 0
baseline, carrying **no 11B-D3 delta** — and the test references neither changed module.
**Stale test encoding the pre-EXCH-01 ordering. Not attributable, not fixed here.**

### 5.10 Summary of what was and was not verified

| Claim | Status |
|---|---|
| Ambiguous `FAILED` row is never auto-resubmitted | **Verified** (SQLite + PostgreSQL) |
| `UNKNOWN` is never auto-resubmitted | **Verified** |
| Pre-submission `FAILED` remains retryable | **Verified** (negative controls) |
| `allow_failed_retry=False` still wins | **Verified** |
| Repeated ticks never clear an ambiguous row | **Verified** (10 ticks) |
| Two workers both refuse and neither claims | **Verified** |
| `EXECUTING` / `COMPLETED` behaviour unchanged | **Verified** |
| Every action has an explicit branch | **Verified** (structural test) |
| TRADE-07 recovery on real PostgreSQL | **Verified** (27 passed, 0 skipped) |
| TRADE-01 advisory-lock concurrency on real PostgreSQL | **Verified** |
| Kill switch, risk gateway, venue routing non-regression | **Verified** |
| Live-order-path contract drift guard green after re-record | **Verified** (60 passed) |
| Broad suite (14,184 passed; all 51 failures triaged, 0 attributable) | **Verified** |
| Venue-side rejection of a duplicate `clientOrderId` | **NOT VERIFIED** — no sandbox evidence |
| Complete 14,488-test suite including `tests/property` | **NOT RUN** |
| Behaviour under real venue latency and partial outage | **NOT VERIFIED** |

---

## 6. Unresolved database, concurrency and venue risks

### 6.1 A refused row is invisible to the watchdog — the main residual risk

`classify_stale_executions` (`execution_record.py:1312`) scans `EXECUTING` rows only. The
guard refuses and leaves the row in `FAILED` or `UNKNOWN`, so **a refused execution is not
picked up by `OrderWatchdog` and will not be adjudicated automatically.** The outcome is a
row that is correctly never resubmitted and also never reconciled without an operator.

This was a deliberate trade. The alternative — transitioning to `EXECUTING` to make the row
visible — would falsely claim it and create a second recovery owner, which the brief
forbids. Closing it properly means widening the watchdog's status filter to include
`FAILED`-with-`submitted_at` and `UNKNOWN`, which is a change to the watchdog's ownership
boundary and needs its own authorisation. **The `critical` log line is currently the only
automatic signal, so an alert on `DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED` is a launch
prerequisite.**

### 6.2 The terminal-state mis-classification is still present

A timeout is still recorded as `FAILED` at `execution_engine.py:1134` and `:1162`. The
guard makes that safe for the retry decision, but the stored status still asserts a
determinate failure that did not occur, and anything else reading `status` — reporting,
P&L, reconciliation — inherits the wrong fact. Writing a genuine indeterminate terminal
state is a P0 that needs authorisation beyond this phase.

### 6.3 Concurrency coverage is real but not exhaustive

Two workers over one database are covered, and TRADE-01's advisory-lock races and partial
unique indexes now execute against real PostgreSQL. What is **not** covered: the guard's
behaviour when `mark_submitted` commits concurrently with a second worker's existence check
on PostgreSQL under genuine lock contention. The insert-conflict guard addresses the shape
of that race, but the interleaving was not driven at the database level.

### 6.4 Venue-side duplicate rejection is UNVERIFIED

**No venue has been observed rejecting a duplicate order.** Scenario 8 of the 11B matrix was
never executed: no sandbox credentials for any of the six allowlisted venues, and Redis is
unreachable at `127.0.0.1:6379` so `enforce_kill_switch` fails closed before an order can be
placed. The `clientOrderId` findings in section 7 are measured from ccxt's parameter mapping
by code inspection, **not** from venue responses. The client-side guard is therefore the
only verified protection against DUP-02.

### 6.5 The DUP-01 mapping findings are version-pinned

All mappings were measured against ccxt 4.3.92. ccxt 4.5.85 is available and may map these
parameters differently. The findings do not transfer across an upgrade without re-measurement.

### 6.6 The re-recorded contract baseline is the one change that wants owner review

Section 4.4 re-recorded one entry in `tests/regression/baseline/live_order_path.json`. It
followed the procedure the module documents, it is purely additive (+7 / −0), it preserves
all three pre-existing provenance notes, and the pre-edit file is retained for direct
reversion. It is nonetheless an edit to a drift guard on the **live-order path**, made on
the authority of this phase's own brief rather than a requirement id. **If that authority is
not accepted, revert that file and the guard will fail again with the single
`contract.returns: entry added` difference, which is an accurate description of the
change.** The fix itself does not depend on it.

### 6.7 Residual non-DUP-02 findings surfaced by this work

- Order-cancellation sessions run at `read committed` where `test_atomic_order_cancellation_fix`
  requires `SERIALIZABLE` (5.7). Pre-existing, reproducible, unfixed, out of scope.
- `test_two_real_os_processes_agree_through_a_shared_store` is load-flaky (5.7).
- `test_exchange_credential_log_redaction.py::…test_a_connect_failure_whose_exception_embeds_the_secret_is_redacted`
  fails reproducibly because the EXCH-01 sandbox guard refuses the connection before the
  log line it asserts on (5.9). Pre-existing; the redaction path is now **unexercised**,
  which is worth an owner's attention even though no secret leaked.
- 49 marketplace and entitlement tests, plus one KILL-03 test, fail only inside a
  14,000-test run and pass in isolation (5.9). Order-dependent shared state across a single
  test database. Not attributable, but it makes the broad suite unreliable as a gate until
  the tests are isolated.
- `staging/pg_dsn.txt` is rewritten on every `bootstrap_pg.py start` because `pgserver`
  takes a new port each time. Any other process holding the old DSN now points at nothing.
- 2 of the 9 residual skips (Supabase configuration, mocking complexity) remain unverified.
- The started PostgreSQL cluster is still running and should be stopped with
  `python staging/bootstrap_pg.py stop` when no longer needed.

---

## 7. Revised DUP-01 verdict and remaining launch gates

### 7.1 DUP-02 — the client-side retry path

**Verdict: FIXED, with a named residual gap.**

An execution whose previous submission may have reached the venue is no longer
automatically resubmitted. Verified by 22 focused tests that were red before the change and
green after, on SQLite and on PostgreSQL, and by a 1,803-test regression sweep over the
idempotency, execution-engine, concurrency, risk-gateway, kill-switch, venue-routing,
signal and position surfaces that ended 1,791 passed / 9 skipped / 1 failed, with the one
failure proved pre-existing (5.7), and by a broad run of **14,184 passing tests** in which
all 51 failures were triaged to order-dependence or a pre-existing stale test, none
attributable (5.9). Those run totals overlap — the batches in 5.2 through 5.6 share files —
so they are not additive. The residual gap is 6.1: refused rows are not watchdog-visible
and need an operator, so the `critical` log must be alerted on before live trading.

This supersedes the 11B-D verdict of `REJECT` ("no live retry path exists"), which was
reached with a method blind to the actual retry mechanism. 11B-D2's reversal stands and is
now implemented.

### 7.2 DUP-01 — venue `clientOrderId` mapping, ccxt 4.3.92, unchanged by this phase

| Venue | Parameter | Honours our id? |
|---|---|---|
| binance | `newClientOrderId` | **Yes** (also auto-generates `brokerId + uuid22()` when none is supplied) |
| bybit | `orderLinkId` | **Yes** |
| okx | `clOrdId` | **Yes** |
| kucoin | `clientOid` | **Yes** |
| coinbase | — | **No** — ccxt overwrites with `'client_order_id': id + '-' + self.uuid()` |
| kraken | — | **No** — no `clientOrderId` in any `create*`/`edit*` method; `userref` is a 32-bit signed int and is not plumbed |

**Verdict: PARTIALLY MITIGATED, and no longer the sole protection.**

Four of six venues can reject a duplicate by client id *if* they behave as their parameter
mapping implies — which has not been observed (6.4). On **coinbase and kraken there is no
venue-side protection at all**, so for those two the client-side guard delivered in this
phase is the only thing standing between a lost response and a doubled position. That is
why the fix was the right place to intervene rather than the venue layer.

### 7.3 Remaining launch gates for live trading

| Gate | Status | Blocking |
|---|---|---|
| DUP-02 client-side retry guard | **Done** | No |
| Alert on `DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED` | Not done | **Yes** — 6.1 leaves no other automatic signal |
| Watchdog visibility for refused rows | Not done, needs authorisation | **Yes** for unattended operation |
| Indeterminate terminal state instead of `FAILED` on timeout | Not done, P0, needs authorisation | **Yes** |
| Venue-side duplicate rejection proven in sandbox (scenario 8) | Not executed — no credentials | **Yes** |
| coinbase / kraken duplicate-order strategy | Not designed | **Yes** for those two venues |
| Sandbox credentials for the allowlisted venues | Absent | **Yes** — blocks all of 11B |
| Redis reachable (`enforce_kill_switch` currently fails closed) | Absent | **Yes** |
| Re-measure DUP-01 mappings if ccxt is upgraded | N/A — not upgraded | No, while pinned at 4.3.92 |
| Order-cancellation isolation level (`read committed` vs `SERIALIZABLE`) | Pre-existing, unfixed | Owner decision |
| Credential-redaction path unexercised behind the EXCH-01 guard | Pre-existing, unfixed | Owner decision |
| 50 order-dependent tests isolated so the broad suite can gate | Not done | Owner decision |
| Re-recorded `live_order_path` baseline entry accepted | Pending review | Owner decision (6.6) |
| Complete 14,488-test suite including `tests/property` | Not practical in this environment | Owner decision |

**Live trading remains OFF and NOT CERTIFIED.** This phase removes one mechanism by which a
duplicate order could be placed automatically. It does not certify the venue integration.
