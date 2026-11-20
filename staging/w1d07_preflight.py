"""Phase 11B-W1D-07. READ-ONLY PREFLIGHT. Authorization + baseline, before any edit.

The brief makes authorization a hard stop condition and explicitly forbids inferring
approval from the prompt or from the W1D-06 readiness report. So this searches the
repository for an actual grant of:

  AP-W1D-3   authorization to implement the W1C-02 remediation
  scope      owner approval for a SHARED classifier across all three functions
  behaviour  owner acceptance of the reordered-identical-rows verdict change

A request, a recommendation, or a report proposing something is NOT a grant. The search
therefore separates grant-shaped language from request-shaped language and prints the
surrounding context so the distinction can be judged rather than asserted.
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
STAGING = os.path.join(REPO, "staging")
OBJ = os.path.join(REPO, ".git", "objects")
DEST = os.path.join(STAGING, "PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md")

TARGETS = [
    "scripts/verify_provisioning.py",
    "scripts/verify_schema_drift.py",
    "scripts/verify_production_schema.py",
    "tests/test_snapshot_dimension_invariants.py",
    "tests/test_drift_classification.py",
]

PRIOR_A = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
PRIOR_C = "8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251"
PRIOR_TREE = "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4"

# Language that would constitute an actual grant.
GRANT_RE = re.compile(
    r"\b(approved|authori[sz]ed|granted|I approve|you may proceed|go ahead|"
    r"permission granted|sign-?off given|ACK)\b", re.I
)
# Language that is merely asking for one.
REQUEST_RE = re.compile(
    r"\b(request|requires?|needs?|awaiting|pending|not granted|absent|unresolved|"
    r"decision needed|proposed|recommend)\b", re.I
)


def run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=REPO, capture_output=True, text=True, errors="replace", env=e, timeout=240
    )


def main() -> int:
    print("=== 0. DESTINATION ===")
    print(f"  PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md exists={os.path.exists(DEST)}")
    print(f"  -> {'OCCUPIED' if os.path.exists(DEST) else 'FREE'}")
    print()

    print("=== 1. AUTHORIZATION SEARCH: 'AP-W1D-3' across staging/ ===")
    hits = 0
    for name in sorted(os.listdir(STAGING)):
        p = os.path.join(STAGING, name)
        if not os.path.isfile(p) or os.path.splitext(name)[1] not in (".md", ".txt", ".json"):
            continue
        try:
            raw = open(p, "rb").read()
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "AP-W1D-3" in line or "AP-W1D3" in line:
                hits += 1
                grant = bool(GRANT_RE.search(line))
                req = bool(REQUEST_RE.search(line))
                kind = "GRANT?" if grant and not req else ("request" if req else "mention")
                print(f"  [{kind:>7}] {name}:{i}")
                print(f"            {line.strip()[:150]}")
    print(f"  total AP-W1D-3 references: {hits}")
    print()

    print("=== 2. AUTHORIZATION SEARCH: W1C-02 status lines ===")
    for name in sorted(os.listdir(STAGING)):
        if not name.startswith("PHASE11B_W1"):
            continue
        p = os.path.join(STAGING, name)
        if not os.path.isfile(p):
            continue
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"W1C-02", line) and re.search(r"OPEN|CLOSED|approved|authoriz", line, re.I):
                print(f"  {name}:{i}")
                print(f"      {line.strip()[:150]}")
    print()

    print("=== 3. ANY GRANT-SHAPED STATEMENT ABOUT THE THREE DECISIONS? ===")
    patterns = {
        "shared classifier scope": r"shared (classifier|helper).{0,80}(approv|authoriz|grant)",
        "reorder behaviour change": r"(reorder|reordered).{0,80}(approv|authoriz|accept|grant)",
        "implement W1C-02": r"(implement|remediat).{0,60}W1C-02.{0,60}(approv|authoriz|grant)",
    }
    found_any = False
    for label, pat in patterns.items():
        rx = re.compile(pat, re.I | re.S)
        n = 0
        for name in sorted(os.listdir(STAGING)):
            p = os.path.join(STAGING, name)
            if not os.path.isfile(p) or os.path.splitext(name)[1] not in (".md", ".txt"):
                continue
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in rx.finditer(text):
                n += 1
                found_any = True
                seg = text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")
                print(f"  {label}: {name}  ...{seg[:170]}")
        if n == 0:
            print(f"  {label}: NO MATCH")
    print(f"  any grant-shaped statement found: {found_any}")
    print()

    print("=== 4. GIT BASELINE ===")
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
    counts = {
        "unstaged": ["git", "diff", "--name-only"],
        "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
        "unmerged": ["git", "ls-files", "-u"],
        "stash": ["git", "stash", "list"],
    }
    print(f"  tracked entries  {len(entries)}")
    print(f"  staged           {len(staged)}")
    for k, a in counts.items():
        print(f"  {k:<16} {len([l for l in run(a).stdout.splitlines() if l.strip()])}")
    print(f"  digest A  {dig_a}  {'SAME' if dig_a == PRIOR_A else 'DRIFT'}")
    print(f"  digest C  {dig_c}  {'SAME' if dig_c == PRIOR_C else 'DRIFT'}")
    print()

    print("=== 5. INDEX TREE (isolated index + isolated object dir) ===")
    before = sum(len(f) for _, _, f in os.walk(OBJ))
    ti = tempfile.mkdtemp(prefix="w1d07_idx_")
    to = tempfile.mkdtemp(prefix="w1d07_obj_")
    try:
        idx = os.path.join(ti, "private.index")
        shutil.copy2(os.path.join(REPO, ".git", "index"), idx)
        env = {
            "GIT_INDEX_FILE": idx,
            "GIT_OBJECT_DIRECTORY": to,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": OBJ,
        }
        wt = run(["git", "write-tree"], env=env)
        print(f"  write-tree exit={wt.returncode}  tree={wt.stdout.strip()}")
        print(f"  prior {PRIOR_TREE}  {'SAME' if wt.stdout.strip() == PRIOR_TREE else 'DRIFT'}")
        print(f"  objects into isolated dir: {sum(len(f) for _, _, f in os.walk(to))}")
    finally:
        shutil.rmtree(ti, ignore_errors=True)
        shutil.rmtree(to, ignore_errors=True)
    after = sum(len(f) for _, _, f in os.walk(OBJ))
    print(f"  shared .git/objects {before} -> {after}  delta={after - before}")
    print()

    print("=== 6. TARGET FILES: status and blob hashes ===")
    for p in TARGETS:
        full = os.path.join(REPO, p.replace("/", os.sep))
        if not os.path.exists(full):
            print(f"  {p}")
            print(f"      DOES NOT EXIST (would be created)")
            continue
        st = run(["git", "status", "--porcelain", "--", p]).stdout.strip()
        h = run(["git", "hash-object", p]).stdout.strip()
        print(f"  {p}")
        print(f"      status={st or '(clean)'!r}")
        print(f"      size={os.path.getsize(full)}  blob={h}")
        print(f"      mtime={os.path.getmtime(full)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
