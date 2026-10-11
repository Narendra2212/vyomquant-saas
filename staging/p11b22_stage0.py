"""A22 Stage 0: entry snapshot, ownership, and discovery of existing Redis-manager tests."""
import hashlib
import json
import subprocess
from pathlib import Path

A20 = {"head": "074b772ab16cece62cbfca0f2b78497be2ae56d5",
       "digest": "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d",
       "staged": 22}
TARGETS = ["backend_app/backend/redis_manager.py",
           "backend_app/core/cache/redis_manager.py",
           "tests/conftest.py"]
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def digest():
    lines = sorted(t(["git", "ls-files", "--stage"]).splitlines())
    h = hashlib.sha256()
    for l in lines:
        h.update(l.encode("utf-8"))
    return h.hexdigest()


out.append("=" * 100)
out.append("STAGE 0A. ENTRY SNAPSHOT")
out.append("=" * 100)
head = t(["git", "rev-parse", "HEAD"]).strip()
out.append("  branch              : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  HEAD                : %s  %s" % (head, "SAME as A20" if head == A20["head"] else ">> CHANGED"))
out.append("  origin/main         : %s" % t(["git", "rev-parse", "origin/main"]).strip())
out.append("  upstream divergence : %s" % (t(["git", "rev-list", "--left-right", "--count",
                                              "HEAD...origin/main"]).strip() or "0 0"))
d = digest()
out.append("  index digest        : %s  %s" % (d, "SAME" if d == A20["digest"] else ">> CHANGED"))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
unstaged = [l for l in t(["git", "diff", "--name-only"]).splitlines() if l.strip()]
untracked = [l[3:] for l in t(["git", "status", "--porcelain"]).splitlines() if l.startswith("??")]
unmerged = [l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]
out.append("  staged / unstaged   : %d / %d  %s"
           % (len(staged), len(unstaged), "staged SAME" if len(staged) == A20["staged"] else ">> CHANGED"))
out.append("  untracked (collapsed): %d" % len(untracked))
out.append("  unmerged            : %d  %s" % (len(unmerged), "OK" if not unmerged else ">> CONFLICTS"))
out.append("")
out.append("  worktrees:")
for l in t(["git", "worktree", "list"]).splitlines():
    out.append("    %s" % l)

Path("staging/p11b22_entry_staged.txt").write_text(
    t(["git", "ls-files", "--stage"]), encoding="utf-8")
Path("staging/p11b22_entry_status.txt").write_text(
    t(["git", "status", "--porcelain"]), encoding="utf-8")
out.append("")
out.append("  entry snapshots written: p11b22_entry_staged.txt, p11b22_entry_status.txt")

out.append("")
out.append("=" * 100)
out.append("STAGE 0B. OWNERSHIP OF TARGET FILES")
out.append("=" * 100)
for p in TARGETS:
    st = t(["git", "status", "--porcelain", "--", p]).rstrip()
    num = t(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    delta = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    blob = t(["git", "hash-object", p]).strip()[:12]
    lines = len(Path(p).read_text(encoding="utf-8", errors="replace").splitlines())
    own = "OTHER WORKSTREAM -- read-only" if st[:2].strip() else "clean (NOT authorization)"
    out.append("  %-50s %-5s %-11s %6d lines  %s  %s"
               % (p, st[:2] if st else "  ", delta, lines, blob, own))

out.append("")
out.append("=" * 100)
out.append("STAGE 0C. DESTINATION")
out.append("=" * 100)
for f in ["staging/PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md"]:
    out.append("  %-62s %s" % (f, "PRESENT -- DO NOT OVERWRITE" if Path(f).exists() else "ABSENT -- safe"))
for f in sorted(Path("staging").glob("PHASE11B_A2[12]*")):
    out.append("  existing A21/A22 report: %s  (%d B)" % (f.name, f.stat().st_size))

out.append("")
out.append("=" * 100)
out.append("STAGE 0D. EXISTING REDIS-MANAGER / RECONNECTION / FAULT-INJECTION TESTS")
out.append("=" * 100)
hits = {}
for pat in ["get_redis_manager", "try_reconnect", "RedisManager(", "_connected",
            "_RECONNECT_COOLDOWN", "_set_test_mode", "_bypass_dev_mode",
            "backend.redis_manager"]:
    g = t(["git", "grep", "-l", pat, "--", "tests/"])
    for f in g.splitlines():
        hits.setdefault(f, []).append(pat)
for f in sorted(hits):
    out.append("  %-62s %s" % (f, ",".join(hits[f])))

out.append("")
out.append("  candidate modules by name:")
for p in sorted(Path("tests").glob("*redis*")):
    out.append("    %-62s %6d lines  %s"
               % (str(p).replace("\\", "/"),
                  len(p.read_text(encoding="utf-8", errors="replace").splitlines()),
                  (t(["git", "status", "--porcelain", "--", str(p).replace("\\", "/")]).rstrip()[:2] or "tracked")))

open("staging/p11b22_stage0.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
