"""Tests for ``backend_app.core.tenant_db_context``.

The properties under test are the ones that decide whether RLS can safely be forced
later, so each is asserted directly rather than inferred:

  * the setting is written with ``set_config(..., true)`` - transaction-scoped, so it
    cannot survive a pgbouncer connection handover into another tenant's request;
  * the tenant id travels as a bound parameter, never interpolated into SQL text;
  * the id is canonicalised, because the policies compare strings and a differently
    cased UUID would match none of its owner's rows;
  * a missing, empty or malformed id is refused *before* any statement is issued;
  * a session with no open transaction is refused, because the setting would be
    discarded before the caller's queries ran;
  * a non-PostgreSQL dialect is detected up front and reported, while a genuine
    PostgreSQL error propagates instead of being swallowed.

No database is required. A recording double stands in for the session, following the
pattern in ``tests/test_migration_tooling.py`` - the assertions are about which
statements are issued and with what parameters, which does not need a socket.
"""

import pytest

from backend_app.core.tenant_db_context import (
    TENANT_SETTING_NAME,
    TenantContextNotTransactional,
    TenantContextUnavailable,
    apply_tenant_context,
    read_tenant_context,
)

TENANT_A = "3f2b8c14-7d6e-4a19-9b05-2c8e1f4a6d73"


class _FakeDialect:
    def __init__(self, name):
        self.name = name


class _FakeBind:
    def __init__(self, dialect_name):
        self.dialect = _FakeDialect(dialect_name)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RecordingSession:
    """A SQLAlchemy ``Session``-shaped double that records statements.

    ``statements`` accumulates ``(sql_text, params)`` for every execute, so a test can
    assert exactly what was issued - including that nothing was issued at all, which is
    the assertion for every fail-closed case.
    """

    def __init__(
        self,
        dialect_name="postgresql",
        in_transaction=True,
        raise_on_execute=None,
        scalar_value=None,
    ):
        self.bind = _FakeBind(dialect_name)
        self.statements = []
        self._in_transaction = in_transaction
        self._raise_on_execute = raise_on_execute
        self._scalar_value = scalar_value

    def in_transaction(self):
        return self._in_transaction

    def execute(self, clause, params=None):
        self.statements.append((str(clause), params))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        return _FakeResult(self._scalar_value)


# ── The setting is written correctly ───────────────────────────────────────────


def test_uses_set_config_with_is_local_true():
    """Transaction-scoped, not session-scoped.

    A session-scoped setting would persist on the pooled connection after the
    transaction ends and be inherited by the next request to check that connection
    out - the exact cross-tenant leak RLS is meant to prevent.
    """
    session = _RecordingSession()

    assert apply_tenant_context(session, TENANT_A) is True
    assert len(session.statements) == 1

    sql, _ = session.statements[0]
    assert "set_config" in sql
    assert TENANT_SETTING_NAME in sql
    assert sql.rstrip().rstrip(")").rstrip().endswith("true"), (
        f"is_local must be true; got {sql!r}"
    )
    assert "SET LOCAL" not in sql.upper()


def test_tenant_id_is_a_bound_parameter_not_interpolated():
    """``SET`` takes no bind parameters; ``set_config`` does.

    The existing SET LOCAL call sites rely on psycopg2 interpolating client-side, which
    makes the value's handling a driver detail. Binding it keeps the id out of the SQL
    text entirely.
    """
    session = _RecordingSession()
    apply_tenant_context(session, TENANT_A)

    sql, params = session.statements[0]
    assert TENANT_A not in sql, "tenant id must not appear in the statement text"
    assert ":tenant_id" in sql
    assert params == {"tenant_id": TENANT_A}


def test_tenant_id_is_canonicalised_to_lowercase():
    """Seven of the nine policies do a plain string comparison.

    ``varchar(36) = text`` means '3F2B...' and '3f2b...' are different values. An
    upper-cased id would install cleanly and then match none of its owner's rows.
    """
    session = _RecordingSession()
    apply_tenant_context(session, TENANT_A.upper())

    _, params = session.statements[0]
    assert params == {"tenant_id": TENANT_A}


def test_accepts_uuid_objects_and_normalises_them():
    from uuid import UUID

    session = _RecordingSession()
    apply_tenant_context(session, UUID(TENANT_A))

    _, params = session.statements[0]
    assert params == {"tenant_id": TENANT_A}


# ── Fail closed, before issuing anything ───────────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [None, "", "   ", "not-a-uuid", "12345", "3f2b8c14-7d6e-4a19-9b05", 0],
    ids=["none", "empty", "whitespace", "word", "digits", "truncated-uuid", "zero"],
)
def test_refuses_unusable_tenant_id_without_issuing_a_statement(bad_id):
    """An empty setting is worse than no setting.

    ``current_setting(name, true)`` returns NULL only while nothing has written the
    setting. Once an empty string is written it returns ``''``, and a later reader can
    no longer tell "this tenant owns no rows" from "no tenant was ever established".
    """
    session = _RecordingSession()

    with pytest.raises(TenantContextUnavailable):
        apply_tenant_context(session, bad_id)

    assert session.statements == [], "must refuse before touching the session"


def test_refuses_when_no_transaction_is_open():
    """Under pool_mode=transaction the setting would not survive to the queries.

    ``core/database_pool.py`` forces ``pgbouncer=true&pool_mode=transaction`` onto the
    Supabase URL, so a statement issued outside an explicit transaction gets its own
    implicit one and anything transaction-scoped dies with it.
    """
    session = _RecordingSession(in_transaction=False)

    with pytest.raises(TenantContextNotTransactional):
        apply_tenant_context(session, TENANT_A)

    assert session.statements == []


# ── Dialect handling: detected, not caught ─────────────────────────────────────


def test_sqlite_reports_false_rather_than_pretending():
    """The default test lane runs on SQLite, which has no set_config.

    Returning False keeps the module usable there while making the absence of
    enforcement assertable, instead of logging at debug and moving on.
    """
    session = _RecordingSession(dialect_name="sqlite")

    assert apply_tenant_context(session, TENANT_A) is False
    assert session.statements == []


def test_postgres_errors_propagate_and_are_not_converted_to_false():
    """The defect in the existing call sites is `except Exception` around SET LOCAL.

    It silences a real PostgreSQL failure exactly as readily as it silences SQLite's
    missing function, so a session that failed to get tenant context looks identical to
    one that never needed it.
    """
    boom = RuntimeError("permission denied for function set_config")
    session = _RecordingSession(raise_on_execute=boom)

    with pytest.raises(RuntimeError, match="set_config"):
        apply_tenant_context(session, TENANT_A)


def test_refusal_precedes_dialect_check():
    """A malformed id on a SQLite session is still a refusal, not a quiet False."""
    session = _RecordingSession(dialect_name="sqlite")

    with pytest.raises(TenantContextUnavailable):
        apply_tenant_context(session, "not-a-uuid")


# ── Reading the setting back ───────────────────────────────────────────────────


def test_read_tenant_context_returns_none_when_unset():
    """This is the state every request path is in today.

    ``current_setting(name, true)`` yields NULL when nothing wrote the setting, the
    policy comparison then yields NULL rather than TRUE, and an enforced policy matches
    zero rows - which is why forcing RLS before context propagation lands would empty
    out reads rather than secure them.
    """
    session = _RecordingSession(scalar_value=None)
    assert read_tenant_context(session) is None


def test_read_tenant_context_returns_the_installed_tenant():
    session = _RecordingSession(scalar_value=TENANT_A)
    assert read_tenant_context(session) == TENANT_A


def test_read_tenant_context_treats_empty_string_as_unset():
    session = _RecordingSession(scalar_value="")
    assert read_tenant_context(session) is None


def test_read_tenant_context_is_none_on_sqlite_without_querying():
    session = _RecordingSession(dialect_name="sqlite")
    assert read_tenant_context(session) is None
    assert session.statements == []


# ── The setting name is shared with the policies ───────────────────────────────


def test_setting_name_matches_the_rls_policies():
    """If these drift apart, tenant filtering silently stops happening.

    The policies in ``alembic/versions/implement_rls_policies_20260817.py`` read
    ``app.current_tenant_id``. A policy comparing against a setting nobody writes
    matches no rows; a setting nobody reads filters nothing.
    """
    assert TENANT_SETTING_NAME == "app.current_tenant_id"
