"""Phase 11B-W1D-08. READ-ONLY. Preflight: baseline, destination, and existing coverage.

The brief's section 3 says "First determine whether existing tests already cover either
invariant. Add no redundant tests." W1D-05 created
`tests/test_snapshot_dimension_invariants.py` with 48 tests claiming to cover exactly these
two invariants. So the decisive question for this phase is whether that coverage is real and
strong, not whether a new file can be written.

This maps every test in that file to invariant A (deterministic ordering) or B (explicit key
declaration), and checks whether a PRODUCTION key declaration exists -- because the brief
forbids adding one and forbids introducing a test-local duplicate.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
OBJ = os.path.join(REPO, ".git", "objects")
DEST = os.path.join(REPO, "staging", "PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md")

GUARD_TEST = "tests/test_snapshot_dimension_invariants.py"
PROD_FILES = [
    "scripts/verify_schema_drift.py",
    "scripts/verify_provisioning.py",
    "scripts/verify_production_schema.py",
]

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


def main() -> int:
    print("=== 0. DESTINATION ===")
    print(f"  PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md exists={os.path.exists(DEST)}")
    print(f"  -> {'OCCUPIED' if os.path.exists(DEST) else 'FREE'}")
    print()

    print("=== 1. BASELINE ===")
    for label, args in [
        ("HEAD", ["git", "rev-parse", "HEAD"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("origin/main", ["git", "rev-parse", "origin/main"]),
        ("divergence L/R", ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]),
    ]:
        print(f"  {label:<16} {run(args).stdout.strip()}")
    print("  worktrees:")
    for l in run(["git", "worktree", "list"]).stdout.splitlines():
        print(f"      {l.strip()}")

    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    dig_a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    dig_c = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()
    print(f"  tracked entries  {len(entries)}")
    print(f"  staged           {len(staged)}")
    for k, a in [
        ("unstaged", ["git", "diff", "--name-only"]),
        ("untracked", ["git", "ls-files", "--others", "--exclude-standard"]),
        ("unmerged", ["git", "ls-files", "-u"]),
        ("stash", ["git", "stash", "list"]),
    ]:
        print(f"  {k:<16} {len([l for l in run(a).stdout.splitlines() if l.strip()])}")
    print(f"  digest A  {dig_a}  {'SAME' if dig_a == PRIOR_A else 'DRIFT'}")
    print(f"  digest C  {dig_c}  {'SAME' if dig_c == PRIOR_C else 'DRIFT'}")

    before = sum(len(f) for _, _, f in os.walk(OBJ))
    ti, to = tempfile.mkdtemp(prefix="w1d08_i_"), tempfile.mkdtemp(prefix="w1d08_o_")
    try:
        idx = os.path.join(ti, "private.index")
        shutil.copy2(os.path.join(REPO, ".git", "index"), idx)
        wt = run(["git", "write-tree"], env={
            "GIT_INDEX_FILE": idx,
            "GIT_OBJECT_DIRECTORY": to,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": OBJ,
        })
        print(f"  index tree {wt.stdout.strip()}  {'SAME' if wt.stdout.strip() == PRIOR_TREE else 'DRIFT'}")
        print(f"  objects into isolated dir: {sum(len(f) for _, _, f in os.walk(to))}")
    finally:
        shutil.rmtree(ti, ignore_errors=True)
        shutil.rmtree(to, ignore_errors=True)
    print(f"  shared .git/objects {before} -> {sum(len(f) for _, _, f in os.walk(OBJ))}")
    print()

    print("=== 2. OWNERSHIP / STATUS OF EVERY CANDIDATE FILE ===")
    for p in PROD_FILES + [GUARD_TEST]:
        full = os.path.join(REPO, p.replace("/", os.sep))
        st = run(["git", "status", "--porcelain", "--", p]).stdout.strip()
        h = run(["git", "hash-object", p]).stdout.strip() if os.path.exists(full) else "-"
        print(f"  {p}")
        print(f"      status={st or '(clean)'!r}  size={os.path.getsize(full) if os.path.exists(full) else 0}  blob={h}")
    print()

    print("=== 3. DOES A PRODUCTION KEY DECLARATION EXIST? ===")
    for p in PROD_FILES:
        text = open(os.path.join(REPO, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
        for name in ("COMPARISON_KEY", "WHOLE_ROW", "KEY_WIDTH"):
            n = len(re.findall(rf"\b{name}\b", text))
            if n:
                print(f"  {p}: {name} x{n}")
    print("  -> production declaration present: ", end="")
    any_prod = any(
        "COMPARISON_KEY" in open(os.path.join(REPO, p.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
        for p in PROD_FILES
    )
    print(any_prod)
    print()

    print("=== 4. WHERE THE KEY DECLARATION ACTUALLY LIVES ===")
    gt = open(os.path.join(REPO, GUARD_TEST.replace("/", os.sep)), encoding="utf-8", errors="replace").read()
    for i, l in enumerate(gt.splitlines(), 1):
        if re.match(r"^(COMPARISON_KEY|WHOLE_ROW|ADVISORY_DIMENSIONS)\b", l.strip()):
            print(f"  {GUARD_TEST}:{i}: {l.strip()[:90]}")
    print()

    print("=== 5. EXISTING TEST INVENTORY, mapped to invariant A / B ===")
    tests = re.findall(r"^def (test_\w+)", gt, re.M)
    print(f"  tests defined in {GUARD_TEST}: {len(tests)}")
    A_RE = re.compile(r"order_by|ordering|order", re.I)
    B_RE = re.compile(r"comparison_key|declare|key_width|whole_row|advisory", re.I)
    a_tests, b_tests, other = [], [], []
    for t in tests:
        if A_RE.search(t):
            a_tests.append(t)
        elif B_RE.search(t):
            b_tests.append(t)
        else:
            other.append(t)
    print(f"  -- invariant A (deterministic ordering): {len(a_tests)}")
    for t in a_tests:
        print(f"       {t}")
    print(f"  -- invariant B (explicit key declaration): {len(b_tests)}")
    for t in b_tests:
        print(f"       {t}")
    print(f"  -- helper / extractor / other: {len(other)}")
    for t in other:
        print(f"       {t}")
    print()

    print("=== 6. IS TEST A A MERE SUBSTRING CHECK? ===")
    depth = "depth" in gt.lower()
    literal = "literal" in gt.lower() or "quote" in gt.lower()
    subq = "subquer" in gt.lower()
    covers = "covers_the_declared_key" in gt
    print(f"  mentions parenthesis depth tracking : {depth}")
    print(f"  mentions string-literal handling    : {literal}")
    print(f"  mentions subquery ORDER BY          : {subq}")
    print(f"  has an ORDER-BY-covers-key test     : {covers}")
    print("  -> a plain `'ORDER BY' in sql` check would have none of these.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
