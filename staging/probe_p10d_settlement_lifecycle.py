"""PHASE 10 WORKSTREAM D — Stage 3 probe. The referral settlement lifecycle, measured.

Observation 3 claims the referral pending->approved settlement step is incomplete. This
probe does not re-read the source; it asks the LIVE database two questions:

  A. WHICH TRANSITIONS EXIST? For each referral ledger, which status values can actually
     be reached, and is there any server-side routine that writes a settlement state.
  B. DO THE UNSAFE ONES FAIL SAFELY? Where settlement is unimplemented, an operator or a
     future caller must not be able to assert "paid" without evidence. Each candidate
     write is attempted and the database's answer recorded.

The brief forbids marking a payout paid without evidence of successful payment and forbids
initiating transfers. Nothing here initiates anything: every write is inside a transaction
that is ROLLED BACK, and no payment provider is contacted.
"""
from __future__ import annotations

import pathlib
import uuid

import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

LEDGERS = ("referral_commissions", "referral_wallets", "referral_payouts",
           "referral_obligations", "referral_relationships")


def _seed(cur) -> tuple[str, str]:
    referrer, referred = str(uuid.uuid4()), str(uuid.uuid4())
    for uid in (referrer, referred):
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (uid, f"p10d-settle-{uid[:8]}@example.invalid"),
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
            (uid,),
        )
    return referrer, referred


def _attempt(cur, label: str, sql: str, params=(), *, expect: str) -> dict:
    """Run one candidate write inside a SAVEPOINT and report the server's verdict."""
    cur.execute("SAVEPOINT probe")
    try:
        cur.execute(sql, params)
        n = cur.rowcount
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        verdict = "ACCEPTED" if n else "MATCHED 0 ROWS"
        ok = (expect == "refuse" and n == 0) or (expect == "accept" and n > 0)
        print(f"  [{'ok ' if ok else 'GAP'}] {label}")
        print(f"         -> {verdict} (rowcount={n}); expected to {expect}")
        return {"label": label, "verdict": verdict, "safe": ok}
    except Exception as e:  # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        name = type(e).__name__
        ok = expect == "refuse"
        print(f"  [{'ok ' if ok else 'GAP'}] {label}")
        print(f"         -> REFUSED by the database ({name}); expected to {expect}")
        return {"label": label, "verdict": f"REFUSED/{name}", "safe": ok}


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    print("=" * 78)
    print("PHASE 10 / WS-D / STEP 3 — referral settlement lifecycle")
    print("=" * 78)
    results: list[dict] = []
    try:
        cur = conn.cursor()

        # ── A. The declared vocabularies and what is stored today ────────────────
        print("A. DECLARED STATUS VOCABULARY vs ROWS PRESENT")
        for t in LEDGERS:
            cur.execute("select to_regclass(%s)", (f"public.{t}",))
            if cur.fetchone()[0] is None:
                print(f"  {t}: ABSENT")
                continue
            cur.execute(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid = to_regclass(%s) and pg_get_constraintdef(oid) "
                "      like '%%status%%'",
                (f"public.{t}",),
            )
            checks = [r[0] for r in cur.fetchall()]
            cur.execute(f'select count(*) from public."{t}"')
            n = cur.fetchone()[0]
            cur.execute(
                "select column_name from information_schema.columns "
                "where table_schema='public' and table_name=%s and column_name='status'",
                (t,),
            )
            has_status = cur.fetchone() is not None
            states = []
            if has_status and n:
                cur.execute(f'select distinct status from public."{t}"')
                states = sorted(str(r[0]) for r in cur.fetchall())
            print(f"  {t}: rows={n} distinct_status={states or '-'}")
            for c in checks:
                print(f"      CHECK {c}")

        # ── Server-side routines that could settle ───────────────────────────────
        print("-" * 78)
        print("B. SERVER-SIDE ROUTINES MENTIONING A SETTLEMENT STATE")
        cur.execute(
            "select p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='public' and p.proname like '%referral%' "
            "order by p.proname"
        )
        funcs = [r[0] for r in cur.fetchall()]
        print(f"  referral functions installed: {funcs}")
        settlers = []
        for fn in funcs:
            cur.execute(
                "select prosrc from pg_proc p join pg_namespace n "
                "on n.oid=p.pronamespace where n.nspname='public' and p.proname=%s",
                (fn,),
            )
            for (src,) in cur.fetchall():
                low = (src or "").lower()
                if "'settled'" in low or "settled_at" in low or "'paid'" in low \
                        or "'completed'" in low or "approved_balance_usd =" in low:
                    settlers.append(fn)
                    break
        print(f"  routines that write a settlement/paid state: {sorted(set(settlers)) or 'NONE'}")
        if not settlers:
            print("  => the pending/payable -> settled step has NO server-side writer.")

        # ── C. Do the unsafe transitions fail safely? ────────────────────────────
        print("-" * 78)
        print("C. CAN A SETTLEMENT STATE BE ASSERTED WITHOUT EVIDENCE?")
        referrer, referred = _seed(cur)

        cur.execute(
            "INSERT INTO public.referral_payouts "
            "(user_id, amount_usd, payout_method, payout_details, status) "
            "VALUES (%s, '50.00', 'bank_transfer', '{}'::jsonb, 'pending') RETURNING id",
            (referrer,),
        )
        payout_id = cur.fetchone()[0]

        results.append(_attempt(
            cur,
            "referral_payouts: pending -> 'paid' (a state the live CHECK omits)",
            "UPDATE public.referral_payouts SET status='paid' WHERE id=%s",
            (payout_id,), expect="refuse",
        ))
        results.append(_attempt(
            cur,
            "referral_payouts: pending -> 'approved' (state the API response expects)",
            "UPDATE public.referral_payouts SET status='approved' WHERE id=%s",
            (payout_id,), expect="refuse",
        ))
        results.append(_attempt(
            cur,
            "referral_payouts: pending -> 'completed' WITHOUT processed_at "
            "(no evidence of payment)",
            "UPDATE public.referral_payouts SET status='completed' WHERE id=%s",
            (payout_id,), expect="refuse",
        ))

        # referral_obligations — 029 constrains its terminal timestamps.
        cur.execute(
            "INSERT INTO public.referral_relationships "
            "(referrer_id, referred_id, referral_code_id, status) "
            "SELECT %s, %s, id, 'pending' FROM public.referral_codes LIMIT 1",
            (referrer, referred),
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO public.referral_codes (user_id, code, is_active) "
                "VALUES (%s, %s, true) RETURNING id",
                (referrer, f"P10D{referrer[:4].upper()}"),
            )
            code_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.referral_relationships "
                "(referrer_id, referred_id, referral_code_id, status) "
                "VALUES (%s, %s, %s, 'pending')",
                (referrer, referred, code_id),
            )
        cur.execute(
            "SELECT public.record_referral_obligation("
            "  'stripe', %s, %s, 100.00, 'USD', 'pro', 'P10D_PROBE', '{}'::jsonb)",
            (f"pi_p10d_{uuid.uuid4().hex[:10]}", referred),
        )
        obl = cur.fetchone()[0]
        print(f"  (seeded obligation {obl})")

        results.append(_attempt(
            cur,
            "referral_obligations: payable -> 'settled' WITHOUT settled_at "
            "(no evidence of payout)",
            "UPDATE public.referral_obligations SET status='settled' "
            "WHERE obligation_id=%s",
            (obl,), expect="refuse",
        ))
        results.append(_attempt(
            cur,
            "referral_obligations: payable -> 'settled' WITH settled_at "
            "(the legitimate settlement write)",
            "UPDATE public.referral_obligations SET status='settled', settled_at=now() "
            "WHERE obligation_id=%s",
            (obl,), expect="accept",
        ))

        print("=" * 78)
        print("VERDICT")
        print("=" * 78)
        gaps = [r for r in results if not r["safe"]]
        for r in results:
            print(f"  {'SAFE' if r['safe'] else 'GAP '}  {r['label']}")
        print(f"\n  unsafe-transition gaps: {len(gaps)} of {len(results)}")
        if gaps:
            print("  The database permits a settlement state to be asserted with no")
            print("  evidence of payment for:")
            for g in gaps:
                print(f"    - {g['label']}  ({g['verdict']})")
        return 0 if not gaps else 2
    finally:
        conn.rollback()
        conn.close()
        print("\n[rolled back — nothing durable was written, no transfer initiated]")


if __name__ == "__main__":
    raise SystemExit(main())
