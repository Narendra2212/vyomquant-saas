"""Did Phase 6's new test files tip the task-12.1 census over its floor?

The census failure appears only in the full suite and never in isolation (8/8 passed,
staging/raw_PG2B.txt). Hypothesis 6.x harvests CONSTANTS from the modules present in the
process to seed its draws, so adding modules can change the example distribution without
changing a single line of the generator. That makes "my 94 new tests shifted it" a real
hypothesis rather than a hand-wave - and a testable one.

Four selections, each in a fresh subprocess:

  A  the property test alone                       -- the isolation baseline
  B  the whole tests/property/ directory           -- its own neighbourhood
  C  the property test + Phase 6's three new files -- the attribution question
  D  the property test + the paper package's own suites

A failure in C but not in B is attribution. A failure in B too is a pre-existing property of
the suite. No failure anywhere means the trigger needs more of the suite present than any of
these selections provide, which is itself worth reporting plainly.

Run:  .venv\\Scripts\\python.exe staging\\probe_census_attribution.py
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

TARGET = "tests/property/test_order_idempotence_under_retry.py"
PHASE6 = [
    "tests/test_environment_precedence_is_centralised.py",
    "tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py",
    "tests/test_paper01_paper_live_isolation.py",
]
PAPER = [
    "tests/test_paper_no_random.py",
    "tests/test_paper_repository.py",
]

SELECTIONS = {
    "A: target alone": [TARGET],
    "B: all of tests/property": ["tests/property"],
    "C: target + Phase 6's three new files": [TARGET, *PHASE6],
    "D: target + paper suites": [TARGET, *PAPER],
}


def run(paths: list[str]) -> tuple[str, int | None, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("AERORA_TEST_DATABASE_URL", None)
    cp = subprocess.run(
        [
            str(PYTHON), "-m", "pytest", *paths,
            "-q", "--tb=line", "-rfE",
            "-p", "no:cacheprovider", "-p", "no:warnings", "-o", "addopts=",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    text = cp.stdout + cp.stderr
    census = re.search(r"'sequence_length_10': (\d+)", text)
    target_failed = "test_order_idempotence_under_generated_retry" in "\n".join(
        line for line in text.splitlines() if line.startswith("FAILED ")
    )
    summary = next(
        (
            line.strip()
            for line in reversed(text.splitlines())
            if re.search(r"\b\d+ (passed|failed|error)", line)
        ),
        "<no summary>",
    )
    verdict = "TARGET FAILED" if target_failed else "target passed"
    return verdict, (int(census.group(1)) if census else None), summary


def main() -> int:
    for label, paths in SELECTIONS.items():
        verdict, census, summary = run(paths)
        print(f"{label}")
        print(f"    {verdict:<14} sequence_length_10={census}")
        print(f"    {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
