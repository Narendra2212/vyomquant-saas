"""A28 Phase 0/1 gate. Strictly read-only. No credential, DSN, DB, Redis or network access.

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
W1D12 = "staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md"
A27 = "staging/PHASE11B_A27_RECONCILIATION_AND_READINESS_REPORT.md"

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
           "   (A27: 22 / 102 / 140 / 0)")
out.append(f"  worktrees/stash : {len([l for l in g(['worktree','list']).splitlines() if l])}"
           f" / {len([l for l in g(['stash','list']).splitlines() if l])}")

hdr("2. LEDGER / RECORD FINGERPRINTS  (A27-recorded -> now)")
for p, exp in ((LEDGER, "7e238620bc22f0ad246f0ebc373b7032c6b28ef0"),
               (W1D12, None), (A27, None)):
    if not Path(p).exists():
        out.append(f"  ABSENT  {p}")
        continue
    b = g(["hash-object", p]).strip()
    xy = next((l[:2] for l in st if l[3:].strip().strip('"') == p), "clean")
    v = "" if exp is None else ("  SAME -- ledger NOT modified since A27"
                               if b == exp else "  >> CHANGED since A27")
    mt = datetime.datetime.fromtimestamp(Path(p).stat().st_mtime).strftime("%m-%d %H:%M:%S")
    out.append(f"  {b}  {xy:<5} {mt}  {p}{v}")

hdr("3. EXACT CURRENT LEDGER LINES 798-806")
lines = Path(LEDGER).read_text(encoding="utf-8").splitlines()
for n in range(797, 807):
    if 0 <= n < len(lines):
        mark = "  <== LINE 802 (AP-W1D-3)" if n + 1 == 802 else ""
        out.append(f"  {n+1:>4} | {lines[n]}{mark}")
out.append("")
out.append(f"  total lines in ledger: {len(lines)}")
l802 = lines[801] if len(lines) > 801 else ""
out.append(f"  line 802 contains 'AP-W1D-3'   : {'AP-W1D-3' in l802}")
out.append(f"  line 802 contains 'NOT GRANTED': {'NOT GRANTED' in l802}")
out.append(f"  line 802 already reconciled    : {'GRANTED (W1D-12)' in l802}")

hdr("4. RECORDS NEWER THAN A27 (targeted -- is there a NEW authorization artifact?)")
a27_m = Path(A27).stat().st_mtime
rows = sorted((f.stat().st_mtime, f.stat().st_size, f.name)
              for f in Path("staging").glob("*")
              if f.is_file() and f.stat().st_mtime > a27_m)
if rows:
    for m, s, n in rows:
        out.append(f"  {datetime.datetime.fromtimestamp(m).strftime('%m-%d %H:%M:%S')}  {s:>8} B  {n}")
else:
    out.append("  (none -- no artifact of any kind created since the A27 report)")

hdr("5. CREDENTIAL HYGIENE (status only -- nothing opened)")
for p in ("exchange_keys.json", ".env", "staging/pg_dsn.txt"):
    ign = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    out.append(f"  {p:<26} exists={Path(p).exists()!s:<6} "
               f"tracked={bool(g(['ls-files','--error-unmatch',p]).strip())!s:<6} gitignored={ign}")

text = "\n".join(out) + "\n"
Path("staging/p11b28_gate.txt").write_text(text, encoding="utf-8")
print(text)
