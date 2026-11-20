"""PHASE 11B-A6 — first actionable traceback line for each failing D5' test. Read-only."""
import pathlib
import re

LOG = pathlib.Path(__file__).resolve().parent / "p11b6_unit_log.txt"
_head = LOG.read_bytes()[:4]
_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
lines = [re.sub(r"^.*?\t\S+Z ", "", ln).rstrip()
         for ln in LOG.read_text(encoding=_enc, errors="replace").splitlines()]

FAILING = [
    "test_the_slot_lock_is_mutually_exclusive_against_real_redis",
    "test_the_guard_admits_exactly_the_limit_against_real_redis",
    "test_the_lock_is_recorded_in_redis_under_the_documented_key",
    "test_an_in_flight_delivery_blocks_a_concurrent_one",
]

print("=" * 78)
print("FAILURE DETAIL - run 38044138718")
print("=" * 78)

for name in FAILING:
    # The FAILURES block header looks like:  ____ test_name ____
    idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("_") and s.endswith("_") and name in s:
            idx = i
            break
    print("-" * 78)
    print(name)
    if idx is None:
        print("   (no FAILURES section found; showing summary line instead)")
        for ln in lines:
            if name in ln and ("FAILED" in ln or "assert" in ln):
                print("   " + ln.strip()[:240])
                break
        continue
    shown = 0
    for ln in lines[idx + 1: idx + 200]:
        s = ln.strip()
        if s.startswith("_") and s.endswith("_") and "test_" in s:
            break
        if s.startswith("E "):
            print("   " + s[:260])
            shown += 1
            if shown >= 5:
                break
    if shown == 0:
        print("   (no 'E ' lines in the section)")

print("=" * 78)
print("SHORT TEST SUMMARY / TOTALS")
for i, ln in enumerate(lines):
    if "short test summary info" in ln:
        for s in lines[i:i + 8]:
            print("   " + s.strip()[:200])
        break
for ln in lines:
    s = ln.strip()
    if re.match(r"^=+.*\b\d+ (passed|failed)\b.*=+$", s):
        print("   TOTALS: " + s[:200])
