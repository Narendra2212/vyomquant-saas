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

(2) was originally carried as a strict xfail against migration 032, on the premise that the
file was "prepared but not applied". It is now asserted outright: the premise was false --
`backend_app/migrations/` is applied WHOLESALE by `scripts/provision_database.py`, so the
constraint has always been present in any database provisioned from repository source, and
production was measured to carry it too. The xfail was removed when the XPASS failed a
strict-mode run (de87b620); the file's contradictory header was corrected under DB-05.

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

    UPDATED FOR REF-01. Settlement for `referral_payouts` IS now implemented --
    `create_referral_payout_request` and `settle_referral_payout`
    (`backend_app/migrations/049_ref01_payout_atomic.sql`) -- exactly as this test's own
    docstring said to expect, and the comment below records that the evidence
    requirements this file pins were in fact satisfied before this assertion was
    updated, not assumed:

      * records evidence of payment: `settle_referral_payout` REQUIRES a non-blank
        `p_reference` to reach 'completed' (RF003 otherwise) and writes `processed_at`
        -- proven by `tests/test_ref01_payout_atomic_postgres.py::
        test_completing_without_a_reference_raises_rf003_and_changes_nothing` and
        `::test_completing_records_reference_and_does_not_touch_the_wallet`.
      * cannot move to a terminal state twice: an already-`completed`/`rejected` payout
        raises RF004 -- proven by `::test_an_already_terminal_payout_cannot_be_settled_
        again`.
      * "idempotent under webhook replay" does not apply to this path the way it does
        to `record_referral_obligation`: a payout settlement is an OPERATOR action
        (`PUT /api/referral/payouts/{id}/settle`, admin-gated), not a payment-provider
        webhook, so there is no redelivery to be idempotent against -- the relevant
        property is the RF004 guard above, which this file's own replacement test
        proves.

    So this test now asserts the NARROWER, still-true thing: no settlement writer
    touches `referral_commissions`/`referral_wallets.pending_balance_usd ->
    approved_balance_usd` (the commission-APPROVAL gap this file's other tests
    document, which REF-01 does not touch and does not claim to), and the only
    settlers installed are the two named ones.
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

    known_ref01_settlers = {"create_referral_payout_request", "settle_referral_payout"}
    unexpected = sorted(set(settlers) - known_ref01_settlers)
    assert unexpected == [], (
        f"a referral settlement writer exists that is neither of REF-01's two known "
        f"functions ({unexpected}). Confirm it records evidence of payment (provider "
        f"reference + timestamp), is idempotent under webhook replay (or, for an "
        f"operator-only action, refuses to re-settle a terminal row), and cannot move "
        f"a payout to a terminal state without that evidence -- then add it here."
    )
    assert set(settlers) == known_ref01_settlers, (
        f"expected exactly REF-01's two settlement functions to be installed, found "
        f"{sorted(settlers)}. If one was removed, the capability gap this file "
        f"documents may have returned."
    )

    # The commission-APPROVAL gap (pending_balance_usd -> approved_balance_usd) is a
    # SEPARATE, still-open gap REF-01 does not touch: neither known settler may write
    # approved_balance_usd by crediting it from pending_balance_usd, only by debiting
    # or re-crediting it against a payout it itself is settling.
    cur.execute(
        "select prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = 'create_referral_payout_request'"
    )
    create_src = (cur.fetchone()[0] or "").lower()
    assert "pending_balance_usd" not in create_src, (
        "create_referral_payout_request now references pending_balance_usd; the "
        "commission-approval gap this file documents may have been closed by this "
        "function rather than by a dedicated approval writer, and this test's "
        "framing needs re-examining"
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


def test_a_payout_cannot_be_completed_without_a_processed_at(payout):
    """A payout may not reach 'completed' without a record of when it was paid.

    Deliberately asserts the SAFE invariant rather than recording the unsafe behaviour, so
    the suite carries the requirement and not the defect.

    THIS CARRIED A ``strict=True`` XFAIL UNTIL THIS COMMIT, on the premise that
    032_referral_payouts_terminal_evidence.sql was "PREPARED, NOT APPLIED". That premise
    is false for every database provisioned from repository source, which is what CI
    tests: ``scripts/provision_database.py`` applies the whole
    ``backend_app/migrations/`` directory, 032 included. The provisioning job's own log
    for this change says so in as many words --
    ``migrations/032_referral_payouts_terminal_evidence.sql: applied`` -- and then
    ``skipped (already applied)`` on the idempotency re-run.

    So the constraint IS present, this test passes, and the marker's own closing sentence
    ("when 032 is applied this test XPASSes and strict mode fails the suite until this
    marker is removed") is the instruction being followed here. That XPASS is precisely
    what failed when this file was first wired into a PostgreSQL lane; it had never run
    against a real server before, so nothing had ever evaluated the premise.

    NO XFAIL/SKIP REPLACES IT, deliberately. The assertion is a money-integrity
    invariant: a database that lets a payout be marked paid with no timestamp, no actor
    and no provider reference -- on a table with no audit trail -- SHOULD red this suite
    rather than quietly record the defect as expected.

    THE HEADER HAS SINCE BEEN CORRECTED (DB-05). This docstring used to end by noting that
    032 still declared itself NOT APPLIED, and that fixing it was blocked because the edit
    changes a checksum that staging/provisioning_snapshot.json records and
    scripts/verify_schema_drift.py enforces, with a baseline only regenerable against a
    real PostgreSQL server. Both halves of that turned out to be wrong when checked:
    `recorded_migrations` is in the drift gate's own ADVISORY_DIMENSIONS, so a changed
    checksum is a WARNING and does not fail the run; and production has no
    `public.schema_migrations` table at all, so the provisioner's exit-4 "applied
    previously with a different checksum" refusal could not fire there either. The header
    now says what production says, and the baseline entry was recomputed rather than
    re-measured, because `sha256(file text)` is a pure function of the bytes.
    """
    cur = payout["cur"]
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE public.referral_payouts SET status = 'completed' WHERE id = %s",
            (payout["id"],),
        )


def test_migration_032_is_present_in_the_live_schema(conn):
    """DB-05. The constraint is IN THE DATABASE, which is the only thing worth asserting.

    THIS TEST USED TO ASSERT THE OPPOSITE, FROM FILE TEXT.
    It read 032's header and required the string ``"NOT APPLIED"`` to still be in it, on
    the premise that the file was prepared but unexecuted. Meanwhile
    ``test_a_payout_cannot_be_completed_without_a_processed_at``, in this same file,
    required the constraint to EXIST. Both passed, because they measured different things:
    one read bytes on disk, the other read the live catalogue. The repository therefore
    carried two tests that contradicted each other, and neither could fail to reveal it.

    The premise was false. ``backend_app/migrations/`` is applied WHOLESALE by
    ``scripts/provision_database.py`` -- the directory is the unit, not the file -- so
    every database provisioned from repository source has always had this constraint, and
    production was measured (``pg_constraint``, 2026-10-24) to have it too.

    So the assertion now looks where the answer is. The file's own self-description is
    checked too, but second, and only for the one property text CAN establish: that it no
    longer claims to be unapplied.
    """
    cur = conn.cursor()
    cur.execute(
        "select pg_get_constraintdef(con.oid) "
        "from pg_constraint con "
        "join pg_class rel on rel.oid = con.conrelid "
        "join pg_namespace ns on ns.oid = rel.relnamespace "
        "where ns.nspname = 'public' "
        "  and rel.relname = 'referral_payouts' "
        "  and con.conname = 'chk_referral_payouts_terminal_evidence'"
    )
    row = cur.fetchone()
    assert row is not None, (
        "chk_referral_payouts_terminal_evidence is absent, so a payout can be marked "
        "'completed' with no processed_at -- a false paid status with nothing to show for "
        "it, on a table with no audit trail. Migration 032 adds it and "
        "backend_app/migrations/ is applied as a directory, so its absence means "
        "provisioning did not run or 032 was removed."
    )
    definition = row[0]
    assert "processed_at" in definition and "completed" in definition, (
        f"the constraint exists but no longer relates 'completed' to processed_at: "
        f"{definition}"
    )


def test_migration_032_no_longer_declares_itself_unapplied():
    """The file must not contradict the database it has already changed.

    Deliberately NOT asserted as ``"NOT APPLIED" not in text``. The corrected header
    QUOTES the superseded banner verbatim -- that is what makes the correction legible to
    someone who finds this file after reading the old one -- so a bare substring check
    would fail on the quotation and teach the next person to delete the history instead of
    recording it. Matching raw strings is how the contradiction above survived in the first
    place; this asserts the two markers that only a corrected file carries.
    """
    sql = (REPO / "backend_app" / "migrations"
           / "032_referral_payouts_terminal_evidence.sql")
    assert sql.exists(), "migration 032 is missing"
    text = sql.read_text(encoding="utf-8")

    assert "chk_referral_payouts_terminal_evidence" in text
    assert "CORRECTION, measured against real production" in text, (
        "032's correction block is gone. If the header was reverted to "
        "'NOT APPLIED. PREPARED FOR THE MIGRATION OWNER'S APPROVAL', note that the claim "
        "is false for every database provisioned from this repository -- see DB-05."
    )
    assert "APPLIED. Present in production" in text, (
        "032 no longer states that it is applied"
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
