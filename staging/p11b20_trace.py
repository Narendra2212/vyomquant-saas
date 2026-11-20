"""A20 Phase 1+2: trace the quota proxy semantics and the async Redis lifecycle. Read-only."""
import re
import subprocess
from pathlib import Path

CORE = "backend_app/core/cache/redis_manager.py"
BACK = "backend_app/backend/redis_manager.py"
SE = "backend_app/core/subscription_engine.py"
CONF = "tests/conftest.py"
out = []


def t(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


core = Path(CORE).read_text(encoding="utf-8", errors="replace")
back = Path(BACK).read_text(encoding="utf-8", errors="replace")
se = Path(SE).read_text(encoding="utf-8", errors="replace")
conf = Path(CONF).read_text(encoding="utf-8", errors="replace")


def block(src, name, limit=30):
    m = re.search(r"\n(    (?:async )?def %s\(.*?)(?=\n    (?:async )?def |\nclass |\Z)"
                  % re.escape(name), src, re.S)
    if not m:
        return ["    (not found: %s)" % name]
    return m.group(1).splitlines()[:limit]


# ---------------------------------------------------- 1. quota proxies
out.append("=" * 100)
out.append("P1. THE PROXIES reserve_quota DEPENDS ON  (%s)" % CORE)
out.append("=" * 100)
for name in ("incrby", "decrby", "expire", "get", "set"):
    out.append("")
    for l in block(core, name, 16):
        out.append("  %s" % l.rstrip()[:118])

# ---------------------------------------------------- 2. eval surface
out.append("")
out.append("=" * 100)
out.append("P1b. IS AN ATOMIC SCRIPT PATH AVAILABLE?")
out.append("=" * 100)
for pat in (r"async def eval_lua", r"async def eval\b", r"def eval_lua", r"def eval\b",
            r"register_script", r"hasattr\(client, 'eval'\)"):
    hits = [(i + 1, l.strip()[:100]) for i, l in enumerate(core.splitlines()) if re.search(pat, l)]
    for ln, l in hits:
        out.append("  %-22s L%-5d %s" % (pat[:22], ln, l))
out.append("")
out.append("  MockRedisClient eval support:")
mm = re.search(r"class MockRedisClient.*?(?=\nclass )", core, re.S)
if mm:
    for i, l in enumerate(mm.group(0).splitlines()):
        if re.search(r"def (eval|eval_lua|incrby|decrby)\b", l):
            out.append("    %s" % l.strip()[:110])

# ---------------------------------------------------- 3. callers of reserve_quota
out.append("")
out.append("=" * 100)
out.append("P1c. CALLERS OF reserve_quota AND WHAT THEY DO WITH THE USAGE VALUE")
out.append("=" * 100)
g = t(["git", "grep", "-n", "reserve_quota", "--", "backend_app/", "tests/"])
for l in g.splitlines():
    out.append("  %s" % l[:140])

# ---------------------------------------------------- 4. async lifecycle
out.append("")
out.append("=" * 100)
out.append("P2a. CONNECTION / POOL CREATION AND LIFETIME  (%s)" % BACK)
out.append("=" * 100)
for i, l in enumerate(back.splitlines(), 1):
    if re.search(r"(aioredis\.from_url|_instance|__new__|_initialized|async def initialize"
                 r"|async def close|aclose|disconnect|_connected|max_connections"
                 r"|def get_redis_manager|_manager\b)", l):
        out.append("  L%-5d %s" % (i, l.rstrip()[:116]))

out.append("")
out.append("=" * 100)
out.append("P2b. HOW THE CORE MANAGER ACQUIRES AND CACHES IT")
out.append("=" * 100)
for l in block(core, "_ensure_manager", 34):
    out.append("  %s" % l.rstrip()[:118])
out.append("")
out.append("  seams and singleton:")
for i, l in enumerate(core.splitlines(), 1):
    if re.search(r"(_instance|__new__|_test_mode|_dev_mode_bypass|_redis_manager\s*=|def _bypass_dev_mode"
                 r"|def _set_test_mode|class SharedRedisManager|^redis_manager\s*=)", l):
        out.append("    L%-5d %s" % (i, l.rstrip()[:112]))

# ---------------------------------------------------- 5. fixtures / loop scope
out.append("")
out.append("=" * 100)
out.append("P2c. TEST FIXTURE LIFECYCLE  (%s, owned by another workstream)" % CONF)
out.append("=" * 100)
out.append("  pytest-asyncio configuration:")
for f in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
    p = Path(f)
    if not p.exists():
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    for i, l in enumerate(txt.splitlines(), 1):
        if re.search(r"asyncio", l):
            out.append("    %s:%d  %s" % (f, i, l.strip()[:100]))
out.append("    (absence of asyncio_mode means pytest-asyncio's default applies: strict,")
out.append("     function-scoped event loop per test)")

out.append("")
out.append("  autouse fixtures and loop/redis-related fixtures in conftest:")
for i, l in enumerate(conf.splitlines(), 1):
    if re.search(r"(@pytest\.fixture|@pytest_asyncio\.fixture|^def |^async def |autouse|scope="
                 r"|event_loop|_REDIS_SINGLETON_SEAMS|new_event_loop|set_event_loop)", l):
        out.append("    L%-5d %s" % (i, l.rstrip()[:112]))

out.append("")
out.append("  does anything close or reset the async pool between tests?")
for pat in ("aclose", "close()", "disconnect", "_cache = None", "_connected = False",
            "reset", "initialize("):
    hits = [i + 1 for i, l in enumerate(conf.splitlines()) if pat in l]
    out.append("    %-18s in conftest: %s" % (pat, hits or "NO"))

open("staging/p11b20_trace.txt", "w", encoding="utf-8").write("\n".join(out))
print("written %d lines" % len(out))
