"""Verify that every file the launch documents cite by path actually exists.

A readiness report whose "reproduce this" section names a script that was deleted is
worse than one with no such section, because it invites a reader to conclude the whole
thing is approximate. This checks the claim mechanically.

Exit 0 -> every cited path resolves.
Exit 1 -> at least one does not; each is printed with the document and line that cites it.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOCS = [
    "FINAL_LAUNCH_READINESS_REPORT.md",
    "PAPER_ONLY_LAUNCH_READINESS.md",
    "DATABASE_PROVISIONING_RUNBOOK.md",
    "BILLING_SECURITY_VERIFICATION.md",
    "RLS_TENANT_ISOLATION_VERIFICATION.md",
    "POST_LAUNCH_IMPROVEMENTS.md",
    "FINAL_PRE_LAUNCH_BUGS.md",
    # The production drift gate's own record. Checked alongside the rest because it is a
    # deliverable that cites tooling by path, and a launch document naming a script that
    # does not exist invites a reader to dismiss the whole set as approximate.
    "staging/FINAL_PRODUCTION_VERIFICATION.md",
]

#: Paths that look like file references in these documents. Restricted to the directories
#: the documents actually cite, so prose like "tests/ is slow" does not become a finding.
PATTERN = re.compile(
    r"(?<![\w/.])"
    r"((?:staging|scripts|tests|migrations|backend_app|\.github)"
    r"[/\\][\w./\\-]*?\.(?:py|sql|yml|json|md|jsx|js))"
)

#: Paths the documents name on purpose without them existing. Two distinct reasons, and
#: both are legitimate -- but each entry needs its reason written down, or this set
#: becomes a way to silence the check.
EXPECTED_ABSENT = {
    # Named in the tenant-isolation matrix as a table/module that was never created --
    # the point of the citation is its ABSENCE.
    "tests/library_favorites.py",
    # OUTPUT paths, not evidence. These are the `--out` arguments in the production
    # verification commands: the documents instruct a reader to CREATE them by running
    # against production, which has not happened for want of a DSN. They must not exist
    # yet -- if they did, it would mean a production run had been made and not reported.
    "staging/production_schema.json",
    "staging/production_isolation.json",
    # Named by `staging/FINAL_PRODUCTION_VERIFICATION.md` as the document that WOULD be
    # produced for a RED production result. There is no production result, so writing a
    # remediation plan for drift nobody has observed would be fiction. Its absence is
    # the correct state and the document says so; this entry records that the absence is
    # deliberate rather than an oversight.
    "staging/PRODUCTION_REMEDIATION_PLAN.md",
}


def main() -> int:
    missing: list[tuple[str, int, str]] = []
    checked = 0

    for doc in DOCS:
        path = ROOT / doc
        if not path.exists():
            missing.append((doc, 0, "THE DOCUMENT ITSELF IS MISSING"))
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            for match in PATTERN.finditer(line):
                cited = match.group(1).replace("\\", "/")
                if cited in EXPECTED_ABSENT:
                    continue
                checked += 1
                if not (ROOT / cited).exists():
                    missing.append((doc, lineno, cited))

    print("=" * 78)
    print("DOCUMENT CITATION CHECK")
    print("=" * 78)
    print(f"{len(DOCS)} document(s), {checked} path citation(s) resolved")
    print("-" * 78)

    if missing:
        # De-duplicate: the same script is cited many times and one line per citation
        # would bury the distinct problems.
        seen: set[str] = set()
        for doc, lineno, cited in missing:
            if cited in seen:
                continue
            seen.add(cited)
            print(f"  MISSING  {cited}")
            print(f"           first cited by {doc}:{lineno}")
        print("-" * 78)
        print(f"SUMMARY: {len(seen)} distinct cited path(s) do not exist")
        return 1

    print("SUMMARY: every cited path exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
