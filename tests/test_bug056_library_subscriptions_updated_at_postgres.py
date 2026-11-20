"""BUG-056 regression: every UPDATE to ``public.library_subscriptions`` used to fail.

THE DEFECT
----------
``trg_library_subscriptions_updated_at`` (``archived_migrations/root_migrations/
007_create_library_subscriptions.sql``) unconditionally assigns ``NEW.updated_at`` on every
UPDATE to ``library_subscriptions``. The table never had an ``updated_at`` column.
PL/pgSQL does not ignore an assignment to a field ``NEW`` does not carry -- it raises
``42703 record "new" has no field "updated_at"`` on the first UPDATE statement, which is
every statement that matters: ``settlement_service.settle()`` (the function that activates a
subscription after a confirmed Stripe/Razorpay payment), ``cancel_subscription``,
``renew_subscription`` and ``expiry_sweep.sweep()`` all UPDATE this table. A paying customer's
subscription could never reach ``active``.

``backend_app.backend.marketplace.COLUMN_CONTRACT["expiry_sweep"]`` already named
``updated_at`` on ``library_subscriptions`` -- the manifest was correct. What was missing was
the column itself, and a test that executed the real trigger rather than a double.

WHY THE EXISTING SUITE DID NOT CATCH THIS
------------------------------------------
``tests/test_settlement_service.py`` and the property suite built on it exercise
``settle()`` against ``FakeSupabase``, a Python double with no trigger of any kind.
``tests/test_expiry_sweep.py::test_the_column_contract_entry_covers_every_table_the_sweep_
touches`` asserts the manifest against itself, not against a live schema.
``tests/test_marketplace_paper_schema_contract.py`` parses migration *text* only (there is no
PostgreSQL in CI, per its own module docstring) -- it cannot see a trigger function's body,
so it cannot see a trigger that assigns a column no ``CREATE``/``ALTER`` statement ever adds.

Catching this required an UPDATE to actually run against a real trigger on a real table. That
is what this file does, and nothing less would have caught it.

THE FIX
-------
``backend_app/migrations/021_fix_library_subscriptions_updated_at.sql`` adds the column
(``ALTER TABLE ... ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``),
additive only, idempotent. It also adds a generic backstop that scans every trigger function
in ``public`` for a ``NEW.<column>`` assignment targeting a column its own table does not
declare, and fails provisioning if one is found -- so a new instance of this same defect class
fails fast instead of failing on the first production UPDATE.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: this bug lives in a trigger function body, "
        "which only fires against a real UPDATE on a real table. SQLite has neither "
        "triggers of this shape nor the column to omit. Start one with "
        "staging/bootstrap_pg.py."
    ),
)

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def test_library_subscriptions_has_an_updated_at_column(engine):
    """The column the trigger has always assumed exists, now actually exists."""
    with engine.begin() as conn:
        dtype = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='library_subscriptions' "
                "  AND column_name='updated_at'"
            )
        ).scalar()
    assert dtype is not None, (
        "public.library_subscriptions has no updated_at column. "
        "trg_library_subscriptions_updated_at unconditionally assigns NEW.updated_at on "
        "every UPDATE, so every UPDATE to this table raises 42703. This is BUG-056. "
        "Apply backend_app/migrations/021_fix_library_subscriptions_updated_at.sql."
    )
    assert dtype in ("timestamp with time zone", "timestamp without time zone"), (
        f"public.library_subscriptions.updated_at is {dtype!r}, expected a timestamp type"
    )


def test_a_real_settlement_confirmation_update_succeeds(engine):
    """The exact UPDATE shape ``settlement_service.settle()`` issues
    (``settlement_service.py``, the PENDING -> ACTIVE transition after a confirmed payment)
    must succeed against the real trigger, with a real, non-reversal settlement row seeded
    first -- what a successful Stripe/Razorpay webhook produces before activation.

    This is the test that would have failed with 42703 before the fix: BUG-056 was not a
    theoretical trigger/column mismatch, it was *this exact statement* failing on *every*
    legitimate payment confirmation.
    """
    author = str(uuid.uuid4())
    buyer = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=30)

    with engine.begin() as conn:
        savepoint = conn.begin_nested()
        try:
            for ident, label in ((author, "author"), (buyer, "buyer")):
                conn.execute(
                    text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
                    {"id": ident, "email": f"bug056-{label}-{ident[:8]}@example.invalid"},
                )
                conn.execute(
                    text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": ident}
                )

            listing_id = conn.execute(
                text(
                    "INSERT INTO public.library_strategies "
                    "(author_id, source_strategy_id, name, category, symbol, timeframe, "
                    " exchange_id, moderation_status, is_active, price_minor, currency) "
                    "VALUES (:author, :src, 'bug056-probe-listing', 'trend', 'BTC/USDT', "
                    "        '1h', 'binance', 'approved', true, 2999, 'USD') "
                    "RETURNING id"
                ),
                {"author": author, "src": str(uuid.uuid4())},
            ).scalar()

            sub_id = conn.execute(
                text(
                    "INSERT INTO public.library_subscriptions "
                    "(library_id, user_id, status, subscription_tier, price_minor, "
                    " currency) "
                    "VALUES (:listing, :buyer, 'pending', 'pro', 2999, 'USD') "
                    "RETURNING id"
                ),
                {"listing": listing_id, "buyer": buyer},
            ).scalar()

            # The payment-gate trigger (trg_subscription_transition_guard) requires a real,
            # non-reversal settlement row before it allows the transition to 'active' -- so
            # this seeds exactly what a successful webhook produces, not a shortcut around it.
            conn.execute(
                text(
                    "INSERT INTO public.marketplace_settlements "
                    "(subscription_id, listing_id, owner_id, purchaser_id, amount_minor, "
                    " currency, owner_share_minor, platform_fee_minor, provider, "
                    " provider_reference, is_reversal, settled_at) "
                    "VALUES (:sub, :listing, :author, :buyer, 2999, 'USD', 2699, 300, "
                    "        'stripe', :ref, false, now())"
                ),
                {
                    "sub": sub_id,
                    "listing": listing_id,
                    "author": author,
                    "buyer": buyer,
                    "ref": f"pi_bug056_{uuid.uuid4().hex[:12]}",
                },
            )

            result = conn.execute(
                text(
                    "UPDATE public.library_subscriptions SET "
                    "  status = 'active', "
                    "  period_start = :start, "
                    "  period_expiry = :expiry, "
                    "  owner_share_minor = 2699, "
                    "  platform_fee_minor = 300 "
                    "WHERE id = :sub"
                ),
                {"start": now, "expiry": expiry, "sub": sub_id},
            )
            assert result.rowcount == 1, (
                "the settlement-confirmation UPDATE affected 0 rows -- the subscription "
                "was not found, which is a different bug than BUG-056 but still a failure"
            )

            activated = conn.execute(
                text(
                    "SELECT status, updated_at IS NOT NULL "
                    "FROM public.library_subscriptions WHERE id = :sub"
                ),
                {"sub": sub_id},
            ).first()
            assert activated is not None
            assert activated[0] == "active"
            assert activated[1] is True, (
                "updated_at was not populated by trg_library_subscriptions_updated_at "
                "after a successful UPDATE"
            )
        finally:
            savepoint.rollback()
