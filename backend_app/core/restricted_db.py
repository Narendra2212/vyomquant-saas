"""core/restricted_db.py — the one real, restricted database connection in this codebase.

TENANT-01's own evidence worked out why `FORCE ROW LEVEL SECURITY` on `payment_methods`
would be a complete no-op: every DSN in this repository connects as `postgres`, a
superuser, and a superuser is exempt from row security regardless of FORCE (PostgreSQL's
own documented behaviour, confirmed empirically against a real replica). The fix is not a
flag on the existing connection; it is a SECOND connection, authenticating as a role that
is neither a superuser nor BYPASSRLS, so row security actually applies to it.

`backend_app/migrations/047_tenant01_restricted_payment_methods_role.sql` creates that
role (``payment_methods_app``) with exactly ``SELECT, DELETE`` on ``public.payment_methods``
and two owner-keyed RLS policies. This module is the app-side half: a dependency that opens
a session through that role, sets the same ``request.jwt.claim.sub`` GUC the real Supabase
JWT path sets (so the existing ``auth.uid() = user_id`` policy applies unmodified), and
scopes every query inside one transaction per request so the GUC never leaks to the next
pooled connection's caller.

Deliberately narrow. This is not a general-purpose "restricted engine" other routes should
reach for -- every other table in this schema is still served through the owner connection
(``core/database_pool.get_db``) for the reasons TENANT-01's own evidence already gives (21
of 23 per-request call sites are background/worker processes with no single tenant to scope
by; forcing RLS platform-wide would silently empty their reads). Adding a second restricted
role for a different table is a decision for whoever owns that table's access pattern, not
something this module should generalise ahead of a second real need.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# Separate from DATABASE_URL on purpose: this is a DIFFERENT role with DIFFERENT,
# deliberately narrower privileges, not a variant connection string for the same
# superuser. Mixing the two into one env var invites someone to "simplify" them back
# together later, which would silently restore the superuser bypass this module exists
# to avoid.
_ENV_VAR = "PAYMENT_METHODS_DB_URL"

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def _get_engine() -> Optional[Engine]:
    """Lazily build the restricted engine. Returns None if PAYMENT_METHODS_DB_URL is
    not configured and this is not a declared local/test environment -- the caller
    (get_payment_methods_db) is responsible for refusing the request rather than
    silently falling back to the shared owner connection in that case, which would
    defeat the entire point of this module.

    Outside that refusal, a declared local/test environment with no
    PAYMENT_METHODS_DB_URL set falls back to the SAME SQLite file
    core.database_pool's own get_db already falls back to -- mirroring
    resolve_database_url's own local_state_is_acceptable() gate exactly, for the same
    reason: a test exercising "does this dependency inject a real, working Session"
    should get one, not a 503, while production still refuses outright. SQLite has no
    roles and no row security, so this fallback proves the dependency wiring works, not
    tenant isolation -- the real isolation guarantee is proven separately against a real
    Postgres replica (tests/test_tenant01_restricted_payment_methods_role_postgres.py).
    """
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    url = os.getenv(_ENV_VAR)
    if not url:
        from backend_app.core.database_pool import SQLITE_FALLBACK_URL
        from backend_app.core.runtime_env import local_state_is_acceptable

        if not local_state_is_acceptable():
            return None
        url = SQLITE_FALLBACK_URL
        logger.warning(
            "[restricted_db] %s not set; falling back to %s because this is a "
            "declared local/test environment. This connection has no RLS and no "
            "roles -- it proves dependency wiring, not tenant isolation.",
            _ENV_VAR, SQLITE_FALLBACK_URL,
        )

    # Deliberately small and un-pooled-beyond-a-handful: this engine serves exactly two
    # routes, not the whole application, and payment_methods_app's own CONNECTION LIMIT
    # 10 (set in the migration) is the real ceiling this should never approach.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(
        url, pool_size=3, max_overflow=2, pool_pre_ping=True, connect_args=connect_args
    )
    _session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    logger.info("[restricted_db] payment_methods_app engine initialised")
    return _engine


@contextmanager
def restricted_payment_methods_session(user_id: str) -> Iterator[Session]:
    """Yield a Session authenticated as payment_methods_app, scoped to `user_id` for
    the lifetime of the transaction. Raises RuntimeError if PAYMENT_METHODS_DB_URL is
    not configured -- callers (the FastAPI dependency below) turn that into a 503
    rather than letting a route silently run unscoped.

    `SET LOCAL` is transaction-scoped, not session-scoped, by design: a pooled
    connection handed to the next request must never carry the previous caller's
    identity. Called on a freshly begun transaction so there is no earlier
    autocommitted statement for the SET LOCAL to trail.
    """
    engine = _get_engine()
    if engine is None or _session_factory is None:
        raise RuntimeError(
            f"{_ENV_VAR} is not configured; the payment_methods_app role has no "
            "connection string to use."
        )

    session = _session_factory()
    try:
        # SET LOCAL is PostgreSQL-specific syntax; the local/test SQLite fallback this
        # engine can use has no RLS to scope in the first place, so there is nothing to
        # set -- skipping it there rather than erroring on a GUC SQLite does not have.
        if engine.dialect.name == "postgresql":
            session.execute(text("SET LOCAL request.jwt.claim.sub = :uid"), {"uid": user_id})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _current_user_dep():
    """Imported lazily, inside the function that needs it, to avoid a module-level
    circular import: core.dependencies imports from several routers' own modules at
    import time, and this module is imported by one of them (routers/billing.py)."""
    from backend_app.core.dependencies import get_current_user

    return get_current_user


def get_payment_methods_db(user: dict = Depends(_current_user_dep())) -> Iterator[Session]:
    """FastAPI dependency for the two routes this module exists for
    (routers/billing.py::get_payment_methods / delete_payment_method). Depends on
    get_current_user the normal FastAPI way, so the route's own `user` parameter and
    this dependency's identity can never disagree. Raises a 503 rather than falling
    back to the shared owner connection if PAYMENT_METHODS_DB_URL is not configured,
    matching PAPER-04's own established pattern for a missing persistence credential:
    fail the request cleanly, never silently run unscoped.
    """
    try:
        with restricted_payment_methods_session(str(user["id"])) as session:
            yield session
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PAYMENT_METHODS_DB_UNAVAILABLE",
                "message": (
                    f"{_ENV_VAR} is not configured. See "
                    "backend_app/migrations/047_tenant01_restricted_payment_methods_role.sql."
                ),
            },
        )
