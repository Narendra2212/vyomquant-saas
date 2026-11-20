"""Execute the billing access matrix against real PostgreSQL.

WHAT THIS ANSWERS, AND WHY IT HAS TO BE EXECUTED. `SELECT relrowsecurity` tells you a
flag. It does not tell you whether a browser-held credential can read another customer's
invoices, because that outcome is decided by three independent things at once: the role
PostgREST resolves the request to, the table privileges that role holds, and the row
policies (if any). A table can have RLS "enabled" and still be fully readable if no
policy restricts the role; a table can have RLS disabled and be unreadable if the role
holds no privilege. Only running the statements separates those cases.

THE ROLES, AND WHICH ONE IS THE BROWSER. `core/dependencies.py:242-281`
(`create_request_supabase_async`) builds a PostgREST client with
`apiKey: SUPABASE_ANON_KEY` and `Authorization: Bearer <end user JWT>`. PostgREST resolves
that to the `authenticated` role with `auth.uid()` populated from the token. With no
token, the same anon key resolves to `anon`. The anon key ships in the browser bundle, so
`anon` is a credential every visitor holds, and `authenticated` is a credential every
registered user holds -- including one who wants to read someone else's invoices.

So the matrix below is: {anon, authenticated as A, authenticated as B, service_role} x
{read all, read another user's row by primary key, insert for self, insert for another
user, update another user's row, delete another user's row}.

Each statement runs inside a SAVEPOINT and is rolled back, so the probe observes the real
answer without leaving anything behind. The outcome is classified as one of:

    VISIBLE / ALLOWED  - the statement succeeded, and for reads returned rows
    EMPTY              - permitted but filtered to zero rows (a policy did its job)
    DENIED_PRIVILEGE   - refused by GRANT (permission denied for table ...)
    DENIED_POLICY      - refused by row security (violates row-level security policy)
    ERROR              - anything else, reported verbatim

The distinction between DENIED_PRIVILEGE and DENIED_POLICY is kept because they are
different controls with different failure modes, and a report that conflates them is
telling the reader something untrue about why they are safe.
"""

from __future__ import annotations

import pathlib
import sys
import uuid

import psycopg2

from dsn import resolve_dsn

REPO = pathlib.Path(__file__).resolve().parent.parent

TABLES = ("invoices", "payment_methods", "subscriptions")

USER_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

#: Minimal valid rows. Every column on these three tables is NOT NULL with no default,
#: so nothing can be omitted. Note user_id is `character varying`, not uuid -- which is
#: itself load bearing for the fix, because `auth.uid()` is uuid and the comparison
#: therefore needs a cast.
def _row(table: str, owner: str, tag: str) -> dict:
    if table == "invoices":
        return {
            "id": f"inv-{tag}", "user_id": owner, "date": "2026-10-06",
            "amtUSD": 49.0, "amtINR": 4100.0, "status": "paid",
            "created_at": "2026-10-06 00:00:00",
        }
    if table == "payment_methods":
        return {
            "id": f"pm-{tag}", "user_id": owner,
            "payment_method_id": f"pm_stripe_{tag}", "brand": "visa",
            "last4": "4242", "expiry_month": 12, "expiry_year": 2030,
            "is_default": True, "created_at": "2026-10-06",
        }
    return {
        "id": f"sub-{tag}", "user_id": owner, "plan_id": "pro",
        "name": "Pro", "priceUSD": 49.0, "priceINR": 4100.0,
        "autoRenew": True,
        "created_at": "2026-10-06 00:00:00",
        "updated_at": "2026-10-06 00:00:00",
    }


def _quote(col: str) -> str:
    # amtUSD / priceINR / autoRenew / nextBillingDate are camelCase, so every identifier
    # is quoted rather than guessing which ones need it.
    return f'"{col}"'


class Probe:
    def __init__(self, conn):
        self.conn = conn
        self.out: list[str] = []

    def emit(self, s: str = "") -> None:
        self.out.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    def _classify(self, exc: Exception) -> tuple[str, str]:
        msg = str(exc).strip().splitlines()[0]
        low = msg.lower()
        if "permission denied" in low:
            return "DENIED_PRIVILEGE", msg
        if "row-level security" in low or "row level security" in low:
            return "DENIED_POLICY", msg
        return "ERROR", msg

    def run_as(self, role: str, claim: str | None, sql: str, params=None):
        """Execute one statement under an adopted identity; always roll back."""
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT probe")
            try:
                cur.execute(
                    "SELECT set_config('request.jwt.claim.sub', %s, true)",
                    (claim or "",),
                )
                cur.execute(f"SET LOCAL ROLE {role}")
                cur.execute(sql, params or ())
                if cur.description is not None:
                    rows = cur.fetchall()
                    return ("VISIBLE" if rows else "EMPTY"), rows
                return ("ALLOWED", cur.rowcount)
            except Exception as exc:  # noqa: BLE001 - classification is the output
                kind, msg = self._classify(exc)
                return kind, msg
            finally:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT probe")
                    cur.execute("RESET ROLE")
                except Exception:  # noqa: BLE001
                    pass


def main() -> int:
    url = resolve_dsn()
    conn = psycopg2.connect(url)
    conn.autocommit = False
    p = Probe(conn)

    # ── seed, as owner ────────────────────────────────────────────────────────
    tag = uuid.uuid4().hex[:8]
    with conn.cursor() as cur:
        for owner in (USER_A, USER_B):
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (owner, f"{owner[:8]}@example.test"),
            )
            cur.execute(
                "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (owner,),
            )
        for table in TABLES:
            for owner, who in ((USER_A, "a"), (USER_B, "b")):
                row = _row(table, owner, f"{tag}-{who}")
                cols = ", ".join(_quote(c) for c in row)
                ph = ", ".join(["%s"] * len(row))
                cur.execute(
                    f"INSERT INTO public.{table} ({cols}) VALUES ({ph})",
                    tuple(row.values()),
                )
    conn.commit()

    p.emit("=" * 78)
    p.emit("BILLING ACCESS MATRIX -- executed against real PostgreSQL")
    p.emit("=" * 78)
    p.emit(f"user A = {USER_A}")
    p.emit(f"user B = {USER_B}")
    p.emit("Two billing rows per table, one owned by each user.")
    p.emit()

    # ── context: RLS flags, policies, privileges ──────────────────────────────
    with conn.cursor() as cur:
        p.emit("-" * 78)
        p.emit("CONTEXT")
        p.emit("-" * 78)
        for table in TABLES:
            cur.execute(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                "  (SELECT count(*) FROM pg_policy pol WHERE pol.polrelid = c.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=%s",
                (table,),
            )
            rls, force, npol = cur.fetchone()
            cur.execute(
                "SELECT grantee, string_agg(DISTINCT privilege_type, ',' ORDER BY privilege_type) "
                "FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND table_name=%s "
                "  AND grantee IN ('anon','authenticated','service_role') "
                "GROUP BY grantee ORDER BY grantee",
                (table,),
            )
            grants = {g: pr for g, pr in cur.fetchall()}
            p.emit(f"{table}")
            p.emit(f"    RLS enabled={rls}  forced={force}  policies={npol}")
            p.emit(f"    anon          = {grants.get('anon','(none)')}")
            p.emit(f"    authenticated = {grants.get('authenticated','(none)')}")
            p.emit(f"    service_role  = {grants.get('service_role','(none)')}")
        p.emit()

    # ── the matrix ────────────────────────────────────────────────────────────
    findings: list[str] = []
    for table in TABLES:
        p.emit("=" * 78)
        p.emit(f"TABLE: {table}")
        p.emit("=" * 78)

        a_id = f"{'inv' if table=='invoices' else 'pm' if table=='payment_methods' else 'sub'}-{tag}-a"
        b_id = f"{'inv' if table=='invoices' else 'pm' if table=='payment_methods' else 'sub'}-{tag}-b"

        cases = [
            ("anon", None, "read ALL rows",
             f"SELECT user_id FROM public.{table}", None),
            ("anon", None, "read user A's row by primary key",
             f"SELECT user_id FROM public.{table} WHERE id = %s", (a_id,)),
            ("authenticated", USER_A, "read ALL rows",
             f"SELECT user_id FROM public.{table}", None),
            ("authenticated", USER_A, "read user B's row by primary key  <-- CROSS-TENANT READ",
             f"SELECT user_id FROM public.{table} WHERE id = %s", (b_id,)),
            ("authenticated", USER_B, "read user A's row by primary key  <-- CROSS-TENANT READ",
             f"SELECT user_id FROM public.{table} WHERE id = %s", (a_id,)),
            ("authenticated", USER_A, "UPDATE user B's row  <-- CROSS-TENANT WRITE",
             f"UPDATE public.{table} SET user_id = %s WHERE id = %s", (USER_A, b_id)),
            ("authenticated", USER_A, "DELETE user B's row  <-- CROSS-TENANT DELETE",
             f"DELETE FROM public.{table} WHERE id = %s", (b_id,)),
            ("service_role", None, "read ALL rows (expected: all, BYPASSRLS)",
             f"SELECT user_id FROM public.{table}", None),
        ]

        # INSERT forging another user's ownership.
        forged = _row(table, USER_B, f"{tag}-forged")
        fcols = ", ".join(_quote(c) for c in forged)
        fph = ", ".join(["%s"] * len(forged))
        cases.insert(
            7,
            ("authenticated", USER_A,
             "INSERT a row owned by user B  <-- FORGED OWNERSHIP",
             f"INSERT INTO public.{table} ({fcols}) VALUES ({fph})",
             tuple(forged.values())),
        )

        for role, claim, label, sql, params in cases:
            kind, detail = p.run_as(role, claim, sql, params)
            ident = f"{role}" + (f" as {claim[:8]}" if claim else "")
            if isinstance(detail, list):
                owners = sorted({r[0] for r in detail})
                shown = f"{len(detail)} row(s) owners={owners}"
            else:
                shown = str(detail)[:90]
            p.emit(f"  [{kind:<17}] {ident:<26} {label}")
            p.emit(f"                       -> {shown}")

            # Record the dangerous outcomes.
            cross = "CROSS-TENANT" in label or "FORGED" in label
            if role == "anon" and kind == "VISIBLE":
                findings.append(f"{table}: ANON can {label} ({shown})")
            if role == "authenticated" and cross and kind in ("VISIBLE", "ALLOWED"):
                if not (isinstance(detail, int) and detail == 0):
                    findings.append(f"{table}: authenticated can {label} ({shown})")
            if role == "authenticated" and not cross and kind == "VISIBLE" and "ALL rows" in label:
                owners = sorted({r[0] for r in detail}) if isinstance(detail, list) else []
                if len(owners) > 1:
                    findings.append(
                        f"{table}: authenticated as A sees {len(owners)} owners in a full read"
                    )
        p.emit()

    # ── cleanup ───────────────────────────────────────────────────────────────
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(
                f"DELETE FROM public.{table} WHERE user_id IN (%s, %s)", (USER_A, USER_B)
            )
    conn.commit()
    conn.close()

    p.emit("=" * 78)
    p.emit("RESULT")
    p.emit("=" * 78)
    if findings:
        p.emit(f"{len(findings)} EXPOSURE(S) CONFIRMED BY EXECUTION:")
        for f in findings:
            p.emit(f"  * {f}")
    else:
        p.emit("No cross-customer or anonymous access succeeded.")

    (REPO / "staging" / "billing_exposure.txt").write_text("\n".join(p.out), encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
