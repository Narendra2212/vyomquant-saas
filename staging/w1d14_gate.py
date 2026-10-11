"""Phase 11B-W1D-14. READ-ONLY. Preservation gate + D5 sign-off search.

Two jobs:

  Phase 0  confirm the W1D-13 result is intact and nothing concurrent has moved the five
           paths this phase would touch.
  Phase 1  establish whether D5's PRODUCTION-AUDIT sign-off exists, and whether it covers
           BOTH `diff_snapshots()` and `check_drift()`.

A sign-off must be an explicit affirmative statement by the production-audit stakeholder
about the severity routing in `verify_production_schema.py`. None of the following counts:
W1D-13's passing tests, the classifier being technically correct, a report recommending it,
the Code owner's conditional "YES subject to sign-off", or silence.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
STAGING = os.path.join(REPO, "staging")

EXPECT_HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"
EXPECT_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
EXPECT_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"

#: Post-W1D-13 fingerprints, from PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md §2.
EXPECT_BLOBS = {
    "scripts/verify_provisioning.py": "8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2",
    "scripts/verify_schema_drift.py": "f853c1482ca295440c78e4f25a8989438fab041e",
    "tests/test_drift_classification.py": "fcffbe8e01367aa7f99fffe2677267821fb8424d",
    "tests/test_snapshot_dimension_invariants.py": "5106dd5916424c9b795a5b393e36afc21c4ba9b4",
    # The second change's subject. Must still be the ORIGINAL blob.
    "scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444",
}

#: An affirmative sign-off. Deliberately narrow.
SIGNOFF = re.compile(
    r"\b(sign-?off\s+(given|granted|received|recorded)|"
    r"production-?audit\s+(owner|stakeholder)\s+(approves?|approved|signs?\s+off|granted)|"
    r"D5\s*(=|:)\s*(signed[- ]off|approved|granted)|"
    r"I\s+(approve|sign\s+off)\b[^.]{0,80}(severity|check_drift|audit))",
    re.I,
)
NEGATIVE = re.compile(r"not\s+(given|granted|satisfied|signed)|outstanding|absent|"
                      r"CONDITIONALLY|awaiting|pending|required", re.I)


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          errors="replace", timeout=300)


def read(p):
    try:
        raw = open(p, "rb").read()
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    problems = []

    def check(label, got, want):
        ok = got == want
        if not ok:
            problems.append(f"{label}: {got!r} != {want!r}")
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<50} {got}")

    print("=" * 78)
    print("PHASE 0 -- PRESERVATION GATE")
    print("=" * 78)
    check("HEAD", run(["git", "rev-parse", "HEAD"]).stdout.strip(), EXPECT_HEAD)
    check("branch", run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip(), "main")
    check("divergence", run(["git", "rev-list", "--left-right", "--count",
                             "origin/main...HEAD"]).stdout.strip(), "0\t0")

    entries = [l for l in run(["git", "ls-files", "-s"]).stdout.splitlines() if l.strip()]
    staged = [l for l in run(["git", "diff-index", "--cached", "HEAD"]).stdout.splitlines() if l.strip()]
    check("staged count", str(len(staged)), "22")
    check("digest A", hashlib.sha256("\n".join(entries).encode()).hexdigest(), EXPECT_A)
    check("digest C", hashlib.sha256("\n".join(sorted(staged)).encode()).hexdigest(), EXPECT_C)
    for label, args in [("unstaged", ["git", "diff", "--name-only"]),
                        ("untracked", ["git", "ls-files", "--others", "--exclude-standard"]),
                        ("unmerged", ["git", "ls-files", "-u"]),
                        ("stash", ["git", "stash", "list"])]:
        n = len([l for l in run(args).stdout.splitlines() if l.strip()])
        print(f"  info  {label:<50} {n}")
    print("  worktrees:")
    for l in run(["git", "worktree", "list"]).stdout.splitlines():
        print(f"        {l.strip()}")
    print()

    print("--- W1D-13 RESULT INTACT? ---")
    for path, expected in EXPECT_BLOBS.items():
        check(f"blob {path}", run(["git", "hash-object", path]).stdout.strip(), expected)
    print()

    print("--- CONCURRENT EDITS TO THE FIVE PATHS ---")
    unstaged = {l.strip() for l in run(["git", "diff", "--name-only"]).stdout.splitlines()}
    cached = {l.strip() for l in run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines()}
    for p in EXPECT_BLOBS:
        inu, inc = p in unstaged, p in cached
        if inu or inc:
            problems.append(f"{p} concurrently modified")
        print(f"  {'PASS' if not (inu or inc) else 'FAIL'}  {p:<48} unstaged={inu} staged={inc}")
    print()

    print("=" * 78)
    print("PHASE 1 -- D5 PRODUCTION-AUDIT SIGN-OFF")
    print("=" * 78)

    print("--- the authoritative W1D ledger ---")
    led = os.path.join(STAGING, "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
    for i, l in enumerate(read(led).splitlines(), 1):
        if re.search(r"\|\s*\*\*AP-W1D-\d\*\*", l):
            print(f"  ledger:{i}: {l.strip()[:150]}")
    print()

    print("--- what the decision record says about D5 ---")
    rec = os.path.join(STAGING, "PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md")
    for i, l in enumerate(read(rec).splitlines(), 1):
        if re.search(r"\bD5\b", l) and re.search(r"GRANT|sign-?off|condition", l, re.I):
            print(f"  W1D-12:{i}: {l.strip()[:150]}")
    print()

    print("--- repository-wide search for an affirmative sign-off ---")
    affirmative = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {
            ".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
            "dist", "build", "releases", "venv", ".venv", "site-packages",
            "python_portable", "Git", "aerora_quant_platform"}]
        for f in files:
            if not f.endswith((".md", ".txt", ".json", ".yml")):
                continue
            p = os.path.join(root, f)
            t = read(p)
            if not t:
                continue
            for m in SIGNOFF.finditer(t):
                seg = t[max(0, m.start() - 130): m.end() + 130].replace("\n", " ")
                negated = bool(NEGATIVE.search(seg))
                rel = os.path.relpath(p, REPO).replace("\\", "/")
                print(f"  [{'negated  ' if negated else 'AFFIRM?  '}] {rel}")
                print(f"      ...{seg[:180]}")
                if not negated:
                    affirmative.append((rel, seg))
    print(f"  candidate affirmative sign-offs: {len(affirmative)}")
    print()

    print("--- does any record name BOTH second-change functions as approved? ---")
    both = 0
    for root, dirs, files in os.walk(STAGING):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith((".md", ".txt")):
                continue
            t = read(os.path.join(root, f))
            if "diff_snapshots" in t and "check_drift" in t and SIGNOFF.search(t):
                seg = t[max(0, SIGNOFF.search(t).start() - 100): SIGNOFF.search(t).end() + 100]
                if not NEGATIVE.search(seg):
                    both += 1
                    print(f"  {f}: possible joint approval -- inspect")
    print(f"  records approving both functions: {both}")
    print()

    print("--- git history ---")
    r = run(["git", "log", "--all", "-i", "--oneline", "--grep=sign-off",
             "--grep=production-audit", "--grep=D5", "-10"])
    print(f"  {r.stdout.strip() or 'no matching commit'}")
    print()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    if problems:
        print(f"  PHASE 0 FAILED -- {len(problems)} problem(s):")
        for p in problems:
            print(f"    - {p}")
        return 2
    print("  PHASE 0: PASSED -- W1D-13 intact, no concurrent edits to the five paths.")
    if affirmative:
        print(f"  PHASE 1: {len(affirmative)} candidate(s) need human reading before relying on them.")
        return 3
    print("  PHASE 1: NO AFFIRMATIVE D5 SIGN-OFF EXISTS.")
    print("           => NO IMPLEMENTATION -- SIGN-OFF REQUIRED. Do not edit.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
