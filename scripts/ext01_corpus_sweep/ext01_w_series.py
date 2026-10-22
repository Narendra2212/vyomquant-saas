"""Pull the fullest description of W-3 / W-4 / W-5 out of the corpus."""
import pathlib
import re

# Repo-relative: this file lives at scripts/ext01_corpus_sweep/, so the repository root is
# two parents up. Committed deliberately -- EXT-01's closure rests on these measurements,
# and a claim nobody can re-run is not evidence.
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
STAGING = REPO / "staging"

# The two files that discuss the W-series most definitionally.
CANDIDATES = [
    "PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md",
    "PHASE11B_D10_DECISION_INTAKE_AND_REMEDIATION_READINESS.md",
    "PHASE11B_D5_WATCHDOG_REPAIR_REPORT.md",
    "PHASE11B_D4_DUP02_RECOVERY_READINESS.md",
]

for name in CANDIDATES:
    path = STAGING / name
    if not path.is_file():
        continue
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [i for i, l in enumerate(lines) if re.search(r"\bW-[345]\b", l)]
    if not hits:
        continue
    print("=" * 100)
    print(name)
    print("=" * 100)
    shown = set()
    for i in hits:
        lo, hi = max(0, i - 3), min(len(lines), i + 9)
        block = range(lo, hi)
        if any(b in shown for b in block):
            continue
        shown.update(block)
        print(f"\n--- around line {i + 1} ---")
        for j in block:
            s = lines[j].rstrip()
            if s:
                print("   " + s[:200])
        if len(shown) > 90:
            break
    print()
