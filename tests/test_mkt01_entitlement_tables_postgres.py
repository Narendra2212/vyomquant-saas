"""MKT-01 regression: browser roles cannot write library_subscriptions / deployment_permissions.

Real-PostgreSQL test (``AERORA_TEST_DATABASE_URL``); every test rolls back.
"""
from __future__ import annotations

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL provisioned from the repo",
)
sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402


@pytest.fixture()
def conn():
    eng = create_engine(TEST_DATABASE_URL, future=True)
    c = eng.connect()
    t = c.begin()
    try:
        yield c
    finally:
        t.rollback()
        c.close()


def _as(conn, role):
    conn.execute(text(f"SET LOCAL ROLE {role}"))


def _reset(conn):
    conn.execute(text("RESET ROLE"))


def _denied(conn, role, sql, params=None):
    sp = conn.begin_nested()
    _as(conn, role)
    try:
        with pytest.raises(DBAPIError) as ei:
            conn.execute(text(sql), params or {})
        assert "42501" in str(ei.value.orig) or "permission denied" in str(ei.value.orig).lower()
    finally:
        sp.rollback()


def _seed(conn):
    """A listing-less subscription row inserted as the owning role (FKs relaxed per column)."""
    conn.execute(text("SET LOCAL session_replication_role = replica"))
    sid = str(uuid.uuid4())
    conn.execute(
        text("INSERT INTO public.library_subscriptions (id, library_id, user_id, status) "
             "VALUES (:i, :l, :u, 'pending')"),
        {"i": sid, "l": str(uuid.uuid4()), "u": str(uuid.uuid4())},
    )
    # triggers must be live again for the assertions that follow
    conn.execute(text("SET LOCAL session_replication_role = origin"))
    return sid


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_browser_role_cannot_insert_an_active_subscription(conn, role):
    _denied(
        conn, role,
        "INSERT INTO public.library_subscriptions (id, library_id, user_id, status) "
        "VALUES (:i, :l, :u, 'active')",
        {"i": str(uuid.uuid4()), "l": str(uuid.uuid4()), "u": str(uuid.uuid4())},
    )


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_browser_role_cannot_update_or_delete_a_subscription(conn, role):
    sid = _seed(conn)
    _denied(conn, role, "UPDATE public.library_subscriptions SET status='active' WHERE id=:i", {"i": sid})
    _denied(conn, role, "DELETE FROM public.library_subscriptions WHERE id=:i", {"i": sid})


def test_trigger_blocks_even_when_privileges_are_regranted(conn):
    conn.execute(text("GRANT INSERT, UPDATE, DELETE ON public.library_subscriptions TO authenticated"))
    sp = conn.begin_nested()
    _as(conn, "authenticated")
    try:
        with pytest.raises(DBAPIError) as ei:
            conn.execute(
                text("INSERT INTO public.library_subscriptions (id, library_id, user_id, status) "
                     "VALUES (:i, :l, :u, 'active')"),
                {"i": str(uuid.uuid4()), "l": str(uuid.uuid4()), "u": str(uuid.uuid4())},
            )
        assert "reserved for the backend" in str(ei.value.orig)
    finally:
        sp.rollback()


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_browser_role_cannot_write_deployment_permissions(conn, role):
    _denied(conn, role, "DELETE FROM public.deployment_permissions")
    _denied(conn, role, "UPDATE public.deployment_permissions SET is_active = false")


def test_browser_role_keeps_select(conn):
    _as(conn, "authenticated")
    conn.execute(text("SELECT count(*) FROM public.library_subscriptions")).scalar()
