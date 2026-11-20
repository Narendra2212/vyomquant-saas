"""Find the SPECIFIC statement responsible for each of the 7 disappearances.

PHASE 11B / WORKSTREAM 1C — read-only, SQL text analysis plus one read-only query.

WHY THIS REPLACES THE EARLIER CHECK
-----------------------------------
`probe_p11b_w1c_drift_forensics.py` reported all 7 disappearances as "EXPLAINED", but its
method was to extract identifiers from the item and look for any of them in the text of
the migrations applied after the snapshot was captured. That matched the bare table name
`library_strategies` in six unrelated files and reported every disappearance as explained
by all six migrations. "Explained by everything" is not an explanation -- it is a token
collision, and accepting it would have let a genuine out-of-band schema change pass as
stale-baseline noise.

The distinction matters more than any other question in this workstream:

  * if a later migration REVOKES or DROPS the item, the baseline is merely old and the
    database is correct;
  * if NOTHING in the migration set does, then the item was changed outside the
    migration set -- the Supabase dashboard is the usual route -- and the schema in front
    of us is not the schema this repository produces.

So this looks for the ACTUAL DDL: a `REVOKE` naming both the privilege and the grantee on
that table, a `DROP POLICY` naming that policy, or a `CREATE OR REPLACE FUNCTION` naming
that function. It searches the WHOLE migration set, not only the late ones, because an
item can also disappear by a migration that was recorded earlier but is idempotent and
re-run.

READ-ONLY: parses .sql text, and issues one read-only query to confirm the current state
of each item.
"""

from __future__ import annotations

import ipaddress
import pathlib
import re
import sys
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = REPO / "staging" / "pg_dsn.txt"
ROOTS = [REPO / "migrations", REPO / "backend_app" / "migrations",
         REPO / "archived_migrations"]

#: The 7 items present in `staging/provisioning_snapshot.json` and absent from the live
#: database, exactly as `verify_schema_drift.py` reported them.
DISAPPEARANCES = [
    ("policy", "library_strategies", "Users can update their own strategies", "UPDATE"),
    ("privilege", "library_strategies", "anon", "DELETE"),
    ("privilege", "library_strategies", "anon", "INSERT"),
    ("privilege", "library_strategies", "anon", "UPDATE"),
    ("privilege", "library_strategies", "authenticated", "INSERT"),
    ("privilege", "library_strategies", "authenticated", "UPDATE"),
    ("function", "process_referral_commission", None, None),
]

LATE = {"026_fee_records_row_level_security.sql",
        "027_referral_ledger_currency.sql",
        "028_safety_control_audit.sql",
        "029_referral_obligations.sql",
        "030_execution_venue_order_id.sql",
        "031_fee_records_fee_pct_nullable.sql"}


def _sql_files() -> dict[str, tuple[pathlib.Path, str]]:
    out = {}
    for root in ROOTS:
        if root.exists():
            for p in sorted(root.rglob("*.sql")):
                out[p.name] = (p, p.read_text(encoding="utf-8", errors="replace"))
    return out


def _connect():
    import psycopg2
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    parsed = urlparse(dsn)
    host = (parsed.hostname or "").lower()
    if parsed.port == 6543:
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
    files = _sql_files()
    print("=" * 78)
    print("THE 7 DISAPPEARANCES -- WHICH STATEMENT IS RESPONSIBLE?")
    print("=" * 78)
    print(f"   .sql files searched: {len(files)}")
    print(f"   migrations applied AFTER the snapshot capture: {len(LATE)}\n")

    conn = _connect()
    cur = conn.cursor()
    unexplained = []

    for kind, target, who, verb in DISAPPEARANCES:
        label = (f"{kind} {target}.{who}.{verb}" if kind == "privilege"
                 else f"{kind} {target}" + (f" / {who!r}" if who else ""))
        print("-" * 78)
        print(f"{label}")
        print("-" * 78)

        # ---- confirm it really is gone, live -------------------------------
        if kind == "privilege":
            cur.execute("""
                select count(*) from information_schema.table_privileges
                where table_schema='public' and table_name=%s
                  and grantee=%s and privilege_type=%s
            """, (target, who, verb))
            live_n = cur.fetchone()[0]
            cur.execute("""
                select privilege_type from information_schema.table_privileges
                where table_schema='public' and table_name=%s and grantee=%s
                order by 1
            """, (target, who))
            still = [r[0] for r in cur.fetchall()]
            print(f"   live: this grant present = {bool(live_n)};  "
                  f"{who} still holds {still or 'nothing'} on {target}")
            pattern = re.compile(
                rf"revoke\b[^;]*\b{re.escape(verb)}\b[^;]*\bon\b[^;]*"
                rf"\b{re.escape(target)}\b[^;]*\bfrom\b[^;]*\b{re.escape(who)}\b",
                re.IGNORECASE | re.DOTALL)
            broad = re.compile(
                rf"revoke\b[^;]*\ball\b[^;]*\bon\b[^;]*\b{re.escape(target)}\b"
                rf"[^;]*\bfrom\b[^;]*\b{re.escape(who)}\b",
                re.IGNORECASE | re.DOTALL)
            allsch = re.compile(
                rf"revoke\b[^;]*\ball\b[^;]*\ball tables in schema public\b"
                rf"[^;]*\bfrom\b[^;]*\b{re.escape(who)}\b",
                re.IGNORECASE | re.DOTALL)
            patterns = [("explicit REVOKE", pattern), ("REVOKE ALL on table", broad),
                        ("REVOKE ALL on schema", allsch)]
        elif kind == "policy":
            cur.execute("""
                select count(*) from pg_policies
                where schemaname='public' and tablename=%s and policyname=%s
            """, (target, who))
            print(f"   live: this policy present = {bool(cur.fetchone()[0])}")
            cur.execute("""
                select policyname, cmd, roles::text from pg_policies
                where schemaname='public' and tablename=%s order by policyname
            """, (target,))
            print(f"   live policies on {target}:")
            for pn, cmd, roles in cur.fetchall():
                print(f"     {pn!r:52} {cmd:8} {roles}")
            patterns = [("DROP POLICY", re.compile(
                rf"drop\s+policy\b[^;]*{re.escape(who)}", re.IGNORECASE | re.DOTALL))]
        else:
            cur.execute("""
                select p.oid::bigint, pg_get_function_identity_arguments(p.oid),
                       p.prosecdef, p.provolatile
                from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                where n.nspname='public' and p.proname=%s
            """, (target,))
            rows = cur.fetchall()
            print(f"   live overloads of {target}: {len(rows)}")
            for oid, args, secdef, vol in rows:
                print(f"     oid={oid} secdef={secdef} volatile={vol}")
                print(f"       args: {args[:96]}")
            patterns = [("CREATE OR REPLACE FUNCTION", re.compile(
                rf"create\s+or\s+replace\s+function\s+(?:public\.)?{re.escape(target)}",
                re.IGNORECASE)),
                ("DROP FUNCTION", re.compile(
                    rf"drop\s+function\b[^;]*{re.escape(target)}",
                    re.IGNORECASE | re.DOTALL))]

        # ---- find the responsible statement --------------------------------
        found = []
        for name, (path, text) in files.items():
            for plabel, pat in patterns:
                m = pat.search(text)
                if m:
                    line = text[:m.start()].count("\n") + 1
                    snippet = " ".join(m.group(0).split())[:118]
                    found.append((name, plabel, line, snippet,
                                  name in LATE))
        print(f"   responsible statements found: {len(found)}")
        for name, plabel, line, snippet, is_late in sorted(found):
            when = "AFTER capture " if is_late else "before capture"
            print(f"     [{when}] {name}:{line}  ({plabel})")
            print(f"        {snippet}")
        if not found:
            unexplained.append(label)
            print("     !! NO statement in the entire migration set accounts for this.")
        print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   disappearances examined : {len(DISAPPEARANCES)}")
    print(f"   UNEXPLAINED             : {len(unexplained)}")
    for item in unexplained:
        print(f"     !! {item}")
    if unexplained:
        print("\n   An unexplained disappearance means the live schema differs from what")
        print("   this repository's migrations produce, in a direction a stale baseline")
        print("   cannot cause. That is an OUT-OF-BAND CHANGE and must be treated as a")
        print("   finding, not as drift noise.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
