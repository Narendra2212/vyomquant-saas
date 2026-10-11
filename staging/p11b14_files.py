"""Phase 0 file inspection for A14. Read-only."""
import hashlib
import re
import subprocess

FILES = [
    ".github/workflows/01-pr-check.yml",
    "tests/real_redis_gate.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "backend_app/backend/redis_manager.py",
    "backend_app/core/subscription_engine.py",
    "backend_app/core/subscription_dependencies.py",
    "backend_app/routers/exchange.py",
]

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout.decode("utf-8", errors="replace")


out.append("=" * 90)
out.append("CURRENT STATE OF THE NINE FILES")
out.append("=" * 90)
out.append("%-54s %-5s %7s %8s  %s" % ("path", "XY", "lines", "blob", "vs HEAD"))
for f in FILES:
    st = sh(["git", "status", "--porcelain", "--", f]).rstrip()
    xy = st[:2] if st else "  "
    try:
        text = open(f, encoding="utf-8", errors="replace").read()
        n = len(text.splitlines())
    except OSError:
        n = -1
    blob = sh(["git", "hash-object", f]).strip()[:8]
    num = sh(["git", "diff", "--numstat", "HEAD", "--", f]).split("\t")
    delta = ("+%s/-%s" % (num[0], num[1])) if len(num) >= 2 else "clean"
    out.append("%-54s %-5s %7d %8s  %s" % (f, xy, n, blob, delta))

# --- workflow specifics ---
out.append("")
out.append("=" * 90)
out.append(".github/workflows/01-pr-check.yml  -- specifics")
out.append("=" * 90)
wf = open(".github/workflows/01-pr-check.yml", encoding="utf-8", errors="replace").read()
out.append("  hunks vs origin/main:")
for l in sh(["git", "diff", "origin/main", "--", ".github/workflows/01-pr-check.yml"]).splitlines():
    if l.startswith("@@"):
        out.append("    " + l)
out.append("  numstat vs origin/main: %s"
           % sh(["git", "diff", "--numstat", "origin/main", "--",
                 ".github/workflows/01-pr-check.yml"]).strip())
out.append("  Redis env lines:")
for i, l in enumerate(wf.splitlines(), 1):
    if re.search(r"^\s*(REDIS_URL|AERORA_TEST_REDIS_URL):", l):
        out.append("    %4d: %s" % (i, l.strip()))
out.append("  job keys present:")
for key in ["unit-tests:", "d5-redis-diagnostics:", "provisioning-and-isolation:",
            "database-tests:", "execution-tests:"]:
    out.append("    %-30s %s" % (key, "yes" if re.search(r"^\s{2}" + re.escape(key), wf, re.M) else "NO"))

# --- production quota API, for the Phase 3 test design ---
out.append("")
out.append("=" * 90)
out.append("backend_app/core/subscription_engine.py -- production quota API")
out.append("=" * 90)
se = open("backend_app/core/subscription_engine.py", encoding="utf-8", errors="replace").read()
for i, l in enumerate(se.splitlines(), 1):
    if re.search(r"(async )?def (reserve_quota|release_quota|get_quota_usage|_meter_key|enforce_quota)\b", l) \
            or re.search(r"_METER_TTL_SECONDS\s*=", l):
        out.append("  %5d: %s" % (i, l.strip()[:120]))

out.append("")
out.append("  reserve_quota body (Redis primitives it uses):")
m = re.search(r"async def reserve_quota.*?(?=\n    (?:async )?def |\nclass )", se, re.S)
if m:
    body = m.group(0)
    for prim in ["incrby", "decrby", "expire", "setex", "get(", "delete", "redis_manager",
                 "_meter_key", "_METER_TTL"]:
        if prim in body:
            out.append("    uses %s" % prim)
    out.append("    body lines: %d" % len(body.splitlines()))

text = "\n".join(out)
open("staging/p11b14_files.txt", "w", encoding="utf-8").write(text)
print(text)
