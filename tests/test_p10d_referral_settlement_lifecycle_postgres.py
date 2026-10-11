"""The referral settlement lifecycle: what exists, and what may not be asserted without it.

PHASE 10 / WORKSTREAM D / observation 3.

WHAT WAS MEASURED (staging/probe_p10d_settlement_lifecycle.py, live PostgreSQL)
------------------------------------------------------------------------------
The accrual half of the referral system is implemented and the SETTLEMENT half is not.
Three ledgers, three vocabularies, and in every one the step that actually pays the
referrer has no writer:

    referral_commissions  pending -> approved -> paid     no writer for either step
    referral_wallets      pending_balance_usd -> approved_balance_usd   no writer
    referral_payouts      pending -> processing -> completed            no writer
    referral_obligations  payable -> settled                            no writer

Reversal IS implemented and idempotent on both `referral_commissions`
(`reverse_referral_commission`) and `referral_obligations` (`reverse_referral_obligation`,
which deliberately refuses to touch an already-`settled` row).

Consequence, stated plainly: `POST /referral/payouts` validates against
`approved_balance_usd` with a $10 minimum, and nothing can move money into that column, so
no payout request can succeed. Measured scale at the time of writing
(`staging/probe_p10d_wallet_stuck_balances.py`): 37 wallets, all four balance columns
summing to 0.00, and zero rows in commissions / payouts / obligations. So this is a
CAPABILITY GAP that blocks the first payout — not money presently trapped.

WHAT THIS FILE DOES
-------------------
It does not implement settlement: that is a product decision, and the brief forbids
marking a payout paid without evidence of payment. It pins the two things that must hold
while settlement is absent:

  1. The unimplemented states cannot be reached by accident (the live CHECK refuses the
     values the API response model advertises).
  2. A terminal, money-sent state cannot be asserted with no evidence.

(2) has one open gap, carried here as a strict xfail against migration 032 rather than
silently omitted. See that test's own docstring.

No payment provider is contacted and no transfer is initiated by any test in this file.
Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL instance (AERORA_TEST_DATABASE_URL).",
)

import psycopg2  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    """A connection whose work is always rolled back."""
    c = psycopg2.connect(TEST_DATABASE_URL)
    c.autocommit = False
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def payout(conn):
    """A real pending payout row, created and rolled back."""
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (uid, f"p10d-settle-{uid[:8]}@example.invalid"),
    )
    cur.execute(
        "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (uid,)
    )
    cur.execute(
        "INSERT INTO public.referral_payouts "
        "(user_id, amount_usd, payout_method, payout_details, status) "
        "VALUES (%s, '50.00', 'bank_transfer', '{}'::jsonb, 'pending') RETURNING id",
        (uid,),
    )
    return {"cur": cur, "id": cur.fetchone()[0], "user_id": uid}


# ═════════════════════════════════════════════════════════════════════════════
#  1. Settlement is unimplemented — recorded, so implementing it is deliberate
# ═════════════════════════════════════════════════════════════════════════════

def test_no_installed_routine_writes_a_referral_settlement_state(conn):
    """Asked of `pg_proc`, not of the migration files, because what matters is what the
    database actually has installed.

    When settlement IS implemented this test fails, and whoever implements it must then
    satisfy the evidence requirements the other tests in this file pin.
    """
    cur = conn.cursor()
    cur.execute(
        "select p.proname, p.prosrc from pg_proc p "
        "join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname like '%referral%'"
    )
    settlers = []
    for name, src in cur.fetchall():
        low = (src or "").lower()
        # `reverse_*` legitimately mentions 'paid' in its predicate (it reverses a paid
        # commission); it is a reversal, not a settlement, so it is excluded by name.
        if name.startswith("reverse_"):
            continue
        if "settled_at" in low or "'settled'" in low or "'completed'" in low \
                or "approved_balance_usd =" in low:
            settlers.append(name)

    assert settlers == [], (
        f"a referral settlement writer now exists ({settlers}). Settlement was "
        f"unimplemented when this was measured. Confirm it records evidence of payment "
        f"(provider reference + timestamp), is idempotent under webhook replay, and "
        f"cannot move a payout to a terminal state without that evidence — then update "
        f"this test."
    )


def test_the_approved_balance_has_no_writer_so_payouts_cannot_be_requested(conn):
    """The user-visible consequence, pinned at the database level.

    `process_referral_commission` credits `pending_balance_usd`. Nothing promotes it to
    `approved_balance_usd`, which is the only column `POST /referral/payouts` will spend.
    """
    cur = conn.cursor()
    cur.execute(
        "select prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = 'process_referral_commission'"
    )
    row = cur.fetchone()
    assert row is not None, "process_referral_commission is not installed"
    src = row[0].lower()

    assert "pending_balance_usd" in src, (
        "the installed commission function no longer credits pending_balance_usd"
    )
    assert "approved_balance_usd = " not in src.replace("approved_balance_usd =\n", ""), (
        "the commission function now writes approved_balance_usd directly. If commissions "
        "are auto-approved, the approval step is no longer a gap and this test should be "
        "replaced by one asserting the approval RULES."
    )


# ═════════════════════════════════════════════════════════════════════════════
#  2. The unreachable states the API advertises
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state", ["approved", "paid", "failed"])
def test_the_live_table_refuses_the_states_only_the_unapplied_redesign_declares(
    payout, state
):
    """`PayoutRecord` exposes `approved_at` and `paid_at`, and the request model's states
    come from `migrations/referral_system_redesign.sql`. That declaration never
    materialised (`CREATE TABLE IF NOT EXISTS` over 006's table — the BUG-035 pattern), so
    the live CHECK admits only pending/processing/completed/rejected.

    This failing closed is the SAFE outcome: a payout cannot be parked in a state no code
    can leave. It is recorded because the API contract still describes those states, which
    is a reported divergence rather than a fixed one.
    """
    cur = payout["cur"]
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE public.referral_payouts SET status = %s WHERE id = %s",
            (state, payout["id"]),
        )


def test_a_payout_can_still_be_rejected_and_progressed(payout):
    """NEGATIVE CONTROL. The refusals above must not be "refuse every transition".

    The states the live table DOES declare remain writable, so the tests above are about
    the specific absent vocabulary and not about a frozen row.
    """
    cur = payout["cur"]
    for state in ("processing", "rejected"):
        cur.execute("SAVEPOINT s")
        cur.execute(
            "UPDATE public.referral_payouts SET status = %s WHERE id = %s RETURNING status",
            (state, payout["id"]),
        )
        assert cur.fetchone()[0] == state
        cur.execute("ROLLBACK TO SAVEPOINT s")


# ═════════════════════════════════════════════════════════════════════════════
#  3. A terminal state may not be asserted without evidence
# ═════════════════════════════════════════════════════════════════════════════

def test_an_obligation_cannot_be_settled_without_a_settled_at(conn):
    """The guarantee 029 already provides, and the model this workstream proposes copying.

    Exercised on a real obligation created through the real accrual function.
    """
    cur = conn.cursor()
    referrer, referred = str(uuid.uuid4()), str(uuid.uuid4())
    for uid in (referrer, referred):
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (uid, f"p10d-obl-{uid[:8]}@example.invalid"),
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
            (uid,),
        )
    # `tr_profiles_create_referral_code` fires on the profiles INSERT above and already
    # created a code for this user (`uq_referral_codes_user` is one per user), so the code
    # is READ rather than inserted.
    cur.execute("SELECT id FROM public.referral_codes WHERE user_id = %s", (referrer,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO public.referral_codes (user_id, code, is_active) "
            "VALUES (%s, %s, true) RETURNING id",
            (referrer, f"P10D{uuid.uuid4().hex[:6].upper()}"),
        )
        row = cur.fetchone()
    code_id = row[0]
    cur.execute(
        "INSERT INTO public.referral_relationships "
        "(referrer_id, referred_id, referral_code_id, status) "
        "VALUES (%s, %s, %s, 'pending')",
        (referrer, referred, code_id),
    )
    cur.execute(
        "SELECT public.record_referral_obligation("
        "  'stripe', %s, %s, 100.00, 'USD', 'pro', 'P10D_TEST', '{}'::jsonb)",
        (f"pi_p10d_{uuid.uuid4().hex[:10]}", referred),
    )
    obligation_id = cur.fetchone()[0]
    assert obligation_id is not None, "the accrual function recorded no obligation"

    # Without evidence: refused.
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE public.referral_obligations SET status = 'settled' "
            "WHERE obligation_id = %s",
            (obligation_id,),
        )
    cur.execute("ROLLBACK TO SAVEPOINT s")

    # With evidence: accepted. Proves the refusal above is about the missing timestamp.
    cur.execute(
        "UPDATE public.referral_obligations "
        "SET status = 'settled', settled_at = now() WHERE obligation_id = %s "
        "RETURNING status, settled_at IS NOT NULL",
        (obligation_id,),
    )
    status, has_ts = cur.fetchone()
    assert (status, has_ts) == ("settled", True)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN GAP. backend_app/migrations/032_referral_payouts_terminal_evidence.sql adds "
        "chk_referral_payouts_terminal_evidence but is PREPARED, NOT APPLIED: it alters a "
        "shared table and needs the migration owner's approval under the Phase 10 "
        "workstream rules. Until it is applied, referral_payouts.status can be set to "
        "'completed' with processed_at NULL — a payout recorded as paid with no record of "
        "when, no actor and no provider reference, on a table with no audit trail. Not "
        "currently reachable from application code (nothing writes any status but "
        "'pending'), so this is a latent exposure for a future settlement job or an "
        "operator UPDATE. When 032 is applied this test XPASSes and strict mode fails the "
        "suite until this marker is removed."
    ),
)
def test_a_payout_cannot_be_completed_without_a_processed_at(payout):
    """The one unsafe transition the database still permits.

    Deliberately asserts the SAFE invariant rather than recording the unsafe behaviour, so
    the suite carries the requirement and not the defect.
    """
    cur = payout["cur"]
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE public.referral_payouts SET status = 'completed' WHERE id = %s",
            (payout["id"],),
        )


def test_the_proposed_migration_exists_and_is_not_silently_applied():
    """The prepared-but-unapplied migration must be on disk and must say so.

    Stops the gap above from being carried by a comment alone.
    """
    sql = (REPO / "backend_app" / "migrations"
           / "032_referral_payouts_terminal_evidence.sql")
    assert sql.exists(), "the proposed migration 032 is missing"
    text = sql.read_text(encoding="utf-8")
    assert "chk_referral_payouts_terminal_evidence" in text
    assert "NOT APPLIED" in text, (
        "032 no longer declares that it is unapplied; if it has been approved and run, "
        "remove the xfail marker above as well"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  4. There is no audit trail — recorded as the remaining capability gap
# ═════════════════════════════════════════════════════════════════════════════

def test_there_is_no_referral_state_change_audit_table(conn):
    """Recorded, not fixed: a settlement implementation needs this and it does not exist.

    The marketplace side has `library_subscription_transitions`; the referral side has
    nothing comparable. `referral_payouts` carries a single `processed_at` and no actor.
    """
    cur = conn.cursor()
    cur.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'public' "
        "  and (table_name like 'referral%%audit%%' "
        "    or table_name like 'referral%%history%%' "
        "    or table_name like 'referral%%transition%%' "
        "    or table_name like 'referral%%event%%')"
    )
    found = [r[0] for r in cur.fetchall()]
    assert found == [], (
        f"a referral audit table now exists ({found}). Update the Phase 10 WS-D report: "
        f"the missing audit trail was one of its recorded remaining gaps."
    )
