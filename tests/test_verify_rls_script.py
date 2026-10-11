"""Tests for the decision logic in ``scripts/verify_rls.py``.

That script reported green over three real gaps, and the reason none was caught is
structural: it built its Supabase client at module scope and called ``sys.exit(1)`` when
the credentials were absent. The module could not be imported without production
secrets, so nothing could ever check whether its checks were capable of failing. One of
them was not - ``check_rls_policies`` initialised ``all_pass = True`` and never
reassigned it.

The client construction now lives in ``get_admin()`` and each check is split into a
``fetch_*`` half that talks to the catalog and an ``evaluate_*`` half that decides. The
evaluate halves are pure, so the tests below need no database, and the specific defects
that shipped are now pinned:

  * a policy check that could not fail is asserted to fail,
  * ``exchange_credentials`` - a table no migration creates - is asserted absent from
    the critical list, with ``exchange_keys`` named instead,
  * an unreadable catalog is asserted to be a failure rather than a silent pass.

Loaded by path rather than imported as a package, because ``scripts/`` is not a package
and the file name would otherwise shadow nothing importable.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_rls.py"


def _load():
    """The script as a module. Must not require credentials - that is the point."""
    spec = importlib.util.spec_from_file_location("verify_rls_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vrls():
    """The script module, loaded once. Credentials are never needed."""
    return _load()


def _policy(table, cmd, name=None):
    return {"tablename": table, "cmd": cmd, "policyname": name or f"{table}_{cmd}_pol"}


# ── The import-time barrier that hid everything ───────────────────────────────


def test_module_imports_without_credentials(monkeypatch):
    """The regression that matters most: no secrets needed to load this.

    While this was false, none of the tests below could exist.
    """
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    module = _load()

    assert callable(module.get_admin)
    assert callable(module.evaluate_rls_enabled)
    assert callable(module.evaluate_rls_policies)


# ── Defect 1: the policy check could not fail ─────────────────────────────────


def test_policies_no_policies_is_a_failure(vrls):
    """Was SKIP-and-pass. An RLS-enabled table with no policy denies everything to
    non-exempt roles; an RLS-disabled one is wide open. Neither is a pass."""
    rows = [_policy(t, "ALL") for t in vrls.CRITICAL_TABLES[1:]]

    assert vrls.evaluate_rls_policies(rows) is False


def test_policies_all_covered_passes(vrls):
    """The check must still be able to succeed, or it is just a tripwire."""
    rows = [_policy(t, "ALL") for t in vrls.CRITICAL_TABLES]

    assert vrls.evaluate_rls_policies(rows) is True


def test_policies_select_only_is_a_failure(vrls):
    """The `referrals` shape: a SELECT-only policy reads as a working table right up
    until something attempts a write, which is then denied to non-exempt roles."""
    rows = [_policy(t, "ALL") for t in vrls.CRITICAL_TABLES[1:]]
    rows.append(_policy(vrls.CRITICAL_TABLES[0], "SELECT"))

    assert vrls.evaluate_rls_policies(rows) is False


def test_policies_explicit_per_command_set_passes(vrls):
    """Four separate policies are as good as one FOR ALL."""
    rows = []
    for table in vrls.CRITICAL_TABLES:
        for cmd in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            rows.append(_policy(table, cmd))

    assert vrls.evaluate_rls_policies(rows) is True


# ── Defect 2: a critical table that does not exist ────────────────────────────


def test_exchange_credentials_is_not_claimed_to_exist(vrls):
    """No migration anywhere creates `exchange_credentials`.

    While it sat in CRITICAL_TABLES it hit the "not found" SKIP branch which, per
    defect 1, still passed - so the most sensitive table on the platform was reported
    green without being examined, and the table that actually holds credentials was
    never named.
    """
    assert "exchange_credentials" not in vrls.CRITICAL_TABLES
    assert "exchange_credentials" not in vrls.EXPECTED_WITHOUT_RLS


def test_exchange_keys_is_checked_for_existence_but_not_for_rls(vrls):
    """`exchange_keys` is real, and RLS is structurally incapable of protecting it.

    `backend/api_key_vault.py` reaches it with SUPABASE_SERVICE_ROLE_KEY, which carries
    BYPASSRLS. Asserting a policy on it would produce a finding nobody can action.
    """
    assert "exchange_keys" in vrls.EXPECTED_WITHOUT_RLS
    assert "exchange_keys" not in vrls.CRITICAL_TABLES

    reason = vrls.EXPECTED_WITHOUT_RLS["exchange_keys"]
    assert "BYPASSRLS" in reason, "the reason must say why RLS cannot apply"


def test_missing_expected_table_is_a_failure(vrls):
    """A table named as expected and not found is a defect either way - wrong name
    here, or a migration that did not run."""
    found = {t: True for t in vrls.CRITICAL_TABLES}
    found.update({t: True for t in vrls.EXPECTED_WITHOUT_RLS})
    del found[vrls.CRITICAL_TABLES[0]]

    assert vrls.evaluate_rls_enabled(found) is False


def test_missing_non_rls_table_is_also_a_failure(vrls):
    """Existence is still asserted for the tables RLS does not govern."""
    found = {t: True for t in vrls.CRITICAL_TABLES}

    assert vrls.evaluate_rls_enabled(found) is False


# ── Defect 3: an unreadable catalog passed ────────────────────────────────────


def test_unreadable_catalog_fails_rather_than_passing(vrls):
    """PostgREST does not expose pg_catalog by default, so this was likely the real
    behaviour in production: every table on SKIP, and a green summary over nothing."""
    assert vrls.evaluate_rls_enabled({}) is False


def test_policy_query_exception_fails(vrls):
    """`None` means the query raised; `[]` means it succeeded and found nothing. Both
    fail, but they are kept distinct so the output can say which happened."""
    assert vrls.evaluate_rls_policies(None) is False


def test_empty_policy_result_fails(vrls):
    assert vrls.evaluate_rls_policies([]) is False


# ── The happy path still passes ───────────────────────────────────────────────


def test_fully_configured_database_passes(vrls):
    """Guards against over-correction. A check that always fails is as useless as one
    that always passes - it just fails in the other direction."""
    found = {t: True for t in vrls.CRITICAL_TABLES}
    found.update({t: True for t in vrls.EXPECTED_WITHOUT_RLS})

    assert vrls.evaluate_rls_enabled(found) is True


def test_rls_disabled_on_a_critical_table_fails(vrls):
    found = {t: True for t in vrls.CRITICAL_TABLES}
    found.update({t: True for t in vrls.EXPECTED_WITHOUT_RLS})
    found[vrls.CRITICAL_TABLES[0]] = False

    assert vrls.evaluate_rls_enabled(found) is False


# ── The table list the SQL reads from ─────────────────────────────────────────


def test_sql_table_list_is_derived_not_hardcoded(vrls):
    """The inline SQL ARRAY still named `exchange_credentials` after the constant had
    been corrected. Deriving the list is what stops that drifting again."""
    assert set(vrls.ALL_EXPECTED_TABLES) == set(vrls.CRITICAL_TABLES) | set(
        vrls.EXPECTED_WITHOUT_RLS
    )

    source = SCRIPT.read_text(encoding="utf-8")
    assert "'strategies','orders','positions'" not in source, (
        "the hardcoded table list is back in the SQL"
    )
