"""Two structural invariants of the provisioning snapshot's dimension set.

PHASE 11B / W1D-05. Closes the guard identified in
`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` section 6 as the smallest next
action: the only item in that report completable with no live database, no role change
and no schema change.

WHY THIS FILE EXISTS
--------------------
`scripts/verify_schema_drift.py:compare()` decides whether a live schema has drifted from
`staging/provisioning_snapshot.json`. Its correctness rests on two properties of
`SNAPSHOT_QUERIES` that nothing currently enforces, and that a future dimension could
silently break.

**1. Every dimension must be totally ordered.** `compare()` begins:

    if expected == actual:
        continue
    missing = [r for r in expected if r not in actual]
    extra   = [r for r in actual   if r not in expected]

The fast path is Python LIST equality, which is order-sensitive. The fallback is
order-INsensitive. So a dimension whose rows arrived with identical content in a
different order falls past the fast path and then produces `0 missing, 0 unexpected` --
a drift report naming no items at all, counted as a HARD FAILURE for any
non-advisory dimension. Today every query carries an `ORDER BY` that determines the
order, so this cannot fire. Nothing checks it.

**2. Every dimension must declare its comparison key.** `compare()` tests whole-tuple
membership (`r not in actual`), so it has no notion of an object's identity separate from
its attributes. A dimension whose SELECT list is wider than its natural key therefore
reports a MODIFIED object twice -- once as missing, once as extra -- with nothing linking
the halves. W1D measured that as affecting 12 of the 16 dimensions. Fixing the reporting
is a separate, larger task (W1C-02, approval AP-W1D-3); what this file does is make the
key width an explicit, reviewed property of every dimension, so the eventual fix has a
declaration to build on and a new dimension cannot be added without someone deciding
what identifies a row in it.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not implement the four-way unchanged/modified/missing/added classification, does
not change `compare()`, and does not alter any existing output or pass/fail semantics.
`scripts/verify_schema_drift.py` and `scripts/verify_provisioning.py` are read, never
written. W1C-02 stays OPEN.

NO DATABASE, NO NETWORK, NO SERVER
----------------------------------
Every assertion below is a property of SQL text and a Python dict. The only imports are
the two scripts that define and consume `SNAPSHOT_QUERIES`; both bind definitions only
and neither connects at import time. That was verified empirically rather than assumed:
importing both under `PGHOST=255.255.255.255` with `PGCONNECT_TIMEOUT=1` completes in
0.17s, which it could not do if a connection were attempted. `psycopg2-binary==2.9.9` is
a pinned dependency in `requirements-base.txt`, so these tests run in the default lane
and never skip -- a skip here would be indistinguishable from a pass, and the invariants
are the whole point.

WHERE THE DECLARATION LIVES, AND WHERE IT SHOULD END UP
-------------------------------------------------------
`COMPARISON_KEY` is defined in this test file rather than in `scripts/`. That is
deliberate for this task, which is scoped to adding guards and is explicitly forbidden
from editing `scripts/verify_schema_drift.py`. When AP-W1D-3 implements the
classification, the declaration belongs beside `compare()` and this file should import it
instead of defining it -- at which point `test_every_dimension_declares_a_comparison_key`
becomes a check on production data rather than on a test constant. Recorded here so the
intermediate state is not mistaken for the intended end state.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.verify_provisioning import SNAPSHOT_QUERIES  # noqa: E402
from scripts.verify_schema_drift import ADVISORY_DIMENSIONS  # noqa: E402
from scripts import verify_schema_drift as _drift  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  The declaration under test
#
#  UPDATED under AP-W1D-3 (W1D-13). `WHOLE_ROW` and `COMPARISON_KEY` were previously
#  DEFINED here, because the task that added these guards was forbidden from editing
#  `scripts/`. They now live beside `SNAPSHOT_QUERIES` in `scripts/verify_provisioning.py`
#  and are IMPORTED, which is the end state this file's docstring anticipated: every guard
#  below now checks the declaration production actually classifies with, rather than a
#  test-local copy that could drift away from it.
# ─────────────────────────────────────────────────────────────────────────────

from scripts.verify_provisioning import COMPARISON_KEY, WHOLE_ROW  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  SQL structure extraction
#
#  Deliberately not regex-only. `ORDER BY` appears INSIDE subqueries in two dimensions
#  (`primary_keys` orders an `ARRAY(SELECT a.attname ... ORDER BY a.attname)`, and
#  `enum_types` orders `ARRAY(SELECT e.enumlabel ... ORDER BY e.enumsortorder)`), so a
#  search for the first or last `ORDER BY` finds a clause that orders the contents of one
#  cell and says nothing about row order. Only a clause at parenthesis depth 0 orders the
#  result set. Column splitting has the same problem in reverse: `array_to_string(roles,
#  ',')` contains a comma inside parentheses AND a comma inside a string literal, so
#  depth and quoting both have to be tracked.
# ─────────────────────────────────────────────────────────────────────────────

def strip_comments(sql: str) -> str:
    """Remove `--` line comments. Several queries carry long rationale comments, and
    `tables`' comment mentions `relname` and `FORCE`, so leaving them in corrupts both
    the column split and the ORDER BY search."""
    return re.sub(r"--[^\n]*", "", sql)


def _depth_zero_spans(sql: str):
    """Yield (index, char, depth, in_quote) for the whole string, tracking both."""
    depth = 0
    in_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_quote:
            if ch == "'" and sql[i + 1:i + 2] == "'":
                yield i, ch, depth, True
                yield i + 1, ch, depth, True
                i += 2
                continue
            if ch == "'":
                in_quote = False
            yield i, ch, depth, True
            i += 1
            continue
        if ch == "'":
            in_quote = True
            yield i, ch, depth, True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        yield i, ch, depth, False
        i += 1


def outer_order_by(sql: str) -> str | None:
    """The `ORDER BY` clause that orders the RESULT SET, or None.

    Found at parenthesis depth 0 and outside string literals. Returns the text after the
    keyword, up to the end of the statement.
    """
    text = strip_comments(sql)
    lowered = text.lower()
    best = None
    for i, ch, depth, in_quote in _depth_zero_spans(text):
        if in_quote or depth != 0 or ch not in "oO":
            continue
        if lowered.startswith("order", i) and re.match(r"order\s+by\b", lowered[i:]):
            best = i
    if best is None:
        return None
    m = re.match(r"order\s+by\b", lowered[best:])
    return text[best + m.end():].strip()


def split_top_level(expr_list: str) -> list[str]:
    """Split on commas at parenthesis depth 0, outside string literals."""
    parts: list[str] = []
    buf: list[str] = []
    for i, ch, depth, in_quote in _depth_zero_spans(expr_list):
        if ch == "," and depth == 0 and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def select_columns(sql: str) -> list[str]:
    """The top-level SELECT list expressions, in order."""
    text = strip_comments(sql)
    lowered = text.lower()
    start = None
    for i, ch, depth, in_quote in _depth_zero_spans(text):
        if in_quote or depth != 0:
            continue
        if lowered.startswith("select", i):
            start = i + len("select")
            break
    assert start is not None, f"no top-level SELECT found in: {text[:80]!r}"
    end = len(text)
    for i, ch, depth, in_quote in _depth_zero_spans(text):
        if in_quote or depth != 0 or i <= start:
            continue
        if re.match(r"from\b", lowered[i:]):
            end = i
            break
    return split_top_level(text[start:end])


def normalise(expr: str) -> str:
    """Compare expressions by meaning, not by spelling.

    Table aliases differ between a SELECT list and an ORDER BY in principle, and
    whitespace differs in practice (`SELECT t.relname, c.conname` vs a multi-line
    `ORDER BY`). Alias prefixes are dropped and whitespace collapsed so
    `pg_get_function_identity_arguments(p.oid)` in a SELECT list matches the same call in
    an ORDER BY.
    """
    out = re.sub(r"\s+", " ", expr).strip().lower()
    out = re.sub(r"\b[a-z_][a-z0-9_]*\.", "", out)      # drop alias qualifiers
    out = re.sub(r"\s+(asc|desc)$", "", out)
    return out.strip()


def key_width(dimension: str) -> int:
    """Declared key width in columns, resolving the WHOLE_ROW sentinel."""
    declared = COMPARISON_KEY[dimension]
    if declared is WHOLE_ROW:
        return len(select_columns(SNAPSHOT_QUERIES[dimension]))
    return int(declared)


# ═════════════════════════════════════════════════════════════════════════════
#  GUARD A — every dimension is totally ordered
# ═════════════════════════════════════════════════════════════════════════════

def test_every_dimension_has_a_result_set_order_by():
    """A dimension with no top-level ORDER BY makes `compare()`'s fast path unreliable.

    Checked at parenthesis depth 0, so the subquery ORDER BY clauses in `primary_keys`
    and `enum_types` do not satisfy this on their own.
    """
    missing = [d for d, sql in SNAPSHOT_QUERIES.items() if outer_order_by(sql) is None]
    assert missing == [], (
        f"these dimensions have no ORDER BY on the result set: {missing}. "
        f"`verify_schema_drift.compare()` compares the two row LISTS for equality "
        f"before falling back to membership tests, so an unordered dimension can "
        f"report '0 missing, 0 unexpected' and still be counted as a hard failure."
    )


@pytest.mark.parametrize("dimension", sorted(SNAPSHOT_QUERIES))
def test_order_by_covers_the_declared_key(dimension):
    """THE SUBSTANTIVE ORDERING CHECK. Presence of `ORDER BY` is not enough.

    An `ORDER BY` that sorts on a non-unique prefix leaves rows within a group in an
    arbitrary order, which is the same defect as having no ORDER BY at all. So the clause
    is required to cover the dimension's declared key: the first `key_width` sort
    expressions must be the first `key_width` selected expressions. Because the key is
    unique by construction (see `COMPARISON_KEY`), covering it makes the ordering total.

    This is also what ties the two guards together -- Guard B's declaration is not a free
    annotation, it is the thing Guard A measures against.
    """
    sql = SNAPSHOT_QUERIES[dimension]
    order_clause = outer_order_by(sql)
    assert order_clause is not None, f"{dimension} has no result-set ORDER BY"

    columns = [normalise(c) for c in select_columns(sql)]
    order_terms = [normalise(t) for t in split_top_level(order_clause)]
    width = key_width(dimension)

    assert len(columns) >= width, (
        f"{dimension}: declared key width {width} exceeds its "
        f"{len(columns)} selected columns"
    )
    assert len(order_terms) >= width, (
        f"{dimension}: ORDER BY has {len(order_terms)} term(s) but the declared key is "
        f"{width} column(s) wide, so rows sharing a key prefix are unordered. "
        f"ORDER BY: {order_clause!r}"
    )
    assert order_terms[:width] == columns[:width], (
        f"{dimension}: ORDER BY does not cover the declared key.\n"
        f"  key columns  (first {width} selected): {columns[:width]}\n"
        f"  ORDER BY     (first {width} terms)   : {order_terms[:width]}\n"
        f"Rows are therefore not deterministically ordered, and `compare()`'s "
        f"list-equality fast path can misfire."
    )


# ═════════════════════════════════════════════════════════════════════════════
#  GUARD B — every dimension declares its comparison key
# ═════════════════════════════════════════════════════════════════════════════

def test_every_dimension_declares_a_comparison_key():
    """No missing and no extra dimension names.

    The failure this prevents: a dimension added to `SNAPSHOT_QUERIES` without anyone
    deciding what identifies one of its rows. `compare()` would then compare it by whole
    tuple -- which is correct only when the key IS the whole tuple -- and any
    modification in it would be reported as a deletion plus an addition.
    """
    declared = set(COMPARISON_KEY)
    actual = set(SNAPSHOT_QUERIES)
    assert declared == actual, (
        f"COMPARISON_KEY and SNAPSHOT_QUERIES disagree.\n"
        f"  dimensions with no declared key : {sorted(actual - declared)}\n"
        f"  declared but not a dimension    : {sorted(declared - actual)}"
    )


@pytest.mark.parametrize("dimension", sorted(COMPARISON_KEY))
def test_a_declared_key_is_whole_row_or_a_valid_width(dimension):
    """`None` is not an acceptable declaration, and a width must be in range.

    `WHOLE_ROW` says "reviewed: this dimension has no attribute columns". `None` would be
    indistinguishable from an absent entry under `dict.get`, which is the confusion this
    sentinel exists to prevent.
    """
    declared = COMPARISON_KEY[dimension]
    assert declared is not None, (
        f"{dimension}: key is None. Use WHOLE_ROW to state that the whole row is the "
        f"key; None cannot be told apart from an undeclared dimension."
    )
    ncols = len(select_columns(SNAPSHOT_QUERIES[dimension]))
    if declared is WHOLE_ROW:
        return
    assert isinstance(declared, int) and not isinstance(declared, bool), (
        f"{dimension}: key must be WHOLE_ROW or an int, got {type(declared).__name__}"
    )
    assert 1 <= declared <= ncols, (
        f"{dimension}: declared key width {declared} is outside 1..{ncols} "
        f"(the number of selected columns)"
    )


def test_whole_row_dimensions_really_have_no_attribute_columns():
    """A WHOLE_ROW declaration is a claim, so it is checked.

    If a dimension declared WHOLE_ROW later gains a descriptive column, the declaration
    becomes wrong silently -- and that dimension would then misreport modifications with
    nobody having changed the declaration. The ORDER BY is the cross-check: for a
    whole-row key every selected column must be a sort term.
    """
    for dimension, declared in sorted(COMPARISON_KEY.items()):
        if declared is not WHOLE_ROW:
            continue
        sql = SNAPSHOT_QUERIES[dimension]
        columns = [normalise(c) for c in select_columns(sql)]
        order_terms = [normalise(t) for t in split_top_level(outer_order_by(sql) or "")]
        assert order_terms[:len(columns)] == columns, (
            f"{dimension} is declared WHOLE_ROW, which means every selected column is "
            f"part of the key, but its ORDER BY does not sort on all of them.\n"
            f"  columns : {columns}\n"
            f"  ORDER BY: {order_terms}\n"
            f"Either a descriptive column was added -- in which case give it a numeric "
            f"key width -- or the ORDER BY needs extending."
        )


# ═════════════════════════════════════════════════════════════════════════════
#  Consistency of the surrounding assumptions
# ═════════════════════════════════════════════════════════════════════════════

def test_the_drift_checker_shares_the_one_dimension_set():
    """`verify_schema_drift` imports `SNAPSHOT_QUERIES` rather than redefining it.

    Asserted on object identity. Two definitions of "the dimensions" would let the
    snapshot writer and the drift reader disagree about what is compared -- the same
    class of defect Workstream I removed for DSN writing, where two implementations of
    "safe to record" meant the weaker one set the real guarantee.
    """
    assert _drift.SNAPSHOT_QUERIES is SNAPSHOT_QUERIES, (
        "scripts/verify_schema_drift.py no longer shares the SNAPSHOT_QUERIES object "
        "defined in scripts/verify_provisioning.py"
    )


def test_advisory_dimensions_are_real_dimensions():
    """A typo in ADVISORY_DIMENSIONS silently promotes a bookkeeping difference to a
    hard failure, because `compare()` routes on membership of that set."""
    unknown = sorted(set(ADVISORY_DIMENSIONS) - set(SNAPSHOT_QUERIES))
    assert unknown == [], (
        f"ADVISORY_DIMENSIONS names dimensions that do not exist: {unknown}"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  NEGATIVE CONTROLS
#
#  Without these, every assertion above could be vacuous -- a parser that never finds a
#  problem passes a suite of "there are no problems" assertions. Each control feeds the
#  same functions a synthetic definition that SHOULD be rejected, and asserts it is.
#  Synthetic text only; no database, no fixture, no real dimension is modified.
# ═════════════════════════════════════════════════════════════════════════════

def test_control_a_dimension_without_any_order_by_is_detected():
    sql = "SELECT a, b FROM t WHERE x = 1"
    assert outer_order_by(sql) is None


def test_control_an_order_by_only_inside_a_subquery_is_not_counted():
    """THE CONTROL THAT MATTERS MOST for Guard A.

    `primary_keys` and `enum_types` both contain a real subquery ORDER BY. If the
    extractor accepted those, Guard A would pass for a dimension whose result set is
    unordered -- which is precisely the defect being guarded against, wearing a
    disguise.
    """
    sql = """
        SELECT t.typname, array_to_string(ARRAY(
                   SELECT e.enumlabel FROM pg_enum e
                   WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder
               ), ',')
        FROM pg_type t
    """
    assert outer_order_by(sql) is None, (
        "a subquery ORDER BY was mistaken for a result-set ORDER BY"
    )


def test_control_the_real_enum_types_query_is_not_satisfied_by_its_subquery():
    """The same property, on the live definition rather than a synthetic one."""
    clause = outer_order_by(SNAPSHOT_QUERIES["enum_types"])
    assert clause is not None
    assert "enumsortorder" not in clause.lower(), (
        f"the extracted clause is the subquery's, not the result set's: {clause!r}"
    )
    assert normalise(split_top_level(clause)[0]) == "typname"


def test_control_an_order_by_that_misses_part_of_the_key_is_detected():
    """A 2-column key ordered on only its first column leaves ties unordered."""
    sql = "SELECT tablename, policyname, cmd FROM pg_policies ORDER BY tablename"
    columns = [normalise(c) for c in select_columns(sql)]
    order_terms = [normalise(t) for t in split_top_level(outer_order_by(sql))]
    assert len(order_terms) < 2, "control is not exercising the shortfall it claims to"
    assert order_terms[:2] != columns[:2]


def test_control_an_order_by_on_the_wrong_columns_is_detected():
    """Right arity, wrong columns -- arity alone must not satisfy Guard A."""
    sql = "SELECT tablename, policyname, cmd FROM pg_policies ORDER BY cmd, tablename"
    columns = [normalise(c) for c in select_columns(sql)]
    order_terms = [normalise(t) for t in split_top_level(outer_order_by(sql))]
    assert len(order_terms) == 2
    assert order_terms[:2] != columns[:2]


def test_control_a_new_dimension_without_a_key_declaration_is_detected():
    """Guard B's failure mode, simulated without touching the real dicts."""
    queries = dict(SNAPSHOT_QUERIES)
    queries["a_new_dimension"] = "SELECT x FROM y ORDER BY x"
    assert set(COMPARISON_KEY) != set(queries)
    assert sorted(set(queries) - set(COMPARISON_KEY)) == ["a_new_dimension"]


def test_control_a_stale_key_declaration_is_detected():
    """The other direction: a declaration left behind after a dimension is removed."""
    keys = dict(COMPARISON_KEY)
    keys["a_removed_dimension"] = 1
    assert sorted(set(keys) - set(SNAPSHOT_QUERIES)) == ["a_removed_dimension"]


def test_control_a_none_key_is_distinguishable_from_an_absent_one():
    """Why WHOLE_ROW is a sentinel and not `None`."""
    keys: dict[str, object] = {"declared_none": None}
    assert keys.get("declared_none") is None
    assert keys.get("never_declared") is None
    assert "declared_none" in keys and "never_declared" not in keys
    assert WHOLE_ROW is not None


def test_control_column_splitting_survives_commas_in_calls_and_literals():
    """`policies` selects `array_to_string(roles, ',')` -- a comma inside parentheses AND
    a comma inside a string literal. A naive split would report 9 columns, inflating the
    arity Guard A compares against."""
    sql = ("SELECT tablename, policyname, permissive, array_to_string(roles, ','), cmd, "
           "coalesce(qual, ''), coalesce(with_check, '') "
           "FROM pg_policies ORDER BY tablename, policyname")
    assert len(select_columns(sql)) == 7
    assert len(select_columns(SNAPSHOT_QUERIES["policies"])) == 7


def test_control_comment_stripping_does_not_eat_real_sql():
    """`tables` carries a six-line rationale comment that mentions `relname` and `FORCE`.
    Leaving it in corrupts the column split; removing too much would corrupt the query."""
    columns = select_columns(SNAPSHOT_QUERIES["tables"])
    assert len(columns) == 2, columns
    assert normalise(columns[0]) == "relname"
    assert "pg_get_userbyid" in normalise(columns[1])


# ═════════════════════════════════════════════════════════════════════════════
#  Visibility: the measured shape, so a reviewer can see what was asserted
# ═════════════════════════════════════════════════════════════════════════════

def test_the_measured_dimension_shape_is_reported():
    """Not a guard -- a record. Prints the table W1D derived by hand, now computed.

    Kept as a test so the numbers are regenerated on every run rather than quoted from a
    report that can go stale.
    """
    lines = ["", f"{'dimension':32} {'cols':>5} {'key':>10} {'attrs':>6}  advisory"]
    lines.append("-" * 70)
    vulnerable = 0
    for dimension in sorted(SNAPSHOT_QUERIES):
        ncols = len(select_columns(SNAPSHOT_QUERIES[dimension]))
        declared = COMPARISON_KEY[dimension]
        width = key_width(dimension)
        attrs = ncols - width
        if attrs:
            vulnerable += 1
        shown = "whole-row" if declared is WHOLE_ROW else str(declared)
        lines.append(
            f"{dimension:32} {ncols:>5} {shown:>10} {attrs:>6}  "
            f"{'advisory' if dimension in ADVISORY_DIMENSIONS else ''}"
        )
    lines.append("-" * 70)
    lines.append(f"dimensions: {len(SNAPSHOT_QUERIES)}   "
                 f"with attribute columns (W1C-02 applies): {vulnerable}")
    print("\n".join(lines))
    assert len(SNAPSHOT_QUERIES) == len(COMPARISON_KEY)
