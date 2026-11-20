"""A27 Phase 0 baseline. Strictly read-only: no git write command is issued.

Digest serialisations documented inline (A25 §9.1: a bare digest is not portable).
    METHOD A14   : sorted `git ls-files --stage` lines, concatenated, NO separator
    METHOD W1D11 : raw-order lines, LF-joined, no trailing LF
Credential files are checked by STATUS ONLY. No content is read.
"""
import datetime
import hashlib
import subprocess
from pathlib import Path

A14 = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
W1D11 = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"

out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def hdr(t):
    out.append("")
    out.append("=" * 94)
    out.append(t)
    out.append("=" * 94)


hdr("1. BASELINE")
head, origin = g(["rev-parse", "HEAD"]).strip(), g(["rev-parse", "origin/main"]).strip()
out.append(f"  branch       : {g(['rev-parse', '--abbrev-ref', 'HEAD']).strip()}")
out.append(f"  HEAD         : {head}")
out.append(f"  origin/main  : {origin}   equal={head == origin}")
out.append(f"  ahead/behind : {g(['rev-list', '--left-right', '--count', 'origin/main...HEAD']).strip()}")

raw = [l for l in g(["ls-files", "--stage"]).splitlines() if l]
h = hashlib.sha256()
for l in sorted(raw):
    h.update(l.encode())
da, dw = h.hexdigest(), hashlib.sha256("\n".join(raw).encode()).hexdigest()
out.append(f"  digest A14   : {da}  {'SAME' if da == A14 else '>> CHANGED'}")
out.append(f"  digest W1D11 : {dw}  {'SAME' if dw == W1D11 else '>> CHANGED'}")

st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
out.append(f"  staged       : {len([l for l in st if l[0] not in ' ?'])}   (A26: 22)")
out.append(f"  unstaged     : {len([l for l in st if not l.startswith('??') and len(l) > 1 and l[1] != ' '])}   (A26: 102)")
out.append(f"  untracked    : {len([l for l in st if l.startswith('??')])}   (A26: 140)")
out.append(f"  unmerged     : {len([l for l in st if l[:2] in ('DD','AU','UD','UA','DU','AA','UU')])}   (A26: 0)")
out.append(f"  tracked      : {len([l for l in g(['ls-files']).splitlines() if l])}   (A26: 2104)")
out.append(f"  worktrees    : {len([l for l in g(['worktree','list']).splitlines() if l])}")
out.append(f"  stash        : {len([l for l in g(['stash','list']).splitlines() if l])}")

hdr("2. PHASE-0 FINGERPRINTS  (A26-recorded -> now)")
expect = {
    "scripts/verify_provisioning.py": "8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2",
    "scripts/verify_schema_drift.py": "f853c1482ca295440c78e4f25a8989438fab041e",
    "scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444",
    "tests/test_snapshot_dimension_invariants.py": "5106dd5916424c9b795a5b393e36afc21c4ba9b4",
    "tests/test_drift_classification.py": "fcffbe8e01367aa7f99fffe2677267821fb8424d",
    "staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md": None,
    "staging/PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md": None,
    "staging/PHASE11B_A26_DECISION_SHEET.md": None,
    "backend_app/main.py": "a3849d4acd7afcf4e53054fc85b16c786776eada",
    "backend_app/core/subscription_engine.py": "256f03994b395d383fc49dd5b87a456ba68d99cb",
    "backend_app/backend/order_watchdog.py": "a67092359cb74ad372a9337d3fd523e42f6ba06c",
    "backend_app/backend/exchange_reconciliation.py": "d0603bac8d2acd7f4aa12d93943430e3db6c5591",
    "tests/exchange_sandbox/harness.py": "b95c0cbba07a50cb9069d320e8aa669f5cd5afc5",
    "staging/PHASE11A_READINESS_SCORECARD.json": None,
}
for p, exp in expect.items():
    if not Path(p).exists():
        out.append(f"  ABSENT                                             {p}")
        continue
    b = g(["hash-object", p]).strip()
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    v = "" if exp is None else ("  SAME" if b == exp else "  >> CHANGED")
    out.append(f"  {b}  {xy:<5} {p}{v}")

hdr("3. REPORTS NEWER THAN W1D13 (10-11 13:34)")
cut = datetime.datetime(2026, 10, 11, 13, 34).timestamp()
rows = []
for f in Path("staging").glob("*.md"):
    if f.stat().st_mtime > cut:
        rows.append((f.stat().st_mtime, f.stat().st_size, f.name))
for m, s, n in sorted(rows):
    out.append(f"  {datetime.datetime.fromtimestamp(m).strftime('%m-%d %H:%M:%S')}  {s:>7} B  {n}")
if not rows:
    out.append("  (none)")

hdr("4. TARGETED RECORD EXISTENCE")
for pat in ("*W1D14*", "*W1D1[4-9]*", "*A23E6*", "*GOVERNANCE*", "*GRANT*", "*APPROVAL*"):
    hits = sorted(p.name for p in Path("staging").glob(pat))
    out.append(f"  {pat:<16} -> {len(hits)}")
    for x in hits:
        out.append(f"      {x}")

hdr("5. CREDENTIAL HYGIENE (status only -- NO content read)")
for p in ("exchange_keys.json", ".env", ".env.corrupt.backup", "staging/pg_dsn.txt"):
    ign = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    trk = bool(g(["ls-files", "--error-unmatch", p]).strip())
    out.append(f"  {p:<30} exists={Path(p).exists()!s:<6} tracked={trk!s:<6} gitignored={ign}")

text = "\n".join(out) + "\n"
Path("staging/p11b27_baseline.txt").write_text(text, encoding="utf-8")
print(text)
