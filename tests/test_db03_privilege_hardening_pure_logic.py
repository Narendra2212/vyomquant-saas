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
