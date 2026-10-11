"""PHASE 10 WORKSTREAM D — Stage 1 probe. `payment_method` vs `payout_method`.

Phase 9 recorded this as "probable but not yet confirmed against the catalogue". This
probe confirms it against the catalogue AND exercises the consequence, because the
column-name question is not the interesting part -- what the payout handler does to the
referrer's balance when the insert fails is.

The handler at backend_app/routers/referral.py:755-800 does, in order:
  1. debit  referral_wallets.approved_balance_usd   (CAS, guarded by .gte())
  2. insert referral_payouts { ..., payment_method, payment_details }
  3. if `payout_resp.data` is empty -> compensating re-credit
...and step 3 is reached only when the insert RETURNS EMPTY, not when it RAISES.

EVERYTHING RUNS INSIDE TRANSACTIONS THAT ARE ROLLED BACK. Writes nothing durable.
No HTTP call, no Supabase client, no payment initiated.
"""
from __future__ import annotations

import pathlib
import uuid

import psycopg2
from psycopg2 import errors as pgerr

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

# The exact key set backend_app/routers/referral.py:770-777 sends to PostgREST.
APP_INSERT_KEYS = ["user_id", "amount_usd", "status", "payment_method", "payment_details"]


def _seed(cur) -> str:
    uid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (uid, f"p10d-payout-{uid[:8]}@example.invalid"),
    )
    cur.execute(
        "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (uid,)
    )
    return uid


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    rc = 0
    print("=" * 78)
    print("PHASE 10 / WS-D / STEP 2 — referral_payouts column reality")
    print("=" * 78)

    try:
        cur = conn.cursor()

        # ── 1. What the catalogue says ────────────────────────────────────────────
        cur.execute(
            "select column_name, is_nullable, column_default "
            "from information_schema.columns "
            "where table_schema='public' and table_name='referral_payouts' "
            "  and column_name in "
            "      ('payment_method','payout_method','payment_details','payout_details')"
            " order by column_name"
        )
        present = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        print("CATALOGUE — which of the four spellings exist on the live table:")
        for name in ("payment_method", "payout_method", "payment_details", "payout_details"):
            if name in present:
                nullable, default = present[name]
                print(f"  PRESENT  {name:<16} nullable={nullable} default={default}")
            else:
                print(f"  ABSENT   {name}")

        app_col_exists = "payment_method" in present
        db_col_exists = "payout_method" in present
        db_col_required = db_col_exists and present["payout_method"][0] == "NO" \
            and present["payout_method"][1] is None

        # ── 2. The application's exact insert ─────────────────────────────────────
        print("-" * 78)
        print("THE APPLICATION'S INSERT — referral.py:770-777, key for key")
        uid = _seed(cur)
        insert_failed_with = None
        try:
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, status, payment_method, payment_details) "
                "VALUES (%s, %s, 'pending', 'bank_transfer', '{}'::jsonb)",
                (uid, "50.00"),
            )
            print("  RESULT: ACCEPTED — Observation 2 is a FALSE POSITIVE")
        except Exception as e:  # noqa: BLE001
            insert_failed_with = type(e).__name__
            print(f"  RESULT: REJECTED — {insert_failed_with}")
            print(f"          {str(e).strip().splitlines()[0][:200]}")
        conn.rollback()

        # ── 3. Negative control: the same insert with the live column names ───────
        print("-" * 78)
        print("NEGATIVE CONTROL — identical insert, live column names")
        cur = conn.cursor()
        uid = _seed(cur)
        control_ok = False
        try:
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, status, payout_method, payout_details) "
                "VALUES (%s, %s, 'pending', 'bank_transfer', '{}'::jsonb) "
                "RETURNING id, payout_method, currency, status",
                (uid, "50.00"),
            )
            row = cur.fetchone()
            control_ok = True
            print(f"  RESULT: ACCEPTED — payout_method={row[1]!r} currency={row[2]!r} "
                  f"status={row[3]!r}")
            print("  => only the column NAMES differed. The insert is otherwise valid.")
        except Exception as e:  # noqa: BLE001
            print(f"  RESULT: REJECTED — {type(e).__name__}: {str(e)[:200]}")
        conn.rollback()

        # ── 4. THE CONSEQUENCE. Debit lands, payout row does not. ─────────────────
        print("-" * 78)
        print("CONSEQUENCE — the handler's own order of operations, replayed")
        cur = conn.cursor()
        uid = _seed(cur)
        cur.execute(
            "INSERT INTO public.referral_wallets "
            "(user_id, pending_balance_usd, approved_balance_usd, paid_balance_usd, "
            " lifetime_earnings_usd) VALUES (%s, 0, 500.00, 0, 500.00)",
            (uid,),
        )
        cur.execute(
            "select approved_balance_usd from public.referral_wallets where user_id=%s",
            (uid,),
        )
        before = cur.fetchone()[0]
        print(f"  step 0  approved_balance_usd = {before}")

        # step 1 — the debit. Exactly the handler's CAS update.
        cur.execute(
            "UPDATE public.referral_wallets "
            "SET approved_balance_usd = %s, updated_at = now() "
            "WHERE user_id = %s AND approved_balance_usd >= %s "
            "RETURNING approved_balance_usd",
            ("450.00", uid, "50.00"),
        )
        debited = cur.fetchone()
        print(f"  step 1  debit applied -> approved_balance_usd = {debited[0]}")

        # A SAVEPOINT stands in for "the statement failed but the caller's outer
        # transaction/connection survives", which is what PostgREST gives the handler:
        # each .execute() is its own request, so the debit is already COMMITTED.
        cur.execute("SAVEPOINT before_payout_insert")
        raised = None
        try:
            cur.execute(
                "INSERT INTO public.referral_payouts "
                "(user_id, amount_usd, status, payment_method, payment_details) "
                "VALUES (%s, %s, 'pending', 'bank_transfer', '{}'::jsonb)",
                (uid, "50.00"),
            )
        except Exception as e:  # noqa: BLE001
            raised = type(e).__name__
            cur.execute("ROLLBACK TO SAVEPOINT before_payout_insert")
        print(f"  step 2  payout insert -> {raised or 'ACCEPTED'}")

        # step 3 — the handler's compensating re-credit. It is inside
        #   `if not payout_resp.data or len(...) == 0:`
        # and an insert that RAISES never produces a `payout_resp` at all; control jumps
        # to `except Exception` at referral.py:797. So the re-credit does not run.
        print("  step 3  compensating re-credit: NOT REACHED — the handler's rollback is")
        print("          guarded by `if not payout_resp.data`, and a raising insert goes")
        print("          straight to `except Exception` (referral.py:782-800)")

        cur.execute(
            "select approved_balance_usd from public.referral_wallets where user_id=%s",
            (uid,),
        )
        after = cur.fetchone()[0]
        cur.execute("select count(*) from public.referral_payouts where user_id=%s", (uid,))
        n_payouts = cur.fetchone()[0]
        print(f"  final   approved_balance_usd = {after}   referral_payouts rows = {n_payouts}")

        money_lost = (after < before) and n_payouts == 0

        print("=" * 78)
        print("VERDICT")
        print("=" * 78)
        print(f"  live table has `payment_method` (what the app writes) .... {app_col_exists}")
        print(f"  live table has `payout_method` ........................... {db_col_exists}")
        print(f"  `payout_method` is NOT NULL with no default .............. {db_col_required}")
        print(f"  the application's exact insert is rejected ............... {bool(raised)}")
        print(f"  the same insert with live column names succeeds .......... {control_ok}")
        print(f"  balance debited AND no payout row exists ................. {money_lost}")
        if money_lost and raised and control_ok and not app_col_exists:
            print("\n  OBSERVATION 2: CONFIRMED REAL DEFECT, and it is not cosmetic.")
            print(f"  {before} - {after} = {before - after} USD left the referrer's")
            print("  approved balance with no payout record of any kind.")
        else:
            rc = 1
            print("\n  OBSERVATION 2: NOT FULLY REPRODUCED — see the lines above.")
        return rc
    finally:
        conn.rollback()
        conn.close()
        print("\n[rolled back — nothing durable was written]")


if __name__ == "__main__":
    raise SystemExit(main())
