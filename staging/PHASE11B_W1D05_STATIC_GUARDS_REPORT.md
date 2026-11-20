# Phase 11B / W1D-05 — Static regression guards for the snapshot dimension set

**Both invariants are now enforced.** One new test file, 48 tests, 0 skips, no database.
No production source was edited, nothing was staged, and `git write-tree` is byte-identical
to the Phase 0 baseline.

---

## 1. Baseline and final preservation

| Property | Phase 0 | Phase 3 | |
| --- | --- | --- | --- |
| Branch / upstream | `main` / `origin/main` | same | **unchanged** |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | **unchanged** |
| Ahead / behind | `0 / 0` | `0 / 0` | **unchanged** |
| **`git write-tree`** | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **identical** | **index content unchanged** |
| Staged entries | 22 | 22 | **unchanged** |
| Unstaged | 100 | 100 | **unchanged** |
| Canonical staged-content digest | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **identical** | **unchanged** |
| `git status --porcelain` | 256 | — | other sessions active throughout |

`.git/index` SHA-256 was **not** used as the identity measure, per the brief. `write-tree`
is the stronger check: it hashes the index's *content* into a tree object, so an identical
value proves the 22 staged entries' paths, modes and blob SHAs are untouched regardless of
stat-cache churn. The canonical digest corroborates it independently and matches the value
published by a concurrent session in `PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` §1.1.

**Collision check:** `tests/test_snapshot_dimension_invariants.py` and
`staging/PHASE11B_W1D05_STATIC_GUARDS_REPORT.md` did not exist at Phase 0. Three other
candidate test names were also checked and were free.

**Forbidden paths, mtimes unchanged:** `scripts/verify_schema_drift.py` (10-06 13:17:28),
`scripts/verify_provisioning.py` (10-06 13:24:15), `scripts/provision_database.py`
(10-06 20:12:33), `staging/provisioning_snapshot.json` (10-06 20:14:20),
`staging/pg_dsn.txt` (10-10 17:55:23 — a concurrent session's bootstrap, preserved, not
touched here).

---

## 2. One correction to the task framing, found in Phase 0

The brief locates `SNAPSHOT_QUERIES` in `scripts/verify_schema_drift.py`. It is **defined
in `scripts/verify_provisioning.py`** (line 68) and only *imported* by
`verify_schema_drift.py:69`:

```python
from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm  # noqa: E402
```

The guards therefore import from the definition site. A test was added asserting
`verify_schema_drift.SNAPSHOT_QUERIES is verify_provisioning.SNAPSHOT_QUERIES` — object
identity — so a future redefinition that let the snapshot writer and the drift reader
disagree about what is compared would fail. Verified: `True` today.

Four consumers exist (`verify_schema_drift.py` ×2 sites, `verify_provisioning.py`
`diff_snapshots`, `verify_production_schema.py`), all iterating `for key in
SNAPSHOT_QUERIES` and reading `.get(key, [])`. The guards match that data structure: a
`dict[str, str]` of dimension name → SQL.

---

## 3. Files changed

| File | Change | Reason |
| --- | --- | --- |
| `tests/test_snapshot_dimension_invariants.py` | **new**, 48 tests | the two guards, their negative controls, and the key declaration |
| `staging/probe_p11b_w1d05_guard_mutation.py` | **new**, read-only probe | mutation-tests the guards for non-vacuity (§5) |
| `staging/p11b_w1d05_guard_mutation.txt` | **new**, log | its output |
| `staging/PHASE11B_W1D05_STATIC_GUARDS_REPORT.md` | **new** | this report |

**No existing file was modified.** All four are untracked (`??`); none was staged.

### 3.1 Where the key declaration lives, and why

`COMPARISON_KEY` is defined **in the test file**, not in `scripts/`, because this task is
forbidden from editing `scripts/verify_schema_drift.py`. The guard still works: adding a
dimension to `SNAPSHOT_QUERIES` without adding it to `COMPARISON_KEY` fails
`test_every_dimension_declares_a_comparison_key`.

When AP-W1D-3 implements the four-way classification, the declaration belongs beside
`compare()` and this file should import it instead of defining it — at which point the
test becomes a check on production data rather than on a test constant. This intermediate
state is documented in the file's docstring so it is not mistaken for the intended end
state.

---

## 4. Guard design

### Guard A — total ordering

`test_every_dimension_has_a_result_set_order_by` plus
`test_order_by_covers_the_declared_key` (parametrised over all 16 dimensions).

**Not a substring check.** Two properties made that insufficient:

1. **`ORDER BY` appears inside subqueries.** `primary_keys` orders an
   `ARRAY(SELECT a.attname … ORDER BY a.attname)` and `enum_types` orders an
   `ARRAY(SELECT e.enumlabel … ORDER BY e.enumsortorder)`. Those order the contents of one
   *cell* and say nothing about row order. The extractor finds the clause at **parenthesis
   depth 0, outside string literals**, so a subquery clause cannot satisfy the guard.
2. **An `ORDER BY` on a non-unique prefix is as bad as none.** The guard requires the
   clause to **cover the declared key**: the first `key_width` sort expressions must equal
   the first `key_width` selected expressions, compared after dropping alias qualifiers
   and collapsing whitespace. Since the key is unique by construction, covering it makes
   the ordering total.

This is what ties the two guards together — Guard B's declaration is not a free
annotation, it is the thing Guard A measures against.

Column splitting tracks parenthesis depth **and** single-quoted literals, because
`policies` selects `array_to_string(roles, ',')` — a comma inside parentheses and a comma
inside a string literal. `--` comments are stripped first; `tables` carries a six-line
rationale comment that mentions `relname`.

All 16 queries pass. **The existing queries were not modified.**

### Guard B — explicit key width

`test_every_dimension_declares_a_comparison_key` (no missing, no extra),
`test_a_declared_key_is_whole_row_or_a_valid_width` (parametrised), and
`test_whole_row_dimensions_really_have_no_attribute_columns`.

A whole-row key is the explicit sentinel `WHOLE_ROW = "whole-row"`, **not `None`**, because
`dict.get()` returns `None` for an absent key: "this dimension has no attribute columns"
is a reviewed conclusion, while "nobody declared this dimension" is an omission, and `None`
would collapse them. `test_a_declared_key_is_whole_row_or_a_valid_width` rejects `None`
explicitly.

A `WHOLE_ROW` declaration is itself checked rather than trusted — if such a dimension later
gains a descriptive column, the ORDER BY cross-check fails, so the claim cannot rot
silently.

**Measured shape** (computed by the suite, not quoted — it matches W1D §3.3's hand-derived
table exactly):

```
dimension                         cols        key  attrs  advisory
----------------------------------------------------------------------
alembic_version                      1  whole-row      0  advisory
columns                              9          2      7
enum_types                           2          1      1
extensions                           1  whole-row      0
foreign_keys                         3          2      1
functions                            3          2      1
indexes                              3          2      1
policies                             7          2      5
primary_keys                         3          2      1
privileges                           3  whole-row      0
recorded_migrations                  3          1      2  advisory
row_level_security                   3          1      2
sequences                            1  whole-row      0
tables                               2          1      1
triggers                             3          2      1
unique_and_check_constraints         4          2      2
----------------------------------------------------------------------
dimensions: 16   with attribute columns (W1C-02 applies): 12
```

### Out of scope, as instructed

No four-way classification was implemented. `compare()` was not touched. No existing
output or pass/fail semantics changed.

---

## 5. Test results

All commands read-only. No database, Redis or other external service was contacted.

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **48 passed**, 0 failed, **0 skipped**, in 6.83s |
| 2 | same, under `PGHOST=255.255.255.255 PGPORT=1 PGCONNECT_TIMEOUT=1`, `AERORA_TEST_DATABASE_URL=''`, `AERORA_TEST_REDIS_URL=''` | **0** | **48 passed in 2.35s** |
| 3 | `python staging/probe_p11b_w1d05_guard_mutation.py` | **0** | 12 mutations applied, **12 bit, 0 silent** |
| 4 | `python -m pytest tests/test_snapshot_dimension_invariants.py tests/test_schema_drift_checker_is_read_only_postgres.py tests/test_migration_tooling.py tests/test_orm_models_match_provisioned_schema.py -q --no-header` | **0** | **88 passed, 12 skipped, 1 xfailed**, 0 failed, in 27.16s |

### 5.1 No external connection — established, not assumed

Run 2 is the evidence. Under a routed-to-nowhere `PGHOST` with a 1-second connect timeout,
48 tests completed in **2.35 s**. A single attempted connection could not finish that fast.
Separately, importing both `scripts.verify_provisioning` and `scripts.verify_schema_drift`
under the same poisoned environment took **0.170 s**, confirming neither connects at import
time.

`psycopg2-binary==2.9.9` is pinned in `requirements-base.txt`, so these tests run in the
default lane and **never skip**. That was deliberate: a skip here would be
indistinguishable from a pass, and the invariants are the whole point.

### 5.2 Accounting for run 4

| Count | Disposition |
| --- | --- |
| 88 passed | 48 new guards + 40 from `test_migration_tooling.py` and `test_orm_models_match_provisioned_schema.py` |
| **12 skipped** | all of `test_schema_drift_checker_is_read_only_postgres.py`, gated on `AERORA_TEST_DATABASE_URL`. **Deliberately not run** — it needs a live PostgreSQL, which this task is forbidden from contacting. Not counted as a pass. |
| 1 xfailed | `test_only_one_model_claims_the_positions_table`, a pre-existing `strict=True` marker; unrelated |
| 0 failed | — |

Only two test files in the repository import the drift/provisioning modules, and the new
file is one of them, so the relevant surface is fully covered.

### 5.3 Negative controls

Ten are inside the test file, exercising the extractor helpers on synthetic SQL: no
`ORDER BY`; an `ORDER BY` only inside a subquery; one missing part of the key; one on the
wrong columns; a new dimension with no declaration; a stale declaration; `None` vs absent;
commas inside calls and literals; comment stripping. One asserts the property on the
**live** `enum_types` definition, that the extracted clause is not the subquery's.

---

## 6. Non-vacuity — the guards were mutation-tested

Negative controls on helper functions can pass while the guard that consumes them fails to
assert. `staging/probe_p11b_w1d05_guard_mutation.py` therefore drives the **real guard test
functions** with the **real dicts** perturbed in memory (reverted in a `finally`), and
requires each to raise `AssertionError`:

| # | Mutation | Result |
| --- | --- | --- |
| M1 / M1b | `policies`: `ORDER BY` removed | **BITES** (both guards) |
| M2 | `policies`: `ORDER BY` narrowed to a non-unique prefix | **BITES** |
| M3 | `policies`: `ORDER BY` on the wrong columns | **BITES** |
| M4 | `enum_types`: result-set `ORDER BY` removed, subquery one left | **BITES** |
| M5 | a new dimension with no key declaration | **BITES** |
| M6 | a stale key declaration for a removed dimension | **BITES** |
| M7 | `privileges`: key declared `None` instead of `WHOLE_ROW` | **BITES** |
| M8 | `tables`: key width 9 against 2 selected columns | **BITES** |
| M9 | `sequences`: a `WHOLE_ROW` dimension gains an attribute column | **BITES** |
| M10 | `verify_schema_drift` redefines `SNAPSHOT_QUERIES` | **BITES** |
| M11 | `ADVISORY_DIMENSIONS` contains a typo | **BITES** |

**12 applied, 12 rejected, 0 silent.** The probe re-verifies afterwards that
`SNAPSHOT_QUERIES` is still the real object and both dicts are restored — confirmed.

M4 is the one that matters most: two real dimensions legitimately contain subquery
`ORDER BY` clauses, so an extractor that accepted those would pass Guard A for a dimension
whose result set is unordered — the exact defect being guarded against, in disguise.

---

## 7. Are both invariants enforced?

| Invariant | Enforced | How it fails if broken |
| --- | --- | --- |
| **1. Every dimension has a total `ORDER BY`** | **YES** | `test_every_dimension_has_a_result_set_order_by` (absence) and `test_order_by_covers_the_declared_key` (insufficiency, per dimension). Subquery clauses do not count. |
| **2. Every dimension declares a comparison key** | **YES** | `test_every_dimension_declares_a_comparison_key` (missing or extra names), `test_a_declared_key_is_whole_row_or_a_valid_width` (`None` or out-of-range), `test_whole_row_dimensions_really_have_no_attribute_columns` (a stale `WHOLE_ROW`). |

Both run in the default `pytest tests/` lane with no gate and no skip.

---

## 8. Unresolved limitations

1. **Key widths are asserted against the SQL, not against the live catalogue.** Guard A
   checks that the `ORDER BY` covers the declared key; it cannot verify that the declared
   key is genuinely *unique* in PostgreSQL. That rests on documented catalogue properties
   (policy names unique per table, index names unique per schema, `filename` being the PK
   of `schema_migrations` per `provision_database.py:154`), recorded as comments against
   each entry. Confirming uniqueness empirically needs a live database.
2. **`COMPARISON_KEY` lives in the test file.** Production code has no key declaration, so
   `compare()` still compares whole tuples and W1C-02's misreporting is unchanged. The
   guard prevents a *new* dimension from being added without a decision; it does not fix
   the existing behaviour.
3. **The declaration is a reviewed judgement, not a derivation.** A future dimension could
   be given a deliberately wrong width that still satisfies Guard A if its `ORDER BY` were
   written to match. The guard forces the decision to be made and visible in review; it
   cannot force it to be correct.
4. **`row_level_security` and `recorded_migrations` carry 2 attribute columns each** and are
   as exposed to W1C-02 as `policies` is. Noted, not addressed here.
5. **No live-database verification of any kind** was performed in this task.

---

## 9. Status — unchanged by this task

| | |
| --- | --- |
| **W1D-05** | **CLOSED** — both invariants enforced, guards mutation-tested, 48/48 passing with 0 skips |
| **W1C-01** | OPEN pending DB-owner review |
| **W1C-02** | OPEN pending implementation and regression evidence — the classification was **not** implemented |
| **W1C-03** | OPEN pending authorized snapshot-provenance change |
| **W1D-01 … W1D-04, W1D-06 … W1D-08** | OPEN — none of their closure criteria were addressed |
| **TENANT-01** | OPEN |
| **QUOTA-01** | OPEN |
| **PostgreSQL integration lane** | UNEXECUTED |
| **Live trading** | OFF / NOT CERTIFIED |

Confirmed: no edit to `scripts/verify_schema_drift.py`, migration files, the provisioning
snapshot, DSN files, roles, grants or shared PostgreSQL resources. No package installed, no
server started, no database contacted, nothing staged, no commit, no push, no CI trigger.
Skipped tests are never counted as passes.
