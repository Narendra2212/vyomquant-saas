"""Phase 11B-W1D-09. READ-ONLY. Output consumers, authorization recheck, baseline.

Phase 2 of the brief requires inspecting existing consumers before recommending any output
schema change. Phase 0 requires rechecking AP-W1D-3 and confirming the destination is free.
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
DEST = os.path.join(REPO, "staging", "PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md")

SKIP = {".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
        "dist", "build", "releases", "venv", ".venv", "site-packages", "python_portable", "Git"}

PRIOR_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
PRIOR_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
PRIOR_TREE = "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4"

# The literal strings today's output emits. Anything parsing these is a consumer.
OUTPUT_TOKENS = [
    "missing, ",
    "unexpected",
    "MISSING from the database",
    "NOT IN this repository",
    "RESULT: DRIFTED",
    "RESULT: NO DRIFT",
    "disappeared",
    "appeared",
    "ADVISORY",
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

    print("=== 1. AP-W1D-3 RECHECK (authoritative record) ===")
    led = os.path.join(REPO, "staging", "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
    if os.path.exists(led):
        for i, l in enumerate(open(led, encoding="utf-8", errors="replace").read().splitlines(), 1):
            if "AP-W1D-3" in l:
                print(f"  ledger:{i}: {l.strip()[:150]}")
    g = run(["git", "log", "--all", "-i", "--oneline", "--grep=AP-W1D", "-5"])
    print(f"  git log --grep=AP-W1D : {g.stdout.strip() or 'no matching commit'}")
    print()

    print("=== 2. OUTPUT CONSUMERS: who parses the drift output? ===")
    found = {}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if not f.endswith((".py", ".yml", ".yaml", ".sh", ".ps1", ".js", ".ts", ".json")):
                continue
            p = os.path.join(root, f)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            for tok in OUTPUT_TOKENS:
                if tok in text:
                    found.setdefault(rel, []).append(tok)
    producers = {
        "scripts/verify_schema_drift.py", "scripts/verify_provisioning.py",
        "scripts/verify_production_schema.py",
    }
    print("  PRODUCERS (emit the text):")
    for rel in sorted(k for k in found if k in producers):
        print(f"      {rel}: {sorted(set(found[rel]))}")
    print("  OTHER files containing those tokens:")
    others = [k for k in found if k not in producers]
    for rel in sorted(others):
        kind = ("staging report/probe" if rel.startswith("staging/")
                else "test" if rel.startswith("tests/")
                else "workflow" if rel.startswith(".github/")
                else "OTHER")
        print(f"      [{kind:<22}] {rel}: {sorted(set(found[rel]))}")
    real = [k for k in others if not k.startswith(("staging/", "tests/"))]
    print()
    print(f"  files outside staging/ and tests/ referencing the output text: {len(real)}")
    for r in real:
        print(f"      {r}")
    print()

    print("=== 2b. DOES ANY CALLER INVOKE THE SCRIPTS AND PARSE stdout? ===")
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if not f.endswith((".py", ".yml", ".yaml", ".sh", ".ps1")):
                continue
            p = os.path.join(root, f)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            if rel in producers:
                continue
            for m in re.finditer(r"verify_schema_drift|verify_production_schema|verify_provisioning", text):
                line_no = text[:m.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1].strip()
                print(f"  {rel}:{line_no}: {line[:110]}")
                break
    print()

    print("=== 3. BASELINE ===")
    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    c = hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest()
    print(f"  HEAD        {run(['git','rev-parse','HEAD']).stdout.strip()}")
    print(f"  branch      {run(['git','rev-parse','--abbrev-ref','HEAD']).stdout.strip()}")
    print(f"  divergence  {run(['git','rev-list','--left-right','--count','origin/main...HEAD']).stdout.strip()}")
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
    ti, to = tempfile.mkdtemp(prefix="w1d09i_"), tempfile.mkdtemp(prefix="w1d09o_")
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

    print("=== 4. CANDIDATE FILES: unchanged? ===")
    for p in ["scripts/verify_schema_drift.py", "scripts/verify_provisioning.py",
              "scripts/verify_production_schema.py",
              "tests/test_snapshot_dimension_invariants.py"]:
        print(f"  {run(['git','hash-object',p]).stdout.strip()}  "
              f"{run(['git','status','--porcelain','--',p]).stdout.strip() or '(clean)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
