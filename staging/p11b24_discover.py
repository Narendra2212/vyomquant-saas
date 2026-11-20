"""A24 step 1: baseline + discover the launch-blocker reports and extract their verdict lines."""
import hashlib
import re
import subprocess
from pathlib import Path

A23_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- baseline
out.append("=" * 104)
out.append("1. BASELINE")
out.append("=" * 104)
out.append("  branch : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  HEAD   : %s" % t(["git", "rev-parse", "HEAD"]).strip())
out.append("  origin/main: %s" % t(["git", "rev-parse", "origin/main"]).strip())
h = hashlib.sha256()
for l in sorted(t(["git", "ls-files", "--stage"]).splitlines()):
    h.update(l.encode("utf-8"))
out.append("  index content digest: %s  %s" % (h.hexdigest(),
                                               "SAME as A23" if h.hexdigest() == A23_DIGEST else ">> CHANGED"))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
unstaged = [l for l in t(["git", "diff", "--name-only"]).splitlines() if l.strip()]
untracked = [l[3:] for l in t(["git", "status", "--porcelain"]).splitlines() if l.startswith("??")]
out.append("  staged / unstaged / untracked : %d / %d / %d" % (len(staged), len(unstaged), len(untracked)))
out.append("  unmerged : %d" % len([l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]))
Path("staging/p11b24_entry_status.txt").write_text(t(["git", "status", "--porcelain"]), encoding="utf-8")

dest = "staging/PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md"
out.append("")
out.append("  destination %s : %s" % (dest, "PRESENT -- STOP" if Path(dest).exists() else "ABSENT -- safe"))

out.append("")
out.append("  concurrently modified source (unstaged, top 25 by churn):")
rows = []
for l in t(["git", "diff", "--numstat"]).splitlines():
    p = l.split("\t")
    if len(p) == 3 and p[0].isdigit():
        rows.append((int(p[0]) + int(p[1]), p[2]))
for n, p in sorted(rows, reverse=True)[:25]:
    out.append("    %6d  %s" % (n, p))

# ---------------------------------------------------------------- report discovery
out.append("")
out.append("=" * 104)
out.append("2. BLOCKER-RELEVANT REPORTS PRESENT")
out.append("=" * 104)
TOPICS = {
    "REDIS": r"(REDIS|A2[0-3])",
    "TENANT-01": r"TENANT|RLS|tenant",
    "QUOTA-01": r"QUOTA|quota",
    "CD-8 / settlement": r"(CD8|CD_8|CD-8|SETTLE|settlement)",
    "W1C-02 / schema drift": r"(W1C|SCHEMA_DRIFT|SCHEMA|drift)",
    "watchdog": r"WATCHDOG|watchdog",
    "launch gate / readiness": r"(LAUNCH|READINESS|SCORECARD|GATE|BLOCKER|GO_NO_GO)",
    "deploy / rollback / monitor": r"(DEPLOY|ROLLBACK|MONITOR|RUNBOOK|CERTIFICATION)",
}
docs = sorted(list(Path("staging").glob("*.md")) + list(Path(".").glob("*.md")))
out.append("  %d markdown reports in staging/ and repo root" % len(docs))
for topic, pat in TOPICS.items():
    hits = [p for p in docs if re.search(pat, p.name, re.I)]
    out.append("")
    out.append("  %s  (%d)" % (topic, len(hits)))
    for p in sorted(hits, key=lambda x: -x.stat().st_mtime)[:14]:
        import datetime
        out.append("    %-62s %7d B  %s"
                   % (p.name[:62], p.stat().st_size,
                      datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")))

open("staging/p11b24_discover.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
