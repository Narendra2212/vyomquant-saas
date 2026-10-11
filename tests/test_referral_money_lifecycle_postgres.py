"""The referral money path works: credit, reverse, and a readable approved balance.

WHY THIS EXISTS (BUG-035). Two files declared ``referral_wallets`` with different column
sets, and ``CREATE TABLE IF NOT EXISTS`` meant the first one to run won:

    006_reconcile_production_database.sql  balance_usd, total_earned_usd, pending_usd
    referral_system_redesign.sql           pending_balance_usd, approved_balance_usd,
                                           paid_balance_usd, lifetime_earnings_usd

The table ended up with 006's three columns while everything else moved to the four-state
model, and that broke every money path in the feature at once -- which was only visible
by executing them:

  * ``process_referral_commission`` -- the version actually installed, verified against
    ``pg_proc`` -- writes ``pending_balance_usd`` and ``lifetime_earnings_usd``. Against
    006's table that is ``UndefinedColumn``, so CREDITING a commission failed.
  * ``reverse_referral_commission`` writes three of the four, so REVERSING on a refund
    failed.
  * ``routers/referral.py`` reads all four in three endpoints, and its payout handler
    does ``float(wallet.get("approved_balance_usd", 0))``. A column that does not exist
    cannot come back from ``select("*")``, so that resolved to ``0.0`` and -- with the
    endpoint's own $10 minimum -- EVERY payout request was answered
    "Insufficient approved balance. Available: $0.00". Quietly, for every user, looking
    exactly like someone with no earnings.

``referral_system_redesign.sql`` now carries an explicit forward migration from 006's
shape, with a precheck that refuses rather than inventing a ``paid_balance_usd`` figure
it cannot derive.

WHAT THIS ASSERTS. The database-level money lifecycle, through the installed PL/pgSQL
functions rather than through HTTP. The functions are where the arithmetic lives and
where the column mismatch bit; the router's own failure was a direct consequence of
``approved_balance_usd`` not existing, which the shape tests below cover.

Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason="AERORA_TEST_DATABASE_URL is unset or not PostgreSQL.",
)

FOUR_STATE_COLUMNS = (
    "pending_balance_usd",
    "approved_balance_usd",
    "paid_balance_usd",
    "lifetime_earnings_usd",
)
RETIRED_COLUMNS = ("balance_usd", "total_earned_usd", "pending_usd")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def referral_pair(engine):
    """A referrer and a referred user, linked, with the referrer's wallet ready."""
    referrer = str(uuid.uuid4())
    referred = str(uuid.uuid4())
    created = [referrer, referred]

    with engine.begin() as conn:
        for uid in created:
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
                {"i": uid, "e": f"{uid[:8]}@referral.test"},
            )
            conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": uid})

        code_id = conn.execute(
            text("SELECT id FROM public.referral_codes WHERE user_id = :i"),
            {"i": referrer},
        ).scalar()
        # status='pending', NOT 'active'. `process_referral_commission` looks the
        # relationship up with `WHERE referred_id = ... AND status = 'pending'` and
        # RETURNs silently when it finds nothing, so an 'active' relationship produces a
        # no-op that looks identical to a broken function. The lifecycle is: a referral
        # is created pending, the referred user's first payment credits the commission
        # and the same function flips it to active -- which is also why only the first
        # payment earns.
        conn.execute(
            text(
                "INSERT INTO public.referral_relationships "
                "  (referrer_id, referred_id, referral_code_id, status) "
                "VALUES (:a, :b, :c, 'pending')"
            ),
            {"a": referrer, "b": referred, "c": code_id},
        )
        conn.execute(
            text(
                "INSERT INTO public.referral_wallets (user_id) VALUES (:i) "
                "ON CONFLICT (user_id) DO NOTHING"
            ),
            {"i": referrer},
        )

    yield referrer, referred

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '3s'"))
        conn.execute(
            text("DELETE FROM public.referral_commissions "
                 "WHERE referrer_id = ANY(CAST(:ids AS uuid[])) "
                 "   OR referred_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": created},
        )
        conn.execute(
            text("DELETE FROM public.referral_relationships "
                 "WHERE referred_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": created},
        )
        for table, column in (
            ("referral_wallets", "user_id"),
            ("referral_codes", "user_id"),
        ):
            conn.execute(
                text(f"DELETE FROM public.{table} "
                     f"WHERE {column} = ANY(CAST(:ids AS uuid[]))"),
                {"ids": created},
            )
        conn.execute(
            text("DELETE FROM public.profiles WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": created},
        )
        conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": created},
        )


def _wallet(conn, user_id: str) -> dict:
    row = conn.execute(
        text(
            "SELECT pending_balance_usd, approved_balance_usd, paid_balance_usd, "
            "       lifetime_earnings_usd "
            "FROM public.referral_wallets WHERE user_id = :i"
        ),
        {"i": user_id},
    ).first()
    assert row is not None, f"no wallet for {user_id}"
    return dict(zip(FOUR_STATE_COLUMNS, row))


# ── the shape ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("column", FOUR_STATE_COLUMNS)
def test_the_four_state_columns_exist(engine, column):
    """``approved_balance_usd`` is the one the payout endpoint reads.

    Its absence did not raise -- ``select("*")`` simply did not return it and
    ``.get(..., 0)`` supplied a zero -- which is why this needs asserting directly rather
    than inferred from an endpoint not erroring.
    """
    with engine.begin() as conn:
        found = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='referral_wallets' "
                "  AND column_name=:c"
            ),
            {"c": column},
        ).scalar()
    assert found == 1, (
        f"referral_wallets.{column} is missing; routers/referral.py reads it and would "
        f"silently see 0"
    )


@pytest.mark.parametrize("column", RETIRED_COLUMNS)
def test_the_retired_006_columns_are_inert_but_retained(engine, column):
    """Two column sets on one table is how this happened. One set must stop being used.

    An earlier version of this test asserted the three 006 columns were DROPPED, and the
    migration dropped them. Both were wrong, and
    ``tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements``
    said so: Requirement 24.7 forbids destructive statements anywhere in the migration
    set. These columns hold money balances, a DROP against production is irreversible,
    and if the backfill in ``referral_system_redesign.sql`` mapped something wrongly the
    original figures are the only way to find out.

    So the invariant is not "gone". It is **inert and safe to leave**:

      * nullable with a default, so no INSERT anywhere has to mention them;
      * written by no installed function -- asserted by
        ``test_the_installed_functions_reference_only_existing_columns`` below, which
        reads ``pg_proc`` rather than the migration files;
      * read by nothing in ``backend_app/``.

    Asserting nullability is what makes retention *provably* harmless rather than
    merely untested: a NOT NULL vestigial column would break every wallet insert.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='referral_wallets' "
                "  AND column_name=:c"
            ),
            {"c": column},
        ).one_or_none()

    assert row is not None, (
        f"referral_wallets.{column} is absent. It is retained deliberately so the "
        f"four-state backfill can be reconciled against the original figures -- see "
        f"migrations/referral_system_redesign.sql. Dropping it is a separate decision."
    )
    is_nullable, default = row
    assert is_nullable == "YES", (
        f"referral_wallets.{column} is NOT NULL, so retaining it is not harmless: every "
        f"wallet INSERT that omits it now fails. Make it nullable or drop it, but it "
        f"cannot stay as it is."
    )
    assert default is not None, (
        f"referral_wallets.{column} has no default; a vestigial money column should "
        f"default to 0 so a partial insert cannot leave it NULL and ambiguous"
    )


def test_the_installed_functions_reference_only_existing_columns(engine):
    """The check that would have caught BUG-035 directly.

    Both money functions are read out of ``pg_proc`` and every wallet column they mention
    is required to exist. This is cheap, general, and fails the moment the table and the
    functions drift apart again -- which is the actual defect, rather than any one of its
    symptoms.
    """
    import re

    with engine.begin() as conn:
        table_columns = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='referral_wallets'"
                )
            ).scalars().all()
        )
        problems: list[str] = []
        for name in ("process_referral_commission", "reverse_referral_commission",
                     "create_referral_code_for_user"):
            body = conn.execute(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname=:n LIMIT 1"
                ),
                {"n": name},
            ).scalar()
            if not body:
                problems.append(f"{name} is not installed")
                continue
            for column in FOUR_STATE_COLUMNS + RETIRED_COLUMNS:
                if re.search(rf"\b{column}\b", body) and column not in table_columns:
                    problems.append(f"{name} references missing column {column}")

    assert not problems, "referral functions disagree with the table: " + "; ".join(problems)


def test_balances_cannot_go_negative(engine, referral_pair):
    """The invariant ``referral_system_redesign.sql`` declares, enforced by the database.

    A negative approved balance would mean a payout was allowed against money that was
    not there.
    """
    referrer, _referred = referral_pair
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        "UPDATE public.referral_wallets "
                        "SET approved_balance_usd = -1 WHERE user_id = :i"
                    ),
                    {"i": referrer},
                )
            assert "chk_non_negative_balances" in str(excinfo.value) or "check" in str(
                excinfo.value
            ).lower(), str(excinfo.value)
        finally:
            trans.rollback()


# ── the money lifecycle ──────────────────────────────────────────────────────


def test_a_commission_credits_the_referrers_pending_balance(engine, referral_pair):
    """``process_referral_commission`` -- the write path that failed outright before."""
    referrer, referred = referral_pair
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    with engine.begin() as conn:
        before = _wallet(conn, referrer)
        conn.execute(
            text(
                "SELECT process_referral_commission(:referred, :payment, :tier, :amount)"
            ),
            {
                "referred": referred,
                "payment": payment_id,
                "tier": "pro",
                "amount": Decimal("100.00"),
            },
        )
        after = _wallet(conn, referrer)

    assert after["pending_balance_usd"] > before["pending_balance_usd"], (
        f"pending_balance_usd did not move: {before} -> {after}. The commission was not "
        f"credited."
    )
    assert after["lifetime_earnings_usd"] > before["lifetime_earnings_usd"]
    # Nothing becomes immediately withdrawable: a fresh commission is pending, not
    # approved. That ordering is what the payout endpoint's "approved" check relies on.
    assert after["approved_balance_usd"] == before["approved_balance_usd"]


def test_a_commission_can_be_reversed(engine, referral_pair):
    """``reverse_referral_commission`` -- the refund path, also broken before.

    A refund that cannot reverse its commission pays a referrer for revenue the business
    gave back.
    """
    referrer, referred = referral_pair
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    with engine.begin() as conn:
        conn.execute(
            text("SELECT process_referral_commission(:r, :p, :t, :a)"),
            {"r": referred, "p": payment_id, "t": "pro", "a": Decimal("100.00")},
        )
        credited = _wallet(conn, referrer)

        conn.execute(
            text("SELECT reverse_referral_commission(:p, :reason)"),
            {"p": payment_id, "reason": "refund issued in test"},
        )
        reversed_ = _wallet(conn, referrer)

    assert reversed_["pending_balance_usd"] < credited["pending_balance_usd"], (
        f"the reversal did not reduce the pending balance: {credited} -> {reversed_}"
    )
    assert reversed_["lifetime_earnings_usd"] < credited["lifetime_earnings_usd"]


def test_an_approved_balance_is_readable_and_non_zero(engine, referral_pair):
    """The payout endpoint's precondition, isolated.

    ``routers/referral.py`` reads ``approved_balance_usd`` and refuses the payout when it
    is below the requested amount. While the column did not exist that read produced
    ``0.0`` for everyone. This approves a balance the way the commission flow would and
    asserts the value the endpoint reads is both present and correct -- which is the
    difference between "no earnings" and "cannot see earnings".
    """
    referrer, referred = referral_pair
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    with engine.begin() as conn:
        conn.execute(
            text("SELECT process_referral_commission(:r, :p, :t, :a)"),
            {"r": referred, "p": payment_id, "t": "pro", "a": Decimal("500.00")},
        )
        pending = _wallet(conn, referrer)["pending_balance_usd"]
        assert pending > 0

        # Approval moves pending -> approved. Done directly because approval is an
        # operator/scheduled action rather than part of the commission function.
        conn.execute(
            text(
                "UPDATE public.referral_wallets "
                "SET approved_balance_usd = approved_balance_usd + pending_balance_usd, "
                "    pending_balance_usd = 0 "
                "WHERE user_id = :i"
            ),
            {"i": referrer},
        )
        after = _wallet(conn, referrer)

    assert after["approved_balance_usd"] == pending, (
        f"approved balance is {after['approved_balance_usd']}, expected {pending}"
    )
    assert after["pending_balance_usd"] == 0

    # The value the payout endpoint reads, read the same way: a dict lookup with a 0
    # default. The point is that the key is present, so the default never applies.
    as_endpoint_sees_it = float(
        dict(zip(FOUR_STATE_COLUMNS, [
            after["pending_balance_usd"], after["approved_balance_usd"],
            after["paid_balance_usd"], after["lifetime_earnings_usd"],
        ])).get("approved_balance_usd", 0)
    )
    assert as_endpoint_sees_it >= 10.0, (
        f"the endpoint would see {as_endpoint_sees_it} and refuse a $10 payout as "
        f"'Insufficient approved balance'"
    )
