"""
core/db_isolation.py - Verified transaction isolation levels.

FIN-CRITICAL-001 / production-launch-hardening task 13.21.

This module exists so that the SERIALIZABLE control cannot be *advertised and absent*
at the same time, which is what it was until task 13.21.

It lives in its own module rather than in ``core/database_pool.py`` on purpose:
``core/database.py`` keeps a fallback path that runs only when ``database_pool`` fails
to import, and that path needs the same mechanism. Importing it from the module that
just failed to import would be no mechanism at all.
"""

from typing import Any


class IsolationLevelUnavailable(RuntimeError):
    """A requested transaction isolation level could not be applied, or could not be
    proven to have been applied.

    THE DEFECT THIS REPLACES. ``DatabasePool.get_transactional_session`` and
    ``core/database.py``'s FINANCIAL branches issued a raw f-string
    ``SET TRANSACTION ISOLATION LEVEL {level}`` through ``Session.execute()`` and wrapped
    it in ``except Exception: logger.warning(...)``. Under SQLAlchemy 2.x (pinned 2.0.35)
    a plain ``str`` is rejected outright -- ``ArgumentError: Textual SQL expression ...
    should be explicitly declared as text(...)`` -- so the statement NEVER ran, the
    warning was its only trace, and the caller was handed a session quietly running at
    the server default while the code read as though SERIALIZABLE were in force.
    Measured against the production server: the level in effect was ``read committed``.

    Wrapping the statement in ``text()`` would not have been enough either. Also measured
    against the production server: a ``text()``-wrapped ``SET TRANSACTION ISOLATION
    LEVEL`` issued on a session that has already run one statement fails with
    ``psycopg2.errors.ActiveSqlTransaction: SET TRANSACTION ISOLATION LEVEL must be
    called before any query``. The supported mechanism is the ``isolation_level``
    execution option, which SQLAlchemy applies to the DBAPI connection at checkout --
    before any transaction begins -- which is why :func:`isolated_session` uses it.

    Raising is the point. A caller that asked for SERIALIZABLE and cannot have it must
    not continue believing it got it.
    """


#: The four SQL-standard isolation levels, as SQLAlchemy spells them for the
#: ``isolation_level`` execution option. Membership here is a spelling check only: no
#: backend accepts all four (SQLite accepts SERIALIZABLE and READ UNCOMMITTED and
#: rejects READ COMMITTED), and the dialect gets the final say at connect time -- its
#: ``ArgumentError`` is deliberately allowed to propagate rather than being swallowed.
#:
#: ``AUTOCOMMIT`` is deliberately NOT a member. SQLAlchemy accepts it in the same option
#: slot, but it is a connection mode rather than an isolation level, and it does not read
#: back as itself (requesting it on SQLite reads back as ``SERIALIZABLE``), so it cannot
#: satisfy the verification this module's contract rests on.
SUPPORTED_ISOLATION_LEVELS = frozenset({
    "SERIALIZABLE",
    "REPEATABLE READ",
    "READ COMMITTED",
    "READ UNCOMMITTED",
})


def normalise_isolation_level(isolation_level: Any) -> str:
    """Upper-case and collapse whitespace in ``isolation_level``, rejecting anything
    that is not one of :data:`SUPPORTED_ISOLATION_LEVELS`.

    Raises:
        IsolationLevelUnavailable: if the value is not a recognised isolation level.
    """
    normalised = " ".join(str(isolation_level).strip().upper().split())
    if normalised not in SUPPORTED_ISOLATION_LEVELS:
        raise IsolationLevelUnavailable(
            f"{isolation_level!r} is not a transaction isolation level this module will "
            f"apply; expected one of {sorted(SUPPORTED_ISOLATION_LEVELS)}"
        )
    return normalised


def isolated_session(session_factory: Any, engine: Any, isolation_level: str):
    """Open a session that is VERIFIED to be running at ``isolation_level``.

    The level is applied through ``engine.execution_options(isolation_level=...)``, the
    mechanism SQLAlchemy supports, and then read back off the session's own connection
    with ``Connection.get_isolation_level()`` -- a real round trip to the server on
    PostgreSQL -- and compared against what was asked for. Nothing is assumed.

    Args:
        session_factory: a ``sessionmaker``. It is called with an explicit ``bind``, so
            its ``autocommit``/``autoflush`` settings are preserved.
        engine: the engine whose pool the session should draw from.
        isolation_level: one of :data:`SUPPORTED_ISOLATION_LEVELS`, case-insensitive.

    Returns:
        A ``Session`` with an open connection at the requested isolation level.

    Raises:
        IsolationLevelUnavailable: if the level is unrecognised, if the dialect refuses
            it, or if the server reports a different level than the one requested. The
            session is closed before the exception leaves. It is never downgraded
            silently -- see :class:`IsolationLevelUnavailable`.
    """
    requested = normalise_isolation_level(isolation_level)
    session = session_factory(bind=engine.execution_options(isolation_level=requested))
    try:
        reported = session.connection().get_isolation_level()
    except Exception as exc:
        session.close()
        raise IsolationLevelUnavailable(
            f"could not apply isolation level {requested} on "
            f"{engine.url.get_backend_name()}: {type(exc).__name__}: {exc}"
        ) from exc
    in_effect = " ".join(str(reported).strip().upper().split())
    if in_effect != requested:
        session.close()
        raise IsolationLevelUnavailable(
            f"asked for isolation level {requested} but the connection reports "
            f"{in_effect} -- refusing to hand back a session that is not what it claims"
        )
    return session
