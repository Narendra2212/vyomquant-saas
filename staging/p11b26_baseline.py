"""A26 entry baseline. Strictly read-only: no git write command is issued.

Digest methods are documented inline, because A25 §9.1 established that a bare
digest value is not a portable invariant.
    METHOD A14   : sorted `git ls-files --stage` lines, concatenated, NO separator
    METHOD W1D11 : raw-order lines, LF-joined, no trailing LF
"""
import hashlib
import os
import subprocess
from pathlib import Path

A14 = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
W1D11 = "98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704"

out = []


def g(args):
    return subprocess.run(
        ["git"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def hdr(t):
    out.append("")
    out.append("=" * 92)
    out.append(t)
    out.append("=" * 92)


hdr("1. BASELINE")
head = g(["rev-parse", "HEAD"]).strip()
origin = g(["rev-parse", "origin/main"]).strip()
out.append(f"  branch        : {g(['rev-parse', '--abbrev-ref', 'HEAD']).strip()}")
out.append(f"  HEAD          : {head}")
out.append(f"  origin/main   : {origin}")
out.append(f"  HEAD==origin  : {head == origin}")
out.append(f"  ahead/behind  : {g(['rev-list', '--left-right', '--count', 'origin/main...HEAD']).strip()}")

raw = [l for l in g(["ls-files", "--stage"]).splitlines() if l]
h = hashlib.sha256()
for l in sorted(raw):
    h.update(l.encode("utf-8"))
d_a14 = h.hexdigest()
d_w1 = hashlib.sha256("\n".join(raw).encode("utf-8")).hexdigest()
out.append(f"  digest A14    : {d_a14}  {'SAME as A25' if d_a14 == A14 else '>> CHANGED'}")
out.append(f"  digest W1D11  : {d_w1}  {'SAME as A25' if d_w1 == W1D11 else '>> CHANGED'}")

st = [l for l in g(["status", "--porcelain"]).splitlines() if l]
staged = [l for l in st if l[0] not in " ?"]
unstaged = [l for l in st if not l.startswith("??") and len(l) > 1 and l[1] != " "]
untracked = [l for l in st if l.startswith("??")]
unmerged = [l for l in st if l[:2] in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")]
out.append(f"  staged        : {len(staged)}   (A25: 22)")
out.append(f"  unstaged      : {len(unstaged)}   (A25: 102)")
out.append(f"  untracked     : {len(untracked)}   (A25: 139)")
out.append(f"  unmerged      : {len(unmerged)}   (A25: 0)")
out.append(f"  tracked       : {len([l for l in g(['ls-files']).splitlines() if l])}   (A25: 2104)")
out.append(f"  worktrees     : {len([l for l in g(['worktree', 'list']).splitlines() if l])}")
out.append(f"  stash         : {len([l for l in g(['stash', 'list']).splitlines() if l])}")

hdr("2. A25 DELIVERABLES")
for p in ("staging/PHASE11B_A25_OWNER_MAP.md",
          "staging/PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md"):
    f = Path(p)
    if f.exists():
        import datetime
        m = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M:%S")
        out.append(f"  PRESENT  {f.stat().st_size:>7} B  {m}  {p}")
    else:
        out.append(f"  ABSENT                             {p}")

hdr("3. FINGERPRINTS vs A25-recorded")
expect = {
    "backend_app/main.py": "a3849d4acd7afcf4e53054fc85b16c786776eada",
    "backend_app/core/subscription_engine.py": "256f03994b395d383fc49dd5b87a456ba68d99cb",
    "backend_app/backend/redis_manager.py": "d7b0e811b615ee13ba089b5cbe9d790161176de2",
    "backend_app/core/cache/redis_manager.py": None,
    "backend_app/backend/exchange_reconciliation.py": None,
    "backend_app/backend/order_watchdog.py": "a67092359cb74ad3" + "?",
    "tests/exchange_sandbox/harness.py": None,
    "tests/test_p10d_exchange_reconciliation_fails_safely.py": None,
    "tests/test_exchange_reconciliation_startup_assessment.py": None,
    "tests/test_redis_debounce.py": "5a8db7a8afbd57a0dfde35c303b49fb233c3fffc",
    "scripts/verify_provisioning.py": "51cb6d241de3ef437b662241379f8985f7d5a51e",
    "scripts/verify_schema_drift.py": "f78b35a52552967d469a7cc7d33de92db0a65e3b",
    "scripts/verify_production_schema.py": "6e194f43a832d1f9cb0f99efd88dd204c5b3c444",
    "tests/test_snapshot_dimension_invariants.py": "bcb4f479fca2365cca2be61aae8961449e03beb9",
}
out.append(f"  {'path':<62} {'XY':<4} blob")
for p, exp in expect.items():
    if not Path(p).exists():
        out.append(f"  {p:<62} {'--':<4} ABSENT")
        continue
    b = g(["hash-object", p]).strip()
    xy = ""
    for l in st:
        if l[3:].strip().strip('"') == p:
            xy = l[:2]
    verdict = ""
    if exp and not exp.endswith("?"):
        verdict = "  SAME" if b == exp else "  >> CHANGED"
    out.append(f"  {p:<62} {xy or 'clean':<4} {b}{verdict}")

hdr("4. CREDENTIAL HYGIENE (names/status only -- NO content read)")
for p in ("exchange_keys.json", ".env", ".env.corrupt.backup"):
    f = Path(p)
    tracked = bool(g(["ls-files", "--error-unmatch", p]).strip())
    ignored = subprocess.run(["git", "check-ignore", "-q", p]).returncode == 0
    out.append(f"  {p:<28} exists={f.exists()!s:<6} tracked={tracked!s:<6} gitignored={ignored}")

hdr("5. SANDBOX ENV VARS PRESENT IN THIS SHELL (names only)")
names = [k for k in os.environ if "SANDBOX" in k.upper() or k.upper().startswith("AERORA_")]
out.append(f"  AERORA_*/SANDBOX vars set: {len(names)}")
for n in sorted(names):
    out.append(f"    {n} = <set, value not read>")
if not names:
    out.append("    (none -- sandbox suite would skip with MISSING EVIDENCE)")

text = "\n".join(out) + "\n"
Path("staging/p11b26_baseline.txt").write_text(text, encoding="utf-8")
print(text)
