"""BILL-07 — the billing-history ledger must be writable, unique per charge, and findable.

WHAT THE FINDING WAS
====================
`public.billing_invoices` is the table behind `GET /api/billing/invoices`. Its only
writer named five columns the schema never declared:

    sb_inv.table("billing_invoices").insert({            billing.py
        "provider": ..., "provider_payment_id": ...,
        "amount_usd": ..., "amount_inr": ..., "plan": ...,
    })

PostgREST rejects the entire statement on an undefined column, and the call sat inside
`try/except ... logger.warning`. So the write failed on EVERY captured payment and said
so only in a log line. Measured read-only against production and against a cluster
provisioned from repository source — the two agreed exactly: 11 columns present, 5 of
the 9 the insert sends missing, and **zero invoice rows in production**.

The repository had already found it. All five sat in
`tests/test_schema_table_reference_drift.py::KNOWN_UNDECLARED_COLUMN_DEFECTS`, and
`.kiro/specs/production-launch-hardening/tasks.md` states the consequence outright —
"no invoice row has ever been written". Both deferred it because `routers/billing.py`
carried a parallel pricing workstream's uncommitted changes. That workstream landed, so
the reason expired.

THE SECOND CONSEQUENCE, which the empty billing page hides
==========================================================
`_revoke_plan_for_refunded_razorpay_payment` finds the purchase a refund relates to with

    .select("user_id,plan").eq("provider", ...).eq("provider_payment_id", ...)

Three of the same five names — and unlike the insert, that read is NOT wrapped. So the
refund webhook raised, Razorpay redelivered into the same failure, and **a fully
refunded customer kept the plan they were refunded for**. This file asserts that filter
returns the row, because that is the property the revocation depends on.

HOW THIS FILE MEASURES
======================
* **Real PostgreSQL, real constraints.** Every claim migration 070 makes is read back
  from the database rather than inferred from the DDL text.
* **The webhook's exact payload.** `test_the_webhook_payload_inserts` sends the same
  nine keys `razorpay_webhook` sends, so a future column rename breaks this test rather
  than silently re-breaking the ledger.
* **An independent connection is the oracle** for "it was recorded", so the claim means
  committed and visible elsewhere, not merely staged in one session.
* **No network, no provider credential, no live payment.** Fixture identities only.

WHAT IS NOT CLAIMED
===================
* That a lost invoice is now recoverable automatically. It is not. If the insert fails
  for a reason other than a duplicate, the handler logs CRITICAL with every field needed
  to reconstruct the row and deliberately does not raise — raising would make Razorpay
  redeliver, and `_record_plan_period` extends the paid period from its current end when
  the same plan is repurchased, so a redelivery would grant a second month for one
  payment. A reconciliation job is not in this change.
* That `amount` (migration 006's column) is populated. It never was, and this change does
  not start: the writer uses the denominated columns.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

# ── explicit PostgreSQL requirement ──────────────────────────────────────────
#
# Same gate as tests/test_money06_*.py and tests/test_trade01_*.py, and deliberately
# with no fallback to a local DSN file: a fallback would make this run in both lanes and
# stop the CI-comparable lane representing a clean runner. A skip here is NOT a pass —
# it means the billing ledger is unverified.
_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")
pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the billing ledger is unverified."
    ),
)

#: The nine keys `razorpay_webhook` actually sends. Kept as data so the test fails on a
#: rename instead of drifting away from the handler it is meant to pin.
WEBHOOK_INSERT_COLUMNS = (
    "user_id", "provider", "provider_payment_id", "amount_usd",
    "amount_inr", "currency", "status", "plan", "created_at",
)

#: The five that migration 070 declares.
COLUMNS_070_DECLARES = (
    "provider", "provider_payment_id", "amount_usd", "amount_inr", "plan",
)


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


@pytest.fixture
def invoice_owner():
    """A profile that can own an invoice row.

    `billing_invoices.user_id` carries `fk_bug049_billing_invoices_user_id` to
    `profiles(id)`, which in turn requires `auth.users(id)`, so both legs are created.
    Cleaned up afterwards, children first.
    """
    uid = str(uuid.uuid4())
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
    cur.execute("INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
    try:
        yield uid
    finally:
        cur.execute("DELETE FROM public.billing_invoices WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM public.profiles WHERE id = %s", (uid,))
        cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        conn.close()


def _insert_capture(cur, user_id, payment_id, amount=Decimal("2499.00"), plan="pro"):
    """Insert exactly what the webhook inserts."""
    cur.execute(
        """
        INSERT INTO public.billing_invoices
            (user_id, provider, provider_payment_id, amount_usd, amount_inr,
             currency, status, plan, created_at)
        VALUES (%s, 'razorpay', %s, 0, %s, 'INR', 'paid', %s, now())
        """,
        (user_id, payment_id, amount, plan),
    )


# ═════════════════════════════════════════════════════════════════════════════
# The columns exist at all
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheLedgerIsWritable:
    def test_all_five_columns_are_declared(self):
        """The defect was five undeclared names. Assert each by name."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'billing_invoices'
            """
        )
        present = {r[0] for r in cur.fetchall()}
        conn.close()
        missing = [c for c in COLUMNS_070_DECLARES if c not in present]
        assert not missing, (
            f"billing_invoices is missing {missing}. The webhook writer names them, so "
            "the invoice write fails on every captured payment and says so only in a "
            "log line -- the BILL-07 defect, reopened."
        )

    def test_every_column_the_webhook_sends_resolves(self):
        """Not just the five: the whole payload must be insertable."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'billing_invoices'
            """
        )
        present = {r[0] for r in cur.fetchall()}
        conn.close()
        missing = [c for c in WEBHOOK_INSERT_COLUMNS if c not in present]
        assert not missing, (
            f"the webhook's insert payload names {missing}, which billing_invoices does "
            "not declare. PostgREST rejects the whole statement on one undefined column."
        )

    def test_the_money_columns_are_exact_not_float(self):
        """A binary float money column would reintroduce what MONEY-02 removed."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'billing_invoices'
               AND column_name IN ('amount_usd', 'amount_inr')
            """
        )
        types = dict(cur.fetchall())
        conn.close()
        assert types, "neither amount column is declared"
        for col, dtype in types.items():
            assert dtype == "numeric", (
                f"billing_invoices.{col} is {dtype}, not numeric. Binary float on a "
                "money amount is the defect MONEY-02 removed from the arithmetic; a "
                "float column would put it back at the storage layer."
            )

    def test_the_webhook_payload_inserts_and_round_trips(self, invoice_owner):
        """The end state the whole finding is about: a row exists afterwards."""
        payment = f"pay_{uuid.uuid4().hex[:18]}"
        conn = _conn()
        cur = conn.cursor()
        _insert_capture(cur, invoice_owner, payment, Decimal("2499.00"), "pro")
        conn.close()

        # Read back over a DIFFERENT connection, so "recorded" means committed.
        oracle = _conn()
        ocur = oracle.cursor()
        ocur.execute(
            """
            SELECT provider, provider_payment_id, amount_inr, currency, status, plan
              FROM public.billing_invoices WHERE user_id = %s
            """,
            (invoice_owner,),
        )
        rows = ocur.fetchall()
        oracle.close()

        assert len(rows) == 1, f"expected exactly one invoice row, got {len(rows)}"
        provider, pid, amount_inr, currency, status, plan = rows[0]
        assert provider == "razorpay"
        assert pid == payment
        assert amount_inr == Decimal("2499.00"), (
            f"amount came back as {amount_inr!r}; the captured figure must survive "
            "storage exactly, not approximately"
        )
        assert (currency, status, plan) == ("INR", "paid", "pro")


# ═════════════════════════════════════════════════════════════════════════════
# One charge, one invoice
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestOneChargeFilesOneInvoice:
    def test_a_redelivery_of_the_same_payment_is_refused(self, invoice_owner):
        """Both providers redeliver for days, and an operator can resend by hand.

        Without uniqueness each redelivery files another invoice for one charge, and the
        customer's billing history shows a payment they made once as several.
        """
        import psycopg2

        payment = f"pay_{uuid.uuid4().hex[:18]}"
        conn = _conn()
        cur = conn.cursor()
        _insert_capture(cur, invoice_owner, payment)

        with pytest.raises(psycopg2.IntegrityError) as caught:
            _insert_capture(cur, invoice_owner, payment)
        conn.close()

        assert caught.value.pgcode == "23505", (
            f"the duplicate was refused with SQLSTATE {caught.value.pgcode}, not 23505. "
            "billing.py::_is_duplicate_invoice matches 23505 to tell 'already on file' "
            "from a real write failure; another code would be logged as a lost payment."
        )

    def test_the_same_payment_id_under_a_different_provider_is_allowed(self, invoice_owner):
        """Uniqueness is per provider. Two gateways may mint the same id string."""
        payment = f"pay_{uuid.uuid4().hex[:18]}"
        conn = _conn()
        cur = conn.cursor()
        _insert_capture(cur, invoice_owner, payment)
        cur.execute(
            """
            INSERT INTO public.billing_invoices
                (user_id, provider, provider_payment_id, currency, status, plan)
            VALUES (%s, 'someotherprovider', %s, 'INR', 'paid', 'pro')
            """,
            (invoice_owner, payment),
        )
        cur.execute(
            "SELECT count(*) FROM public.billing_invoices WHERE provider_payment_id = %s",
            (payment,),
        )
        assert cur.fetchone()[0] == 2
        conn.close()

    def test_rows_with_no_payment_id_do_not_block_each_other(self, invoice_owner):
        """NULL is the honest unknown and must not collide with itself.

        The handler now sends None rather than "" when a capture carries no id. The
        empty string is a real value that identifies nothing: it would satisfy the unique
        index and so let the FIRST id-less capture block every later one.
        """
        conn = _conn()
        cur = conn.cursor()
        for _ in range(2):
            cur.execute(
                """
                INSERT INTO public.billing_invoices
                    (user_id, provider, provider_payment_id, currency, status)
                VALUES (%s, 'razorpay', NULL, 'INR', 'paid')
                """,
                (invoice_owner,),
            )
        cur.execute(
            "SELECT count(*) FROM public.billing_invoices"
            " WHERE user_id = %s AND provider_payment_id IS NULL",
            (invoice_owner,),
        )
        assert cur.fetchone()[0] == 2, (
            "two id-less captures must coexist; the unique index is partial precisely "
            "so an unknown id is not treated as a shared one"
        )
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# The refund path can find what it needs
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestARefundCanFindItsPurchase:
    def test_the_revocation_filter_returns_the_row(self, invoice_owner):
        """Pins `_revoke_plan_for_refunded_razorpay_payment`'s exact query.

        It reads `user_id, plan` filtered by `provider` and `provider_payment_id` — all
        three were undeclared, and that read is not wrapped, so the refund webhook raised
        and a refunded customer kept their plan.
        """
        payment = f"pay_{uuid.uuid4().hex[:18]}"
        conn = _conn()
        cur = conn.cursor()
        _insert_capture(cur, invoice_owner, payment, plan="pro")

        cur.execute(
            """
            SELECT user_id, plan FROM public.billing_invoices
             WHERE provider = 'razorpay' AND provider_payment_id = %s LIMIT 1
            """,
            (payment,),
        )
        found = cur.fetchone()
        conn.close()

        assert found is not None, (
            "the refund path cannot locate the purchase, so a full refund cannot revoke "
            "the plan it paid for"
        )
        assert str(found[0]) == invoice_owner
        assert found[1] == "pro", (
            "plan must be readable: the revocation is conditional on the buyer still "
            "being on the plan that payment bought, so an old refund never undoes a "
            "later upgrade"
        )

    def test_the_revocation_filter_is_index_backed(self):
        """A per-payment lookup on a growing ledger must not be a sequential scan."""
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
             WHERE schemaname = 'public' AND tablename = 'billing_invoices'
               AND indexname = 'uq_billing_invoices_provider_payment'
            """
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None, (
            "uq_billing_invoices_provider_payment is absent. It carries both duties: "
            "de-duplicating a redelivery, and serving the refund lookup."
        )
        assert "provider" in row[0] and "provider_payment_id" in row[0]


# ═════════════════════════════════════════════════════════════════════════════
# The constraints refuse what they claim to refuse
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheLedgerRefusesNonsense:
    @pytest.mark.parametrize(
        "label,columns,values",
        [
            ("blank provider_payment_id",
             "(user_id, provider, provider_payment_id, currency, status)",
             ("razorpay", "   ", "INR", "paid")),
            ("blank provider",
             "(user_id, provider, provider_payment_id, currency, status)",
             ("  ", "pay_x", "INR", "paid")),
            ("blank plan",
             "(user_id, plan, currency, status)",
             ("", "INR", "paid")),
        ],
    )
    def test_blank_is_refused_the_same_as_missing(self, invoice_owner, label, columns, values):
        """A blank string is a real value that identifies nothing."""
        import psycopg2

        placeholders = ", ".join(["%s"] * (len(values) + 1))
        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.IntegrityError) as caught:
            cur.execute(
                f"INSERT INTO public.billing_invoices {columns} VALUES ({placeholders})",
                (invoice_owner, *values),
            )
        conn.close()
        assert caught.value.pgcode == "23514", (
            f"{label} was refused with {caught.value.pgcode}, expected a CHECK "
            "violation (23514)"
        )

    @pytest.mark.parametrize("column", ["amount_usd", "amount_inr"])
    def test_a_negative_capture_is_refused(self, invoice_owner, column):
        """A negative capture is not a refund, it is a bad write."""
        import psycopg2

        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.IntegrityError) as caught:
            cur.execute(
                f"""
                INSERT INTO public.billing_invoices
                    (user_id, provider, provider_payment_id, {column}, currency, status)
                VALUES (%s, 'razorpay', %s, -1, 'INR', 'paid')
                """,
                (invoice_owner, f"pay_{uuid.uuid4().hex[:12]}"),
            )
        conn.close()
        assert caught.value.pgcode == "23514"

    def test_an_unknown_amount_stays_null_rather_than_zero(self, invoice_owner):
        """NULL must remain legal. A `DEFAULT 0` would assert a zero-value payment.

        This is the same rule the reading endpoint already follows for `provider` — "a
        null is honest where a fabricated 'razorpay' would not be".
        """
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.billing_invoices
                (user_id, provider, provider_payment_id, currency, status)
            VALUES (%s, 'razorpay', %s, 'INR', 'paid')
            """,
            (invoice_owner, f"pay_{uuid.uuid4().hex[:12]}"),
        )
        cur.execute(
            "SELECT amount_inr, amount_usd FROM public.billing_invoices WHERE user_id = %s",
            (invoice_owner,),
        )
        amount_inr, amount_usd = cur.fetchone()
        conn.close()
        assert amount_inr is None and amount_usd is None, (
            "an unrecorded amount came back as a number. A zero on a row whose amount is "
            "unknown asserts the payment was zero."
        )


# ═════════════════════════════════════════════════════════════════════════════
# The handler's duplicate detection matches what Postgres actually raises
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheHandlerRecognisesTheCollision:
    def test_is_duplicate_invoice_matches_a_real_violation(self, invoice_owner):
        """Connect the database's behaviour to the branch that depends on it.

        The two could drift apart silently: the index could raise a code the handler does
        not match, and a correctly de-duplicated redelivery would then be logged at
        CRITICAL as a lost payment. So the REAL exception is fed to the REAL predicate.
        """
        import psycopg2

        from backend_app.routers.billing import _is_duplicate_invoice

        payment = f"pay_{uuid.uuid4().hex[:18]}"
        conn = _conn()
        cur = conn.cursor()
        _insert_capture(cur, invoice_owner, payment)
        try:
            _insert_capture(cur, invoice_owner, payment)
            pytest.fail("the duplicate was accepted")
        except psycopg2.IntegrityError as exc:
            assert _is_duplicate_invoice(exc), (
                "_is_duplicate_invoice did not recognise a real unique violation from "
                f"the index: pgcode={exc.pgcode!r}. The handler would log this "
                "correctly-de-duplicated redelivery as an unrecorded payment."
            )
        finally:
            conn.close()

    def test_is_duplicate_invoice_does_not_swallow_other_failures(self):
        """Narrow on purpose: misreading a real error as 'already recorded' loses money.

        A payment missing from the ledger with nothing logged at CRITICAL is strictly
        worse than a noisy log line.
        """
        from backend_app.routers.billing import _is_duplicate_invoice

        for other in (
            Exception("connection refused"),
            Exception("permission denied for table billing_invoices"),
            Exception('null value in column "user_id" violates not-null constraint'),
            Exception("23514 check constraint violated"),
        ):
            assert not _is_duplicate_invoice(other), (
                f"{other!r} was treated as a duplicate; it is a real failure and must "
                "reach the CRITICAL branch"
            )
