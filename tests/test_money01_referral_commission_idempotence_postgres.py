"""MONEY-01 regression: one payment can produce at most one referral commission.

THE DEFECT, CONFIRMED NOT SUSPECTED
-----------------------------------
Two versions of `process_referral_commission` exist in the repository. The audit could
not determine which was installed. It was read off the live catalogue during remediation
(`staging/rem_inspect2.txt`)::

    PROCESS_REF_COMM_HAS_DUP_GUARD| NO_EXISTS_GUARD

The installed body was the `migrations/referral_system_redesign.sql` version, which has
**no** `payment_id` duplicate check: it computed `p_payment_amount_usd * 0.20`,
unconditionally INSERTed into `referral_commissions`, and then upserted
`referral_wallets` with `pending_balance_usd = pending_balance_usd + amount`.

And nothing in the schema stopped it. `referral_commissions` carried only
`idx_referral_commissions_payment` -- a **plain** index, not unique. So a redelivered or
replayed payment webhook credited the referrer twice. The Redis two-phase lock in
`routers/billing.py` was the only thing in the way, and it is a 60-second window on a
store that can be unavailable -- unlike the marketplace path, which makes its
exactly-once guarantee in the database with `uq_settlement_reference_reversal`.

WHY A `SELECT EXISTS` GUARD WOULD NOT HAVE BEEN ENOUGH
-----------------------------------------------------
The *other* version (`006_reconcile_production_database.sql`) does carry
`IF EXISTS (SELECT 1 ... WHERE payment_id = p_payment_id) THEN RETURN NULL`. That is
still unsafe: two concurrent webhook deliveries can both run the SELECT before either
runs the INSERT, both see no row, and both insert. Only a unique index makes the check
and the write one atomic decision.
:func:`test_two_concurrent_workers_cannot_both_credit` is the test that demonstrates
this, and it is the one the brief specifically asks for.

THE FIX
-------
`backend_app/migrations/024_remediate_referral_double_commission.sql`:

* `uq_referral_commission_payment` -- a partial unique index on `payment_id`
  `WHERE payment_id IS NOT NULL`;
* the function replaced at its **real** installed signature with
  `ON CONFLICT (payment_id) DO NOTHING`, and the wallet update moved *inside* the
  `v_commission_id IS NOT NULL` guard. That last part matters: in the version replaced,
  the wallet upsert ran unconditionally, so deduplicating the ledger row alone would have
  fixed the symptom and left the balance still double-moving.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: the invariant is a partial unique index and "
        "the proof is a genuine two-connection race. SQLite has neither. Start one with "
        "staging/bootstrap_pg.py."
    ),
)

sa = pytest.importorskip("sqlalchemy")
psycopg2 = pytest.importorskip("psycopg2")
from sqlalchemy import create_engine, text  # noqa: E402

COMMISSION_RATE = 0.20


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_referral_pair(conn):
    """A referrer, a referred user, and an active relationship between them."""
    referrer = str(uuid.uuid4())
    referred = str(uuid.uuid4())
    for ident, label in ((referrer, "referrer"), (referred, "referred")):
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :e)"),
            {"id": ident, "e": f"money01-{label}-{ident[:8]}@example.invalid"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": ident})

    # Creating a profile already provisions a referral code for it (there is a
    # `uq_referral_codes_user UNIQUE (user_id)` and the row appears without this insert),
    # so this is fetch-or-create rather than a plain insert. Discovered by this test
    # failing with 23505 on `uq_referral_codes_user` for every seeded referrer.
    code_id = conn.execute(
        text(
            "INSERT INTO public.referral_codes (user_id, code) VALUES (:u, :c) "
            "ON CONFLICT (user_id) DO UPDATE SET code = public.referral_codes.code "
            "RETURNING id"
        ),
        {"u": referrer, "c": f"CODE{referrer[:8].upper()}"},
    ).scalar()

    conn.execute(
        text(
            "INSERT INTO public.referral_relationships "
            "(referrer_id, referred_id, referral_code_id, status) "
            "VALUES (:a, :b, :c, 'active')"
        ),
        {"a": referrer, "b": referred, "c": code_id},
    )
    return referrer, referred


def _commission_count(conn, payment_id):
    return conn.execute(
        text("SELECT count(*) FROM public.referral_commissions WHERE payment_id = :p"),
        {"p": payment_id},
    ).scalar()


def _wallet_pending(conn, referrer):
    return conn.execute(
        text(
            "SELECT coalesce(pending_balance_usd, 0) FROM public.referral_wallets "
            " WHERE user_id = :u"
        ),
        {"u": referrer},
    ).scalar()


# ── 1. the invariant exists in the database ──────────────────────────────────


def test_unique_index_on_payment_id_exists(engine):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                " WHERE schemaname='public' AND tablename='referral_commissions' "
                "   AND indexname='uq_referral_commission_payment'"
            )
        ).first()
    assert row is not None, (
        "uq_referral_commission_payment is missing. Without it a replayed payment "
        "webhook can double-credit a referrer. This is MONEY-01."
    )
    assert "UNIQUE" in row[0].upper(), f"the index is not UNIQUE: {row[0]}"
    assert "payment_id" in row[0]


def test_exactly_one_process_referral_commission_overload(engine):
    """Two overloads would mean PostgREST could resolve the app's call to either.

    This is not hypothetical: the first version of migration 024 declared a different
    argument list, which created a SECOND overload and left the vulnerable original
    live while reporting success.
    """
    with engine.begin() as conn:
        sigs = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT pg_get_function_identity_arguments(p.oid) "
                    "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    " WHERE n.nspname='public' AND p.proname='process_referral_commission'"
                )
            ).fetchall()
        ]
    assert len(sigs) == 1, f"expected exactly one overload, found {len(sigs)}: {sigs}"
    for expected in ("p_referred_id", "p_payment_id", "p_subscription_tier",
                     "p_payment_amount_usd"):
        assert expected in sigs[0], (
            f"the surviving signature ({sigs[0]}) lacks {expected}; it would not match "
            f"the named arguments routers/billing.py passes."
        )


def test_installed_function_carries_the_atomic_guard(engine):
    with engine.begin() as conn:
        body = conn.execute(
            text(
                "SELECT pg_get_functiondef(p.oid) "
                "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                " WHERE n.nspname='public' AND p.proname='process_referral_commission'"
            )
        ).scalar()
    assert "ON CONFLICT (payment_id)" in body, (
        "the installed function has no ON CONFLICT (payment_id) guard — a replay would "
        "raise 23505 on every provider retry instead of being a clean no-op."
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_browser_roles_cannot_execute_the_commission_function(engine, role):
    """It takes a caller-supplied `p_referred_id` and has no auth.uid() check."""
    with engine.begin() as conn:
        can = conn.execute(
            text(
                "SELECT has_function_privilege(:r, "
                " 'public.process_referral_commission"
                " (uuid,character varying,character varying,numeric)', 'EXECUTE')"
            ),
            {"r": role},
        ).scalar()
    assert can is False, (
        f"{role} can EXECUTE process_referral_commission, which credits a referral "
        f"wallet for a caller-supplied user id."
    )


def test_service_role_retains_execute(engine):
    """The legitimate webhook caller must still work."""
    with engine.begin() as conn:
        assert conn.execute(
            text(
                "SELECT has_function_privilege('service_role', "
                " 'public.process_referral_commission"
                " (uuid,character varying,character varying,numeric)', 'EXECUTE')"
            )
        ).scalar() is True


# ── 2. behaviour: a single payment credits exactly once ──────────────────────


def test_a_single_payment_creates_one_commission_and_moves_the_wallet(engine):
    """The happy path. Asserted first, so a fix that broke crediting is caught."""
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            referrer, referred = _seed_referral_pair(conn)
            payment_id = f"pay_{uuid.uuid4().hex[:16]}"

            returned = conn.execute(
                text(
                    "SELECT public.process_referral_commission("
                    "  :referred, :pid, 'pro', 100.00)"
                ),
                {"referred": referred, "pid": payment_id},
            ).scalar()

            assert returned is not None, "the first commission was not created"
            assert _commission_count(conn, payment_id) == 1
            assert float(_wallet_pending(conn, referrer)) == pytest.approx(
                100.00 * COMMISSION_RATE
            )
        finally:
            sp.rollback()


def test_replaying_the_same_payment_is_a_no_op(engine):
    """Webhook redelivery. The second call must create nothing and move no money."""
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            referrer, referred = _seed_referral_pair(conn)
            payment_id = f"pay_{uuid.uuid4().hex[:16]}"
            args = {"referred": referred, "pid": payment_id}
            call = text(
                "SELECT public.process_referral_commission(:referred, :pid, 'pro', 100.00)"
            )

            first = conn.execute(call, args).scalar()
            balance_after_first = float(_wallet_pending(conn, referrer))

            second = conn.execute(call, args).scalar()
            third = conn.execute(call, args).scalar()

            assert first is not None
            assert second is None, "a replay returned a new commission id"
            assert third is None
            assert _commission_count(conn, payment_id) == 1, (
                "a replayed payment created a second commission row"
            )
            assert float(_wallet_pending(conn, referrer)) == balance_after_first, (
                "a replayed payment moved the wallet again. Deduplicating the ledger row "
                "is not enough if the wallet upsert runs unconditionally."
            )
        finally:
            sp.rollback()


def test_a_direct_duplicate_insert_is_refused_by_the_database(engine):
    """The invariant must hold against anything that writes the table, not just the fn."""
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            referrer, referred = _seed_referral_pair(conn)
            payment_id = f"pay_{uuid.uuid4().hex[:16]}"
            # `subscription_tier` is NOT NULL on this table -- discovered by this test
            # failing with 23502. Worth recording because it means the commission
            # function cannot credit a payment whose tier is unknown; that is
            # pre-existing behaviour, unchanged by the MONEY-01 fix, and is noted in
            # staging/REMEDIATION_REPORT.md rather than altered here.
            ins = text(
                "INSERT INTO public.referral_commissions "
                "(referrer_id, referred_id, payment_id, subscription_tier, "
                " payment_amount_usd, commission_rate, commission_amount_usd, status) "
                "VALUES (:a, :b, :p, 'pro', 100.00, 0.20, 20.00, 'pending')"
            )
            params = {"a": referrer, "b": referred, "p": payment_id}
            conn.execute(ins, params)

            inner = conn.begin_nested()
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(ins, params)
            inner.rollback()
        finally:
            sp.rollback()


def test_two_different_payments_each_credit(engine):
    """The invariant must not over-reach and block legitimate separate payments."""
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            referrer, referred = _seed_referral_pair(conn)
            call = text(
                "SELECT public.process_referral_commission(:referred, :pid, 'pro', 50.00)"
            )
            a = conn.execute(call, {"referred": referred, "pid": f"pay_a_{uuid.uuid4().hex[:8]}"}).scalar()
            b = conn.execute(call, {"referred": referred, "pid": f"pay_b_{uuid.uuid4().hex[:8]}"}).scalar()
            assert a is not None and b is not None and a != b
            assert float(_wallet_pending(conn, referrer)) == pytest.approx(
                2 * 50.00 * COMMISSION_RATE
            )
        finally:
            sp.rollback()


# ── 3. refusals that protect the invariant's preconditions ───────────────────


def test_a_null_payment_id_is_refused(engine):
    """A payment with no identity cannot be deduplicated, so it must not be credited.

    SQL treats every NULL as distinct, so the partial unique index cannot stop a NULL
    payment_id from inserting unboundedly across retries. The function refuses instead.
    """
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            _referrer, referred = _seed_referral_pair(conn)
            result = conn.execute(
                text(
                    "SELECT public.process_referral_commission(:referred, NULL, 'pro', 100.00)"
                ),
                {"referred": referred},
            ).scalar()
            assert result is None
            assert conn.execute(
                text(
                    "SELECT count(*) FROM public.referral_commissions "
                    " WHERE referred_id = :b"
                ),
                {"b": referred},
            ).scalar() == 0
        finally:
            sp.rollback()


@pytest.mark.parametrize("amount", ["0", "-50.00", "NULL"])
def test_a_non_positive_payment_is_refused(engine, amount):
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            _referrer, referred = _seed_referral_pair(conn)
            result = conn.execute(
                text(
                    f"SELECT public.process_referral_commission("
                    f" :referred, :pid, 'pro', {amount})"
                ),
                {"referred": referred, "pid": f"pay_{uuid.uuid4().hex[:12]}"},
            ).scalar()
            assert result is None, f"a payment of {amount} produced a commission"
        finally:
            sp.rollback()


def test_an_unreferred_user_produces_no_commission(engine):
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            orphan = str(uuid.uuid4())
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:id, :e)"),
                {"id": orphan, "e": f"orphan-{orphan[:8]}@example.invalid"},
            )
            conn.execute(text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": orphan})
            result = conn.execute(
                text(
                    "SELECT public.process_referral_commission(:u, :pid, 'pro', 100.00)"
                ),
                {"u": orphan, "pid": f"pay_{uuid.uuid4().hex[:12]}"},
            ).scalar()
            assert result is None
        finally:
            sp.rollback()


def test_self_referral_is_refused_by_the_schema(engine):
    """`chk_no_self_referral` makes a self-referring relationship unrepresentable."""
    with engine.begin() as conn:
        sp = conn.begin_nested()
        try:
            me = str(uuid.uuid4())
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:id, :e)"),
                {"id": me, "e": f"self-{me[:8]}@example.invalid"},
            )
            conn.execute(text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": me})
            code_id = conn.execute(
                text(
                    "INSERT INTO public.referral_codes (user_id, code) VALUES (:u, :c) "
                    "ON CONFLICT (user_id) DO UPDATE SET code = public.referral_codes.code "
                    "RETURNING id"
                ),
                {"u": me, "c": f"SELF{me[:8].upper()}"},
            ).scalar()

            inner = conn.begin_nested()
            with pytest.raises(sa.exc.IntegrityError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO public.referral_relationships "
                        "(referrer_id, referred_id, referral_code_id, status) "
                        "VALUES (:a, :a, :c, 'active')"
                    ),
                    {"a": me, "c": code_id},
                )
            inner.rollback()
            assert "chk_no_self_referral" in str(exc.value)
        finally:
            sp.rollback()


# ── 4. THE CONCURRENCY PROOF ─────────────────────────────────────────────────


def test_two_concurrent_workers_cannot_both_credit(engine):
    """Two real connections, released together, both calling for the SAME payment.

    This is the test the brief asks for, and it is the one a `SELECT EXISTS` guard would
    fail: both workers would pass the existence check before either inserted. Only the
    unique index makes the decision atomic.

    Uses raw psycopg2 connections rather than the SQLAlchemy engine so that each worker
    genuinely has its own session and its own transaction, and a `threading.Barrier`
    so they are released at the same instant rather than merely started together.
    """
    # Seed in committed state so both workers can see it, then clean up explicitly.
    setup = psycopg2.connect(TEST_DATABASE_URL)
    setup.autocommit = True
    referrer = str(uuid.uuid4())
    referred = str(uuid.uuid4())
    payment_id = f"pay_race_{uuid.uuid4().hex[:12]}"
    try:
        with setup.cursor() as cur:
            for ident, label in ((referrer, "referrer"), (referred, "referred")):
                cur.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                    (ident, f"race-{label}-{ident[:8]}@example.invalid"),
                )
                cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (ident,))
            cur.execute(
                "INSERT INTO public.referral_codes (user_id, code) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET code = public.referral_codes.code "
                "RETURNING id",
                (referrer, f"RACE{referrer[:8].upper()}"),
            )
            code_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.referral_relationships "
                "(referrer_id, referred_id, referral_code_id, status) "
                "VALUES (%s, %s, %s, 'active')",
                (referrer, referred, code_id),
            )

        barrier = threading.Barrier(2)
        results: list = []
        errors: list = []

        def worker():
            conn = psycopg2.connect(TEST_DATABASE_URL)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    barrier.wait(timeout=15)
                    cur.execute(
                        "SELECT public.process_referral_commission(%s, %s, 'pro', 100.00)",
                        (referred, payment_id),
                    )
                    results.append(cur.fetchone()[0])
            except Exception as exc:  # noqa: BLE001 - recorded, then asserted on
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Whatever happened at the application layer, the DATABASE must show one credit.
        with setup.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.referral_commissions WHERE payment_id = %s",
                (payment_id,),
            )
            commission_rows = cur.fetchone()[0]
            cur.execute(
                "SELECT coalesce(pending_balance_usd, 0) FROM public.referral_wallets "
                " WHERE user_id = %s",
                (referrer,),
            )
            wallet_row = cur.fetchone()
            wallet = float(wallet_row[0]) if wallet_row else 0.0

        assert commission_rows == 1, (
            f"{commission_rows} commission rows were created for one payment by two "
            f"concurrent workers. The economic invariant is broken. "
            f"(returned={results}, errors={[str(e) for e in errors]})"
        )
        assert wallet == pytest.approx(100.00 * COMMISSION_RATE), (
            f"the wallet holds {wallet}, expected {100.00 * COMMISSION_RATE}. Two "
            f"concurrent workers moved the balance twice even though only one "
            f"commission row exists — the wallet update is not inside the insert guard."
        )
        granted = [r for r in results if r is not None]
        assert len(granted) <= 1, (
            f"both workers believed they created a commission: {results}"
        )
    finally:
        with setup.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_commissions WHERE payment_id = %s",
                (payment_id,),
            )
            cur.execute("DELETE FROM public.referral_wallets WHERE user_id = %s", (referrer,))
            cur.execute(
                "DELETE FROM public.referral_relationships WHERE referred_id = %s",
                (referred,),
            )
            cur.execute("DELETE FROM public.referral_codes WHERE user_id = %s", (referrer,))
            for ident in (referrer, referred):
                cur.execute("DELETE FROM public.profiles WHERE id = %s", (ident,))
                cur.execute("DELETE FROM auth.users WHERE id = %s", (ident,))
        setup.close()
