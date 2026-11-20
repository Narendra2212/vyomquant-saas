"""A20: the SharedRedisManager proxies (not the mock's), _connected invalidation, and callers."""
import re
import subprocess
from pathlib import Path

CORE = "backend_app/core/cache/redis_manager.py"
BACK = "backend_app/backend/redis_manager.py"
out = []
core = Path(CORE).read_text(encoding="utf-8", errors="replace").splitlines()
back = Path(BACK).read_text(encoding="utf-8", errors="replace").splitlines()


def show(lines, start, n, label):
    out.append("")
    out.append("  %s  (L%d)" % (label, start + 1))
    for i in range(start, min(start + n, len(lines))):
        out.append("    %4d  %s" % (i + 1, lines[i].rstrip()[:112]))


out.append("=" * 100)
out.append("SharedRedisManager PROXIES USED BY reserve_quota  (NOT MockRedisClient's)")
out.append("=" * 100)
cls_at = next(i for i, l in enumerate(core) if l.startswith("class SharedRedisManager"))
out.append("  class SharedRedisManager begins at L%d" % (cls_at + 1))
for name in ("incrby", "decrby", "expire"):
    idxs = [i for i, l in enumerate(core)
            if re.match(r"\s+async def %s\(" % name, l) and i > cls_at]
    for i in idxs:
        show(core, i, 14, "SharedRedisManager.%s" % name)

out.append("")
out.append("=" * 100)
out.append("DOES ANY ERROR PATH INVALIDATE `_connected` ?")
out.append("=" * 100)
out.append("  occurrences of `_connected` in %s:" % BACK)
for i, l in enumerate(back, 1):
    if "_connected" in l:
        out.append("    %4d  %s" % (i, l.rstrip()[:112]))
out.append("")
out.append("  occurrences of `_connected` in %s:" % CORE)
hits = [(i, l) for i, l in enumerate(core, 1) if "_connected" in l]
for i, l in hits:
    out.append("    %4d  %s" % (i, l.rstrip()[:112]))
if not hits:
    out.append("    NONE -- the core proxies never touch the connected flag")

out.append("")
out.append("  get_redis_manager() reconnection gate:")
gi = next(i for i, l in enumerate(back) if "async def get_redis_manager" in l)
show(back, gi, 44, "get_redis_manager")

out.append("")
out.append("  try_reconnect:")
ti = next((i for i, l in enumerate(back) if "async def try_reconnect" in l), None)
if ti is not None:
    show(back, ti, 34, "try_reconnect")

out.append("")
out.append("=" * 100)
out.append("DOES ANY CALLER SURFACE THE REFUSAL USAGE VALUE TO AN API RESPONSE?")
out.append("=" * 100)
for path, ln in (("backend_app/routers/strategies.py", 2077),
                 ("backend_app/backend/strategy_service.py", 1852),
                 ("backend_app/core/usage_ledger.py", 494)):
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    out.append("")
    out.append("  %s around L%d:" % (path, ln))
    for i in range(max(0, ln - 3), min(ln + 22, len(lines))):
        out.append("    %4d  %s" % (i + 1, lines[i].rstrip()[:112]))

open("staging/p11b20_trace2.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
