"""Phase 9 - environment confirmation.

Reads the local PostgreSQL DSN from staging/pg_dsn.txt and reports substrate
facts needed before any Phase 9 change is made. Read-only: no DDL, no writes.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def dsn() -> str:
    return (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()


def main() -> int:
    print("=" * 78)
    print("PHASE 9 ENVIRONMENT CONFIRMATION")
    print("=" * 78)

    print("ROOT =", ROOT)
    print("PYTHON =", sys.version.split()[0])

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        print("GIT_HEAD =", head)
    except Exception as exc:  # pragma: no cover - diagnostic only
        print("GIT_HEAD = <unavailable>", exc)

    try:
        import psycopg2
    except Exception as exc:
        print("PSYCOPG2 = <unavailable>", exc)
        return 1

    d = dsn()
    print("DSN =", d)
    try:
        conn = psycopg2.connect(d)
    except Exception as exc:
        print("PG_CONNECT = FAILED", exc)
        return 1

    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("select version()")
        print("PG_VERSION =", cur.fetchone()[0].split(",")[0])

        cur.execute(
            "select count(*) from information_schema.tables "
            "where table_schema = 'public'"
        )
        print("PUBLIC_TABLES =", cur.fetchone()[0])

        # Tables that Phase 9 findings touch.
        for name in (
            "referral_commissions",
            "referral_payouts",
            "referrals",
            "execution_records",
            "fee_records",
            "invoices",
            "payments",
            "wallets",
            "wallet_transactions",
            "schema_migrations",
            "audit_logs",
        ):
            cur.execute("select to_regclass(%s) is not null", (f"public.{name}",))
            print(f"  TABLE {name:28s} exists={cur.fetchone()[0]}")

        # Do not assume schema_migrations' shape - introspect it.
        cur.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema='public' and table_name='schema_migrations' "
            "order by ordinal_position"
        )
        cols = cur.fetchall()
        print("SCHEMA_MIGRATIONS COLUMNS =", [c[0] for c in cols])
        colnames = [c[0] for c in cols]
        order_col = colnames[0]
        cur.execute(
            f'select * from public.schema_migrations order by "{order_col}"'
        )
        rows = cur.fetchall()
        print("SCHEMA_MIGRATIONS ROWS =", len(rows))
        for row in rows[-10:]:
            print("  ", dict(zip(colnames, (str(v)[:40] for v in row))))

        # What money/audit-shaped tables actually exist? MONEY-06 needs the
        # real vocabulary, not an assumed wallet/ledger pair.
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema='public' and ("
            "  table_name like '%referral%' or table_name like '%wallet%'"
            "  or table_name like '%ledger%' or table_name like '%payout%'"
            "  or table_name like '%commission%' or table_name like '%audit%'"
            "  or table_name like '%payment%' or table_name like '%invoice%'"
            "  or table_name like '%credit%' or table_name like '%balance%'"
            ") order by table_name"
        )
        print("MONEY/AUDIT-SHAPED TABLES:")
        for (name,) in cur.fetchall():
            print("   -", name)

    conn.close()
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
