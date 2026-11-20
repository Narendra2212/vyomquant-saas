"""Tests for the decision logic in ``scripts/post_migration_validation.py``.

DB-03: section 3 of that script ("VALIDATING RLS STATUS") used to walk the same fixed
``required_tables`` list section 1 checks for existence, so a customer table a later
migration grants ``anon``/``authenticated`` a privilege on -- Supabase's default
privilege shape does this automatically for most new tables -- was invisible to it
unless someone also remembered to add its name to that list. ``evaluate_privilege_
hardening`` is the pure decision half of the replacement: given ``(tablename,
rowsecurity)`` rows for every table the catalog itself says grants one of those two
roles something, decide pass/fail. No table name is special-cased here, which is the
point -- the test for "a table outside required_tables is still caught" belongs to the
real-Postgres half of this fix (test_db03_privilege_hardening_detects_untracked_tables_postgres.py),
since it needs the catalog join to prove a table was found by privilege rather than by
name.

Loaded by path rather than imported as a package, because ``scripts/`` is not a package
(same convention as tests/test_verify_rls_script.py).
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "post_migration_validation.py"


def _load():
    """The script as a module. Must not require a live database to import."""
    spec = importlib.util.spec_from_file_location("post_migration_validation_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_privileged_table_without_rls_fails():
    mod = _load()
    assert mod.evaluate_privilege_hardening([("widgets", False)]) is False


def test_a_privileged_table_with_rls_enabled_passes():
    mod = _load()
    assert mod.evaluate_privilege_hardening([("widgets", True)]) is True


def test_an_empty_result_passes_vacuously_nothing_is_privileged_to_fail_on():
    mod = _load()
    assert mod.evaluate_privilege_hardening([]) is True


def test_one_bad_table_among_many_good_ones_still_fails():
    mod = _load()
    rows = [("profiles", True), ("strategies", True), ("new_customer_table", False), ("orders", True)]
    assert mod.evaluate_privilege_hardening(rows) is False


def test_the_failure_message_names_every_unprotected_table(capsys):
    mod = _load()
    mod.evaluate_privilege_hardening([("profiles", True), ("leaky_table", False), ("also_leaky", False)])
    out = capsys.readouterr().out
    assert "leaky_table" in out
    assert "also_leaky" in out
    assert "FAILED" in out


# ---------------------------------------------------------------------------
# DB-04: partition_privileged_tables, the allowlist filter this check needed before it
# could be wired into CI without failing on plans/plan_features/plan_limits/
# plan_prices/plan_migration_mapping -- public pricing-catalog data with no customer
# identity, which 999_privilege_hardening.sql's own narrower backstop already
# correctly leaves unflagged.
# ---------------------------------------------------------------------------

def test_an_allowlisted_table_is_set_aside_not_checked():
    mod = _load()
    rows = [("plans", False), ("profiles", True)]
    checked, allowlisted = mod.partition_privileged_tables(rows)
    assert checked == [("profiles", True)]
    assert allowlisted == [("plans", False)]


def test_an_allowlisted_table_without_rls_does_not_fail_the_check():
    """The actual bug this fix closes: before this, running the script at all against
    a real, fully-provisioned database failed on exactly this table set."""
    mod = _load()
    rows = [(name, False) for name in sorted(mod.PUBLIC_CATALOG_TABLES)]
    checked, allowlisted = mod.partition_privileged_tables(rows)
    assert checked == []
    assert mod.evaluate_privilege_hardening(checked) is True


def test_a_non_allowlisted_table_without_rls_still_fails_even_alongside_allowlisted_ones():
    """The allowlist narrows what is checked; it must not widen what passes. A real
    customer table slipping in beside the catalog tables must still fail."""
    mod = _load()
    rows = [("plans", False), ("new_customer_table", False)]
    checked, allowlisted = mod.partition_privileged_tables(rows)
    assert mod.evaluate_privilege_hardening(checked) is False


def test_the_allowlist_is_exactly_the_five_known_public_catalog_tables():
    """Pins the set itself, so a silent addition (or removal) is a visible diff, not a
    behaviour change nobody reviewed -- adding a table here is a product decision per
    this module's own comment, not something that should drift unnoticed."""
    mod = _load()
    assert mod.PUBLIC_CATALOG_TABLES == frozenset({
        "plans", "plan_features", "plan_limits", "plan_prices", "plan_migration_mapping",
    })
