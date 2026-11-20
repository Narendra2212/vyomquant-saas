"""MONEY-04 — the referral ledger declares its currency, and the database enforces it.

WHAT MONEY-04 ACTUALLY WAS, AND WHY THIS FILE IS NOT THE FIX FOR IT
-------------------------------------------------------------------
MONEY-04 was carried as an open P0: a Razorpay payment captured in paise credited as
though it were dollars, overstating a referral commission by roughly the USD/INR rate
(~83x). Re-examined from the code rather than from the earlier report, **that defect does
not exist in the current tree**:

* `backend_app/routers/billing.py:2562-2608` is an explicit refusal block. The Razorpay
  webhook branch does not call `process_referral_commission` at all — it logs a skip
  naming `payment_id`, `payment_amount`, `user_id` and `item_key`.
* The only surviving commission credit is the Stripe one at `billing.py:2216-2223`, whose
  amount comes from `_provider_major_amount(..., "USD")`.
* `tests/test_money02_billing_amount_precision.py` already pins that invariant by AST:
  exactly one `process_referral_commission` call site, in `stripe_webhook`, USD-scoped.

So the open status was stale documentation, not an unresolved defect. That reconciliation
is recorded in `staging/MONEY_FLOW_AUDIT.md` and `staging/REMEDIATION_REPORT.md` with the
code and test evidence, rather than closed on the strength of the earlier report.
:func:`test_the_razorpay_branch_still_refuses_rather_than_credits` below is the code
evidence, asserted here beside the database evidence so the two cannot drift apart.

WHAT WAS STILL GENUINELY OPEN, AND IS WHAT THIS FILE PROVES
------------------------------------------------------------
`referral_commissions`, `referral_wallets` and `referral_payouts` carried **no currency
column at all**. The unit lived only in column NAMES (`payment_amount_usd`,
`commission_amount_usd`, `pending_balance_usd`, `amount_usd`, …), which is a convention
enforced by nothing, and the USD-only invariant was asserted only by an AST scan of ONE
module. A credit site added from any other module would have satisfied every test in the
repository.

`backend_app/migrations/027_referral_ledger_currency.sql` adds
`currency TEXT NOT NULL DEFAULT 'USD'` plus `CHECK (currency = 'USD')` to all three. This
file proves, against a real server:

* the column and the constraint exist with the shape claimed, read from the catalogues;
* an explicit non-USD credit is refused **by the database**, on insert and on update;
* a credit through the installed function lands with a defined currency;
* duplicate delivery, provider retries, two genuinely concurrent connections and a rolled
  back transaction all leave the ledger with one correctly-denominated credit;
* re-applying 027 changes nothing.

WHAT IS NOT CLAIMED
-------------------
The constraint cannot detect an amount that is in the wrong unit but *declared* as USD. A
writer that puts an INR figure into `commission_amount_usd` without naming `currency`
gets the default and succeeds. No database-level constraint can tell "20.00 USD" from
"20.00 rupees labelled USD". :func:`test_the_constraint_cannot_catch_a_mislabelled_amount`
asserts that limit explicitly rather than leaving a reader to assume more coverage than
exists. The residual is covered for the one provider that can produce it by the refusal
block in `billing.py`, and is recorded as MONEY-06 (Razorpay referral commissions are
never credited, so settlement is manual and auditable only through logs).

LOCAL POSTGRESQL EVIDENCE. Production was never connected to in any phase of this audit.
"""

from __future__ import annotations

import os
import pathlib
import threading
import uuid
from decimal import Decimal

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "the invariant is a NOT NULL column with a CHECK constraint and the proof needs "
        "real transactions and two real connections. SQLite has neither CHECK "
        "enforcement on this shape nor role-aware catalogues. NOT a pass."
    ),
)

sa = pytest.importorskip("sqlalchemy")
psycopg2 = pytest.importorskip("psycopg2")
from sqlalchemy import create_engine, text  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = _REPO_ROOT / "backend_app" / "migrations" / "027_referral_ledger_currency.sql"

#: The three tables 027 covers. Parametrised rather than written out three times so a
#: fourth ledger table added later is a one-line change here.
LEDGER_TABLES = ("referral_commissions", "referral_wallets", "referral_payouts")

COMMISSION_RATE = Decimal("0.20")
PAYMENT_USD = Decimal("100.00")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed(cur, tag: str):
    """A referrer, a referred user and an active relationship, COMMITTED.

    Committed rather than held in a transaction because the concurrency test needs two
    separate connections to see the same relationship. Cleaned up by the fixture.
    """
    referrer = str(uuid.uuid4())
    referred = str(uuid.uuid4())
    for ident, label in ((referrer, "referrer"), (referred, "referred")):
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
            (ident, f"money04-{tag}-{label}-{ident[:8]}@example.invalid"),
        )
        cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (ident,))

    # Creating a profile already provisions a referral code (uq_referral_codes_user), so
    # this is fetch-or-create. Same discovery as
    # tests/test_money01_referral_commission_idempotence_postgres.py::_seed_referral_pair.
    cur.execute(
        "INSERT INTO public.referral_codes (user_id, code) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET code = public.referral_codes.code "
        "RETURNING id",
        (referrer, f"M04{referrer[:9].upper()}"),
    )
    code_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO public.referral_relationships "
        "(referrer_id, referred_id, referral_code_id, status) "
        "VALUES (%s, %s, %s, 'active')",
        (referrer, referred, code_id),
    )
    return referrer, referred


@pytest.fixture
def pair():
    """A committed referral pair plus a unique payment id, torn down afterwards."""
    conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    tag = uuid.uuid4().hex[:8]
    with conn.cursor() as cur:
        referrer, referred = _seed(cur, tag)
    payment_id = f"pay_m04_{tag}"
    try:
        yield {
            "conn": conn,
            "referrer": referrer,
            "referred": referred,
            "payment_id": payment_id,
        }
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.referral_commissions WHERE referrer_id = %s", (referrer,)
            )
            cur.execute("DELETE FROM public.referral_payouts WHERE user_id = %s", (referrer,))
            cur.execute("DELETE FROM public.referral_wallets WHERE user_id = %s", (referrer,))
            cur.execute(
                "DELETE FROM public.referral_relationships WHERE referred_id = %s", (referred,)
            )
            cur.execute("DELETE FROM public.referral_codes WHERE user_id = %s", (referrer,))
            for ident in (referrer, referred):
                cur.execute("DELETE FROM public.profiles WHERE id = %s", (ident,))
                cur.execute("DELETE FROM auth.users WHERE id = %s", (ident,))
        conn.close()


def _commission_rows(cur, payment_id):
    cur.execute(
        "SELECT currency, payment_amount_usd, commission_amount_usd "
        "  FROM public.referral_commissions WHERE payment_id = %s",
        (payment_id,),
    )
    return cur.fetchall()


def _wallet(cur, referrer):
    cur.execute(
        "SELECT currency, coalesce(pending_balance_usd, 0) "
        "  FROM public.referral_wallets WHERE user_id = %s",
        (referrer,),
    )
    return cur.fetchone()


# ══════════════════════════════════════════════════════════════════════════
#  1. THE INVARIANT EXISTS, READ FROM THE CATALOGUES
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("table", LEDGER_TABLES)
def test_the_currency_column_has_the_declared_shape(engine, table):
    """Not "a currency column exists" — the exact shape 027 claims.

    A nullable column would let an amount be stored with no declared unit, which is the
    finding. A missing default would break the two installed ledger functions, neither of
    which names the column. Both are asserted, from `information_schema`, not inferred
    from an application response.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT data_type, is_nullable, column_default "
                "  FROM information_schema.columns "
                " WHERE table_schema='public' AND table_name=:t AND column_name='currency'"
            ),
            {"t": table},
        ).first()

    assert row is not None, (
        f"public.{table} has no currency column. Migration 027 has not been applied to "
        f"this database, so the amounts in it carry no declared unit. This is MONEY-04's "
        f"residual."
    )
    data_type, is_nullable, column_default = row
    assert data_type == "text", f"{table}.currency is {data_type}, not text"
    assert is_nullable == "NO", (
        f"{table}.currency is nullable, so a row can still store an amount with no "
        f"declared currency"
    )
    assert column_default is not None and "USD" in column_default, (
        f"{table}.currency default is {column_default!r}. The two installed ledger "
        f"functions insert without naming the column and would fail without it."
    )


@pytest.mark.parametrize("table", LEDGER_TABLES)
def test_a_check_constraint_pins_the_currency(engine, table):
    """The column without the constraint is documentation a writer can contradict."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
                " WHERE conrelid = to_regclass(:t) AND contype = 'c' "
                "   AND pg_get_constraintdef(oid) ILIKE '%currency%'"
            ),
            {"t": f"public.{table}"},
        ).fetchall()

    assert rows, f"public.{table} has a currency column but nothing constrains it"
    definitions = " ".join(r.def_ if hasattr(r, "def_") else r[1] for r in rows)
    assert "USD" in definitions, (
        f"the currency constraint(s) on {table} do not mention USD: {definitions}"
    )
    # Deliberately NOT a multi-currency IN-list. Permitting INR with no defined
    # conversion is how MONEY-04 was reported in the first place.
    assert "INR" not in definitions.upper(), (
        f"the currency constraint on {table} admits INR: {definitions}. This ledger "
        f"performs no FX and has no defined rate, so admitting a second currency without "
        f"deciding how balances are denominated reopens MONEY-04."
    )


@pytest.mark.parametrize("table", LEDGER_TABLES)
def test_every_money_column_is_exact_not_binary_float(engine, table):
    """"A defined unit" also means the amount is not stored as a binary float.

    `double precision` cannot represent 0.01 exactly, so a ledger column of that type has
    no exact unit regardless of what `currency` says.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                " WHERE table_schema='public' AND table_name=:t "
                "   AND (column_name LIKE '%usd%' OR column_name LIKE 'amount%' "
                "        OR column_name LIKE '%balance%' OR column_name LIKE '%earnings%')"
            ),
            {"t": table},
        ).fetchall()

    assert rows, f"no money-shaped column found on {table}; the filter below is wrong"
    offenders = [(c, d) for c, d in rows if d in ("double precision", "real")]
    assert not offenders, (
        f"{table} stores money in binary floating point: {offenders}. 0.01 has no exact "
        f"representation there, so the amount has no exact unit."
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. CURRENCIES ARE NOT INTERCHANGEABLE — THE DATABASE REFUSES
# ══════════════════════════════════════════════════════════════════════════


def test_an_explicit_non_usd_payout_is_refused_by_the_database(pair):
    """The PostgREST-written table, refused at the server.

    `backend_app/routers/referral.py:770-779` inserts here from application code. A
    future caller that passes a currency is refused rather than silently recorded.
    """
    conn = pair["conn"]
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.CheckViolation) as exc:
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, payout_method, currency) "
                "VALUES (%s, %s, 'bank', 'INR')",
                (pair["referrer"], "500.00"),
            )
        assert "currency" in str(exc.value).lower()


def test_the_same_payout_in_usd_succeeds(pair):
    """Negative control for the test above: only the currency differed.

    Without this, a CheckViolation could come from any other constraint on the table and
    the previous test would still pass.
    """
    conn = pair["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.referral_payouts "
            "(user_id, amount_usd, payout_method, currency) "
            "VALUES (%s, %s, 'bank', 'USD') RETURNING currency",
            (pair["referrer"], "500.00"),
        )
        assert cur.fetchone()[0] == "USD"


def test_a_payout_written_without_naming_currency_comes_out_usd(pair):
    """The exact shape `routers/referral.py` writes — no `currency` key.

    This is why 027 uses a DEFAULT rather than a bare NOT NULL: the application's own
    insert does not name the column, and a bare NOT NULL would break the payout path.
    """
    conn = pair["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.referral_payouts "
            "(user_id, amount_usd, payout_method, payout_details, status) "
            "VALUES (%s, %s, 'bank', '{}'::jsonb, 'pending') RETURNING currency",
            (pair["referrer"], "250.00"),
        )
        assert cur.fetchone()[0] == "USD", (
            "a payout inserted the way the application inserts it did not land with a "
            "declared currency"
        )


@pytest.mark.parametrize("table", LEDGER_TABLES)
def test_an_update_cannot_relabel_a_stored_amount(engine, pair, table):
    """A CHECK applies to UPDATE too, which is the case that matters for a ledger.

    Relabelling an existing balance is how a currency becomes "interchangeable" after the
    fact. Exercised on a row that genuinely exists for each table.
    """
    conn = pair["conn"]
    referrer, referred, payment_id = pair["referrer"], pair["referred"], pair["payment_id"]

    with conn.cursor() as cur:
        # Create one real row in `table`, by the route that table is actually written by.
        if table == "referral_commissions":
            cur.execute(
                "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
                (referred, payment_id, str(PAYMENT_USD)),
            )
            where = ("payment_id = %s", (payment_id,))
        elif table == "referral_wallets":
            cur.execute(
                "INSERT INTO public.referral_wallets (user_id) VALUES (%s) "
                "ON CONFLICT (user_id) DO NOTHING",
                (referrer,),
            )
            where = ("user_id = %s", (referrer,))
        else:
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, payout_method) VALUES (%s, '10.00', 'bank')",
                (referrer,),
            )
            where = ("user_id = %s", (referrer,))

        cur.execute(f'SELECT count(*) FROM public."{table}" WHERE {where[0]}', where[1])
        assert cur.fetchone()[0] >= 1, (
            f"no row was created in {table}, so the UPDATE below would affect 0 rows and "
            f"pass vacuously"
        )

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                f'UPDATE public."{table}" SET currency = \'INR\' WHERE {where[0]}', where[1]
            )


# ══════════════════════════════════════════════════════════════════════════
#  3. A CREDIT THROUGH THE REAL FUNCTION IS DENOMINATED
# ══════════════════════════════════════════════════════════════════════════


def test_a_credit_lands_with_a_defined_currency_on_both_sides(pair):
    """Commission row AND wallet row. A credit with a denominated ledger entry and an
    undenominated balance would still leave the balance ambiguous."""
    conn = pair["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
            (pair["referred"], pair["payment_id"], str(PAYMENT_USD)),
        )
        rows = _commission_rows(cur, pair["payment_id"])
        assert len(rows) == 1, f"expected one commission row, got {len(rows)}"
        currency, payment_amount, commission_amount = rows[0]
        assert currency == "USD"
        assert payment_amount == PAYMENT_USD
        assert commission_amount == PAYMENT_USD * COMMISSION_RATE, (
            f"commission {commission_amount} is not {COMMISSION_RATE} of {payment_amount}"
        )

        wallet = _wallet(cur, pair["referrer"])
        assert wallet is not None, "the credit created no wallet row"
        assert wallet[0] == "USD", f"the wallet balance carries currency {wallet[0]!r}"
        assert wallet[1] == PAYMENT_USD * COMMISSION_RATE


# ══════════════════════════════════════════════════════════════════════════
#  4. DUPLICATE DELIVERY, RETRIES, ROLLBACK, CONCURRENCY
# ══════════════════════════════════════════════════════════════════════════


def test_duplicate_delivery_credits_once_and_stays_denominated(pair):
    """The same webhook delivered twice. MONEY-01's invariant, re-checked with currency."""
    conn = pair["conn"]
    with conn.cursor() as cur:
        for _ in range(2):
            cur.execute(
                "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
                (pair["referred"], pair["payment_id"], str(PAYMENT_USD)),
            )
        rows = _commission_rows(cur, pair["payment_id"])
        assert len(rows) == 1, (
            f"duplicate delivery created {len(rows)} commission rows for one payment"
        )
        assert rows[0][0] == "USD"
        assert _wallet(cur, pair["referrer"])[1] == PAYMENT_USD * COMMISSION_RATE, (
            "the wallet moved twice for one payment"
        )


def test_five_provider_retries_credit_once(pair):
    """A provider that retries until it gets a 2xx. Five sequential deliveries."""
    conn = pair["conn"]
    with conn.cursor() as cur:
        for _ in range(5):
            cur.execute(
                "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
                (pair["referred"], pair["payment_id"], str(PAYMENT_USD)),
            )
        rows = _commission_rows(cur, pair["payment_id"])
        assert len(rows) == 1, f"five retries produced {len(rows)} commission rows"
        currency, _, _ = rows[0]
        assert currency == "USD"
        assert _wallet(cur, pair["referrer"])[1] == PAYMENT_USD * COMMISSION_RATE


def test_a_rolled_back_transaction_credits_nothing(pair):
    """A webhook handler that fails after the credit must leave no balance behind.

    Uses a second, NON-autocommit connection so the rollback is a real transaction
    rollback rather than a savepoint inside the fixture's connection.
    """
    referred, referrer, payment_id = pair["referred"], pair["referrer"], pair["payment_id"]

    work = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
    try:
        with work.cursor() as cur:
            cur.execute(
                "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
                (referred, payment_id, str(PAYMENT_USD)),
            )
            # Visible inside the transaction...
            assert len(_commission_rows(cur, payment_id)) == 1, (
                "the credit did not happen at all, so the rollback below proves nothing"
            )
            assert _wallet(cur, referrer)[0] == "USD"
        work.rollback()
    finally:
        work.close()

    # ...and gone from every other session afterwards.
    with pair["conn"].cursor() as cur:
        assert _commission_rows(cur, payment_id) == [], (
            "a commission row survived a rolled back transaction"
        )
        wallet = _wallet(cur, referrer)
        assert wallet is None or wallet[1] == 0, (
            f"the wallet balance survived a rolled back transaction: {wallet}"
        )


def test_two_concurrent_connections_credit_once_in_one_currency(pair):
    """Two real connections released together by a barrier, same payment.

    The currency half of this matters as much as the count: a race that produced one row
    but left the wallet undenominated would still be a money defect.
    """
    referred, referrer, payment_id = pair["referred"], pair["referrer"], pair["payment_id"]

    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def worker():
        conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                barrier.wait(timeout=20)
                cur.execute(
                    "SELECT public.process_referral_commission(%s, %s, 'pro', %s)",
                    (referred, payment_id, str(PAYMENT_USD)),
                )
                results.append(cur.fetchone()[0])
        except Exception as exc:  # noqa: BLE001 — recorded, then asserted on
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    with pair["conn"].cursor() as cur:
        rows = _commission_rows(cur, payment_id)
        wallet = _wallet(cur, referrer)

    assert len(rows) == 1, (
        f"{len(rows)} commission rows for one payment from two concurrent connections. "
        f"(returned={results}, errors={[str(e) for e in errors]})"
    )
    assert rows[0][0] == "USD", f"the surviving row carries currency {rows[0][0]!r}"
    assert wallet is not None and wallet[1] == PAYMENT_USD * COMMISSION_RATE, (
        f"the wallet holds {wallet}, expected {PAYMENT_USD * COMMISSION_RATE} USD"
    )
    assert wallet[0] == "USD"
    assert len([r for r in results if r is not None]) <= 1, (
        f"both workers believed they created a commission: {results}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. THE LIMIT OF THE GUARANTEE, ASSERTED RATHER THAN ASSUMED
# ══════════════════════════════════════════════════════════════════════════


def test_the_constraint_cannot_catch_a_mislabelled_amount(pair):
    """What 027 does NOT do, pinned so no reader infers more coverage than exists.

    An INR figure written into `payment_amount_usd` without naming `currency` is accepted,
    because no constraint can distinguish it from a USD figure of the same magnitude. This
    test PASSES on that insert succeeding. It exists so the honest limit is part of the
    suite rather than a sentence in a document, and so that if a future change does make
    this detectable, this test fails and the documentation gets corrected.
    """
    conn = pair["conn"]
    with conn.cursor() as cur:
        # 8300.00 is what ~100 USD of Razorpay payment looks like in rupees. Nothing in
        # the schema can tell it from 8300 dollars.
        cur.execute(
            "INSERT INTO public.referral_commissions "
            "(referrer_id, referred_id, payment_id, subscription_tier, "
            " payment_amount_usd, commission_rate, commission_amount_usd, status) "
            "VALUES (%s, %s, %s, 'pro', 8300.00, 0.20, 1660.00, 'pending') "
            "RETURNING currency, payment_amount_usd",
            (pair["referrer"], pair["referred"], pair["payment_id"] + "_mislabelled"),
        )
        currency, amount = cur.fetchone()

    assert currency == "USD" and amount == Decimal("8300.00"), (
        "a rupee amount was stored and labelled USD. That is the documented residual of "
        "MONEY-04 and the reason the Razorpay branch of billing.py refuses to credit at "
        "all rather than converting. If this assertion now fails, the database gained a "
        "way to detect a mislabelled unit and MONEY_FLOW_AUDIT.md must be corrected."
    )


def test_the_razorpay_branch_still_refuses_rather_than_credits():
    """The code evidence for closing MONEY-04, beside the database evidence.

    `tests/test_money02_billing_amount_precision.py` owns the AST-level invariant (exactly
    one credit call site, in `stripe_webhook`). This asserts the complementary fact in the
    file that closes the finding: the Razorpay branch carries an explicit, auditable
    refusal naming the payment, so a skipped commission is recoverable rather than lost.
    """
    billing = _REPO_ROOT / "backend_app" / "routers" / "billing.py"
    source = billing.read_text(encoding="utf-8", errors="replace")

    assert "MONEY-04" in source, (
        "the MONEY-04 refusal block has been removed from routers/billing.py. If the "
        "Razorpay branch now credits referral commissions, the ~83x overstatement is back "
        "and this finding is not closed."
    )
    marker = "referral commission SKIPPED for Razorpay"
    assert marker in source, (
        f"the explicit refusal log ({marker!r}) is gone. Without it a skipped Razorpay "
        f"commission leaves no auditable trace at all."
    )
    # The refusal must name enough to settle the payment by hand.
    block_start = source.index(marker)
    block = source[block_start : block_start + 1200]
    for field in ("payment", "user", "plan"):
        assert field in block, (
            f"the refusal log does not name {field!r}, so a manual settlement could not "
            f"identify which payment to settle"
        )


# ══════════════════════════════════════════════════════════════════════════
#  6. MIGRATION REPLAY
# ══════════════════════════════════════════════════════════════════════════


def _currency_snapshot(cur):
    cur.execute(
        """
        SELECT c.table_name, c.data_type, c.is_nullable, c.column_default
          FROM information_schema.columns c
         WHERE c.table_schema = 'public' AND c.column_name = 'currency'
           AND c.table_name = ANY(%s)
         ORDER BY 1
        """,
        (list(LEDGER_TABLES),),
    )
    columns = tuple(cur.fetchall())
    cur.execute(
        """
        SELECT cl.relname, con.conname, pg_get_constraintdef(con.oid)
          FROM pg_constraint con
          JOIN pg_class cl ON cl.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = cl.relnamespace
         WHERE n.nspname = 'public' AND cl.relname = ANY(%s)
           AND con.contype = 'c' AND pg_get_constraintdef(con.oid) ILIKE '%%currency%%'
         ORDER BY 1, 2
        """,
        (list(LEDGER_TABLES),),
    )
    return columns, tuple(cur.fetchall())


def test_re_applying_027_changes_nothing():
    """Requirement 24.8's idempotency property, for this file, against a real server.

    Applied inside a savepoint and rolled back, so the test leaves the database as it
    found it.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.replace("BEGIN;", "", 1)
    index = body.rfind("COMMIT;")
    assert index != -1, "027 no longer ends with COMMIT; the replay harness assumed it did"
    body = body[:index]

    conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            before = _currency_snapshot(cur)
            cur.execute("SAVEPOINT replay")
            cur.execute(body)
            after = _currency_snapshot(cur)
            cur.execute("ROLLBACK TO SAVEPOINT replay")
        conn.rollback()
    finally:
        conn.close()

    assert after == before, (
        "re-applying 027 on an already-migrated database changed the currency columns or "
        "their constraints. Re-application must be a no-op."
    )


def _statements_only(sql: str) -> str:
    """`sql` with comments removed and string literals KEPT, upper-cased.

    Single pass, so a literal is consumed whole the moment it opens and a `--` occurring
    INSIDE one is never mistaken for a comment.

    LITERALS ARE KEPT ON PURPOSE, and that is the opposite of the choice made in
    `tests/test_tenant02_fee_records_rls_postgres.py::_executable_sql`. 027 iterates over
    three tables, so every one of its statements is built with
    `EXECUTE format('ALTER TABLE public.%I ADD COLUMN …', tbl)` — the DDL lives inside
    string literals. Blanking them leaves almost nothing to scan and a deny-list would
    pass vacuously; that is exactly what the negative controls below caught on the first
    run of this test. Same reasoning, and same shape, as
    `tests/test_marketplace_paper_migrations.py::_statements`.

    The cost of keeping literals is that a forbidden word inside a `RAISE EXCEPTION`
    message would trip the scan. 027 has none, and if one is added later this test fails
    loudly rather than silently weakening — which is the right way round.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch, nxt = sql[i], (sql[i + 1] if i + 1 < n else "")
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue
        if ch == "'":
            start = i
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(sql[start:i])
            continue
        out.append(ch)
        i += 1
    return "".join(out).upper()


def test_the_migration_is_additive_only():
    """027 adds; it never removes, renames or rewrites rows."""
    raw = MIGRATION.read_text(encoding="utf-8")
    body = _statements_only(raw)

    for forbidden in ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "TRUNCATE",
                      "DELETE FROM", "RENAME"):
        assert forbidden not in body, f"027 executes {forbidden!r}, which is not additive"

    # Negative control (a): the scan can actually see 027's statements. Without this the
    # six `not in` assertions above would pass against an empty string.
    for required in ("ADD COLUMN IF NOT EXISTS", "ADD CONSTRAINT", "CHECK (CURRENCY ="):
        assert required in body, (
            f"the comment stripper removed 027's {required!r}, so the deny-list above "
            f"scanned prose rather than SQL"
        )

    # Negative control (b): the stripper is load-bearing. 027's prose explains why it does
    # NOT use `DROP CONSTRAINT IF EXISTS`, so the raw file contains the phrase and only
    # comment-stripping keeps the deny-list from tripping on it.
    assert "DROP CONSTRAINT" in raw.upper(), (
        "027 no longer explains in prose why it avoids DROP CONSTRAINT, so this test no "
        "longer demonstrates that the comment stripper is what makes the scan pass"
    )
