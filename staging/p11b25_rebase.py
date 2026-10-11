"""A25 step 1: rebaseline, newer-report check, destination checks, concurrent-file map."""
import datetime
import hashlib
import subprocess
from pathlib import Path

A24_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
A24_MTIME = Path("staging/PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md").stat().st_mtime
CONCURRENT = ["backend_app/main.py", "backend_app/routers/exchange.py",
              "backend_app/routers/billing.py", "backend_app/core/global_safety.py",
              "backend_app/core/cache/redis_manager.py",
              "backend_app/core/subscription_engine.py",
              "backend_app/backend/redis_manager.py",
              "backend_app/backend/order_watchdog.py",
              "tests/test_redis_debounce.py"]
DESTS = ["staging/PHASE11B_A25_OWNER_MAP.md",
         "staging/PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md"]
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


out.append("=" * 104)
out.append("1. BASELINE")
out.append("=" * 104)
out.append("  branch: %s   HEAD: %s" % (t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip(),
                                        t(["git", "rev-parse", "HEAD"]).strip()))
out.append("  origin/main: %s" % t(["git", "rev-parse", "origin/main"]).strip())
h = hashlib.sha256()
for l in sorted(t(["git", "ls-files", "--stage"]).splitlines()):
    h.update(l.encode("utf-8"))
out.append("  index content digest: %s  %s" % (h.hexdigest(),
                                               "SAME as A24" if h.hexdigest() == A24_DIGEST else ">> CHANGED"))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
unstaged = [l for l in t(["git", "diff", "--name-only"]).splitlines() if l.strip()]
untracked = [l[3:] for l in t(["git", "status", "--porcelain"]).splitlines() if l.startswith("??")]
out.append("  staged / unstaged / untracked: %d / %d / %d" % (len(staged), len(unstaged), len(untracked)))
out.append("  unmerged: %d" % len([l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]))
Path("staging/p11b25_entry_status.txt").write_text(t(["git", "status", "--porcelain"]), encoding="utf-8")

out.append("")
out.append("=" * 104)
out.append("2. DESTINATIONS")
out.append("=" * 104)
for d in DESTS:
    p = Path(d)
    out.append("  %-62s %s" % (d, "PRESENT -- use a new numbered name" if p.exists() else "ABSENT -- safe"))
for p in sorted(Path("staging").glob("PHASE11B_A25*")):
    out.append("    existing A25 artifact: %-56s %7d B" % (p.name, p.stat().st_size))
if not list(Path("staging").glob("PHASE11B_A25*")):
    out.append("    (no PHASE11B_A25* artifact exists yet)")

out.append("")
out.append("=" * 104)
out.append("3. REPORTS NEWER THAN A24  (A24 mtime %s)" % datetime.datetime.fromtimestamp(A24_MTIME).strftime("%m-%d %H:%M:%S"))
out.append("=" * 104)
newer = [p for p in list(Path("staging").glob("*.md")) + list(Path(".").glob("*.md"))
         if p.stat().st_mtime > A24_MTIME]
if not newer:
    out.append("  none -- A24 is still the newest planning artifact")
for p in sorted(newer, key=lambda x: x.stat().st_mtime):
    out.append("  %-62s %7d B  %s" % (p.name[:62], p.stat().st_size,
                                      datetime.datetime.fromtimestamp(p.stat().st_mtime)
                                      .strftime("%m-%d %H:%M:%S")))

out.append("")
out.append("=" * 104)
out.append("4. CONCURRENT TARGET FILES -- fingerprints")
out.append("=" * 104)
out.append("  %-50s %-5s %-12s %s" % ("path", "XY", "vs HEAD", "worktree blob"))
for p in CONCURRENT:
    st = t(["git", "status", "--porcelain", "--", p]).rstrip()
    num = t(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    delta = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    out.append("  %-50s %-5s %-12s %s" % (p, st[:2] if st else "  ", delta,
                                          t(["git", "hash-object", p]).strip()[:16]))

open("staging/p11b25_rebase.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
