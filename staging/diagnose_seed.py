"""Seed one table through the production probe's own code path and print the row.

The probe reported `notifications` as NO CONTROL -- the owner could not read a row it had
just created, on a table whose policy is the simplest possible `auth.uid() = user_id`.
That is either a schema finding or a seeder bug, and the two are distinguished by looking
at what was actually written.

    python staging/diagnose_seed.py notifications
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg2

from dsn import resolve_dsn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.owner_columns import derive_owner_columns  # noqa: E402
from scripts.verify_production_security import (  # noqa: E402
    _choose_owner_column,
    _ensure_identity,
    _seed,
)

TABLE = sys.argv[1] if len(sys.argv) > 1 else "notifications"


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = False
    owner = str(uuid.uuid4())

    try:
        with conn.cursor() as cur:
            owner_columns, _ = derive_owner_columns(conn)
            _ensure_identity(cur, owner)

            owner_col = _choose_owner_column(cur, TABLE, owner_columns)
            print(f"table        : {TABLE}")
            print(f"owner identity: {owner}")
            print(f"owner_column : {owner_col}")
            print(f"ownership columns present on this table: "
                  f"{[c for c in owner_columns if c]}"[:0] or "", end="")
            print()

            print(f"'{owner_col}' in owner_columns? "
                  f"{owner_col in owner_columns}")
            cur.execute(
                "SELECT att.attname, fn.nspname, fc.relname FROM pg_constraint con "
                "JOIN pg_class c ON c.oid=con.conrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' "
                "JOIN pg_class fc ON fc.oid=con.confrelid "
                "JOIN pg_namespace fn ON fn.oid=fc.relnamespace "
                "CROSS JOIN LATERAL unnest(con.conkey) k(attnum) "
                "JOIN pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=k.attnum "
                "WHERE con.contype='f' AND c.relname=%s",
                (TABLE,),
            )
            print(f"foreign keys: {cur.fetchall()}")

            _seed(cur, TABLE, owner, owner_columns, primary_owner=owner_col)
            print("last statement the seeder executed:")
            print("  " + (cur.query.decode() if isinstance(cur.query, bytes)
                          else str(cur.query))[:600])

            cur.execute(
                f'SELECT "{owner_col}"::text FROM public."{TABLE}" ORDER BY 1'
            )
            rows = [r[0] for r in cur.fetchall()]
            print(f"rows now in {TABLE}: {len(rows)}")
            print(f"  values of {owner_col}: {rows}")
            print(f"  does any equal the owner? {owner in rows}")

            cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner,))
            cur.execute("SELECT set_config('app.current_tenant_id', %s, true)", (owner,))
            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute("SELECT auth.uid()::text")
            print(f"  auth.uid() as authenticated = {cur.fetchone()[0]!r}")
            cur.execute(
                f'SELECT count(*) FROM public."{TABLE}" WHERE "{owner_col}"::text = %s',
                (owner,),
            )
            print(f"  visible to the owner: {cur.fetchone()[0]}")
            cur.execute("RESET ROLE")
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
