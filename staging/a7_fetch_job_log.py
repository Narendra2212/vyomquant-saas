"""Fetches a named job's log from a GitHub Actions run and extracts the Redis evidence.

Usage: python staging/a7_fetch_job_log.py <run_id>

READ-ONLY against GitHub. Writes only to staging/.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

JOB_NAME = "Unit & Integration Tests"
RAW = "staging/a7_unit_log.txt"

REQUIRED = [
    "test_concurrent_reservations_are_atomic_against_real_redis",
    "test_the_slot_lock_is_mutually_exclusive_against_real_redis",
    "test_the_guard_admits_exactly_the_limit_against_real_redis",
]
WEBHOOK = [
    "test_the_credential_gate_refuses_test_keys",
    "test_a_redelivered_webhook_is_ignored",
    "test_the_lock_is_recorded_in_redis_under_the_documented_key",
    "test_a_different_event_id_is_not_deduplicated",
    "test_an_in_flight_delivery_blocks_a_concurrent_one",
    "test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind",
]


def gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"gh failed: {proc.stderr[:500]}")
    return proc.stdout


def main() -> int:
    run_id = sys.argv[1]
    jobs = json.loads(gh(["run", "view", run_id, "--json", "jobs"]))["jobs"]
    match = [j for j in jobs if j["name"] == JOB_NAME]
    if not match:
        raise SystemExit(f"job {JOB_NAME!r} not found")
    job = match[0]
    print(f"job: {job['name']}  id={job['databaseId']}  "
          f"{job['status']}/{job['conclusion']}")
    print(f"url: {job.get('url')}\n")

    log = gh(["run", "view", run_id, "--log", "--job", str(job["databaseId"])])
    with open(RAW, "w", encoding="utf-8") as handle:
        handle.write(log)
    print(f"log written: {RAW} ({len(log)} chars)\n")

    lines = log.splitlines()

    print("=== REDIS SERVICE / SWITCH ===")
    for pat, label in [
        (r"redis", "redis mentions"),
        (r"AERORA_TEST_REDIS_URL", "switch mentions"),
    ]:
        n = sum(1 for l in lines if re.search(pat, l, re.I))
        print(f"  {label}: {n}")
    for l in lines:
        if "Initializing containers" in l or ("redis" in l.lower() and ("health" in l.lower() or "Starting" in l or "Created" in l)):
            print(f"  > {l.strip()[-160:]}")
    print()

    print("=== GATE: SKIP REASON PRESENT? ===")
    skip_hits = [l for l in lines if "Real-Redis coverage is opt-in" in l]
    print(f"  gate skip-reason occurrences: {len(skip_hits)}  (0 => gate reached 'run')")
    for l in skip_hits[:3]:
        print(f"  > {l.strip()[-200:]}")
    print()

    print("=== REQUIRED QUOTA REDIS TESTS ===")
    for name in REQUIRED:
        hits = [l for l in lines if name in l and re.search(r"PASSED|FAILED|SKIPPED|ERROR", l)]
        print(f"  {name}")
        if hits:
            for l in hits[:2]:
                print(f"      {l.strip()[-150:]}")
        else:
            print("      NOT FOUND in log")
    print()

    print("=== WEBHOOK REDIS TESTS ===")
    for name in WEBHOOK:
        hits = [l for l in lines if name in l and re.search(r"PASSED|FAILED|SKIPPED|ERROR", l)]
        verdict = "NOT FOUND"
        if hits:
            m = re.search(r"PASSED|FAILED|SKIPPED|ERROR", hits[0])
            verdict = m.group(0) if m else "?"
        print(f"  {verdict:<10} {name}")
    print()

    print("=== PYTEST SUMMARY LINES ===")
    for l in lines:
        if re.search(r"=+ .*(passed|failed|error).* =+", l):
            print(f"  {l.strip()[-200:]}")
    print()

    print("=== ALL FAILED / ERROR TEST IDS ===")
    failed = sorted({
        m.group(1)
        for l in lines
        for m in [re.search(r"(tests/\S+::\S+)\s+(?:FAILED|ERROR)", l)]
        if m
    })
    if not failed:
        failed = sorted({
            m.group(1)
            for l in lines
            for m in [re.search(r"^FAILED\s+(\S+)", l.split("\t")[-1].strip())]
            if m
        })
    for f in failed:
        print(f"  {f}")
    print(f"  total distinct: {len(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
