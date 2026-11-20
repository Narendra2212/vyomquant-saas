"""SEC-16 regression: browser roles cannot flip a deployment to live or rebind it.

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
        eng.dispose()


def _seed(c):
    uid, sid, vid, did = (str(uuid.uuid4()) for _ in range(4))
    c.execute(text("INSERT INTO auth.users (id) VALUES (:u)"), {"u": uid})
    c.execute(
        text("INSERT INTO public.strategies (id, user_id, name, symbol) VALUES (:s, :u, 'S', 'BTC/USDT')"),
        {"s": sid, "u": uid},
    )
    c.execute(
        text(
            "INSERT INTO public.strategy_versions (id, strategy_id, version, blueprint, schema_version, "
            "validation_state, lifecycle_state) VALUES (:v, :s, '1', '{}'::jsonb, '1', 'UNVALIDATED', 'DRAFT')"
        ),
        {"v": vid, "s": sid},
    )
    c.execute(
        text(
            "INSERT INTO public.strategy_deployments (id, strategy_id, user_id, version_id, version, status, mode) "
            "VALUES (:d, :s, :u, :v, '1', 'stopped', 'paper')"
        ),
        {"d": did, "s": sid, "u": uid, "v": vid},
    )
    return uid, sid, vid, did


def _as(c, uid, role="authenticated"):
    c.execute(text(f"SET LOCAL ROLE {role}"))
    c.execute(text("SELECT set_config('request.jwt.claim.sub', :u, true)"), {"u": uid})


def _denied(c, sql, params):
    sp = c.begin_nested()
    with pytest.raises(DBAPIError) as ei:
        c.execute(text(sql), params)
    sp.rollback()
    return ei.value


@pytest.mark.parametrize(
    "assignment",
    [
        "mode = 'live', exchange_account_id = gen_random_uuid()",
        "exchange_account_id = gen_random_uuid()",
        "risk_config_id = gen_random_uuid()",
        "execution_config = to_jsonb(1)",
        "dag_hash = 'forged'",
        "total_pnl = 1000000",
        "roi_pct = 999",
        "marketplace_listing_id = gen_random_uuid()",
    ],
)
def test_user_cannot_patch_binding_or_performance_columns(conn, assignment):
    uid, _, _, did = _seed(conn)
    _as(conn, uid)
    err = _denied(conn, f"UPDATE public.strategy_deployments SET {assignment} WHERE id = :d", {"d": did})
    assert getattr(err.orig, "pgcode", "") == "42501"


def test_user_can_still_write_lifecycle_columns(conn):
    uid, _, _, did = _seed(conn)
    _as(conn, uid)
    n = conn.execute(
        text(
            "UPDATE public.strategy_deployments SET status='running', started_at=now(), "
            "stopped_at=NULL, error_message=NULL WHERE id=:d"
        ),
        {"d": did},
    ).rowcount
    assert n == 1


def test_user_cannot_insert_a_live_deployment_directly(conn):
    uid, sid, vid, _ = _seed(conn)
    _as(conn, uid)
    err = _denied(
        conn,
        "INSERT INTO public.strategy_deployments (strategy_id, user_id, version_id, version, status, mode, exchange_account_id) "
        "VALUES (:s, :u, :v, '1', 'pending', 'live', gen_random_uuid())",
        {"s": sid, "u": uid, "v": vid},
    )
    assert getattr(err.orig, "pgcode", "") == "42501"


def test_user_can_insert_a_paper_deployment(conn):
    uid, sid, vid, _ = _seed(conn)
    _as(conn, uid)
    n = conn.execute(
        text(
            "INSERT INTO public.strategy_deployments (strategy_id, user_id, version_id, version, status, mode) "
            "VALUES (:s, :u, :v, '1', 'pending', 'paper')"
        ),
        {"s": sid, "u": uid, "v": vid},
    ).rowcount
    assert n == 1


def test_service_role_can_create_live_and_rebind(conn):
    uid, sid, vid, did = _seed(conn)
    conn.execute(text("SET LOCAL ROLE service_role"))
    n = conn.execute(
        text("UPDATE public.strategy_deployments SET mode='live', exchange_account_id=gen_random_uuid() WHERE id=:d"),
        {"d": did},
    ).rowcount
    assert n == 1
