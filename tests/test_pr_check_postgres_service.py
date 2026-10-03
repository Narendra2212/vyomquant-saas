"""tests/test_pr_check_postgres_service.py

The PR check has a real PostgreSQL, and it has it in a job that cannot change the
SQLite lane.

THE GAP THIS CLOSES (production-launch-hardening task 13.21, defect B)
----------------------------------------------------------------------
``01-pr-check.yml``'s ``unit-tests`` job had a ``services:`` block containing
``redis:7-alpine`` and no PostgreSQL. Three test files reach a database through
``backend_app.core.database.SessionLocal`` and therefore could not run anywhere; task
13.13 carried them as environment gaps. One of them,
``tests/test_transaction_isolation_serializable.py``, is the test that would have caught
defect A -- the SERIALIZABLE isolation control that had never executed, because it was
issued as a raw SQL string SQLAlchemy 2.x rejects and the rejection was swallowed into a
``logger.warning``.

WHY THESE ASSERTIONS AND NOT A GREEN RUN
----------------------------------------
GitHub Actions cannot be driven from this environment, so **the next CI run is the real
verification** and nothing in this file claims otherwise. What is checkable here is the
same thing ``tests/test_websocket_upgrade_gate.py`` and
``tests/test_security_gate_blocks_deploy.py`` check about their own workflows: that the
file parses, and that the wiring the job depends on is present and says what it is
supposed to say. The most important assertion in the file is the negative one --
``unit-tests`` must still have no ``DATABASE_URL`` -- because that is what keeps the
11,000+ tests that pass against SQLite passing against SQLite.
"""

from __future__ import annotations

import os

import pytest
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PR_CHECK_YML = os.path.join(REPO, ".github", "workflows", "01-pr-check.yml")

CANCELLATION_FILE = "tests/test_atomic_order_cancellation_fix.py"
GLOBAL_SERIALIZABLE_FILE = "tests/test_transaction_isolation_serializable.py"


@pytest.fixture(scope="module")
def workflow():
    with open(PR_CHECK_YML, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def raw():
    with open(PR_CHECK_YML, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def db_job(workflow):
    assert "database-tests" in workflow["jobs"], (
        "the PR check has no job providing a PostgreSQL service"
    )
    return workflow["jobs"]["database-tests"]


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE SERVICE EXISTS
# ══════════════════════════════════════════════════════════════════════════════


def test_the_workflow_parses(workflow):
    assert isinstance(workflow, dict)
    assert "jobs" in workflow


def test_a_postgres_service_container_is_declared(db_job):
    services = db_job.get("services") or {}
    assert "postgres" in services, f"no postgres service; services are {list(services)}"


def test_the_postgres_image_is_pinned_to_a_major_version(db_job):
    """Production reports ``server_version 17.6``. An unpinned ``postgres:latest`` would
    let the lane drift to a different major than the one being tested against.
    """
    image = db_job["services"]["postgres"]["image"]
    assert image.startswith("postgres:"), image
    tag = image.split(":", 1)[1]
    assert tag != "latest", "the postgres image tag must be pinned, not 'latest'"
    assert tag.split("-")[0] == "17", (
        f"production is PostgreSQL 17.x; the service is pinned to {tag}"
    )


def test_the_service_is_health_checked_before_the_steps_run(db_job):
    """Without a health gate the first step races the server's startup, which fails as a
    connection error that looks like a test failure.
    """
    options = db_job["services"]["postgres"]["options"]
    assert "--health-cmd" in options
    assert "pg_isready" in options
    assert "--health-retries" in options


def test_the_service_port_is_published_to_the_runner(db_job):
    assert "5432:5432" in db_job["services"]["postgres"]["ports"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. DATABASE_URL IS WIRED TO IT -- AND ONLY TO IT
# ══════════════════════════════════════════════════════════════════════════════


def test_database_url_points_at_the_service(db_job):
    url = db_job["env"]["DATABASE_URL"]
    assert url.startswith("postgresql://"), url
    assert "localhost:5432" in url or "127.0.0.1:5432" in url, url


def test_the_conftest_opt_in_is_also_set(db_job):
    """``tests/conftest.py`` blanks ``DATABASE_URL`` at import unless
    ``AERORA_TEST_DATABASE_URL`` is set. Setting only ``DATABASE_URL`` would provision
    the schema with alembic and then run the tests against SQLite anyway -- which is the
    second half of why these files could not run even where a database existed.
    """
    assert db_job["env"]["AERORA_TEST_DATABASE_URL"] == db_job["env"]["DATABASE_URL"]


def test_the_conftest_still_blanks_database_url_by_default():
    """The counterpart assertion in the conftest itself. The default must stay "blank
    it", or every one of the 11,000+ tests becomes sensitive to whatever DATABASE_URL
    happens to be in the developer's environment.
    """
    with open(os.path.join(REPO, "tests", "conftest.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert 'os.environ.get("AERORA_TEST_DATABASE_URL", "")' in source
    assert 'os.environ["DATABASE_URL"] = _EXPLICIT_TEST_DATABASE_URL' in source


def test_the_url_is_not_a_remote_server(db_job):
    """The one thing this job must never do is point at a real deployment:
    ``test_atomic_order_cancellation_fix.py`` INSERTs and UPDATEs ``orders`` rows, so a
    run against production would write test orders into it. Task 13.11 says so in as many
    words and this is that warning turned into an assertion.
    """
    url = db_job["env"]["DATABASE_URL"]
    for forbidden in ("supabase", "amazonaws", "rds.", "${{"):
        assert forbidden not in url, (
            f"DATABASE_URL in the database-tests job references {forbidden!r}: {url}"
        )


def test_the_unit_tests_job_still_has_no_database_url(workflow):
    """THE LOAD-BEARING NEGATIVE ASSERTION.

    The 11,000+ tests in ``unit-tests`` pass against the SQLite fallback
    ``core/database_pool.py`` chooses when DATABASE_URL is unset. A DATABASE_URL anywhere
    in that job -- job level or step level -- would re-point every ``SessionLocal()`` in
    the suite at a real server in one move. That is a much larger change than task 13.21
    and is exactly why the PostgreSQL lives in its own job.
    """
    unit_tests = yaml.dump(workflow["jobs"]["unit-tests"])
    for name in ("DATABASE_URL", "AERORA_TEST_DATABASE_URL"):
        assert name not in unit_tests, (
            f"the unit-tests job now carries a {name}; the SQLite lane is no longer "
            "the SQLite lane"
        )


def test_the_unit_tests_job_keeps_its_redis_service(workflow):
    """Adding a job must not have disturbed the one that was already there."""
    assert "redis" in workflow["jobs"]["unit-tests"]["services"]


def test_the_two_jobs_are_independent(workflow):
    """Neither waits on the other, so a PostgreSQL-only failure cannot mask or delay the
    SQLite lane's result, and both report separately.
    """
    db_needs = workflow["jobs"]["database-tests"]["needs"]
    unit_needs = workflow["jobs"]["unit-tests"]["needs"]
    db_needs = [db_needs] if isinstance(db_needs, str) else list(db_needs)
    unit_needs = [unit_needs] if isinstance(unit_needs, str) else list(unit_needs)
    assert "unit-tests" not in db_needs
    assert "database-tests" not in unit_needs


# ══════════════════════════════════════════════════════════════════════════════
# 3. THE SCHEMA IS PROVISIONED, AND STOPS WHERE IT HAS TO
# ══════════════════════════════════════════════════════════════════════════════


def _step(job, name):
    matches = [s for s in job["steps"] if s.get("name") == name]
    assert matches, f"no step named {name!r}; steps are {[s.get('name') for s in job['steps']]}"
    return matches[0]


def test_the_schema_is_provisioned_from_the_repositorys_own_migrations(db_job):
    run = _step(db_job, "Provision schema")["run"]
    assert "alembic upgrade" in run, run


def test_provisioning_stops_at_the_branchpoint_and_not_at_head(db_job):
    """``alembic upgrade head`` cannot provision a fresh database, and that was measured
    rather than assumed. ``add_foreign_keys_20260817.py`` adds
    ``FOREIGN KEY (tenant_id) REFERENCES profiles(id)``, and ``profiles`` has no
    ``CREATE TABLE`` anywhere in this repository. Its per-statement
    ``try/except Exception`` does not rescue it: PostgreSQL aborts the entire transaction
    on the first failed statement, so everything after the swallow -- including alembic's
    own ``UPDATE alembic_version`` -- fails with ``InFailedSqlTransaction``.

    ``d97ffff9c3bb`` is the revision that creates ``orders`` and is also the branchpoint,
    so the path to it from base is linear.
    """
    run = _step(db_job, "Provision schema")["run"]
    assert "d97ffff9c3bb" in run, run
    assert "upgrade head" not in run, (
        "provisioning runs `alembic upgrade head`, which cannot complete on an empty "
        "database -- see this test's docstring"
    )


def test_the_baseline_alone_would_provision_nothing(db_job):
    """A guard on the reasoning above rather than on the workflow: whoever edits the
    revision in that step next should know that ``4ef23035a692``'s ``upgrade()`` contains
    only DROPs -- its ``CREATE TABLE`` calls are in ``downgrade()`` -- so stopping one
    revision earlier would leave no ``orders`` table at all.
    """
    baseline = os.path.join(
        REPO, "backend_app", "alembic", "versions", "4ef23035a692_baseline.py"
    )
    with open(baseline, encoding="utf-8") as handle:
        source = handle.read()
    upgrade_body = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "create_table" not in upgrade_body
    assert "DROP TABLE IF EXISTS orders" in upgrade_body


# ══════════════════════════════════════════════════════════════════════════════
# 4. WHAT THE JOB RUNS, AND THE TWO OMISSIONS NAMED RATHER THAN HIDDEN
# ══════════════════════════════════════════════════════════════════════════════


def test_the_blocked_cancellation_tests_now_run(db_job):
    run = _step(db_job, "Run database-backed tests")["run"]
    assert CANCELLATION_FILE in run, run


def test_the_postgres_only_isolation_proof_runs(db_job):
    run = _step(db_job, "Run database-backed tests")["run"]
    assert "tests/test_database_isolation_level_postgres.py" in run, run


def test_the_global_serializable_assertion_is_deselected_not_rewritten(db_job, raw):
    """``test_transaction_isolation_for_cancellation`` asserts that the plain, general
    ``SessionLocal()`` reports ``serializable``, which contradicts
    ``core/database.py``'s own design (FINANCIAL is SERIALIZABLE, GENERAL and READ_ONLY
    are READ COMMITTED). It is deselected, and the assertion in the test file is left
    exactly as it was written. Deselecting states the conflict; editing the assertion
    would erase it.
    """
    run = _step(db_job, "Run database-backed tests")["run"]
    assert "--deselect" in run
    assert f"{CANCELLATION_FILE}::test_transaction_isolation_for_cancellation" in run

    with open(os.path.join(REPO, CANCELLATION_FILE), encoding="utf-8") as handle:
        source = handle.read()
    assert "assert 'serializable' in isolation_lower" in source, (
        "the deselected test's assertion was changed; it must be preserved verbatim so "
        "the conflict stays visible"
    )


def test_the_global_serializable_file_is_not_silently_selected(db_job):
    """All three of ``test_transaction_isolation_serializable.py``'s tests make the same
    global-SERIALIZABLE assertion, so selecting the file would red the job for a design
    disagreement rather than a defect. It is left out on purpose and recorded in task
    13.21 as an open decision -- not relaxed, and not quietly passing.
    """
    run = _step(db_job, "Run database-backed tests")["run"]
    assert GLOBAL_SERIALIZABLE_FILE not in run


def test_the_omissions_are_explained_in_the_workflow_itself(raw):
    """A deselect with no reason beside it becomes a weakened assertion the next time
    someone reads it in a hurry.
    """
    assert "40001" in raw, (
        "the workflow does not say why the general session is not SERIALIZABLE"
    )
    assert "paper_repository" in raw
    assert GLOBAL_SERIALIZABLE_FILE in raw, (
        "the workflow does not name the file it deliberately does not run"
    )


def test_the_job_is_bounded(db_job):
    assert isinstance(db_job.get("timeout-minutes"), int)
    assert db_job["timeout-minutes"] <= 60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
