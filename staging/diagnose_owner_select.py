"""Why does the owner's own-row SELECT return nothing in the isolation probe?

The probe's positive control failed on 15 tables. That matters more than the negative
results: if the owner cannot see its own row either, then "B saw nothing" may mean the
policy discriminates, or it may mean nobody can see anything -- and those are an
isolation guarantee and an outage respectively, producing identical output.

This reproduces one table's positive control step by step, printing what the server
thinks the session identity is at each point, so the cause is read rather than guessed.
"""

from __future__ import annotations

import sys
import uuid

import psycopg2

from dsn import resolve_dsn

TABLE = sys.argv[1] if len(sys.argv) > 1 else "profiles"


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = False
    owner = str(uuid.uuid4())

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (owner, f"diag-{owner[:8]}@example.invalid"),
            )
            cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (owner,))

            print(f"table under test : {TABLE}")
            print(f"owner identity   : {owner}")
            print()

            cur.execute(f'SELECT count(*) FROM public."{TABLE}"')
            print(f"as owner role, rows visible: {cur.fetchone()[0]}")

            # Install the identity the probe installs.
            cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner,))
            cur.execute("SELECT set_config('app.current_tenant_id', %s, true)", (owner,))

            cur.execute("SELECT current_setting('request.jwt.claim.sub', true)")
            print(f"request.jwt.claim.sub = {cur.fetchone()[0]!r}")
            cur.execute("SELECT auth.uid()")
            print(f"auth.uid()            = {cur.fetchone()[0]!r}")

            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute("SELECT current_user, current_setting('request.jwt.claim.sub', true)")
            who, claim = cur.fetchone()
            print(f"after SET LOCAL ROLE  : current_user={who} claim={claim!r}")
            cur.execute("SELECT auth.uid()")
            print(f"auth.uid() as {who:<14}= {cur.fetchone()[0]!r}")

            for label, sql, params in (
                ("count(*)", f'SELECT count(*) FROM public."{TABLE}"', None),
                ("where id::text = owner",
                 f'SELECT count(*) FROM public."{TABLE}" WHERE "id"::text = %s', (owner,)),
                ("where id = auth.uid()",
                 f'SELECT count(*) FROM public."{TABLE}" WHERE "id" = auth.uid()', None),
            ):
                cur.execute("SAVEPOINT s")
                try:
                    cur.execute(sql, params)
                    print(f"  {label:<26} -> {cur.fetchone()[0]}")
                    cur.execute("RELEASE SAVEPOINT s")
                except psycopg2.Error as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT s")
                    print(f"  {label:<26} -> ERROR {exc.pgcode} "
                          f"{str(exc).splitlines()[0][:70]}")

            cur.execute("RESET ROLE")
            cur.execute(
                "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
                "FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND table_name=%s AND grantee='authenticated'",
                (TABLE,),
            )
            print(f"authenticated grants on {TABLE}: {cur.fetchone()[0]}")
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
