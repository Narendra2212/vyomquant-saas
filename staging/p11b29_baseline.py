"""A29 Phase 0 baseline. Strictly read-only. No credential/DSN/DB/Redis/network access.

Digest serialisations (A25 ss9.1 -- a bare digest is not a portable invariant):
    METHOD A14   : sorted `git ls-files --stage` lines, concatenated, NO separator
    METHOD W1D11 : raw-order lines, LF-joined, no trailing LF
"""
import datetime
import hashlib
import subprocess
from pathlib import Path

A14 = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
W1D11 = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
LEDGER = "staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md"
A28 = "staging/PHASE11B_A28_LEDGER_GATE_DELTA.md"

out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def hdr(t):
    out.append("")
    out.append("=" * 94)
    out.append(t)
    out.append("=" * 94)


hdr("1. REPO STATE")
head, origin = g(["rev-parse", "HEAD"]).strip(), g(["rev-parse", "origin/main"]).strip()
out.append(f"  branch/HEAD  : {g(['rev-parse','--abbrev-ref','HEAD']).strip()} / {head}")
out.append(f"  origin/main  : {origin}   equal={head == origin}")
out.append(f"  ahead/behind : {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")
raw = [l for l in g(["ls-files", "--stage"]).splitlines() if l]
h = hashlib.sha256()
for l in sorted(raw):
    h.update(l.encode())
da, dw = h.hexdigest(), hashlib.sha256("\n".join(raw).encode()).hexdigest()
out.append(f"  digest A14    : {da}  {'SAME' if da == A14 else '>> CHANGED'}")
out.append(f"  digest W1D11  : {dw}  {'SAME' if dw == W1D11 else '>> CHANGED'}")
st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
out.append(f"  staged/unstaged/untracked/unmerged : "
           f"{len([l for l in st if l[0] not in ' ?'])} / "
           f"{len([l for l in st if not l.startswith('??') and len(l)>1 and l[1]!=' '])} / "
           f"{len([l for l in st if l.startswith('??')])} / "
           f"{len([l for l in st if l[:2] in ('DD','AU','UD','UA','DU','AA','UU')])}"
           "   (A28: 22 / 102 / 140 / 0)")
out.append(f"  worktrees/stash : {len([l for l in g(['worktree','list']).splitlines() if l])}"
           f" / {len([l for l in g(['stash','list']).splitlines() if l])}")
out.append(f"  tracked entries : {len([l for l in g(['ls-files']).splitlines() if l])}  (A28: 2104)")

hdr("2. FINGERPRINTS (A27/A28-recorded -> now)")
expect = {
    "scripts/verify_provisioning.py": "8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2",
    "scripts/verify_schema_drift.py": "f853c1482ca295440c78e4f25a8989438fab041e",
    "scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444",
    "tests/test_snapshot_dimension_invariants.py": "5106dd5916424c9b795a5b393e36afc21c4ba9b4",
    "tests/test_drift_classification.py": "fcffbe8e01367aa7f99fffe2677267821fb8424d",
    LEDGER: "7e238620bc22f0ad246f0ebc373b7032c6b28ef0",
    "staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md": "1402bdc5a1b030430d6e192989a851b4a21ce9a8",
}
for p, exp in expect.items():
    if not Path(p).exists():
        out.append(f"  ABSENT  {p}")
        continue
    b = g(["hash-object", p]).strip()
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    out.append(f"  {b}  {xy:<5} {p}  {'SAME' if b == exp else '>> CHANGED'}")

hdr("3. LEDGER LINE 802 (verbatim)")
lines = Path(LEDGER).read_text(encoding="utf-8").splitlines()
out.append(f"  802 | {lines[801]}")
out.append(f"  contains 'NOT GRANTED' : {'NOT GRANTED' in lines[801]}")
out.append(f"  already reconciled     : {'GRANTED (W1D-12)' in lines[801]}")

hdr("4. ARTIFACTS NEWER THAN A28 -- is there a NEW affirmative decision record?")
a28_m = Path(A28).stat().st_mtime
rows = sorted((f.stat().st_mtime, f.stat().st_size, f.name)
              for f in Path("staging").glob("*")
              if f.is_file() and f.stat().st_mtime > a28_m)
if rows:
    for m, s, n in rows:
        out.append(f"  {datetime.datetime.fromtimestamp(m).strftime('%m-%d %H:%M:%S')}  {s:>8} B  {n}")
else:
    out.append("  (none since A28)")

hdr("5. CREDENTIAL HYGIENE (status only -- nothing opened)")
for p in ("exchange_keys.json", ".env", "staging/pg_dsn.txt"):
    ign = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    out.append(f"  {p:<26} exists={Path(p).exists()!s:<6} gitignored={ign}")

text = "\n".join(out) + "\n"
Path("staging/p11b29_baseline.txt").write_text(text, encoding="utf-8")
print(text)
