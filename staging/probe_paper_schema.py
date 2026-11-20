"""Is the canonical paper ledger actually present in the live database?

Decides whether PAPER-01 can be tested against real PostgreSQL at all, and whether the
DEFAULT-ACCOUNT path (migration 013, session_id NULLABLE on the seven children) is
available -- that path is the one a BotRunner bot can use, because a bot has no
paper_sessions row.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

#: `pgserver` picks a new port on every restart, so resolve rather than hard-code.
#: Precedence matches staging/dsn.py: the explicit variable beats the file.
import pathlib  # noqa: E402

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or (pathlib.Path(__file__).resolve().parent / "pg_dsn.txt")
    .read_text(encoding="utf-8")
    .strip()
)

import psycopg2  # noqa: E402

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()

PAPER_TABLES = [
    "paper_sessions", "paper_accounts", "paper_orders", "paper_fills",
    "paper_positions", "paper_balance_events", "paper_trades",
    "paper_equity_snapshots", "paper_metrics", "paper_events",
    "paper_market_events", "paper_order_allowed_transitions",
    "paper_session_allowed_transitions",
]

print("TABLE PRESENCE")
print("=" * 78)
present = []
for name in PAPER_TABLES:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        " WHERE table_schema='public' AND table_name=%s)", (name,)
    )
    ok = cur.fetchone()[0]
    if ok:
        present.append(name)
    print(f"  {'YES' if ok else 'NO ':<4} {name}")

print(f"\n{len(present)}/{len(PAPER_TABLES)} present")

if "paper_orders" not in present:
    print("\nVERDICT: migration 009 is NOT applied. The canonical ledger does not exist "
          "in this database, so PAPER-01 cannot be verified against real PostgreSQL here.")
    raise SystemExit(0)

print()
print("MIGRATION 013 - session_id NULLABILITY ON THE SEVEN CHILDREN")
print("=" * 78)
print("(NULLABLE = the default account can own rows without a paper_sessions row,")
print(" which is the only path a BotRunner bot can use)")
CHILDREN = ["paper_orders", "paper_fills", "paper_positions",
            "paper_balance_events", "paper_trades", "paper_equity_snapshots",
            "paper_metrics"]
relaxed = 0
for name in CHILDREN:
    if name not in present:
        print(f"  {name:<24} (table absent)")
        continue
    cur.execute(
        "SELECT is_nullable FROM information_schema.columns "
        " WHERE table_schema='public' AND table_name=%s AND column_name='session_id'",
        (name,),
    )
    row = cur.fetchone()
    val = row[0] if row else "NO COLUMN"
    if val == "YES":
        relaxed += 1
    print(f"  {name:<24} session_id is_nullable={val}")
print(f"\n{relaxed}/{len(CHILDREN)} relaxed -> "
      f"{'013 APPLIED' if relaxed == len(CHILDREN) else '013 NOT (fully) APPLIED'}")

print()
print("THE IDEMPOTENCY INDEXES")
print("=" * 78)
cur.execute(
    "SELECT indexname, indexdef FROM pg_indexes "
    " WHERE schemaname='public' AND tablename IN "
    " ('paper_orders','paper_fills','paper_positions','paper_accounts') "
    " ORDER BY tablename, indexname"
)
for name, definition in cur.fetchall():
    print(f"  {name}")
    print(f"      {definition}")

print()
print("APPEND-ONLY GUARD + TRANSITION TRIGGERS")
print("=" * 78)
cur.execute(
    "SELECT c.relname, t.tgname FROM pg_trigger t "
    "  JOIN pg_class c ON c.oid = t.tgrelid "
    " WHERE NOT t.tgisinternal AND c.relname LIKE 'paper%' "
    " ORDER BY c.relname, t.tgname"
)
rows = cur.fetchall()
if not rows:
    print("  (none)")
for table, trigger in rows:
    print(f"  {table:<26} {trigger}")

print()
print("SEEDED TRANSITION ROWS")
print("=" * 78)
for name in ("paper_order_allowed_transitions", "paper_session_allowed_transitions"):
    if name not in present:
        continue
    cur.execute(f"SELECT count(*) FROM public.{name}")
    print(f"  {name:<36} {cur.fetchone()[0]} rows")
