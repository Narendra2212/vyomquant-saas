"""PHASE 11B-A6 — per-file pass/fail counts for the three D5' files in CI. Read-only."""
import pathlib
import re
from collections import defaultdict

LOG = pathlib.Path(__file__).resolve().parent / "p11b6_unit_log.txt"
_head = LOG.read_bytes()[:4]
_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
lines = [re.sub(r"^.*?\t\S+Z ", "", ln).rstrip()
         for ln in LOG.read_text(encoding=_enc, errors="replace").splitlines()]

FILES = (
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
)

tally = defaultdict(lambda: defaultdict(int))
names = defaultdict(lambda: defaultdict(list))

for ln in lines:
    m = re.match(r"^(tests/\S+?\.py)::(\S+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL)\b",
                 ln.strip())
    if not m:
        continue
    f, test, verdict = m.group(1), m.group(2), m.group(3)
    if f in FILES:
        tally[f][verdict] += 1
        names[f][verdict].append(test)

print("=" * 78)
print("PER-FILE OUTCOMES IN CI (run 38044138718)")
print("=" * 78)
for f in FILES:
    t = tally[f]
    total = sum(t.values())
    print(f"\n{f}")
    print(f"   total {total}   " + "  ".join(f"{k}={v}" for k, v in sorted(t.items())))
    for verdict in ("FAILED", "ERROR", "SKIPPED"):
        if t.get(verdict):
            print(f"   {verdict}:")
            for n in names[f][verdict]:
                mark = "  <-- real-Redis" if "real_redis" in n else ""
                print(f"      {n}{mark}")
