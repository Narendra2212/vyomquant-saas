# Phase 10 — Workstream B — TEST-05 and execution idempotency

**Target:** `tests/property/test_order_idempotence_under_retry.py::test_order_idempotence_under_generated_retry_and_restart_sequences`
**Baseline revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (branch `main`)
**Live trading:** OFF throughout — re-verified, see *Live-trading posture*.
**Stop conditions:** none triggered. No duplicate order submission, no duplicate financial
effect, and no execution defect was found. The property's assertions passed on every example of
every run in this workstream; the only thing that ever failed was the non-vacuity census.

---

## 1. Verdict in one paragraph

TEST-05 is a genuine test defect, not a product defect, but **the Phase 6–9 diagnosis of it was
wrong** and the consequence of that error was material: the flake was attributed to Hypothesis
harvesting constants from a larger `sys.modules` in full-suite runs, and therefore believed not
to reproduce in isolation. It reproduces in isolation at roughly **8%** (12 of 150 measured
100-example runs). The "8/8 passed in fresh subprocesses" evidence Phase 6 recorded had about a
one-in-three chance of occurring at that rate, so it never supported the conclusion drawn from
it. The real cause is that `sequence_length_10` is an **existence** floor on one particular drawn
value, and Hypothesis's tree-guided generation does not redraw the sequence length 100
independent times — it visits a handful of prefixes deeply, a mean of 8.33 of the 9 possible
lengths per run (min 6). The fix forces the existence cases as explicit examples so those floors
hold by construction, and leaves the distribution floors to the generated run, where they still
test the sampler. `max_examples` is unchanged, no floor is lowered, no assertion is relaxed, and
no test is skipped.

---

## 2. Baseline, as found

`git status --porcelain` showed 205 changed entries from parallel workstreams. The targeted file
was **already modified** when this workstream started: the working tree carried an uncommitted
44-line partial fix that stacked three `@example` cases by hand inside the test function. That
work was preserved and extended rather than replaced — see §4.

The targeted test **passed** in isolation as found:

```
.venv\Scripts\python.exe -m pytest tests/property/test_order_idempotence_under_retry.py -q
  -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE
→ 3 passed in 8.31s, exit 0
```

A clean run is not evidence of a fix at an 8% flake rate, so the Phase 9 failure was reproduced
against the **generator** rather than against the clock. Environment: Python 3.12.10,
Hypothesis 6.165.10, pytest 9.1.1.

---

## 3. Root cause

### 3.1 What the census actually asserts

`P12_1_FLOORS` mixes two kinds of claim, and they fail for different reasons:

| kind | buckets | floor | what it claims |
|---|---|---|---|
| **existence** | `sequence_length_10`, `sequence_length_2` | 1 | one specific drawn value appeared at least once |
| **distribution** | `flavour_limit/market`, `step_retry/restart`, `sequence_length_at_or_above_5`, `final_state_*`, `examples` | 15–100 | a fraction of the run had some property |

### 3.2 The measurement

Three probes, all driving the real `submit_retry_restart_sequences()`:

| probe | trials | `sequence_length_10 == 0` |
|---|---|---|
| `staging/probe_test05_census_distribution.py` (minimal imports) | 30 | **3 (10.0%)** |
| `staging/probe_test05_integers_bimodality.py` | 60 | **8 (13.3%)** |
| `staging/probe_test05_prefix_autocorrelation.py` | 60 | **1 (1.7%)** |
| **combined** | **150** | **12 (8.0%)** |

It is the **only** bucket that ever fell short. Measured minima over 60 runs for the others:

```
flavour_limit                  min  31  floor 20      step_retry    min 191  floor 50
flavour_market                 min  21  floor 20      step_restart  min 182  floor 50
sequence_length_at_or_above_5  min  51  floor 20      sequence_length_2 min 2  floor 1
```

### 3.3 Why — isolated to the mechanism

The zero-runs are **bimodal**, not Poisson: a run draws length 10 about 14 times or not at all.
Independent draws cannot do that — at p ≈ 0.14, P(0 in 100) ≈ 5 × 10⁻⁷. A representative zero
run's length histogram and draw order:

```
{2: 18, 3: 12, 4: 14, 6: 9, 7: 24, 8: 15, 9: 8}        # 5 and 10 never drawn
[2,2,7,2,2,8,8,6,6,3,3,3,3,3,3,3,4,4,4,4,4,4,6,6,6,6,6,6,6,7,7,7,7,7,7,7,8,8,8,8]
```

Long runs of one value. Four configurations separate cause from coincidence:

| configuration | zero-trials |
|---|---|
| `st.integers(2, 10)` alone, default phases | **0/60** — exhausts all 9 values, draws 10 exactly once every run |
| `st.integers(2, 10)` alone, generate phase only | **0/60** |
| `st.sampled_from(range(2, 11))` | **0/60** |
| the real composite | **8/60** |

So the integer distribution is **not** the cause. The cause is the composite's prefix structure:
`flavour` and `length` are drawn first, then a 1–9 element `st.lists` and a *filtered* `st.text`
key. Hypothesis draws a novel prefix and then explores many children beneath it, varying the deep
later draws while the early ones stay fixed. `length` is therefore visited a handful of times
deeply, not redrawn 100 times — mean 8.33 distinct lengths per run, min 6 of 9. An existence floor
on one length is a bet on which prefixes that run happened to visit.

This also corrects the record: the `flavour_limit occurred 18 time(s), floor 20` manifestation
reported in Phases 7–8 belongs to the **pre-fix draw order**, before `flavour` was moved to be the
first draw (a change already committed, documented in the module docstring). Post-fix, `flavour_limit`
never fell below 31 in 60 runs.

### 3.4 Raising `max_examples` is not a fix — measured, not asserted

With floors scaled to the budget so the comparison is like-for-like:

| `max_examples` | `sequence_length_10` shortfall | distinct lengths/run |
|---|---|---|
| 100 | 1/60 | mean 8.33, min 6 |
| 200 | **1/30 — still short** | mean 8.87, min 8 |
| 400 | 0/30 | mean 9.00, min 9 |

More examples deepen exploration under already-visited prefixes. At 400 the flake is rarer, not
eliminated, at four times the runtime. `max_examples` stays at 100, and a guard now pins it there.

### 3.5 Classification

Probabilistic sample coverage **interacting with a flawed coverage assertion**: the floor is
written as if the draws were independent, and they are not. Not Hypothesis filtering or shrinking,
not test configuration, and **not** an idempotency defect — the property's own assertions never
failed in any run in this workstream.

---

## 4. Files changed

| file | change |
|---|---|
| `tests/property/test_order_idempotence_under_retry.py` | the only test or product file touched. +257/−9 against `HEAD`, of which 44 lines were the partial fix already in the working tree. |

No product code, no migration, and no shared artifact was modified. Nothing owned by another
workstream was touched: tenant-isolation tests and fixtures, quota enforcement, exchange
connection-pool code, fee/referral/reconciliation code, shared migrations, and the global finding
registries and scorecards are all unchanged. **`staging/FINAL_LAUNCH_SCORECARD.md` and
`staging/full_audit_results.json` still carry TEST-05 as `REPORTED`/open — updating them needs
coordinator authorisation and was deliberately not done.**

New evidence files, all under the already-untracked `staging/`:

```
probe_test05_census_distribution.py      the census distribution over repeated runs
probe_test05_integers_bimodality.py      isolates the cause to the composite's prefix structure
probe_test05_prefix_autocorrelation.py   per-bucket shortfall rates + the max_examples sweep
probe_test05_forced_examples_run.py      proves the @example cases execute
probe_test05_real_census_contents.py     inspects the REAL test's census, with a control
falsify_test05_coverage_guards.py        13 falsifications of the two new guards
run_test05_determinism.ps1               repeated independent-process runs
test05_determinism_P10B{1,2,3}.txt       their results
suite_P10B_{RETRY,MONEY,PROPDIR,PAIR,SEP}.txt   related-suite results
```

### What changed in the test module

1. **`_census_buckets_for(seq)`** — one declaration of "which census buckets does this sequence's
   shape contribute". The property body and both new guards read it, so there is no second copy to
   drift. The per-step `recorder.mark` moved out of the submission loop into this function; counts
   are identical, and the census is only asserted after a run completes successfully.
2. **`EXISTENCE_FLOORS`** — derived from `P12_1_FLOORS` (`floor <= 1`), not restated, so a newly
   added existence bucket joins it automatically.
3. **`FORCED_LEN_10` / `FORCED_LEN_5` / `FORCED_LEN_2`, `DETERMINISTIC_COVERAGE`** — the three
   boundary sequences, lifted from local variables to module level so guards can read them.
4. **`_forcing`** — registers every `DETERMINISTIC_COVERAGE` entry as an `@example`. This replaced
   a hand-stacked `@example` list, which was a real hole found during verification: the stacked
   decorators named the constants individually, so the tuple the guards check could have
   desynchronised from the cases Hypothesis actually runs. Now they are the same objects.
5. **Two new guards** (§5.2).

---

## 5. Why the fix is correct

### 5.1 The forced cases are the same path, not a weaker one

A forced sequence goes through exactly the same `_assert_the_sequence_yields_exactly_one_order` as
a generated one: the real `paper_simulator.submit_intent`, the same `FakeSupabase` persistence
double, the same duplicate-answer oracle read off the stored row, the same
`_snapshot(supabase) == after_first` financial-effect oracle, and the same direct
`uq_paper_order_idem` probe at the end. Nothing is mocked, and the operation whose idempotency is
under test is the one being driven.

Each forced sequence is also a sequence the generator could itself have produced — asserted, not
asserted-in-prose (§5.2).

### 5.2 The mechanism is self-checking

`test_every_existence_floor_is_forced_rather_than_left_to_the_draw` reads `EXISTENCE_FLOORS` off
the floors and requires `DETERMINISTIC_COVERAGE` to cover each one, and additionally pins
`Phase.explicit` (without it, `@example` cases never run and the floors silently become bets again)
and `max_examples == 100` (so the forbidden shortcut cannot be taken quietly).

`test_the_forced_coverage_sequences_are_legal_generator_draws` asserts every field of every forced
sequence comes from the pool the generator draws it from, the length is within `[2, 10]`, the steps
are all in `STEP_KINDS`, the keys are distinct, and each key satisfies both the generator's filter
and `chk_paper_order_idem_len` (1–128,
`backend_app/migrations/009_paper_trading.sql:737-738`).

Both guards were **falsified**, 13 cases, all behaving as required
(`staging/falsify_test05_coverage_guards.py`, exit 0):

| mutation | guard fires |
|---|---|
| forced coverage removed entirely | yes |
| only the length-10 case removed (the exact Phase 9 shortfall) | yes |
| a new existence floor added with no forced case | yes |
| `Phase.explicit` removed from the settings | yes |
| `max_examples` raised to 400 instead of forcing the case | yes |
| forced quantity / step kind / length / key-length / key-whitespace outside the generator | yes (5) |
| two forced sequences sharing one key | yes |
| both guards unmutated | pass |

### 5.3 The forced cases demonstrably land in the census

`--hypothesis-show-statistics` prints only a generate-phase line for this property, so it does not
by itself establish that the explicit examples contributed. Two direct measurements do.

`staging/probe_test05_forced_examples_run.py`, driven at `max_examples=1`:

```
total invocations: 4        lengths seen: {2: 2, 5: 1, 10: 1}
invocation order: ['forced-len10-rrrrrrrrrr', 'forced-len5-restart', 'forced-len2-retry', '0']
ALL THREE FORCED EXAMPLES EXECUTED: True
```

`staging/probe_test05_real_census_contents.py` runs the **real collected test** and reads its
`Recorder`, with a genuine control (emptying `DETERMINISTIC_COVERAGE` really does remove the
examples, now that `_forcing` drives them):

```
as shipped:  examples 103   sequence_length_10  10   sequence_length_2  17   verdict PASSED
control:     examples 100   sequence_length_10   2   sequence_length_2  18   verdict PASSED
             delta +3 = exactly the forced set
sequence_length_10  floor 1, forced alone supplies 1 -> GUARANTEED
sequence_length_2   floor 1, forced alone supplies 1 -> GUARANTEED
```

The control run drawing only 2 length-10s is the hazard in one line: the generated run alone sits
near the edge of a floor of 1.

### 5.4 Distribution floors deliberately left probabilistic

They are not forced, because forcing them would convert a guard on the sampler into a guard on
this file's own literals — a broken generator would still clear a floor it was handed. They have
1.05×–3.8× measured headroom and never fell short in 150 runs. The two kinds and the reason they
cannot be guarded the same way are documented at `EXISTENCE_FLOORS` in the module.

---

## 6. Before / after test results

All commands run from the repository root with `AERORA_TEST_DATABASE_URL` and `DATABASE_URL`
cleared — the CI unit-test lane — via `.venv\Scripts\python.exe -m pytest … -q -p no:cacheprovider
-p no:warnings -o addopts=""`.

### 6.1 The targeted test

| | result | duration |
|---|---|---|
| before (as found, partial fix present) | **3 passed**, exit 0 | 8.31 s |
| before (generator measured, `HEAD` census) | **fails ~8% of runs**, 12/150 | — |
| after | **5 passed**, exit 0 | 10.3 s |

### 6.2 Determinism — repeated independent processes

Separate processes per run, because the pre-fix flake was per-run.

| batch | runs | result |
|---|---|---|
| `P10B1` | 30 | **30 pass / 0 fail** |
| `P10B2` | 60 | **60 pass / 0 fail** |
| `P10B3` (final mechanism) | 30 | **30 pass / 0 fail** |
| **total** | **120** | **120 pass / 0 fail** |

At the measured 8% pre-fix rate, 120 consecutive clean runs has probability ≈ 4 × 10⁻⁵. Combined
with §5.3 (the floors are met by construction), the census no longer depends on the draw.

### 6.3 Related retry, restart and idempotency tests

`staging/suite_P10B_RETRY.txt` — **165 passed, exit 0, 76.7 s**:
`tests/crash_recovery/test_worker_crash_mid_submission.py`,
`tests/property/test_paper_idempotence.py`, `tests/test_atomic_idempotency_fix.py`,
`tests/test_idempotent_submission_fails_closed.py`,
`tests/test_order_normalization_and_venue_idempotency.py`,
`tests/test_idempotency_lua_prefix_agreement.py`, `tests/test_chaos_recovery.py`,
`tests/test_signal_lifecycle_idempotency_migration.py`,
`tests/test_task_9_1_signal_idempotency_key.py`,
`tests/property/test_expiry_sweep_idempotence.py`,
`tests/property/test_settlement_idempotence.py`.

### 6.4 Financial-side-effect and order-submission tests

**338 passed, 4 skipped, exit 0, 278 s**: `tests/test_paper_order_lifecycle_writes.py`,
`tests/test_single_execution_pipeline.py`,
`tests/test_execution_engine_accounting_identity.py`,
`tests/test_simulated_orders_reach_the_simulator.py`,
`tests/test_atomic_order_cancellation_fix.py`, `tests/test_paper_live_separation.py`,
`tests/test_paper_no_random.py`, `tests/test_execution_paths_that_claim_an_exchange.py`,
`tests/test_trade04_live_order_gate_at_venue_boundary.py`,
`tests/property/test_paper_order_lifecycle.py`. The 4 skips are database-gated and skip by design
in this lane.

### 6.5 Whole property suite and the coverage scoreboard

`staging/suite_P10B_PROPDIR.txt` — `tests/property/` → **138 passed, 1 xfailed, 3 xpassed, exit 0**,
1069 s. `tests/property/test_property_coverage.py` (the Requirement 29.3 scoreboard, which
AST-walks every property module) → **2 passed**; the two new guards are not `test_p{n}_` functions
and do not disturb it.

### 6.6 Two environment artifacts, both non-reproducing — reported, not hidden

Neither is in the targeted file, neither reproduced, and neither is caused by this change. Both
occurred while the machine was contended (this workstream's own background runs plus concurrent
python processes from other tabs, confirmed by process start times).

**(a) `tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order`**
failed once, in a `tests/property/` run that overlapped a 30-process determinism loop. It is a
concurrency property that asserts exact backoff delays (`race.starved_sleeps.delays == [0.05, 0.10]`),
so it is timing-sensitive. Not reproduced in four subsequent measurements:

| check | result |
|---|---|
| the module alone | 6 passed |
| this workstream's module then confluence, in collection order | 11 passed |
| `tests/property/` uncontended | 138 passed, exit 0 |
| every Phase 6–9 full-run junit record (`staging/fullreg_*.xml`) | passing, no failure element |

**(b) `tests/test_paper_live_separation.py::TestPlaceOrderNeverConstructsTheLiveAdapter::test_a_market_order`**
failed once with **`MemoryError`**, raised inside a lazy third-party import
(`place_order` → `routers.risk` → `core.dependencies` → `supabase` → `gotrue` → pydantic model
construction). A resource exhaustion, not an assertion failure. Re-ran the class: **4 passed**;
re-ran the whole group: **338 passed**.

Flagging both to the coordinator as environment/scheduling observations: running these suites
concurrently on this machine can produce false reds. Neither is owned by this workstream and
neither was modified.

---

## 7. Retry, restart and financial-side-effect coverage

What the property itself exercises, and where the rest genuinely lives. The property is the
**paper** path; it constructs no live adapter, and that is pinned structurally by
`test_this_module_never_constructs_a_live_venue_adapter`.

| requirement | in this property | elsewhere (verified passing, §6.3) |
|---|---|---|
| duplicate requests | **yes** — every step after the first repeats the same key and the same order parameters; 2–10 submissions per example | `test_paper_idempotence.py` P-22 |
| retry after transient failures | **no** — no fault injection on this path | `test_a_transient_outage_is_retried_and_then_reconciled`, `test_an_unreachable_exchange_stops_at_three_attempts_and_marks_the_signal`, `test_the_thirty_second_budget_cuts_the_attempts_short` |
| restart and recovery | **yes** — a `restart` step rebuilds `SessionConfig` independently and copies the intent, so nothing a crashed process held is reused; forced length-10 case alternates 5 restarts with 4 retries | `test_a_resumed_worker_that_submits_anyway_still_creates_no_second_order`, `test_the_marker_is_written_once_however_many_times_a_worker_restarts`, `test_recovery_refuses_resubmission_when_an_order_already_exists` |
| idempotency-key persistence | **yes** — the stored row's `idempotency_key` is asserted unchanged after every repeat, and `uq_paper_order_idem` is asked **directly** at the end of every sequence (a second `repo.insert_order` under the same key must raise `PaperConcurrencyConflict` and persist nothing) | `test_the_durable_unique_index_refuses_a_second_row_for_the_same_key` |
| repeated processing of the same logical order | **yes** — the sequence *shape* is drawn (length, which kind at each position), not hand-built | — |
| duplicate venue-submission prevention | **not applicable** — the paper path never reaches a venue | `test_the_venue_itself_refuses_a_second_order_under_the_same_key`. **No claim of venue-level exactly-once execution is made here.** That test is another module's, is not re-derived here, and what a venue double refuses is not a protocol guarantee. |
| duplicate financial-effect prevention | **yes** — after every repeat, `_snapshot(supabase) == after_first` over **accounts, orders, fills, positions, balance_events, trades and equity_snapshots**. An acknowledgement of an already-placed order may not move a balance, a position, a fill or a trade. | `test_execution_engine_accounting_identity.py`, `test_paper_order_lifecycle_writes.py` (§6.4) |
| partial failure and recovery | **no** — every step is awaited to completion | `test_recovery_reconciles_a_partial_fill_as_a_partial_fill`, `test_a_crash_before_the_exchange_call_leaves_the_signal_submittable`, `test_recovery_reconciles_a_fill_that_happened_while_the_worker_was_down` |

Nothing was weakened to achieve this. The forced cases **added** coverage of the length boundaries
on every run — previously the maximum-length sequence was absent from roughly one run in twelve.

---

## 8. Residual risks

1. **The distribution floors remain probabilistic.** `flavour_*` (20), `step_*` (50),
   `sequence_length_at_or_above_5` (20) and `final_state_*` (15) are satisfied by the generated run
   with 1.05×–3.8× measured headroom and never fell short in 150 runs, but they are not guaranteed.
   `flavour_market` at min 21 against floor 20 is the thinnest. This is deliberate (§5.4); if one
   ever reds, the fix is to re-measure the draw order as the module docstring records being done
   before, not to lower the floor.
2. **The prefix-visitation mechanism is a Hypothesis implementation detail.** A future Hypothesis
   release could change generation and shift every number here. The guards would still hold, since
   the existence floors no longer depend on generation at all.
3. **Sequential, not concurrent** — unchanged and still stated in the module: a genuinely concurrent
   pair of first submissions racing the in-code probe is P-23's subject. The direct
   `uq_paper_order_idem` probe is what holds under an interleaving this file cannot produce.
4. **The same defect class may exist in sibling property modules.** `test_paper_confluence.py`
   (P-23) and `test_paper_idempotence.py` (P-21/P-22) use the same `Recorder`. P-23's own comment
   describes its two-way floors as drawn rather than forced. They were not audited for existence
   floors here — out of scope, flagged for the coordinator.
5. **`tests/property/` takes ~18 minutes**, and the targeted file's property drives up to 10 real
   submissions per example. The three forced cases add 3 examples (~0.2 s).
6. **Unverified:** nothing in this workstream was exercised against PostgreSQL. The property drives
   the `FakeSupabase` double, which is the harness the whole paper property suite uses; the
   `uq_paper_order_idem` and `chk_paper_order_idem_len` claims are read out of
   `backend_app/migrations/009_paper_trading.sql` and asserted against that double, not against a
   live database.

---

## 9. Live-trading posture

Unchanged and re-verified. `staging/verify_live_trading_off.py` → *"every case refused live
execution. Live customer trading is OFF"*, including the decisive case with both `VYOMQUANT_MODE=live`
and `VYOMQUANT_ENABLE_LIVE_TRADING=true` set under `ENV=production`, which
`ExecutionFlags.LIVE_TRADING_ENABLED` refuses as a hardcoded `bool = False`. Enabling live trading
requires a code change, not a configuration change. Every test run in this workstream used the
no-database CI unit lane with `AERORA_TEST_DATABASE_URL` and `DATABASE_URL` cleared, and the
targeted module constructs no live venue adapter.

---

## 10. Commit / branch reference

**No commit was created and no branch was cut.** The working tree carries 240+ changed entries
from parallel workstreams, so any commit from this tab would capture other tabs' in-flight work.
The change is left staged-in-working-tree at baseline `074b772ab16cece62cbfca0f2b78497be2ae56d5`,
confined to one file:

```
tests/property/test_order_idempotence_under_retry.py   +257 / -9
```

Isolating it is a one-line operation when the coordinator wants it:

```
git checkout -b phase10/workstream-b-test05
git add tests/property/test_order_idempotence_under_retry.py
git commit -m "TEST-05: force the census existence floors instead of drawing for them"
```

## 11. Asks of the coordinator

1. **Authorisation to close TEST-05** in `staging/FINAL_LAUNCH_SCORECARD.md` (line 127) and
   `staging/full_audit_results.json` (the `TEST-05` entry). Both still read `REPORTED`/open and
   both carry the superseded root cause ("Hypothesis 6.x harvests constants … which is why a 4-,
   136- or 191-test selection cannot reproduce what 13,534 do"). Not edited without approval.
2. **A decision on whether to audit P-21/P-22/P-23 for existence floors** (residual risk 4).
3. **Noting the two environment artifacts** in §6.6 — these suites can produce false reds when run
   concurrently on this machine.

---
---

# Coordinator pass — verification, record update, sibling audit

Baseline revision unchanged: `074b772ab16cece62cbfca0f2b78497be2ae56d5`, branch `main`.
Working tree at 243 entries (205 at workstream start; the growth is other tabs). The index was
not touched: all 22 staged entries are the pre-existing other-workstream set (tenant isolation,
RLS, terraform, the security review), and the owned test file is **not** staged.

## Scope 1 — TEST-05 verification: PASSED, no discrepancy with the report

Implementation checked against the claims, not taken on trust:

| claim | verified |
|---|---|
| forced examples driven by `DETERMINISTIC_COVERAGE` through `_forcing` | yes — `@_forcing` sits between `@PROPERTY_SETTINGS` and `@given`; `_forcing` iterates the tuple and applies `example(seq=...)`; no hand-stacked `@example` remains anywhere in the module |
| `EXISTENCE_FLOORS` derived, not restated | yes — `tuple(sorted(key for key, floor in P12_1_FLOORS.items() if floor <= 1))` |
| `max_examples` unchanged at 100 | yes, and pinned by a guard assertion |
| both coverage guards present and effective | yes — see falsification below |

```
.venv\Scripts\python.exe -m pytest tests/property/test_order_idempotence_under_retry.py -v
  -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE
→ 5 passed in 13.84s, exit 0
   test_order_idempotence_under_generated_retry_and_restart_sequences  PASSED
   test_this_module_never_constructs_a_live_venue_adapter              PASSED
   test_every_existence_floor_is_forced_rather_than_left_to_the_draw   PASSED
   test_the_forced_coverage_sequences_are_legal_generator_draws        PASSED
   test_the_sequence_generator_never_places_a_second_first_submission  PASSED

.venv\Scripts\python.exe staging\falsify_test05_coverage_guards.py
→ ALL GUARD FALSIFICATIONS BEHAVED AS REQUIRED: True, exit 0  (13 cases)

.venv\Scripts\python.exe staging\probe_test05_real_census_contents.py
→ sequence_length_10: floor 1, forced alone supplies 1, run saw 6 -> GUARANTEED
   sequence_length_2:  floor 1, forced alone supplies 1, run saw 3 -> GUARANTEED
   EVERY EXISTENCE FLOOR IS SATISFIED BY THE FORCED SET ALONE: True, exit 0
```

That run saw only 6 length-10 and 3 length-2 draws from generation, which is the hazard in one
line: the generated run alone sits near a floor of 1.

**One wart fixed in this pass.** `test_the_forced_coverage_sequences_are_legal_generator_draws`
built a local `lengths` set that was populated and never read — dead code implying a diversity
check that was not made. Removed; the substantive claim is already covered by the existence-floor
guard. Diff for the owned file is now **+255 / −9** (was +257 / −9). Re-verified after the
removal: 5 passed, 13/13 falsifications.

## Scope 2 — TEST-05 records updated

### `staging/full_audit_results.json`

TEST-05 moved from `remediation.open` to `remediation.resolved` in the file's own established
shape (`status` / `resolution` / `evidence`, `status: "FIXED"`), and `remediation.corrections[21]`
(`CORR-P10-02`) had its `why`, `fix` and `lesson` corrected — that entry still asserted the
`st.integers(2, 10)` low-end-bias explanation, which this workstream's measurements disprove.

No new status vocabulary was introduced: `FIXED` is the value used by all 57 pre-existing
`resolved` entries.

```
.venv\Scripts\python.exe staging\verify_phase10_consistency.py
→ ALL CONSISTENCY ASSERTIONS PASSED, exit 0
   full_audit_results.json  nonce=P10FINAL  resolved=58  open=25   orphans=0
JSON parses; TEST-05 in resolved, not in open; keys = [evidence, resolution, status]
```

**Diff scope proved, not asserted.** `staging/` is untracked, so `git diff` is empty by
construction. Instead, the three sibling audit files still carry the pre-edit `remediation` block
(it was byte-identical across all four before this pass, same nonce `P10FINAL`, same hash), which
makes them an exact control. Structural diff of the edited block against `money_flow_audit.json`:

```
('CHANGED',            '/corrections[21]/why')
('CHANGED',            '/corrections[21]/fix')
('CHANGED',            '/corrections[21]/lesson')
('REMOVED_IN_EDITED',  '/open/TEST-05')
('ADDED_IN_EDITED',    '/resolved/TEST-05')
total differences: 5
```

Five differences, all TEST-05. **No unrelated change.**

### `staging/FINAL_LAUNCH_SCORECARD.md`

* **L127**, the "Open findings, current" row: status `REPORTED` → **`**FIXED** (Phase 10,
  Workstream B)`**, and the finding text rewritten — it previously read "Hypothesis
  constant-census flake", which encodes the superseded cause. It now states the existence-floor /
  tree-guided-generation mechanism, the 12-of-150 isolation measurement, the fix, and explicitly
  that the idempotency property never failed.
* **L1185**, the Phase 6 addendum narrative: **not rewritten** — it is a record of what the Phase 6
  run observed. Annotated instead with the document's own `>` superseded-marker convention,
  naming which inference was wrong (that the flake needed a full suite) and which parts held
  ("flaky by construction", "the fix is in the generator").

### Mirror divergence — needs authorization

The `remediation` block is **replicated byte-identically across four files**, all checked by
`verify_phase10_consistency.py`: `full_audit_results.json`, `marketplace_audit.json`,
`money_flow_audit.json`, `trading_audit.json`. Scope 2 authorized one of them, so the other three
still show `resolved=57 open=26` with TEST-05 open and `CORR-P10-02` carrying the superseded
cause. The verifier checks each file independently and still passes, so this is not detected
automatically. **Flagged for authorization**, not actioned. Remedy is to apply the identical two
changes to the other three files.

## Scope 3 — sibling property coverage audit (read-only, nothing changed)

**Headline: no sibling carries TEST-05's shape.** TEST-05's defect was a census floor of **1** on
one *drawn* value. The lowest floor anywhere in P-21, P-22 or P-23 is **20**. Measured directly by
running each real property and reading the `Recorder` it built
(`staging/probe_phase10_sibling_census_margins.py`), plus a generator-only sweep
(`staging/probe_phase10_sibling_drawn_buckets.py`).

The useful distinction is not the floor's size but whether the case it counts is **forced** into
every example — in which case the floor is an arithmetic consequence of the forcing and no draw
can reduce it — or **drawn**, in which case the margin is the whole question.

| property | buckets | floors ≤ 1 | forced / guaranteed | genuinely drawn | min margin among drawn | verdict |
|---|---|---|---|---|---|---|
| **P-21** `test_p21_duplicate_fill_event_changes_nothing` | 27 | **none** | **27** | **0** | n/a | **no risk** |
| **P-22** `test_p22_duplicate_order_intent_yields_one_order` | 18 | **none** | 16 + 2 with a forced minimum | 0 effectively | 1.23x, cannot go below 1.00x | **no risk** |
| **P-23** `test_p23_concurrent_intents_match_a_sequential_order` | 23 | **none** | 19 | **4** | **1.20x** | **one item to watch** |

### P-21 — every floor is forced

In-test, 58.3 s, PASSED. Every example drives all **four** staged scenarios times all **four**
repetition bands, so `scenario_*` (100/100 each), `repetition_1` / `_2` / `_three_or_four` /
`_five_or_more` (400/400 each), `runs` (1600/1600), `durable_uq_paper_fill_event_refused`
(400/400), `final_state_FILLED` / `_PARTIALLY_FILLED` (200/200), `position_closed_to_zero`,
`position_partially_closed`, `prior_fill_on_the_same_order`, `realized_pnl_positive`,
`realized_pnl_negative` and `trade_row_written` (100/100 each) are all exact consequences of that
forcing. Widest margins: `repeat_met_the_duplicate_guard` and `repeat_met_the_terminal_guard` at
7.68x. The fee/slippage pairs sit at 2.00x.

> **Correction to my own first measurement.** A generator-only sweep appeared to show all four
> P-21 repetition bands short at 0.25x in 20/20 runs. That was a **probe defect, not a finding**:
> the probe counted `case.repetitions` once per example while the real property marks them once
> per *scenario*, so it under-counted by exactly the factor of four. The in-test run above shows
> 400/400 on each band. Recorded rather than deleted, because a reader checking the probe file
> would otherwise hit the same wrong conclusion.

### P-22 — `side_buy` / `side_sell` are drawn but have a forced floor

`side` is drawn (`st.sampled_from(("buy", "sell"))`), and `side_buy` / `side_sell` carry floors of
`EXAMPLES` = 100 each — which looks unsatisfiable for a single two-way draw over 100 examples. It
is not, and the reason is structural: `_first_intent` gives the **limit** flavour `case.side` and
the **market** flavour `case.other_side()`, and all three flavours run on every example. So every
example marks **both** sides at least once, and each bucket's guaranteed minimum is exactly
`EXAMPLES`. Measured in-test 166 / 134; generator-only over 20 runs, min 155 / 123, **0/20
shortfalls**. Not at risk from a draw.

*Fragility worth noting, not a defect:* the floor sits at precisely the guaranteed minimum, so if
a future edit made the market flavour use `case.side` instead of `other_side()`, these two floors
would become a drawn claim needing 100 of ~300 marks — or, if the mark count per example dropped
to one, **arithmetically unsatisfiable**. It would fail loudly and immediately rather than
intermittently, which is the better failure mode.

### P-23 — four genuinely drawn floors, and the `side` draw is skewed

P-23 already documents this distinction itself, in the floors block: *"These four are DRAWN
two-way choices, not forced cases, and the floors say so … Everything above this line is forced
into every example and its floor is the arithmetic consequence of that forcing."* The four are
`side_buy`, `side_sell`, `limit2_was_a_second_limit_order`, `limit2_was_a_second_market_order`,
each with floor `EXAMPLES // 5` = **20**, and the stated rationale is that a fair draw gives
"about fifty of each", making 20 a fifth of expectation.

**That premise does not hold for `side`.** In-test, 8 independent runs, all PASSED:

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | mean | min margin |
|---|---|---|---|---|---|---|---|---|---|---|
| `side_buy` | 67 | 68 | 72 | 64 | 75 | 66 | 76 | 49 | 67.1 | 2.45x |
| `side_sell` | 33 | 32 | 28 | 36 | **25** | 34 | **24** | 51 | 32.9 | **1.20x** |
| `limit2` limit | 54 | 74 | 37 | 70 | 61 | 55 | 61 | 56 | 58.5 | 1.85x |
| `limit2` market | 46 | 26 | 63 | 30 | 39 | 45 | 39 | 44 | 41.5 | 1.30x |

`side` lands near **67/33**, not 50/50, so `side_sell`'s real margin is 1.20x–1.80x rather than the
~2.5x its floor's reasoning assumes. No shortfall in 8 in-test runs. A generator-only sweep (20
runs, strategy driven without the property body) put `side_sell` as low as **14 — below the floor
— in 2 of 20 runs**; that is a measurement of the *strategy*, not of the in-test distribution, and
the two measurably differ, so it is reported as a warning signal rather than as a failure rate.

This is the same **draw-order skew** that `test_order_idempotence_under_retry.py` already
documented and fixed once in its own generator: with `flavour` drawn immediately after a filtered
`st.text` draw, empirical sampling put one branch at roughly 30% instead of 50/50, and moving the
draw to the front of the composite removed it. In `confluent_intent_sets`, `side` is drawn after a
filtered key draw and `limit2_order_type` is drawn later still, immediately before a
variable-length `frozenset` draw.

**Proposed fix, presented for authorization and NOT applied:** move the `side` and
`limit2_order_type` draws to the front of `confluent_intent_sets`, ahead of the filtered and
variable-length draws — the identical, already-proven remedy. It changes no assertion, no floor
and no example count. Re-measure `side_sell` over ≥20 in-test runs afterwards; expect the mean to
move from ~33 toward ~50 and the minimum margin from 1.20x to ~2.2x. P-23 runs 60–120 s per
execution, so ~30–60 min of measurement.

## Live-trading posture

Unchanged. No execution flag, product module, migration or database configuration was touched in
this pass. Every run used the no-database CI unit lane with `AERORA_TEST_DATABASE_URL` and
`DATABASE_URL` cleared.

## Still true, and still unverified

* **No PostgreSQL verification** in this workstream. The properties drive the `FakeSupabase`
  double; the `uq_paper_order_idem` and `chk_paper_order_idem_len` claims are read out of
  `backend_app/migrations/009_paper_trading.sql`.
* **No venue-level exactly-once claim.** The paper path never reaches a venue.
* **The two transient environment failures from the first pass remain on the record** (§6.6): the
  P-23 confluence failure under load (not reproduced in 4 subsequent checks, and PASSED in all 8
  in-test runs above) and the `MemoryError` in `test_paper_live_separation.py` (passed on re-run,
  338 passed for the whole group). Both were resource/scheduling artifacts on a contended machine.
