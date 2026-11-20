"""DB-04: scripts/post_migration_validation.py's catalog-driven privilege-hardening
check existed, was tested, and was correct -- but nothing in any CI job, deploy script
or migration step ever called it against a real database. Confirmed this session by
grepping every .github/workflows/*.yml for a caller and finding none, and by running it
directly against a real, fully-provisioned Postgres replica (exit 0 once the
PUBLIC_CATALOG_TABLES allowlist was added; exit 1 without it, failing on
plans/plan_features/plan_limits/plan_prices/plan_migration_mapping -- global pricing-
catalog data with no customer identity, not the per-customer leak this check exists
to catch).

This pins the wiring itself: a static check of the workflow YAML, not a real-Postgres
run (that already exists, in the provisioning-and-isolation job this test reads --
running it twice would duplicate coverage this test is not meant to add)."""

from __future__ import annotations

import os

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOW_PATH = os.path.join(REPO, ".github", "workflows", "01-pr-check.yml")


def _provisioning_job():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        workflow = yaml.safe_load(f)
    job = workflow["jobs"].get("provisioning-and-isolation")
    assert job is not None, (
        "the 'provisioning-and-isolation' job (the one with a real postgres service "
        "and a full `provision_database.py --apply`) is gone or renamed -- this test "
        "needs repointing, not deleting"
    )
    return job


def _step_commands(job):
    return [step.get("run", "") for step in job.get("steps", [])]


def test_post_migration_validation_is_actually_invoked():
    commands = _step_commands(_provisioning_job())
    assert any("scripts/post_migration_validation.py" in cmd for cmd in commands), (
        "No step in provisioning-and-isolation runs scripts/post_migration_validation.py "
        "-- DB-04's own defect (written, tested, never called) has reappeared."
    )


def test_it_runs_after_the_real_schema_is_provisioned():
    """Calling it before `provision_database.py --apply` has run would check an empty
    or partially-provisioned database and either vacuously pass or fail for the wrong
    reason."""
    commands = _step_commands(_provisioning_job())
    apply_index = next(
        (i for i, cmd in enumerate(commands) if "provision_database.py" in cmd and "--apply" in cmd),
        None,
    )
    validation_index = next(
        (i for i, cmd in enumerate(commands) if "scripts/post_migration_validation.py" in cmd),
        None,
    )
    assert apply_index is not None, "no provisioning step found to order against"
    assert validation_index is not None, "no validation step found (see the other test in this file)"
    assert validation_index > apply_index, (
        "post_migration_validation.py runs before the schema is actually provisioned"
    )


def test_the_reports_directory_is_created_first():
    """validate_production_db() writes reports/post_reconciliation_metadata.json with a
    plain open() call -- no os.makedirs. A CI runner with no reports/ directory yet
    would fail on FileNotFoundError, not on anything this script is meant to catch."""
    commands = _step_commands(_provisioning_job())
    validation_cmd = next(
        (cmd for cmd in commands if "scripts/post_migration_validation.py" in cmd), None
    )
    assert validation_cmd is not None
    assert "mkdir -p reports" in validation_cmd or "mkdir reports" in validation_cmd
