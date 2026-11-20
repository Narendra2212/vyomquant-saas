"""A23: the two facts that make the proposed diff plan concrete -- consumers of a rebuild,
and every caller of close(). Read-only."""
import re
import subprocess
from pathlib import Path

out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


out.append("=" * 100)
out.append("A. CONSUMERS THAT WOULD SEE A REBUILT MANAGER")
out.append("=" * 100)
out.append("  1. FastAPI dependency injection:")
g = t(["git", "grep", "-n", "Depends(get_redis_manager)", "--", "backend_app/"])
for l in g.splitlines():
    out.append("    %s" % l[:112])
ex = Path("backend_app/routers/exchange.py").read_text(encoding="utf-8", errors="replace").splitlines()
for i, l in enumerate(ex, 1):
    if "Depends(get_redis_manager)" in l:
        out.append("")
        out.append("    context around exchange.py:%d" % i)
        for j in range(max(0, i - 8), min(i + 4, len(ex))):
            out.append("      %4d  %s" % (j + 1, ex[j].rstrip()[:104]))
out.append("")
out.append("  -> a handler receives the manager object as a per-request dependency, so a rebuild")
out.append("     between requests is picked up; a rebuild DURING a request is not. exchange.py is")
out.append("     also the AUTH-01 / CONN-01 / TENANT-01 file, so the semantics intersect that work.")

out.append("")
out.append("  2. Direct awaiters of get_redis_manager():")
for l in t(["git", "grep", "-n", "await get_redis_manager()", "--",
            "backend_app/", "workers/", "scripts/", "tee/"]).splitlines():
    out.append("    %s" % l[:112])

out.append("")
out.append("  3. The compat proxy and the core shared manager re-resolve each access:")
out.append("     backend/redis_manager.py:580-590   RedisManagerCompatProxy.__getattr__ re-imports")
out.append("     core/cache/redis_manager.py:489-498 _ensure_manager() re-assigns from get_redis_manager()")

out.append("")
out.append("=" * 100)
out.append("B. EVERY CALLER OF RedisManager.close()  -- F3 blast radius")
out.append("=" * 100)
hits = []
for pat in (r"redis_manager\.close\(\)", r"manager\.close\(\)", r"mgr\.close\(\)",
            r"_redis_manager\.close\(\)"):
    for l in t(["git", "grep", "-n", pat, "--", "backend_app/", "workers/", "scripts/",
                "tee/", "tests/"]).splitlines():
        if l.strip():
            hits.append(l)
seen = set()
for l in hits:
    if l in seen:
        continue
    seen.add(l)
    out.append("  %s" % l[:112])
out.append("")
out.append("  total distinct close() call sites: %d" % len(seen))

out.append("")
out.append("=" * 100)
out.append("C. THE EXACT LINES THE PROPOSED PLAN WOULD TOUCH")
out.append("=" * 100)
back = Path("backend_app/backend/redis_manager.py").read_text(encoding="utf-8",
                                                              errors="replace").splitlines()
for lo, hi, label in ((113, 126, "__init__ -- where a loop handle would be recorded"),
                      (133, 185, "initialize() -- F3 close-before-replace, F4 partial cleanup"),
                      (215, 224, "close() -- the supported teardown"),
                      (545, 553, "get_redis_manager() fast path -- loop check insertion point")):
    out.append("")
    out.append("  %s  (L%d-%d)" % (label, lo, hi))
    for i in range(lo - 1, min(hi, len(back))):
        out.append("    %4d  %s" % (i + 1, back[i].rstrip()[:104]))

open("staging/p11b23_consumers.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
