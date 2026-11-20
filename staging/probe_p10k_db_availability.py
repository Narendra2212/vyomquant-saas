"""PHASE 10 / WORKSTREAM K -- read-only check for an authorized test database.

Task 5 permits the over-limit account query ONLY if an authorized database is available.
This decides that, with one connection attempt per endpoint and no retries. It issues no
DDL, no DML, and no write of any kind.

No DSN, credential or customer identifier is printed. Host and port only.
"""

from __future__ import annotations

import os
import pathlib
import re
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("AUTHORIZED TEST DATABASE -- AVAILABILITY (READ-ONLY, ONE ATTEMPT PER ENDPOINT)")

    print("1. Environment configuration")
    env_dsn = None
    for key in ("AERORA_TEST_DATABASE_URL", "DATABASE_URL", "PROVISION_DATABASE_URL",
                "SUPABASE_DB_URL"):
        value = os.environ.get(key)
        if not value:
            print(f"   {key:28s} unset")
        else:
            env_dsn = env_dsn or value
            print(f"   {key:28s} PRESENT (value withheld)")

    print("\n2. staging/pg_dsn.txt (a local bootstrap cluster, per the runbook)")
    endpoints = []
    p = pathlib.Path("staging/pg_dsn.txt")
    if p.is_file():
        raw = p.read_text(encoding="utf-8", errors="replace").strip()
        m = re.search(r"@?([0-9A-Za-z.\-]+):(\d+)/", raw)
        if m:
            host, port = m.group(1), int(m.group(2))
            print(f"   present; host={host} port={port} (credential withheld)")
            endpoints.append((host, port))
        else:
            print("   present but the shape was not parsed; value withheld")
    else:
        print("   absent")

    print("\n3. Reachability -- one attempt each, no retries")
    reachable = []
    for host, port in endpoints + [("127.0.0.1", 5432)]:
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            print(f"   {host}:{port}  OPEN")
            reachable.append((host, port))
        except Exception as exc:
            print(f"   {host}:{port}  closed ({type(exc).__name__})")
        finally:
            s.close()

    banner("VERDICT")
    if not reachable:
        print("   NO authorized database is reachable.")
        print("   -> The over-limit account query is NOT executed and NOT estimated.")
        print("   -> Recorded as NOT OBTAINABLE, requiring a coordinator-provided")
        print("      read-only connection against a non-production replica or an")
        print("      authorized staging cluster.")
    else:
        print("   An endpoint answered. It must still be CONFIRMED non-production and")
        print("   authorized for a read-only aggregate query before anything is run.")
        print("   This probe does NOT connect, authenticate, or query.")


if __name__ == "__main__":
    main()
