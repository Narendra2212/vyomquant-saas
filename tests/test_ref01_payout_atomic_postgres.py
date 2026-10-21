"""REF-01 -- the atomic payout RPCs, proven against a real PostgreSQL server.

backend_app/migrations/049_ref01_payout_atomic.sql installs two SECURITY DEFINER
functions, `create_referral_payout_request` and `settle_referral_payout`, replacing a
debit-then-insert pair (and a settle-then-recredit pair) that each used to be two
separate PostgREST statements with a best-effort compensating action on failure.

WHAT THIS FILE PROVES, AND WHY IT NEEDS A REAL DATABASE
--------------------------------------------------------
The property under test is a TRANSACTIONAL one -- "if the second half of a two-part
write fails, the first half is not left standing" -- which no mock of
`supabase-py`/`postgrest` can demonstrate, because the mock has no transaction to roll
back. Every assertion here reads a row back over an INDEPENDENT connection (never the
one that wrote it), the same oracle discipline `tests/test_money06_referral_obligations_
postgres.py` and `tests/test_mkt01_entitlement_tables_postgres.py` already use, so "it
was recorded" means committed and visible elsewhere, not merely staged in a session.

Gated on AERORA_TEST_DATABASE_URL naming PostgreSQL, the same mechanism every other
``*_postgres.py`` file in this directory uses. A SKIP here is NOT a pass -- the
atomicity this migration exists to provide is unverified until this runs for real.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
        "here is NOT a pass -- REF-01's atomicity is unverified."
    ),
)


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


@pytest.fixture
def referral_user():
    """One user with a referral_wallets row, seeded with a known approved balance."""
    user_id = str(uuid.uuid4())
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (user_id, f"ref01-pg-{user_id[:8]}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user_id,),
            )
            # A trigger may already have created a wallet for this profile; force
            # the known opening balance regardless of which path created it.
            cur.execute(
                "INSERT INTO public.referral_wallets (user_id, approved_balance_usd) "
                "VALUES (%s, 100.00) "
                "ON CONFLICT (user_id) DO UPDATE SET approved_balance_usd = 100.00",
                (user_id,),
            )
    finally:
        conn.close()

    yield user_id

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_payouts WHERE user_id = %s", (user_id,)
            )
            cur.execute(
                "DELETE FROM public.referral_wallets WHERE user_id = %s", (user_id,)
            )
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (user_id,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (user_id,))
    finally:
        conn.close()


def _wallet_balance(user_id: str) -> Decimal:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT approved_balance_usd FROM public.referral_wallets "
                "WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _payout_rows(user_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, amount_usd, payout_method, processed_at, "
                "payout_details FROM public.referral_payouts WHERE user_id = %s",
                (user_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _create_payout(user_id: str, amount, method="bank_transfer", details=None):
    conn = _conn(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.create_referral_payout_request(%s, %s, %s, %s::jsonb)",
                (user_id, amount, method, __import__("json").dumps(details or {})),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _settle_payout(payout_id: str, status: str, reference=None, reason=None):
    conn = _conn(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.settle_referral_payout(%s, %s, %s, %s)",
                (payout_id, status, reference, reason),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  PART 1 — THE FUNCTIONS AND THEIR GUARANTEES EXIST
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_both_functions_exist_exactly_once_and_are_service_and_authenticated_only():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns "
                "ON ns.oid = p.pronamespace WHERE ns.nspname='public' "
                "AND p.proname='create_referral_payout_request'"
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns "
                "ON ns.oid = p.pronamespace WHERE ns.nspname='public' "
                "AND p.proname='settle_referral_payout'"
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT has_function_privilege('anon', "
                "'public.create_referral_payout_request(uuid,numeric,text,jsonb)', "
                "'EXECUTE')"
            )
            assert cur.fetchone()[0] is False

            cur.execute(
                "SELECT has_function_privilege('authenticated', "
                "'public.settle_referral_payout(uuid,text,text,text)', 'EXECUTE')"
            )
            assert cur.fetchone()[0] is False, (
                "settle_referral_payout bypasses RLS for ANY user's payout and must "
                "not be callable by a browser role"
            )
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  PART 2 — create_referral_payout_request
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_a_successful_request_debits_and_inserts_together(referral_user):
    user_id = referral_user
    payout = _create_payout(user_id, 40.00)

    assert payout["status"] == "pending"
    assert _wallet_balance(user_id) == Decimal("60.00")
    rows = _payout_rows(user_id)
    assert len(rows) == 1
    assert rows[0][1] == "pending"
    assert str(rows[0][2]) == "40.00"


@pg_only
def test_insufficient_balance_raises_rf001_and_writes_nothing(referral_user):
    user_id = referral_user
    import psycopg2

    with pytest.raises(psycopg2.Error) as exc_info:
        _create_payout(user_id, 500.00)
    assert exc_info.value.pgcode == "RF001"

    assert _wallet_balance(user_id) == Decimal("100.00"), (
        "a refused payout request must leave the wallet completely untouched"
    )
    assert _payout_rows(user_id) == []


@pg_only
def test_no_wallet_row_raises_rf002(referral_user):
    import psycopg2

    user_id = referral_user
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_wallets WHERE user_id = %s", (user_id,)
            )
    finally:
        conn.close()

    with pytest.raises(psycopg2.Error) as exc_info:
        _create_payout(user_id, 10.00)
    assert exc_info.value.pgcode == "RF002"


@pg_only
def test_concurrent_requests_for_the_same_wallet_cannot_both_succeed(referral_user):
    """THE ATOMICITY PROPERTY, UNDER REAL CONCURRENCY.

    Two threads each request a payout for more than half the wallet's balance.
    `SELECT ... FOR UPDATE` serialises them; at most one may succeed, and the
    wallet never goes negative and never double-counts.
    """
    import threading

    user_id = referral_user
    results = {}

    def _attempt(name):
        try:
            results[name] = ("ok", _create_payout(user_id, 60.00))
        except Exception as exc:  # noqa: BLE001
            results[name] = ("error", getattr(exc, "pgcode", str(exc)))

    t1 = threading.Thread(target=_attempt, args=("a",))
    t2 = threading.Thread(target=_attempt, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    outcomes = [v[0] for v in results.values()]
    assert outcomes.count("ok") == 1, (
        f"exactly one of two concurrent $60 requests against a $100 wallet should "
        f"succeed; got {results}"
    )
    assert outcomes.count("error") == 1
    assert results[[k for k, v in results.items() if v[0] == "error"][0]][1] == "RF001"

    assert _wallet_balance(user_id) == Decimal("40.00")
    assert len(_payout_rows(user_id)) == 1


# ══════════════════════════════════════════════════════════════════════════
#  PART 3 — settle_referral_payout
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_completing_records_reference_and_does_not_touch_the_wallet(referral_user):
    user_id = referral_user
    payout = _create_payout(user_id, 25.00)
    balance_after_debit = _wallet_balance(user_id)

    settled = _settle_payout(payout["id"], "completed", reference="TXN-ABC-999")

    assert settled["status"] == "completed"
    assert settled["payout_details"]["settlement_reference"] == "TXN-ABC-999"
    assert settled["processed_at"] is not None
    assert _wallet_balance(user_id) == balance_after_debit


@pg_only
def test_completing_without_a_reference_raises_rf003_and_changes_nothing(referral_user):
    import psycopg2

    user_id = referral_user
    payout = _create_payout(user_id, 25.00)

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(payout["id"], "completed", reference=None)
    assert exc_info.value.pgcode == "RF003"

    rows = _payout_rows(user_id)
    assert rows[0][1] == "pending", "a refused settlement must not mutate the payout"


@pg_only
def test_rejecting_recredits_the_wallet_by_exactly_the_payout_amount(referral_user):
    user_id = referral_user
    payout = _create_payout(user_id, 30.00)
    balance_after_debit = _wallet_balance(user_id)

    settled = _settle_payout(payout["id"], "rejected", reason="bad bank details")

    assert settled["status"] == "rejected"
    assert settled["payout_details"]["rejection_reason"] == "bad bank details"
    assert _wallet_balance(user_id) == balance_after_debit + Decimal("30.00")
    assert _wallet_balance(user_id) == Decimal("100.00")


@pg_only
@pytest.mark.parametrize("terminal_status,reference,reason", [
    ("completed", "TXN-1", None),
    ("rejected", None, "first reason"),
])
def test_an_already_terminal_payout_cannot_be_settled_again(
    referral_user, terminal_status, reference, reason
):
    import psycopg2

    user_id = referral_user
    payout = _create_payout(user_id, 15.00)
    _settle_payout(payout["id"], terminal_status, reference=reference, reason=reason)
    balance_after_first_settle = _wallet_balance(user_id)

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(payout["id"], "completed", reference="TXN-SECOND")
    assert exc_info.value.pgcode == "RF004"

    assert _wallet_balance(user_id) == balance_after_first_settle, (
        "a refused re-settlement must not move the wallet a second time"
    )


@pg_only
def test_rejecting_with_no_wallet_row_raises_rf002_and_leaves_the_payout_pending(
    referral_user,
):
    """THE ATOMICITY PROPERTY FOR THE REJECTION PATH.

    If the wallet row vanishes between request and settlement (an edge case, but
    exactly the one the OLD non-atomic code could only log CRITICAL about), the
    whole settlement must roll back -- the payout must stay 'pending', re-settleable
    once the wallet is reconciled, rather than becoming 'rejected' with the money
    nowhere.
    """
    import psycopg2

    user_id = referral_user
    payout = _create_payout(user_id, 10.00)

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_wallets WHERE user_id = %s", (user_id,)
            )
    finally:
        conn.close()

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(payout["id"], "rejected", reason="no wallet")
    assert exc_info.value.pgcode == "RF002"

    rows = _payout_rows(user_id)
    assert rows[0][1] == "pending", (
        "the status flip must have rolled back together with the failed re-credit, "
        "not been left standing as 'rejected' with no money returned"
    )
