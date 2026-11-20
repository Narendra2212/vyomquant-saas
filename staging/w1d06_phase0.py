"""Phase 11B-W1D-06. READ-ONLY. Phase 0 rebaseline and shared-state preservation.

Index tree is verified with BOTH an isolated index file and an isolated object directory, so
no verification object can reach the shared store:

    GIT_INDEX_FILE                   = <temp>   (seeded by read-tree from the real index's tree)
    GIT_OBJECT_DIRECTORY             = <temp>   (new objects land here)
    GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects   (existing objects stay readable)

Digest uses the method this session has used since A9/A10 (method A), and is compared only
against values produced by that same method.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile

REPO = r"c:\aerora_quant_backend_updated_final1"
OBJ = os.path.join(REPO, ".git", "objects")
DEST = os.path.join(REPO, "staging", "PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md")

# Method-A values previously recorded by THIS session (A9, A10, A17).
PRIOR_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
PRIOR_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
PRIOR_TREE = "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4"


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=240
    )


def objcount() -> int:
    return sum(len(f) for _, _, f in os.walk(OBJ))


def main() -> int:
    print("=== DESTINATION ===")
    print(f"  PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md exists={os.path.exists(DEST)}")
    print(f"  -> {'OCCUPIED - do not overwrite' if os.path.exists(DEST) else 'FREE'}")
    print()

    print("=== GIT STATE ===")
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("origin/main", ["git", "rev-parse", "origin/main"]),
        ("divergence L/R", ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]),
    ]:
        print(f"  {label:<16} {run(args).stdout.strip()}")
    wt = run(["git", "worktree", "list"])
    print("  worktrees:")
    for l in wt.stdout.splitlines():
        print(f"      {l.strip()}")
    print()

    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    dig_a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    di = run(["git", "diff-index", "--cached", "HEAD"])
    staged = [l for l in di.stdout.splitlines() if l.strip()]
    dig_c = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()
    unstaged = [l for l in run(["git", "diff", "--name-only"]).stdout.splitlines() if l.strip()]
    untracked = [l for l in run(["git", "ls-files", "--others", "--exclude-standard"]).stdout.splitlines() if l.strip()]
    unmerged = [l for l in run(["git", "ls-files", "-u"]).stdout.splitlines() if l.strip()]
    stash = [l for l in run(["git", "stash", "list"]).stdout.splitlines() if l.strip()]

    print("=== COUNTS ===")
    print(f"  tracked entries  {len(entries)}")
    print(f"  staged           {len(staged)}")
    print(f"  unstaged         {len(unstaged)}")
    print(f"  untracked        {len(untracked)}")
    print(f"  unmerged         {len(unmerged)}")
    print(f"  stash            {len(stash)}")
    print()

    print("=== DIGESTS (method A / C, this session's established method) ===")
    print(f"  A measured {dig_a}")
    print(f"  A prior    {PRIOR_A}   {'SAME' if dig_a == PRIOR_A else 'DRIFT'}")
    print(f"  C measured {dig_c}")
    print(f"  C prior    {PRIOR_C}   {'SAME' if dig_c == PRIOR_C else 'DRIFT'}")
    print()

    print("=== INDEX TREE via ISOLATED index + ISOLATED object dir ===")
    before = objcount()
    tmp_idx = tempfile.mkdtemp(prefix="w1d06_idx_")
    tmp_obj = tempfile.mkdtemp(prefix="w1d06_obj_")
    try:
        idx = os.path.join(tmp_idx, "private.index")
        env = {
            "GIT_INDEX_FILE": idx,
            "GIT_OBJECT_DIRECTORY": tmp_obj,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": OBJ,
        }
        # Seed the private index from the SHARED index's content by replaying ls-files.
        # Simplest faithful route: copy the shared index file, then write-tree from the copy.
        shutil.copy2(os.path.join(REPO, ".git", "index"), idx)
        wtree = run(["git", "write-tree"], env=env)
        print(f"  write-tree exit={wtree.returncode}")
        print(f"  INDEX TREE  {wtree.stdout.strip()}")
        print(f"  prior       {PRIOR_TREE}   "
              f"{'SAME' if wtree.stdout.strip() == PRIOR_TREE else 'DRIFT'}")
        if wtree.returncode != 0:
            print(f"  stderr: {wtree.stderr.strip()[:200]}")
        created = sum(len(f) for _, _, f in os.walk(tmp_obj))
        print(f"  objects written to isolated dir : {created}")
    finally:
        shutil.rmtree(tmp_idx, ignore_errors=True)
        shutil.rmtree(tmp_obj, ignore_errors=True)
    after = objcount()
    print(f"  shared .git/objects {before} -> {after}  delta={after - before}")
    print(f"  isolated dirs removed: {not os.path.exists(tmp_idx) and not os.path.exists(tmp_obj)}")
    print()

    print("=== TARGET FILES: tracked state ===")
    for p in (
        "scripts/verify_provisioning.py",
        "scripts/verify_schema_drift.py",
        "tests/test_snapshot_dimension_invariants.py",
    ):
        st = run(["git", "status", "--porcelain", "--", p]).stdout.strip()
        h = run(["git", "hash-object", p]).stdout.strip()
        size = os.path.getsize(os.path.join(REPO, p.replace("/", os.sep)))
        print(f"  {p}")
        print(f"      status={st or '(clean/tracked)'!r}  size={size}  hash={h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
