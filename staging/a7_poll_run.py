"""Polls a GitHub Actions run and prints its per-job status. READ-ONLY.

Usage: python staging/a7_poll_run.py <run_id> [max_wait_seconds]

Exists because the PowerShell wrapper on this host mangles inline here-strings and `$_`,
so a poll loop cannot reliably be written in the shell.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time


def view(run_id: str) -> dict:
    proc = subprocess.run(
        ["gh", "run", "view", run_id, "--json", "status,conclusion,jobs"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh run view failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def main() -> int:
    run_id = sys.argv[1]
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    deadline = time.time() + budget

    while True:
        data = view(run_id)
        status = data.get("status")
        print(f"run {run_id}: {status} / {data.get('conclusion')}")
        for job in data.get("jobs", []):
            print(f"  {job['name'][:50]:52} {job['status']}/{job['conclusion']}")
        if status == "completed" or time.time() > deadline:
            print("" if status == "completed" else "\n(poll budget exhausted; still running)")
            return 0
        print("  ... waiting 60s\n", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
