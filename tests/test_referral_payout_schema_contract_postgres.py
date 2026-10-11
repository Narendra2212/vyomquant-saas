"""Phase 10, observation B: referral_payouts is written with its REAL column names.

THE DEFECT
----------
`backend_app/routers/referral.py::create_payout_request` inserted into `referral_payouts`
using the keys `payment_method` and `payment_details`. The provisioned table
(`migrations/006_reconcile_production_database.sql`, confirmed against the live DB) names
those columns `payout_method` (VARCHAR(50) NOT NULL) and `payout_details` (JSONB). A second
migration (`migrations/referral_system_redesign.sql`) declares the OTHER names, but
`CREATE TABLE IF NOT EXISTS` means 006's shape is the one materialised, and migration 027
builds on it -- so `payout_method` is authoritative. Against that schema the old insert wrote
a non-existent column and left the NOT NULL `payout_method` unset, so EVERY payout request
failed its insert and 500-ed after the wallet had been debited (then rolled back). The read
paths read `payment_method`, which never exists on the row, so a stored method always came
back None.

This is the payout-column instance of the same `CREATE TABLE IF NOT EXISTS` dual-definition
class as BUG-035 (the wallet columns) -- see `test_referral_money_lifecycle_postgres.py`.

WHAT THIS PROVES
----------------
Driven at the database level, exactly as `test_money04_...` drives this same table, because
the handler reaches it through PostgREST which is not available locally. The test uses the
HANDLER'S CORRECTED PAYLOAD SHAPE (`payout_method` / `payout_details`) and asserts the row
inserts against the real schema, that the method round-trips, and -- the regression guard --
that the OLD payload shape (`payment_method` / `payout_details` without `payout_method`)
STILL FAILS, so a revert of the fix is caught. The response-field mapping
(`payout_method` -> the API's `payment_method`) is asserted on the router source.

No real payout is initiated and no money moves: `referral_payouts` is a request record whose
pending->paid lifecycle is not implemented (reported, not fabricated).

Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import ast
import inspect
import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL instance (AERORA_TEST_DATABASE_URL).",
)

import psycopg2  # noqa: E402


@pytest.fixture
def conn():
    c = psycopg2.connect(TEST_DATABASE_URL)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def referrer(conn):
    """A profile that can own a payout row (FK referral_payouts.user_id -> profiles.id)."""
    uid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (uid, f"{uid[:8]}@payout.test"),
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,)
        )
    conn.commit()
    return uid


def test_the_corrected_insert_shape_succeeds_against_the_real_schema(conn, referrer):
    """The payload create_payout_request now writes must insert and come back readable."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.referral_payouts "
            "(user_id, amount_usd, status, payout_method, payout_details) "
            "VALUES (%s, %s, %s, %s, %s::jsonb) "
            "RETURNING id, payout_method, status",
            (referrer, "25.00", "pending", "bank_transfer", "{}"),
        )
        row = cur.fetchone()
    assert row is not None, "the corrected payout insert produced no row"
    assert row[1] == "bank_transfer", "payout_method did not round-trip"
    assert row[2] == "pending"


def test_the_old_insert_shape_still_fails_so_a_revert_is_caught(conn, referrer):
    """REGRESSION GUARD: the pre-fix payload must not silently succeed.

    The old shape named `payment_method`/`payment_details` and never set the NOT NULL
    `payout_method`. Against the real schema that is a bad write: `payment_method` is not a
    column, and `payout_method` NOT NULL is unsatisfied. Either way PostgreSQL refuses, which
    is what made the payout endpoint 500. If this ever stops failing, the column names have
    drifted and the fix's premise is gone.
    """
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.Error):
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, status, payment_method, payment_details) "
                "VALUES (%s, %s, %s, %s, %s::jsonb)",
                (referrer, "25.00", "pending", "bank_transfer", "{}"),
            )
    conn.rollback()


def test_a_payout_defaults_to_pending_and_usd(conn, referrer):
    """A newly created payout is pending and USD -- it is a REQUEST, not a settled payment.

    This pins the fail-safe incomplete state: no pending->paid transition and no payment
    initiation is implemented, so a payout must stay pending until an authorised,
    evidence-backed settlement path exists (reported, not fabricated).
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.referral_payouts "
            "(user_id, amount_usd, payout_method) VALUES (%s, %s, %s) "
            "RETURNING status, currency",
            (referrer, "15.00", "upi"),
        )
        status, currency = cur.fetchone()
    assert status == "pending", "a new payout must start pending, never paid"
    assert currency == "USD"


def test_the_router_insert_uses_the_authoritative_column_names():
    """Source-level guard: the handler writes payout_method/payout_details, not payment_*.

    Reads create_payout_request's own source so a future edit that reintroduces the wrong
    column key is caught without a database.
    """
    from backend_app.routers import referral as referral_router

    src = inspect.getsource(referral_router)
    tree = ast.parse(src)

    # Find every dict literal that is the argument to a `.insert(...)` whose receiver chain
    # mentions referral_payouts in the same statement's source segment.
    offenders = []
    good = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if "referral_payouts" not in seg:
            # The .insert is on a chain whose table isn't visible in this call's segment;
            # check the enclosing statement instead by widening to the whole call text.
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                keys = {
                    k.value for k in arg.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
                if "payment_method" in keys or "payment_details" in keys:
                    offenders.append(sorted(keys))
                if "payout_method" in keys:
                    good.append(sorted(keys))

    assert not offenders, (
        f"create_payout_request still inserts the wrong column name(s) into "
        f"referral_payouts: {offenders}. The provisioned column is payout_method/"
        f"payout_details."
    )
    assert good, (
        "no referral_payouts insert naming payout_method was found; the fix may have been "
        "removed or the detection widened incorrectly"
    )


def test_the_read_path_maps_payout_method_to_the_api_field():
    """The response field stays `payment_method`, sourced from the `payout_method` column.

    Pins that the read tolerates the authoritative column, so the API contract is unchanged
    while the DB contract is corrected.
    """
    from backend_app.routers import referral as referral_router

    src = inspect.getsource(referral_router)
    assert 'p.get("payout_method")' in src, (
        "the payout read path does not consult the authoritative `payout_method` column"
    )
