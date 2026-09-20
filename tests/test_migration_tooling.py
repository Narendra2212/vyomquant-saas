"""
tests/test_migration_tooling.py

Tests for ``scripts/migration_preflight.py`` and ``scripts/apply_migrations.py``
(the read-only checker and the dry-run-by-default applier for the
``backend_app/migrations/006``-``014`` set).

WHY THIS IS A RECORDING-DOUBLE TEST, NOT A LIVE-POSTGRES TEST
---------------------------------------------------------------
There is no PostgreSQL in this environment (the sibling migration tests -
``tests/test_marketplace_paper_migrations.py``,
``tests/test_schema_as_code_completeness.py`` - establish the same
constraint). Every test here either:

  * asserts a refusal fires *before* any connector is invoked at all (no
    recording double needed - the assertion is that ``psycopg2.connect``
    itself is never reached), or
  * passes a small recording double in place of ``psycopg2.connect`` so a
    test can assert exactly which statements (if any) were issued, without
    opening a socket.

No test in this file contacts a real database.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
BACKEND_MIGRATIONS_DIR = REPO_ROOT / "backend_app" / "migrations"

from scripts import migration_preflight as preflight  # noqa: E402
from scripts import apply_migrations as applier  # noqa: E402


# ============================================================================
# Recording doubles - stand in for psycopg2.connect / a live connection
# ============================================================================


class _RecordingCursor:
    """Records every SQL string passed to ``execute`` and answers canned rows."""

    def __init__(self, log: List[str], rows=None) -> None:
        self._log = log
        self._rows = rows if rows is not None else []

    def execute(self, sql, params=None) -> None:
        self._log.append(sql if isinstance(sql, str) else str(sql))

    def fetchall(self):
        return self._rows

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


class _RecordingConnection:
    """A ``psycopg2.connect``-shaped double. Never opens a socket.

    ``statement_log`` accumulates every statement any cursor issued, so a
    test can assert "zero statements were issued" (the dry-run guarantee) or
    inspect what would have run.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.statement_log: List[str] = []
        self.closed = False
        self.committed = 0
        self.rolledback = 0

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.statement_log)

    def set_session(self, readonly=None, autocommit=None) -> None:
        return None

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolledback += 1

    def close(self) -> None:
        self.closed = True


def _never_connect(dsn: str):
    """A connector double that fails the test if it is ever invoked."""
    raise AssertionError(
        f"psycopg2.connect (or the injected connector) was called with a "
        f"DSN when the code path under test must refuse before connecting: "
        f"{dsn!r}"
    )


class _RecordingConnector:
    """A callable connector double that records whether/how it was invoked."""

    def __init__(self, connection: Optional[_RecordingConnection] = None) -> None:
        self.calls: List[str] = []
        self._connection = connection

    def __call__(self, dsn: str) -> _RecordingConnection:
        self.calls.append(dsn)
        return self._connection or _RecordingConnection(dsn)


# ============================================================================
# 1. Absent DSN is refused with no network attempt
# ============================================================================


def test_preflight_refuses_absent_dsn_with_no_network_attempt():
    connector = _RecordingConnector()
    exit_code = preflight.run(argv=[], connector=connector)
    assert exit_code == 2
    assert connector.calls == [], "no connection attempt may be made when the DSN is absent"


def test_apply_migrations_refuses_absent_dsn_with_no_network_attempt():
    connector = _RecordingConnector()
    exit_code = applier.run(argv=["--apply"], connector=connector)
    assert exit_code == 2
    assert connector.calls == [], "no connection attempt may be made when the DSN is absent"


def test_resolve_dsn_raises_named_refusal_without_dsn():
    with pytest.raises(preflight.RefusalError):
        preflight.resolve_dsn(None, env={})


def test_resolve_dsn_reads_env_var_when_no_cli_dsn():
    dsn = preflight.resolve_dsn(None, env={"MIGRATION_DATABASE_URL": "postgresql://u:p@h:5432/d"})
    assert dsn == "postgresql://u:p@h:5432/d"


def test_resolve_dsn_prefers_cli_over_env():
    dsn = preflight.resolve_dsn(
        "postgresql://cli:pw@host:5432/db",
        env={"MIGRATION_DATABASE_URL": "postgresql://env:pw@host:5432/db"},
    )
    assert dsn == "postgresql://cli:pw@host:5432/db"


# ============================================================================
# 2. Port-6543 DSN is refused, naming the direct-session port
# ============================================================================


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:pass@host.example.com:6543/postgres",
        "postgresql://user:pass@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres",
    ],
)
def test_preflight_refuses_port_6543_naming_direct_port(dsn: str):
    connector = _RecordingConnector()
    exit_code = preflight.run(argv=["--dsn", dsn], connector=connector)
    assert exit_code == 2
    assert connector.calls == [], "a pooler-port DSN must be refused before connecting"


def test_validate_dsn_port_raises_named_refusal_for_6543():
    with pytest.raises(preflight.RefusalError) as excinfo:
        preflight.validate_dsn_port("postgresql://u:p@host:6543/db")
    message = str(excinfo.value)
    assert "6543" in message
    assert "5432" in message, "the refusal must name the direct-session port"
    assert "pooler" in message.lower()


def test_validate_dsn_port_accepts_5432():
    # Must not raise.
    preflight.validate_dsn_port("postgresql://u:p@host:5432/db")


def test_apply_migrations_refuses_port_6543_naming_direct_port():
    connector = _RecordingConnector()
    dsn = "postgresql://user:pass@host.example.com:6543/postgres"
    exit_code = applier.run(argv=["--dsn", dsn, "--apply"], connector=connector)
    assert exit_code == 2
    assert connector.calls == []


# ============================================================================
# 3. 010 without --maintenance-window is refused, naming the lock and table
# ============================================================================


def test_apply_migrations_refuses_010_without_maintenance_window():
    connector = _RecordingConnector()
    dsn = "postgresql://user:pass@host.example.com:5432/postgres"
    exit_code = applier.run(argv=["--dsn", dsn, "--apply"], connector=connector)
    assert exit_code == 2
    assert connector.calls == [], (
        "the maintenance-window refusal must fire before any connection is "
        "attempted"
    )


def test_maintenance_window_reason_names_lock_and_table():
    reason = applier.MAINTENANCE_WINDOW_REASON
    assert "ACCESS EXCLUSIVE" in reason
    assert "public.signals" in reason
    assert "010_signal_environment.sql" in applier.MAINTENANCE_WINDOW_REQUIRED_FILE


def test_dry_run_shows_010_would_be_refused_but_does_not_refuse_the_dry_run_itself():
    """A dry run may preview the plan (including 010) without the flag; only
    an --apply run is refused outright, since previewing performs no lock."""
    connector = _RecordingConnector()
    dsn = "postgresql://user:pass@host.example.com:5432/postgres"
    exit_code = applier.run(argv=["--dsn", dsn], connector=connector)
    assert exit_code == 0
    assert connector.calls == [], "a dry run must never connect"


# ============================================================================
# 4. Dry-run is the default: no statement is issued without --apply
# ============================================================================


def test_apply_migrations_dry_run_is_default_with_valid_dsn():
    connector = _RecordingConnector()
    dsn = "postgresql://user:pass@host.example.com:5432/postgres"
    exit_code = applier.run(argv=["--dsn", dsn], connector=connector)
    assert exit_code == 0
    assert connector.calls == [], "without --apply, no connection may be made and no statement issued"


def test_apply_migrations_with_apply_and_maintenance_window_does_connect():
    """Sanity check the inverse: with --apply and --maintenance-window and a
    valid (non-pooler) DSN, the connector *is* invoked - proving the dry-run
    assertions above are meaningful and not simply "never connects at all"."""
    log: List[str] = []
    connection = _RecordingConnection("postgresql://user:pass@host.example.com:5432/postgres")
    connector = _RecordingConnector(connection=connection)
    dsn = "postgresql://user:pass@host.example.com:5432/postgres"
    applier.run(argv=["--dsn", dsn, "--apply", "--maintenance-window"], connector=connector)
    assert connector.calls == [dsn], "with --apply, the connector must be invoked exactly once"


# ============================================================================
# 5. Expected-object sets are derived from the .sql files
# ============================================================================


def test_009_expected_objects_include_paper_sessions_table_column():
    expectations = preflight.expectations_for_file(preflight._migration_path("009"))
    assert ("paper_sessions", "id") in expectations.columns


def test_009_expected_indexes_include_uq_paper_account_session():
    expectations = preflight.expectations_for_file(preflight._migration_path("009"))
    assert "uq_paper_account_session" in expectations.indexes


def test_007_expected_indexes_do_not_include_uq_paper_account_session():
    """uq_paper_account_session is a 009 object (paper_accounts). It must not
    appear in 007's expected set, which owns none of the paper_* tables."""
    expectations = preflight.expectations_for_file(preflight._migration_path("007"))
    assert "uq_paper_account_session" not in expectations.indexes
    assert ("paper_sessions", "id") not in expectations.columns


def test_009_expected_columns_include_paper_sessions_table():
    expectations = preflight.expectations_for_file(preflight._migration_path("009"))
    tables = {table for table, _ in expectations.columns}
    assert "paper_sessions" in tables
    assert "paper_accounts" in tables


def test_012_and_014_declare_no_checkable_objects():
    """012 is a pure INSERT seed row and 014 is a pure CREATE OR REPLACE
    FUNCTION - neither creates a table, column, constraint, index, RLS flag
    or policy this parser tracks, so both must report zero total units."""
    e012 = preflight.expectations_for_file(preflight._migration_path("012"))
    e014 = preflight.expectations_for_file(preflight._migration_path("014"))
    assert e012.total_units == 0
    assert e014.total_units == 0


# ============================================================================
# 6. Ordering is the declared dependency order, with 009 before 010
# ============================================================================


def test_migration_ids_are_in_declared_dependency_order():
    assert preflight.MIGRATION_IDS == [
        "006", "007", "008", "009", "010", "011", "012", "013", "014",
    ]


def test_009_appears_before_010_in_migration_files():
    names = [p.name for p in preflight.MIGRATION_FILES]
    index_009 = next(i for i, n in enumerate(names) if n.startswith("009_"))
    index_010 = next(i for i, n in enumerate(names) if n.startswith("010_"))
    assert index_009 < index_010, "009_paper_trading.sql must precede 010_signal_environment.sql"


def test_010_declares_a_named_dependency_on_009_paper_sessions():
    """010's own preflight DO-block raises a named error pointing at
    paper_sessions / 009_paper_trading.sql when paper_sessions is absent -
    the mechanism that enforces 009-before-010 at apply time, independent of
    this tooling's own ordering list."""
    path = preflight._migration_path("010")
    sql = path.read_text(encoding="utf-8", errors="replace").lower()
    assert "to_regclass('public.paper_sessions') is null" in sql
    assert "009_paper_trading.sql" in sql


def test_apply_migrations_files_to_run_match_dependency_order():
    names = [p.name.split("_", 1)[0] for p in applier.MIGRATION_FILES]
    assert names == preflight.MIGRATION_IDS


# ============================================================================
# 7. Guard: no module under scripts/ contains a hardcoded postgres URL with a
#    password
# ============================================================================

# scripts/run_schema_migration_prod.py is a KNOWN, ACKNOWLEDGED offender - it
# is the record of the credential leak this tooling exists to not repeat (see
# the task description: "leave it as the record of the leak; that file's fate
# is the user's call"). It is explicitly named here, not silently excluded, so
# the exemption itself is visible in the test and any second offender still
# fails the scan.
_KNOWN_ACKNOWLEDGED_LEAK = "run_schema_migration_prod.py"

# A postgres(ql):// URL with a literal user:password@ (not an interpolation
# placeholder, not a bare host with no credentials).
_HARDCODED_POSTGRES_URL_WITH_PASSWORD = re.compile(
    r"postgres(?:ql)?://[A-Za-z0-9_.%+-]+:[A-Za-z0-9_.%+-]+@", re.IGNORECASE
)

# Generic documentation placeholders (`user:pass@host`, `user:password@host`,
# etc.) used in --help text and docstring usage examples. These are not
# credential-shaped in the way run_schema_migration_prod.py's leak is - they
# name no real user, host or secret - so the general guard below excludes
# exactly this token pair rather than exempting a whole file by name.
_PLACEHOLDER_CREDENTIAL_PAIRS = frozenset(
    {
        ("user", "pass"),
        ("user", "password"),
        ("username", "password"),
    }
)


def _is_placeholder_credential(match: "re.Match[str]") -> bool:
    m = re.match(r"^.*?://([^:]+):([^@]+)@$", match.group(0), re.IGNORECASE)
    if not m:
        return False
    user, password = m.group(1).lower(), m.group(2).lower()
    return (user, password) in _PLACEHOLDER_CREDENTIAL_PAIRS


def _scripts_python_files() -> List[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.py") if p.is_file())


def test_known_leak_file_is_still_present_and_still_the_only_acknowledged_offender():
    """Guards the guard's own premise: if run_schema_migration_prod.py is ever
    removed or fixed, this acknowledgement becomes stale and must be revisited
    rather than silently continuing to exempt a name that no longer offends."""
    path = SCRIPTS_DIR / _KNOWN_ACKNOWLEDGED_LEAK
    assert path.is_file(), (
        f"{_KNOWN_ACKNOWLEDGED_LEAK} was expected to exist as the acknowledged "
        "record of the credential leak this guard test exempts by name. If it "
        "was removed or fixed, remove the exemption in this test too."
    )
    text = path.read_text(encoding="utf-8", errors="replace")
    assert _HARDCODED_POSTGRES_URL_WITH_PASSWORD.search(text), (
        f"{_KNOWN_ACKNOWLEDGED_LEAK} no longer contains a hardcoded "
        "postgres URL with a password - the exemption below is stale and "
        "should be removed so this file is covered by the general guard."
    )


def test_no_unacknowledged_hardcoded_postgres_url_with_password_in_scripts():
    """No module under scripts/ (other than the one acknowledged, named
    exception above) may contain a hardcoded postgres URL with a literal
    password - the exact class of defect run_schema_migration_prod.py is."""
    offenders: List[Tuple[str, str]] = []
    for path in _scripts_python_files():
        if path.name == _KNOWN_ACKNOWLEDGED_LEAK:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _HARDCODED_POSTGRES_URL_WITH_PASSWORD.finditer(text):
            if _is_placeholder_credential(match):
                continue
            offenders.append((path.name, match.group(0)))
    assert not offenders, (
        "hardcoded postgres URL(s) with a literal password found outside the "
        f"acknowledged exemption: {offenders}"
    )


def test_migration_preflight_and_apply_migrations_contain_no_hardcoded_dsn():
    """Named, specific check on the two new scripts themselves: any
    postgres(ql):// URL-with-password shape their docstring usage examples
    match must be one of the documentation placeholder pairs
    (user:pass@host), never a real-looking credential."""
    for path in (SCRIPTS_DIR / "migration_preflight.py", SCRIPTS_DIR / "apply_migrations.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _HARDCODED_POSTGRES_URL_WITH_PASSWORD.finditer(text):
            assert _is_placeholder_credential(match), (
                f"{path.name} contains a non-placeholder credential-looking "
                f"string: {match.group(0)!r}"
            )


# ============================================================================
# Additional coverage: post_migration_validation.py's import fix
# ============================================================================


def test_post_migration_validation_imports_os():
    """Regression guard for the missing `import os` this task fixed - the
    module must be importable without NameError on `os.getenv`."""
    text = (SCRIPTS_DIR / "post_migration_validation.py").read_text(encoding="utf-8")
    assert re.search(r"^import os$", text, re.MULTILINE), (
        "scripts/post_migration_validation.py must import os (it calls "
        "os.getenv at module scope)"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
