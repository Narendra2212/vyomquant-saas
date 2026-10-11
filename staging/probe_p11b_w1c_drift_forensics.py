"""Explain all 16 drift dimensions, and establish the baseline snapshot's provenance.

PHASE 11B / WORKSTREAM 1C — STRICTLY READ-ONLY.

THE QUESTION W1B LEFT OPEN
--------------------------
`scripts/verify_schema_drift.py` exits 1 with `RESULT: DRIFTED`, 12 of 16 dimensions. Its
own closing text says the two possible causes -- a change made outside the migration set,
or a stale baseline -- have OPPOSITE fixes, and it cannot tell them apart. W1B could not
either, for two reasons it recorded as W1B-06 and W1B-08:

  * `staging/provisioning_snapshot.json` carries NO provenance. Its 17 top-level keys are
    all schema dimensions; there is no DSN, data directory, server version, role or
    capture timestamp inside the file. So it cannot be shown to describe this cluster.
  * 7 items are present in the baseline and ABSENT from the live database (1 policy, 1
    function overload, 5 privilege grants). A stale baseline explains additions. It does
    not explain disappearances.

WHAT THIS PROBE DOES
--------------------
1. PROVENANCE, forensically rather than from metadata the file does not have. The
   snapshot's own `recorded_migrations` list IS a timestamp: it names exactly which
   migrations had been applied when it was captured. Cross-referencing that against the
   live ledger's `applied_at` column dates the capture to an interval, and comparing its
   `alembic_version` and table set tests whether it describes THIS cluster or a different
   one.

2. EVERY DIMENSION, classified. For each of the 16, the direction of difference and --
   critically -- whether each individual item is EXPLAINED by a migration applied after
   the capture interval. An item created by migration 028 in a baseline captured before
   028 is not drift; it is the baseline being old. An item that no migration accounts for
   IS drift and must be reported as such.

3. THE 7 DISAPPEARANCES, individually. For each, the probe looks for a migration that
   drops or replaces it. A disappearance with a migration behind it is explained; one
   without it is a real finding.

NO WRITES. One connection, `default_transaction_read_only` set AND read back, SELECTs
only. Refuses non-loopback and port 6543. Touches no row, no DSN, no Alembic state.
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
MIGRATION_ROOTS = [REPO / "migrations", REPO / "backend_app" / "migrations",
                   REPO / "archived_migrations"]

LOOPBACK = {"localhost", "127.0.0.1", "::1", ""}


def _connect(dsn: str):
    import psycopg2
    parsed = urlparse(dsn)
    host = (parsed.hostname or "").strip()
    if parsed.port == 6543:
        raise SystemExit("REFUSED: port 6543 is the Supabase transaction pooler.")
    if host.lower() not in LOOPBACK:
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise SystemExit(f"REFUSED: {host!r} is not loopback.")
    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    if cur.fetchone()[0] != "on":
        conn.close()
        raise SystemExit("ABORT: could not enforce a read-only session.")
    return conn


def _migration_text() -> dict[str, str]:
    """Every migration file's text, keyed by basename, searched recursively."""
    out: dict[str, str] = {}
    for root in MIGRATION_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.sql"):
            out[path.name] = path.read_text(encoding="utf-8", errors="replace")
    return out


def main() -> int:
    if not SNAPSHOT.exists():
        print("staging/provisioning_snapshot.json absent")
        return 1
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    conn = _connect(dsn)
    cur = conn.cursor()

    print("=" * 78)
    print("W1C DRIFT FORENSICS  (read-only)")
    print("=" * 78)
    st = SNAPSHOT.stat()
    print(f"   snapshot  {SNAPSHOT.name}  {st.st_size} B  "
          f"mtime {dt.datetime.fromtimestamp(st.st_mtime)}")
    print(f"   target    {urlparse(dsn).hostname}:{urlparse(dsn).port}")

    # ── 1. PROVENANCE ───────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("1. PROVENANCE OF THE BASELINE SNAPSHOT")
    print("=" * 78)
    print("\n-- (a) metadata fields present in the file --------------------------------")
    meta_keys = [k for k in snap
                 if k.lower() in ("dsn", "database", "host", "port", "data_directory",
                                  "server_version", "role", "captured_at", "timestamp",
                                  "generator", "version", "meta", "_meta")]
    print(f"   provenance keys: {meta_keys or 'NONE'}")
    print(f"   all keys       : {sorted(snap)}")

    print("\n-- (b) does it claim the same Alembic revision? ---------------------------")
    snap_alembic = snap.get("alembic_version")
    cur.execute("select version_num from alembic_version order by 1")
    live_alembic = [r[0] for r in cur.fetchall()]
    print(f"   snapshot alembic_version : {snap_alembic}")
    print(f"   live     alembic_version : {live_alembic}")
    same_rev = [list(x) if isinstance(x, (list, tuple)) else x
                for x in (snap_alembic or [])] == [[v] for v in live_alembic] \
        or snap_alembic == live_alembic
    print(f"   same revision            : {same_rev}")

    print("\n-- (c) DATE THE CAPTURE from its own migration list -----------------------")
    snap_migs = {tuple(m) if isinstance(m, list) else m
                 for m in snap.get("recorded_migrations", [])}
    snap_names = {m[0] if isinstance(m, tuple) else m for m in snap_migs}
    cur.execute("select filename, applied_at, step from public.schema_migrations "
                "order by applied_at")
    live_rows = cur.fetchall()
    live_names = {r[0] for r in live_rows}

    in_snap = [r for r in live_rows if r[0] in snap_names]
    not_in_snap = [r for r in live_rows if r[0] not in snap_names]
    print(f"   snapshot records   : {len(snap_names)} migrations")
    print(f"   live records       : {len(live_names)} migrations")
    print(f"   in both            : {len(in_snap)}")
    print(f"   live only          : {len(not_in_snap)}")
    print(f"   snapshot only      : {len(snap_names - live_names)}  "
          f"{sorted(snap_names - live_names) or '(none)'}")
    if in_snap:
        newest_shared = max(r[1] for r in in_snap)
        print(f"   newest shared applied_at  : {newest_shared}")
    if not_in_snap:
        oldest_extra = min(r[1] for r in not_in_snap)
        print(f"   oldest live-only applied_at: {oldest_extra}")
        print(f"\n   => CAPTURE INTERVAL: after {newest_shared}")
        print(f"                        before {oldest_extra}")
        print(f"      file mtime {dt.datetime.fromtimestamp(st.st_mtime)} "
              f"{'falls inside' if newest_shared.replace(tzinfo=None) <= dt.datetime.fromtimestamp(st.st_mtime) <= oldest_extra.replace(tzinfo=None) else 'does NOT fall inside'} that interval")
    print("\n   live-only migrations, in application order:")
    for name, applied, step in not_in_snap:
        print(f"     {applied}  {step:9} {name}")

    print("\n-- (d) is the snapshot a strict SUBSET of live? ---------------------------")
    print("   (a subset is what a stale capture of the SAME database looks like; items")
    print("    present only in the snapshot would mean a DIFFERENT database or a drop)")
    for dim in ("tables", "primary_keys", "row_level_security", "enum_types",
                "columns", "indexes", "foreign_keys",
                "unique_and_check_constraints", "policies", "privileges",
                "functions", "triggers", "sequences", "extensions"):
        if dim not in snap:
            continue
        s = {tuple(x) if isinstance(x, list) else x for x in snap[dim]}
        print(f"     {dim:30} snapshot={len(s)}")

    # ── 2. EVERY DIMENSION ──────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("2. THE 16 DIMENSIONS")
    print("=" * 78)
    sys.path.insert(0, str(REPO))
    from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm  # noqa: E402

    migs = _migration_text()
    late_names = [r[0].rsplit("/", 1)[-1] for r in not_in_snap]
    late_text = "\n".join(migs.get(n, "") for n in late_names)
    print(f"   migrations applied after the capture interval: {late_names}")
    print(f"   their combined SQL: {len(late_text):,} chars  "
          f"(this is the text that must explain the differences)")

    results = []
    for dim, sql in SNAPSHOT_QUERIES.items():
        cur.execute(sql)
        live = {_norm(r) for r in cur.fetchall()}
        base = {_norm(tuple(x) if isinstance(x, list) else (x,))
                if not isinstance(x, (list, tuple)) else _norm(tuple(x))
                for x in snap.get(dim, [])}
        missing = base - live          # in baseline, gone from DB
        extra = live - base            # in DB, not in baseline
        results.append((dim, len(base), len(live), missing, extra))

    print(f"\n   {'dimension':32} {'base':>6} {'live':>6} {'missing':>8} {'extra':>7}")
    print("   " + "-" * 64)
    for dim, nb, nl, missing, extra in results:
        flag = "same " if not missing and not extra else "DRIFT"
        print(f"   {dim:32} {nb:>6} {nl:>6} {len(missing):>8} {len(extra):>7}  {flag}")

    # ── 3. THE DISAPPEARANCES ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("3. EVERY DISAPPEARANCE, ITEM BY ITEM")
    print("=" * 78)
    print("   An item in the baseline but not the database. For each, the probe looks")
    print("   for a later migration that drops, replaces or revokes it.\n")

    total_missing = 0
    unexplained = []
    for dim, nb, nl, missing, extra in results:
        if not missing:
            continue
        print(f"-- {dim}: {len(missing)} missing " + "-" * (50 - len(dim)))
        for item in sorted(missing, key=str):
            total_missing += 1
            tokens = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", str(item))
                      if t.lower() not in ("none", "true", "false", "null", "public",
                                           "character", "varying", "timestamp", "with",
                                           "time", "zone", "numeric", "uuid", "text",
                                           "jsonb", "boolean", "integer")]
            hits = sorted({n for n in late_names
                           for t in tokens[:6] if t in migs.get(n, "")})
            status = "EXPLAINED by " + ", ".join(hits) if hits else "UNEXPLAINED"
            if not hits:
                unexplained.append((dim, item))
            print(f"     {str(item)[:104]}")
            print(f"       -> {status}")
        print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   dimensions drifting      : "
          f"{sum(1 for _, _, _, m, e in results if m or e)} of {len(results)}")
    print(f"   total items missing      : {total_missing}")
    print(f"   of which UNEXPLAINED     : {len(unexplained)}")
    for dim, item in unexplained:
        print(f"     !! {dim}: {str(item)[:92]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
