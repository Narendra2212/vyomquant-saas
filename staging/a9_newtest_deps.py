"""Phase 11B-A9. READ-ONLY. Do the NEW production-path tests' dependencies exist in CI?

This is the A8 category-1 trap, re-applied to Phase 3. The exchange-slot suite failed 21
times in CI because it was committed without the production API it calls. The two new
production-path quota tests call into:

    backend_app.core.cache.redis_manager._bypass_dev_mode(True)
    backend_app.core.cache.redis_manager._get_active_cache()
    backend_app.core.cache.redis_manager.MockRedisClient
    backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota / get_quota_usage / _meter_key
    backend_app.backend.redis_manager.get_redis_url / REDIS_DB_CACHE
    tests.real_redis_gate.require_real_redis / real_redis_required / ALLOW_REMOTE_VAR / _LOOPBACK_HOSTS
    tests/conftest.py  _REDIS_SINGLETON_SEAMS  (restores the bypass after each test)

`backend_app/core/cache/redis_manager.py` is +185/-5 in the WORKTREE. If the seams the new
tests use live only in that uncommitted diff, the tests will fail in CI exactly the way the
slot-lock suite did -- and the D5' repair would ship the same defect it is meant to fix.

Checked at HEAD and on each PR head, by symbol, read-only.
"""

from __future__ import annotations

import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

REVS = [
    ("HEAD/main", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR#6", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
    ("PR#7", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#8", "a624ce89b16005b0c73197005d6d0672737dad65"),
]

# (symbol, file it must be defined in, how it is defined)
DEPS = [
    ("_bypass_dev_mode", "backend_app/core/cache/redis_manager.py", "def _bypass_dev_mode"),
    ("_get_active_cache", "backend_app/core/cache/redis_manager.py", "def _get_active_cache"),
    ("MockRedisClient", "backend_app/core/cache/redis_manager.py", "class MockRedisClient"),
    ("reserve_quota", "backend_app/core/subscription_engine.py", "def reserve_quota"),
    ("get_quota_usage", "backend_app/core/subscription_engine.py", "def get_quota_usage"),
    ("_meter_key", "backend_app/core/subscription_engine.py", "def _meter_key"),
    ("get_redis_url", "backend_app/backend/redis_manager.py", "def get_redis_url"),
    ("REDIS_DB_CACHE", "backend_app/backend/redis_manager.py", "REDIS_DB_CACHE ="),
    ("_REDIS_SINGLETON_SEAMS", "tests/conftest.py", "_REDIS_SINGLETON_SEAMS"),
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def blob(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def main() -> int:
    print("=== cache-manager drift: worktree vs committed ===")
    for p in (
        "backend_app/core/cache/redis_manager.py",
        "backend_app/core/subscription_engine.py",
        "backend_app/backend/redis_manager.py",
        "tests/conftest.py",
    ):
        r = run(["git", "status", "--porcelain", "--", p])
        n = run(["git", "diff", "HEAD", "--numstat", "--", p])
        print(f"  {p:<48} status={r.stdout.strip()[:3]!r:<6} numstat={n.stdout.strip() or '(clean)'}")
    print()

    print("=== dependency presence by revision (definition must exist) ===")
    header = f"  {'symbol':<24} {'file':<46} " + "".join(f"{lbl:<12}" for lbl, _ in REVS)
    print(header)
    print("  " + "-" * (len(header) - 2))
    missing: list[tuple[str, str]] = []
    for sym, path, needle in DEPS:
        cells = []
        for lbl, rev in REVS:
            b = blob(rev, path)
            if b is None:
                cells.append("NOFILE")
                missing.append((lbl, f"{sym} (file absent)"))
            elif needle in b:
                cells.append("ok")
            else:
                cells.append("MISSING")
                missing.append((lbl, sym))
        print(f"  {sym:<24} {path:<46} " + "".join(f"{c:<12}" for c in cells))
    print()

    print("=== worktree (on disk) for the same symbols ===")
    for sym, path, needle in DEPS:
        disk = open(f"{REPO}\\" + path.replace("/", "\\"), encoding="utf-8", errors="replace").read()
        print(f"  {sym:<24} {'ok' if needle in disk else 'MISSING'}")
    print()

    print("=== VERDICT ===")
    if missing:
        print(f"  {len(missing)} dependency gap(s) -- the new tests would fail in CI:")
        for lbl, sym in missing:
            print(f"    {lbl}: {sym}")
    else:
        print("  All dependencies of the two new production-path tests are present at HEAD")
        print("  and on all three PR heads. They do NOT repeat the slot-lock defect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
