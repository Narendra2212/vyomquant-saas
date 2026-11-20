"""Phase 11B-A8. READ-ONLY.

Webhook-cluster evidence for the report:
  1. the two failure entries with their assertion text, from the decoded CI log
  2. whether the worktree fix helper `_app_cache_url` exists on any branch
  3. the keyspace-split lines in backend_app/backend/redis_manager.py
"""

from __future__ import annotations

import re
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
LOG = "staging/a8_raw_job.log"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

REVS = [
    ("HEAD/main", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR#8 a624ce89", "a624ce89b16005b0c73197005d6d0672737dad65"),
    ("PR#7 49e70da2", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#6 d07014d3", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def load() -> list[str]:
    raw = open(f"{REPO}\\{LOG}", "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="replace")
    return [ANSI.sub("", TS.sub("", l)).rstrip() for l in text.splitlines()]


def main() -> int:
    lines = load()
    start = next(i for i, l in enumerate(lines) if "short test summary" in l)
    print("=== WEBHOOK FAILURE ENTRIES (from short test summary info) ===")
    for l in lines[start + 1:]:
        s = l.strip()
        if s.startswith("=") and "short" not in s:
            break
        if "test_webhook_duplicate_delivery_real_redis" in s:
            print(f"  {s[:300]}")
    print()

    print("=== _app_cache_url PRESENCE ===")
    for label, rev in REVS:
        r = run(["git", "grep", "-c", "_app_cache_url", rev, "--", "*.py"])
        print(f"  {label:<14} exit={r.returncode} {'FOUND' if r.returncode == 0 else 'ABSENT'} {r.stdout.strip()[:100]}")
    r = run(["git", "grep", "-n", "_app_cache_url", "--", "*.py"])
    hits = [l for l in r.stdout.splitlines() if l.strip()]
    print(f"  worktree       exit={r.returncode} hits={len(hits)}")
    for h in hits[:8]:
        print(f"      {h[:150]}")
    print()

    print("=== KEYSPACE SPLIT: backend_app/backend/redis_manager.py (worktree) ===")
    path = f"{REPO}\\backend_app\\backend\\redis_manager.py"
    want = re.compile(r"REDIS_DB_CACHE\s*=|^REDIS_URL\s*=|def get_redis_url|query_params\['db'\]|from_url\(get_redis_url")
    for i, l in enumerate(open(path, encoding="utf-8", errors="replace").read().splitlines(), 1):
        if want.search(l.strip()) or want.search(l):
            print(f"  {i:>5}: {l.strip()[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
