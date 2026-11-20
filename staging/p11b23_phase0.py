"""A23 Phase 0 + Phase 1: baseline, source inventory, and the authorization search."""
import hashlib
import re
import subprocess
from pathlib import Path

BACK = "backend_app/backend/redis_manager.py"
CORE = "backend_app/core/cache/redis_manager.py"
DEB = "tests/test_redis_debounce.py"
A22_DIGEST = "7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d"
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


def digest():
    h = hashlib.sha256()
    for l in sorted(t(["git", "ls-files", "--stage"]).splitlines()):
        h.update(l.encode("utf-8"))
    return h.hexdigest()


# ============================================================ baseline
out.append("=" * 100)
out.append("P0A. PRESERVATION BASELINE")
out.append("=" * 100)
out.append("  branch        : %s" % t(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip())
out.append("  HEAD          : %s" % t(["git", "rev-parse", "HEAD"]).strip())
out.append("  origin/main   : %s" % t(["git", "rev-parse", "origin/main"]).strip())
d = digest()
out.append("  index digest  : %s  %s" % (d, "SAME as A22" if d == A22_DIGEST else ">> CHANGED"))
staged = [l for l in t(["git", "diff", "--cached", "--name-only"]).splitlines() if l.strip()]
out.append("  staged paths  : %d" % len(staged))
out.append("  unmerged      : %d" % len([l for l in t(["git", "ls-files", "--unmerged"]).splitlines() if l.strip()]))
Path("staging/p11b23_entry_status.txt").write_text(t(["git", "status", "--porcelain"]), encoding="utf-8")
out.append("  entry status snapshot: staging/p11b23_entry_status.txt")

out.append("")
out.append("  TARGET AND RESTRICTED FILES:")
for p in (BACK, CORE, DEB, "tests/conftest.py", "pytest.ini",
          "backend_app/core/subscription_engine.py"):
    st = t(["git", "status", "--porcelain", "--", p]).rstrip()
    num = t(["git", "diff", "--numstat", "HEAD", "--", p]).split("\t")
    delta = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    out.append("    %-50s %-5s %-11s blob=%s"
               % (p, st[:2] if st else "  ", delta, t(["git", "hash-object", p]).strip()[:12]))

out.append("")
out.append("  destination:")
dest = "staging/PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md"
out.append("    %-66s %s" % (dest, "PRESENT -- do not overwrite" if Path(dest).exists() else "ABSENT -- safe"))
for f in sorted(Path("staging").glob("PHASE11B_A2[23]*")):
    out.append("    existing: %-56s %7d B" % (f.name, f.stat().st_size))

# ============================================================ R6 node ids
out.append("")
out.append("=" * 100)
out.append("P0B. THE R6 TESTS AND THE STRICT-XFAIL NODE ID")
out.append("=" * 100)
deb = Path(DEB).read_text(encoding="utf-8", errors="replace")
for i, l in enumerate(deb.splitlines(), 1):
    if re.match(r"\s*(async )?def test_", l):
        out.append("    L%-5d %s" % (i, l.strip()[:100]))
out.append("")
xf = [(i, l.strip()) for i, l in enumerate(deb.splitlines(), 1)
      if "xfail" in l or "strict=True" in l]
out.append("  xfail markers:")
for i, l in xf:
    out.append("    L%-5d %s" % (i, l[:100]))
out.append("")
out.append("  STRICT-XFAIL NODE ID:")
out.append("    tests/test_redis_debounce.py::test_r6_d_desired_a_runtime_failure_should_be_recoverable")
out.append("  ownership of that test: added by THIS session in A22 (file was tracked+clean at A22 entry);")
out.append("  current status of the file: %s" % (t(["git", "status", "--porcelain", "--", DEB]).rstrip() or "clean"))

# ============================================================ client/pool API
out.append("")
out.append("=" * 100)
out.append("P0C. CLIENT / POOL CREATION SITES AND THE SUPPORTED CLOSE API")
out.append("=" * 100)
back = Path(BACK).read_text(encoding="utf-8", errors="replace").splitlines()
for i, l in enumerate(back, 1):
    if re.search(r"(aioredis\.from_url|await self\._(cache|queue|events)\.close|async def close"
                 r"|\.aclose|connection_pool|max_connections)", l):
        out.append("    L%-5d %s" % (i, l.rstrip()[:108]))

out.append("")
out.append("  installed redis library close/aclose surface:")
probe = t(["python", "-c",
           "import redis, redis.asyncio as a, inspect;"
           "print('redis', redis.__version__);"
           "print('Redis.aclose', hasattr(a.Redis, 'aclose'));"
           "print('Redis.close', hasattr(a.Redis, 'close'));"
           "print('ConnectionPool.aclose', hasattr(a.ConnectionPool, 'aclose'));"
           "print('ConnectionPool.disconnect', hasattr(a.ConnectionPool, 'disconnect'));"
           "print('close sig', str(inspect.signature(a.Redis.close)))"])
for l in probe.splitlines():
    out.append("    %s" % l)

# ============================================================ consumers
out.append("")
out.append("=" * 100)
out.append("P0D. CONSUMERS OF INITIALIZATION / SHUTDOWN")
out.append("=" * 100)
for pat in ("get_redis_manager", r"\.initialize\(\)", r"\.close\(\)", "try_reconnect",
            "RedisManager()"):
    g = t(["git", "grep", "-n", pat, "--", "backend_app/", "workers/", "scripts/", "tee/"])
    lines = [l for l in g.splitlines() if l.strip()]
    out.append("")
    out.append("  %-24s %d hit(s)" % (pat, len(lines)))
    for l in lines[:14]:
        out.append("    %s" % l[:112])

# ============================================================ AUTHORIZATION
out.append("")
out.append("=" * 100)
out.append("P1. AUTHORIZATION SEARCH")
out.append("=" * 100)
GRANT = re.compile(
    r"(approv\w*|authoris\w*|authoriz\w*|sign[- ]?off|granted|go[- ]ahead)", re.I)
TARGETED = re.compile(
    r"(option\s*B|loop[- ]identity|F3|F4|redis_manager\.py)", re.I)
hits = []
for f in sorted(Path("staging").glob("*.md")) + sorted(Path(".").glob("*.md")):
    try:
        txt = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for i, line in enumerate(txt.splitlines(), 1):
        if GRANT.search(line) and TARGETED.search(line):
            hits.append((f.name, i, line.strip()[:132]))
out.append("  lines combining an approval word with an option-B/F3/F4/redis_manager reference:")
if not hits:
    out.append("    >> NONE FOUND")
for n, i, l in hits[:40]:
    out.append("    %-52s L%-5d %s" % (n, i, l))

out.append("")
out.append("  dedicated approval artifacts (by filename):")
appr = [p for p in Path(".").rglob("*")
        if p.is_file() and re.search(r"(APPROV|AUTHORIS|AUTHORIZ|SIGNOFF|SIGN_OFF|GRANT)", p.name, re.I)
        and ".git" not in p.parts and ".venv" not in p.parts and "site-packages" not in str(p)]
for p in appr[:20]:
    out.append("    %s" % p)

out.append("")
out.append("  git trailers / commit messages mentioning approval for this file:")
g = t(["git", "log", "--all", "--format=%h %s", "-20", "--", BACK])
for l in g.splitlines():
    out.append("    %s" % l[:112])
out.append("  (commits touching %s above; empty means it has never been modified on any branch)" % BACK)

open("staging/p11b23_phase0.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
