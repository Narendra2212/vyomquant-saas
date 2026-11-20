"""A test file that gates itself on PostgreSQL must be named in a PostgreSQL lane.

WHY THIS EXISTS

``01-pr-check.yml``'s two PostgreSQL jobs (``database-tests`` and
``provisioning-and-isolation``) name the files they run EXPLICITLY. Neither runs
``pytest tests/``. That is a deliberate design -- the job comments explain that pointing
the whole suite at PostgreSQL in one move would make a new PostgreSQL-specific failure
indistinguishable from a regression -- but it has a failure mode nothing was watching:

A file that gates itself on ``AERORA_TEST_DATABASE_URL`` and is not named in either list
runs against a real server NOWHERE. It still looks accounted for, because:

* the ``unit-tests`` lane has no ``DATABASE_URL`` on purpose, so the file's
  PostgreSQL tests SKIP there -- and a skip is not a failure, so a fully green run
  reports nothing wrong; and
* neither PostgreSQL lane lists the file, so its gate never opens.

Measured against the real CI log for run 39695784610 (HEAD dfd9ca24, all 8 jobs green):
49 files gate on that variable, the two lanes ran 11 of them, and the remaining 35
contributed 432 SKIPPED results and zero assertions against PostgreSQL. Among them was
the regression test for SEC-07, a P0 privilege-escalation finding recorded in the ledger
as VERIFIED_FIXED.

This guard fails if a PostgreSQL-gated test file is not named in a PostgreSQL lane, so
adding one without wiring it up is a red pipeline rather than a silent 'skipped'.

HOW TO SATISFY IT

Either add the file to a PostgreSQL lane in ``01-pr-check.yml``, or -- if it references
the variable without being a PostgreSQL-behaviour test -- add it to ``EXEMPT`` below with
a reason. The exemption list is deliberately tiny and each entry states why.
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict, Set

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "01-pr-check.yml"

#: The environment variable ``tests/conftest.py`` requires before it lets a real
#: ``DATABASE_URL`` survive into the test process. A file naming it is declaring that it
#: has assertions a SQLite substrate cannot carry.
PG_GATE_VARIABLE = "AERORA_TEST_DATABASE_URL"

#: Files that name the gate variable without being PostgreSQL-behaviour tests. Each entry
#: must say why running it in a PostgreSQL lane would add runtime and no coverage.
EXEMPT: Dict[str, str] = {
    "tests/conftest.py": (
        "the gate itself -- it reads the variable to decide whether to blank DATABASE_URL"
    ),
    "tests/real_redis_gate.py": "shared Redis gate helper, not a test module",
    "tests/test_pr_check_postgres_service.py": (
        "its subject is this workflow's YAML (that the service and env are declared), "
        "not a running server; it passes in the SQLite lane"
    ),
    "tests/test_sqlite_lane_is_greenable.py": (
        "measures the SQLite lane by design; running it against PostgreSQL would measure "
        "the opposite of what it exists to measure"
    ),
    "tests/test_webhook_duplicate_delivery_real_redis.py": (
        "needs a real Redis service, which neither PostgreSQL job runs; it is gated on "
        "Redis and only mentions the PostgreSQL variable in passing"
    ),
    "tests/test_every_postgres_gated_file_runs_in_a_postgres_lane.py": (
        "this guard itself -- its subject is the workflow YAML and the tests directory, "
        "not a running server; it names the gate variable only to search for it"
    ),
}


def _postgres_gated_files() -> Set[str]:
    """Repo-relative paths of test modules that gate themselves on PostgreSQL."""
    found: Set[str] = set()
    for path in TESTS_DIR.rglob("*.py"):
        try:
            if PG_GATE_VARIABLE in path.read_text(encoding="utf-8", errors="replace"):
                found.add("tests/" + path.relative_to(TESTS_DIR).as_posix())
        except OSError:
            continue
    return found


def _files_named_in_postgres_lanes() -> Set[str]:
    """Every ``tests/...py`` path mentioned inside a job that defines a PostgreSQL DSN.

    Read as text rather than parsed YAML on purpose: the thing being measured is which
    paths the ``run:`` shell scripts actually hand to pytest, and those are plain strings
    inside a block scalar. A path mentioned anywhere in such a job's executable lines is
    treated as covered, which is the permissive direction -- this guard is here to catch
    a file named NOWHERE, not to police which lane it landed in.

    COMMENT LINES ARE STRIPPED FIRST. Without that, merely DISCUSSING a file in a comment
    inside a PostgreSQL job would mark it covered -- which this guard did to itself on its
    first run, because the step comment above names this very file. A mention is not an
    invocation.
    """
    raw = WORKFLOW.read_text(encoding="utf-8")
    text = "\n".join(
        "" if line.lstrip().startswith("#") else line for line in raw.splitlines()
    )

    # Split into top-level jobs on two-space-indented keys under `jobs:`.
    job_starts = [m.start() for m in re.finditer(r"^  [A-Za-z0-9_-]+:$", text, re.MULTILINE)]
    job_starts.append(len(text))

    named: Set[str] = set()
    for i in range(len(job_starts) - 1):
        block = text[job_starts[i]: job_starts[i + 1]]
        # A PostgreSQL job is one that points the test process at a real server.
        if f"{PG_GATE_VARIABLE}:" not in block:
            continue
        for m in re.finditer(r"tests/[A-Za-z0-9_/]+\.py", block):
            named.add(m.group(0))
    return named


def test_the_workflow_and_tests_directory_are_both_present():
    """Guards against this file passing vacuously if either input moves."""
    assert WORKFLOW.is_file(), f"workflow not found at {WORKFLOW}"
    assert TESTS_DIR.is_dir(), f"tests directory not found at {TESTS_DIR}"
    gated = _postgres_gated_files()
    assert gated, (
        f"no test file mentions {PG_GATE_VARIABLE}; the detector has stopped measuring "
        "anything and this file's other assertions would pass for the wrong reason"
    )


def test_at_least_one_job_declares_a_postgres_dsn():
    """Negative-control on the lane detector itself."""
    named = _files_named_in_postgres_lanes()
    assert named, (
        "no job in 01-pr-check.yml declares "
        f"{PG_GATE_VARIABLE}, so the lane detector found no PostgreSQL lane at all. "
        "Either the workflow changed shape or this guard's parser is broken; both make "
        "the coverage assertion below meaningless."
    )


def test_every_postgres_gated_test_file_is_named_in_a_postgres_lane():
    gated = _postgres_gated_files()
    named = _files_named_in_postgres_lanes()

    uncovered = sorted(gated - named - set(EXEMPT))
    assert not uncovered, (
        "These test files gate themselves on "
        f"{PG_GATE_VARIABLE} but are named in no PostgreSQL lane in 01-pr-check.yml, so "
        "their PostgreSQL assertions run against a real server NOWHERE -- they skip in "
        "the SQLite lane, which a green run does not report:\n"
        + "\n".join(f"  {path}" for path in uncovered)
        + "\n\nAdd each to a PostgreSQL lane, or to this file's EXEMPT map with a reason."
    )


def test_no_exemption_is_stale():
    """An exemption for a file that no longer exists (or no longer gates) must go.

    Otherwise the list silently accumulates entries that would hide a future gap.
    """
    gated = _postgres_gated_files()
    all_test_paths = {
        "tests/" + p.relative_to(TESTS_DIR).as_posix() for p in TESTS_DIR.rglob("*.py")
    }

    missing = sorted(path for path in EXEMPT if path not in all_test_paths)
    assert not missing, (
        "EXEMPT names files that no longer exist; delete these entries:\n"
        + "\n".join(f"  {path}" for path in missing)
    )

    no_longer_gated = sorted(path for path in EXEMPT if path not in gated)
    assert not no_longer_gated, (
        f"EXEMPT names files that no longer mention {PG_GATE_VARIABLE}, so they are not "
        "PostgreSQL-gated and need no exemption; delete these entries:\n"
        + "\n".join(f"  {path}" for path in no_longer_gated)
    )


def test_every_exemption_carries_a_reason():
    unreasoned = sorted(path for path, reason in EXEMPT.items() if not (reason or "").strip())
    assert not unreasoned, (
        "every exemption must state why the file is not a PostgreSQL-behaviour test:\n"
        + "\n".join(f"  {path}" for path in unreasoned)
    )
