"""Phase 11B-W1D-11. READ-ONLY. Reconciliation preflight.

Three jobs, nothing more:
  1. Confirm the four candidate fingerprints are unchanged, which licenses reusing the
     W1D-09/W1D-10 call-graph and coverage analysis instead of repeating it.
  2. Recheck the authoritative approval records for AP-W1D-3 and for any decision matching
     D1..D5, classifying each as GRANTED / REJECTED / UNRESOLVED.
  3. Destination safety, preservation baseline, concurrent-work check.

A grant must be an explicit affirmative statement about THIS decision. A recommendation, a
report's existence, or silence is not a grant.
"""

from __future__ import annotations

import datetime
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
STAGING = os.path.join(REPO, "staging")
OBJ = os.path.join(REPO, ".git", "objects")
DEST = os.path.join(STAGING, "PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md")

PRIOR_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
PRIOR_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
PRIOR_TREE = "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4"

CANDIDATES = [
    ("scripts/verify_schema_drift.py", "f78b35a52552967d469a7cc7d33de92db0a65e3b"),
    ("scripts/verify_provisioning.py", "51cb6d241de3ef437b662241379f8985f7d5a51e"),
    ("scripts/verify_production_schema.py", "6e194f43a832d1f9cb0f99efd88dd204c5b3c444"),
    ("tests/test_snapshot_dimension_invariants.py", "bcb4f479fca2365cca2be61aae8961449e03beb9"),
]

PRIOR_REPORTS = [
    "PHASE11B_W1D10_CLASSIFIER_IMPLEMENTATION_READINESS.md",
    "PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md",
    "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md",
]

# Phrases that would constitute a real grant, as opposed to discussing one.
AFFIRM = re.compile(
    r"\b(I approve|approved by|hereby grant|is granted|now granted|"
    r"authori[sz]ation granted|you may implement|proceed with implementation|"
    r"D[1-5]\s*=\s*(approved|granted|yes))\b", re.I)


def run(args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          errors="replace", env=e, timeout=240)


def read(p):
    try:
        raw = open(p, "rb").read()
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    print("=== 0. DESTINATION ===")
    print(f"  exists={os.path.exists(DEST)} -> {'OCCUPIED' if os.path.exists(DEST) else 'FREE'}")
    print()

    print("=== 1. PRIOR REPORTS PRESENT ===")
    for n in PRIOR_REPORTS:
        p = os.path.join(STAGING, n)
        print(f"  {'EXISTS ' if os.path.exists(p) else 'MISSING'} "
              f"{os.path.getsize(p) if os.path.exists(p) else 0:>7} B  {n}")
    print()

    print("=== 2. CANDIDATE FINGERPRINTS (licenses reuse of prior analysis) ===")
    drift = False
    for p, expected in CANDIDATES:
        h = run(["git", "hash-object", p]).stdout.strip()
        same = h == expected
        drift |= not same
        print(f"  {'SAME ' if same else 'DRIFT'}  {h}  {p}")
    print(f"  -> prior call-graph / coverage analysis may be reused: {not drift}")
    print()

    print("=== 3. AP-W1D-3 AND THE FULL APPROVAL LEDGER ===")
    led = os.path.join(STAGING, "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
    text = read(led)
    for i, l in enumerate(text.splitlines(), 1):
        if re.search(r"\|\s*\*\*AP-W1D-\d\*\*\s*\|", l):
            print(f"  ledger:{i}: {l.strip()[:165]}")
    print()
    print("  owner role column, extracted:")
    for i, l in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*\|\s*\*\*(AP-W1D-\d)\*\*\s*\|(.+?)\|(.+?)\|(.+?)\|", l)
        if m:
            ap, what, who, state = (x.strip() for x in m.groups())
            print(f"      {ap}  owner={who!r:<34} state={state}")
    print()

    print("=== 4. SEARCH FOR AN AFFIRMATIVE GRANT (D1..D5 or AP-W1D-3) ===")
    hits = 0
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {
            ".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
            "dist", "build", "releases", "venv", ".venv", "site-packages",
            "python_portable", "Git", "aerora_quant_platform"}]
        for f in files:
            if not f.endswith((".md", ".txt", ".json")):
                continue
            p = os.path.join(root, f)
            t = read(p)
            if not t:
                continue
            for m in AFFIRM.finditer(t):
                seg = t[max(0, m.start() - 110): m.end() + 110].replace("\n", " ")
                if not re.search(r"AP-W1D|classifier|drift|D[1-5]\b", seg, re.I):
                    continue
                hits += 1
                rel = os.path.relpath(p, REPO).replace("\\", "/")
                print(f"  {rel}: ...{seg[:190]}")
    print(f"  affirmative grants relating to this decision set: {hits}")
    r = run(["git", "log", "--all", "-i", "--oneline", "--grep=AP-W1D",
             "--grep=classifier", "--grep=W1C-02", "-10"])
    print(f"  git log: {r.stdout.strip() or 'no matching commit'}")
    print()

    print("=== 5. PRESERVATION BASELINE ===")
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
    ti, to = tempfile.mkdtemp(prefix="w1d11i_"), tempfile.mkdtemp(prefix="w1d11o_")
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

    print("=== 6. CONCURRENT WORK: newest staging artifacts ===")
    rows = sorted(((os.path.getmtime(os.path.join(STAGING, n)), n)
                   for n in os.listdir(STAGING)
                   if os.path.isfile(os.path.join(STAGING, n))), reverse=True)
    mine = ("w1d09_", "w1d10_", "w1d11_", "PHASE11B_W1D09", "PHASE11B_W1D10", "PHASE11B_W1D11")
    for mt, n in rows[:12]:
        tag = "(this session)" if n.startswith(mine) else "*** OTHER ***"
        print(f"  {datetime.datetime.fromtimestamp(mt).strftime('%H:%M:%S')}  {tag:<16} {n}")
    print()
    print("  do any concurrent artifacts touch the drift/provisioning scripts?")
    touched = []
    for mt, n in rows[:40]:
        if n.startswith(mine):
            continue
        t = read(os.path.join(STAGING, n))
        if re.search(r"verify_schema_drift|verify_provisioning|verify_production_schema", t):
            touched.append(n)
    print(f"      {touched or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
