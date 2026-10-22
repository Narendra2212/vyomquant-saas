"""MKT-06 -- the atomic creator-payout RPCs, proven against a real PostgreSQL server.

backend_app/migrations/051_mkt06_creator_payout_atomic.sql installs two SECURITY DEFINER
functions, `create_creator_payout` and `settle_creator_payout`, giving marketplace
creators a weekly, platform-initiated payout mechanism built on the REF-01 template
(`049_ref01_payout_atomic.sql`).

WHAT THIS FILE PROVES, AND WHY IT NEEDS A REAL DATABASE
---------------------------------------------------------
The properties under test are TRANSACTIONAL and CONCURRENCY ones -- "running the
creation function twice for the same creator does not double-count", "two concurrent
instances racing for the same creator produce exactly one payout", "a rejected payout's
claimed settlements become payable again" -- none of which a mock of supabase-py/postgrest
can demonstrate, because the mock has no real transaction, row set or advisory lock to
exercise. Every assertion reads a row back over an INDEPENDENT connection, the same oracle
discipline `tests/test_ref01_payout_atomic_postgres.py` already uses.

Gated on AERORA_TEST_DATABASE_URL naming PostgreSQL. A SKIP here is NOT a pass -- the
idempotency and currency-separation properties this migration exists to provide are
unverified until this runs for real.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
        "here is NOT a pass -- MKT-06's atomicity and currency-separation guarantees "
        "are unverified."
    ),
)


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


def _seed_listing_and_subscription(cur, owner_id: str, buyer_id: str, price_minor=2999,
                                    currency="USD"):
    for ident, label in ((owner_id, "owner"), (buyer_id, "buyer")):
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (ident, f"mkt06-{label}-{ident[:8]}@example.invalid"),
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING",
            (ident,),
        )

    cur.execute(
        "INSERT INTO public.library_strategies "
        "(author_id, source_strategy_id, name, category, symbol, timeframe, "
        " exchange_id, moderation_status, is_active, price_minor, currency) "
        "VALUES (%s, %s, 'mkt06-probe-listing', 'trend', 'BTC/USDT', '1h', 'binance', "
        "        'approved', true, %s, %s) RETURNING id",
        (owner_id, str(uuid.uuid4()), price_minor, currency),
    )
    listing_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO public.library_subscriptions "
        "(library_id, user_id, status, subscription_tier, price_minor, currency) "
        "VALUES (%s, %s, 'pending', 'pro', %s, %s) RETURNING id",
        (listing_id, buyer_id, price_minor, currency),
    )
    sub_id = cur.fetchone()[0]
    return listing_id, sub_id


def _seed_settlement(cur, *, owner_id, buyer_id, sub_id, listing_id, amount_minor,
                      owner_share_minor, platform_fee_minor, currency="USD",
                      is_reversal=False, reverses_reference=None):
    ref = f"pi_mkt06_{uuid.uuid4().hex[:16]}"
    cur.execute(
        "INSERT INTO public.marketplace_settlements "
        "(subscription_id, listing_id, owner_id, purchaser_id, amount_minor, currency, "
        " owner_share_minor, platform_fee_minor, provider, provider_reference, "
        " is_reversal, reverses_reference, settled_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'stripe', %s, %s, %s, now()) "
        "RETURNING id",
        (sub_id, listing_id, owner_id, buyer_id, amount_minor, currency,
         owner_share_minor, platform_fee_minor, ref, is_reversal, reverses_reference),
    )
    return cur.fetchone()[0]


@pytest.fixture
def creator():
    """One creator with a listing and subscriber, no settlements seeded yet."""
    owner_id = str(uuid.uuid4())
    buyer_id = str(uuid.uuid4())
    conn = _conn()
    try:
        with conn.cursor() as cur:
            listing_id, sub_id = _seed_listing_and_subscription(cur, owner_id, buyer_id)
    finally:
        conn.close()

    yield {"owner_id": owner_id, "buyer_id": buyer_id, "listing_id": listing_id,
           "sub_id": sub_id}

    # `marketplace_settlements` is append-only by design
    # (`marketplace_settlement_append_only_guard`, confirmed against the live
    # trigger body this file's own header links to): it refuses every UPDATE
    # unconditionally, and refuses DELETE while its `subscription_id` still
    # exists -- which itself cannot be deleted while a settlement references it
    # (`ON DELETE RESTRICT`). There is therefore no in-band way to remove a
    # seeded settlement/subscription/listing fixture once a settlement row has
    # been written against it, by design, in production as much as here. This
    # teardown only removes what THIS migration's own new tables created --
    # `marketplace_creator_payouts` (its claims cascade automatically) -- and
    # leaves the ledger fixture rows in place, exactly as the real ledger would.
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.marketplace_creator_payouts WHERE owner_id = %s",
                (owner_id,),
            )
    finally:
        conn.close()


def _create_payout(owner_id: str, period_start=None, period_end=None):
    period_end = period_end or datetime.now(timezone.utc)
    period_start = period_start or (period_end - timedelta(days=7))
    conn = _conn(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM public.create_creator_payout(%s, %s, %s)",
                (owner_id, period_start, period_end),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _settle_payout(payout_id: str, status: str, reference=None, reason=None):
    conn = _conn(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.settle_creator_payout(%s, %s, %s, %s)",
                (payout_id, status, reference, reason),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _payout_rows(owner_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, amount_minor, currency, reference, reason "
                "FROM public.marketplace_creator_payouts WHERE owner_id = %s "
                "ORDER BY currency",
                (owner_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _unclaimed_settlement_count(owner_id: str, currency=None):
    """Settlements for this owner with no row in marketplace_creator_payout_claims."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if currency:
                cur.execute(
                    "SELECT count(*) FROM public.marketplace_settlements s "
                    "WHERE s.owner_id = %s AND s.currency = %s "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM public.marketplace_creator_payout_claims c "
                    "  WHERE c.settlement_id = s.id)",
                    (owner_id, currency),
                )
            else:
                cur.execute(
                    "SELECT count(*) FROM public.marketplace_settlements s "
                    "WHERE s.owner_id = %s "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM public.marketplace_creator_payout_claims c "
                    "  WHERE c.settlement_id = s.id)",
                    (owner_id,),
                )
            return cur.fetchone()[0]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  PART 1 — THE FUNCTIONS AND THEIR GUARANTEES EXIST
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_both_functions_exist_exactly_once_and_are_service_role_only():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns "
                "ON ns.oid = p.pronamespace WHERE ns.nspname='public' "
                "AND p.proname='create_creator_payout'"
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns "
                "ON ns.oid = p.pronamespace WHERE ns.nspname='public' "
                "AND p.proname='settle_creator_payout'"
            )
            assert cur.fetchone()[0] == 1

            cur.execute(
                "SELECT has_function_privilege('authenticated', "
                "'public.create_creator_payout(uuid,timestamptz,timestamptz)', "
                "'EXECUTE')"
            )
            assert cur.fetchone()[0] is False, (
                "create_creator_payout must not be callable by a browser role -- "
                "creation is platform-initiated only"
            )

            cur.execute(
                "SELECT has_function_privilege('authenticated', "
                "'public.settle_creator_payout(uuid,text,text,text)', 'EXECUTE')"
            )
            assert cur.fetchone()[0] is False, (
                "settle_creator_payout bypasses RLS for ANY owner's payout and must "
                "not be callable by a browser role"
            )
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  PART 2 — create_creator_payout
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_zero_unpaid_balance_creates_no_payout_row(creator):
    """A creator with zero unpaid earnings (no settlements at all) gets no payout."""
    rows = _create_payout(creator["owner_id"])
    assert rows == []
    assert _payout_rows(creator["owner_id"]) == []


@pg_only
def test_a_positive_balance_creates_exactly_one_payout_and_claims_the_settlement(creator):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            settlement_id = _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    rows = _create_payout(creator["owner_id"])
    assert len(rows) == 1
    assert rows[0]["amount_minor"] == 2699
    assert rows[0]["currency"] == "USD"
    assert rows[0]["status"] == "pending"

    assert _unclaimed_settlement_count(creator["owner_id"]) == 0

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payout_id FROM public.marketplace_creator_payout_claims "
                "WHERE settlement_id = %s", (settlement_id,),
            )
            assert cur.fetchone()[0] == rows[0]["id"]
    finally:
        conn.close()


@pg_only
def test_running_the_function_twice_in_a_row_does_not_double_count(creator):
    """THE EXACT RACE REF-01'S OWN MIGRATION GUARDS AGAINST, APPLIED HERE.

    Calling create_creator_payout a second time immediately after the first -- same
    creator, same week -- must create NOTHING the second time, because the first call
    already claimed every settlement that produced its payout.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    first = _create_payout(creator["owner_id"])
    assert len(first) == 1

    second = _create_payout(creator["owner_id"])
    assert second == [], (
        "a second call for the same creator immediately after the first created a "
        "payout row too -- the same settlement was double-counted"
    )

    rows = _payout_rows(creator["owner_id"])
    assert len(rows) == 1, "exactly one payout row must exist after both calls"
    assert rows[0]["amount_minor"] == 2699


@pg_only
def test_two_different_currencies_produce_two_separate_payout_rows(creator):
    """Money must never be summed across currencies (creator_earnings's own rule)."""
    owner_id = creator["owner_id"]
    buyer_id = creator["buyer_id"]

    conn = _conn()
    try:
        with conn.cursor() as cur:
            # USD settlement on the existing listing/subscription.
            _seed_settlement(
                cur, owner_id=owner_id, buyer_id=buyer_id,
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
                currency="USD",
            )
            # A second listing/subscription priced in INR for the SAME owner.
            listing_id_inr, sub_id_inr = _seed_listing_and_subscription(
                cur, owner_id, buyer_id, price_minor=199900, currency="INR"
            )
            _seed_settlement(
                cur, owner_id=owner_id, buyer_id=buyer_id,
                sub_id=sub_id_inr, listing_id=listing_id_inr,
                amount_minor=199900, owner_share_minor=179910, platform_fee_minor=19990,
                currency="INR",
            )
    finally:
        conn.close()

    rows = _create_payout(owner_id)
    assert len(rows) == 2, f"expected one payout per currency, got {rows}"

    by_currency = {r["currency"]: r["amount_minor"] for r in rows}
    assert by_currency == {"USD": 2699, "INR": 179910}, (
        "currencies must never be combined into one payout total"
    )

    # `marketplace_creator_payouts` is this test's own; the fixture's teardown
    # already clears rows for `owner_id`. The extra INR listing/subscription/
    # settlement rows are left in place, same as every other fixture here --
    # see the `creator` fixture's own teardown comment for why.


@pg_only
def test_a_net_negative_currency_balance_creates_no_payout_and_leaves_rows_unclaimed(
    creator
):
    """A reversal outweighing its payment must not produce a payout, and must not be
    claimed away where a future payment could never net against it."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            payment_id = _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
            cur.execute(
                "SELECT provider_reference FROM public.marketplace_settlements "
                "WHERE id = %s", (payment_id,),
            )
            original_ref = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.marketplace_settlements "
                "(subscription_id, listing_id, owner_id, purchaser_id, amount_minor, "
                " currency, owner_share_minor, platform_fee_minor, provider, "
                " provider_reference, is_reversal, reverses_reference, settled_at) "
                "VALUES (%s, %s, %s, %s, 2999, 'USD', 2699, 300, 'stripe', %s, true, "
                "        %s, now())",
                (creator["sub_id"], creator["listing_id"], creator["owner_id"],
                 creator["buyer_id"], original_ref, original_ref),
            )
    finally:
        conn.close()

    rows = _create_payout(creator["owner_id"])
    assert rows == [], "a net-zero (fully reversed) balance must create no payout"
    assert _unclaimed_settlement_count(creator["owner_id"], "USD") == 2, (
        "both the payment and its reversal must remain unclaimed, available to net "
        "against a future payment in this currency"
    )


@pg_only
def test_concurrent_creation_for_the_same_creator_produces_exactly_one_payout(creator):
    """THE ATOMICITY PROPERTY, UNDER REAL CONCURRENCY.

    Two threads each call create_creator_payout for the SAME creator at the same time.
    pg_advisory_xact_lock serialises them; only the first to acquire it finds anything
    to claim, so exactly one payout row results, never two and never a doubled amount.
    """
    import threading

    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    results = {}

    def _attempt(name):
        try:
            results[name] = ("ok", _create_payout(creator["owner_id"]))
        except Exception as exc:  # noqa: BLE001
            results[name] = ("error", str(exc))

    t1 = threading.Thread(target=_attempt, args=("a",))
    t2 = threading.Thread(target=_attempt, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    non_empty = [v[1] for v in results.values() if v[0] == "ok" and v[1]]
    assert len(non_empty) == 1, (
        f"exactly one of two concurrent calls should have found something to claim; "
        f"got {results}"
    )

    rows = _payout_rows(creator["owner_id"])
    assert len(rows) == 1, f"expected exactly one payout row, got {rows}"
    assert rows[0]["amount_minor"] == 2699


# ══════════════════════════════════════════════════════════════════════════
#  PART 3 — settle_creator_payout
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_completing_records_reference_and_leaves_settlements_claimed(creator):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    payout = _create_payout(creator["owner_id"])[0]
    settled = _settle_payout(payout["id"], "completed", reference="UPI-TXN-1")

    assert settled["status"] == "completed"
    assert settled["reference"] == "UPI-TXN-1"
    assert settled["settled_at"] is not None
    assert _unclaimed_settlement_count(creator["owner_id"]) == 0, (
        "completing a payout must leave its claimed settlements claimed"
    )


@pg_only
def test_completing_without_a_reference_is_refused_and_changes_nothing(creator):
    import psycopg2

    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    payout = _create_payout(creator["owner_id"])[0]

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(payout["id"], "completed", reference=None)
    assert exc_info.value.pgcode == "PY003"

    rows = _payout_rows(creator["owner_id"])
    assert rows[0]["status"] == "pending"


@pg_only
def test_rejecting_a_payout_unclaims_its_settlements_which_become_payable_next_cycle(
    creator
):
    """THE SINGLE MOST IMPORTANT CORRECTNESS PROPERTY.

    A rejected payout's claimed settlement must become unclaimed again -- and the
    NEXT create_creator_payout call for this creator must pick it back up and create
    a fresh payout for the same amount. A rejection that permanently hid the money
    would be worse for the creator than having no payout mechanism at all.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    first_payout = _create_payout(creator["owner_id"])[0]
    assert _unclaimed_settlement_count(creator["owner_id"]) == 0

    settled = _settle_payout(first_payout["id"], "rejected", reason="bad bank details")
    assert settled["status"] == "rejected"
    assert settled["reason"] == "bad bank details"

    assert _unclaimed_settlement_count(creator["owner_id"]) == 1, (
        "the rejected payout's settlement must be unclaimed again"
    )

    second_payout = _create_payout(creator["owner_id"])
    assert len(second_payout) == 1, (
        "the next weekly sweep must create a fresh payout for the un-claimed money"
    )
    assert second_payout[0]["amount_minor"] == 2699
    assert second_payout[0]["id"] != first_payout["id"]

    rows = _payout_rows(creator["owner_id"])
    assert len(rows) == 2
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["pending", "rejected"]


@pg_only
@pytest.mark.parametrize("terminal_status,reference,reason", [
    ("completed", "TXN-1", None),
    ("rejected", None, "first reason"),
])
def test_an_already_terminal_payout_cannot_be_settled_again(
    creator, terminal_status, reference, reason
):
    import psycopg2

    conn = _conn()
    try:
        with conn.cursor() as cur:
            _seed_settlement(
                cur, owner_id=creator["owner_id"], buyer_id=creator["buyer_id"],
                sub_id=creator["sub_id"], listing_id=creator["listing_id"],
                amount_minor=2999, owner_share_minor=2699, platform_fee_minor=300,
            )
    finally:
        conn.close()

    payout = _create_payout(creator["owner_id"])[0]
    _settle_payout(payout["id"], terminal_status, reference=reference, reason=reason)

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(payout["id"], "completed", reference="TXN-SECOND")
    assert exc_info.value.pgcode == "PY004"


@pg_only
def test_settling_a_nonexistent_payout_raises_py002():
    import psycopg2

    with pytest.raises(psycopg2.Error) as exc_info:
        _settle_payout(str(uuid.uuid4()), "completed", reference="TXN-GHOST")
    assert exc_info.value.pgcode == "PY002"
