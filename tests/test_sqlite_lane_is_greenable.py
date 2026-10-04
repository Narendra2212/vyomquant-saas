"""
tests/test_sqlite_lane_is_greenable.py

The gating CI lane cannot pass.

``.github/workflows/01-pr-check.yml`` has two python test jobs. ``unit-tests`` runs
``pytest tests/ -v --tb=short -k "not chaos and not load"`` with no ``DATABASE_URL``,
deliberately, so every ``SessionLocal()`` in the suite resolves to the
``sqlite:///./algo22.db`` fallback -- the workflow says so in a block comment and calls
it load-bearing. ``database-tests`` runs a named handful of files against a real
``postgres:17-alpine``.

Three modules execute PostgreSQL-only SQL through ``SessionLocal()`` with no guard, so
they are collected by the SQLite lane and fail there unconditionally -- eight failures,
every run, on every branch:

  tests/test_atomic_order_cancellation_fix.py      4 failures
      ``INSERT ... NOW()`` into a table the module never creates, then
      ``UPDATE ... WHERE (data->>'status') IS DISTINCT FROM 'cancelled'``.
      On SQLite: ``no such table: orders``.
  tests/test_position_delta_race_condition_fix.py  1 failure
      ``SELECT * FROM positions WHERE ... FOR UPDATE``.
      On SQLite: ``near "FOR": syntax error``.
  tests/test_transaction_isolation_serializable.py 3 failures
      ``SHOW transaction_isolation``.
      On SQLite: ``near "SHOW": syntax error``.

The ``unit-tests`` job has no ``continue-on-error`` and ``pytest.ini`` has no ``--ignore``
or ``--deselect``, so there is no mechanism by which that job reports success. The
workflow's own comment -- "The 11,000+ tests that pass today pass against SQLite" -- is
not true of this lane as configured.

WHY A SKIP IS THE FIX AND NOT A REWRITE. These three files are asserting on PostgreSQL
semantics on purpose: ``FOR UPDATE`` row locks, ``SHOW transaction_isolation``, and
``data->>`` on a ``JSON`` column. SQLite has no row locks and no isolation level to
report. Rewriting them to run on SQLite would delete the thing each one exists to check.
The repository already settled on the alternative for exactly this situation:
``tests/test_database_isolation_level_postgres.py`` carries a module-level ``skipif`` on
``DATABASE_URL`` and documents it as "skipped in the SQLite lane". That is the pattern
applied here.

WHY THE SKIP ALONE WOULD BE A REGRESSION, AND WHAT STOPS IT. Two of the three files are
not named in the ``database-tests`` job, so a skip on its own would convert a loud
failure into a silent pass and leave the FIN-CRITICAL-002 row-locking control and the
FIN-CRITICAL-001 isolation-level control verified by nothing at all. That is a worse
state than a red lane, not a better one. ``test_server_requiring_modules_run_somewhere``
below is what makes the trade honest: it fails if a module is skipped out of the SQLite
lane without also being named in the PostgreSQL job.

TWO THINGS ARE DELIBERATELY EXEMPT, AND BOTH FOR THE SAME REASON. The workflow declines
to run ``tests/test_transaction_isolation_serializable.py`` at all, and ``--deselect``-s
``test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation``,
because each asserts that the general-purpose ``SessionLocal()`` reports
``serializable``. ``core/database.py`` says the opposite by design -- FINANCIAL is
SERIALIZABLE, GENERAL and READ_ONLY are READ COMMITTED -- and only
``backend/paper/paper_repository.py`` retries on PostgreSQL's ``40001``
``serialization_failure``, so flipping the shared engine would convert a dormant control
into live ``could not serialize access`` errors. That is an open design decision, which
the workflow records; it is not a missing server. Adding a skip guard means these now run
in neither job, so both gaps are asserted here --
``test_the_global_serializable_module_is_parked_not_hidden`` and
``test_the_deselected_cancellation_isolation_test_runs_nowhere`` -- and
``test_every_server_requiring_module_is_classified`` makes it impossible to add a guard
to a third module without deciding which of the two buckets it belongs in.

A SECOND DEFECT IN THE SAME FILE. ``test_position_delta_race_condition_fix.py``'s
``test_concurrent_position_update_safety`` collected every exception from its five
worker threads into a list, printed a count, and asserted nothing. On SQLite all five
failed with ``near "FOR": syntax error`` and the test reported success -- it would have
passed against a database with no ``positions`` table and no row locking at all.
Guarding it without repairing it would have moved a test that proves nothing from one
job to another, so it now asserts that no worker failed.
``test_the_repaired_concurrency_test_still_asserts`` holds that.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "01-pr-check.yml"

# The modules that reach a real server through SessionLocal() and cannot run on SQLite.
# Keyed by path, valued by the number of failures each contributed to the SQLite lane
# before the guard was added, so the docstring's arithmetic stays checkable.
SERVER_REQUIRING_MODULES = {
    "tests/test_atomic_order_cancellation_fix.py": 4,
    "tests/test_position_delta_race_condition_fix.py": 1,
    "tests/test_transaction_isolation_serializable.py": 3,
}

# Of those, the ones that must be named in the `database-tests` job: skipping them out of
# the SQLite lane is only legitimate because they run against a real server there.
MUST_RUN_ON_POSTGRES = (
    "tests/test_atomic_order_cancellation_fix.py",
    "tests/test_position_delta_race_condition_fix.py",
)

# Already guarded before this file existed; included so the guard-shape assertion has a
# known-good reference and regressions in the house pattern are caught too.
ALREADY_GUARDED = (
    "tests/test_database_isolation_level_postgres.py",
    "tests/test_strategy_lifecycle_concurrency_postgres.py",
)

DESELECTED_FROM_POSTGRES_JOB = (
    "tests/test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation"
)

# Runs in neither job, on purpose, because its assertion is the open question rather than
# a settled expectation. Every test in this module asserts that the general-purpose
# `SessionLocal()` reports `serializable`. `core/database.py` says the opposite by design
# -- FINANCIAL is SERIALIZABLE, GENERAL and READ_ONLY are READ COMMITTED -- and only
# `backend/paper/paper_repository.py` retries on PostgreSQL's `40001`
# serialization_failure, so flipping the shared engine would convert a dormant control
# into live `could not serialize access` errors across the app. The workflow records this
# as an open decision and declines to select the file; adding it to the PostgreSQL job
# would red that lane on an unresolved design question rather than on a regression.
PARKED_ON_OPEN_DESIGN_DECISION = "tests/test_transaction_isolation_serializable.py"


def _module_source(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"{rel} does not exist; update SERVER_REQUIRING_MODULES"
    return path.read_text(encoding="utf-8")


def _postgres_skip_guard_names(source: str) -> set[str]:
    """Names assigned at module level to a ``pytest.mark.skipif`` mentioning postgres.

    The repository uses the guard in two shapes and both are legitimate:

      ``pytestmark = pytest.mark.skipif(...)``
          every test in the module needs the server
          (``test_database_isolation_level_postgres.py``)
      ``requires_postgres = pytest.mark.skipif(...)`` applied per test
          the module mixes server tests with source-level tests that must keep
          running in the SQLite lane
          (``test_strategy_lifecycle_concurrency_postgres.py``)

    Parsed rather than grepped, and restricted to ``tree.body``, so a ``skipif`` built
    inside a function body or the word postgres in a docstring cannot satisfy it.
    """
    names: set[str] = set()
    for node in ast.parse(source).body:  # module level only -- not ast.walk
        if not isinstance(node, ast.Assign):
            continue
        rendered = ast.dump(node.value)
        if "skipif" not in rendered or "postgres" not in rendered.lower():
            continue
        names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _guards_every_test_in_the_module(source: str) -> bool:
    """True when no test function in the module can run without PostgreSQL."""
    return "pytestmark" in _postgres_skip_guard_names(source)


def _has_a_postgres_skip_guard(source: str) -> bool:
    """True when the module has a PostgreSQL guard in either supported shape.

    For the per-test shape the guard name must actually decorate something, so an
    assigned-but-unused marker does not count.
    """
    names = _postgres_skip_guard_names(source)
    if not names:
        return False
    if "pytestmark" in names:
        return True
    decorators = {
        node.id
        for fn in ast.walk(ast.parse(source))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in fn.decorator_list
        if isinstance(node, ast.Name)
    }
    return bool(names & decorators)


@pytest.mark.parametrize("rel", sorted(SERVER_REQUIRING_MODULES))
def test_server_requiring_module_skips_itself_off_postgres(rel: str) -> None:
    """Each server-requiring module carries a PostgreSQL skip guard.

    Without this the module is collected by the ``unit-tests`` job, which has no
    DATABASE_URL by design, and fails on SQLite syntax.
    """
    assert _has_a_postgres_skip_guard(_module_source(rel)), (
        f"{rel} executes PostgreSQL-only SQL through SessionLocal() but has no "
        f"module-level `pytest.mark.skipif(...)` keyed on DATABASE_URL naming a "
        f"PostgreSQL server. The `unit-tests` job in {WORKFLOW.name} runs "
        f"`pytest tests/` against the SQLite fallback and this module fails there "
        f"unconditionally ({SERVER_REQUIRING_MODULES[rel]} failures)."
    )


@pytest.mark.parametrize("rel", sorted(ALREADY_GUARDED))
def test_the_house_guard_pattern_is_intact(rel: str) -> None:
    """The two modules that were already guarded still are.

    These are the reference for the pattern the test above requires. If the pattern
    changes here, the requirement above is measuring something the repository no longer
    does.
    """
    assert _has_a_postgres_skip_guard(_module_source(rel)), (
        f"{rel} was a reference implementation of the PostgreSQL skip guard and no "
        f"longer has one"
    )


@pytest.mark.parametrize("rel", sorted(MUST_RUN_ON_POSTGRES))
def test_server_requiring_modules_run_somewhere(rel: str) -> None:
    """A module skipped out of the SQLite lane must be named in the PostgreSQL job.

    This is the assertion that stops the skip guard from being a way to hide failures. A
    module that skips in ``unit-tests`` and is absent from ``database-tests`` is verified
    by nothing. The one module deliberately exempt from this is handled by
    ``test_the_global_serializable_module_is_parked_not_hidden``.
    """
    assert WORKFLOW.is_file(), f"{WORKFLOW} not found"
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert rel in workflow, (
        f"{rel} skips itself in the SQLite `unit-tests` lane but is not named anywhere "
        f"in {WORKFLOW.name}, so it runs in no CI job at all. Add it to the "
        f"`database-tests` job's pytest invocation."
    )


def test_every_server_requiring_module_is_classified() -> None:
    """No module may be guarded without a decision about where it runs.

    Guards the bookkeeping: each entry in ``SERVER_REQUIRING_MODULES`` is either required
    to run on PostgreSQL or explicitly parked. A module added to the first list without
    being added to one of the second two would skip everywhere unnoticed, which is the
    failure mode this whole file exists to prevent.
    """
    classified = set(MUST_RUN_ON_POSTGRES) | {PARKED_ON_OPEN_DESIGN_DECISION}
    unclassified = set(SERVER_REQUIRING_MODULES) - classified
    assert not unclassified, (
        f"these modules carry a PostgreSQL skip guard but are neither named in the "
        f"database-tests job nor recorded as parked, so they run nowhere: "
        f"{sorted(unclassified)}"
    )


def test_the_global_serializable_module_is_parked_not_hidden() -> None:
    """Pins the one whole module that the skip guard leaves with no home.

    ``test_transaction_isolation_serializable.py`` asserts that the general-purpose
    session reports ``serializable``, which contradicts ``core/database.py``'s stated
    design. The workflow declines to select it for that reason, not for want of a server.
    With a skip guard added it no longer runs in the SQLite lane either, so it runs
    nowhere -- recorded here deliberately. If someone adds it to the PostgreSQL job this
    test fails, which is the prompt to confirm the design decision was actually made.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    db_job = workflow.split("- name: Run database-backed tests", 1)
    assert len(db_job) == 2, "could not locate the database-tests pytest step"
    invocation = db_job[1].split("docker-check:", 1)[0]
    assert PARKED_ON_OPEN_DESIGN_DECISION not in invocation, (
        f"{PARKED_ON_OPEN_DESIGN_DECISION} is now selected by the database-tests job. "
        f"Every test in it asserts the general session is SERIALIZABLE, which "
        f"core/database.py contradicts by design. If that design decision has been "
        f"resolved, delete this test and the PARKED_ON_OPEN_DESIGN_DECISION entry; if "
        f"not, the job will red on an open question."
    )
    assert _guards_every_test_in_the_module(
        _module_source(PARKED_ON_OPEN_DESIGN_DECISION)
    ), "the module-level skip this recorded gap depends on is gone"


def test_the_deselected_cancellation_isolation_test_runs_nowhere() -> None:
    """Pins the one individual test the skip guard leaves with no home.

    The workflow ``--deselect``-s this test from the PostgreSQL job for the same
    global-SERIALIZABLE reason. Adding a module-level skip to its file means it is no
    longer run by the SQLite job either. This test asserts that state deliberately: it is
    a recorded gap, not an accident, and if someone removes the deselect this test fails
    and the note gets revisited.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert DESELECTED_FROM_POSTGRES_JOB in workflow, (
        "expected the workflow to still --deselect "
        f"{DESELECTED_FROM_POSTGRES_JOB}; if that deselect was removed, this recorded "
        "coverage gap is closed and this test should be deleted"
    )
    assert _guards_every_test_in_the_module(
        _module_source("tests/test_atomic_order_cancellation_fix.py")
    ), "the module-level skip this gap depends on is gone"


def test_the_repaired_concurrency_test_still_asserts() -> None:
    """``test_concurrent_position_update_safety`` must contain real assertions.

    It had none: five worker threads, every exception swallowed into a list, a printed
    count, and no ``assert``. That is why it passed on SQLite while all five workers were
    failing on ``near "FOR": syntax error``. Moving a test that cannot fail from the
    SQLite lane into the PostgreSQL job would have been pure motion, so the repair and
    the guard went in together and this holds the repair in place.
    """
    source = _module_source("tests/test_position_delta_race_condition_fix.py")
    tree = ast.parse(source)
    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "test_concurrent_position_update_safety"
        ),
        None,
    )
    assert target is not None, (
        "test_concurrent_position_update_safety is gone; if it was deleted rather than "
        "repaired, delete this test too"
    )
    asserts = [n for n in ast.walk(target) if isinstance(n, ast.Assert)]
    assert asserts, (
        "test_concurrent_position_update_safety contains no assert statement. It "
        "collects worker exceptions into a list and prints a count, so it reports "
        "success even when every worker failed. It must assert that no worker failed."
    )


def test_the_sqlite_lane_reports_no_failures_for_these_modules() -> None:
    """The property itself: on SQLite these modules produce zero failures.

    The three assertions above check the mechanism. This one checks the outcome, by
    running the modules the way the ``unit-tests`` job does -- no DATABASE_URL, no
    AERORA_TEST_DATABASE_URL -- and requiring pytest to report no failures. Skips are
    acceptable; failures and errors are not.

    Run in a subprocess because the guard under test is evaluated at module import, and
    this module has already been imported by the time any in-process check could look.
    """
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("AERORA_TEST_DATABASE_URL", None)
    env["PYTHONPATH"] = str(REPO_ROOT)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *SERVER_REQUIRING_MODULES,
            "-p",
            "no:cacheprovider",
            "--tb=no",
            "-q",
            "--no-header",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = "\n".join(
        (completed.stdout or "").strip().splitlines()[-15:]
    )
    assert " failed" not in (completed.stdout or ""), (
        "the SQLite lane still reports failures for the server-requiring modules; "
        f"pytest exited {completed.returncode}\n{tail}"
    )
    assert "error" not in (completed.stdout or "").lower().replace(
        "errors.pydantic.dev", ""
    ), (
        "the SQLite lane reports collection or execution errors for the "
        f"server-requiring modules; pytest exited {completed.returncode}\n{tail}"
    )
