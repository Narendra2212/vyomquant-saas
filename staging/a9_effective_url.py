"""Phase 11B-A9. READ-ONLY. Does the gate's database actually match the application's?

The brief says: "Do not assume that changing the URL alone establishes keyspace
equivalence. Verify the effective URL and the application's actual Redis client
configuration."

A14 changed the workflow's AERORA_TEST_REDIS_URL from /15 to /0. This checks whether that
actually makes the gate's endpoint and the application's cache resolve to the SAME database,
by calling the application's own URL builder and asking redis-py to parse both -- offline,
no connection attempted.
"""

from __future__ import annotations

import os
import sys

# Run from staging/ but import the application package from the repo root.
sys.path.insert(0, r"c:\aerora_quant_backend_updated_final1")

# Match the CI environment for the duration of this probe only. These are process-local;
# nothing is written to disk and no server is contacted.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

CI_GATE_BEFORE = "redis://localhost:6379/15"   # all three PR heads
CI_GATE_AFTER = "redis://localhost:6379/0"     # A14's worktree change


def main() -> int:
    from redis.asyncio import ConnectionPool

    from backend_app.backend.redis_manager import (
        REDIS_DB_CACHE,
        REDIS_DB_EVENTS,
        REDIS_DB_QUEUE,
        get_redis_url,
    )

    print("=== application's database partitioning ===")
    print(f"  REDIS_DB_CACHE  = {REDIS_DB_CACHE}")
    print(f"  REDIS_DB_QUEUE  = {REDIS_DB_QUEUE}")
    print(f"  REDIS_DB_EVENTS = {REDIS_DB_EVENTS}")
    print()

    app_url = get_redis_url(REDIS_DB_CACHE)
    print("=== effective URLs ===")
    print(f"  application cache : get_redis_url({REDIS_DB_CACHE}) -> {app_url}")
    print(f"  gate BEFORE (PRs) : {CI_GATE_BEFORE}")
    print(f"  gate AFTER  (A14) : {CI_GATE_AFTER}")
    print()

    def db_of(url: str) -> object:
        try:
            return ConnectionPool.from_url(url).connection_kwargs.get("db")
        except Exception as exc:  # noqa: BLE001
            return f"<parse error: {type(exc).__name__}>"

    app_db = db_of(app_url)
    before_db = db_of(CI_GATE_BEFORE)
    after_db = db_of(CI_GATE_AFTER)

    print("=== resolved database per URL (redis-py parse, no connection) ===")
    print(f"  application cache : db={app_db}")
    print(f"  gate BEFORE       : db={before_db}")
    print(f"  gate AFTER        : db={after_db}")
    print()

    print("=== the override, demonstrated ===")
    for u in (
        "redis://localhost:6379/15",
        "redis://localhost:6379/15?db=0",
        "redis://localhost:6379?db=0",
        "redis://localhost:6379",
        "redis://localhost:6379/0",
    ):
        print(f"  {u:<34} -> db={db_of(u)}")
    print()

    print("=== VERDICT ===")
    print(f"  gate BEFORE matched the app's database : {before_db == app_db}")
    print(f"  gate AFTER  matches the app's database : {after_db == app_db}")
    if after_db == app_db and before_db != app_db:
        print("  => A14's /15 -> /0 change is CORRECT and necessary: it makes the endpoint")
        print("     the gate PINGs the same database the application's cache uses.")
    elif after_db == app_db and before_db == app_db:
        print("  => both match; the change is cosmetic in this environment.")
    else:
        print("  => the change does NOT establish equivalence. Investigate before relying on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
