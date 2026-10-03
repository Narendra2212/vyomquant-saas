"""
tests/test_database_isolation_level_postgres.py

production-launch-hardening task 13.21 -- the half of defect A's proof that only a real
PostgreSQL can give, and the reason defect B (no PostgreSQL in CI) had to be fixed in the
same task.

``tests/test_database_isolation_level_control.py`` pins everything about the isolation
control that is observable without a server: that SQLAlchemy 2.x rejects the raw string
the pre-fix code issued, that the repaired mechanism applies and reads back a level, that
a failure raises instead of warning, and that neither module can go back to issuing the
old statement. What it cannot show is the thing the control is actually FOR: that
PostgreSQL itself reports ``serializable`` for the session, which is what
``SHOW transaction_isolation`` answers and what
``tests/test_transaction_isolation_serializable.py`` was written to check and could never
run.

It also pins the specific PostgreSQL failure that rules out the obvious half-fix of
wrapping the old statement in ``text()``: once a session has issued one statement, a
``SET TRANSACTION ISOLATION LEVEL`` is refused with
``psycopg2.errors.ActiveSqlTransaction: SET TRANSACTION ISOLATION LEVEL must be called
before any query``. The execution-option mechanism is applied at connection checkout,
before any transaction starts, so it has no such window -- and that difference is asserted
here rather than described.

EVERY TEST IN THIS FILE SKIPS unless ``DATABASE_URL`` names a PostgreSQL server, so the
default ``pytest tests/`` lane -- which runs against SQLite and must keep doing so -- is
unaffected. The ``database-tests`` job in ``.github/workflows/01-pr-check.yml`` is where
it runs for real.

**Validates: Requirements 1.16 / 2.16** (the database-level serialisation half, recorded
BLOCKED by tasks 12.6 and 13.11) and ``bugfix.md``'s ``§Bug condition``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith(("postgresql", "postgres://")),
    reason=(
        "needs a real PostgreSQL through DATABASE_URL; provided by the database-tests "
        "job in .github/workflows/01-pr-check.yml, skipped in the SQLite lane"
    ),
)


@pytest.fixture(scope="module")
def pool():
    from backend_app.core.database_pool import get_db_pool

    pool = get_db_pool()
    assert pool.engine.url.get_backend_name() == "postgresql", (
        f"expected a postgresql engine, got {pool.engine.url.get_backend_name()}"
    )
    return pool


def _isolation_of(session) -> str:
    return str(session.execute(text("SHOW transaction_isolation")).scalar()).lower()


def test_a_transactional_session_really_is_serializable(pool):
    """The assertion the whole control exists for, answered by the server.

    Pre-fix this returned ``read committed`` -- measured against the production server --
    while ``get_transactional_session`` was named and documented as SERIALIZABLE and
    logged a warning nobody read.
    """
    session = pool.get_transactional_session("SERIALIZABLE")
    try:
        assert _isolation_of(session) == "serializable"
    finally:
        session.close()


def test_the_level_is_in_effect_before_the_first_statement(pool):
    """Not just "set at some point". ``SET TRANSACTION ISOLATION LEVEL`` has to be the
    first statement in a transaction; the execution option sidesteps that by applying the
    level at connection checkout. So the level must already hold on the very first query,
    and must still hold after one.
    """
    session = pool.get_transactional_session("SERIALIZABLE")
    try:
        assert _isolation_of(session) == "serializable"
        session.execute(text("SELECT 1"))
        assert _isolation_of(session) == "serializable"
    finally:
        session.rollback()
        session.close()


def test_the_old_mechanism_is_refused_by_postgresql_mid_transaction(pool):
    """Why wrapping the old statement in ``text()`` would not have been a fix.

    This is the second, independent reason the pre-fix approach was wrong: even with the
    SQLAlchemy ``ArgumentError`` removed, PostgreSQL refuses the statement once the
    transaction has done anything. A per-request ``SET`` on a pooled session is therefore
    fragile by construction, not merely mis-typed.
    """
    from sqlalchemy.exc import InternalError

    session = pool.get_session()
    try:
        session.execute(text("SELECT 1"))
        with pytest.raises(InternalError) as excinfo:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        assert "must be called before any query" in str(excinfo.value)
    finally:
        session.rollback()
        session.close()


def test_a_different_level_also_takes_effect(pool):
    """Guards against a false positive: if SERIALIZABLE happened to be the server
    default, the test above would pass while the mechanism did nothing. A second,
    non-default level proves the option is what is doing the work.
    """
    session = pool.get_transactional_session("REPEATABLE READ")
    try:
        assert _isolation_of(session) == "repeatable read"
    finally:
        session.close()


def test_the_general_session_is_still_read_committed(pool):
    """The scope of the fix, asserted so it cannot drift.

    `core/database.py`'s design is FINANCIAL = SERIALIZABLE, GENERAL/READ_ONLY = READ
    COMMITTED. Task 13.21 deliberately did NOT make the shared engine SERIALIZABLE: only
    `backend/paper/paper_repository.py` retries on `40001`, so a global change would turn
    a dormant control into live serialization failures. If someone later decides the
    opposite -- which is the open decision task 13.21 records against
    `tests/test_transaction_isolation_serializable.py` -- this test is the one that should
    be changed, consciously, in that same commit.
    """
    session = pool.get_session()
    try:
        assert _isolation_of(session) == "read committed"
    finally:
        session.close()


def test_a_failure_to_apply_raises_against_a_real_server(pool):
    from backend_app.core.db_isolation import IsolationLevelUnavailable

    with pytest.raises(IsolationLevelUnavailable):
        pool.get_transactional_session("SERIALISABLE")


def test_the_reachable_entry_point_works_against_a_real_server():
    from backend_app.core.database import get_financial_db_context

    with get_financial_db_context() as session:
        assert _isolation_of(session) == "serializable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
