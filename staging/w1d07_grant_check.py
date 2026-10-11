"""Phase 11B-W1D-07. READ-ONLY. Is the recorded 'AP-W1D-3 NOT GRANTED' superseded anywhere?

One explicit negative was found. Before declaring BLOCKED I check whether any later record
grants it: a decision log, a newer report, a PR comment, a commit message, or any file
outside staging/. A single grant anywhere would change the outcome, so the search is
deliberately wide and the context is printed for judgement rather than summarised.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
SKIP = {
    ".git", "node_modules", ".hypothesis", ".codex_verify_venv", "__pycache__",
    "dist", "build", ".pytest_cache", "releases", "venv", ".venv", "site-packages",
}
EXTS = (".md", ".txt", ".json", ".yml", ".yaml")

GRANTED_RE = re.compile(r"\bGRANTED\b", re.I)
NOT_GRANTED_RE = re.compile(r"\bNOT\s+GRANTED\b", re.I)


def read(p: str) -> str:
    try:
        raw = open(p, "rb").read()
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    print("=== A. EVERY 'GRANTED' OCCURRENCE, repository-wide ===")
    pos, neg = 0, 0
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if not f.endswith(EXTS):
                continue
            p = os.path.join(root, f)
            text = read(p)
            if not GRANTED_RE.search(text):
                continue
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            for i, line in enumerate(text.splitlines(), 1):
                if not GRANTED_RE.search(line):
                    continue
                negated = bool(NOT_GRANTED_RE.search(line))
                if negated:
                    neg += 1
                else:
                    pos += 1
                    print(f"  [AFFIRMATIVE] {rel}:{i}")
                    print(f"      {line.strip()[:160]}")
    print(f"  affirmative 'GRANTED' lines : {pos}")
    print(f"  'NOT GRANTED' lines         : {neg}")
    print()

    print("=== B. THE AP-W1D-3 LEDGER ROW, in context ===")
    p = os.path.join(REPO, "staging", "PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
    lines = read(p).splitlines()
    for i, line in enumerate(lines, 1):
        if "AP-W1D" in line:
            print(f"  {i}: {line.strip()[:170]}")
    print()

    print("=== C. IS THERE A DECISION LOG? ===")
    found = False
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if re.search(r"decision.?log|approval|authoriz", f, re.I) and f.endswith(EXTS):
                rel = os.path.relpath(os.path.join(root, f), REPO).replace("\\", "/")
                print(f"  {rel}")
                found = True
    if not found:
        print("  none found")
    print()

    print("=== D. GIT HISTORY: any commit granting it? ===")
    r = subprocess.run(
        ["git", "log", "--all", "--oneline", "-i", "--grep=W1D-3", "--grep=W1C-02",
         "--grep=drift classification", "-20"],
        cwd=REPO, capture_output=True, text=True, errors="replace", timeout=120,
    )
    out = r.stdout.strip()
    print(f"  exit={r.returncode}")
    print(f"  {out if out else 'no matching commit'}")
    print()

    print("=== E. VERDICT ===")
    if pos == 0:
        print("  No affirmative grant exists anywhere in the repository.")
        print("  AP-W1D-3 is recorded as NOT GRANTED and is not superseded.")
        print("  => The brief's stop condition is MET. Do not edit.")
    else:
        print(f"  {pos} affirmative 'GRANTED' line(s) found -- inspect them above before deciding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
