"""Summarises recent `01 PR Check` runs and tests each one for the D5' work.

For every run, the question is: does the commit that run actually tested contain the shared
real-Redis gate, the three real-Redis test files, and the `AERORA_TEST_REDIS_URL` switch? If
not, that run cannot be the evidence Phase 11B-A3 requires, however green it is.

`git cat-file -e <sha>:<path>` is the test. A commit this clone does not have is reported as
`unfetched` rather than guessed at.

Read-only.
"""

from __future__ import annotations

import json
import subprocess
import sys

PATHS = [
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]
WORKFLOW = ".github/workflows/01-pr-check.yml"
SWITCH = "AERORA_TEST_REDIS_URL"


def git(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, proc.stdout or ""


def have_commit(sha: str) -> bool:
    return git(["cat-file", "-e", f"{sha}^{{commit}}"])[0] == 0


def main() -> int:
    # PowerShell's `>` redirect writes UTF-16LE with a BOM on this host, so a plain
    # utf-8 read fails on byte 0 with 0xff. Decode by sniffing the BOM rather than
    # assuming, so the script works whichever shell captured the file.
    with open("staging/a3_runs.json", "rb") as handle:
        blob = handle.read()
    if blob[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = blob.decode("utf-16")
    else:
        text = blob.decode("utf-8-sig")
    runs = json.loads(text)

    print(f"Runs inspected: {len(runs)}\n")
    header = f"{'run id':<12} {'sha':<10} {'branch':<34} {'result':<14} {'D5 files':<10} {SWITCH}"
    print(header)
    print("-" * len(header))

    eligible = 0
    for run in runs:
        sha = run.get("headSha") or ""
        short = sha[:8]
        branch = (run.get("headBranch") or "")[:33]
        result = f"{run.get('status')}/{run.get('conclusion')}"[:13]

        if not have_commit(sha):
            files_cell, switch_cell = "unfetched", "unfetched"
        else:
            code, out = git(["ls-tree", "-r", "--name-only", sha, "--", *PATHS])
            found = len([l for l in out.splitlines() if l.strip()]) if code == 0 else 0
            code_wf, wf = git(["show", f"{sha}:{WORKFLOW}"])
            has_switch = code_wf == 0 and SWITCH in wf
            files_cell = f"{found}/4"
            switch_cell = "yes" if has_switch else "no"
            if found == len(PATHS) and has_switch:
                eligible += 1

        print(f"{run.get('databaseId'):<12} {short:<10} {branch:<34} {result:<14} {files_cell:<10} {switch_cell}")

    print()
    print(f"Runs containing the complete D5' change set: {eligible}")
    if eligible == 0:
        print("=> NO ELIGIBLE RUN. The required real-Redis evidence cannot be read from CI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
