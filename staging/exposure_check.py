"""For every table with RLS disabled, report whether it holds per-customer data.

WHY THIS IS THE RIGHT QUESTION. In Supabase's model, `anon` and `authenticated` hold
privileges on `public` by default -- Supabase's own bootstrap issues
`GRANT ALL ON ALL TABLES IN SCHEMA public` plus matching `ALTER DEFAULT PRIVILEGES` for
those roles. Row level security, not table privilege, is what separates one customer's
rows from another's on the PostgREST path. A table in `public` with RLS disabled is
therefore readable in full by any holder of the anon key -- which is a public,
browser-shipped credential -- unless some migration explicitly REVOKEs it.

This repository's newer migrations (007, 008, 009, 017, 018, 019, 004d) are careful about
this: each issues explicit per-table GRANTs and an explicit `REVOKE ALL ... FROM anon`.
The older tables carry no grant statements at all, which is consistent with having been
created through the Supabase dashboard, where the defaults apply silently.

So for each RLS-disabled table the questions are: does it contain a column identifying a
customer, and does any migration REVOKE access to it? A table that answers yes/no is a
candidate cross-customer disclosure.

This reports the repository's state. It cannot see production, where RLS may have been
enabled out-of-band through the dashboard; that distinction is preserved in the output.
"""

from __future__ import annotations

import pathlib
import re
import sys

import psycopg2

from dsn import resolve_dsn

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Columns that mean "this row belongs to one customer".
#:
#: `creator_id` was missing from the first version of this list, and its absence hid a
#: real finding: `creator_analytics` carries creator_id plus total_earnings and had RLS
#: off with Supabase's default GRANT ALL, so any anon-key holder could read -- and write
#: -- every creator's earnings. The sweep reported it in the benign group purely because
#: the column was spelled differently. Any name added here should be assumed to be
#: hiding something until the sweep is re-run.
OWNER_COLUMNS = {
    "user_id", "tenant_id", "profile_id", "owner_id", "author_id",
    "customer_id", "subscriber_id", "referrer_id", "created_by",
    "creator_id", "seller_id", "account_id",
}

SQL_DIRS = [
    REPO / "backend_app" / "migrations",
    REPO / "migrations",
    REPO / "backend_app" / "archived_migrations" / "root_migrations",
]


def migration_text() -> str:
    blobs = []
    for d in SQL_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.sql")):
            try:
                blobs.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    vers = REPO / "backend_app" / "alembic" / "versions"
    if vers.exists():
        for p in sorted(vers.glob("*.py")):
            blobs.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(blobs)


def main() -> int:
    url = resolve_dsn()
    corpus = migration_text()

    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
            ORDER BY c.relname
            """
        )
        tables = [r[0] for r in cur.fetchall()]

        cols: dict[str, set[str]] = {}
        counts: dict[str, int] = {}
        for t in tables:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                (t,),
            )
            cols[t] = {r[0] for r in cur.fetchall()}
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            counts[t] = cur.fetchone()[0]

    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    emit("=" * 78)
    emit("RLS-DISABLED TABLES -- per-customer data and explicit REVOKEs")
    emit("=" * 78)
    emit("Verified against the staging database provisioned from this repository.")
    emit("NOT verified against production, where RLS may have been enabled through the")
    emit("Supabase dashboard rather than by a migration in this tree.")
    emit()

    exposed, platform = [], []
    for t in tables:
        owners = sorted(cols[t] & OWNER_COLUMNS)
        revoked = bool(
            re.search(rf"REVOKE\s+ALL\s+ON\s+(public\.)?{re.escape(t)}\b", corpus, re.I)
        )
        granted = bool(
            re.search(rf"GRANT\s+[\w,\s]+ON\s+(public\.)?{re.escape(t)}\b", corpus, re.I)
        )
        row = (t, owners, revoked, granted, counts[t])
        (exposed if owners and not revoked else platform).append(row)

    emit("-" * 78)
    emit("A. PER-CUSTOMER DATA, RLS OFF, NO EXPLICIT REVOKE")
    emit("   Readable in full by any anon-key holder if Supabase default privileges apply.")
    emit("-" * 78)
    if not exposed:
        emit("  (none)")
    for t, owners, _rev, granted, n in exposed:
        emit(f"  {t}")
        emit(f"      owner column(s) : {', '.join(owners)}")
        emit(f"      explicit GRANT in migrations : {'yes' if granted else 'no'}")
        emit(f"      rows in staging : {n}")
    emit()

    emit("-" * 78)
    emit("B. REMAINING RLS-DISABLED TABLES (no per-customer column, or REVOKEd)")
    emit("-" * 78)
    for t, owners, rev, _g, _n in platform:
        note = "REVOKEd" if rev else "no per-customer column"
        emit(f"  {t:<36} {note}" + (f"  [{', '.join(owners)}]" if owners else ""))
    emit()
    emit(f"SUMMARY: {len(exposed)} table(s) in group A, {len(platform)} in group B")

    (REPO / "staging" / "exposure_check.txt").write_text("\n".join(out), encoding="utf-8")
    return 1 if exposed else 0


if __name__ == "__main__":
    sys.exit(main())
