"""Phase 11B-W1D-10. READ-ONLY. Destination, authorization, baseline, concurrency."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
STAGING = os.path.join(REPO, "staging")
OBJ = os.path.join(REPO, ".git", "objects")
DEST = os.path.join(STAGING, "PHASE11B_W1D10_CLASSIFIER_IMPLEMENTATION_READINESS.md")

PRIOR_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
PRIOR_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
PRIOR_TREE = "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4"

CANDIDATES = [
    ("scripts/verify_schema_drift.py", "f78b35a52552967d469a7cc7d33de92db0a65e3b"),
    ("scripts/verify_provisioning.py", "51cb6d241de3ef437b662241379f8985f7d5a51e"),
    ("scripts/verify_production_schema.py", "6e194f43a832d1f9cb0f99efd88dd204c5b3c444"),
    ("tests/test_snapshot_dimension_invariants.py", "bcb4f479fca2365cca2be61aae8961449e03beb9"),
]

REQUIRED_READS = [
    "PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md",
    "p11b_w1d06x_multiplicity.txt",
    "p11b_w1d06x_prototype.txt",
    "PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md",
    "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md",
]


def run(args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          errors="replace", env=e, timeout=240)


def main() -> int:
    print("=== 0. DESTINATION ===")
    print(f"  exists={os.path.exists(DEST)} -> {'OCCUPIED' if os.path.exists(DEST) else 'FREE'}")
    print()

    print("=== 1. REQUIRED PRIOR ARTIFACTS PRESENT? ===")
    for n in REQUIRED_READS:
        p = os.path.join(STAGING, n)
        print(f"  {'EXISTS ' if os.path.exists(p) else 'MISSING'} "
              f"{os.path.getsize(p) if os.path.exists(p) else 0:>7} B  {n}")
    print()

    print("=== 2. AUTHORIZATION: AP-W1D-3 ===")
    led = os.path.join(STAGING, "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
    for i, l in enumerate(open(led, encoding="utf-8", errors="replace").read().splitlines(), 1):
        if "AP-W1D-3" in l:
            print(f"  ledger:{i}: {l.strip()[:150]}")
    print(f"  git log --grep=AP-W1D : "
          f"{run(['git','log','--all','-i','--oneline','--grep=AP-W1D','-5']).stdout.strip() or 'no matching commit'}")
    # any scope decision recorded anywhere?
    import re
    grant = re.compile(r"(scope|classifier|reorder\w*).{0,60}(approved|authoriz\w+|granted)", re.I | re.S)
    hits = 0
    for n in sorted(os.listdir(STAGING)):
        p = os.path.join(STAGING, n)
        if not os.path.isfile(p) or os.path.splitext(n)[1] not in (".md", ".txt"):
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in grant.finditer(t):
            seg = t[max(0, m.start() - 70): m.end() + 40].replace("\n", " ")
            if "NOT GRANTED" in seg or "not granted" in seg.lower():
                continue
            hits += 1
            print(f"  possible scope grant in {n}: ...{seg[:150]}")
    print(f"  candidate scope grants (excluding explicit negatives): {hits}")
    print()

    print("=== 3. BASELINE ===")
    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    c = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()
    print(f"  HEAD       {run(['git','rev-parse','HEAD']).stdout.strip()}")
    print(f"  branch     {run(['git','rev-parse','--abbrev-ref','HEAD']).stdout.strip()}")
    print(f"  divergence {run(['git','rev-list','--left-right','--count','origin/main...HEAD']).stdout.strip()}")
    print(f"  tracked {len(entries)}  staged {len(staged)}")
    for k, args in [("unstaged", ["git", "diff", "--name-only"]),
                    ("untracked", ["git", "ls-files", "--others", "--exclude-standard"]),
                    ("unmerged", ["git", "ls-files", "-u"]),
                    ("stash", ["git", "stash", "list"])]:
        print(f"  {k:<10} {len([l for l in run(args).stdout.splitlines() if l.strip()])}")
    print(f"  digest A {a}  {'SAME' if a == PRIOR_A else 'DRIFT'}")
    print(f"  digest C {c}  {'SAME' if c == PRIOR_C else 'DRIFT'}")
    print("  worktrees:")
    for l in run(["git", "worktree", "list"]).stdout.splitlines():
        print(f"      {l.strip()}")

    before = sum(len(f) for _, _, f in os.walk(OBJ))
    ti, to = tempfile.mkdtemp(prefix="w1d10i_"), tempfile.mkdtemp(prefix="w1d10o_")
    try:
        idx = os.path.join(ti, "private.index")
        shutil.copy2(os.path.join(REPO, ".git", "index"), idx)
        wt = run(["git", "write-tree"], env={
            "GIT_INDEX_FILE": idx, "GIT_OBJECT_DIRECTORY": to,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": OBJ})
        print(f"  index tree {wt.stdout.strip()}  {'SAME' if wt.stdout.strip() == PRIOR_TREE else 'DRIFT'}")
        print(f"  objects into isolated dir {sum(len(f) for _, _, f in os.walk(to))}")
    finally:
        shutil.rmtree(ti, ignore_errors=True)
        shutil.rmtree(to, ignore_errors=True)
    print(f"  shared .git/objects {before} -> {sum(len(f) for _, _, f in os.walk(OBJ))}")
    print()

    print("=== 4. CANDIDATE FINGERPRINTS vs W1D-09 ===")
    for p, expected in CANDIDATES:
        h = run(["git", "hash-object", p]).stdout.strip()
        print(f"  {'SAME ' if h == expected else 'DRIFT'}  {h}  {p}")
    print()

    print("=== 5. CONCURRENT SESSIONS: newest staging artifacts ===")
    import datetime
    rows = []
    for n in os.listdir(STAGING):
        p = os.path.join(STAGING, n)
        if os.path.isfile(p):
            rows.append((os.path.getmtime(p), n))
    rows.sort(reverse=True)
    for mt, n in rows[:14]:
        mine = n.startswith(("w1d09_", "w1d10_", "PHASE11B_W1D09", "PHASE11B_W1D10",
                             "probe_p11b_w1d06_", "w1d06", "w1d07", "w1d08",
                             "PHASE11B_W1D06", "PHASE11B_W1D07", "PHASE11B_W1D08"))
        print(f"  {datetime.datetime.fromtimestamp(mt).strftime('%H:%M:%S')}  "
              f"{'(this session)' if mine else '*** OTHER ***':<16} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
