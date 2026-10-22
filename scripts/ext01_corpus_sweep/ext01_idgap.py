"""EXT-01: which finding ids does the corpus name that the ledger does not track?

EXT-01's residual risk is "a distinct finding is buried in an unread file". A finding
carries an ID, and the corpus's own IDs are machine-extractable from all 113 files at
once -- so the question "is there an untracked finding in there" can be answered over the
WHOLE corpus rather than by sampling it.

The prior pass preserved four corpus ids in the ledger (DUP-01, CD-8, RECON-01, W-6). This
asks what else the corpus names.
"""
import collections
import json
import pathlib
import re

# Repo-relative: this file lives at scripts/ext01_corpus_sweep/, so the repository root is
# two parents up. Committed deliberately -- EXT-01's closure rests on these measurements,
# and a claim nobody can re-run is not evidence.
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
STAGING = REPO / "staging"

LEDGER = json.loads(
    (REPO / "docs/launch_readiness/REMEDIATION_LEDGER.json").read_text(encoding="utf-8")
)
ledger_ids = {f["id"].upper() for f in LEDGER["findings"]}

# Also treat any id MENTIONED in ledger text as cross-referenced, since the prior pass
# said it preserved corpus ids by reference rather than by always creating a row.
ledger_text = json.dumps(LEDGER).upper()

ID_RE = re.compile(r"\b(?:[A-Z]{2,8}-\d{1,3}|W-\d{1,3}|U\d{1,3}|BUG-?\d{2,3})\b")

# Patterns that are not finding ids at all.
NOISE = re.compile(
    r"^(?:SHA-\d+|UTF-\d+|ISO-\d+|RFC-\d+|CVE-\d+|GHSA|PYSEC|AAL\d|API-\d+|HTTP-\d+"
    r"|TLS-\d+|AES-\d+|RSA-\d+|P-\d+|S-\d+|X-\d+|PG-\d+|V-\d+|B\d{3}|E\d{3}|W\d{3})$"
)

per_id_files = collections.defaultdict(set)
for path in sorted(STAGING.glob("PHASE11B_*.md")):
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in set(ID_RE.findall(text)):
        if NOISE.match(match):
            continue
        per_id_files[match].add(path.name)

# Also scan the corpus's own top-level report.
report = STAGING / "REMEDIATION_REPORT.md"
if report.is_file():
    text = report.read_text(encoding="utf-8", errors="replace")
    for match in set(ID_RE.findall(text)):
        if not NOISE.match(match):
            per_id_files[match].add(report.name)

print("EXT-01 -- corpus finding ids vs the ledger")
print("=" * 100)
print(f"distinct candidate ids in the corpus: {len(per_id_files)}")
print(f"ids in the ledger: {len(ledger_ids)}")
print()

tracked, mentioned_only, untracked = [], [], []
for fid, files in per_id_files.items():
    if fid in ledger_ids:
        tracked.append(fid)
    elif fid in ledger_text:
        mentioned_only.append((fid, files))
    else:
        untracked.append((fid, files))

print(f"TRACKED as its own ledger row : {len(tracked)}")
print("   " + ", ".join(sorted(tracked)))
print()
print(f"MENTIONED in ledger text only : {len(mentioned_only)}")
for fid, files in sorted(mentioned_only):
    print(f"   {fid:<12} in {len(files)} corpus file(s)")
print()
print(f"NOT IN THE LEDGER AT ALL      : {len(untracked)}")
print("=" * 100)
for fid, files in sorted(untracked, key=lambda kv: (-len(kv[1]), kv[0])):
    print(f"\n  {fid}  -- named in {len(files)} corpus file(s)")
    for name in sorted(files)[:5]:
        print(f"       {name}")
