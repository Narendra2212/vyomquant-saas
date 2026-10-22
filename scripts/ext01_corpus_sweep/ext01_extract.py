"""EXT-01: extract what the corpus actually CLAIMS about the untracked finding ids."""
import collections
import pathlib
import re

# Repo-relative: this file lives at scripts/ext01_corpus_sweep/, so the repository root is
# two parents up. Committed deliberately -- EXT-01's closure rests on these measurements,
# and a claim nobody can re-run is not evidence.
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
STAGING = REPO / "staging"

TARGETS = ["CONN-01", "W-1", "W-3", "W-4", "W-5", "W-6",
           "CD-1", "CD-2", "CD-3", "CD-4", "CD-5", "CD-6", "CD-7", "UR-1"]

files = sorted(STAGING.glob("PHASE11B_*.md")) + [STAGING / "REMEDIATION_REPORT.md"]

best = collections.defaultdict(list)
for path in files:
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for fid in TARGETS:
            if re.search(rf"\b{re.escape(fid)}\b", line):
                # Prefer lines that look DEFINITIONAL: a colon, a dash, or a verdict word.
                score = 0
                low = line.lower()
                if re.search(rf"\b{re.escape(fid)}\b\s*[:\u2014\-]", line):
                    score += 3
                if any(w in low for w in ("is ", "was ", "does ", "fails", "missing",
                                          "not ", "returns", "raises", "misclassif")):
                    score += 2
                if len(line.strip()) > 60:
                    score += 1
                if score >= 4:
                    best[fid].append((score, path.name, line.strip()[:400]))

print("EXT-01 -- what the corpus claims about each untracked id")
print("=" * 104)
for fid in TARGETS:
    entries = sorted(best.get(fid, []), key=lambda e: -e[0])
    print(f"\n{'=' * 104}")
    print(f"{fid}   ({len(entries)} definitional line(s) found)")
    print("=" * 104)
    seen = set()
    shown = 0
    for score, name, line in entries:
        key = line[:120]
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{name}]")
        print(f"    {line}")
        shown += 1
        if shown >= 3:
            break
    if not entries:
        print("  (no definitional line matched)")
