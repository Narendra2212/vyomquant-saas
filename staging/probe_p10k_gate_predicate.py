"""PHASE 10 / WORKSTREAM K -- why the CI Redis service does NOT close the QUOTA-01 gate.

`.github/workflows/01-pr-check.yml`'s `unit-tests` job provisions `redis:7-alpine`, maps
6379, health-checks it, and exports `REDIS_URL: redis://localhost:6379` to the
`pytest tests/ ...` step. That looks like the gate is already satisfied on every PR.

It is not. `tests/conftest.py` line 13 does, unconditionally and at import time:

    os.environ["REDIS_URL"] = ""        # "force in-memory rate limiting even if set"

conftest is imported BEFORE any test module, so by the time a module-level
`os.environ.get("REDIS_URL")` runs, the value the workflow supplied is gone.

This probe measures that, with NO network connection: it only inspects environment state
after importing the real conftest, and runs pytest in a mode where the gated tests skip
(so nothing dials a server).
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


CHILD = r"""
import os, sys
sys.path.insert(0, r"{repo}")
before_redis = os.environ.get("REDIS_URL")
before_aerora = os.environ.get("AERORA_TEST_REDIS_URL")
import tests.conftest  # noqa: F401  -- the real conftest, imported as pytest would
after_redis = os.environ.get("REDIS_URL")
after_aerora = os.environ.get("AERORA_TEST_REDIS_URL")
print("REDIS_URL               before=%r after=%r" % (before_redis, after_redis))
print("AERORA_TEST_REDIS_URL   before=%r after=%r" % (before_aerora, after_aerora))
# The gate expression, verbatim from the test module.
gate = os.environ.get("AERORA_TEST_REDIS_URL") or os.environ.get("REDIS_URL") or ""
print("gate value              = %r" % gate)
print("gate would RUN the tests = %s" % gate.startswith(("redis://", "rediss://")))
"""


def run_case(title: str, env_overrides: dict) -> None:
    print(f"\n-- {title}")
    env = dict(os.environ)
    env.update(env_overrides)
    env.setdefault("PYTHONPATH", REPO)
    out = subprocess.run(
        [sys.executable, "-c", CHILD.format(repo=REPO)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=REPO,
    )
    for line in (out.stdout or "").strip().splitlines():
        print(f"     {line}")
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()[-3:]
        for line in tail:
            print(f"     stderr: {line}")


def main():
    banner("QUOTA-01 GATE PREDICATE -- does the CI Redis service reach the gated tests?")

    run_case(
        "A. CI's actual configuration: REDIS_URL exported by the workflow",
        {"REDIS_URL": "redis://localhost:6379", "AERORA_TEST_REDIS_URL": ""},
    )
    run_case(
        "B. The lever that WOULD work: AERORA_TEST_REDIS_URL (conftest leaves it alone)",
        {"REDIS_URL": "redis://localhost:6379",
         "AERORA_TEST_REDIS_URL": "redis://localhost:6379/15"},
    )
    run_case(
        "C. This local environment: neither set",
        {"REDIS_URL": "", "AERORA_TEST_REDIS_URL": ""},
    )

    banner("CONCLUSION")
    print("  Case A is what CI runs today. If its gate value is '' then the CI redis")
    print("  service, although healthy and reachable, CANNOT close this gate: conftest")
    print("  blanks REDIS_URL before the test module reads it.")
    print()
    print("  Case B shows the required change -- set AERORA_TEST_REDIS_URL on the job.")
    print("  That is a CI workflow edit and is NOT made by this workstream.")
    print()
    print("  NOTE: the same gate shape is used by the PRE-EXISTING")
    print("  tests/test_quota01_metered_reservation_is_hard.py real-Redis test, so that")
    print("  test has also never executed in CI.")


if __name__ == "__main__":
    main()
