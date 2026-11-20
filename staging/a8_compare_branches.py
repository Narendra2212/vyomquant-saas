"""Compares the three parallel D5' branches. READ-ONLY.

Phase 11B-A8 discovered three open PRs attempting the same D5' real-Redis verification.
This reports, per branch: its commit, its parent, the files it changes against main,
whether it carries the missing `exchange_connection_slot_guard` production API, and whether
its webhook test carries the `_app_cache_url` keyspace fix.

No checkout, no fetch of working-tree state, no ref is created or moved.
"""

from __future__ import annotations

import json
import subprocess

BRANCHES = [
    "origin/ci/d5-real-redis-quota-evidence",
    "origin/ci/d5-prime-real-redis",
    "origin/ci/d5-real-redis-switch",
]
BASE = "origin/main"

# The API the exchange-slot test needs, absent from main (20 of 21 CI failures).
GUARD_API = ("def exchange_connection_slot_guard", "def _slot_lock_key")
IMPL = "backend_app/core/subscription_dependencies.py"
WEBHOOK = "tests/test_webhook_duplicate_delivery_real_redis.py"
KEYSPACE_FIX = "_app_cache_url"


def git(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "")


def blob(ref: str, path: str) -> str | None:
    code, out = git(["cat-file", "-p", f"{ref}:{path}"])
    return out if code == 0 else None


def main() -> int:
    base = git(["rev-parse", BASE])[1].strip()
    print(f"base {BASE} = {base}\n")

    for ref in BRANCHES:
        code, sha = git(["rev-parse", ref])
        if code != 0:
            print(f"=== {ref}: NOT PRESENT ===\n")
            continue
        sha = sha.strip()
        parents = git(["rev-list", "--parents", "-n", "1", sha])[1].split()[1:]
        subject = git(["log", "-1", "--format=%s", sha])[1].strip()
        changed = [
            l.split("\t", 1)[1]
            for l in git(["diff-tree", "-r", "--name-status", base, sha])[1].splitlines()
            if "\t" in l
        ]
        stat = git(["diff", "--shortstat", base, sha])[1].strip()

        impl = blob(ref, IMPL) or ""
        has_guard = all(marker in impl for marker in GUARD_API)
        wh = blob(ref, WEBHOOK)
        has_fix = (KEYSPACE_FIX in wh) if wh is not None else None

        print(f"=== {ref} ===")
        print(f"  commit      : {sha}")
        print(f"  parent(s)   : {' '.join(p[:8] for p in parents)}"
              f"{'  (== main)' if parents and parents[0] == base else ''}")
        print(f"  subject     : {subject[:90]}")
        print(f"  shortstat   : {stat}")
        print(f"  files ({len(changed)}):")
        for path in changed:
            print(f"      {path}")
        print(f"  carries exchange_connection_slot_guard + _slot_lock_key : {has_guard}")
        print(f"  webhook test has {KEYSPACE_FIX!r} keyspace fix          : {has_fix}")
        print()

    print("=== CI RUNS PER BRANCH ===")
    code, out = git(["rev-parse", "--short", "HEAD"])
    proc = subprocess.run(
        ["gh", "run", "list", "--limit", "40", "--json",
         "databaseId,name,headSha,headBranch,event,status,conclusion,createdAt"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode == 0:
        runs = json.loads(proc.stdout)
        for ref in BRANCHES:
            branch = ref.replace("origin/", "")
            mine = [r for r in runs if r["headBranch"] == branch and r["name"] == "01 PR Check"]
            print(f"  {branch}")
            if not mine:
                print("      (no 01 PR Check run found in the last 40)")
            for r in mine:
                print(f"      run {r['databaseId']}  {r['headSha'][:8]}  "
                      f"{r['event']}  {r['status']}/{r['conclusion']}  {r['createdAt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
