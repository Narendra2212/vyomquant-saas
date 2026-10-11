"""Integration tests for ``tenant_db_context`` against a real PostgreSQL server.

``tests/test_tenant_db_context.py`` asserts which statements are issued, using a
recording double. That is the right shape for most of the contract, but three claims
cannot be proven that way because they are properties of PostgreSQL rather than of our
code:

  1. ``set_config(name, value, true)`` actually installs a readable setting.
  2. The setting is **transaction-scoped**, so it is gone after commit or rollback.
  3. A later transaction on the *same connection* cannot see the earlier transaction's
     tenant.

Claim 3 is the security property the whole design rests on. ``core/database_pool.py``
forces ``pgbouncer=true&pool_mode=transaction`` onto the Supabase URL, so a backend
connection is returned to the pool when its transaction ends and handed to a different
request - belonging to a different tenant - next. If the setting outlived its
transaction, that next request would start with the previous tenant installed and the
RLS policies would authorise it. A mock cannot tell us whether that happens; only
PostgreSQL can.

Gated on ``AERORA_TEST_DATABASE_URL``, the deliberate opt-in in ``tests/conftest.py``
(lines 37-41). Unset, these skip and the SQLite lane is untouched. The
``database-tests`` job in ``.github/workflows/01-pr-check.yml`` sets it to a local
service container.

No application schema is required. These exercise the setting mechanism itself, using a
temporary table created and dropped inside the test, so they cannot be broken by
migration drift and cannot touch real rows.
"""

import os
import uuid

import pytest

from backend_app.core.tenant_db_context import (
    TENANT_SETTING_NAME,
    TenantContextNotTransactional,
    apply_tenant_context,
    read_tenant_context,
)

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason=(
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL. These tests verify "
        "PostgreSQL setting semantics and have nothing to assert without a server."
    ),
)

TENANT_A = "3f2b8c14-7d6e-4a19-9b05-2c8e1f4a6d73"
TENANT_B = "9a1e4c77-0b2d-4f58-8e31-6d4a2b9c0f15"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine):
    """A session whose transaction is always rolled back.

    Mirrors the application's factory - ``autocommit=False, autoflush=False``, matching
    ``DatabasePool.initialize`` - so transaction behaviour here is the behaviour the app
    gets, not a more forgiving variant.
    """
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()


# ── Claim 1: the setting is really installed ───────────────────────────────────


def test_apply_tenant_context_installs_a_readable_setting(session):
    session.begin()

    assert apply_tenant_context(session, TENANT_A) is True
    assert read_tenant_context(session) == TENANT_A


def test_setting_is_unset_before_anything_installs_it(session):
    """The state every request path is in today.

    ``current_setting(name, true)`` returns NULL rather than raising, which is what
    makes the policy comparison yield NULL instead of TRUE - and therefore what makes an
    enforced policy match zero rows rather than every row.
    """
    session.begin()

    assert read_tenant_context(session) is None


def test_uncast_text_comparison_matches_the_installed_tenant(session):
    """The predicate seven of the nine policies actually use.

    Those tables hold ``tenant_id varchar(36)`` and compare it to the setting with no
    cast. This asserts that ``varchar = text`` against ``current_setting`` both runs and
    selects correctly - the thing I could not confirm from source, and the reason the
    'missing cast' reading of those policies was wrong.
    """
    session.begin()
    session.execute(
        text(
            "CREATE TEMPORARY TABLE tenant_probe "
            "(tenant_id varchar(36), payload text) ON COMMIT DROP"
        )
    )
    session.execute(
        text("INSERT INTO tenant_probe VALUES (:a, 'a-row'), (:b, 'b-row')"),
        {"a": TENANT_A, "b": TENANT_B},
    )

    apply_tenant_context(session, TENANT_A)

    rows = session.execute(
        text(
            "SELECT payload FROM tenant_probe "
            f"WHERE tenant_id = current_setting('{TENANT_SETTING_NAME}', true)"
        )
    ).scalars().all()

    assert rows == ["a-row"]


def test_uuid_cast_comparison_matches_the_installed_tenant(session):
    """The predicate the other two policies use.

    ``execution_records`` and ``reconciliation_mismatches`` hold ``tenant_id uuid`` and
    cast with ``::text``. Both predicate forms are therefore exercised here, which is
    what establishes that the existing policies are type-correct as written.
    """
    session.begin()
    session.execute(
        text(
            "CREATE TEMPORARY TABLE tenant_probe_uuid "
            "(tenant_id uuid, payload text) ON COMMIT DROP"
        )
    )
    session.execute(
        text("INSERT INTO tenant_probe_uuid VALUES (:a, 'a-row'), (:b, 'b-row')"),
        {"a": TENANT_A, "b": TENANT_B},
    )

    apply_tenant_context(session, TENANT_B)

    rows = session.execute(
        text(
            "SELECT payload FROM tenant_probe_uuid "
            f"WHERE tenant_id::text = current_setting('{TENANT_SETTING_NAME}', true)"
        )
    ).scalars().all()

    assert rows == ["b-row"]


def test_unset_context_selects_no_rows(session):
    """Why forcing RLS before context propagation would empty reads rather than secure
    them. NULL in the comparison is not TRUE, so an enforced policy matches nothing."""
    session.begin()
    session.execute(
        text(
            "CREATE TEMPORARY TABLE tenant_probe_empty "
            "(tenant_id varchar(36), payload text) ON COMMIT DROP"
        )
    )
    session.execute(
        text("INSERT INTO tenant_probe_empty VALUES (:a, 'a-row')"), {"a": TENANT_A}
    )

    rows = session.execute(
        text(
            "SELECT payload FROM tenant_probe_empty "
            f"WHERE tenant_id = current_setting('{TENANT_SETTING_NAME}', true)"
        )
    ).scalars().all()

    assert rows == []


# ── Claims 2 and 3: transaction scope, and no leak across a handover ──────────


def test_setting_does_not_survive_commit(session):
    """``is_local=true`` is what makes this true. With ``false`` the setting would
    persist on the connection and be inherited by whoever checks it out next."""
    session.begin()
    apply_tenant_context(session, TENANT_A)
    assert read_tenant_context(session) == TENANT_A
    session.commit()

    session.begin()
    assert read_tenant_context(session) is None, (
        "tenant context survived COMMIT - set_config was not transaction-scoped, and "
        "the next request on this pooled connection would inherit it"
    )


def test_setting_does_not_survive_rollback(session):
    session.begin()
    apply_tenant_context(session, TENANT_A)
    assert read_tenant_context(session) == TENANT_A
    session.rollback()

    session.begin()
    assert read_tenant_context(session) is None


def test_tenant_b_transaction_cannot_see_tenant_a(session):
    """THE security property, on one connection, in sequence.

    This is the pgbouncer transaction-pooling scenario written as a test: tenant A's
    request completes, the connection is reused, tenant B's request begins. B must see
    its own tenant and never A's.
    """
    session.begin()
    apply_tenant_context(session, TENANT_A)
    assert read_tenant_context(session) == TENANT_A
    session.commit()

    session.begin()
    apply_tenant_context(session, TENANT_B)
    observed = read_tenant_context(session)
    session.commit()

    assert observed == TENANT_B
    assert observed != TENANT_A


def test_second_connection_does_not_see_the_first_tenant(engine):
    """Cross-connection, for the case where the pool hands out a different socket."""
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    first = factory()
    try:
        first.begin()
        apply_tenant_context(first, TENANT_A)
        assert read_tenant_context(first) == TENANT_A
        first.commit()
    finally:
        first.close()

    second = factory()
    try:
        second.begin()
        assert read_tenant_context(second) is None
    finally:
        second.rollback()
        second.close()


# ── Refusals, against a real server ───────────────────────────────────────────


def test_refuses_outside_a_transaction(session):
    """The refusal exists because a transaction-scoped setting applied outside a
    transaction is discarded before the caller's queries run."""
    if session.in_transaction():
        session.rollback()

    with pytest.raises(TenantContextNotTransactional):
        apply_tenant_context(session, TENANT_A)


def test_each_tenant_sees_only_its_own_rows_in_sequence(session):
    """Both tenants, one table, one connection, consecutive transactions."""
    # A session-scoped temporary table, deliberately WITHOUT `ON COMMIT DROP`: the data
    # has to outlive the first transaction for the second one to query it, which is the
    # whole point here. Still a temp table, so it is confined to this connection and
    # disappears when the session closes - no real table is created and nothing needs
    # dropping in a finally block.
    session.begin()
    session.execute(
        text("CREATE TEMPORARY TABLE tenant_seq_probe (tenant_id varchar(36), payload text)")
    )
    session.execute(
        text("INSERT INTO tenant_seq_probe VALUES (:a, 'a-row'), (:b, 'b-row')"),
        {"a": TENANT_A, "b": TENANT_B},
    )
    session.commit()

    for tenant, expected in ((TENANT_A, ["a-row"]), (TENANT_B, ["b-row"])):
        session.begin()
        apply_tenant_context(session, tenant)
        rows = session.execute(
            text(
                "SELECT payload FROM tenant_seq_probe "
                f"WHERE tenant_id = current_setting('{TENANT_SETTING_NAME}', true)"
            )
        ).scalars().all()
        session.commit()
        assert rows == expected


def test_a_tenant_with_no_rows_sees_nothing_rather_than_everything(session):
    """Fails closed, not open - the direction that matters if a tenant id is wrong."""
    session.begin()
    session.execute(
        text(
            "CREATE TEMPORARY TABLE tenant_probe_closed "
            "(tenant_id varchar(36), payload text) ON COMMIT DROP"
        )
    )
    session.execute(
        text("INSERT INTO tenant_probe_closed VALUES (:a, 'a-row')"), {"a": TENANT_A}
    )

    apply_tenant_context(session, str(uuid.uuid4()))

    rows = session.execute(
        text(
            "SELECT payload FROM tenant_probe_closed "
            f"WHERE tenant_id = current_setting('{TENANT_SETTING_NAME}', true)"
        )
    ).scalars().all()

    assert rows == []
