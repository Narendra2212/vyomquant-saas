# Phase 10 — P-23 draw-order experiment

**Subject:** `tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order`,
and the `confluent_intent_sets` generator that feeds it.
**Baseline revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5`, branch `main`. No commit made.
**Live trading:** OFF / NOT CERTIFIED. Paper trading: NOT CLASSIFIED. Launch status unchanged.
**Audit JSON mirrors:** not modified in this task — proved, see §7.

---

## 1. Verdict

| dimension | mean bias off a fair draw | per-run sd (worst) | worst min margin | verdict |
|---|---|---|---|---|
| **`side`** | 18.0 → **0.0** | 5.7 → 10.4 | 1.10x → **1.65x** | **FIXED** |
| **`limit2_order_type`** | 15.8 → **2.4** | 6.9 → 11.8 | 1.10x → **1.15x** | **STILL MARGINAL** |

The change does what it was meant to do for `side` and only half of it for
`limit2_order_type`. Both draws were biased and both are now unbiased in the mean, but
hoisting a draw to the front of the generator also **raises its per-run variance**, and for
`limit2_order_type` the variance increase roughly cancelled the bias correction, leaving the
minority bucket's minimum essentially where it was (22 → 23 against a floor of 20).

**No regression, and a net improvement.** All **40** in-test executions (20 baseline + 20
post-change) passed with **zero floor breaches in either arm**, and the one arm that had ever
produced a below-floor count — the supplementary generator-only sweep, where `side_sell` hit 14
against floor 20 in 2 of 20 runs — produces none after the change.

`limit2_order_type` remains a thin distribution floor and is **not** claimed fixed.

---

## 2. Baseline

### What was inspected first

`git status`: HEAD `074b772ab16cece62cbfca0f2b78497be2ae56d5`, 22 staged entries (all
pre-existing other-workstream work — tenant isolation, RLS, terraform, the security review),
242 working-tree entries. `tests/property/test_paper_confluence.py` had **no** unstaged
changes, so the baseline is the committed file. Nothing was reset, stashed, cleaned, checked
out over or reverted; the index was not touched.

### The implementation matched the reported description

No discrepancy, so no stop. Confirmed in `confluent_intent_sets`:

* **No deterministic coverage cases exist** — the property is `@PROPERTY_SETTINGS` +
  `@given(case=confluent_intent_sets())` with no `@example`, no `skip`, no `xfail`.
  `EXAMPLES = 100`.
* The original draw order, and which draws consume an unpredictable amount of the choice
  sequence:

  | # | draw | consumption |
  |---|---|---|
  | 1 | `quantities` — `st.lists(..., min_size=5, max_size=5, unique=True)` | **filtered** (internal rejection sampling via `unique=True`) |
  | 2 | `prices` — `st.lists(..., min_size=3, max_size=3, unique=True)` | **filtered** |
  | 3 | `key_suffix` — `st.text(min_size=1, max_size=16)` | **variable-length** |
  | 4 | **`side`** — `st.sampled_from(("buy", "sell"))` | plain — *immediately downstream of 1–3* |
  | 5–8 | `close`, `fee_rate`, `slippage_rate`, `rejected_intent` | plain |
  | 9 | **`limit2_order_type`** — `st.sampled_from(("limit", "market"))` | plain — *immediately upstream of 10–11* |
  | 10 | `switch_at` — `st.sets(st.integers(...), max_size=2)` | **variable-length** |
  | 11 | `nested_switch_at` — `st.sets(..., max_size=1)` | **variable-length** |

  `quantities` and `prices` are drawn before the `_ConfluenceCase(...)` call; the rest are
  drawn as constructor arguments, which Python evaluates in source order.

* `side` and `limit2_order_type` are consumed exactly once per example each, marked by
  `recorder.mark(f"side_{case.side}")` and the `limit2_was_a_second_{limit,market}_order`
  branch at the top of `check`. Their floors are `EXAMPLES // 5` = **20**, and `P23_FLOORS`
  documents them as the only drawn ones: *"These four are DRAWN two-way choices, not forced
  cases … Everything above this line is forced into every example and its floor is the
  arithmetic consequence of that forcing."* The stated rationale for 20 is that a fair draw
  gives "about fifty of each".

### Focused test, pre-change

```
.venv\Scripts\python.exe -m pytest
  "tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order"
  -q -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE
→ 1 passed in 70.35s   exit 0   (wall 77.9 s)
```

### Baseline in-test distribution — 20 independent executions

Measured with the existing in-test mechanism
(`staging/probe_phase10_sibling_census_margins.py`, which runs the **real collected property**
and reads the `Recorder` it built). A generator-only sweep was **not** used as a substitute.

```
Start-Process powershell.exe -File staging\run_p23_draw_order_measure.ps1 BASE20 20
→ exit 0;  20/20 PASSED;  total property time 760.2 s, mean 38.0 s/run
   log: staging/p23_measure_BASE20.txt
```

| run | `side_buy` | `side_sell` | `l2_limit` | `l2_market` | verdict |
|---:|---:|---:|---:|---:|---|
| 1 | 73 | 27 | 58 | 42 | PASSED |
| 2 | 58 | 42 | 55 | 45 | PASSED |
| 3 | 69 | 31 | 59 | 41 | PASSED |
| 4 | 73 | 27 | 62 | 38 | PASSED |
| 5 | 69 | 31 | 60 | 40 | PASSED |
| 6 | 62 | 38 | 62 | 38 | PASSED |
| 7 | 69 | 31 | 69 | 31 | PASSED |
| 8 | 70 | 30 | 75 | 25 | PASSED |
| 9 | 60 | 40 | 73 | 27 | PASSED |
| 10 | 64 | 36 | 61 | 39 | PASSED |
| 11 | 64 | 36 | 61 | 39 | PASSED |
| 12 | 74 | 26 | 65 | 35 | PASSED |
| 13 | 76 | **24** | 70 | 30 | PASSED |
| 14 | 66 | 34 | 71 | 29 | PASSED |
| 15 | 71 | 29 | 57 | 43 | PASSED |
| 16 | 73 | 27 | 73 | 27 | PASSED |
| 17 | 78 | **22** | 78 | **22** | PASSED |
| 18 | 67 | 33 | 65 | 35 | PASSED |
| 19 | 65 | 35 | 76 | 24 | PASSED |
| 20 | 59 | 41 | 65 | 35 | PASSED |

| bucket | floor | min | mean | max | min margin | breaches |
|---|---:|---:|---:|---:|---:|---|
| `side_buy` | 20 | 58 | 68.0 | 78 | 2.90x | 0/20 |
| `side_sell` | 20 | **22** | 32.0 | 42 | **1.10x** | 0/20 |
| `limit2_was_a_second_limit_order` | 20 | 55 | 65.8 | 78 | 2.75x | 0/20 |
| `limit2_was_a_second_market_order` | 20 | **22** | 34.2 | 45 | **1.10x** | 0/20 |

Both two-way draws were skewed roughly **68/32** and **66/34**, not 50/50, so each minority
bucket's real margin was **1.10x** rather than the ~2.5x the floor's reasoning assumed. The
`side_buy` and `limit2_limit` buckets never once fell below 55 — the bias was consistent in
direction, not noise.

---

## 3. The change

`tests/property/test_paper_confluence.py` only. **+34 / −2**, of which 32 added lines are an
explanatory comment. The functional change is four lines:

```
+    side = draw(st.sampled_from(("buy", "sell")))
+    limit2_order_type = draw(st.sampled_from(("limit", "market")))
-        side=draw(st.sampled_from(("buy", "sell"))),
+        side=side,
-        limit2_order_type=draw(st.sampled_from(("limit", "market"))),
+        limit2_order_type=limit2_order_type,
```

The two draws are hoisted to the top of `confluent_intent_sets`, ahead of the two `unique=True`
filtered list draws, the variable-length `st.text` and the two variable-length `st.sets` — the
repository's established remedy, already documented and applied in
`tests/property/test_order_idempotence_under_retry.py`'s generator for its own `flavour` draw.

**Not changed:** assertions, property semantics, the oracle, `P23_FLOORS` and their rationale,
the existence-floor framing, `max_examples` (still 100), deterministic coverage cases (none
exist), skip/xfail status (none), any other generator, any other test, any product code,
migration, execution gate or database configuration.

---

## 4. Post-change measurement — 20 independent in-test executions

Same mechanism, same count, same machine, nothing else running.

```
Start-Process powershell.exe -File staging\run_p23_draw_order_measure.ps1 POST20 20
→ exit 0;  20/20 PASSED;  total property time 731.9 s, mean 36.6 s/run
   log: staging/p23_measure_POST20.txt
```

| run | `side_buy` | `side_sell` | `l2_limit` | `l2_market` | verdict |
|---:|---:|---:|---:|---:|---|
| 1 | 38 | 62 | 45 | 55 | PASSED |
| 2 | 47 | 53 | 36 | 64 | PASSED |
| 3 | 40 | 60 | 45 | 55 | PASSED |
| 4 | 61 | 39 | 39 | 61 | PASSED |
| 5 | 42 | 58 | 49 | 51 | PASSED |
| 6 | 46 | 54 | 48 | 52 | PASSED |
| 7 | 56 | 44 | 38 | 62 | PASSED |
| 8 | 40 | 60 | 47 | 53 | PASSED |
| 9 | 57 | 43 | 51 | 49 | PASSED |
| 10 | 56 | 44 | 53 | 47 | PASSED |
| 11 | 64 | 36 | 61 | 39 | PASSED |
| 12 | 64 | 36 | **26** | 74 | PASSED |
| 13 | 34 | 66 | 42 | 58 | PASSED |
| 14 | 47 | 53 | 50 | 50 | PASSED |
| 15 | 45 | 55 | 39 | 61 | PASSED |
| 16 | 35 | 65 | 77 | **23** | PASSED |
| 17 | 49 | 51 | 69 | 31 | PASSED |
| 18 | 48 | 52 | 56 | 44 | PASSED |
| 19 | 63 | 37 | 46 | 54 | PASSED |
| 20 | 67 | 33 | 36 | 64 | PASSED |

| bucket | floor | min | mean | max | min margin | breaches |
|---|---:|---:|---:|---:|---:|---|
| `side_buy` | 20 | 34 | **50.0** | 67 | 1.70x | 0/20 |
| `side_sell` | 20 | 33 | **50.0** | 66 | **1.65x** | 0/20 |
| `limit2_was_a_second_limit_order` | 20 | **26** | 47.6 | 77 | 1.30x | 0/20 |
| `limit2_was_a_second_market_order` | 20 | **23** | 52.4 | 74 | **1.15x** | 0/20 |

### Side-by-side

| bucket | arm | mean | sd | min | max | min margin | \|mean−50\| |
|---|---|---:|---:|---:|---:|---:|---:|
| `side_buy` | baseline | 68.0 | 5.7 | 58 | 78 | 2.90x | 18.0 |
| `side_buy` | post | 50.0 | 10.4 | 34 | 67 | 1.70x | **0.0** |
| `side_sell` | baseline | 32.0 | 5.7 | 22 | 42 | 1.10x | 18.0 |
| `side_sell` | post | 50.0 | 10.4 | 33 | 66 | **1.65x** | **0.0** |
| `l2_limit` | baseline | 65.8 | 6.9 | 55 | 78 | 2.75x | 15.8 |
| `l2_limit` | post | 47.6 | 11.8 | 26 | 77 | 1.30x | 2.4 |
| `l2_market` | baseline | 34.2 | 6.9 | 22 | 45 | 1.10x | 15.8 |
| `l2_market` | post | 52.4 | 11.8 | 23 | 74 | **1.15x** | 2.4 |

### Why `limit2_order_type`'s minimum barely moved — the honest reading

The bias correction is real for both dimensions (18.0 → 0.0 and 15.8 → 2.4), but the per-run
standard deviation roughly doubled in both (5.7 → 10.4 and 6.9 → 11.8). That is not noise, it
is the expected consequence of the change: a draw hoisted to the front of the generator becomes
an **early** draw, and under Hypothesis's tree-guided generation the early draws are precisely
the ones held fixed while many children are explored beneath one prefix, so a single run
samples them fewer times and more coarsely.

This is the same mechanism recorded for TEST-05 in
`staging/PHASE10_TEST05_IDEMPOTENCY_REPORT.md`, where the *earliest-but-one* draw (`length`)
showed extreme per-run variance — 0 to 31 occurrences of one value across runs — for exactly
this reason. The experiment therefore trades **bias** for **variance**:

* for `side`, the bias was 18 and the sd rose by 4.7, so the net effect on the minority
  bucket's minimum is strongly positive: 22 → 33, margin 1.10x → 1.65x;
* for `limit2_order_type`, the bias was 15.8 and the sd rose by 4.9, and the two roughly
  cancel: 22 → 23, margin 1.10x → 1.15x.

No aggregate average is being used to claim success. Both verdicts are read off the **minimum
across 20 runs**, which is the quantity the floor actually has to survive.

---

## 5. Supplementary generator-only diagnostic

Treated as a diagnostic only — it measures the *strategy*, not the in-test distribution, and the
two were measured to differ materially (pre-change, generator-only put `side_sell` at mean 32
with min 14, while in-test put it at mean 32.0 with min 22).

```
.venv\Scripts\python.exe staging\probe_phase10_sibling_drawn_buckets.py 20
```

| bucket | pre-change min (margin) | post-change min (margin) |
|---|---|---|
| `side_buy` | 50 (2.50x) | 22 (1.10x) |
| `side_sell` | **14 (0.70x — below floor, 2/20 runs short)** | 34 (1.70x) |
| `l2_limit` | 44 (2.20x) | **20 (1.00x — exactly at the floor)** |
| `l2_market` | 30 (1.50x) | 29 (1.45x) |

Means moved to 49.5 / 50.5 and 48.0 / 52.0, confirming the bias correction at strategy level
too. The only arm in this whole experiment that ever produced a **below-floor** count was
pre-change generator-only `side_sell`; post-change, nothing falls below the floor, though
`l2_limit` touches it exactly once.

---

## 6. Regression

All run sequentially with nothing else executing — P-23 asserts exact backoff delays
(`race.starved_sleeps.delays == [0.05, 0.10]`) and was observed in a prior pass to produce a
false red under heavy parallel load.

| # | command | result | duration | exit |
|---|---|---|---|---|
| 1 | focused P-23 (post-change) | **1 passed** | 105.77 s (wall 113.1 s) | 0 |
| 2 | `test_paper_confluence.py` + `test_paper_idempotence.py` + `test_order_idempotence_under_retry.py` + `test_property_coverage.py` | **19 passed** | 168.76 s (wall 186.8 s) | 0 |
| 3 | `tests/property/` (whole suite) | **138 passed, 1 xfailed, 3 xpassed** | 676.33 s | 0 |
| 4 | `staging/check_audit_mirrors_untouched.py` | mirrors untouched | — | 0 |

Run 3 is **identical** to the pre-change full-property baseline recorded in
`staging/PHASE10_TEST05_IDEMPOTENCY_REPORT.md` (138 passed, 1 xfailed, 3 xpassed): no test
changed outcome.

All pytest invocations used the no-database CI unit lane, with `AERORA_TEST_DATABASE_URL` and
`DATABASE_URL` cleared, and `-q -p no:cacheprovider -p no:warnings -o addopts=""`.

---

## 7. Change control

Final unstaged tracked diff under `tests/property/`:

```
255  9  tests/property/test_order_idempotence_under_retry.py    (prior TEST-05 task, untouched here)
 34  2  tests/property/test_paper_confluence.py                 (THIS TASK)
 69  9  tests/property/test_protected_logic_containment.py      (another workstream, untouched)
 34  7  tests/property/test_tenant_isolation_matrix.py          (another workstream, untouched)
```

* **Staged entries: 22, unchanged.** The index was never touched.
* **No commit.**
* **No audit JSON mirror modified**, proved rather than asserted
  (`staging/check_audit_mirrors_untouched.py`, exit 0): the three untouched mirrors remain
  byte-identical to each other at hash `4534ab814deb` — their state from *before* the prior
  TEST-05 task — and `full_audit_results.json` differs from each by exactly the five TEST-05
  differences made in that prior task and nothing else.
* No product code, migration, execution gate, exchange credential, production configuration or
  database state was touched.

New evidence files, all under the already-untracked `staging/`:

```
run_p23_draw_order_measure.ps1            detached runner for N in-test measurements
p23_measure_BASE20.txt                    the 20 baseline runs
p23_measure_POST20.txt                    the 20 post-change runs
summarize_p23_measure.py                  per-run table + floor-breach check
compare_p23_measure.py                    the two-arm comparison
check_audit_mirrors_untouched.py          the mirror-integrity proof
PHASE10_P23_DRAW_ORDER_REPORT.md          this report
```

---

## 8. Discrepancies and limitations

1. **No discrepancy between the implementation and its reported description** was found, so no
   stop was triggered. The draw order, the filtered/variable-length classification and the
   single-consumption of both draws were all confirmed before editing.
2. **20 runs cannot resolve a 1.15x tail.** Zero breaches in 40 in-test executions bounds the
   per-run breach probability loosely, not tightly. `limit2_order_type` is reported as still
   marginal on the strength of its *minimum* (23 against 20), not on an absence of failures.
3. **Each arm's 20 runs share one process.** Hypothesis seeds each run independently, but the
   runs share the process's import set and constants cache. The same method was used for both
   arms, so the comparison is like for like; the absolute variance may differ slightly from
   20 separate processes.
4. **The variance increase is a real cost of the remedy**, not an artifact. It is visible in
   both dimensions and is explained by the same tree-guided-generation mechanism that caused
   TEST-05. Hoisting a draw is the right fix for *bias* and is neutral-to-harmful for *per-run
   variance*.
5. **Not verified against PostgreSQL.** P-23 drives the `FakeSupabase` double.
6. **No venue-level correctness or exactly-once claim.** P-23 is the paper concurrency
   property; it never reaches a venue. Nothing here certifies trading.
7. **The two transient environment failures from the earlier pass remain on the record**
   (`PHASE10_TEST05_IDEMPOTENCY_REPORT.md` §6.6): the P-23 confluence failure under heavy
   parallel load, which has now passed in a further 41 consecutive executions (20 baseline +
   20 post-change + 1 focused), and the `MemoryError` in `test_paper_live_separation.py`.

---

## 9. Proposed next action — needs authorization, not applied

`limit2_order_type` is still a thin distribution floor (1.15x). Draw-order relocation has been
measured and is not sufficient for it, so the next step should **not** be another reordering.

The repository's own proven remedy for a thin census floor on a drawn value is TEST-05's: stop
drawing for the case and **force** it, leaving the rest of the example generated. Concretely,
force `limit2_order_type` into both values — either by adding deterministic `@example` cases, or
by driving both values per example the way P-22 forces its three flavours and P-21 its four
scenarios — which would convert that pair from a distribution floor into an arithmetic
consequence of forcing, as 19 of P-23's 23 floors already are. That is a change to the
property's example shape and its cost is runtime (P-23 already runs 60–120 s), so it needs
authorization and its own measurement pass.

Also still outstanding from the prior task: authorization to synchronize the TEST-05 entry into
the three audit JSON mirrors (`marketplace_audit.json`, `money_flow_audit.json`,
`trading_audit.json`), which this task was explicitly forbidden from touching.
