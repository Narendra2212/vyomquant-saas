"""SEC-07 regression: a signed-in user must not be able to change platform-controlled
``public.profiles`` columns (tier, limits, freeze flag, balance, ...) via the browser role.

Runs against a real PostgreSQL provisioned from the repo
(``scripts/provision_database.py --apply --create-substrate``); set
``AERORA_TEST_DATABASE_URL``. Every test runs in a transaction that is rolled back.
"""
from __future__ import annotations

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL provisioned from the repo (RLS + trigger behaviour)",
)

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402


@pytest.fixture()
def conn():
    eng = create_engine(TEST_DATABASE_URL, future=True)
    c = eng.connect()
    trans = c.begin()
    try:
        yield c
    finally:
        trans.rollback()
        c.close()
        eng.dispose()


def _seed(c):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    for uid in (a, b):
        c.execute(text("INSERT INTO auth.users (id) VALUES (:i)"), {"i": uid})
        c.execute(
            text(
                "INSERT INTO public.profiles (id, username, subscription_tier, is_frozen, max_api_slots) "
                "VALUES (:i, :u, 'free', false, 1) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": uid, "u": f"u_{uid[:8]}"},
        )
    return a, b


def _as_user(c, uid, role="authenticated"):
    c.execute(text(f"SET LOCAL ROLE {role}"))
    c.execute(text("SELECT set_config('request.jwt.claim.sub', :u, true)"), {"u": uid})


def _expect_denied(c, sql, params):
    sp = c.begin_nested()
    with pytest.raises(DBAPIError) as ei:
        c.execute(text(sql), params)
    sp.rollback()
    assert "42501" in str(ei.value.orig.pgcode) or "permission denied" in str(ei.value).lower() \
        or "platform-controlled" in str(ei.value) or "not permitted" in str(ei.value)


@pytest.mark.parametrize(
    "assignment",
    [
        "subscription_tier = 'elite_1999'",
        "max_api_slots = 99",
        "is_frozen = false",
        "balance = 1000000",
        "plan_limit_overrides = '{\"bots\": 1000}'::jsonb",
        "available_discounts = 50",
        "billing_currency = 'INR'",
        "referred_by_user_id = :b",
    ],
)
def test_user_cannot_change_platform_columns_on_own_row(conn, assignment):
    a, b = _seed(conn)
    conn.execute(text("UPDATE public.profiles SET is_frozen = true WHERE id = :i"), {"i": a})
    _as_user(conn, a)
    _expect_denied(conn, f"UPDATE public.profiles SET {assignment} WHERE id = :i", {"i": a, "b": b})


def test_user_can_still_edit_their_own_profile_fields(conn):
    a, _ = _seed(conn)
    _as_user(conn, a)
    n = conn.execute(
        text(
            "UPDATE public.profiles SET display_name='New', bio='hi', avatar_url='x', "
            "telegram_id='t', full_name='F', preferred_currency='INR', username=:u WHERE id=:i"
        ),
        {"i": a, "u": f"renamed_{a[:6]}"},
    ).rowcount
    assert n == 1


def test_user_cannot_modify_another_users_row(conn):
    a, b = _seed(conn)
    _as_user(conn, a)
    n = conn.execute(text("UPDATE public.profiles SET display_name='pwn' WHERE id=:i"), {"i": b}).rowcount
    assert n == 0  # RLS hides the row


def test_browser_roles_cannot_insert_or_delete_profiles(conn):
    a, _ = _seed(conn)
    _as_user(conn, a)
    _expect_denied(conn, "DELETE FROM public.profiles WHERE id = :i", {"i": a})
    _expect_denied(
        conn,
        "INSERT INTO public.profiles (id, subscription_tier) VALUES (:i, 'elite_1999')",
        {"i": str(uuid.uuid4())},
    )


def test_anon_cannot_update_profiles(conn):
    a, _ = _seed(conn)
    _as_user(conn, a, role="anon")
    n = 0
    try:
        sp = conn.begin_nested()
        n = conn.execute(text("UPDATE public.profiles SET subscription_tier='elite_1999' WHERE id=:i"), {"i": a}).rowcount
        sp.rollback()
    except DBAPIError:
        pass
    assert n == 0


def test_service_role_can_still_change_platform_columns(conn):
    a, _ = _seed(conn)
    conn.execute(text("SET LOCAL ROLE service_role"))
    n = conn.execute(
        text("UPDATE public.profiles SET subscription_tier='pro', is_frozen=true, max_api_slots=5 WHERE id=:i"),
        {"i": a},
    ).rowcount
    assert n == 1
