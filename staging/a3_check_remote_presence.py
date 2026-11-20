"""Determines whether the D5' real-Redis work exists anywhere a GitHub Actions run could
have seen it.

A GitHub Actions run can only execute code that is present in a commit on the remote. So
"is there an eligible run?" reduces to "is this work on any remote ref?". This answers that
by inspecting every remote branch's tree, rather than inferring from local `git status`.

Read-only: `git ls-tree` and `git show` only. No fetch, no checkout, no ref is created or
moved.
"""

from __future__ import annotations

import subprocess
import sys

PATHS = [
    "tests/real_redis_gate.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
]

# The D5' switch itself. Its presence in the workflow is what makes the real-Redis tests
# run rather than skip, so a ref without it could not produce the required evidence even if
# it had the test files.
WORKFLOW = ".github/workflows/01-pr-check.yml"
SWITCH = "AERORA_TEST_REDIS_URL"


def run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, (proc.stdout or "")


def remote_branches() -> list[str]:
    code, out = run(["branch", "-r", "--format=%(refname:short)"])
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip() and "->" not in line]


def main() -> int:
    branches = remote_branches()
    print(f"Remote branches inspected: {len(branches)}\n")

    any_file_anywhere = False
    any_switch_anywhere = False

    header = f"{'branch':<52} {'gate+tests':<12} {SWITCH}"
    print(header)
    print("-" * len(header))

    for branch in branches:
        code, out = run(["ls-tree", "-r", "--name-only", branch, "--", *PATHS])
        present = [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else []

        code_wf, wf = run(["show", f"{branch}:{WORKFLOW}"])
        has_switch = code_wf == 0 and SWITCH in wf

        if present:
            any_file_anywhere = True
        if has_switch:
            any_switch_anywhere = True

        print(
            f"{branch:<52} {(str(len(present)) + '/4'):<12} {'yes' if has_switch else 'no'}"
        )

    print()
    print("LOCAL WORKING TREE (not visible to CI):")
    code, out = run(["status", "--porcelain=v1", "--", *PATHS, WORKFLOW, "tests/conftest.py"])
    for line in out.splitlines():
        if line.strip():
            print(f"  {line}")

    print()
    if any_file_anywhere or any_switch_anywhere:
        print("RESULT: some part of the D5' work IS on a remote ref — an eligible run may exist.")
        return 0

    print("RESULT: NO remote ref carries the gate, the tests, or the D5' switch.")
    print("        Therefore NO GitHub Actions run can contain the D5' changes, and no")
    print("        eligible run can exist until the work is pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
