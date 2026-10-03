"""
tests/test_database_isolation_level_control.py

production-launch-hardening task 13.21, defect A.

WHAT THIS PINS, AND WHY IT NEEDS NO POSTGRESQL
------------------------------------------------------------------------------
The SERIALIZABLE isolation control had never executed. ``DatabasePool.
get_transactional_session`` and ``core/database.py``'s two FINANCIAL branches each issued
a raw f-string ``SET TRANSACTION ISOLATION LEVEL {level}`` through ``Session.execute()``
wrapped in ``except Exception: logger.warning(...)``. Under SQLAlchemy 2.x -- pinned
``sqlalchemy==2.0.35`` in ``requirements-base.txt``, confirmed installed -- a plain
``str`` is rejected before it reaches any server:

    ArgumentError: Textual SQL expression 'SET TRANSACTION ISOLATION...' should be
    explicitly declared as text('SET TRANSACTION ISOLATION...')

so the statement never ran, the ``logger.warning`` was its only trace, and the caller got
a session quietly running at the server default. Measured against the production server
before the fix, ``SHOW transaction_isolation`` on the session
``get_transactional_session("SERIALIZABLE")`` returned read ``read committed``.

That ``ArgumentError`` is raised by SQLAlchemy itself, not by any database, so **the
"raw string is rejected" half is pinnable with no database at all** -- an in-memory SQLite
session raises the identical error. And SQLite's own dialect accepts ``SERIALIZABLE``
(it is in fact SQLite's default), so the "the repaired mechanism genuinely applies the
level" half is pinnable on SQLite too, read back through
``Connection.get_isolation_level()``. Hence: no PostgreSQL, no Docker, no network.

What SQLite cannot show is the PostgreSQL-specific fragility that rules out the
obvious half-fix of merely wrapping the statement in ``text()``: on PostgreSQL a
``text()``-wrapped ``SET TRANSACTION ISOLATION LEVEL`` issued after one statement has run
fails with ``psycopg2.errors.ActiveSqlTransaction: SET TRANSACTION ISOLATION LEVEL must
be called before any query``. That was measured by hand against the production server and
is recorded in task 13.21 and in ``db_isolation.IsolationLevelUnavailable``'s docstring;
it is not asserted here, because asserting it needs PostgreSQL. The source-level
assertions below pin the consequence instead: neither module may go back to issuing that
statement at all.

BOTH HALVES ARE PINNED, which is the point of the task:
  1. the mechanism WORKS -- a session handed back as SERIALIZABLE reports SERIALIZABLE;
  2. a failure to apply it RAISES rather than warning -- the swallow is gone, and its
     absence is asserted against the shipped source, not just against behaviour.

**Validates: Requirements 1.16 / 2.16** (the database-level serialisation half, which
task 12.6 and task 13.11 both record as BLOCKED; a SERIALIZABLE session that actually
works is the mechanism that half needs) and ``bugfix.md``'s ``§Bug condition`` -- a safety
control that cannot be applied must not let the caller continue believing it was.
"""

from __future__ import annotations

import inspect
import logging
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend_app.core import database as core_database
from backend_app.core import database_pool as core_database_pool
from backend_app.core.db_isolation import (
    SUPPORTED_ISOLATION_LEVELS,
    IsolationLevelUnavailable,
    isolated_session,
    normalise_isolation_level,
)

_REPO = Path(__file__).resolve().parent.parent
_DATABASE_PY = _REPO / "backend_app" / "core" / "database.py"
_DATABASE_POOL_PY = _REPO / "backend_app" / "core" / "database_pool.py"


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite://")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_factory(sqlite_engine):
    return sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE DEFECT ITSELF: the mechanism the old code used cannot work on 2.x
# ══════════════════════════════════════════════════════════════════════════════


def test_a_raw_string_set_transaction_statement_is_rejected_by_sqlalchemy(sqlite_factory):
    """The exact call the three pre-fix sites made, against no database in particular.

    This is the whole defect in one assertion: the statement never reached a server, so
    wrapping it in ``except Exception`` could only ever hide it.
    """
    session = sqlite_factory()
    try:
        with pytest.raises(ArgumentError) as excinfo:
            session.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
    finally:
        session.close()

    message = str(excinfo.value)
    assert "Textual SQL expression" in message
    assert "should be explicitly declared as text(" in message


def test_the_shipped_sqlalchemy_is_the_2_x_that_rejects_it():
    """Guard the premise. If this project ever moves back to 1.x the assertion above
    stops meaning what it says, so the version is pinned here rather than assumed.
    """
    import sqlalchemy

    major = int(sqlalchemy.__version__.split(".")[0])
    assert major >= 2, (
        f"this file's reasoning is specific to SQLAlchemy 2.x; found "
        f"{sqlalchemy.__version__}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. HALF ONE: THE REPAIRED MECHANISM ACTUALLY APPLIES THE LEVEL
# ══════════════════════════════════════════════════════════════════════════════


def test_isolated_session_applies_and_proves_serializable(sqlite_engine, sqlite_factory):
    session = isolated_session(sqlite_factory, sqlite_engine, "SERIALIZABLE")
    try:
        assert session.connection().get_isolation_level() == "SERIALIZABLE"
        assert session.get_bind().get_execution_options()["isolation_level"] == "SERIALIZABLE"
        # and it is a usable session, not just a configured one
        assert session.execute(text("select 1")).scalar() == 1
    finally:
        session.close()


def test_isolated_session_applies_a_non_default_level_too(sqlite_engine, sqlite_factory):
    """SERIALIZABLE happens to be SQLite's own default, so proving only that would not
    distinguish "we applied it" from "we did nothing". READ UNCOMMITTED is the other
    level SQLite's dialect accepts, and it is NOT the default, so a successful read-back
    of it is positive evidence that the execution option is doing the work.
    """
    session = isolated_session(sqlite_factory, sqlite_engine, "READ UNCOMMITTED")
    try:
        assert session.connection().get_isolation_level() == "READ UNCOMMITTED"
    finally:
        session.close()


def test_isolated_session_accepts_lowercase_and_odd_spacing(sqlite_engine, sqlite_factory):
    session = isolated_session(sqlite_factory, sqlite_engine, "  read   uncommitted ")
    try:
        assert session.connection().get_isolation_level() == "READ UNCOMMITTED"
    finally:
        session.close()


def test_isolated_session_preserves_the_factory_settings(sqlite_engine, sqlite_factory):
    """The level is applied by rebinding, so the sessionmaker's own configuration must
    survive -- a financial session that silently started autoflushing would be a new bug
    introduced by the fix.
    """
    session = isolated_session(sqlite_factory, sqlite_engine, "SERIALIZABLE")
    try:
        assert session.autoflush is False
    finally:
        session.close()


def test_the_pool_hands_back_a_verified_session(monkeypatch, sqlite_engine):
    pool = object.__new__(core_database_pool.DatabasePool)
    pool.initialize(database_url="sqlite://")
    try:
        session = pool.get_transactional_session("SERIALIZABLE")
        try:
            # The explicit execution option is the discriminator, not the reported level:
            # SERIALIZABLE is SQLite's own default, so a read-back alone cannot tell
            # "we applied it" from the pre-fix "we skipped it on sqlite entirely".
            assert session.get_bind().get_execution_options().get(
                "isolation_level"
            ) == "SERIALIZABLE"
            assert session.connection().get_isolation_level() == "SERIALIZABLE"
        finally:
            session.close()
    finally:
        pool.close()


# ══════════════════════════════════════════════════════════════════════════════
# 3. HALF TWO: A FAILURE TO APPLY IT RAISES, AND DOES NOT WARN
# ══════════════════════════════════════════════════════════════════════════════


def test_an_unrecognised_level_raises(sqlite_engine, sqlite_factory):
    with pytest.raises(IsolationLevelUnavailable) as excinfo:
        isolated_session(sqlite_factory, sqlite_engine, "SERIALISABLE")  # British spelling
    assert "not a transaction isolation level" in str(excinfo.value)


def test_a_level_the_dialect_refuses_raises(sqlite_engine, sqlite_factory):
    """``READ COMMITTED`` is a real SQL isolation level that SQLite's dialect does not
    implement. The old code's response to an inapplicable level was a warning; the new
    code's is an exception.
    """
    with pytest.raises(IsolationLevelUnavailable) as excinfo:
        isolated_session(sqlite_factory, sqlite_engine, "READ COMMITTED")
    assert "could not apply isolation level READ COMMITTED" in str(excinfo.value)
    assert "ArgumentError" in str(excinfo.value)


def test_a_server_reporting_a_different_level_raises(sqlite_engine, sqlite_factory):
    """The read-back is load-bearing, not decoration. If the option were accepted and the
    server still ran at something else -- which is exactly the state the defect left
    production in -- the session must not be handed back.
    """
    from sqlalchemy.engine import Connection

    original = Connection.get_isolation_level
    try:
        Connection.get_isolation_level = lambda self: "READ COMMITTED"
        with pytest.raises(IsolationLevelUnavailable) as excinfo:
            isolated_session(sqlite_factory, sqlite_engine, "SERIALIZABLE")
    finally:
        Connection.get_isolation_level = original

    message = str(excinfo.value)
    assert "asked for isolation level SERIALIZABLE" in message
    assert "reports READ COMMITTED" in message
    assert "not what it claims" in message


def test_a_failure_raises_instead_of_logging_a_warning(sqlite_engine, sqlite_factory, caplog):
    """The specific regression. Pre-fix, this path logged
    ``Failed to set isolation level SERIALIZABLE: ...`` at WARNING and returned the
    session anyway. Nothing may be logged in place of the exception.
    """
    with caplog.at_level(logging.WARNING):
        with pytest.raises(IsolationLevelUnavailable):
            isolated_session(sqlite_factory, sqlite_engine, "READ COMMITTED")

    swallowed = [r for r in caplog.records if "isolation level" in r.getMessage().lower()]
    assert swallowed == [], (
        "a failure to apply an isolation level was logged rather than raised: "
        f"{[r.getMessage() for r in swallowed]}"
    )


def test_the_failed_session_is_closed_not_leaked(sqlite_engine, sqlite_factory):
    """A raise mid-way through must not leave the session open, or the honest failure
    becomes a connection leak -- which would be a new defect introduced by the fix."""
    closed: list[object] = []

    def _factory(**kwargs):
        session = sqlite_factory(**kwargs)
        original_close = session.close

        def _close():
            closed.append(session)
            return original_close()

        session.close = _close
        return session

    with pytest.raises(IsolationLevelUnavailable):
        isolated_session(_factory, sqlite_engine, "READ COMMITTED")

    assert len(closed) == 1, "isolated_session raised without closing the session it opened"


def test_normalise_rejects_autocommit_deliberately():
    """``AUTOCOMMIT`` rides in the same SQLAlchemy option slot but is a connection mode,
    not an isolation level, and does not read back as itself -- so it cannot satisfy this
    module's verify-before-returning contract and is excluded on purpose.
    """
    assert "AUTOCOMMIT" not in SUPPORTED_ISOLATION_LEVELS
    with pytest.raises(IsolationLevelUnavailable):
        normalise_isolation_level("AUTOCOMMIT")


# ══════════════════════════════════════════════════════════════════════════════
# 4. THE SOURCE ITSELF: the broken mechanism and the swallow are gone
#
# Behavioural tests alone cannot cover this: the two `core/database.py` call sites live
# in a fallback branch that only executes when `core.database_pool` fails to import, so
# no in-process test can reach them (see test_the_financial_branches_were_unreachable
# below). Reading the shipped source is the only cover available for them, and it is also
# what stops the raw statement being reintroduced anywhere in either module.
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", [_DATABASE_PY, _DATABASE_POOL_PY], ids=["database", "database_pool"])
def test_no_module_issues_a_raw_set_transaction_statement(path):
    source = path.read_text(encoding="utf-8")
    code = [
        line for line in source.splitlines()
        if "SET TRANSACTION ISOLATION LEVEL" in line and not line.lstrip().startswith("#")
    ]
    offenders = [line for line in code if "execute(" in line]
    assert offenders == [], (
        f"{path.name} still issues SET TRANSACTION ISOLATION LEVEL as a statement: {offenders}"
    )


@pytest.mark.parametrize("path", [_DATABASE_PY, _DATABASE_POOL_PY], ids=["database", "database_pool"])
def test_no_module_swallows_an_isolation_failure_into_a_warning(path):
    source = path.read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in source.splitlines()
        if re.search(r"logger\.warning\(.*[Ff]ailed to set isolation", line)
    ]
    assert offenders == [], f"{path.name} still warns instead of raising: {offenders}"


def test_the_pool_no_longer_skips_the_control_on_sqlite():
    """The old guard was ``if "sqlite" not in str(self._engine.url):`` -- i.e. on SQLite
    the control was skipped entirely and the session returned as though it had been
    applied. The execution option is dialect-aware, so the skip is gone.
    """
    source = inspect.getsource(core_database_pool.DatabasePool.get_transactional_session)
    assert "sqlite" not in source.lower().split('"""')[-1], (
        "get_transactional_session's body still branches on the sqlite dialect"
    )
    assert "isolated_session(" in source


def test_the_look_alike_session_factories_are_gone():
    """``SessionLocalFinancial`` and ``SessionLocalReadOnly`` both bound the same bare
    engine as ``SessionLocal`` and carried no isolation level: the names promised a
    control they did not hold. They are deleted rather than repaired.
    """
    source = _DATABASE_PY.read_text(encoding="utf-8")
    for name in ("SessionLocalFinancial", "SessionLocalReadOnly"):
        assignments = [
            line for line in source.splitlines()
            if re.match(rf"\s*{name}\s*=", line)
        ]
        assert assignments == [], f"{name} is still defined: {assignments}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. REACHABILITY, PINNED SO THE SEVERITY CLAIM IN 13.21 STAYS HONEST
# ══════════════════════════════════════════════════════════════════════════════


def test_the_financial_branches_were_unreachable_and_still_route_through_the_pool():
    """``core.database``'s FINANCIAL branches live under ``if not POOLING_AVAILABLE``.
    POOLING_AVAILABLE is True in every configuration that imports at all, so the exported
    ``get_db`` is ``database_pool``'s -- signature ``()``. This is the measured basis for
    13.21's severity claim: the control was not merely uncalled, it was undefined.
    """
    assert core_database.POOLING_AVAILABLE is True
    assert core_database.get_db.__module__ == "backend_app.core.database_pool"
    assert str(inspect.signature(core_database.get_db)) == "()"
    with pytest.raises(TypeError):
        core_database.get_db(core_database.TransactionType.FINANCIAL)


def test_the_financial_transaction_type_now_has_somewhere_to_go():
    """The enum was reachable and the control behind it was not. ``TransactionType`` is
    only honest if a caller can obtain what it names.
    """
    assert core_database.get_isolation_level(
        core_database.TransactionType.FINANCIAL
    ) == "SERIALIZABLE"
    assert hasattr(core_database, "get_financial_db_context")


def test_get_financial_db_context_yields_a_verified_session_and_closes_it(monkeypatch):
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    class _Pool:
        def get_transactional_session(self, isolation_level):
            return isolated_session(factory, engine, isolation_level)

    monkeypatch.setattr(core_database, "get_db_pool", lambda: _Pool())
    try:
        with core_database.get_financial_db_context() as session:
            assert session.connection().get_isolation_level() == "SERIALIZABLE"
            opened = session
        # the context manager must close on the way out, not leave it to the caller
        assert opened.get_transaction() is None
    finally:
        engine.dispose()


def test_get_financial_db_context_propagates_the_failure(monkeypatch):
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    class _Pool:
        def get_transactional_session(self, isolation_level):
            return isolated_session(factory, engine, "READ COMMITTED")

    monkeypatch.setattr(core_database, "get_db_pool", lambda: _Pool())
    try:
        with pytest.raises(IsolationLevelUnavailable):
            with core_database.get_financial_db_context():
                pass  # pragma: no cover - the context must never open
    finally:
        engine.dispose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
