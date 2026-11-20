"""PHASE 11B-A2 -- read-only: what is actually failing in CI's `Unit & Integration Tests`?

This is the job D5' edits. If it is already red for reasons unrelated to D5', then pushing
D5' would not produce a clean verification signal, and that is a finding in its own right.

`gh run view --log` is a READ operation. Nothing is triggered, pushed or re-run.
"""

from __future__ import annotations

import re
import subprocess

RUN_ID = "37346853502"          # most recent 01-pr-check run (failure)
JOB_NAME = "Unit & Integration Tests"


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner(f"CI job log: run {RUN_ID} / {JOB_NAME}")

    r = subprocess.run(
        ["gh", "run", "view", RUN_ID, "--log", "--job", _job_id()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log = r.stdout or ""
    if r.returncode != 0 or not log:
        print(f"  gh exit={r.returncode}; stderr: {(r.stderr or '').strip()[:200]}")
        return

    lines = log.splitlines()
    print(f"  log lines: {len(lines)}")

    # The pytest summary and the first failures are what matter.
    banner("A. REDIS SERVICE HEALTH (did the service come up?)")
    for ln in lines:
        low = ln.lower()
        if "redis" in low and any(k in low for k in
                                 ("health", "starting", "created", "pull", "ready")):
            print(f"  {ln.strip()[-150:]}")

    banner("B. PYTEST SUMMARY LINES")
    for ln in lines:
        if re.search(r"=+\s*\d+ (passed|failed)", ln) or "short test summary" in ln:
            print(f"  {ln.strip()[-160:]}")

    banner("C. FIRST FAILED TESTS (up to 15)")
    failed = [ln.strip() for ln in lines if re.search(r"^\S*\s*FAILED ", ln)]
    if not failed:
        failed = [ln.strip() for ln in lines if "FAILED " in ln]
    for ln in failed[:15]:
        print(f"  {ln[-170:]}")
    print(f"\n  total FAILED lines seen: {len(failed)}")

    banner("D. ERROR / COLLECTION PROBLEMS")
    for ln in lines:
        if ("ERROR " in ln or "error:" in ln.lower()) and "pytest" not in ln.lower():
            s = ln.strip()
            if len(s) > 20:
                print(f"  {s[-150:]}")
                if _count(locals()) > 12:
                    break

    banner("E. DOES THE LOG MENTION THE QUOTA OR WEBHOOK REAL-REDIS TESTS?")
    for needle in ("quota01_exchange_slot", "quota01_metered", "real_redis",
                   "webhook_duplicate_delivery", "AERORA_TEST_REDIS_URL",
                   "api04_store_failure"):
        hits = [ln for ln in lines if needle in ln]
        print(f"  {needle:34s} {len(hits)} mention(s)")


_seen = {"n": 0}


def _count(_):
    _seen["n"] += 1
    return _seen["n"]


def _job_id() -> str:
    """Resolve the numeric job id for JOB_NAME (read-only)."""
    import json
    r = subprocess.run(
        ["gh", "run", "view", RUN_ID, "--json", "jobs"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        jobs = json.loads(r.stdout)["jobs"]
    except Exception:
        return ""
    for j in jobs:
        if j.get("name") == JOB_NAME:
            return str(j.get("databaseId"))
    return ""


if __name__ == "__main__":
    main()
