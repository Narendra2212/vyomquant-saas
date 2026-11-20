"""
core/tenant_db_context.py - bind a SQLAlchemy session to exactly one tenant.

The RLS policies in ``alembic/versions/implement_rls_policies_20260817.py`` compare
``tenant_id`` against ``current_setting('app.current_tenant_id', true)``. Nothing on a
request path ever sets that setting: ``SET LOCAL app.current_tenant_id`` appears only in
``backend/transactional_execution_manager.py`` and ``backend/tenant_rls_validator.py``,
``get_db``/``get_db_context`` set nothing, and no middleware sets it either. So the
policies have no tenant to compare against, and tenant isolation on the SQLAlchemy path
currently rests entirely on application-level ``WHERE`` clauses.

This module supplies the missing half. It is a prerequisite for enabling
``FORCE ROW LEVEL SECURITY``, not a consequence of it: with the setting unset,
``current_setting(..., true)`` returns NULL, the policy comparison yields NULL rather
than TRUE, and an enforced policy therefore matches zero rows. Forcing RLS before this
is in place converts reads on ``orders``, ``positions`` and ``fills`` into silently
empty result sets. See ``docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md``.

Four implementation choices here are load-bearing.

**set_config(), not SET LOCAL.** ``SET`` is not a parameterisable statement in
PostgreSQL - it takes no bind parameters. The existing
``text("SET LOCAL app.current_tenant_id = :tenant_id")`` works only because psycopg2
interpolates parameters client-side before the statement is sent; on a driver that binds
server-side (asyncpg) the same statement fails, and either way the value's safety depends
on the driver rather than on the protocol. ``set_config(name, value, is_local)`` is an
ordinary function call, so the tenant id binds like any other parameter.

**is_local must be true.** ``core/database_pool.py`` appends
``pgbouncer=true&pool_mode=transaction`` to the Supabase URL, so a backend connection is
returned to the pool when its transaction ends and handed to a different request next.
``set_config(..., false)`` is session-scoped and would survive that handover, leaving the
previous tenant's id installed for whoever picks the connection up - a cross-tenant read
under the very mechanism meant to prevent one. ``true`` scopes the setting to the
transaction, which is also why it has to be re-applied per transaction rather than once
per connection.

**The transaction must already be open.** Under transaction pooling a statement issued
outside an explicit transaction gets its own implicit one, so a setting applied there is
discarded before the queries it was meant to govern. This module refuses to apply context
to a session with no open transaction instead of appearing to succeed.

**Unsupported dialects are detected, not caught.** The existing call site wraps its
``SET LOCAL`` in ``except Exception`` and logs at ``debug``, which silences a genuine
PostgreSQL failure exactly as readily as it silences SQLite's lack of ``set_config``. Here
the dialect is checked up front and a real PostgreSQL error is allowed to propagate.
"""

import logging
from contextlib import contextmanager
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: The setting name the RLS policies read. Defined once so the policies in
#: implement_rls_policies_20260817.py and this module cannot drift apart. Changing it
#: here without reissuing every policy silently disables tenant filtering.
TENANT_SETTING_NAME = "app.current_tenant_id"

#: set_config's third argument is is_local. See the module docstring - this is the
#: difference between a transaction-scoped setting and one that leaks across a pgbouncer
#: connection handover.
_SET_TENANT_SQL = text(
    f"SELECT set_config('{TENANT_SETTING_NAME}', :tenant_id, true)"
)

_READ_TENANT_SQL = text(
    f"SELECT current_setting('{TENANT_SETTING_NAME}', true)"
)


class TenantContextError(RuntimeError):
    """Base class for refusals raised by this module."""


class TenantContextUnavailable(TenantContextError):
    """No usable tenant identity was supplied.

    Raised instead of installing an empty or malformed value. An empty setting is
    materially worse than no setting: ``current_setting(name, true)`` returns ``''``
    rather than NULL once something has written an empty string, so a later reader
    cannot distinguish "this tenant has no rows" from "nobody established a tenant".
    """


class TenantContextNotTransactional(TenantContextError):
    """Asked to apply tenant context to a session with no open transaction.

    Under ``pool_mode=transaction`` the setting would land in its own short-lived
    implicit transaction and be gone before the caller's queries ran. Refusing is the
    only honest option, because the alternative is a session that looks tenant-scoped
    and is not.
    """


def _canonical_tenant_id(raw: Any) -> str:
    """The tenant id as the RLS policies will compare it, or a refusal.

    Normalising through ``UUID`` is not merely validation. Seven of the nine policies
    compare a ``varchar(36)`` column to a ``text`` setting, which is a plain string
    comparison: '7B3F...' and '7b3f...' are different strings, and a tenant whose id
    arrived upper-cased would match none of its own rows while appearing perfectly
    authenticated. ``str(UUID(x))`` yields the canonical lower-case hyphenated form that
    the stored values use.
    """
    if raw is None:
        raise TenantContextUnavailable(
            "No tenant id supplied. Tenant context cannot be established, so the "
            "session must not be used for tenant-scoped queries."
        )

    candidate = str(raw).strip()
    if not candidate:
        raise TenantContextUnavailable(
            "Tenant id is empty after stripping whitespace. Refusing to install an "
            "empty tenant context (see TenantContextUnavailable)."
        )

    try:
        return str(UUID(candidate))
    except (ValueError, AttributeError, TypeError) as exc:
        # The id is never interpolated into SQL - it binds as a parameter - so this is
        # not an injection guard. It is a correctness guard: a non-UUID value would be
        # installed happily and then match no row, turning a malformed identity into an
        # empty result set rather than an error.
        raise TenantContextUnavailable(
            f"Tenant id is not a UUID, so it cannot match any tenant_id value: {exc}"
        ) from exc


def _dialect_name(session: Any) -> Optional[str]:
    """The bound dialect's name, or ``None`` if it cannot be determined."""
    bind = getattr(session, "bind", None)
    if bind is None:
        get_bind = getattr(session, "get_bind", None)
        if callable(get_bind):
            try:
                bind = get_bind()
            except Exception:  # pragma: no cover - defensive
                return None
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", None)


def _in_transaction(session: Any) -> bool:
    """Whether the session currently has an open transaction.

    ``Session.in_transaction()`` is SQLAlchemy 1.4+. A session object that does not
    expose it is treated as transactional rather than refused, so this helper cannot
    become the reason a legitimate caller fails.
    """
    probe = getattr(session, "in_transaction", None)
    if not callable(probe):
        return True
    try:
        return bool(probe())
    except Exception:  # pragma: no cover - defensive
        return True


def apply_tenant_context(session: Any, tenant_id: Any) -> bool:
    """Install ``tenant_id`` as the session's tenant for the current transaction.

    Returns ``True`` when the setting was applied, and ``False`` when the bound dialect
    has no ``set_config`` - SQLite, which the default test lane runs on. The boolean is
    returned rather than swallowed so a caller that needs enforcement can assert on it;
    a PostgreSQL failure is never converted into ``False``, it propagates.

    Raises:
        TenantContextUnavailable: the id is absent, empty, or not a UUID. Raised before
            any statement is issued.
        TenantContextNotTransactional: the session has no open transaction, so a
            transaction-scoped setting would not survive to cover the caller's queries.
    """
    canonical = _canonical_tenant_id(tenant_id)

    dialect = _dialect_name(session)
    if dialect != "postgresql":
        # Checked, not caught: an `except Exception` here would hide a real PostgreSQL
        # error just as effectively as it hides SQLite's missing function, which is the
        # defect in the existing SET LOCAL call sites.
        logger.warning(
            "Tenant context not applied: dialect %r has no set_config(). Row-level "
            "security is a PostgreSQL mechanism and is NOT in effect on this session.",
            dialect,
        )
        return False

    if not _in_transaction(session):
        raise TenantContextNotTransactional(
            "apply_tenant_context requires an already-open transaction. Under "
            "pool_mode=transaction a transaction-scoped setting applied outside one is "
            "discarded before the caller's queries run. Open the transaction first, or "
            "use tenant_db_session()."
        )

    session.execute(_SET_TENANT_SQL, {"tenant_id": canonical})
    logger.debug("Tenant context installed for the current transaction.")
    return True


def read_tenant_context(session: Any) -> Optional[str]:
    """The tenant currently installed, or ``None`` if none is.

    ``current_setting(name, true)`` returns NULL when the setting was never written in
    this session, which is the state every request path is in today. Intended for
    verification and for tests, not for request handling.
    """
    if _dialect_name(session) != "postgresql":
        return None
    value = session.execute(_READ_TENANT_SQL).scalar()
    return value or None


@contextmanager
def tenant_db_session(tenant_id: Any):
    """A session bound to one tenant for the life of one transaction.

    For workers, background jobs and anything else off the request path - the places the
    audit found carrying no tenant context at all. The transaction is opened before the
    setting is applied, then committed on clean exit and rolled back on exception.

    Not a substitute for ``get_db``: this commits when the block exits normally, so a
    caller that wants to manage its own commit boundaries should open the transaction
    itself and call :func:`apply_tenant_context` directly.
    """
    canonical = _canonical_tenant_id(tenant_id)

    from backend_app.core.database_pool import db_pool

    session = db_pool.get_session()
    try:
        with session.begin():
            apply_tenant_context(session, canonical)
            yield session
    finally:
        session.close()


def get_tenant_db(user: dict):
    """FastAPI dependency: a tenant-scoped session for the authenticated caller.

    Deliberately additive. ``get_db`` is left exactly as it is so that adopting this is
    a per-router decision rather than a change to every handler at once, and so that
    nothing currently working can be broken by this module landing.

    Two differences from ``get_db`` a caller must know about:

    * The session arrives with an open transaction, and that transaction is **committed**
      when the request completes normally. ``get_db`` never commits. A handler that
      already calls ``db.commit()`` is fine - the outer block finds nothing left to do -
      but a handler relying on "no commit means no write" would change behaviour.
    * The tenant is taken from the verified JWT, never from the request body or a query
      parameter. This matches what the routers already do: ``routers/orders.py`` and
      ``routers/portfolio.py`` both derive ``tenant_id`` as ``UUID(user["id"])``.

    Take the dependency from :func:`tenant_db_dependency`, which resolves
    ``get_current_user`` for you::

        from fastapi import Depends
        from backend_app.core.tenant_db_context import tenant_db_dependency

        TenantDB = tenant_db_dependency()

        @router.get("/positions")
        async def list_positions(db=Depends(TenantDB)): ...

    Call ``tenant_db_dependency()`` once at module scope, not inside the signature, so
    FastAPI sees one stable dependency object per route.
    """
    tenant_id = _canonical_tenant_id((user or {}).get("id"))

    from backend_app.core.database_pool import db_pool

    session = db_pool.get_session()
    try:
        with session.begin():
            apply_tenant_context(session, tenant_id)
            yield session
    finally:
        session.close()


def tenant_db_dependency():
    """``Depends``-ready wrapper around :func:`get_tenant_db`.

    Built lazily so importing this module does not require FastAPI to be installed -
    the pure-Python helpers above are unit-testable without it.
    """
    from fastapi import Depends

    from backend_app.core.dependencies import get_current_user

    def _dependency(user: dict = Depends(get_current_user)):
        yield from get_tenant_db(user)

    return _dependency
