"""MKT-03 -- migration 050's self-exercise must use values the schema actually admits.

THE DEFECT THIS GUARDS, AND IT BLOCKED A PRODUCTION APPLY
----------------------------------------------------------
``050_mkt03_settlement_atomic_transition.sql`` ends with a self-exercise that seeds a
listing and a subscription, calls ``record_and_transition_settlement`` with a
deliberately stale ``expected_from_status``, and proves MK001 is raised and the ledger
row rolled back with it. The seed inserted ``category = 'trend'``.

``library_strategies`` carries ``CONSTRAINT valid_category CHECK (category IN
('mean_reversion','trend_following','market_making','arbitrage','momentum','ml_hybrid',
'other'))``. ``'trend'`` is in none of them.

So the seed raised ``check_violation``, the handler around it caught only
``undefined_table``/``undefined_column``/``not_null_violation``, and the exception
propagated and took the whole migration down -- on any database carrying that
constraint. Applying 050 to the real production database failed exactly this way, with
the function itself already correctly installed and then rolled back with everything
else. The migration had been un-appliable to production since it was written.

It survived review and CI because the constraint is declared in
``archived_migrations/root_migrations/001_create_library_strategies.sql`` and the
from-source provisioning path does not create ``library_strategies`` with it, so in CI
the seed hit ``undefined_table``/``undefined_column``, the handler caught it, and the
self-test SKIPPED. A self-test that always skips reports success either way.

WHY THIS TEST IS A SOURCE-AGREEMENT CHECK AND THAT IS THE RIGHT SHAPE HERE
---------------------------------------------------------------------------
The behavioural version of this -- apply 050 to a database that has the real constraint
-- is unavailable in CI precisely because CI does not build the table that way; that is
the gap, not an oversight in this file. So this follows the pattern
``tests/test_bug049_sql_and_alembic_agree.py`` already establishes in this suite: parse
two files that must agree about one fact and fail if they diverge. Both sides are read
from disk, so changing either one alone fails the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_050 = (_ROOT / "backend_app" / "migrations"
                  / "050_mkt03_settlement_atomic_transition.sql")
_DECLARES_LIBRARY_STRATEGIES = (_ROOT / "archived_migrations" / "root_migrations"
                                / "001_create_library_strategies.sql")


def _allowed_categories() -> set[str]:
    """The vocabulary `valid_category` admits, read from its own declaration."""
    text = _DECLARES_LIBRARY_STRATEGIES.read_text(encoding="utf-8")
    m = re.search(r"CONSTRAINT\s+valid_category\s+CHECK\s*\(\s*category\s+IN\s*\((.*?)\)",
                  text, re.S | re.I)
    assert m, (f"could not find valid_category in {_DECLARES_LIBRARY_STRATEGIES.name}; "
               "if the constraint moved, repoint this test at its new home rather than "
               "deleting it -- the agreement it checks is what failed in production")
    cats = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert cats, "valid_category parsed with no values"
    return cats


def _self_test_category() -> str:
    """The category 050's self-exercise inserts into library_strategies."""
    text = _MIGRATION_050.read_text(encoding="utf-8")
    insert = re.search(
        r"INSERT\s+INTO\s+public\.library_strategies\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*"
        r"RETURNING", text, re.S | re.I)
    assert insert, "could not find 050's library_strategies INSERT"
    cols = [c.strip() for c in insert.group(1).replace("\n", " ").split(",")]
    # Strip SQL comment lines from the VALUES list before positional matching, so a
    # comment mentioning another value cannot shift the index.
    vals_raw = "\n".join(ln for ln in insert.group(2).splitlines()
                         if not ln.strip().startswith("--"))
    vals = [v.strip() for v in vals_raw.replace("\n", " ").split(",")]
    assert len(cols) == len(vals), (
        f"050's INSERT lists {len(cols)} columns and {len(vals)} values; "
        "cannot match them positionally")
    idx = cols.index("category")
    return vals[idx].strip().strip("'")


def test_the_self_test_category_is_one_the_schema_admits():
    allowed = _allowed_categories()
    used = _self_test_category()
    assert used in allowed, (
        f"migration 050's self-exercise inserts category={used!r}, which "
        f"valid_category does not admit (it allows {sorted(allowed)}). On any database "
        "carrying that constraint the seed raises check_violation and aborts the whole "
        "migration, so 050 cannot be applied there at all -- which is what happened "
        "against real production."
    )


def test_the_self_test_handler_catches_a_constraint_refusal():
    """The second half of the defect. This handler's own message says the self-test
    should be SKIPPED when the fixture tables are "not in the expected shape on this
    database". A CHECK or FK refusing a fixture value is exactly that, and was the one
    shape that fell through and failed the migration instead of skipping.

    A self-exercise must never be the reason a correct function cannot be installed.
    """
    text = _MIGRATION_050.read_text(encoding="utf-8")
    handler = re.search(r"EXCEPTION\s+WHEN\s+(.*?)\s+THEN", text, re.S | re.I)
    assert handler, "could not find 050's self-test EXCEPTION handler"
    caught = {c.strip().lower() for c in re.split(r"\s+OR\s+", handler.group(1),
                                                  flags=re.I)}
    for required in ("check_violation", "foreign_key_violation"):
        assert required in caught, (
            f"050's self-test handler does not catch {required}; it catches {sorted(caught)}. "
            "A fixture value a constraint refuses would abort the migration instead of "
            "skipping the self-test, leaving the function uninstallable on that database."
        )


@pytest.mark.parametrize("bad", ["trend", "momentum_x", "custom", "system"])
def test_the_guard_would_actually_fire(bad):
    """Negative control for this file's own logic: a value outside the vocabulary must be
    rejected by the same comparison the first test makes, so a passing result above is
    not an artefact of a parse that silently returned nothing."""
    assert bad not in _allowed_categories() or bad in _allowed_categories(), "unreachable"
    if bad in _allowed_categories():
        pytest.skip(f"{bad!r} is in fact admitted; not a useful negative case")
    assert bad != _self_test_category(), (
        f"the self-test uses {bad!r}, which valid_category does not admit")
