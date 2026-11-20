"""PHASE 11B-A6 — pull the per-test Redis evidence out of the CI job log.

Reads `staging/p11b6_unit_log.txt` (the `Unit & Integration Tests` job log for run
38044138718) and reports, for the nine D5'-gated tests: the outcome, and for each failure
the first actionable assertion/exception line. Also extracts the Redis service health lines
and the overall pytest summary. Read-only.
"""
import pathlib
import re
import sys

LOG = pathlib.Path(__file__).resolve().parent / "p11b6_unit_log.txt"

QUOTA = [
    "test_the_slot_lock_is_mutually_exclusive_against_real_redis",
    "test_the_guard_admits_exactly_the_limit_against_real_redis",
    "test_concurrent_reservations_are_atomic_against_real_redis",
]
WEBHOOK = [
    "test_the_credential_gate_refuses_test_keys",
    "test_a_redelivered_webhook_is_ignored",
    "test_the_lock_is_recorded_in_redis_under_the_documented_key",
    "test_a_different_event_id_is_not_deduplicated",
    "test_an_in_flight_delivery_blocks_a_concurrent_one",
    "test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind",
]

# PowerShell's `>` redirection wrote this log as UTF-16LE with a BOM (verified: the file
# starts ff fe). Reading it as UTF-8 yields NUL-interleaved garbage and every pattern
# misses, which is how a first version of this script reported "0/9 executed" against a log
# that plainly contained all nine results. Sniff the BOM rather than assume.
_head = LOG.read_bytes()[:4]
_enc = "utf-16" if _head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
raw = LOG.read_text(encoding=_enc, errors="replace").splitlines()
# Strip the "step name\ttimestamp " prefix GitHub adds.
lines = [re.sub(r"^.*?\t\S+Z ", "", ln).rstrip() for ln in raw]

print("=" * 78)
print("PHASE 11B-A6 - per-test Redis evidence, run 38044138718")
print("=" * 78)

print("A. REDIS SERVICE / GATE CONFIGURATION")
for ln in lines:
    low = ln.lower()
    if ("redis" in low and any(k in low for k in (
            "initializing containers", "creating container", "starting container",
            "health", "aerora_test_redis_url"))) or "redis:7-alpine" in low:
        print("   " + ln[:150])

print("-" * 78)
print("B. OUTCOMES")


def outcome(name):
    for ln in lines:
        if f"::{name} " in ln and any(k in ln for k in
                                      ("PASSED", "FAILED", "ERROR", "SKIPPED")):
            for k in ("PASSED", "FAILED", "ERROR", "SKIPPED"):
                if k in ln:
                    return k
    return "NOT FOUND"


results = {}
print("   three quota real-Redis tests:")
for n in QUOTA:
    results[n] = outcome(n)
    print(f"      {results[n]:<10} {n}")
print("   six webhook tests:")
for n in WEBHOOK:
    results[n] = outcome(n)
    print(f"      {results[n]:<10} {n}")

executed = sum(1 for v in results.values() if v in ("PASSED", "FAILED"))
skipped = sum(1 for v in results.values() if v == "SKIPPED")
print(f"\n   executed (not skipped): {executed}/9     skipped: {skipped}/9")

print("-" * 78)
print("C. FIRST ACTIONABLE LINE PER FAILURE")
for name, verdict in results.items():
    if verdict not in ("FAILED", "ERROR"):
        continue
    # Find the failure section header in the summary block.
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("_") and name in ln:
            start = i
            break
    if start is None:
        print(f"\n   {name}: section not found")
        continue
    print(f"\n   {name}")
    shown = 0
    for ln in lines[start + 1: start + 120]:
        if ln.startswith("_") and "test_" in ln:
            break
        s = ln.strip()
        if s.startswith("E ") or s.startswith("E\t"):
            print(f"      {s[:300]}")
            shown += 1
            if shown >= 4:
                break

print("-" * 78)
print("D. PYTEST SUMMARY LINE(S)")
for ln in lines:
    if re.search(r"=+ .*(passed|failed).* in .*=+", ln):
        print("   " + ln.strip()[:200])

print("-" * 78)
print("E. OTHER FAILED TESTS IN THIS JOB (unrelated to D5')")
d5 = set(QUOTA) | set(WEBHOOK)
others = []
for ln in lines:
    m = re.match(r"^(FAILED|ERROR) (\S+)", ln.strip())
    if m:
        nodeid = m.group(2)
        tname = nodeid.split("::")[-1].split("[")[0]
        if tname not in d5:
            others.append(f"{m.group(1)} {nodeid}")
for o in sorted(set(others))[:40]:
    print("   " + o[:170])
print(f"\n   total distinct non-D5' failures/errors listed: {len(set(others))}")
sys.exit(0)
