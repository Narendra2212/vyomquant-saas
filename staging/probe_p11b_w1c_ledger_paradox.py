"""The snapshot's ledger says 023/024 were applied, yet its schema is the PRE-023 state.

PHASE 11B / WORKSTREAM 1C — read-only.

THE PARADOX
-----------
All 7 disappearances trace to two migrations:

    023_remediate_privilege_escalation.sql     -> the 5 revokes + the policy rewrite
    024_remediate_referral_double_commission.sql -> the function replacement

Both are recorded in `staging/provisioning_snapshot.json`'s own `recorded_migrations`
list, so the database it was captured from had already recorded them. Yet the snapshot's
`privileges`, `policies` and `functions` show the state BEFORE those migrations ran:
`anon` still holding INSERT/UPDATE/DELETE on `library_strategies`, and
`process_referral_commission` still `prosecdef = false`.

A ledger row and the schema disagreeing is not a detail. It decides what the drift means:

  * If 023/024 were applied ONCE, before the capture, the snapshot should already show
    their effects. It does not -- so either the capture came from a different database, or
    the migrations were recorded without taking effect.
  * If they were RE-APPLIED after the capture, the effects would appear later while the
    ledger stayed silent -- `public.schema_migrations` is keyed by filename, so a re-run
    of an already-recorded file adds NO row and updates no timestamp unless the migration
    runner does so deliberately. In that case the ledger CANNOT date the change, and the
    drift tool's "recorded_migrations" dimension is blind to it.

The second case is both benign in outcome and a real defect in the evidence chain: it
means a schema change can happen with no ledger trace at all. This probe determines which
case holds, and whether the ledger can express a re-application.

READ-ONLY: SELECTs only, read-only session enforced and read back.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import pathlib
import re
import sys
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = REPO / "staging" / "pg_dsn.txt"
SNAPSHOT = REPO / "staging" / "provisioning_snapshot.json"
ROOTS = [REPO / "migrations", REPO / "backend_app" / "migrations",
         REPO / "archived_migrations"]
SUSPECTS = ("023_remediate_privilege_escalation.sql",
            "024_remediate_referral_double_commission.sql")


def _connect():
    import psycopg2
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    p = urlparse(dsn)
    host = (p.hostname or "").lower()
    if p.port == 6543:
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


def _find(name: str):
    for root in ROOTS:
        for p in root.rglob(name) if root.exists() else []:
            return p
    return None


def main() -> int:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snap_mtime = dt.datetime.fromtimestamp(SNAPSHOT.stat().st_mtime)
    conn = _connect()
    cur = conn.cursor()

    print("=" * 78)
    print("LEDGER vs SCHEMA: when did 023 and 024 actually take effect?")
    print("=" * 78)
    print(f"   snapshot file mtime: {snap_mtime}")

    print("\n-- 1. what the live ledger says about the two suspects --------------------")
    cur.execute("""
        select filename, applied_at, step, sha256
        from public.schema_migrations
        where filename like %% or filename like %%
        order by applied_at
    """.replace("%%", "%s"), (f"%{SUSPECTS[0]}", f"%{SUSPECTS[1]}"))
    rows = cur.fetchall()
    for fn, applied, step, sha in rows:
        local = applied.astimezone().replace(tzinfo=None)
        rel = "BEFORE" if local < snap_mtime else "AFTER"
        print(f"   {fn}")
        print(f"     applied_at {applied}  (local {local}) -> {rel} the snapshot")
        print(f"     step {step}   sha256 {sha[:24]}...")

    print("\n-- 2. are they in the snapshot's own ledger? -----------------------------")
    snap_names = {m[0] if isinstance(m, (list, tuple)) else m
                  for m in snap.get("recorded_migrations", [])}
    for s in SUSPECTS:
        present = any(s in n for n in snap_names)
        print(f"   {s:46} in snapshot ledger = {present}")

    print("\n-- 3. can the ledger even express a RE-application? ----------------------")
    cur.execute("""
        select count(*), count(distinct filename) from public.schema_migrations
    """)
    n, nd = cur.fetchone()
    print(f"   rows={n}  distinct filenames={nd}  "
          f"=> {'one row per file' if n == nd else 'duplicates exist'}")
    cur.execute("""
        select con.conname, pg_get_constraintdef(con.oid)
        from pg_constraint con join pg_class c on c.oid = con.conrelid
        join pg_namespace ns on ns.oid = c.relnamespace
        where ns.nspname='public' and c.relname='schema_migrations'
        order by 1
    """)
    cons = cur.fetchall()
    print(f"   constraints on schema_migrations: {len(cons)}")
    for name, defn in cons:
        print(f"     {name}: {defn}")
    print("   => a UNIQUE/PK on filename means a re-run CANNOT add a row, so the")
    print("      ledger records FIRST application only and cannot date a re-run.")

    print("\n-- 4. does the migration runner upsert applied_at on re-run? -------------")
    prov = REPO / "scripts" / "provision_database.py"
    if prov.exists():
        text = prov.read_text(encoding="utf-8", errors="replace")
        for pat, label in (
            (r"on\s+conflict[^;]{0,160}", "ON CONFLICT clause"),
            (r"insert\s+into\s+(?:public\.)?schema_migrations[^;]{0,200}",
             "ledger INSERT"),
            (r"already\s+applied|skip", "skip-if-applied logic"),
        ):
            for m in re.finditer(pat, text, re.IGNORECASE | re.DOTALL):
                line = text[:m.start()].count("\n") + 1
                print(f"   {label} @ provision_database.py:{line}")
                print(f"     {' '.join(m.group(0).split())[:150]}")

    print("\n-- 5. the policy the drift tool called MISSING ---------------------------")
    cur.execute("""
        select policyname, permissive, roles::text, cmd, qual, with_check
        from pg_policies
        where schemaname='public' and tablename='library_strategies'
          and policyname='Users can update their own strategies'
    """)
    live_pol = cur.fetchone()
    print("   LIVE:")
    if live_pol:
        for key, val in zip(("policyname", "permissive", "roles", "cmd", "qual",
                             "with_check"), live_pol):
            print(f"     {key:12} {str(val)[:96]}")
    snap_pol = [p for p in snap.get("policies", [])
                if len(p) > 1 and p[0] == "library_strategies"
                and "update their own" in str(p[1])]
    print("   SNAPSHOT:")
    for p in snap_pol:
        for i, val in enumerate(p):
            print(f"     [{i}]          {str(val)[:96]}")
    if live_pol and snap_pol:
        print("\n   => The policy EXISTS live. The drift tool compares whole tuples, so")
        print("      a changed predicate reads as one row missing plus one row extra,")
        print("      not as a modification. Both halves are the SAME policy.")

    print("\n-- 6. what 023 does to that policy ---------------------------------------")
    path = _find(SUSPECTS[0])
    if path:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
                r"(drop\s+policy|create\s+policy)[^;]*library_strategies[^;]*;",
                text, re.IGNORECASE | re.DOTALL):
            line = text[:m.start()].count("\n") + 1
            print(f"   {path.name}:{line}")
            for seg in " ".join(m.group(0).split()).split(" WITH CHECK "):
                print(f"     {seg[:150]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
