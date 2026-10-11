"""Runtime regression tests for the schema-drift classifier (W1C-02 / AP-W1D-3).

WHY THIS FILE EXISTS
--------------------
`tests/test_snapshot_dimension_invariants.py` (W1D-05) constrains the SQL and the key
declaration: every dimension has a total `ORDER BY`, and every dimension declares what
identifies one of its rows. Those are STATIC properties of the query text.

Nothing tested how two already-ordered result sets are actually DIFFED, and no test in the
repository called `compare()`, `diff_snapshots()` or `check_drift()` at all. That is how
W1C-02 survived: `compare()` tested whole-tuple membership, so an object present on both
sides whose attribute changed was reported as one row MISSING and one row UNEXPECTED --
asserting the same policy was both absent from the database and foreign to the repository.
A reader following the script's own remediation advice would have written a migration to
CREATE a policy that already existed.

Two further defects shared that cause. A multiplicity difference, and a reordering, both
produced a HARD FAILURE that named nothing at all: `expected == actual` is order- and
count-sensitive so the fast path was skipped, then `r not in actual` membership is order-
and count-INsensitive so both lists came back empty. The run exited 1 having printed
`columns: 0 missing, 0 unexpected`.

WHAT IS AND IS NOT COVERED HERE
-------------------------------
These are the 14 cases approved in `staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md`
§5, which records decisions D1 (ambiguous keys are hard failures with distinct labels) and
D2 (reordered identical rows are unchanged).

Every case is PURE DATA. `classify_dimension()` takes two lists and returns a dataclass;
`compare()` takes two dicts and returns two lists of strings. Neither performs I/O, so
there is no gate, no skip and no xfail in this file -- a skip here would be
indistinguishable from a pass, and the classification is the whole point.

WHAT THESE TESTS DO NOT ESTABLISH
---------------------------------
Nothing about PostgreSQL. The declared key widths rest on documented catalogue properties
and are UNVERIFIED against any live catalogue for all 16 dimensions. These tests assert
that the classifier behaves correctly INCLUDING when a declared key turns out not to be
unique (case 7) -- they do not assert that any key is unique.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.verify_provisioning import (  # noqa: E402
    COMPARISON_KEY,
    SNAPSHOT_QUERIES,
    WHOLE_ROW,
    classify_dimension,
)
from scripts.verify_schema_drift import (  # noqa: E402
    ADVISORY_DIMENSIONS,
    ROW_CAP,
    compare,
    render,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures as plain constants. Real dimension names, because `classify_dimension`
#  looks the key width up in the production declaration.
# ─────────────────────────────────────────────────────────────────────────────

#: `policies` is keyed (tablename, policyname) with five attribute columns. This is the
#: dimension whose misreport W1C-02 observed.
POLICY = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "")
POLICY_CHANGED = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "(a = b)")
#: A second policy sharing the SAME key as POLICY but differing in attributes. Used to
#: force the ambiguous-key path; it is not a configuration PostgreSQL would produce if the
#: declared key is genuinely unique, which is exactly the assumption under test.
POLICY_COLLIDES = ("fee_records", "fee_select", "RESTRICTIVE", "{anon}", "ALL", "(other)", "")

#: `columns` is keyed (table_name, column_name) with seven attribute columns, four of them
#: not `coalesce(...)`-guarded -- so a NULL is reachable.
COL_A = ("t1", "c1", "integer", "", "NO", "", "NO", "", "NEVER")
COL_B = ("t2", "c2", "text", "", "YES", "", "NO", "", "NEVER")
COL_A_NULL = ("t1", "c1", None, "", "NO", "", "NO", "", "NEVER")
COL_A_EMPTY = ("t1", "c1", "", "", "NO", "", "NO", "", "NEVER")

#: `privileges` is WHOLE_ROW -- the row IS the key, so `modified` is meaningless there.
#: It is also the one dimension where identical duplicate rows are reachable by
#: construction: `information_schema.role_table_grants` is one row per (grantor, grantee,
#: table, privilege, is_grantable) and the query projects only three of those with neither
#: DISTINCT nor GROUP BY, so two grantors of one privilege yield two identical rows.
GRANT = ("fee_records", "authenticated", "SELECT")
GRANT_OTHER = ("fee_records", "anon", "SELECT")

#: `recorded_migrations` is advisory and keyed on (filename).
MIGRATION = ("001_init.sql", "abc123", "step1")
MIGRATION_CHANGED = ("001_init.sql", "def456", "step1")


def snapshot(**dimensions) -> dict:
    """A full 16-dimension snapshot, empty except where named."""
    out = {name: [] for name in SNAPSHOT_QUERIES}
    out.update(dimensions)
    return out


def failures_for(dimension, expected, actual) -> list[str]:
    f, a = compare(snapshot(**{dimension: expected}), snapshot(**{dimension: actual}))
    return a if dimension in ADVISORY_DIMENSIONS else f


# ─────────────────────────────────────────────────────────────────────────────
#  Case 1 -- an undeclared dimension is inconclusive, not silently compared
# ─────────────────────────────────────────────────────────────────────────────

def test_an_undeclared_dimension_is_inconclusive():
    """A dimension nobody has declared the identity of must not be compared anyway.

    Falling back to a whole-row comparison would produce a plausible-looking result and
    mask the omission the static guard exists to catch, so this refuses instead.
    """
    result = classify_dimension("a_dimension_nobody_declared", [COL_A], [COL_B])

    assert result.verdict == "inconclusive"
    assert result.notes, "an inconclusive result must say why"
    assert any("UNDECLARED" in n for n in result.notes)
    assert result.modified == [] and result.missing == [] and result.added == [], (
        "an undeclared dimension must not be classified into buckets"
    )


def test_an_undeclared_dimension_is_named_in_the_output():
    lines = render(classify_dimension("a_dimension_nobody_declared", [COL_A], []))
    assert lines, "an inconclusive dimension must produce output"
    assert "a_dimension_nobody_declared" in lines[0]
    assert any("UNDECLARED" in l for l in lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Case 2 -- an invalid key declaration is inconclusive
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_width", [0, -1, 99, None, True, "2"])
def test_an_invalid_key_declaration_is_inconclusive(bad_width, monkeypatch):
    """A width that is not `WHOLE_ROW` and not a usable column count is refused.

    `None` is included deliberately: `dict.get()` returns `None` for an ABSENT key, so
    `None` must not be treated as a valid declaration meaning anything. `True` is included
    because `isinstance(True, int)` is True in Python and a bool is not a column count.
    """
    monkeypatch.setitem(COMPARISON_KEY, "policies", bad_width)
    result = classify_dimension("policies", [POLICY], [POLICY_CHANGED])

    assert result.verdict == "inconclusive", f"width {bad_width!r} should be refused"
    assert any("INVALID KEY DECLARATION" in n for n in result.notes)
    assert result.modified == []


def test_a_valid_width_is_still_accepted_after_the_invalid_ones(monkeypatch):
    """Control for the parametrised test above: the real declaration still classifies."""
    assert COMPARISON_KEY["policies"] == 2, "the real declaration was not restored"
    assert classify_dimension("policies", [POLICY], [POLICY]).verdict == "same"


# ─────────────────────────────────────────────────────────────────────────────
#  Case 3 -- two distinct objects sharing a key are NOT one modification  (D1)
# ─────────────────────────────────────────────────────────────────────────────

def test_two_distinct_objects_sharing_a_key_are_not_one_modification():
    """The central safety property. THIS is the defect a naive keyed fix introduces.

    Grouping by key and differencing the attribute multisets would report
    `MODIFIED ('fee_records','fee_select')` for what is actually two distinct policies --
    W1C-02's error from the opposite direction. Under D1 the group is flagged AMBIGUOUS
    and falls back to a whole-row comparison, which can never pair two non-identical rows.
    """
    result = classify_dimension("policies", [POLICY, POLICY_COLLIDES], [POLICY])

    assert result.verdict == "drift"
    assert result.ambiguous, "a shared key must be reported as ambiguous"
    assert result.ambiguous[0]["key"] == ("fee_records", "fee_select")
    assert result.ambiguous[0]["baseline"] == 2
    assert result.ambiguous[0]["live"] == 1
    assert result.modified == [], (
        "two distinct objects must NOT be collapsed into one modification"
    )
    # The row that genuinely is not on the live side is still named.
    assert POLICY_COLLIDES in result.missing


def test_an_ambiguous_key_is_a_hard_failure_with_a_distinct_label():
    """D1: hard failure, and labelled so a reader cannot mistake it for a schema change."""
    lines = failures_for("policies", [POLICY, POLICY_COLLIDES], [POLICY])

    assert lines, "an ambiguous key must be a hard failure, not silence"
    assert any("AMBIGUOUS KEY" in l for l in lines), "the label must be distinct"
    assert any("not classified" in l for l in lines)
    assert not any("MODIFIED" in l for l in lines)


def test_an_ambiguous_key_does_not_hide_a_real_change_elsewhere():
    """One ambiguous key must not suppress classification of the dimension's other keys."""
    other = ("other_table", "p2", "PERMISSIVE", "{authenticated}", "SELECT", "(q)", "")
    other_changed = ("other_table", "p2", "PERMISSIVE", "{authenticated}", "SELECT", "(q)", "(r)")
    result = classify_dimension(
        "policies", [POLICY, POLICY_COLLIDES, other], [POLICY, other_changed]
    )

    assert result.ambiguous, "the colliding key is still reported"
    assert [m["key"] for m in result.modified] == [("other_table", "p2")], (
        "the non-colliding key must still be classified as modified"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Case 4 -- duplicate rows with EQUAL multiplicity are unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_equal_duplicate_multiplicity_is_unchanged():
    result = classify_dimension("privileges", [GRANT, GRANT], [GRANT, GRANT])

    assert result.verdict == "same"
    assert result.unchanged == 2, "both copies must be counted"
    assert result.duplicated == []
    assert render(result) == []


def test_equal_duplicate_multiplicity_reports_no_drift_through_compare():
    assert failures_for("privileges", [GRANT, GRANT], [GRANT, GRANT]) == []


# ─────────────────────────────────────────────────────────────────────────────
#  Case 5 -- duplicate rows with DIFFERING multiplicity report explicit counts
# ─────────────────────────────────────────────────────────────────────────────

def test_differing_duplicate_multiplicity_reports_counts():
    """Defect D3. Previously this printed `0 missing, 0 unexpected` and named nothing."""
    result = classify_dimension("privileges", [GRANT, GRANT], [GRANT])

    assert result.verdict == "drift"
    assert len(result.duplicated) == 1
    assert result.duplicated[0] == {"subject": GRANT, "baseline": 2, "live": 1}
    assert result.unchanged == 1
    assert result.missing == [] and result.added == [], (
        "neither side lost the OBJECT -- only its multiplicity changed"
    )


def test_differing_multiplicity_in_the_other_direction():
    result = classify_dimension("privileges", [GRANT], [GRANT, GRANT])

    assert result.verdict == "drift"
    assert result.duplicated[0] == {"subject": GRANT, "baseline": 1, "live": 2}


def test_differing_multiplicity_names_its_subject():
    """The explicit fix for the original symptom: a drift that named nothing."""
    lines = failures_for("privileges", [GRANT, GRANT], [GRANT])

    assert lines, "a multiplicity difference must be a hard failure"
    assert any("DUPLICATED" in l for l in lines)
    assert any("baseline=2" in l and "live=1" in l for l in lines)
    assert not any("0 missing, 0 unexpected" in l for l in lines), (
        "the subject-less output must be gone"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Case 6 -- a non-key attribute change is one modification, attribute identified
# ─────────────────────────────────────────────────────────────────────────────

def test_a_modified_attribute_is_one_modification_not_a_removal_and_addition():
    """The headline W1C-02 case."""
    result = classify_dimension("policies", [POLICY], [POLICY_CHANGED])

    assert result.verdict == "drift"
    assert len(result.modified) == 1
    assert result.modified[0]["key"] == ("fee_records", "fee_select")
    assert result.missing == [], "the object is present on both sides"
    assert result.added == [], "the object is not foreign to the repository"


def test_the_changed_attribute_is_identified():
    result = classify_dimension("policies", [POLICY], [POLICY_CHANGED])
    changes = result.modified[0]["changes"]

    assert len(changes) == 1, "exactly one attribute changed"
    assert changes[0]["was"] == ""
    assert changes[0]["now"] == "(a = b)"
    assert changes[0]["column_index"] == 6, "with_check is column 6 of the SELECT list"
    assert changes[0]["index"] == 4, "and attribute 4, since the key is 2 columns wide"


def test_a_modified_object_is_named_exactly_once():
    """Previously it appeared twice, in two unlinked lines."""
    lines = failures_for("policies", [POLICY], [POLICY_CHANGED])

    assert sum(1 for l in lines if "MODIFIED" in l) == 1
    assert not any("MISSING from the database" in l for l in lines)
    assert not any("NOT IN this repository" in l for l in lines)
    assert any("-> '(a = b)'" in l for l in lines), "old -> new must be shown"


def test_multiple_changed_attributes_are_all_listed():
    two_changed = ("fee_records", "fee_select", "RESTRICTIVE", "{authenticated}",
                   "SELECT", "(uid = x)", "(a = b)")
    result = classify_dimension("policies", [POLICY], [two_changed])

    assert [c["column_index"] for c in result.modified[0]["changes"]] == [2, 6]


# ─────────────────────────────────────────────────────────────────────────────
#  Case 7 -- genuine removal and addition still classify correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_a_genuine_removal_is_missing():
    result = classify_dimension("policies", [POLICY], [])

    assert result.verdict == "drift"
    assert result.missing == [POLICY]
    assert result.added == [] and result.modified == []


def test_a_genuine_addition_is_added():
    result = classify_dimension("policies", [], [POLICY])

    assert result.verdict == "drift"
    assert result.added == [POLICY]
    assert result.missing == [] and result.modified == []


def test_removal_and_addition_keep_their_original_labels():
    """Backwards compatibility of the two row labels."""
    assert any("MISSING from the database" in l
               for l in failures_for("policies", [POLICY], []))
    assert any("NOT IN this repository" in l
               for l in failures_for("policies", [], [POLICY]))


# ─────────────────────────────────────────────────────────────────────────────
#  Case 8 -- whole-row dimensions never produce `modified`
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "dimension", [d for d, k in COMPARISON_KEY.items() if k == WHOLE_ROW]
)
def test_a_whole_row_dimension_never_reports_modified(dimension):
    """There is no key separate from the row, so a change can only be a loss or a gain."""
    result = classify_dimension(dimension, [GRANT], [GRANT_OTHER])

    assert result.key_spec == WHOLE_ROW
    assert result.modified == [], (
        f"{dimension} has no attribute columns; `modified` would be invented"
    )
    assert result.verdict == "drift"
    assert GRANT in result.missing and GRANT_OTHER in result.added


def test_the_whole_row_set_matches_the_declaration():
    """Guards the parametrisation above against silently covering nothing."""
    whole = [d for d, k in COMPARISON_KEY.items() if k == WHOLE_ROW]
    assert sorted(whole) == [
        "alembic_version", "extensions", "privileges", "sequences",
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  Case 9 -- identical snapshots and empty dimensions report nothing
# ─────────────────────────────────────────────────────────────────────────────

def test_identical_snapshots_report_nothing():
    """Two rows with DISTINCT keys, identical on both sides.

    Note the fixture choice: `POLICY` and `POLICY_CHANGED` deliberately share a key, so
    using both here would exercise the ambiguous-key path instead of this one. A second
    policy with its own key is required.
    """
    other = ("fee_records", "fee_insert", "PERMISSIVE", "{authenticated}", "INSERT", "(q)", "")
    result = classify_dimension("policies", [POLICY, other], [POLICY, other])

    assert result.verdict == "same"
    assert result.unchanged == 2
    assert render(result) == []


def test_an_ambiguous_key_is_reported_even_when_both_sides_are_identical():
    """A wrong key width must surface on its own, not only when something else changes.

    `POLICY` and `POLICY_COLLIDES` share a key, so this input proves the declared width
    does not identify an object -- a finding regardless of the two sides agreeing. If this
    were reported as `same`, a bad declaration would stay invisible until an unrelated
    change happened to expose it.
    """
    both = [POLICY, POLICY_COLLIDES]
    result = classify_dimension("policies", both, list(both))

    assert result.verdict == "drift"
    assert result.ambiguous == [
        {"key": ("fee_records", "fee_select"), "baseline": 2, "live": 2}
    ]
    assert result.modified == [], "no modification may be invented from a collision"
    assert result.missing == [] and result.added == [], (
        "the rows themselves are present on both sides"
    )
    assert result.unchanged == 2


def test_empty_dimensions_report_nothing():
    result = classify_dimension("policies", [], [])

    assert result.verdict == "same"
    assert result.unchanged == 0
    assert render(result) == []


def test_a_whole_identical_snapshot_produces_no_drift():
    snap = snapshot(policies=[POLICY], privileges=[GRANT, GRANT], columns=[COL_A, COL_B])
    failures, advisories = compare(snap, snap)

    assert failures == [] and advisories == []


# ─────────────────────────────────────────────────────────────────────────────
#  Case 10 -- reordered identical rows are unchanged  (D2)
# ─────────────────────────────────────────────────────────────────────────────

def test_reordered_identical_rows_are_not_drift():
    """D2, the one approved observable behaviour change.

    Previously `expected == actual` was list equality, so a reordering skipped the fast
    path; then membership found every row on the other side, so both lists came back
    empty and the run failed with `0 missing, 0 unexpected`.
    """
    result = classify_dimension("columns", [COL_A, COL_B], [COL_B, COL_A])

    assert result.verdict == "same"
    assert result.unchanged == 2
    assert render(result) == []


def test_reordering_does_not_hide_a_real_change():
    """Control: reorder-insensitivity must not also swallow a genuine modification."""
    changed = ("t2", "c2", "uuid", "", "YES", "", "NO", "", "NEVER")
    result = classify_dimension("columns", [COL_A, COL_B], [changed, COL_A])

    assert result.verdict == "drift"
    assert [m["key"] for m in result.modified] == [("t2", "c2")]


def test_reordered_whole_row_rows_are_not_drift():
    result = classify_dimension("privileges", [GRANT, GRANT_OTHER], [GRANT_OTHER, GRANT])

    assert result.verdict == "same"
    assert result.unchanged == 2


# ─────────────────────────────────────────────────────────────────────────────
#  Case 11 -- null-safe ordering and deterministic output
# ─────────────────────────────────────────────────────────────────────────────

def test_a_null_attribute_change_is_a_modification():
    result = classify_dimension("columns", [COL_A_NULL], [COL_A_EMPTY])

    assert result.verdict == "drift"
    assert len(result.modified) == 1
    changes = result.modified[0]["changes"]
    assert changes[0]["was"] is None
    assert changes[0]["now"] == ""


def test_null_and_empty_string_are_distinguished():
    """`_norm` preserves `None`, so NULL and '' are different values, not the same one."""
    assert classify_dimension("columns", [COL_A_NULL], [COL_A_NULL]).verdict == "same"
    assert classify_dimension("columns", [COL_A_EMPTY], [COL_A_EMPTY]).verdict == "same"
    assert classify_dimension("columns", [COL_A_NULL], [COL_A_EMPTY]).verdict == "drift"


def test_sorting_does_not_crash_on_mixed_null_and_text_keys():
    """`None < 'x'` raises TypeError in Python 3; a plain sorted() would crash here.

    Four of `columns`' seven attribute columns are not `coalesce(...)`-guarded, so a NULL
    is reachable on real data -- this is a crash, not a mis-ordering.
    """
    rows_with_null_key = [(None, "c1", "integer", "", "NO", "", "NO", "", "NEVER")]
    rows_with_text_key = [("t1", "c1", "integer", "", "NO", "", "NO", "", "NEVER")]

    result = classify_dimension("columns", rows_with_null_key, rows_with_text_key)

    assert result.verdict == "drift"
    assert len(result.missing) == 1 and len(result.added) == 1


def test_output_is_deterministic_across_repeated_runs():
    """Same inputs, byte-identical output, including with NULLs in play.

    The fixture deliberately mixes three paths at once -- an ambiguous key (`COL_A_NULL`
    and `COL_A` share `('t1','c1')`), a `None` attribute, and an added row -- because
    determinism is only interesting where sorting actually has work to do.
    """
    expected = [COL_B, COL_A_NULL, COL_A]
    actual = [COL_A_EMPTY, COL_B, ("t3", "c3", "text", "", "NO", "", "NO", "", "NEVER")]

    runs = [render(classify_dimension("columns", expected, actual)) for _ in range(5)]

    assert all(r == runs[0] for r in runs), "classification must be deterministic"
    assert runs[0], "the fixture must actually produce output"
    assert any("AMBIGUOUS KEY" in l for l in runs[0]), (
        "the fixture should be exercising the ambiguous path, where sorting matters most"
    )


def test_whole_snapshot_output_is_deterministic():
    base = snapshot(policies=[POLICY], privileges=[GRANT, GRANT], columns=[COL_A_NULL])
    live = snapshot(policies=[POLICY_CHANGED], privileges=[GRANT], columns=[COL_A_EMPTY])

    runs = [compare(base, live) for _ in range(5)]

    assert all(r == runs[0] for r in runs)


# ─────────────────────────────────────────────────────────────────────────────
#  Case 12 -- no nonzero drift may be reported without naming a subject
# ─────────────────────────────────────────────────────────────────────────────

SUBJECT_MARKERS = (
    "MODIFIED", "MISSING from the database", "NOT IN this repository",
    "DUPLICATED", "AMBIGUOUS KEY", "UNDECLARED", "INVALID KEY DECLARATION",
)

NONZERO_CASES = [
    ("modified attribute", "policies", [POLICY], [POLICY_CHANGED]),
    ("removal", "policies", [POLICY], []),
    ("addition", "policies", [], [POLICY]),
    ("multiplicity baseline", "privileges", [GRANT, GRANT], [GRANT]),
    ("multiplicity live", "privileges", [GRANT], [GRANT, GRANT]),
    ("whole-row change", "privileges", [GRANT], [GRANT_OTHER]),
    ("ambiguous key", "policies", [POLICY, POLICY_COLLIDES], [POLICY]),
    ("advisory change", "recorded_migrations", [MIGRATION], [MIGRATION_CHANGED]),
]


@pytest.mark.parametrize("label,dimension,expected,actual", NONZERO_CASES)
def test_drift_is_never_reported_without_naming_a_subject(label, dimension, expected, actual):
    """The explicit fix for `columns: 0 missing, 0 unexpected` as a hard failure."""
    result = classify_dimension(dimension, expected, actual)
    lines = render(result)

    assert result.verdict != "same", f"{label} should be drift"
    assert result.has_findings(), f"{label}: a nonzero verdict with no findings"
    assert len(lines) > 1, f"{label}: a header with no detail names nothing"
    assert any(any(m in l for m in SUBJECT_MARKERS) for l in lines), (
        f"{label}: no line names a subject"
    )


@pytest.mark.parametrize("label,dimension,expected,actual", NONZERO_CASES)
def test_the_header_counts_match_the_detail(label, dimension, expected, actual):
    result = classify_dimension(dimension, expected, actual)
    header = render(result)[0]

    if result.verdict == "inconclusive":
        pytest.skip("inconclusive results have no count header")
    assert f"{len(result.modified)} modified" in header
    assert f"{len(result.missing)} missing" in header
    assert f"{len(result.added)} added" in header


# ─────────────────────────────────────────────────────────────────────────────
#  Case 13 -- verdict and detail agree; advisory routing preserved
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,dimension,expected,actual", NONZERO_CASES + [
    ("unchanged", "policies", [POLICY], [POLICY]),
    ("both empty", "policies", [], []),
    ("reordered", "columns", [COL_A, COL_B], [COL_B, COL_A]),
])
def test_the_verdict_agrees_with_whether_detail_was_emitted(label, dimension, expected, actual):
    """`same` must print nothing; anything else must print something.

    This is the property the previous implementation violated in the summary table: for
    reordered rows it printed `DRIFT` in the summary and nothing in the detail.
    """
    result = classify_dimension(dimension, expected, actual)
    lines = render(result)

    if result.verdict == "same":
        assert lines == [], f"{label}: `same` must emit no detail"
    else:
        assert lines, f"{label}: `{result.verdict}` must emit detail"


def test_advisory_dimensions_route_to_advisories_not_failures():
    failures, advisories = compare(
        snapshot(recorded_migrations=[MIGRATION]),
        snapshot(recorded_migrations=[MIGRATION_CHANGED]),
    )

    assert failures == [], "an advisory-only difference must not be a hard failure"
    assert advisories, "the difference must still be reported"
    assert any("MODIFIED" in l for l in advisories)


def test_non_advisory_dimensions_route_to_failures():
    failures, advisories = compare(
        snapshot(policies=[POLICY]), snapshot(policies=[POLICY_CHANGED])
    )

    assert failures and advisories == []


def test_the_advisory_set_is_what_the_drift_checker_declares():
    assert sorted(ADVISORY_DIMENSIONS) == ["alembic_version", "recorded_migrations"]


# ─────────────────────────────────────────────────────────────────────────────
#  Case 14 -- backwards compatibility
# ─────────────────────────────────────────────────────────────────────────────

def test_compare_still_returns_two_lists_of_strings():
    """The caller contract `main()` depends on is unchanged."""
    failures, advisories = compare(
        snapshot(policies=[POLICY]), snapshot(policies=[POLICY_CHANGED])
    )

    assert isinstance(failures, list) and isinstance(advisories, list)
    assert all(isinstance(l, str) for l in failures + advisories)


def test_the_row_cap_is_still_twelve():
    assert ROW_CAP == 12


def test_more_rows_than_the_cap_are_truncated_with_a_count():
    many = [
        ("fee_records", f"p{i}", "PERMISSIVE", "{authenticated}", "SELECT", "(q)", "")
        for i in range(ROW_CAP + 5)
    ]
    lines = failures_for("policies", many, [])

    shown = [l for l in lines if "MISSING from the database" in l]
    assert len(shown) == ROW_CAP
    assert any(f"... and {5} more missing" in l for l in lines)


def test_every_dimension_in_the_snapshot_has_a_declared_key():
    """The production declaration must cover the production query set exactly.

    This is the same property `tests/test_snapshot_dimension_invariants.py` asserts; it is
    repeated here because `classify_dimension` returns `inconclusive` for anything it does
    not find, and a silent gap would turn every comparison of that dimension into an
    unexplained refusal.
    """
    assert set(COMPARISON_KEY) == set(SNAPSHOT_QUERIES)


def test_classification_touches_neither_input():
    """Pure function: the caller's lists must not be mutated."""
    expected = [POLICY, POLICY_CHANGED]
    actual = [POLICY]
    before_e = list(expected)
    before_a = list(actual)

    classify_dimension("policies", expected, actual)

    assert expected == before_e and actual == before_a
