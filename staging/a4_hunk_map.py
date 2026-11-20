"""Maps the hunks of the unstaged `01-pr-check.yml` diff and separates D5' from the
unrelated `provisioning-and-isolation` job.

Phase 11B-A4 needs this to propose a partial-commit method that cannot accidentally ship
another workstream's job. It classifies each hunk by whether its added lines mention the
D5' switch or the provisioning job, and reports the functional (non-comment, non-blank)
added lines so a reviewer can see exactly what the intended change is.

READ-ONLY. Runs `git diff` only. Stages nothing, writes nothing to the index.
"""

from __future__ import annotations

import subprocess
import sys

WORKFLOW = ".github/workflows/01-pr-check.yml"
SWITCH = "AERORA_TEST_REDIS_URL"
OTHER_JOB = "provisioning-and-isolation"


def main() -> int:
    proc = subprocess.run(
        ["git", "diff", "-U3", "--", WORKFLOW],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        print("git diff failed")
        return 1

    hunks: list[dict] = []
    cur: dict | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            cur = {"hdr": line, "added": []}
            hunks.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            cur["added"].append(line[1:])

    print(f"{WORKFLOW}: {len(hunks)} hunk(s)\n")

    d5_hunks, other_hunks, unclear = [], [], []

    for index, hunk in enumerate(hunks, start=1):
        body = "\n".join(hunk["added"])
        is_d5 = SWITCH in body
        is_other = OTHER_JOB in body
        # Comment-only hunks are attributed by which change they document.
        functional = [
            l for l in hunk["added"]
            if l.strip() and not l.strip().startswith("#")
        ]

        if is_d5 and not is_other:
            bucket, label = d5_hunks, "D5'"
        elif is_other and not is_d5:
            bucket, label = other_hunks, "OTHER WORKSTREAM"
        elif not functional and ("REDIS" in body.upper() or "conftest" in body):
            bucket, label = d5_hunks, "D5' (comment only)"
        else:
            bucket, label = unclear, "UNCLEAR — review by hand"
        bucket.append(index)

        print(f"HUNK {index}  {hunk['hdr']}")
        print(f"  classification : {label}")
        print(f"  added lines    : {len(hunk['added'])} ({len(functional)} functional)")
        for l in functional[:8]:
            print(f"  FUNCTIONAL     > {l}")
        if len(functional) > 8:
            print(f"  ... {len(functional) - 8} more functional lines")
        print()

    print("SUMMARY")
    print(f"  D5' hunks              : {d5_hunks}")
    print(f"  other-workstream hunks : {other_hunks}")
    print(f"  unclear                : {unclear}")
    print()
    if unclear:
        print("  A hunk could not be attributed automatically. Do NOT use `git add -p`")
        print("  blindly; review that hunk by hand first.")
        return 1
    if d5_hunks and other_hunks:
        print("  The two changes occupy DISJOINT hunks, so `git add -p` can accept the")
        print(f"  D5' hunks {d5_hunks} and skip {other_hunks} with no manual hunk splitting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
