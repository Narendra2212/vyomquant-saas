"""Read-only preflight of the W1 PostgreSQL target, re-measured from scratch.

PHASE 11B / WORKSTREAM 1B — Phase 2 deliverable evidence.

WHAT THIS IS FOR
----------------
W1A reported this cluster's state. The W1B brief says not to rely on that without
checking it again, so every property below is measured here rather than quoted. It also
answers the question `scripts/verify_schema_drift.py` raised but cannot answer itself:
its verdict was `DRIFTED`, and its own closing text says the two possible causes --
"a change was made outside the migration set" versus "the baseline is stale" -- have
OPPOSITE fixes. Deciding between them needs the snapshot's provenance and the live
migration ledger side by side, which is what this prints.

READ-ONLY, ENFORCED BY THE SERVER, NOT BY INTENTION
---------------------------------------------------
Every connection sets `default_transaction_read_only` and then READS IT BACK and aborts
unless the server reports `on`. That matters specifically on psycopg2: calling
`set_session(readonly=True)` while `autocommit` is False leaves the setting `off`,
because the parameter is applied to the NEXT transaction and one is already open. So
`autocommit=True` is set first and the readback is the proof, not the call.

The probe also refuses any DSN whose host is not loopback, and refuses port 6543
(Supabase's transaction-mode pooler, where session-level settings do not survive).

It issues SELECTs only. It starts no server, writes no file, and does not touch
`staging/pg_dsn.txt`, the Alembic state or any row.
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
#: Searched RECURSIVELY. An earlier revision of this probe globbed `*.sql` at the top
#: level of two directories only and reported 12 recorded migrations as having no source
#: file. All 12 exist -- under `migrations/hardening/` and
#: `archived_migrations/root_migrations/`. The glob was the defect, not the repository,
#: and a non-recursive search here manufactures a false P0.
MIGRATION_DIRS = [
    REPO / "migrations",
    REPO / "backend_app" / "migrations",
    REPO / "archived_migrations",
]

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", ""}


def _refuse_unless_local(dsn: str) -> str:
    parsed = urlparse(dsn)
    host = (parsed.hostname or "").strip()
    port = parsed.port
    if port == 6543:
        raise SystemExit("REFUSED: port 6543 is Supabase's transaction-mode pooler.")
    if host.lower() not in LOOPBACK_NAMES:
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise SystemExit(
                f"REFUSED: {host!r} is not loopback. W1B does not touch remote clusters."
            )
    return f"{host}:{port}/{(parsed.path or '/').lstrip('/')}"


def _connect(dsn: str):
    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    setting = cur.fetchone()[0]
    if setting != "on":
        conn.close()
        raise SystemExit(
            f"ABORT: server reports default_transaction_read_only={setting!r}, "
            "expected 'on'. Refusing to read under a session that could write."
        )
    cur.execute("SHOW transaction_read_only")
    txn = cur.fetchone()[0]
    print(f"  read-only enforced   : default_transaction_read_only={setting!r}, "
          f"transaction_read_only={txn!r}")
    return conn


def _one(cur, sql, args=None):
    cur.execute(sql, args or ())
    row = cur.fetchone()
    return row[0] if row else None


def main() -> int:
    if not DSN_FILE.exists():
        print("staging/pg_dsn.txt is absent; nothing to preflight.")
        return 1
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    print("=" * 78)
    print("W1B READ-ONLY POSTGRESQL PREFLIGHT")
    print("=" * 78)
    print(f"  dsn file             : {DSN_FILE.relative_to(REPO)}")
    print(f"  endpoint             : {_refuse_unless_local(dsn)}")

    conn = _connect(dsn)
    cur = conn.cursor()

    print("\n-- identity and locality -------------------------------------------------")
    for label, sql in [
        ("server_version", "select current_setting('server_version')"),
        ("version()", "select version()"),
        ("data_directory", "select current_setting('data_directory')"),
        ("current_database", "select current_database()"),
        ("current_user", "select current_user"),
        ("session_user", "select session_user"),
        ("is_superuser", "select current_setting('is_superuser')"),
        ("inet_server_addr", "select coalesce(host(inet_server_addr()),'(local socket)')"),
        ("inet_server_port", "select inet_server_port()"),
        ("postmaster_start", "select pg_postmaster_start_time()"),
        ("in_recovery", "select pg_is_in_recovery()"),
    ]:
        print(f"  {label:21}: {_one(cur, sql)}")

    print("\n-- role vs table ownership -----------------------------------------------")
    owner_rows = _one(cur, """
        select count(*) from pg_tables
        where schemaname='public' and tableowner = current_user
    """)
    total = _one(cur, "select count(*) from pg_tables where schemaname='public'")
    print(f"  public tables                 : {total}")
    print(f"  owned by current_user         : {owner_rows}")
    print(f"  current_user bypasses RLS?    : "
          f"{_one(cur, 'select rolbypassrls from pg_roles where rolname=current_user')}")
    cur.execute("""
        select tableowner, count(*) from pg_tables where schemaname='public'
        group by tableowner order by 2 desc
    """)
    for owner, n in cur.fetchall():
        print(f"    owner {owner!r}: {n} tables")

    print("\n-- row level security ----------------------------------------------------")
    cur.execute("""
        select
          count(*) filter (where c.relrowsecurity)      as enabled,
          count(*) filter (where c.relforcerowsecurity) as forced,
          count(*)                                       as tables
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relkind='r'
    """)
    enabled, forced, ntab = cur.fetchone()
    print(f"  tables                        : {ntab}")
    print(f"  RLS ENABLED                   : {enabled}")
    print(f"  RLS FORCED                    : {forced}")
    print(f"  policies                      : "
          f"{_one(cur, chr(115)+'elect count(*) from pg_policies where schemaname=' + chr(39) + 'public' + chr(39))}")
    if forced == 0:
        print("  NOTE: FORCED is 0. A table owner is EXEMPT from its own RLS policies "
              "unless FORCE ROW LEVEL SECURITY is set. Any tenant-isolation result "
              "obtained as the owner is therefore NOT evidence that RLS blocks "
              "owner-bypass access.")

    print("\n-- migration state -------------------------------------------------------")
    print(f"  alembic_version               : "
          f"{_one(cur, 'select string_agg(version_num,chr(44)) from alembic_version')}")
    nmig = _one(cur, "select count(*) from public.schema_migrations")
    print(f"  schema_migrations rows        : {nmig}")

    # The ledger's column names are not assumed: a wrong guess here aborted an earlier
    # run of this probe, and guessing is exactly what the brief forbids.
    cur.execute("""
        select column_name, data_type from information_schema.columns
        where table_schema='public' and table_name='schema_migrations'
        order by ordinal_position
    """)
    cols = cur.fetchall()
    print(f"  schema_migrations columns     : "
          f"{', '.join(f'{c}:{t}' for c, t in cols)}")
    colnames = [c for c, _ in cols]

    group_col = next((c for c in ("phase", "step", "stage", "migration_phase")
                      if c in colnames), None)
    if group_col:
        cur.execute(f"""
            select {group_col}, count(*) from public.schema_migrations
            group by {group_col} order by {group_col}
        """)
        for label, n in cur.fetchall():
            print(f"    {group_col} {label!r}: {n}")
    else:
        print("    (no phase/step grouping column present)")

    name_col = next((c for c in ("filename", "migration", "name", "script")
                     if c in colnames), colnames[0])
    print(f"  migration-name column         : {name_col!r}")
    cur.execute(f"select {name_col} from public.schema_migrations order by 1")
    recorded = [r[0] for r in cur.fetchall()]

    print("\n-- recorded migrations vs files on disk -----------------------------------")
    print(f"  search roots: {[str(p.relative_to(REPO)) for p in MIGRATION_DIRS]}")
    on_disk = {}
    for root in MIGRATION_DIRS:
        if root.exists():
            for f in root.rglob("*.sql"):
                on_disk.setdefault(f.name, []).append(
                    str(f.relative_to(REPO)).replace("\\", "/"))
    missing_source = []
    for rec in recorded:
        base = rec.rsplit("/", 1)[-1]
        if base not in on_disk:
            missing_source.append(rec)
    print(f"  recorded rows                 : {len(recorded)}")
    print(f"  recorded WITH a .sql on disk  : {len(recorded) - len(missing_source)}")
    print(f"  recorded with NO source file  : {len(missing_source)}")
    for rec in missing_source:
        print(f"      !! {rec}")
    mismatched_dir = [r for r in recorded
                      if r.rsplit("/", 1)[-1] in on_disk
                      and r not in on_disk[r.rsplit("/", 1)[-1]]]
    print(f"  recorded path != actual path  : {len(mismatched_dir)}")
    for rec in mismatched_dir[:12]:
        print(f"      recorded {rec!r} -> actually at {on_disk[rec.rsplit('/',1)[-1]]}")
    if len(mismatched_dir) > 12:
        print(f"      ... and {len(mismatched_dir) - 12} more")

    print("\n-- row counts in the tables the lane would write --------------------------")
    for table in ("profiles", "exchange_keys", "orders", "positions",
                  "execution_records", "fee_records", "referral_payouts",
                  "safety_control_audit", "referral_obligations",
                  "bot_ownership_claims", "schema_migrations"):
        n = _one(cur, "select to_regclass(%s)", (f"public.{table}",))
        if n is None:
            print(f"  {table:24}: (absent)")
            continue
        cur.execute(f"select count(*) from public.{table}")
        print(f"  {table:24}: {cur.fetchone()[0]}")

    conn.close()

    print("\n-- baseline snapshot provenance ------------------------------------------")
    if not SNAPSHOT.exists():
        print("  staging/provisioning_snapshot.json absent")
        return 0
    stat = SNAPSHOT.stat()
    print(f"  file                          : {SNAPSHOT.relative_to(REPO)}")
    print(f"  size / mtime                  : {stat.st_size} bytes / "
          f"{__import__('datetime').datetime.fromtimestamp(stat.st_mtime)}")
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for key in sorted(data):
        value = data[key]
        if isinstance(value, list):
            print(f"  {key:30}: list[{len(value)}]")
        elif isinstance(value, dict):
            print(f"  {key:30}: dict({len(value)} keys) {sorted(value)[:8]}")
        else:
            print(f"  {key:30}: {value!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
