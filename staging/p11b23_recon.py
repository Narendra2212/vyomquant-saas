"""A23: reconcile with the three concurrent reports, and check pytest.ini's change."""
import re
import subprocess
from pathlib import Path

out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- pytest.ini
out.append("=" * 100)
out.append("A. pytest.ini CHANGE -- does it affect the strict xfail?")
out.append("=" * 100)
d = t(["git", "diff", "HEAD", "--", "pytest.ini"])
for l in d.splitlines():
    if l[:1] in "+-" and not l.startswith(("+++", "---")):
        out.append("  %s" % l.rstrip()[:112])
ini = Path("pytest.ini").read_text(encoding="utf-8", errors="replace")
out.append("")
for key in ("xfail_strict", "asyncio_mode", "addopts", "asyncio_default"):
    hits = [(i, l.strip()) for i, l in enumerate(ini.splitlines(), 1) if key in l]
    out.append("  %-24s %s" % (key, hits or "absent"))

# ---------------------------------------------------------------- concurrent reports
REPORTS = [
    "staging/PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md",
    "staging/PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md",
    "staging/PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md",
]
out.append("")
out.append("=" * 100)
out.append("B. CONCURRENT REPORTS ON THIS TOPIC")
out.append("=" * 100)
for r in REPORTS:
    p = Path(r)
    if not p.exists():
        out.append("  ABSENT %s" % r)
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    out.append("")
    out.append("  %s  (%d B, %d lines)" % (p.name, p.stat().st_size, len(txt.splitlines())))
    out.append("    first 10 lines:")
    for l in txt.splitlines()[:10]:
        out.append("      %s" % l[:112])
    out.append("    approval-state lines:")
    for i, l in enumerate(txt.splitlines(), 1):
        if re.search(r"NOT GRANTED|not granted|no implementation|APPROVAL|approval (is )?(absent|missing)"
                     r"|awaiting", l, re.I):
            out.append("      L%-5d %s" % (i, l.strip()[:106]))
    out.append("    did it modify production source?")
    for i, l in enumerate(txt.splitlines(), 1):
        if re.search(r"(files? changed|no source|not edited|read-only|investigation[- ]only)", l, re.I):
            out.append("      L%-5d %s" % (i, l.strip()[:106]))

# ---------------------------------------------------------------- is the file still clean?
out.append("")
out.append("=" * 100)
out.append("C. HAS ANY SESSION ALREADY EDITED THE TARGET FILE?")
out.append("=" * 100)
st = t(["git", "status", "--porcelain", "--", "backend_app/backend/redis_manager.py"]).rstrip()
out.append("  status            : %r" % st)
out.append("  blob              : %s" % t(["git", "hash-object", "backend_app/backend/redis_manager.py"]).strip())
out.append("  HEAD blob         : %s" % t(["git", "rev-parse", "HEAD:backend_app/backend/redis_manager.py"]).strip())
out.append("  identical to HEAD : %s" % (not st))
for sym in ("_loop", "get_running_loop", "aclose", "_created_on_loop", "_pools"):
    n = len(re.findall(re.escape(sym),
                       Path("backend_app/backend/redis_manager.py").read_text(encoding="utf-8",
                                                                              errors="replace")))
    out.append("    occurrences of %-18s %d" % (sym, n))

open("staging/p11b23_recon.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
