"""Is the task-12.1 census floor flaky, or did Phase 6 break it?

`tests/property/test_order_idempotence_under_retry.py` failed in the Phase 6 full run with

    task-12.1 would be vacuous: sequence_length_10 occurred 0 time(s), floor 1
    -- full census {'examples': 100, ..., 'sequence_length_at_or_above_5': 56,
                    'sequence_length_10': 0, ...}

and PASSES in isolation. The census is a COVERAGE floor over Hypothesis-generated examples,
not an assertion about the system under test, so the question is whether the generator
GUARANTEES the case or merely tends to produce it. If it merely tends to, the floor is flaky
by construction and the failure is independent of anything this phase changed.

Measured by running the file N times in fresh subprocesses and recording, per run, the
observed `sequence_length_10` count. Each subprocess gets its own Hypothesis entropy, which
is exactly the variable under test.

Run:  .venv\\Scripts\\python.exe staging\\probe_census_flake.py [runs]
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
TARGET = "tests/property/test_order_idempotence_under_retry.py"

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def main() -> int:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("AERORA_TEST_DATABASE_URL", None)

    outcomes: list[tuple[int, str, int | None]] = []

    for i in range(1, RUNS + 1):
        cp = subprocess.run(
            [
                str(PYTHON), "-m", "pytest", TARGET,
                "-q", "--tb=line", "-rfE",
                "-p", "no:cacheprovider", "-p", "no:warnings",
                "-o", "addopts=",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        text = cp.stdout + cp.stderr
        hit = re.search(r"'sequence_length_10': (\d+)", text)
        observed = int(hit.group(1)) if hit else None
        verdict = "FAILED" if cp.returncode != 0 else "passed"
        outcomes.append((i, verdict, observed))
        print(f"  run {i}: {verdict:<7} sequence_length_10={observed}")

    failures = sum(1 for _, v, _ in outcomes if v == "FAILED")
    print(f"\nRUNS={RUNS}  FAILED={failures}  PASSED={RUNS - failures}")
    print(
        "VERDICT="
        + (
            "FLAKY (the floor is not guaranteed by the generator)"
            if 0 < failures < RUNS
            else "DETERMINISTIC in this sample -- "
            + ("always fails" if failures == RUNS else "always passes")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
