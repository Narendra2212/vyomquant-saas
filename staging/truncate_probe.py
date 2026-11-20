"""Can the browser's anon role TRUNCATE an RLS-protected table?

WHY THIS IS A DIFFERENT QUESTION FROM THE ROW-LEVEL ONE. Row level security filters rows.
`TRUNCATE` does not touch rows individually -- it is a table-level operation, and
PostgreSQL does not apply row policies to it. So a table can have RLS enabled, a correct
owner-access policy, and still be emptied in full by any role holding the TRUNCATE
privilege.

Supabase's default `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated,
service_role` includes TRUNCATE. The earlier sweeps asked "can anon READ another
customer's rows?" and, after the hardening, the answer became no. This asks the other
question.

Every attempt runs inside a SAVEPOINT and is rolled back, so nothing is actually
destroyed. The outcome is what matters, not the effect.
"""

from __future__ import annotations

import pathlib
import sys

import psycopg2

from dsn import resolve_dsn

REPO = pathlib.Path(__file__).resolve().parent.parent

#: A spread: RLS-enabled with an owner policy, RLS-enabled financial, and the identity
#: table everything references.
TARGETS = (
    "profiles",
    "strategies",
    "orders",
    "execution_records",
    "referral_wallets",
    "paper_orders",
    "invoices",
)


def main() -> int:
    url = resolve_dsn()
    conn = psycopg2.connect(url)
    conn.autocommit = False
    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    emit("=" * 78)
    emit("TRUNCATE PROBE -- does RLS stop a table-level wipe? (it does not)")
    emit("=" * 78)
    emit("Each attempt is rolled back. Nothing is destroyed.")
    emit()

    vulnerable: list[str] = []
    with conn.cursor() as cur:
        for table in TARGETS:
            cur.execute(
                "SELECT c.relrowsecurity, "
                "  (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=%s",
                (table,),
            )
            row = cur.fetchone()
            if not row:
                emit(f"  {table:<26} ABSENT")
                continue
            rls, npol = row

            # Plain TRUNCATE and TRUNCATE ... CASCADE are tried separately. Several
            # tables refuse the plain form only because a foreign key references them,
            # which is incidental protection rather than a security control -- CASCADE
            # asks PostgreSQL to truncate the referencing tables too, and the role holds
            # TRUNCATE on those as well. If CASCADE succeeds where plain did not, the FK
            # was never protecting anything.
            for role in ("anon", "authenticated"):
                for variant, suffix in (("TRUNCATE", ""), ("TRUNCATE CASCADE", " CASCADE")):
                    cur.execute("SAVEPOINT tprobe")
                    try:
                        cur.execute("SELECT set_config('request.jwt.claim.sub', '', true)")
                        cur.execute(f"SET LOCAL ROLE {role}")
                        cur.execute(f"TRUNCATE TABLE public.{table}{suffix}")
                        verdict = f"*** {variant} SUCCEEDED ***"
                        vulnerable.append(f"{table} (as {role}, {variant})")
                    except Exception as exc:  # noqa: BLE001
                        first = str(exc).strip().splitlines()[0]
                        verdict = f"refused: {first[:62]}"
                    finally:
                        cur.execute("ROLLBACK TO SAVEPOINT tprobe")
                        try:
                            cur.execute("RESET ROLE")
                        except Exception:  # noqa: BLE001
                            pass
                    emit(
                        f"  {table:<22} rls={rls!s:<5} pol={npol:<3} "
                        f"{role:<14} {variant:<17} {verdict}"
                    )
            emit()

    conn.rollback()
    conn.close()

    emit("=" * 78)
    if vulnerable:
        emit(f"{len(vulnerable)} TABLE/ROLE PAIR(S) CAN BE WIPED BY A BROWSER CREDENTIAL:")
        for v in vulnerable:
            emit(f"  * {v}")
        emit()
        emit("Row level security does not filter TRUNCATE. The privilege itself has to go.")
    else:
        emit("No TRUNCATE succeeded for anon or authenticated.")
    emit("=" * 78)

    (REPO / "staging" / "truncate_probe.txt").write_text("\n".join(out), encoding="utf-8")
    return 1 if vulnerable else 0


if __name__ == "__main__":
    sys.exit(main())
