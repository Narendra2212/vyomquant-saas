"""Does any metered-reservation test exercise the PRODUCTION quota API against real Redis?

Phase 3 asks this explicitly. Read-only source analysis.
"""
import re

PATH = "tests/test_quota01_metered_reservation_is_hard.py"
src = open(PATH, encoding="utf-8", errors="replace").read()
lines = src.splitlines()


def code_only(block: str) -> str:
    """Strip `#` comments and docstring bodies.

    Without this the scan reports a FALSE POSITIVE: the real-Redis test names
    `reserve_quota` only in the comment "Mirror reserve_quota's increment-first-revert
    against the real server", while its executable body calls `client.incrby` directly.
    A provenance question must be answered from code, not from prose about code.
    """
    kept, in_doc = [], False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.count('"""') == 1:
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith(('"""', "#")):
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)

# Production quota entry points vs raw Redis commands.
PROD = ["reserve_quota", "enforce_quota", "check_quota", "release_quota",
        "SubscriptionEngine(", "eng.", "sd.enforce", "sd.reserve"]
RAW = ["client.incrby", "client.decrby", "client.incr", "client.get(",
       "client.set(", "client.delete", "aioredis.from_url"]

# Split into test functions.
tests = []
cur = None
for i, l in enumerate(lines):
    m = re.match(r"\s*(?:async\s+)?def (test_\w+)", l)
    if m:
        if cur:
            cur["end"] = i
            tests.append(cur)
        cur = {"name": m.group(1), "start": i, "end": len(lines)}
if cur:
    tests.append(cur)

out = []
out.append("=" * 78)
out.append("tests/test_quota01_metered_reservation_is_hard.py - API provenance per test")
out.append("=" * 78)
for t in tests:
    body = code_only("\n".join(lines[t["start"]:t["end"]]))
    prod = sorted({p for p in PROD if p in body})
    raw = sorted({r for r in RAW if r in body})
    real = "real_redis" in t["name"] or "require_real_redis()" in body
    out.append("")
    out.append("  %s  (line %d)" % (t["name"], t["start"] + 1))
    out.append("    real-Redis gated : %s" % real)
    out.append("    production API   : %s" % (", ".join(prod) if prod else "NONE"))
    out.append("    raw Redis calls  : %s" % (", ".join(raw) if raw else "none"))

out.append("")
out.append("=" * 78)
out.append("VERDICT")
out.append("=" * 78)
for t in tests:
    body = code_only("\n".join(lines[t["start"]:t["end"]]))
    if "real_redis" in t["name"]:
        prod = [p for p in PROD if p in body]
        raw = [r for r in RAW if r in body]
        out.append("  %s" % t["name"])
        out.append("    exercises production quota API against real Redis: %s"
                   % ("YES" if prod else "NO"))
        out.append("    exercises raw Redis commands only               : %s"
                   % ("YES" if raw and not prod else "no"))

open("staging/p11b13_metered_api.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
