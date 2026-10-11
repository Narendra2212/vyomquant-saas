# Phase 10 — Workstream P-23C — deterministic `side` coverage

**Subject:** `tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order`
**Prior evidence:** `staging/PHASE10_P23B_DETERMINISTIC_COVERAGE_REPORT.md`, `staging/PHASE10_P23_DRAW_ORDER_REPORT.md`
**Live trading:** OFF / NOT CERTIFIED. Paper trading: NOT CLASSIFIED. Launch status unchanged.
**Audit JSON mirrors:** not touched.

> Property-test coverage does **not** establish exchange correctness, exactly-once execution,
> paper-trading certification, or live-trading readiness. Nothing in this report changes any of
> those. Live trading remains **OFF / NOT CERTIFIED**.

---

## Phase 0 — read-only baseline (recorded before any edit)

### 0.1 Git state

| | |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| branch | `main`, upstream `origin/main`, `## main...origin/main` (no divergence markers) |
| staged entries | **22** |
| working-tree entries | 254 |
| untracked paths | 3077 |

The 22 staged entries are the same pre-existing other-workstream set recorded in the previous two
reports, verified by name: `.github/workflows/05-security.yml`, `.kiro/security-template.md`,
`algo22-terminal/src/pages/ExchangeManager.jsx`, `backend_app/core/dependencies.py`,
`backend_app/core/tenant_db_context.py`, `backend_app/routers/auth.py`,
`backend_app/routers/exchange.py`, two ADRs, the tenant-isolation matrix, the security review,
`scripts/verify_rls*.py`, four `terraform/*` files and five tenant/RLS/worker test files.
**No index entry will be changed by this task.**

Concurrent work has grown substantially since the previous task — unstaged diffs now include
`backend_app/backend/exchange_executor.py` (+1188), `backend_app/core/execution_engine.py`
(+911), `backend_app/core/models/execution_record.py` (+721), `backend_app/routers/billing.py`
(+700), `backend_app/core/global_safety.py` (+653), `backend_app/backend/paper_trading_service.py`
(+615) and ~90 others. **None of these is touched by this task.** Within `tests/property/`:

```
255   9  tests/property/test_order_idempotence_under_retry.py   (TEST-05 task,      untouched here)
237  17  tests/property/test_paper_confluence.py                (P-23 + P-23B,      THIS TASK'S FILE)
 69   9  tests/property/test_protected_logic_containment.py     (other workstream,  untouched)
 34   7  tests/property/test_tenant_isolation_matrix.py         (other workstream,  untouched)
```

### 0.2 The file matches the reported baseline — no stop condition

`tests/property/test_paper_confluence.py` is at **+237 / −17**, exactly as
`PHASE10_P23B_DETERMINISTIC_COVERAGE_REPORT.md` §8 records. Every element the two prior reports
describe is present and unmodified by any other workstream:

| element | line | state |
|---|---|---|
| `LIMIT2_ORDER_TYPES: Tuple[str, ...] = ("limit", "market")` | 354 | present |
| `_ConfluenceCase.with_limit2_order_type` | 410 | present |
| `side = draw(st.sampled_from(("buy", "sell")))` — the P-23 draw-order fix, still first | 582 | present |
| `_pinned_limit2_order_type = draw(...)` — the deliberate choice-sequence pin | 583 | present |
| `limit2_order_type=LIMIT2_ORDER_TYPES[0]` in the constructor call | 613 | present |
| `for limit2_order_type in LIMIT2_ORDER_TYPES:` + `with_limit2_order_type` in the body | 1467–1468 | present |
| `test_the_forced_limit2_dimension_is_pinned_not_drawn` (the AST guard) | 1502 | present |

The functional diff is exactly the P-23 draw-order change plus the P-23B forcing change. Nothing
material differs from the reports, so **no stop was triggered**.

### 0.3 Implementation and precedents read

* `test_paper_confluence.py` in full for the relevant parts: `_ConfluenceCase` (`__slots__`,
  constructor, `key`, `intent`, `__repr__`, `with_limit2_order_type`), `confluent_intent_sets`
  and its draw order, the forced order-type loop in `check`, `_sequential_oracle`
  (24 `itertools.permutations`), `_run_concurrently`,
  `_assert_one_concurrent_run_matches_the_oracle`, `P23_FLOORS`, `P23_LABELS`, and the module's
  structural guards.
* `test_paper_idempotence.py` — P-22 forces three `SUBMIT_FLAVOURS` per example via
  `for flavour in SUBMIT_FLAVOURS:` calling the whole assertion function per value; P-21 forces
  four scenarios × four repetition bands. Both convert drawn dimensions into forced ones so their
  floors become arithmetic consequences.
* `test_order_idempotence_under_retry.py` — TEST-05's `DETERMINISTIC_COVERAGE` + `_forcing`
  convention, which forces **existence** floors of 1 via `@example`.
* `paper_census.py` — `Recorder.mark` refuses unknown buckets; `assert_not_vacuous` fails with
  *"Fix a shortfall by FORCING the case in the generator, never by lowering the floor."*

**Key structural finding for the design.** `_ConfluenceCase.intent` uses `self.side` for **every
one** of the six intents — `limit`, `market`, `limit2`, `starved_limit`, `starved_market`
directly, and `rejected` through `overrides.setdefault("side", self.side)`. None of the four
`REJECTED_INTENTS` entries carries a `side` key, so the `setdefault` always takes the case's side.
**One case is therefore one side across all its intents**, which is precisely the
permutation-commutativity premise the generator's docstring states ("One side per example,
drawn … Same-side fills merge into one position at a weighted average entry price, which is
commutative"). Forcing `side` as separate per-variant values preserves that premise exactly,
because each variant still carries a single side throughout.

### 0.4 The existing guard verified

`test_the_forced_limit2_dimension_is_pinned_not_drawn` passes and still asserts its three claims:
both order types produce genuinely different `limit2` intents; the body iterates
`LIMIT2_ORDER_TYPES`, calls `with_limit2_order_type` and never reads `case.limit2_order_type`;
and `_pinned_limit2_order_type` is assigned-and-never-read in the generator. The pin was **not**
removed or rewritten despite appearing unused.

### 0.5 Baseline reproduction — actual collected property, no database

Environment: Python 3.12.10, Hypothesis 6.165.10, pytest 9.1.1, `.venv\Scripts\python.exe`,
`AERORA_TEST_DATABASE_URL` and `DATABASE_URL` cleared (the CI unit lane),
`-q -p no:cacheprovider -p no:warnings -o addopts=""`. All runs sequential — P-23 asserts exact
backoff delays (`race.starved_sleeps.delays == [0.05, 0.10]`) and both prior reports record false
reds under parallel load.

| # | command | result | duration | exit |
|---|---|---|---|---|
| B1 | focused P-23 | 1 passed | 38.87 s (wall 40.8 s) | 0 |
| B2 | four-module group (`test_paper_confluence` + `test_paper_idempotence` + `test_order_idempotence_under_retry` + `test_property_coverage`) | 20 passed | 53.13 s (wall 54.9 s) | 0 |
| B3 | 30 in-test runs of the real collected property (`staging/p23_measure_P23C_BASE30.txt`) | **30/30 PASSED, 0 floor breaches** | 2107.0 s, mean 70.2 s/run | 0 |

Per-run counts, B3 (floor = 20 on each):

| run | `side_buy` | `side_sell` | `l2_limit` | `l2_market` | verdict |
|---:|---:|---:|---:|---:|---|
| 1 | 65 | 35 | 100 | 100 | PASSED |
| 2 | 57 | 43 | 100 | 100 | PASSED |
| 3 | 62 | 38 | 100 | 100 | PASSED |
| 4 | 41 | 59 | 100 | 100 | PASSED |
| 5 | 65 | 35 | 100 | 100 | PASSED |
| 6 | 55 | 45 | 100 | 100 | PASSED |
| 7 | **21** | 79 | 100 | 100 | PASSED |
| 8 | 48 | 52 | 100 | 100 | PASSED |
| 9 | 37 | 63 | 100 | 100 | PASSED |
| 10 | 42 | 58 | 100 | 100 | PASSED |
| 11 | 35 | 65 | 100 | 100 | PASSED |
| 12 | 66 | 34 | 100 | 100 | PASSED |
| 13 | 37 | 63 | 100 | 100 | PASSED |
| 14 | 45 | 55 | 100 | 100 | PASSED |
| 15 | 62 | 38 | 100 | 100 | PASSED |
| 16 | 63 | 37 | 100 | 100 | PASSED |
| 17 | 63 | 37 | 100 | 100 | PASSED |
| 18 | 58 | 42 | 100 | 100 | PASSED |
| 19 | 44 | 56 | 100 | 100 | PASSED |
| 20 | **23** | 77 | 100 | 100 | PASSED |
| 21 | 52 | 48 | 100 | 100 | PASSED |
| 22 | 64 | 36 | 100 | 100 | PASSED |
| 23 | 45 | 55 | 100 | 100 | PASSED |
| 24 | 38 | 62 | 100 | 100 | PASSED |
| 25 | 55 | 45 | 100 | 100 | PASSED |
| 26 | 48 | 52 | 100 | 100 | PASSED |
| 27 | 70 | 30 | 100 | 100 | PASSED |
| 28 | 46 | 54 | 100 | 100 | PASSED |
| 29 | 49 | 51 | 100 | 100 | PASSED |
| 30 | 71 | 29 | 100 | 100 | PASSED |

| bucket | floor | min | mean | max | min margin | breaches |
|---|---:|---:|---:|---:|---:|---|
| `side_buy` | 20 | **21** | 50.9 | 71 | **1.05×** | 0/30 |
| `side_sell` | 20 | 29 | 49.1 | 79 | 1.45× | 0/30 |
| `limit2_was_a_second_limit_order` | 20 | **100** | 100.0 | 100 | **5.00×** | 0/30 |
| `limit2_was_a_second_market_order` | 20 | **100** | 100.0 | 100 | **5.00×** | 0/30 |

### 0.6 Was the reported defect reproduced?

**Partly, and the distinction matters.**

* **The breach itself did NOT recur.** 30 fresh in-test runs on the current build produced zero
  floor failures. A ~1 % per-run event is not reliably reproducible in 30 runs, and **no failure
  rate is claimed from this sample.**
* **The defect's mechanism DID reproduce.** `side_buy` reached **21 against a floor of 20** — one
  single count of margin — and 23 in another run. The bucket is a drawn two-way floor and its
  minimum continues to land on the floor.
* **The prior breach stands as recorded evidence on this exact build.**
  `staging/p23_measure_P23BPIN.txt` run 15 shows `side_sell = 16`, a real
  `paper_census.py:92 AssertionError` from the real collected property, measured after the pin
  was restored — i.e. against the code as it stands today. The P-23B four-module failure
  (`side_buy occurred 16 time(s)`) is also on record.

Combined in-test evidence for `side` across both tasks: **110 runs, 1 breach, minimum 16,
and minima of 21–23 recurring.** The floor is not safe.

* **`limit2_order_type` is confirmed fixed**, now over **90** in-test runs in total
  (60 in P-23B + 30 here), every one exactly 100/100 with standard deviation 0.0.

---

## Phase 1 — design assessment (recorded before editing)

### 1.1 Proposed variant structure

```
for side in SIDES:                              # ("buy", "sell")      - NEW, forced
    for limit2_order_type in LIMIT2_ORDER_TYPES:    # ("limit", "market")  - already forced
        variant = case.with_side(side).with_limit2_order_type(limit2_order_type)
        oracle  = _sequential_oracle(variant, ...)           # its OWN 24-permutation oracle
        _assert_one_concurrent_run_matches_the_oracle(variant, "limit",  "starved_limit",  ...)
        _assert_one_concurrent_run_matches_the_oracle(variant, "market", "starved_market", ...)
```

Four variants per generated example. `side_buy` / `side_sell` are marked once per side per
example (outer loop) → **100 each**; `limit2_*` are marked once per inner iteration → **200
each**. Both floors of 20 become arithmetic consequences of the forcing.

The generator's `side` draw is **retained as a pin** (`_pinned_side`, assigned and never read),
exactly as `_pinned_limit2_order_type` already is, because P-23B measured that deleting such a
draw shortens the choice sequence and shifted `side` into a floor breach. Pinning keeps the
generator's draws byte-for-byte identical, so no other dimension's distribution moves.

### 1.2 Why these are genuinely distinct cases, not duplicated census marks

Each variant produces a **different intent set**: `side` flows into all six intents and
`limit2_order_type` into `limit2`'s order type and whether it carries a limit price. So each
variant gets its own `_sequential_oracle` — 24 permutations replayed on 24 fresh stores — and its
own two interleaved runs contending on their own version-guarded write site. A buy-side set and
a sell-side set differ in realized PnL against cost basis, in which balance column moves, and in
what the position looks like.

This is verifiable rather than asserted: every per-variant census bucket must **quadruple**
(`permutations_replayed` 2400 → 9600, `concurrent_runs` 200 → 800, and so on). A counter bumped
without the work would leave those unchanged. §3 reports them.

### 1.3 Expected runtime

Four variants against the current two: ~2× the current cost, ~4× the pre-P-23 original.

| | current | expected |
|---|---|---|
| focused P-23 | 38.9–77.1 s | ~80–150 s |
| `tests/property/` | 781 s | ~890–1000 s |

### 1.4 Risks

| risk | assessment |
|---|---|
| draw-order behaviour | **mitigated by pinning.** The generator's draw sequence is unchanged, which is the measured lesson from P-23B's breach. |
| Hypothesis shrinking | a failing example now replays 4× the work, so shrink time grows proportionally. Acceptable; assertion messages print the **variant** (`case!r` inside the per-run helper receives the variant), so shrinker output still names the real side and order type. |
| oracle validity | **premise preserved.** Each variant carries one side across all six intents (§0.3), which is what commutativity requires. The property still asserts the common result over all 24 permutations rather than relying on the argument. |
| CI time | +~110–220 s on the property suite. Documented for acceptance. |
| production code | none required. This is a test-only census/coverage concern. |

### 1.5 Does the baseline support the hypothesis?

Yes. `side` is a drawn two-way floor at 20 of an expected 50 whose minimum has been measured at
16 (a real breach) and repeatedly at 21–23. Forcing is the repository's own established remedy
(P-21, P-22, and `limit2_order_type` in this very file, now 90/90 runs at sd 0.0). No speculative
mechanism is being invented.

---

## Phase 2 — implementation

`tests/property/test_paper_confluence.py` only. Nothing else was edited.

Diff against HEAD: **+356 / −18**, of which the Phase 0 baseline already carried +237/−17. This
task therefore contributes **+119 / −1**, the bulk of which is documentation and the extended
guard. The functional change is small:

| element | change |
|---|---|
| `SIDES: Tuple[str, ...] = ("buy", "sell")` | new module constant, documented with the measurement that justifies forcing |
| `_ConfluenceCase.with_side(side)` | new, mirrors the existing `with_limit2_order_type`; replaces the side for the **whole** case so the variant stays one-sided |
| generator | `side = draw(...)` → `_pinned_side = draw(...)` — the draw is **retained as a pin**, assigned and never read |
| generator constructor call | `side=side` → `side=SIDES[0]` (placeholder, replaced on every iteration) |
| `check` body | `recorder.mark(f"side_{case.side}")` once per example → `for side in SIDES:` with `recorder.mark(f"side_{side}")` inside, wrapping the existing `LIMIT2_ORDER_TYPES` loop; the variant is now `case.with_side(side).with_limit2_order_type(limit2_order_type)` |
| guard | `test_the_forced_limit2_dimension_is_pinned_not_drawn` → `test_the_forced_dimensions_are_pinned_not_drawn`, extended from three claims to four (§3.4) |

**Ownership boundaries.** No application code, migration, PostgreSQL configuration, exchange
setting, execution gate, tenant-isolation gate, paper-trading gate, unrelated test, or audit JSON
mirror was touched. No broad staging, reset, clean, stash, checkout-overwrite or revert command
was used at any point. The other three modified files under `tests/property/` are untouched at
their Phase 0 values (+255/−9, +69/−9, +34/−7).

**Nothing weakened.** `P23_FLOORS` is byte-for-byte unchanged — no floor lowered, none removed.
`max_examples` remains 100. No assertion was deleted or relaxed; every removed line is the
relocation of an existing `recorder.mark` into the new loop. No skip or xfail was added. The
`limit2_order_type` forcing and its pin are preserved exactly.

### Why the `side` draw is pinned rather than deleted

P-23B measured that deleting a now-redundant draw shortens the generator's choice sequence,
shifts every later draw, and pushed `side` into an actual floor breach
(`side_buy occurred 16 time(s), floor 20` in the four-module group). Retaining both draws as
pins keeps `confluent_intent_sets`'s draws byte-for-byte identical to the shape every
distribution measurement in all three reports was taken against, so forcing one dimension cannot
silently damage another. The guard asserts both pins stay assigned-and-unread, with the
measurement quoted in the failure message.

---

## Phase 3 — verification

All runs sequential, no database, CI unit lane (`AERORA_TEST_DATABASE_URL` and `DATABASE_URL`
cleared), `-q -p no:cacheprovider -p no:warnings -o addopts=""`. Python 3.12.10,
Hypothesis 6.165.10, pytest 9.1.1.

| # | check | command / artifact | result | duration | exit |
|---|---|---|---|---|---|
| V1 | guard | `::test_the_forced_dimensions_are_pinned_not_drawn` | **1 passed** | 1.69 s | 0 |
| V2 | guard falsification | `staging/falsify_p23c_forced_dimension_guard.py` | **13/13 fired as required** | — | 0 |
| V3 | focused P-23 | `::test_p23_concurrent_intents_match_a_sequential_order` | **1 passed** | 86.44 s (wall 88.6 s) | 0 |
| V4 | full census, 1 in-test run | `probe_phase10_sibling_census_margins.py p23 1` | PASSED, all 23 buckets above floor | 61.1 s | — |
| V5 | four-module group | `test_paper_confluence` + `test_paper_idempotence` + `test_order_idempotence_under_retry` + `test_property_coverage` | **20 passed** | 90.13 s (wall 92.2 s) | 0 |
| V6 | 30 in-test runs | `staging/p23_measure_P23C_POST30.txt` | **30/30 PASSED, 0 floor breaches** | 6885.7 s, mean 229.5 s/run | 0 |
| V7 | full property suite | `tests/property/` | **139 passed, 1 xfailed, 3 xpassed** | 789.98 s | 0 |

### V6 — per-run coverage, 30 in-test runs (floor 20 on each bucket)

Every run produced identical counts, so the table is stated once rather than thirty times:

| bucket | every run | floor | margin | sd | breaches |
|---|---:|---:|---:|---:|---|
| `side_buy` | **100** | 20 | **5.00×** | **0.0** | 0/30 |
| `side_sell` | **100** | 20 | **5.00×** | **0.0** | 0/30 |
| `limit2_was_a_second_limit_order` | **200** | 20 | **10.00×** | **0.0** | 0/30 |
| `limit2_was_a_second_market_order` | **200** | 20 | **10.00×** | **0.0** | 0/30 |

Runs 1–30 all `PASSED`; `FLOOR BREACHES: NONE`; worst min margin across the four formerly drawn
buckets **5.00×**, against 1.05× at baseline.

Before and after, same measurement mechanism:

| bucket | baseline (30 runs) | after (30 runs) |
|---|---|---|
| `side_buy` | min **21**, mean 50.9, max 71, margin **1.05×** | **100 constant**, margin **5.00×** |
| `side_sell` | min 29, mean 49.1, max 79, margin 1.45× | **100 constant**, margin **5.00×** |
| `limit2_*` | 100 constant, 5.00× | **200 constant**, 10.00× |

### V4 — the census is satisfied by work, not by counters

Every per-variant bucket **quadrupled**, which is the arithmetic consequence of four variants
replacing one. A counter incremented without the work would have left these unchanged:

| bucket | before | after | margin |
|---|---:|---:|---:|
| `permutations_replayed` | 2400 | **9600** | 4.00× |
| `concurrent_runs` | 200 | **800** | 4.00× |
| `sequential_permutations_agreed` | 100 | **400** | 4.00× |
| `backoffs_slept` | 400 | **1600** | 4.00× |
| `outer_was_a_limit_order` / `_market_order` | 200 each | **400 each** | 4.00× |
| `a_real_conflict_occurred`, `the_outer_retried_and_then_succeeded`, `a_nested_submission_also_conflicted`, `every_raced_intent_succeeded`, `a_market_order_filled`, `a_position_was_opened`, `a_resting_order_locked_funds`, `a_rejected_intent_survived_the_race`, `starved_409_left_a_durable_accepted_order` | 400 each | **800 each** | 4.00× |
| `starved_limit_reported_partial_write` / `starved_market_left_one_unsettled_fill` | 200 each | **400 each** | 4.00× |
| `competitors_released_inside_a_read` | 1200 | **2400** | 6.00× |
| **`side_buy` / `side_sell`** | ~49 / ~51 | **100 / 100** | **5.00×** |
| **`limit2_*`** | 100 each | **200 each** | **10.00×** |
| `examples` | 100 | **100** | 1.00× — `max_examples` unchanged |

So each of the four combinations runs its own 24-permutation oracle and its own two interleaved
comparisons: 96 permutations and 8 concurrent runs per generated example.

### V2 — the guard is falsifiable

13 cases, every one behaving as required (`staging/falsify_p23c_forced_dimension_guard.py`,
exit 0). The AST claims were falsified by pointing the module's `__file__` at a mutated copy of
its own source, so the guard genuinely re-parsed broken text rather than the probe
re-implementing its logic:

| mutation | guard fires |
|---|---|
| guard unmutated | passes |
| `with_limit2_order_type` made an identity | yes |
| `with_side` made an identity | yes |
| **a variant made two-sided (oracle premise broken)** | **yes** |
| `SIDES` reduced to one value | yes |
| `LIMIT2_ORDER_TYPES` reduced to one value | yes |
| body no longer iterates `SIDES` | yes |
| body no longer iterates `LIMIT2_ORDER_TYPES` | yes |
| body reads `case.side` again | yes |
| `_pinned_side` draw deleted | yes |
| `_pinned_limit2_order_type` draw deleted | yes |
| `_pinned_side` draw read again | yes |
| `_pinned_limit2_order_type` draw read again | yes |

The two-sided case is the important one: it is the only mechanical check that the oracle's
same-side commutativity premise still holds, and it catches a variant whose `rejected` intent
sits on the opposite side.

### V7 — test dispositions

**139 passed, 1 xfailed, 3 xpassed, 0 failed, 0 errors, no skips in this lane.** Identical
counts to the P-23B baseline (139 / 1 / 3) — the guard was **renamed**, not added, so the total
is unchanged. The 1 xfail and 3 xpasses are pre-existing and belong to other property modules;
no test changed disposition.

### Runtime impact

| scope | before | after | factor |
|---|---|---|---|
| focused P-23 (back-to-back, same session) | 38.87 s | **86.44 s** | 2.22× |
| four-module group | 53.13 s | **90.13 s** | 1.70× |
| `tests/property/` full suite | 781.38 s | **789.98 s** | **1.01×** |
| 30-run in-test campaign, mean per run | 70.2 s | 229.5 s | 3.27× |

The suite-level impact is the figure that matters for CI and it is **+8.6 s (+1.1 %)**, well
inside the suite's own run-to-run variance. The 3.27× campaign figure is inflated: that campaign
ran while other workstreams had up to ten concurrent Python processes on the machine, and it is
reported for completeness rather than as the CI cost.

---

## Acceptance

| criterion | status |
|---|---|
| Both `side` values and both order types genuinely exercised for every generated example | **MET** — 100/100 and 200/200 in all 30 runs, sd 0.0 |
| Each forced combination executes its own oracle and concurrent comparison | **MET** — `permutations_replayed` 9600 and `concurrent_runs` 800 per run, 4.00× |
| No census bucket satisfied by incrementing a counter without the work | **MET** — every per-variant bucket quadrupled; guard asserts the four variants are distinct intent sets |
| Former `side` floor breach not reproduced | **MET** — 0 breaches in 30 runs; the bucket is now constant at 5.00× and cannot vary |
| Focused test, regression group, full suite pass | **MET** — 1, 20 and 139 passed, all exit 0 |
| Pre-existing xfails/xpasses accurately reported | **MET** — 1 xfailed, 3 xpassed, unchanged |
| Floors, `max_examples`, assertions unchanged | **MET** — `P23_FLOORS` untouched, `max_examples` 100, no assertion weakened, no skip/xfail added |
| Runtime increase documented and acceptable | **MET** — +1.1 % on the property suite |
| Oracle premise certain | **MET** — each variant is one-sided across all six intents, asserted mechanically and falsified |

### Verdict: **ACCEPT**

### Remaining risks

1. **The two pins are load-bearing and unusual.** `_pinned_side` and `_pinned_limit2_order_type`
   are deliberately unused draws. They are documented at the draw site, in both constants' notes,
   and asserted by the guard, whose failure message carries the measurement. A future reader may
   still try to delete them; the guard is the mitigation.
2. **P-23 is now the most expensive property in the suite** — four variants, 96 permutation
   replays and 8 interleaved runs per example, ~400 real `submit_intent` calls per example. The
   suite cost is +1.1 %, but if further dimensions are ever forced the right question becomes
   whether `EXAMPLES = 100` with 2ⁿ forced variants is the best use of a fixed budget. A design
   question, not a defect.
3. **Shrinking is 4× slower on failure.** Assertion messages print the variant, so shrinker output
   still names the real side and order type.
4. **`side` and `limit2_order_type` are no longer searched.** Forcing removes them from
   Hypothesis's search space, which is the point — but it also means the census floors on those
   four buckets no longer detect anything about the sampler, because there is no sampler left for
   them. The floors are retained unchanged and are now arithmetic consequences, exactly as 19 of
   P-23's other buckets already were, and as P-21's 27 and P-22's are.
5. **P-23 has no drawn census floors left.** Every one of its 23 buckets is now forced. That
   matches P-21 and P-22 and closes the defect class that TEST-05, P-23, P-23B and P-23C all
   belonged to.
6. **Not verified against PostgreSQL.** P-23 drives the `FakeSupabase` double.
7. **Breach-rate history.** The `side` breach rate was never established from a small sample and
   is not claimed here; what is established is that the bucket can no longer vary at all.
8. **Still outstanding from earlier tasks:** authorization to synchronize the TEST-05 entry into
   the three audit JSON mirrors (`marketplace_audit.json`, `money_flow_audit.json`,
   `trading_audit.json`).

### Final Git state

```
HEAD            074b772ab16cece62cbfca0f2b78497be2ae56d5   (unchanged)
branch          main, upstream origin/main, no divergence
staged entries  22  (unchanged - the same pre-existing other-workstream set)
no commit, no push, no branch created, no CI triggered
no audit JSON mirror modified
tests/property/test_order_idempotence_under_retry.py   255   9   (untouched by this task)
tests/property/test_paper_confluence.py                356  18   (THIS TASK: +119 / -1)
tests/property/test_protected_logic_containment.py      69   9   (untouched by this task)
tests/property/test_tenant_isolation_matrix.py          34   7   (untouched by this task)
```

All concurrent work is preserved.

### Scope of the claim

Deterministic coverage of `side` and `limit2_order_type` establishes that all four combinations
are raced against every generated intent set, each with its own sequential oracle. It does **not**
establish exchange correctness, exactly-once execution, paper-trading certification, or
live-trading readiness. **Live trading remains OFF / NOT CERTIFIED**, paper trading remains
**NOT CLASSIFIED**, and launch status is unchanged.
