"""
tests/test_no_undefined_names.py

Regression guard for undefined names in ``backend_app``.

WHY THIS FILE EXISTS
--------------------
``.github/workflows/01-pr-check.yml`` runs, in its ``validate-code`` job:

    flake8 backend_app --count --select=E9,F63,F7,F82 --show-source --statistics

That selection is the syntax-and-undefined-name gate: ``E9`` is a parse failure, ``F63``
and ``F7`` are broken comparisons and statements, and ``F82`` is an undefined name. Every
one of them means a line raises the moment control reaches it.

The gate is real but it only fires on ``pull_request``, so nothing ever ran it against
``main``. Four F821 instances were therefore sitting on the default branch:

    backend_app/core/credential_vault.py  _validate_credential_strength  (never defined)
    backend_app/core/credential_vault.py  credential_id                  (read two lines
                                                                          before assignment)
    backend_app/routers/strategy_operations.py  performance_monitor      (never imported)

The first two meant that storing an exchange API credential - the live-trading onboarding
path - raised on every single call. The third meant ``GET /performance/summary`` answered
500 every time.

This test runs the workflow's exact selection inside the pytest lane, which does run on
every push, so a fifth instance cannot land silently. It asserts a zero exit and prints
flake8's own ``--show-source`` output on failure, so a red run names the file, line and
symbol without a second command.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Character-for-character the selection in 01-pr-check.yml's "Flake8 Check" step. Changing
# it here without changing it there makes this test stop guarding what CI guards.
FLAKE8_SELECT = "E9,F63,F7,F82"
FLAKE8_TARGET = "backend_app"


def _flake8_available() -> bool:
    try:
        import flake8  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    not _flake8_available(),
    reason="flake8 is not installed; the authoritative gate is 01-pr-check.yml's "
           "validate-code job, which installs it explicitly",
)
def test_backend_app_has_no_undefined_names():
    """``flake8 --select=E9,F63,F7,F82 backend_app`` must report zero findings."""
    env = dict(os.environ)
    # utf-8 so flake8's output survives a Windows console codepage; no .pyc so the run
    # leaves the tree byte-identical.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [
            sys.executable, "-m", "flake8", FLAKE8_TARGET,
            "--count",
            f"--select={FLAKE8_SELECT}",
            "--show-source",
            "--statistics",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, (
        f"flake8 --select={FLAKE8_SELECT} reported findings in {FLAKE8_TARGET}. Each one is "
        f"a line that raises when reached - a syntax error, a broken comparison, or an "
        f"undefined name. This is the same command 01-pr-check.yml runs, so a non-zero exit "
        f"here is a non-zero exit there.\n\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


@pytest.mark.skipif(
    not _flake8_available(),
    reason="flake8 is not installed",
)
@pytest.mark.parametrize(
    "module",
    [
        # The two modules that carried the four defects this guard was written for. Named
        # individually so a regression in either is attributed without reading the
        # package-wide output.
        "backend_app/core/credential_vault.py",
        "backend_app/routers/strategy_operations.py",
    ],
)
def test_previously_broken_modules_have_no_undefined_names(module):
    """Each module that carried an F821 stays clean under the same selection."""
    assert (REPO_ROOT / module).is_file(), f"{module} is missing"

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-m", "flake8", module, f"--select={FLAKE8_SELECT}", "--show-source"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, (
        f"{module} carries an undefined name or syntax defect again:\n{result.stdout}\n{result.stderr}"
    )
