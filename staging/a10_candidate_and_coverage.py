"""Phase 11B-A10. READ-ONLY. Verify A15's prepared blobs and re-check the 9 coverage items.

A15 records three prepared blob ids. Two are plain worktree files and can be hashed
directly; the workflow candidate is a *derived* artifact (PR #6's file + the worktree comment
block) so it is checked structurally instead of by hash.

Then Phase 1's nine coverage requirements, re-verified against the file as it stands now
rather than trusted from the A9 report.
"""

from __future__ import annotations

import os
import re
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"

EXPECTED = {
    "tests/test_webhook_duplicate_delivery_real_redis.py": "3ffbccde68c3b57485a9971d4977c34aff598b47",
    "tests/test_quota01_metered_reservation_is_hard.py": "7baff0b64f0c7441f4192a9a6b2df53e7a68dce9",
    "tests/real_redis_gate.py": "d40a1bb7",
    "tests/test_quota01_exchange_slot_is_atomic.py": "2d7ccbbb",
}

# (requirement, regexes that must ALL appear somewhere in the file)
COVERAGE = [
    ("1 real-Redis opt-in + fail-closed", [r"@real_redis_required", r"require_real_redis\(\)"]),
    ("2 rejects mock / unavailable client", [r"_app_cache_or_fail", r"MockRedisClient", r"pytest\.fail"]),
    ("3 bidirectional keyspace proof", [r"_prove_one_keyspace", r"KEYSPACE SPLIT", r"KEYSPACE SPLIT \(reverse\)"]),
    ("4 concurrent via production reserve_quota", [r"asyncio\.gather", r"SubscriptionEngine\.reserve_quota"]),
    ("5 exact-limit admission + refusal counts", [r"len\(allowed\) == limit", r"len\(refused\) == racers - limit"]),
    ("6 get_quota_usage agrees with raw key", [r"get_quota_usage", r"via_key = int\(await client\.get\(key\)\)", r"via_api == limit", r"via_key == limit"]),
    ("7 correct usage after refusals", [r"0 <= usage <= limit", r"usage == limit"]),
    ("8 zero allowance, no meter key", [r"limit=0", r"client\.exists\(key\) == 0"]),
    ("9 cleanup limited to test-owned keys", [r"_meter_key\(user, resource, period\)", r"client\.delete\(key\)", r"aclose\(\)"]),
]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    print("=== A15's PREPARED BLOBS, re-hashed from the current worktree ===")
    for path, expected in EXPECTED.items():
        r = run(["git", "hash-object", path])
        actual = r.stdout.strip()
        short = actual[: len(expected)]
        verdict = "MATCH" if short == expected else "DRIFTED"
        print(f"  {path}")
        print(f"      expected {expected}   actual {actual}   {verdict}")
    print()

    print("=== WORKFLOW CANDIDATE: structural check (derived artifact, not a worktree file) ===")
    wf_disk = open(REPO + r"\.github\workflows\01-pr-check.yml", encoding="utf-8", errors="replace").read()
    r = subprocess.run(["git", "cat-file", "blob", f"{PR6}:.github/workflows/01-pr-check.yml"], cwd=REPO, capture_output=True)
    wf_pr6 = r.stdout.decode("utf-8", errors="replace")
    pat = re.compile(r"AERORA_TEST_REDIS_URL\s*:\s*(\S+)")
    print(f"  PR #6     lines={wf_pr6.count(chr(10))}  AERORA_TEST_REDIS_URL={[m.group(1) for m in pat.finditer(wf_pr6)]}")
    print(f"  worktree  lines={wf_disk.count(chr(10))}  AERORA_TEST_REDIS_URL={[m.group(1) for m in pat.finditer(wf_disk)]}")
    print(f"  PR #6 has d5-redis-diagnostics         : {'d5-redis-diagnostics' in wf_pr6}")
    print(f"  worktree has d5-redis-diagnostics      : {'d5-redis-diagnostics' in wf_disk}")
    print(f"  PR #6 has provisioning-and-isolation   : {'provisioning-and-isolation' in wf_pr6}")
    print(f"  worktree has provisioning-and-isolation: {'provisioning-and-isolation' in wf_disk}")
    print(f"  env lines still on /15 in PR #6        : {sum(1 for m in pat.finditer(wf_pr6) if '/15' in m.group(1))}")
    print(f"  env lines still on /15 in worktree     : {sum(1 for m in pat.finditer(wf_disk) if '/15' in m.group(1))}")
    print("  => the candidate must take PR #6's job set and rewrite BOTH /15 occurrences.")
    print("     The worktree workflow is NOT the candidate: it lacks the diagnostic job and")
    print("     carries another workstream's provisioning-and-isolation job.")
    print()

    print("=== PHASE 1 COVERAGE, re-verified against the current file ===")
    path = REPO + r"\tests\test_quota01_metered_reservation_is_hard.py"
    text = open(path, encoding="utf-8", errors="replace").read()
    print(f"  file: {os.path.getsize(path)} B, {text.count(chr(10))} lines")
    tests = re.findall(r"^async def (test_\w+)", text, re.M)
    print(f"  tests defined: {len(tests)}")
    for t in tests:
        tag = "PROD-PATH" if t.startswith("test_production_") else ""
        print(f"      {t[:88]:<90} {tag}")
    print()
    gaps = []
    for label, needles in COVERAGE:
        missing = [n for n in needles if not re.search(n, text)]
        ok = not missing
        if not ok:
            gaps.append((label, missing))
        print(f"  {'OK  ' if ok else 'GAP '}  {label}")
        for m in missing:
            print(f"           missing pattern: {m}")
    print()
    print("=== VERDICT ===")
    if gaps:
        print(f"  {len(gaps)} coverage gap(s) -- report before proposing code.")
    else:
        print("  All 9 requirements still covered by the EXISTING two production-path tests.")
        print("  A third test is NOT needed and must not be added.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
