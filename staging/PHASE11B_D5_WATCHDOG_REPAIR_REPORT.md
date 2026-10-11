# Phase 11B-D5 — Watchdog Adjudication Repair (W-1 / W-2)

**W-1 FIXED · W-2 FIXED · W-2b FIXED (a third defect in the same block, found while
fixing W-2) · W-1b FOUND AND NOT FIXED — it blocks the benefit of W-1 and is outside this
phase's authorisation.**

Two files changed: `backend_app/backend/order_watchdog.py` (+143 / −9 attributable) and a
new `tests/test_d5_watchdog_adjudication_repair.py` (18 tests). No status, no migration, no
venue policy, no commit. **Live trading remains OFF / NOT CERTIFIED.**

---

## 1. Phase 0 — rebaseline, path corrections, ownership

### 1.1 Baseline

| Property | D4 reference | **Measured at Phase 0** | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main...HEAD` | `0 0` | `0 0` | no |
| Worktrees | 1 | **1** (`C:/aerora_quant_backend_updated_final1 074b772a [main]`) | no |
| Staged entries | 22 | **22** | no |
| Canonical staged digest | `2e994018…7152` | **`2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152`** | **no** |
| Unstaged modified | 100 | 100 | no |

The canonical staged digest is computed over `git diff --cached --raw` with CRLF
normalised, blank lines dropped — the tool-independent form the concurrent PG-preservation
workstream introduced. It reproduced exactly, so the 22 staged paths, modes and blob SHAs
are untouched.

### 1.2 Path correction to the brief

| Brief | Actual |
|---|---|
| `backend_app/core/order_watchdog.py` | **does not exist** |
| — | `backend_app/backend/order_watchdog.py` ← the authorised target |

A **second copy** exists at `aerora_quant_platform/backend_api/backend/order_watchdog.py`
carrying both defects (`await self._get_executor_for_order` at its lines 191 and 354;
`await self._persist_order_update` at 368 and 412; `def _get_executor_for_order` at 535).
It is **untracked by git** (`git ls-files --error-unmatch` → "did not match any file(s)
known to git"), and every reference to `aerora_quant_platform/` in `backend_app/` or
`tests/` is a *scan-exclusion list or docstring* that already treats it as a stale
duplicate tree (`tests/test_stale_duplicate_frontend.py`,
`tests/test_execution_paths_that_claim_an_exchange.py:138`,
`tests/test_no_unused_jwt_library.py:70`). **Not live code, not imported, not fixed here.**
Recorded as a residual item.

### 1.3 Target-file status at Phase 0 — no ownership conflict

| File | Porcelain | Staged? | Pre-edit SHA-256 (16) |
|---|---|---|---|
| `backend_app/backend/order_watchdog.py` | ` M` | **no** | `03AFD5F20C0716E4` |
| `backend_app/core/execution_engine.py` | ` M` | no | `E1D65AA0E60868A4` |
| `backend_app/core/models/execution_record.py` | ` M` | no | `DE6C95CE08AEF318` |
| `backend_app/backend/exchange_executor.py` | ` M` | no | `81ED5679BB93AFB0` |
| `tests/conftest.py` | ` M` | no | `9D7BC7FE48C0FFB8` |
| `pytest.ini` | ` M` | no | `2CCC1AED690E6C7F` |
| `tests/test_trade07_…_postgres.py` | `??` | n/a | `24A25DF7288BCE83` |
| `tests/test_dup02_…_retried.py` | `??` | n/a | `6A75D0CB36D56E48` |

`git diff --cached --name-only` over all eight returned **empty**. The leading space in
` M` means unstaged-only. `order_watchdog.py` carried `292 / 2` from the TRADE-07
workstream — **identical to the D4 measurement**, so nothing moved under this phase.
**No conflict; editing proceeded.**

### 1.4 Concurrent work that appeared DURING this phase

Other workstreams wrote files while D5 was running (mtimes after 21:00 local):
`backend_app/core/subscription_engine.py` (21:23), `tests/test_a20_quota_refusal_semantics.py`,
`tests/test_redis_debounce.py`, and the `PHASE11B_A20*` / `PHASE11B_W1D07_W1C02*` reports
with their `staging/p11b20_*`, `p11b22_*`, `w1d07_*` probes. Four files under
`backend_app/migrations` / `alembic` are modified and fifteen untracked — all pre-existing
from other owners.

**None is a D5 target.** Re-verified after those writes: the seven must-be-unchanged files
are hash-identical to Phase 0, and `order_watchdog.py` still hashes to this phase's build
`507F5C85A7984200CE4D729651231453A53E85EF12C94F1798882680CACC3ADE`. The staged digest is
still `2e994018…`. **Nothing was overwritten or absorbed.**

---

## 2. Root causes

### 2.1 W-1 — an awaited synchronous method disabled all ambiguous adjudication

`_get_executor_for_order` is declared `def` (not `async def`) and was awaited at three
sites: `_reconcile_open_orders`, `_adjudicate_ambiguous_execution`, `_refresh_order_status`.
`await` on a non-awaitable raises `TypeError`.

The consequence is entirely determined by what each call site does with that exception:

| Call site | Enclosing handler | Effect |
|---|---|---|
| `_adjudicate_ambiguous_execution` | `try/except Exception` that logs "could not build an executor" and leaves `executor = None` | **Every ambiguous row took the "cannot ask" branch** → `UNKNOWN` + alert, and `find_order_by_client_order_id` was never reached for any row |
| `_refresh_order_status` | none locally; propagates to `_check_stale_orders`' handler | logged as "Failed to refresh order" |
| `_reconcile_open_orders` | per-order `try/except` | logged as "Error reconciling order", `continue` |

So the venue lookup TRADE-07 built — and which the D3/D4 reports name as the owner of
ambiguous-submission adjudication — was unreachable in every case.

**This was a liveness defect, not a safety one.** The broken branch is the conservative
one: it refuses and records `UNKNOWN`. Nothing resubmitted. That is why no safety test
caught it, and why two of this phase's own safety-oracle tests pass in both the broken and
the repaired state (section 4.3).

### 2.2 W-2 — a persistence call with no implementation

`_persist_order_update` was called at two sites in `_refresh_order_status` and a
repository-wide search for `def _persist_order_update` returned **zero** definitions.
`OrderWatchdog` has no base class, so both calls raised `AttributeError`.

Both call sites first execute:

```python
order.status = ExecutionStatus.UNKNOWN
order.exchange_status = "unknown: …"
```

**Assigning an ORM attribute is not a commit.** It dirties the instance on the watchdog's
long-lived session, and whether it ever reached the database depended on an unrelated later
`self.db.commit()` elsewhere in the class (e.g. in `_update_order_from_status`). The
"DO NOT ASSUME FILLED" fail-safe the surrounding comments describe was therefore not
durable, and the operator-facing reason was the part most likely to be lost.

### 2.3 W-2b — a column the model does not have, reachable only once W-2 was fixed

The no-executor alert read `order.user_id`:

```python
f"Verify exchange credentials for user {order.user_id}, exchange {order.exchange_id}."
```

`ExecutionRecordModel` declares no `user_id` column — verified by `hasattr` and by reading
the model. Before W-2 the `AttributeError` from `_persist_order_update` fired first, so this
line was never evaluated. **Fixing W-2 alone would have replaced a swallowed `AttributeError`
with a raised one, in the same branch.** Corroboration that this is a known shape:
`_adjudicate_ambiguous_execution` already compensates with
`shim.user_id = row.get("tenant_id")` at line 502.

### 2.4 W-1b — FOUND, NOT FIXED: neither dependency the method reaches for exists

Removing the awaits restores control flow. It does **not** make the method return an
executor in production:

| Dependency | Measured state |
|---|---|
| `app_state.exchange_router` | `AppState` (`backend_app/core/state.py:88`) declares exactly `vault`, `telemetry`, `risk`, `alert`, `fleet`, `ws`. The **only two references to `exchange_router` in the entire application are the two lines inside this method**. `hasattr` is False. |
| `app_state.vault.get_exchange_client` | **No `def get_exchange_client` anywhere.** `APIKeyVault` exposes `store_exchange_keys`, `load_decrypted_keys`, `delete_exchange_keys`, `list_connected_exchanges`, `get_user_tier`, `rotate_key`. The call raises `AttributeError`, converted to `None` by the method's own `except`. |

So in production the method still returns `None` and adjudication still takes the
"cannot ask" branch — now for an honest reason rather than a swallowed `TypeError`.
Wiring an executor factory is credential and executor-lifecycle work this phase is
explicitly forbidden from doing ("Preserve existing dependency-injection, exchange
credential, tenant, and executor lifecycle behavior"). It is recorded in the method's
docstring and is the top remaining blocker.

**This is why the tests prove reachability with an injected stub and claim nothing about
production venue recovery.**

---

## 3. Exact changes

Only `backend_app/backend/order_watchdog.py`. Diff vs index: `292 / 2` → **`435 / 11`**,
i.e. **+143 / −9 attributable to D5**, most of it the two docstrings that record the
measurements above.

### 3.1 W-1 — three `await`s removed; the method stays synchronous

| Line | Change |
|---|---|
| 285 | `executor = await self._get_executor_for_order(order)` → `executor = self._get_executor_for_order(order)` |
| 524 | same, in `_adjudicate_ambiguous_execution` |
| 730 | same, in `_refresh_order_status` |
| ~1051 | `_get_executor_for_order` docstring records the invariant, why it is *not* made `async`, and W-1b |

**Why removal rather than `async def`.** The brief offered both and set the test: async only
"if its body or dependencies require it". The body performs no I/O — one attribute read, one
module-singleton import, one factory call — and neither dependency it reaches for exists
(2.4), so no dependency can be said to require awaiting. Declaring `async def` would have
described work that does not happen and left three `await`s looking correct while the real
problem moved out of sight.

The trade-off is explicit and tested: if a future executor factory becomes a coroutine, this
must become `async def` **and** the awaits must return, in one change. The structural test
asserts both halves, so a one-sided change fails.

### 3.2 W-2 — `_persist_order_update` defined, synchronous, returning success

New method placed beside `_update_order_from_status`, whose convention it follows:

```python
def _persist_order_update(self, order: ExecutionRecordModel) -> bool:
    execution_id = getattr(order, "execution_id", "unknown")
    try:
        self.db.commit()
        return True
    except Exception:
        try:
            self.db.rollback()
        except Exception:
            logger.warning(...)
        logger.critical("W-2: could not persist the status change for execution %s …")
        return False
```

Four decisions, each matching something the codebase already does:

- **No new abstraction.** `_update_order_from_status` in this same class already persists
  with `self.db.commit()` on the constructor-supplied session, and does not use
  `ExecutionRecordRepository`. One persistence convention in this class, not two.
- **Synchronous.** `Session.commit()` is blocking. Declaring it `async` would have
  re-created the shape of W-1 in a second place.
- **Rollback on failure.** A failed statement leaves the transaction needing a rollback on
  PostgreSQL; without one every later statement on this long-lived session inherits the
  aborted transaction. `_update_order_from_status`' position-lock handler already does this.
- **Failure returned, not swallowed.** `logger.critical` plus `False`.

Both call sites (761, 808) now capture the result and append `_UNPERSISTED_SUFFIX` to the
alert when it is `False`, so the alert cannot announce an `UNKNOWN` recovery whose write
failed. A new module-level constant carries that text:

```python
_UNPERSISTED_SUFFIX = (
    " WARNING: this status change could NOT be committed to the database, so the "
    "execution row still carries its previous status. The row has not been recovered."
)
```

On failure the row keeps its previous status — the safe direction: it stays in the stale
population and is re-examined.

### 3.3 W-2b — `order.user_id` → `order.tenant_id` (line 767)

The only column that exists, and the same substitution `_adjudicate_ambiguous_execution`
already makes on its shim.

### 3.4 What was NOT touched

No change to: the stale-execution recovery population, `classify_stale_executions`,
`resolve_stale_execution`, the DUP-02 guard, `ExecutionStatus`, venue routing,
`clientOrderId` formatting, retry policy, order submission, the watchdog's selectors
(`_find_stale_orders` / `_find_open_orders` — that is W-3), session lifecycle, or alert
delivery. No file other than the watchdog and the new test. No safety guard, refusal path
or test was removed or weakened.

---

## 4. Regression coverage and results

Python `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1. `AERORA_TEST_DATABASE_URL`
pointed at the provisioned local cluster verified in D4 (`pgdata_r1`, 89 public tables,
`127.0.0.1`) — read-only `verify` only; the cluster was **not** started, stopped or
re-pointed. **No skip is reported as a pass.**

### 4.1 The new suite

```
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe -m pytest tests/test_d5_watchdog_adjudication_repair.py `
  -q --no-header --tb=short -rfs --timeout=600
```

**EXIT=0 — 18 passed, 0 failed, 0 skipped** (22.1 s) · `staging/p11bd5_new.txt`

Including `test_persist_is_durable_on_postgresql`, which **ran** rather than skipping.

| Group | Tests |
|---|---|
| Structural (W-1 invariant both ways, W-2 existence, the general rule, W-2b) | 4 |
| W-1 behaviour: lookup reached; confirmed order; `not_found`; inconclusive; no venue id; still open; missing executor; never-resubmits sweep | 8 |
| W-2: commit + fresh-session read; failed commit returns False and does not half-apply; no-executor branch end to end; failed persist surfaced in the alert; retries-exhausted branch | 5 |
| W-2 on PostgreSQL (native `executionstatus` enum) | 1 |

Design points worth stating:

- `_StubExecutor.create_order` / `place_order` / `cancel_order` **raise**. "Recovery never
  resubmits" is enforced by the double, so a resubmission is an `AssertionError` rather than
  a quiet pass.
- The injected lookup is a **synchronous** callable, so re-adding `await` fails the
  behaviour tests too, not only the structural ones.
- W-2 reads back through a **second, independent session** over a **file-backed** SQLite
  database. In-memory would share one connection and could not distinguish a committed row
  from a dirty attribute on an identity map — which is precisely the confusion W-2 was.
- `_RecordingRepo` stands in for the repository so the *decision* is observable without
  `resolve_stale_execution`'s PostgreSQL-only SQL, which
  `tests/test_trade07_stale_execution_recovery_postgres.py` already covers against a real
  server.

### 4.2 Proof the tests bind to the defects, not to the implementation

All three defects were reintroduced into the repaired file by textual substitution, the
suite re-run, and the file restored from a hash-verified backup.

| State | Result | Artifact |
|---|---|---|
| Defects present | **15 failed, 3 passed** | `staging/p11bd5_redproof.txt` |
| Repaired | **18 passed** | `staging/p11bd5_new.txt` |

Restored hash `507F5C85A7984200CE4D729651231453A53E85EF12C94F1798882680CACC3ADE` — identical
to the post-fix build. Failing node IDs under the mutation:

```
test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it
test_the_persist_helper_exists_is_synchronous_and_is_not_awaited
test_every_awaited_self_method_in_the_module_is_actually_a_coroutine
test_the_no_executor_branch_reads_no_column_the_model_lacks
test_adjudication_reaches_the_venue_lookup
test_a_confirmed_venue_order_is_completed_with_the_venue_id
test_a_definite_not_found_fails_the_row
test_an_inconclusive_lookup_leaves_the_row_unknown_and_alerts
test_an_order_still_open_at_the_venue_stays_unknown_and_records_the_id
test_persist_commits_and_a_fresh_session_can_read_the_result
test_a_failed_commit_returns_false_and_leaves_the_previous_status
test_the_no_executor_branch_persists_unknown_and_its_reason
test_a_failed_persist_is_surfaced_in_the_alert_not_reported_as_recovery
test_the_retries_exhausted_branch_also_persists
test_persist_is_durable_on_postgresql
```

### 4.3 The three that pass in BOTH states, and why that is correct

`test_a_missing_executor_still_refuses_rather_than_resubmitting`,
`test_a_venue_that_answers_without_an_order_id_stays_unknown` and
`test_adjudication_never_resubmits_on_any_lookup_outcome` pass broken and repaired.

That is the expected result and it is the clearest statement of what W-1 was: **a liveness
defect, not a safety one.** With the defect present every outcome collapsed to `UNKNOWN`,
which is inside the permitted set these three assert. Nothing ever resubmitted. Reported
rather than papered over, because a reader comparing the two runs will notice it.

### 4.4 Adjacent suites — serialized, one process, shared database

Run in a single pytest process (`-p no:randomly`) so no two tests touch the shared
PostgreSQL tables concurrently.

```
.venv\Scripts\python.exe -m pytest `
  tests/test_d5_watchdog_adjudication_repair.py `
  tests/test_trade07_stale_execution_recovery_postgres.py `
  tests/test_dup02_ambiguous_submission_is_not_retried.py `
  tests/test_p10d_exchange_reconciliation_fails_safely.py `
  tests/test_position_delta_race_condition_fix.py `
  tests/test_positions_table_column_agreement.py `
  tests/test_exchange_safety_fix.py `
  -p no:randomly -q --no-header --tb=short -rfs --timeout=900
```

**EXIT=0 — 101 passed, 0 failed, 0 skipped** (43.4 s) · `staging/p11bd5_batch_pg.txt`

These are the five suites that reference `OrderWatchdog` at all, plus the two position/lock
suites that exercise the commit path the new helper joins.

### 4.5 The D4 focused set, re-run unchanged

Same twelve suites as D4 §4.1, same command shape.

**EXIT=0 — 300 passed, 0 failed, 0 skipped** (130.1 s) · `staging/p11bd5_d4set.txt`

**Identical to D4's 300 passed / 0 failed / 0 skipped.** No regression.

### 4.6 Independent static checks (run in parallel with 4.5 — no shared state)

| Check | Result |
|---|---|
| `py_compile` on both changed files | EXIT=0 |
| Module imports cleanly; `iscoroutinefunction(_get_executor_for_order)` | `False` |
| `hasattr(OrderWatchdog, '_persist_order_update')` | `True` |
| `iscoroutinefunction(_persist_order_update)` | `False` |
| `await self._get_executor_for_order` / `await self._persist_order_update` occurrences | **0** |
| Non-comment `order.user_id` occurrences | **0** |

### 4.7 A test-harness trap worth recording

The first run of the new suite was **6 failed / 12 passed**, and every failure was in the
test data, not the code under test. `execution_records.tenant_id` is
`postgresql.UUID(as_uuid=True)`; SQLite does not recognise that type name, so the column
gets **NUMERIC affinity**. A tenant UUID whose 32-char hex form is all digits
(`11111111-1111-…`) is stored as a float and comes back as one, and SQLAlchemy's result
processor fails with `'float' object has no attribute 'replace'`. The fix is a
letter-bearing UUID, and the reason is recorded in a comment beside it so the trap is not
rediscovered. The existing DUP-02 suite avoids it only incidentally, by using
`uuid.uuid4()`.

### 4.8 Explicitly NOT verified

- **No venue was contacted and no venue behaviour is verified.** The tests prove control
  reaches `find_order_by_client_order_id` with an **injected stub** and that the decision
  persists. They prove nothing about any of the six venues.
- **Adjudication still does not work end to end in production** — W-1b (2.4). No test
  claims otherwise.
- The duplicate watchdog at `aerora_quant_platform/…` is untested and unfixed.
- W-3, W-4, W-5, venue policy, new statuses and migrations are untouched by design.

---

## 5. Scope audit

| Check | Result |
|---|---|
| Files changed by D5 | **exactly 2**: `backend_app/backend/order_watchdog.py`, `tests/test_d5_watchdog_adjudication_repair.py` |
| `execution_engine.py` | hash-identical to Phase 0 ✔ (forbidden file, untouched) |
| `execution_record.py` | hash-identical ✔ (forbidden file, untouched) |
| `exchange_executor.py` | hash-identical ✔ (forbidden file, untouched) |
| `tests/conftest.py`, `pytest.ini` | hash-identical ✔ |
| `test_trade07_…`, `test_dup02_…` | hash-identical ✔ — no existing test altered |
| New enum value | **no** — `ExecutionStatus` still has exactly 5 members |
| New migration | **no** — nothing written under `backend_app/migrations` or `alembic` |
| Recovery population widened | **no** — `classify_stale_executions` untouched |
| Safety guard / refusal path removed or weakened | **no** |
| Anything staged | **no** — `git add` was never invoked, in any form |
| Shared staged index | 22 entries, digest `2e994018…7152` — **identical to Phase 0** |
| Shared `HEAD` | `074b772a…` on `main`, `0 0` — unchanged |
| Commit / push / reset / rebase / branch / PR | **none** |
| Exchange-slot, tenant, billing or other concurrent work | **untouched** — see 1.4 |
| Database migrated or mutated | **no** — the one PG test inserts and deletes its own row and its own tenant |

---

## 6. Rollback

Single file, no data involved, no schema involved.

1. **Full revert of D5.** Restore the watchdog from the hash-verified pre-D5 copy or
   re-apply the inverse edits, then delete the new test:
   - the repaired build is `507F5C85A7984200CE4D729651231453A53E85EF12C94F1798882680CACC3ADE`;
     the pre-D5 build was `03AFD5F20C0716E45F1799342CCA4813E73E73F72D582AD3DF250F3EFE418D15`
     (`staging/p11bd5_backup/order_watchdog.py.fixed` holds the repaired build for
     comparison);
   - `Remove-Item tests/test_d5_watchdog_adjudication_repair.py`.
   Reverting restores the pre-D5 behaviour exactly: the awaits return, the persist helper
   disappears, adjudication goes back to "cannot ask" for every row. Nothing persisted by
   the fix needs undoing, because the fix writes no new value — it commits values the code
   already assigned.
2. **Partial revert.** W-1 (three call sites), W-2 (the helper plus two call sites) and
   W-2b (one f-string) are textually independent and can be reverted separately. The
   structural tests will fail for whichever is reverted, which is the intended signal.
3. **No database rollback exists or is needed.** No migration, no new status, no backfill.
4. **Nothing to unstage.** Nothing was staged.

---

## 7. W-1 / W-2 acceptance matrix

| # | Requirement from the brief | Evidence | Verdict |
|---|---|---|---|
| 1 | All three `_get_executor_for_order` call sites traced before choosing | §2.1 table, with each site's enclosing handler and effect | **MET** |
| 2 | Smallest correct change consistent with the async architecture | Three `await`s removed; method left `def` because the body does no I/O and no existing dependency requires awaiting (§3.1) | **MET** |
| 3 | DI, credential, tenant and executor-lifecycle behaviour preserved | Method body byte-unchanged; only its call sites and docstring changed | **MET** |
| 4 | No change to venue routing, client-order-ID formatting, retry policy, order submission | §3.4; `exchange_executor.py` hash-identical | **MET** |
| 5 | Test invoking `_adjudicate_ambiguous_execution` with a stub executor proving `find_order_by_client_order_id` is reached | `test_adjudication_reaches_the_venue_lookup` asserts `executor.lookup_calls == [(execution_id, symbol)]`; fails when the defect is reintroduced | **MET** |
| 6 | Confirmed matching venue order covered | `test_a_confirmed_venue_order_is_completed_with_the_venue_id` → COMPLETED with `VENUE-1` | **MET** |
| 7 | Lookup that cannot safely establish the outcome covered | `test_an_inconclusive_lookup_leaves_the_row_unknown_and_alerts`, `…without_an_order_id_stays_unknown`, `…still_open_at_the_venue…` | **MET** |
| 8 | A failed or inconclusive lookup must not cause blind resubmission | `_StubExecutor` raises from `create_order`/`place_order`/`cancel_order`; `test_adjudication_never_resubmits_on_any_lookup_outcome` sweeps all five outcomes | **MET** |
| 9 | Both `_persist_order_update` call sites traced; existing commit pattern reused | §2.2, §3.2 — `self.db.commit()`, same as `_update_order_from_status`; no new repository abstraction | **MET** |
| 10 | Narrowest correct persistence, existing transaction conventions | One method, commit + rollback-on-failure + `critical` + bool | **MET** |
| 11 | UNKNOWN persisted when adjudication cannot establish a safe outcome | `test_persist_commits_and_a_fresh_session_can_read_the_result`, `test_the_no_executor_branch_persists_unknown_and_its_reason` | **MET** |
| 12 | `exchange_status` / reason persisted | asserted in both of the above | **MET** |
| 13 | A fresh session can read the result after commit | second independent session over file-backed SQLite; repeated on PostgreSQL via `status::text` | **MET** |
| 14 | A persistence failure is visible rather than silently reported as successful recovery | `test_a_failed_commit_returns_false_and_leaves_the_previous_status` + `test_a_failed_persist_is_surfaced_in_the_alert_not_reported_as_recovery` (`_UNPERSISTED_SUFFIX` in the alert) | **MET** |
| 15 | Not assuming an ORM assignment equals a commit | the fresh-session reads are the whole point; §4.1 explains why in-memory SQLite was rejected | **MET** |
| 16 | Tests added in the same change | `tests/test_d5_watchdog_adjudication_repair.py`, 18 tests | **MET** |
| 17 | W-3/W-4/W-5, venue policy, new status, migration not implemented | §5 | **MET** |
| 18 | Adjudication works end to end in production | **NOT MET — W-1b (§2.4).** Out of this phase's authorisation; explicitly not claimed | **OPEN** |

---

## 8. Can W-3 safely proceed?

**Yes — and it should be sequenced after W-1b, not before it.**

W-3 is: teach `_find_stale_orders`, `_find_open_orders` and
`event_router._find_execution_by_order_id` to read `venue_order_id`, because
`execution_records.order_id` is never written on the live path (D4 A-3), which leaves those
three selectors matching nothing.

**Why it is safe now:**

- No conflict with D5. W-3 touches selector predicates and the event lookup; D5 touched
  three call sites, added one method and fixed one f-string. Disjoint.
- D5 removed the hazard that would have made W-3 untestable. Before D5, a row W-3 newly
  selected would flow into `_refresh_order_status`, whose first statement awaited a sync
  method — so W-3 would have widened the population feeding a broken path.
- The commit path W-3's rows depend on now exists and is tested, so a W-3 test can assert
  on persisted state rather than on a dirty attribute.
- D5 leaves a structural guard (`test_every_awaited_self_method_in_the_module_is_actually_a_coroutine`)
  that will catch the same class of defect anywhere W-3 adds code to this module.

**Two conditions:**

1. **W-3 spans two owners.** `order_watchdog.py` (watchdog owner) and
   `backend_app/backend/event_router.py` (execution-state owner). The event-router half
   also needs the FK reasoning from TRADE-12 preserved: `order_id` is FK'd to `orders(id)`
   and `venue_order_id` deliberately is not, so W-3 must **coalesce**, never swap.
2. **W-1b should land first or alongside.** W-3 restores *which rows are selected*; W-1b
   restores *whether the venue can be asked about them*. Doing W-3 alone widens the
   population reaching an adjudicator that still cannot obtain an executor — more rows
   correctly refused, none resolved. The combination is what produces working recovery.

**W-4 and W-5 remain blocked** and their order is unchanged from D4 §6: W-5 (widening
`classify_stale_executions`) must not precede W-4 (an explicit exit rule for `UNKNOWN`), or
rows will be selected into a state nothing can resolve.

---

## 9. Remaining blockers

| # | Item | Severity | Owner | Note |
|---|---|---|---|---|
| 1 | **W-1b** — `app_state.exchange_router` does not exist and `get_exchange_client` has no definition | **P0** | watchdog + credential/DI owner | Adjudication still cannot obtain an executor in production. Recorded in the method docstring. |
| 2 | **W-3** — `order_id` never written; two selectors and the fill path match nothing | **P0** | watchdog + execution-state | Cleared to proceed, §8 |
| 3 | **W-4** — nothing exits `UNKNOWN`; the DUP-02 guard refuses it forever | **P0** | execution-state + coordinator | Unchanged by D5 |
| 4 | **W-6** — `_handle_ccxt_error` substring order relabels a timeout as a determinate rejection | **P0** | execution-state + coordinator | `exchange_executor.py` forbidden here |
| 5 | **A-6** — the `ALERT_*` names in `.env.example` have no consumer; `get_alert_system()` reads different names | **P0** | platform | D5 makes the alert text *correct*; it still may not be delivered |
| 6 | Duplicate watchdog at `aerora_quant_platform/backend_api/backend/order_watchdog.py` carries both defects | P2 | coordinator | Untracked, not imported, excluded by existing scan tests |
| 7 | Venue-side duplicate rejection unverified on all six venues | P1 | coordinator + risk | Unchanged; no sandbox credentials |

---

W-1 **FIXED** · W-2 **FIXED** · W-2b **FIXED** · W-1b **OPEN** · 18 new tests, 15 of which
fail when the defects are reintroduced · 101 and 300 adjacent tests pass with zero skips ·
2 files changed · no status, no migration, no staged change, no commit · shared `HEAD` and
the 22-entry staged index preserved byte-for-byte · no venue contacted · **live trading OFF
/ NOT CERTIFIED**.
