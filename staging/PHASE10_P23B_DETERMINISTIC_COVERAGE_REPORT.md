# Phase 10 — Workstream P-23B — deterministic coverage for `limit2_order_type`

**Subject:** `tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order`
**Baseline revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5`, branch `main`. No commit, no push.
**Baseline evidence:** `staging/PHASE10_P23_DRAW_ORDER_REPORT.md`
**Live trading:** OFF / NOT CERTIFIED. Paper trading: NOT CLASSIFIED. Launch status unchanged.
**Audit JSON mirrors:** not touched.

---

## 1. Verdict

**`limit2_order_type` is fixed, deterministically.** Across **60** independent in-test runs it is
exactly **100 / 100** every time — standard deviation **0.0**, margin **5.00×** against the floor
of 20. It is no longer a draw.

| arm | runs | `l2_limit` | `l2_market` | sd | min margin |
|---|---:|---|---|---:|---:|
| ORIGINAL | 20 | mean 65.8, min 55 | mean 34.2, min **22** | 6.9 | 1.10× |
| DRAW-ORDER (prior task) | 20 | mean 47.6, min 26 | mean 52.4, min **23** | 11.8 | 1.15× |
| **FORCED (this task, 3 arms)** | **60** | **100 always** | **100 always** | **0.0** | **5.00×** |

**But the campaign uncovered a separate, blocking problem: `side`'s floor actually fails.**
Over the 80 in-test runs measured here, `side_sell` hit **16 against a floor of 20** — a real
breach, in a real collected-test run. It is **not caused by this task's change** (§5), and it
needs its own authorized pass (§9).

**Acceptance recommendation: accept the `limit2_order_type` change; do not treat P-23 as robust
until `side` is given the same treatment.**

---

## 2. Preservation and baseline

Inspected before editing. HEAD `074b772ab16cece62cbfca0f2b78497be2ae56d5`, branch `main`,
**22 staged entries** (all pre-existing other-workstream work), 247 working-tree entries.
`tests/property/test_paper_confluence.py` carried the prior task's `+34/−2`, and its functional
content was verified to be exactly the two hoisted draws plus the two argument substitutions —
**no discrepancy with the report, so no stop was triggered.** Nothing was reset, stashed,
cleaned, reverted or overwritten; the index was never modified.

Also read, as the repository's own precedents for deterministic property coverage:
`test_order_idempotence_under_retry.py` (TEST-05: `DETERMINISTIC_COVERAGE` + `_forcing`, forcing
**existence** floors of 1), `test_paper_idempotence.py` P-22 (`for flavour in SUBMIT_FLAVOURS:`,
three flavours per example) and P-21 (four scenarios × four repetition bands per example).

Confirmed P-23 has **no** `@example` cases, no `skip`, no `xfail`, and `EXAMPLES = 100`.

Baseline focused test, on the shipped prior build:

```
pytest "tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order"
  -q -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE
→ 1 passed in 51.79s   exit 0   (wall 56.6 s)
```

Property-suite baseline (carried from the prior report, same command):
**138 passed, 1 xfailed, 3 xpassed in 676.33 s, exit 0.**

---

## 3. Approach comparison, and why B was chosen

### Approach A — explicit Hypothesis examples

The decisive fact is that **P-23's floors are 20, not 1.** TEST-05's `@example` remedy worked
because its floors were *existence* floors of 1, which a single forced case satisfies by
construction. Here:

* **Two `@example` cases** (one per value) guarantee each value is *exercised* once, but leave
  the floor of 20 needing **19 more** from draws. The margin stays at 1.15×. **This fails the
  stated objective**, which is the margin, not merely touching both values.
* **Twenty `@example` cases per value** (40 total) would satisfy the floor arithmetically, at a
  cost of +40 examples (~+40% runtime). But each would be a full hand-written `_ConfluenceCase`
  — five unique quantities, three unique prices, two switch sets — and twenty near-identical
  clones marking one bucket twenty times add **no diversity**. That is satisfying a census by
  duplication, which is the thing the census exists to detect. Unmaintainable, and dishonest.

### Approach B — deterministic per-example forcing — **CHOSEN**

Drive **every** `LIMIT2_ORDER_TYPES` value against every generated intent set, exactly as P-22
drives its three `SUBMIT_FLAVOURS` and P-21 its four scenarios. Each bucket then receives
precisely one mark per example, so a floor of 20 becomes an arithmetic consequence of the
forcing — **100 against 20, at any seed, in any lane** — rather than a bet on a draw frequency.

It is the smallest implementation that *guarantees* the coverage: one loop, one copy-helper, no
literals, no new assertions, no floor change.

### Expected and measured runtime cost

Each value needs its **own oracle**, because the oracle is a function of the intent set — the
24-permutation replay dominates the ~100 `submit_intent` calls per example — and its own pair of
interleaved runs. So the per-example pipeline runs twice.

| | predicted | measured |
|---|---|---|
| focused P-23 | ~2× | 51.79 s → **77.05 s** (1.49×) |
| `tests/property/` | +~10–15% | 676.33 s → **781.38 s** (+15.5%) |

Less than 2× in wall-clock because fixed collection and import overhead does not double.

---

## 4. Implementation

`tests/property/test_paper_confluence.py` only. Final unstaged diff **+237 / −17** against HEAD,
which includes the prior task's `+34/−2`. The bulk is documentation and the new guard test.

| element | what it does |
|---|---|
| `LIMIT2_ORDER_TYPES: Tuple[str, ...] = ("limit", "market")` | the forced dimension, documented with the measurement that justifies forcing it |
| `_ConfluenceCase.with_limit2_order_type(order_type)` | returns the same generated case with only that field replaced, so the two variants of one example differ in exactly that order type |
| `check` body | `for limit2_order_type in LIMIT2_ORDER_TYPES:` — builds the variant, marks its bucket, computes **its own** oracle, and runs **both** interleaved runs against it |
| generator | `side` stays hoisted (the prior fix, preserved); the `limit2_order_type` draw is retained as `_pinned_limit2_order_type` and deliberately unused — see §5 |
| `test_the_forced_limit2_dimension_is_pinned_not_drawn` | new guard, three claims (§6) |

**Not changed:** any assertion, the property oracle, `P23_FLOORS` (no value altered, none
lowered), `max_examples` (still 100), skip/xfail status (none exist), the `side` draw-order fix,
any other generator or test, any application code, migration, PostgreSQL configuration, exchange
credential, execution gate, tenant-isolation gate or paper-trading gate.

Every removed line in the diff is a relocation of an existing `recorder.mark` or
`_assert_one_concurrent_run_matches_the_oracle` call into the new loop. **No assertion was
deleted, weakened, skipped or xfailed.**

---

## 5. An unexpected outcome, investigated rather than smoothed over

### What happened

The first implementation **deleted** the now-redundant `limit2_order_type` draw, which is the
clean thing to do with dead code. The four-module regression group then **failed**:

```
paper_census.py:92: AssertionError: P-23 would be vacuous: side_buy occurred 16 time(s), floor 20
  -- full census {... 'side_buy': 16, 'side_sell': 84,
                  'limit2_was_a_second_limit_order': 100,
                  'limit2_was_a_second_market_order': 100}
1 failed, 18 passed in 129.91s
```

The forcing worked perfectly — `limit2` is 100/100. The breach was entirely **`side`**, and the
identical four-module selection had **passed** in the prior task. Deleting one draw shortens the
generator's choice sequence, shifts every later draw's position, and moved `side`'s distribution.

### What was done about it

The draw was **restored as an explicit pin** (`_pinned_limit2_order_type`, assigned and never
read), so the choice sequence keeps the shape `side`'s own fix was measured against. The
four-module group then passed — **20 passed in 58.03 s, exit 0**. The pin is documented at the
draw site and asserted by the new guard, so it cannot be removed as dead code without the
measurement that explains it.

The floor was **not** lowered and no assertion was touched.

### But `side` fails anyway — and that is the real finding

With the pin restored, a further 20-run arm produced a **genuine breach**: run 15,
`side_sell = 16`, floor 20.

| arm | runs | `side` mean | sd | `side` min | margin | breaches |
|---|---:|---:|---:|---:|---:|---|
| ORIGINAL | 20 | 68.0 / 32.0 | 5.7 | 22 | 1.10× | 0/20 |
| DRAW-ORDER (prior task) | 20 | 50.0 / 50.0 | 10.4 | 33 | 1.65× | 0/20 |
| FORCED-A (pin deleted) | 20 | 48.1 / 51.9 | 12.6 | 22 | 1.10× | 0/20 |
| FORCED-B (pin deleted) | 20 | 44.8 / 55.2 | 10.9 | 28 | 1.40× | 0/20 |
| **FORCED-PIN (shipped)** | 20 | 50.4 / 49.6 | 15.9 | **16** | **0.80×** | **1/20** |
| **all four post-hoist arms** | **80** | **50.0** | **12.7** | **16** | **0.80×** | **1 (1.2 %)** |

**Attribution — this task did not cause it.** The breach occurred in the arm whose generator
choice sequence is *identical* to the prior task's DRAW-ORDER arm, and the ORIGINAL arm already
sat at min 22 against a floor of 20. `side` is a drawn two-way floor set at 20 of an expected 50,
with sd ≈ 13 and fat tails from tree-guided prefix exploration; it is unsafe on its own terms.
What this task's **80 extra in-test runs** did was *reveal* it. A 20-run minimum cannot see a
~1 % per-run tail, which is precisely why the prior report's `side` verdict of "FIXED, 1.65×"
was over-confident.

**Correction to `staging/PHASE10_P23_DRAW_ORDER_REPORT.md`:** its `side` verdict should read
*bias corrected, margin still marginal* rather than *FIXED*. The bias correction is durable —
`side`'s mean is 50.0 across all 80 post-hoist runs, against 68/32 originally — but the margin
claim does not survive more sampling.

---

## 6. Coverage evidence — both values are genuinely exercised

### The marks are not the evidence; the work is

Full census from a post-forcing in-test run. **Every** forced bucket doubled exactly, which is
what proves the whole pipeline ran twice rather than a counter being bumped twice:

| bucket | before | after | margin |
|---|---:|---:|---:|
| `permutations_replayed` | 2400 | **4800** | 2.00× |
| `concurrent_runs` | 200 | **400** | 2.00× |
| `sequential_permutations_agreed` | 100 | **200** | 2.00× |
| `outer_was_a_limit_order` / `_market_order` | 100 each | **200 each** | 2.00× |
| `a_real_conflict_occurred` | 200 | **400** | 2.00× |
| `the_outer_retried_and_then_succeeded` | 200 | **400** | 2.00× |
| `a_nested_submission_also_conflicted` | 200 | **400** | 2.00× |
| `every_raced_intent_succeeded` | 200 | **400** | 2.00× |
| `a_market_order_filled`, `a_position_was_opened`, `a_resting_order_locked_funds`, `a_rejected_intent_survived_the_race`, `starved_409_left_a_durable_accepted_order` | 200 each | **400 each** | 2.00× |
| `starved_limit_reported_partial_write` / `starved_market_left_one_unsettled_fill` | 100 each | **200 each** | 2.00× |
| `backoffs_slept` | 400 | **800** | 2.00× |
| `competitors_released_inside_a_read` | 400 | **1200** | 3.00× |
| **`limit2_was_a_second_limit_order`** | ~48 | **100** | **5.00×** |
| **`limit2_was_a_second_market_order`** | ~52 | **100** | **5.00×** |
| `examples` | 100 | **100** | 1.00× — `max_examples` unchanged |
| `side_buy` / `side_sell` | ~49 / ~51 | ~49 / ~51 | still one mark per example |

Every assertion inside `_assert_one_concurrent_run_matches_the_oracle` therefore runs **400**
times per run instead of 200, and the oracle replays **4800** permutations instead of 2400. The
existing assertions are not merely intact, they are exercised twice as often.

### Per-run floor checks, 60 in-test runs

`limit2_was_a_second_limit_order` and `limit2_was_a_second_market_order` were **exactly 100 in
every one of the 60 runs** (FORCED-A 20, FORCED-B 20, FORCED-PIN 20); sd 0.0; 0 breaches.
Logs: `staging/p23_measure_P23B20.txt`, `..._P23B20B.txt`, `..._P23BPIN.txt`.

### The new guard

`test_the_forced_limit2_dimension_is_pinned_not_drawn` asserts three things:

1. both `LIMIT2_ORDER_TYPES` values produce **different** `limit2` intents, so forcing both is
   two different races rather than the same race counted twice;
2. the property body iterates `LIMIT2_ORDER_TYPES`, calls `with_limit2_order_type`, and **never**
   reads `case.limit2_order_type` — so a generated value cannot decide coverage again;
3. `_pinned_limit2_order_type` is **assigned and never read** in the generator (AST-checked), with
   the failure message carrying the `side_buy occurred 16 time(s)` measurement so the pin cannot
   be deleted as dead code without encountering the reason it exists.

---

## 7. Commands, results, durations, exit codes

Run sequentially; P-23 asserts exact backoff delays and produces false reds under parallel load.
All in the no-database CI unit lane (`AERORA_TEST_DATABASE_URL` and `DATABASE_URL` cleared),
`-q -p no:cacheprovider -p no:warnings -o addopts=""`.

| # | command | result | duration | exit |
|---|---|---|---|---|
| 1 | focused P-23, **baseline** | 1 passed | 51.79 s (wall 56.6 s) | 0 |
| 2 | focused P-23, first build (pin deleted) | 1 passed | 83.04 s (wall 90.5 s) | 0 |
| 3 | 20 in-test runs, pin deleted (`P23B20`) | 20/20 PASSED | 1838.2 s, mean 91.9 s | 0 |
| 4 | full census capture, 1 in-test run | PASSED | 76.7 s | — |
| 5 | 20 further in-test runs (`P23B20B`) | 20/20 PASSED | mean ~83 s | 0 |
| 6 | four-module group, pin deleted | **1 FAILED**, 18 passed — `side_buy occurred 16 time(s), floor 20` | 129.91 s | 1 |
| 7 | new guard alone | 1 passed | 3.99 s | 0 |
| 8 | four-module group, **pin restored** | **20 passed** | 58.03 s (wall 60.6 s) | 0 |
| 9 | 20 in-test runs, pin restored (`P23BPIN`) | **19/20 PASSED, 1 FAILED** — run 15, `side_sell = 16` | 926.6 s, mean 46.3 s | 0 |
| 10 | focused P-23, **shipped build** | 1 passed | 77.05 s (wall 81.6 s) | 0 |
| 11 | `tests/property/` full suite | **139 passed, 1 xfailed, 3 xpassed** | 781.38 s | 0 |

The four-module group is the same selection used in the prior report
(`test_paper_confluence` + `test_paper_idempotence` + `test_order_idempotence_under_retry` +
`test_property_coverage`); it reports **20** passed rather than 19 because of the new guard.

Full suite: **139 passed** (baseline 138, +1 guard), **1 xfailed, 3 xpassed** — identical xfail
and xpass counts to the baseline, so no test changed disposition. No skips in this lane.

Supplementary diagnostics (generator-only sweeps) were **not** used as a primary result anywhere
in this report; every figure above is from the real collected property.

---

## 8. Change control

```
255  9  tests/property/test_order_idempotence_under_retry.py   (TEST-05 task, untouched here)
237 17  tests/property/test_paper_confluence.py                (THIS TASK + prior P-23 task)
 69  9  tests/property/test_protected_logic_containment.py     (another workstream, untouched)
 34  7  tests/property/test_tenant_isolation_matrix.py         (another workstream, untouched)
```

* **Staged entries: 22, unchanged.** The index was never modified.
* **No commit, no push.**
* **No audit JSON mirror touched.**
* No application code, migration, PostgreSQL configuration, exchange credential, execution gate,
  tenant-isolation gate or paper-trading gate was modified.

New evidence files, all under the already-untracked `staging/`:

```
p23_measure_P23B20.txt / _P23B20B.txt / _P23BPIN.txt   the three 20-run in-test arms
compare_p23b_arms.py                                   four-arm comparison
analyse_p23b_side_risk.py                              the side-breach attribution
PHASE10_P23B_DETERMINISTIC_COVERAGE_REPORT.md           this report
```

---

## 9. Acceptance recommendation and remaining risks

### Recommendation

**Accept the `limit2_order_type` change.** It delivers exactly what was asked: deterministic
coverage of both values, proven over 60 in-test runs at sd 0.0 and 5.00× the floor, with no floor
lowered, no assertion weakened, `max_examples` unchanged, nothing mocked, and every existing
assertion exercised twice as often. The cost is +25 s on the focused test and +15.5 % on the
property suite.

### Remaining risks

1. **`side`'s floor fails, measured at ~1.2 % of runs (1 breach in 80 in-test runs, plus one in
   the four-module group).** This is the dominant remaining risk and it is **pre-existing**, not
   introduced here. Until it is addressed, P-23 can red CI intermittently and **P-23 should not
   be described as robust**. The proven remedy is the one applied here: force `side` the same way,
   which would take P-23 to zero drawn census floors and match P-21 and P-22. Cost is a further
   doubling (2 sides × 2 order types = 4 variants per example, roughly 4× the original baseline,
   focused test ~200 s). It also needs care that each variant keeps **one** side across its
   intents, since the oracle's permutation-commutativity premise depends on that — satisfied by
   per-variant forcing, but it must be stated. **Needs its own authorized pass.**
2. **The pin is load-bearing and unusual.** `_pinned_limit2_order_type` is a deliberately unused
   draw. It is documented and guarded, but it is the kind of thing a future reader may delete. The
   guard's failure message is the mitigation.
3. **The runtime cost compounds.** If `side` is also forced, P-23 reaches ~4× its original cost.
   At some point the right answer may be to revisit whether `EXAMPLES = 100` with four forced
   variants is the best allocation of a fixed time budget — a design question, not a defect.
4. **Breach-rate estimates are loose.** 80 runs bound a ~1 % tail only roughly.
5. **Not verified against PostgreSQL.** P-23 drives the `FakeSupabase` double.
6. **Scope of the claim.** Deterministic coverage of `limit2_order_type` establishes that both
   order types are raced on every example. It does **not** establish venue-level correctness,
   exactly-once execution, paper-trading classification or live-trading readiness. Live trading
   remains OFF / NOT CERTIFIED, paper trading remains NOT CLASSIFIED, and launch status is
   unchanged.
7. **Still outstanding from earlier tasks:** authorization to synchronize the TEST-05 entry into
   the three audit JSON mirrors.
