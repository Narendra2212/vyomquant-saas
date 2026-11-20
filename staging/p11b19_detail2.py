"""A19: corrected extraction -- full log lines and the real failure blocks.

Supersedes the 'which assertions passed' inference in p11b19_detail.txt, which was unsound:
pytest echoes the whole source of a failing test in --tb=long, so the presence of an
assertion's message string proves nothing about whether that assertion fired.
"""
import re
from pathlib import Path

D = Path("staging/p11b19_diag")
out = []

# -------------------------------------------------- webhook: full SET-failure lines
out.append("=" * 100)
out.append("WEBHOOK -- 'Cache SET failed' lines, FULL (no truncation)")
out.append("=" * 100)
wh = (D / "webhook.log").read_text(encoding="utf-8", errors="replace").splitlines()
for i, l in enumerate(wh):
    if "Cache SET failed" in l or "Cache GET failed" in l:
        out.append("  L%-4d %s" % (i, l.strip()))

out.append("")
out.append("=" * 100)
out.append("WEBHOOK -- the probe's own failure message, FULL")
out.append("=" * 100)
blk = [i for i, l in enumerate(wh) if "REFUSED a write" in l]
for i in blk[:1]:
    for l in wh[max(0, i - 6):i + 14]:
        out.append("  %s" % l.rstrip())

out.append("")
out.append("  Context: which probe step is this?")
for i, l in enumerate(wh):
    if "wrote = await manager.set" in l or "if not wrote" in l:
        out.append("    L%-4d %s" % (i, l.strip()))

# -------------------------------------------------- metered: the real failure block
out.append("")
out.append("=" * 100)
out.append("METERED -- the failure block, verbatim")
out.append("=" * 100)
mt = (D / "metered.log").read_text(encoding="utf-8", errors="replace").splitlines()
start = next((i for i, l in enumerate(mt)
              if re.search(r"^_+ test_production_reserve_quota", l.strip())), None)
if start is None:
    start = next((i for i, l in enumerate(mt) if "FAILURES" in l), 0)
end = next((i for i in range(start + 1, len(mt))
            if re.search(r"^(=+|_+ test_)", mt[i].strip())), len(mt))
for l in mt[start:min(end, start + 80)]:
    out.append("  %s" % l.rstrip()[:160])

out.append("")
out.append("=" * 100)
out.append("METERED -- which assert line actually raised (from the traceback marker '>')")
out.append("=" * 100)
for i, l in enumerate(mt):
    if l.lstrip().startswith(">") and "assert" in l:
        out.append("  L%-4d %s" % (i, l.strip()[:150]))
    if l.startswith("E ") or l.lstrip().startswith("E   "):
        out.append("  L%-4d %s" % (i, l.strip()[:150]))

out.append("")
out.append("  short test summary:")
for l in mt:
    if l.startswith("FAILED") or l.startswith("ERROR"):
        out.append("    %s" % l.strip()[:160])

open("staging/p11b19_detail2.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
