"""SEC-DEF-01: SECURITY DEFINER functions cannot be driven by the anon key or for another user.

Found 2026-11-19 in the final launch certification. Production let an UNAUTHENTICATED request
(only the public anon key) run ``increment_ml_addon`` for any account -- reproduced on the QA
account and reverted -- and any signed-in user run ``create_referral_payout_request`` and
``paper_apply_fill_atomic`` for ANOTHER user. Migration 078 revokes the service-role-only
primitives from browser roles and puts a caller check in front of the user-facing ones.

Runs against the schema the repository provisions (``scripts/provision_database.py``), where
identities are adopted through the ``request.jwt.claim.*`` settings the substrate reads.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL instance: roles, grants and SECURITY DEFINER do not exist in SQLite.",
)

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

SERVICE_ONLY = (
    "public.increment_ml_addon(uuid)",
    "public.execute_secure_v2(uuid, numeric)",
    "public.execute_secure_v3(uuid, numeric, uuid)",
    "public.reverse_referral_commission(character varying, text)",
)

PAPER_SIG = (
    "uuid, uuid, uuid, uuid, text, bigint, text, uuid, "
    "jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb"
)
GUARDED = {
    "create_referral_code_for_user": "uuid",
    "create_referral_payout_request": "uuid, numeric, text, jsonb",
    "paper_apply_fill_atomic": PAPER_SIG,
}


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _exists(conn, signature):
    return conn.execute(text("SELECT to_regprocedure(:s) IS NOT NULL"), {"s": signature}).scalar()


def _can(conn, role, signature):
    return conn.execute(
        text("SELECT has_function_privilege(:r, :s, 'EXECUTE')"), {"r": role, "s": signature}
    ).scalar()


def test_service_only_primitives_are_not_executable_by_browser_roles(engine):
    with engine.connect() as conn:
        present = [s for s in SERVICE_ONLY if _exists(conn, s)]
        assert present, "none of the service-only primitives is provisioned; this test checks nothing"
        for signature in present:
            for role in ("anon", "authenticated", "public"):
                assert not _can(conn, role, signature), f"{role} can still EXECUTE {signature}"
            assert _can(conn, "service_role", signature), f"service_role lost EXECUTE on {signature}"


def test_the_guarded_bodies_are_service_role_only_and_the_wrappers_refuse_anon(engine):
    with engine.connect() as conn:
        for name, args in GUARDED.items():
            wrapper, body = f"public.{name}({args})", f"public.{name}__impl({args})"
            assert _exists(conn, wrapper), wrapper
            assert not _can(conn, "anon", wrapper), f"anon can EXECUTE {wrapper}"
            assert _can(conn, "authenticated", wrapper), f"signed-in users lost {wrapper}"
            if _exists(conn, body):
                for role in ("anon", "authenticated"):
                    assert not _can(conn, role, body), f"{role} can call {body} past its guard"


def _call_as(conn, uid, sql, params):
    conn.execute(text("SET LOCAL ROLE authenticated"))
    conn.execute(text("SELECT set_config('request.jwt.claim.role', 'authenticated', true)"))
    conn.execute(text("SELECT set_config('request.jwt.claim.sub', :u, true)"), {"u": uid})
    return conn.execute(text(sql), params)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT public.create_referral_code_for_user(CAST(:victim AS uuid))",
        "SELECT public.create_referral_payout_request(CAST(:victim AS uuid), 10, 'upi', '{}'::jsonb)",
        "SELECT public.paper_apply_fill_atomic(CAST(:victim AS uuid), NULL, NULL, NULL, 'e', 0, 'x', NULL,"
        " '{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}')",
    ],
)
def test_a_signed_in_user_cannot_act_for_another_user(engine, sql):
    attacker, victim = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(sa.exc.DBAPIError) as exc:
                _call_as(conn, attacker, sql, {"victim": victim})
            assert "SEC-DEF-01" in str(exc.value)
        finally:
            trans.rollback()
