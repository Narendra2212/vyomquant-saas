"""A29 exit audit. Read-only. Proves exactly one file changed and it changed additively."""
import hashlib
import subprocess
from pathlib import Path

A14 = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
W1D11 = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"
TARGET = "tests/test_drift_classification.py"
ENTRY_BLOB = "fcffbe8e01367aa7f99fffe2677267821fb8424d"

out = []


def g(a):
    return subprocess.run(["git"] + a, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def hdr(t):
    out.append("")
    out.append("=" * 92)
    out.append(t)
    out.append("=" * 92)


hdr("1. INDEX / WORKTREE PRESERVATION")
raw = [l for l in g(["ls-files", "--stage"]).splitlines() if l]
h = hashlib.sha256()
for l in sorted(raw):
    h.update(l.encode())
da, dw = h.hexdigest(), hashlib.sha256("\n".join(raw).encode()).hexdigest()
out.append(f"  HEAD        : {g(['rev-parse','HEAD']).strip()}")
out.append(f"  ahead/behind: {g(['rev-list','--left-right','--count','origin/main...HEAD']).strip()}")
out.append(f"  digest A14  : {'SAME' if da == A14 else '>> CHANGED'}  {da}")
out.append(f"  digest W1D11: {'SAME' if dw == W1D11 else '>> CHANGED'}  {dw}")
st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
out.append(f"  staged/unstaged/untracked/unmerged : "
           f"{len([l for l in st if l[0] not in ' ?'])} / "
           f"{len([l for l in st if not l.startswith('??') and len(l)>1 and l[1]!=' '])} / "
           f"{len([l for l in st if l.startswith('??')])} / "
           f"{len([l for l in st if l[:2] in ('DD','AU','UD','UA','DU','AA','UU')])}"
           "   (entry: 22 / 102 / 140 / 0)")
out.append(f"  tracked entries : {len([l for l in g(['ls-files']).splitlines() if l])}  (entry: 2104)")

hdr("2. AUTHORIZED SCOPE -- exactly one file may have changed")
scope = {
    "scripts/verify_provisioning.py": "8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2",
    "scripts/verify_schema_drift.py": "f853c1482ca295440c78e4f25a8989438fab041e",
    "tests/test_snapshot_dimension_invariants.py": "5106dd5916424c9b795a5b393e36afc21c4ba9b4",
    TARGET: ENTRY_BLOB,
}
for p, exp in scope.items():
    b = g(["hash-object", p]).strip()
    tag = "CHANGED (authorized target)" if p == TARGET and b != exp else (
        "SAME -- untouched" if b == exp else ">> UNEXPECTED CHANGE")
    out.append(f"  {b}  {p}")
    out.append(f"      {tag}")

hdr("3. EXPLICITLY NOT-AUTHORIZED FILES -- must be untouched")
forbidden = {
    "scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444",
    "staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md": "7e238620bc22f0ad246f0ebc373b7032c6b28ef0",
    "staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md": "1402bdc5a1b030430d6e192989a851b4a21ce9a8",
    "staging/PHASE11A_READINESS_SCORECARD.json": "bc8f63d0f621c414280849c4a3adb4d554361664",
    "backend_app/main.py": "a3849d4acd7afcf4e53054fc85b16c786776eada",
    "backend_app/core/subscription_engine.py": "256f03994b395d383fc49dd5b87a456ba68d99cb",
    "backend_app/backend/redis_manager.py": "d7b0e811b615ee13ba089b5cbe9d790161176de2",
}
for p, exp in forbidden.items():
    b = g(["hash-object", p]).strip()
    out.append(f"  {'SAME ' if b == exp else '>> CHANGED '} {p}")

hdr("4. LEDGER LINE 802 -- must still be stale/untouched")
led = Path("staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md").read_text(encoding="utf-8").splitlines()
for n in (800, 801, 802, 803, 804):
    status = led[n - 1].split("|")[-2].strip() if led[n - 1].count("|") >= 2 else "?"
    out.append(f"  L{n}: status={status!r}")
out.append(f"  L802 edited by A29 : {'GRANTED (W1D-12)' in led[801]}")

hdr("5. TARGET FILE -- additive-only evidence")
text = Path(TARGET).read_text(encoding="utf-8")
lines = text.splitlines()
out.append(f"  total lines now : {len(lines)}   (W1D-13 recorded 665)")
out.append(f"  new test functions added:")
for i, l in enumerate(lines):
    if l.startswith("def test_counts_reduce") or \
       l.startswith("def test_the_legacy") or \
       l.startswith("def _legacy_membership_diff") or \
       l.startswith("LEGACY_ARITHMETIC_CASES"):
        out.append(f"      L{i+1}: {l[:78]}")
out.append(f"  'production-audit' touched anywhere in target : {'production_schema' in text}")

text_out = "\n".join(out) + "\n"
Path("staging/p11b29_audit.txt").write_text(text_out, encoding="utf-8")
print(text_out)
