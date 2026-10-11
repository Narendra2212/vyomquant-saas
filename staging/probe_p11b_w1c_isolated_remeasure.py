"""Is an ISOLATED PostgreSQL lane available? And is the second cluster running?

PHASE 11B / WORKSTREAM 1B — read-only. Phase 5 evidence for approval request B.

TWO QUESTIONS
-------------
1. COULD an isolated, disposable cluster be created, if it were approved? That needs a
   server factory (`pgserver`, which `staging/bootstrap_pg.py` uses), a client, and
   DISK. Disk is the one that nearly got assumed: `C:` reports very little free space,
   and a fresh cluster plus the 89-table provisioning plus WAL does not fit in nothing.
   An approval request for an option that cannot physically run is worse than no request.

2. IS THE SECOND CLUSTER RUNNING? `staging/` contains two data directories --
   `pgdata` and `pgdata_r1` -- and BOTH carry a `postmaster.pid`. That is the exact
   condition the Workstream H incident arose from: two clusters, random high ports, and
   a shared DSN file that could be pointed at either. A pid file alone does not mean a
   live server (it survives a crash), so this probes the recorded port directly.

STRICTLY READ-ONLY. Reads pid files, opens and closes TCP sockets, issues at most one
read-only `SELECT` per reachable endpoint, and measures free disk. Starts nothing.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import socket
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGING = REPO / "staging"
DSN_FILE = STAGING / "pg_dsn.txt"

MODULES = ("pgserver", "psycopg2", "testing", "pytest_postgresql", "redis", "alembic",
           "sqlalchemy")


def _port_from_pid_file(data_dir: pathlib.Path) -> int | None:
    """`postmaster.pid` line 4 is the port. Reading it is not starting anything."""
    pid_file = data_dir / "postmaster.pid"
    if not pid_file.exists():
        return None
    try:
        lines = pid_file.read_text(encoding="utf-8", errors="replace").splitlines()
        return int(lines[3].strip())
    except (IndexError, ValueError, OSError):
        return None


def _tcp(port: int) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(("127.0.0.1", port))
        return "OPEN"
    except Exception as exc:                                   # noqa: BLE001
        return type(exc).__name__
    finally:
        sock.close()


def _identify(port: int) -> str:
    try:
        import psycopg2
    except ImportError:
        return "(psycopg2 unavailable)"
    dsn = f"postgresql://postgres:@127.0.0.1:{port}/postgres"
    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor()
        cur.execute("SHOW default_transaction_read_only")
        if cur.fetchone()[0] != "on":
            return "(refusing to read: could not enforce read-only)"
        cur.execute(
            "select current_setting('data_directory'), current_setting('server_version'),"
            " (select count(*) from information_schema.tables"
            "  where table_schema='public')")
        datadir, version, ntables = cur.fetchone()
        return f"datadir={datadir} version={version} public_tables={ntables}"
    except Exception as exc:                                   # noqa: BLE001
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        return f"unreachable: {type(exc).__name__}: {first}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                                  # noqa: BLE001
                pass


def main() -> int:
    print("=" * 78)
    print("ISOLATED-CLUSTER OPTION AND SECOND-CLUSTER STATUS")
    print("=" * 78)

    print("\n-- 1. tooling available --------------------------------------------------")
    for name in MODULES:
        spec = importlib.util.find_spec(name)
        print(f"   {name:22} {'present' if spec else 'ABSENT'}")
    try:
        import pgserver
        print(f"   pgserver version       {getattr(pgserver, '__version__', '(unknown)')}")
        root = pathlib.Path(pgserver.__file__).parent
        print(f"   pgserver location      {root}")
        for candidate in ("pginstall", "pg_install", "postgres"):
            path = root / candidate
            if path.exists():
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                print(f"   bundled server         {path.name}/  "
                      f"{size / 1_048_576:.0f} MiB")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   pgserver import failed: {type(exc).__name__}: {exc}")

    print("\n-- 2. DISK, which decides whether option B is physically possible --------")
    usage = shutil.disk_usage(str(REPO))
    print(f"   total          {usage.total / 2**30:>8.1f} GiB")
    print(f"   used           {usage.used / 2**30:>8.1f} GiB")
    print(f"   FREE           {usage.free / 2**30:>8.2f} GiB  "
          f"({usage.free / 2**20:,.0f} MiB)")
    for data_dir in sorted(p for p in STAGING.iterdir()
                           if p.is_dir() and (p / "PG_VERSION").exists()):
        size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
        print(f"   existing cluster {data_dir.name:14} {size / 2**20:>8,.0f} MiB")
    print("   NOTE: a fresh cluster is initdb + the 89-table schema + WAL. The existing")
    print("   clusters above are the only direct measurement of what that costs here.")

    print("\n-- 3. both clusters: pid file, port, and what is really answering --------")
    recorded = DSN_FILE.read_text(encoding="utf-8").strip() if DSN_FILE.exists() else ""
    print(f"   recorded DSN           {recorded}")
    for data_dir in sorted(p for p in STAGING.iterdir()
                           if p.is_dir() and (p / "PG_VERSION").exists()):
        port = _port_from_pid_file(data_dir)
        print(f"\n   cluster {data_dir.name}")
        print(f"     PG_VERSION           "
              f"{(data_dir / 'PG_VERSION').read_text(encoding='utf-8').strip()}")
        print(f"     postmaster.pid       {(data_dir / 'postmaster.pid').exists()}")
        print(f"     recorded port        {port}")
        if port is None:
            print("     -> no port recorded; nothing to probe")
            continue
        state = _tcp(port)
        print(f"     tcp 127.0.0.1:{port}   {state}")
        if state == "OPEN":
            print(f"     identity             {_identify(port)}")
            in_dsn = f":{port}/" in recorded
            print(f"     is the recorded DSN? {in_dsn}")
        else:
            print("     -> pid file present but nothing is listening: STALE pid file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
