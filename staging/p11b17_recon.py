"""Which p11b14 artifact vanished, and what does the concurrent A17 already conclude?"""
import re
from pathlib import Path

A16_LIST = [
    "p11b14_phase0.py", "p11b14_baseline.py", "p11b14_baseline.txt",
    "p11b14_index_delta.py", "p11b14_index_delta.txt",
    "p11b14_index_content_digest.py", "p11b14_index_digest.txt",
    "p11b14_files.py", "p11b14_files.txt",
    "p11b14_metered_delta.py", "p11b14_metered_delta.txt",
    "p11b14_blocking_lane.py", "p11b14_blocking_lane.txt",
    "p11b14_preexisting.py", "p11b14_preexisting.txt",
    "p11b14_local.txt", "p11b14_pr6_unit.log",
]
out = []
out.append("=" * 96)
out.append("A. p11b14_* INVENTORY vs A16")
out.append("=" * 96)
present = {p.name for p in Path("staging").glob("p11b14_*")}
for n in A16_LIST:
    p = Path("staging") / n
    out.append("  %-9s %-40s %s" % ("PRESENT" if p.exists() else ">> GONE", n,
                                    ("%d bytes" % p.stat().st_size) if p.exists() else ""))
extra = sorted(present - set(A16_LIST))
out.append("  unexpected additions: %s" % (extra or "none"))

out.append("")
out.append("  KEY EVIDENCE FILES -- still intact?")
for n, what in [("p11b14_preexisting.txt", "39-failure baseline + per-file node counts"),
                ("p11b14_blocking_lane.txt", "23 introduced / 39 pre-existing split"),
                ("p11b14_index_digest.txt", "content-digest invariant"),
                ("p11b14_pr6_unit.log", "PR #6 blocking-lane log")]:
    p = Path("staging") / n
    out.append("    %-9s %-28s %s" % ("PRESENT" if p.exists() else ">> GONE", n, what))

out.append("")
out.append("=" * 96)
out.append("B. CONCURRENT A17 REPORT -- what it already concludes")
out.append("=" * 96)
p = Path("staging/PHASE11B_A17_D5_READINESS__kiro_session.md")
t = p.read_text(encoding="utf-8", errors="replace")
out.append("  %s  %d bytes, %d lines" % (p.name, p.stat().st_size, len(t.splitlines())))
out.append("")
out.append("  --- first 30 lines ---")
for l in t.splitlines()[:30]:
    out.append("  " + l[:116])

out.append("")
out.append("  --- approval-status signals ---")
for label, pat in [
    ("claims any approval granted", r"\bapproved\b(?!\s*\?)"),
    ("states gate still closed", r"no approval|not approved|BLOCKED|awaiting"),
    ("names canonical PR", r"canonical[^\n]{0,60}#\s*[678]"),
    ("addresses C / slot guard owner", r"slot[- ]guard|store_keys"),
    ("records a decision matrix", r"decision matrix|approval matrix"),
    ("verified the patch", r"rc=0|applies cleanly|APPLIES"),
]:
    hits = [(t[:m.start()].count("\n") + 1,
             t.splitlines()[t[:m.start()].count("\n")].strip()[:104])
            for m in re.finditer(pat, t, re.I)]
    out.append("")
    out.append("    %-34s %d hit(s)" % (label, len(hits)))
    for ln, s in hits[:5]:
        out.append("        L%-5d %s" % (ln, s))

text = "\n".join(out)
open("staging/p11b17_recon.txt", "w", encoding="utf-8").write(text)
print("written")
