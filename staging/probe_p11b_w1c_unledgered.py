"""Which repository migrations took effect WITHOUT a ledger record?

PHASE 11B / WORKSTREAM 1C — read-only. This is the finding that decides what the drift
means, so it is measured rather than inferred.

WHAT THE PREVIOUS PROBE TURNED UP
---------------------------------
All 7 "disappearances" trace to `023_remediate_privilege_escalation.sql` and
`024_remediate_referral_double_commission.sql`. Their effects are unambiguously present
in the live database -- `anon` holds only SELECT on `library_strategies`, and
`process_referral_commission` is `prosecdef = true`. But a lookup for either filename in
`public.schema_migrations` returned NO ROWS, in the live ledger and in the snapshot's.

If that holds, those migrations were applied without being recorded, and
`public.schema_migrations` -- the artifact `verify_schema_drift.py` trusts as the
deployment ledger, and the one the provisioning runbook treats as authoritative -- is
incomplete. A schema change with no ledger trace cannot be dated, attributed or audited.

So this enumerates every `.sql` in the repository's migration set, asks the ledger about
each by basename, and then tests the live catalogue for a marker of each unledgered
migration's effect. A migration that is absent from the ledger AND whose effect is absent
is simply not applied, which is fine. One that is absent from the ledger while its effect
IS present is the finding.

READ-ONLY: SELECTs only, read-only session enforced and read back.
"""

from __future__ import annotations

import ipaddress
import json
import pathlib
import sys
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = REPO / "staging" / "pg_dsn.txt"
SNAPSHOT = REPO / "staging" / "provisioning_snapshot.json"
ROOTS = [REPO / "migrations", REPO / "backend_app" / "migrations",
         REPO / "archived_migrations"]

#: A catalogue probe per migration whose effect can be observed directly. Each is a
#: read-only SELECT returning a boolean: did this migration's change land?
EFFECT_PROBES = {
    "022_fix_library_subscriptions_updated_at.sql": (
        "trigger on library_subscriptions",
        "select exists(select 1 from pg_trigger t join pg_class c on c.oid=t.tgrelid "
        "where c.relname='library_subscriptions' and not t.tgisinternal)"),
    "023_remediate_privilege_escalation.sql": (
        "anon has NO write grants on library_strategies",
        "select not exists(select 1 from information_schema.table_privileges "
        "where table_schema='public' and table_name='library_strategies' "
        "and grantee='anon' and privilege_type in ('INSERT','UPDATE','DELETE'))"),
    "024_remediate_referral_double_commission.sql": (
        "process_referral_commission is SECURITY DEFINER",
        "select coalesce((select p.prosecdef from pg_proc p "
        "join pg_namespace n on n.oid=p.pronamespace "
        "where n.nspname='public' and p.proname='process_referral_commission' "
        "limit 1), false)"),
    "025_work03_bot_ownership_claims.sql": (
        "table bot_ownership_claims exists",
        "select to_regclass('public.bot_ownership_claims') is not null"),
    "026_fee_records_row_level_security.sql": (
        "fee_records has RLS enabled",
        "select coalesce((select c.relrowsecurity from pg_class c "
        "join pg_namespace n on n.oid=c.relnamespace "
        "where n.nspname='public' and c.relname='fee_records'), false)"),
    "027_referral_ledger_currency.sql": (
        "referral_wallets has a currency column",
        "select exists(select 1 from information_schema.columns "
        "where table_schema='public' and table_name='referral_wallets' "
        "and column_name like '%currency%')"),
    "028_safety_control_audit.sql": (
        "table safety_control_audit exists",
        "select to_regclass('public.safety_control_audit') is not null"),
    "029_referral_obligations.sql": (
        "table referral_obligations exists",
        "select to_regclass('public.referral_obligations') is not null"),
    "030_execution_venue_order_id.sql": (
        "execution_records.venue_order_id exists",
        "select exists(select 1 from information_schema.columns "
        "where table_schema='public' and table_name='execution_records' "
        "and column_name='venue_order_id')"),
    "031_fee_records_fee_pct_nullable.sql": (
        "fee_records.fee_pct is nullable",
        "select coalesce((select is_nullable='YES' from information_schema.columns "
        "where table_schema='public' and table_name='fee_records' "
        "and column_name='fee_pct'), false)"),
    "032_referral_payouts_terminal_evidence.sql": (
        "referral_payouts has terminal-evidence columns",
        "select exists(select 1 from information_schema.columns "
        "where table_schema='public' and table_name='referral_payouts' "
        "and column_name in ('terminal_evidence','evidence','terminal_state'))"),
}


def _connect():
    import psycopg2
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    p = urlparse(dsn)
    host = (p.hostname or "").lower()
    if p.port == 6543:
        raise SystemExit("REFUSED: transaction pooler")
    if host not in ("localhost", "127.0.0.1", "::1", ""):
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise SystemExit("REFUSED: not loopback")
    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    if cur.fetchone()[0] != "on":
        conn.close()
        raise SystemExit("ABORT: read-only not enforced")
    return conn


def main() -> int:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("select filename, applied_at, step from public.schema_migrations")
    ledger = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    ledger_base = {k.rsplit("/", 1)[-1]: v for k, v in ledger.items()}

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snap_base = {(m[0] if isinstance(m, (list, tuple)) else m).rsplit("/", 1)[-1]
                 for m in snap.get("recorded_migrations", [])}

    on_disk = {}
    for root in ROOTS:
        if root.exists():
            for p in sorted(root.rglob("*.sql")):
                on_disk.setdefault(p.name, str(p.relative_to(REPO)).replace("\\", "/"))

    print("=" * 78)
    print("MIGRATION LEDGER COMPLETENESS")
    print("=" * 78)
    print(f"   .sql files in the migration set : {len(on_disk)}")
    print(f"   rows in public.schema_migrations: {len(ledger)}")
    print(f"   rows in the snapshot's ledger   : {len(snap_base)}")
    print(f"   files on disk with NO ledger row: "
          f"{len(set(on_disk) - set(ledger_base))}")

    print("\n-- numbered migrations 022..032, the window the drift sits in -------------")
    print(f"   {'migration':48} {'ledger':>8} {'snapshot':>9}  effect")
    print("   " + "-" * 74)
    unledgered_but_applied = []
    for name in sorted(EFFECT_PROBES):
        label, sql = EFFECT_PROBES[name]
        in_ledger = name in ledger_base
        in_snap = name in snap_base
        try:
            cur.execute(sql)
            applied = bool(cur.fetchone()[0])
        except Exception as exc:                                  # noqa: BLE001
            applied = None
            label += f"  (probe error: {type(exc).__name__})"
        eff = {True: "PRESENT", False: "absent", None: "?"}[applied]
        print(f"   {name:48} {str(in_ledger):>8} {str(in_snap):>9}  {eff}")
        print(f"       marker: {label}")
        if applied and not in_ledger:
            unledgered_but_applied.append((name, label))

    print("\n-- every file on disk with no ledger row ---------------------------------")
    missing_rows = sorted(set(on_disk) - set(ledger_base))
    for name in missing_rows:
        print(f"     {name:52} {on_disk[name]}")

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   APPLIED BUT NOT LEDGERED: {len(unledgered_but_applied)}")
    for name, label in unledgered_but_applied:
        print(f"     !! {name}")
        print(f"        evidence it ran: {label}")
    if unledgered_but_applied:
        print("\n   These changes are present in the live schema and have NO row in")
        print("   `public.schema_migrations`. The ledger therefore cannot date or")
        print("   attribute them, and `verify_schema_drift.py`'s recorded_migrations")
        print("   dimension is blind to them. The schema is AHEAD of its own ledger.")
        print("   `schema_migrations_pkey` is PRIMARY KEY (filename) and the runner")
        print("   inserts with ON CONFLICT (filename) DO NOTHING, so a re-run cannot")
        print("   correct this by itself.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
