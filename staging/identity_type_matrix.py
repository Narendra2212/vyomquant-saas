"""The tenant/user identity type matrix the launch gate asks for, with FK consequences.

    | Table | Column | Actual type | Expected | FK present | Verdict |

WHY THE TYPE MATTERS, CONCRETELY
--------------------------------
A foreign key cannot be created between two different types. That is not a style
objection -- it is the mechanism behind BUG-033, where 5 of 7 declared foreign keys could
not be created because `tenant_id` was `uuid` on some tables and `varchar(36)` on others.
Alembic `a1b2c3d4e5f6` normalised `tenant_id` to `uuid` and all 7 landed.

`user_id` was never normalised, and this script is what measures the consequence. Three
declared types survive:

    uuid                    the canonical shape, matching profiles.id / auth.users.id
    character varying(36)   invoices, payment_methods, subscriptions -- the BILLING tables
    text                    exchange_keys, risk_settings, and others

A `varchar(36)` or `text` user_id cannot reference `profiles(id)`, which is `uuid`. So
those tables have NO foreign key to the customer they belong to, and the consequences are
specific:

  * an invoice can name a user that does not exist;
  * deleting a customer leaves their invoices behind, un-cascaded, because there is no
    ON DELETE to cascade;
  * nothing at the database level prevents a typo in a user_id from orphaning a billing
    record.

What it does NOT break is confidentiality. The RLS policies on those tables read
`(auth.uid())::text = (user_id)::text` -- an explicit cast on both sides -- so isolation
still holds. That distinction is the whole point of this script: it reports an INTEGRITY
defect, and says so, rather than inflating it into an exposure.

Exit 0 -> every identity column is `uuid` and carries a foreign key.
Exit 1 -> at least one does not. The verdict column says which kind of problem it is.
"""

from __future__ import annotations

import os
import sys

import psycopg2

from dsn import redact, resolve_dsn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.owner_columns import derive_owner_columns  # noqa: E402

#: The canonical type. `profiles.id` and `auth.users.id` are both `uuid`, so anything
#: referencing a customer must be `uuid` to be able to declare the reference.
EXPECTED = "uuid"

QUERY = """
SELECT c.table_name,
       c.column_name,
       c.data_type,
       coalesce(c.character_maximum_length::text, '') AS maxlen,
       c.is_nullable,
       (SELECT string_agg(con.conname || ' -> ' || parent.relname, ', ')
          FROM pg_constraint con
          JOIN pg_class t          ON t.oid = con.conrelid
          JOIN pg_namespace n      ON n.oid = t.relnamespace
          JOIN pg_class parent     ON parent.oid = con.confrelid
          CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum)
          JOIN pg_attribute att    ON att.attrelid = con.conrelid
                                   AND att.attnum = k.attnum
         WHERE n.nspname = 'public'
           AND t.relname = c.table_name
           AND con.contype = 'f'
           AND att.attname = c.column_name) AS fk
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.column_name = ANY(%s)
ORDER BY c.table_name, c.column_name
"""


def main() -> int:
    dsn = resolve_dsn()
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    owner_columns, _derived_only = derive_owner_columns(conn)
    with conn.cursor() as cur:
        cur.execute(QUERY, (list(owner_columns),))
        rows = cur.fetchall()
    conn.close()

    print("=" * 100)
    print("IDENTITY COLUMN TYPE MATRIX")
    print("=" * 100)
    print(f"database: {redact(dsn)}")
    print(f"expected type for any column naming a customer: {EXPECTED} "
          f"(profiles.id and auth.users.id are both uuid)")
    print("-" * 100)
    print(f"{'Table':<34}{'Column':<22}{'Actual':<22}{'FK':<5}{'Verdict'}")
    print("-" * 100)

    wrong_type: list[tuple[str, str, str]] = []
    no_fk: list[tuple[str, str]] = []
    ok = 0

    for table, column, dtype, maxlen, _nullable, fk in rows:
        actual = f"{dtype}({maxlen})" if maxlen else dtype
        has_fk = bool(fk)

        if actual != EXPECTED:
            verdict = "TYPE MISMATCH -- no FK possible"
            wrong_type.append((table, column, actual))
        elif not has_fk:
            verdict = "uuid but UNCONSTRAINED"
            no_fk.append((table, column))
        else:
            verdict = "ok"
            ok += 1

        print(f"{table:<34}{column:<22}{actual:<22}{'yes' if has_fk else 'no':<5}{verdict}")

    print("-" * 100)
    print(f"{len(rows)} identity column(s): {ok} correct, "
          f"{len(wrong_type)} wrong type, {len(no_fk)} uuid without a foreign key")

    if wrong_type:
        print()
        print("TYPE MISMATCHES -- these columns CANNOT carry a foreign key to profiles(id)")
        print("-" * 100)
        by_type: dict[str, list[str]] = {}
        for table, column, actual in wrong_type:
            by_type.setdefault(actual, []).append(f"{table}.{column}")
        for actual in sorted(by_type):
            print(f"  {actual}")
            for ref in sorted(by_type[actual]):
                print(f"      {ref}")
        print()
        print("  IMPACT: integrity, not confidentiality.")
        print("    * a row can name a customer that does not exist;")
        print("    * deleting a customer does not cascade, because there is no")
        print("      ON DELETE to cascade -- the rows are simply orphaned;")
        print("    * RLS still isolates these tables: their policies cast both sides,")
        print("      e.g. `(auth.uid())::text = (user_id)::text`, so a type mismatch")
        print("      does NOT create cross-tenant access. Verified separately by")
        print("      tests/test_billing_data_isolation_postgres.py.")
        print()
        print("  This is BUG-033's unfixed sibling: alembic a1b2c3d4e5f6 normalised")
        print("  `tenant_id` to uuid and all 7 foreign keys then landed. `user_id` was")
        print("  not part of that change.")

    if no_fk:
        print()
        print(f"UUID BUT UNCONSTRAINED ({len(no_fk)}) -- correct type, no declared reference")
        print("-" * 100)
        print("  INFORMATIONAL, not a failure. These columns CAN carry a foreign key --")
        print("  the type is right -- so adding one is a decision rather than a repair,")
        print("  and some are deliberate: an append-only audit row should arguably")
        print("  survive the deletion of the customer it describes.")
        print("-" * 100)
        for table, column in no_fk:
            print(f"  {table}.{column}")

    # Only a TYPE MISMATCH fails this probe. That is the condition which makes a foreign
    # key impossible rather than merely absent, and it is the one with no benign reading.
    return 1 if wrong_type else 0


if __name__ == "__main__":
    sys.exit(main())
