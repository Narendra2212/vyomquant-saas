"""Phase 11B-A8. READ-ONLY.

The A12 report (staging/PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md) is the parallel
workstream's own account. It overlaps my A8 diagnostics and contradicts two of my numbers.
Rather than copy its claims, verify each one that changes my report:

  C1  exchange.py staged diffstat          A12: +268/-15   (my report said "253-line")
  C2  subscription_dependencies.py unstaged A12: +253/-0
  C3  exchange-slot test: worktree vs branch content identical modulo CRLF?
      (my drift table shows 41804 vs 40937 bytes, implying a real difference)
  C4  AERORA_TEST_REDIS_URL value in the worktree workflow vs each branch
      (A12 claims it changed /15 -> /0 locally, uncommitted)
  C5  untracked count                      A12: 3207
  C6  does the d5-redis-diagnostics job exist on PR #6 and still carry /15?
"""

from __future__ import annotations

import re
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

REVS = [
    ("main", "074b772ab16cece62cbfca0f2b78497be2ae56d5"),
    ("PR#8", "a624ce89b16005b0c73197005d6d0672737dad65"),
    ("PR#7", "49e70da23c2858ffc9a6f34d5db4521401477c82"),
    ("PR#6", "d07014d36d32c63db44f8fb9659933c0e84e82f2"),
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def show(spec: str) -> str | None:
    r = run(["git", "show", spec])
    return r.stdout if r.returncode == 0 else None


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def main() -> int:
    print("=== C1/C2  diffstats ===")
    for label, args in [
        ("exchange.py  STAGED (index vs HEAD)", ["git", "diff", "--cached", "--numstat", "--", "backend_app/routers/exchange.py"]),
        ("exchange.py  UNSTAGED (worktree vs index)", ["git", "diff", "--numstat", "--", "backend_app/routers/exchange.py"]),
        ("subscription_dependencies.py STAGED", ["git", "diff", "--cached", "--numstat", "--", "backend_app/core/subscription_dependencies.py"]),
        ("subscription_dependencies.py UNSTAGED", ["git", "diff", "--numstat", "--", "backend_app/core/subscription_dependencies.py"]),
    ]:
        r = run(args)
        print(f"  {label:<44} exit={r.returncode}  {r.stdout.strip() or '(no change)'}")
    print()

    print("=== C3  exchange-slot test: worktree vs branch, CRLF-normalised ===")
    path = "tests/test_quota01_exchange_slot_is_atomic.py"
    disk_raw = open(f"{REPO}\\" + path.replace("/", "\\"), "rb").read()
    disk = norm(disk_raw.decode("utf-8", errors="replace"))
    print(f"  worktree: {len(disk_raw)} raw bytes, {len(disk)} normalised chars, {disk.count(chr(10))} newlines")
    for label, rev in REVS:
        blob = show(f"{rev}:{path}")
        if blob is None:
            print(f"  {label:<6} absent")
            continue
        n = norm(blob)
        same = n == disk
        print(f"  {label:<6} {len(n)} normalised chars  identical_to_worktree={same}")
        if not same:
            # first differing line
            a, b = n.splitlines(), disk.splitlines()
            print(f"          lines: branch={len(a)} worktree={len(b)}")
            for i, (x, y) in enumerate(zip(a, b), 1):
                if x != y:
                    print(f"          first diff at line {i}:")
                    print(f"            branch  : {x[:120]}")
                    print(f"            worktree: {y[:120]}")
                    break
            else:
                print("          common prefix identical; lengths differ only")
    print()

    print("=== C4  AERORA_TEST_REDIS_URL in 01-pr-check.yml ===")
    wf = ".github/workflows/01-pr-check.yml"
    pat = re.compile(r"AERORA_TEST_REDIS_URL\s*:\s*(\S+)")
    disk_wf = norm(open(f"{REPO}\\" + wf.replace("/", "\\"), encoding="utf-8", errors="replace").read())
    hits = [(i, m.group(1)) for i, l in enumerate(disk_wf.splitlines(), 1) for m in [pat.search(l)] if m]
    print(f"  worktree  : {hits}")
    for label, rev in REVS:
        blob = show(f"{rev}:{wf}")
        if blob is None:
            print(f"  {label:<10}: absent")
            continue
        h = [(i, m.group(1)) for i, l in enumerate(norm(blob).splitlines(), 1) for m in [pat.search(l)] if m]
        print(f"  {label:<10}: {h}")
    print()

    print("=== C5  untracked count ===")
    r = run(["git", "ls-files", "--others", "--exclude-standard"])
    untracked = [l for l in r.stdout.splitlines() if l.strip()]
    print(f"  exit={r.returncode}  untracked={len(untracked)}")
    print()

    print("=== C6  d5-redis-diagnostics job presence ===")
    for label, rev in REVS:
        blob = show(f"{rev}:{wf}") or ""
        print(f"  {label:<10}: 'd5-redis-diagnostics' present={'d5-redis-diagnostics' in blob}")
    print(f"  worktree  : 'd5-redis-diagnostics' present={'d5-redis-diagnostics' in disk_wf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
