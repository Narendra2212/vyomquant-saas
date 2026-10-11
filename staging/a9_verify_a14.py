"""Phase 11B-A9. READ-ONLY. Verify A14's load-bearing claims against the current repo.

The brief says to treat prior reports' claims as unverified until rechecked. A14 claims the
work this brief asks for is already done. Each claim that would change my actions is checked
here independently.

  V1  PR #7 and PR #8 are the same tree (so one must close regardless)
  V2  the metered test file carries the two production-path real-Redis tests
  V3  the worktree workflow has AERORA_TEST_REDIS_URL=/0 and no d5-redis-diagnostics job
  V4  the staged set is still the same 22 paths with the same blob identities
  V5  HEAD / upstream / branch unchanged
  V6  which of the 9 Phase-3 assertions the existing tests actually make
"""

from __future__ import annotations

import hashlib
import re
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"

PR = {
    "#6": "d07014d36d32c63db44f8fb9659933c0e84e82f2",
    "#7": "49e70da23c2858ffc9a6f34d5db4521401477c82",
    "#8": "a624ce89b16005b0c73197005d6d0672737dad65",
}


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    print("=== V1  PR tree identity ===")
    trees = {}
    for label, sha in PR.items():
        r = run(["git", "rev-parse", f"{sha}^{{tree}}"])
        trees[label] = r.stdout.strip()
        print(f"  PR {label}  commit={sha[:10]}  tree={trees[label]}")
    same78 = trees["#7"] == trees["#8"]
    print(f"  PR#7 tree == PR#8 tree : {same78}")
    d = run(["git", "diff", "--stat", PR["#7"], PR["#8"]])
    print(f"  git diff #7..#8 exit={d.returncode} output={d.stdout.strip()[:120] or '(empty)'}")
    print(f"  PR#6 tree distinct     : {trees['#6'] != trees['#7']}")
    print()

    print("=== V2  production-path tests present ===")
    path = REPO + r"\tests\test_quota01_metered_reservation_is_hard.py"
    text = open(path, encoding="utf-8", errors="replace").read()
    print(f"  file lines={text.count(chr(10))}  bytes={len(text.encode('utf-8'))}")
    for name in (
        "test_production_reserve_quota_admits_exactly_the_limit_against_real_redis",
        "test_production_zero_allowance_writes_no_meter_against_real_redis",
        "test_production_reserve_quota_is_atomic_against_real_redis",
    ):
        print(f"  {name[:72]:<74} {'PRESENT' if name in text else 'absent'}")
    print()

    print("=== V3  worktree workflow ===")
    wf = open(REPO + r"\.github\workflows\01-pr-check.yml", encoding="utf-8", errors="replace").read()
    print(f"  lines={wf.count(chr(10))}")
    for i, l in enumerate(wf.splitlines(), 1):
        if "AERORA_TEST_REDIS_URL" in l or re.search(r"^\s*REDIS_URL\s*:", l):
            print(f"    {i:>5}: {l.strip()[:90]}")
    print(f"  d5-redis-diagnostics in worktree: {'d5-redis-diagnostics' in wf}")
    jobs = re.findall(r"^  ([a-z0-9][a-z0-9\-]*):\s*$", wf, re.M)
    print(f"  job keys: {jobs}")
    print()

    print("=== V4/V5  staged set and identity ===")
    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    print(f"  tracked entries={len(entries)}  content-sha256={hashlib.sha256(chr(10).join(entries).encode()).hexdigest()}")
    di = run(["git", "diff-index", "--cached", "HEAD"])
    rows = [l for l in di.stdout.splitlines() if l.strip()]
    print(f"  staged rows={len(rows)}")
    blobs = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
    print(f"  staged blob-identity sha256={blobs}")
    for key in ("rev-parse HEAD", "rev-parse --abbrev-ref HEAD"):
        r = run(["git"] + key.split())
        print(f"  git {key} -> {r.stdout.strip()}")
    r = run(["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"])
    print(f"  origin/main...HEAD -> {r.stdout.strip()}")
    r = run(["git", "stash", "list"])
    print(f"  stash entries={len([l for l in r.stdout.splitlines() if l.strip()])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
