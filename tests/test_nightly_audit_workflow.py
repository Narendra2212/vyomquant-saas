"""
tests/test_nightly_audit_workflow.py

Unit tests verifying that:
1. .github/workflows/04-nightly-audit.yml incorporates pip-audit, npm audit, and cargo audit.
2. requirements.txt parses cleanly without encoding errors.
"""

import pathlib


def test_nightly_audit_workflow_contains_audit_steps():
    workflow_path = pathlib.Path(".github/workflows/04-nightly-audit.yml")
    assert workflow_path.exists(), ".github/workflows/04-nightly-audit.yml file missing"

    content = workflow_path.read_text(encoding="utf-8")

    assert "pip-audit" in content, "Nightly workflow missing pip-audit step"
    assert "npm audit" in content, "Nightly workflow missing npm audit step"
    assert "cargo audit" in content, "Nightly workflow missing cargo audit step"
    assert "pip-audit -r requirements-cpu.txt" in content, "Nightly workflow missing pip-audit command"
    assert "cd algo22-terminal" in content, "Nightly workflow missing npm audit path"
    assert "cd algo22-terminal/src-tauri" in content, "Nightly workflow missing cargo audit path"


def test_requirements_txt_ascii_comments():
    req_path = pathlib.Path("requirements.txt")
    assert req_path.exists(), "requirements.txt missing"

    content = req_path.read_text(encoding="utf-8")

    # Assert no non-ASCII box drawing characters remain in requirements.txt
    for line in content.splitlines():
        if line.startswith("#"):
            assert line.isascii(), f"Non-ASCII comment line found: {line}"


# ══════════════════════════════════════════════════════════════════════════════
#  SEC-01: the scheduled secret-history sweep
#
#  SEC-01's closing note: "The defect class was never one careless file; it was that
#  nothing watched for this." Rotation fixed the three credentials that had leaked; the
#  scheduled sweep is what notices a fourth.
#
#  These tests pin the three properties that decide whether the sweep CAN fail at all.
#  Each of them, if quietly changed, leaves a green check that proves nothing -- which is
#  worse than no check, because it is read as assurance.
# ══════════════════════════════════════════════════════════════════════════════

import yaml  # noqa: E402

WORKFLOW = pathlib.Path(".github/workflows/04-nightly-audit.yml")
SWEEP_JOB = "secret-history-sweep"
SWEEP_SCRIPT = "scripts/audit_secrets_in_git_history.py"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _sweep_job() -> dict:
    jobs = _workflow()["jobs"]
    assert SWEEP_JOB in jobs, (
        f"{SWEEP_JOB} is gone from the nightly workflow. SEC-01 rests on this running: "
        f"nothing else compares git history against the CURRENT value of every live "
        f"secret."
    )
    return jobs[SWEEP_JOB]


def _step_running(job: dict, needle: str) -> dict:
    for step in job["steps"]:
        if needle in str(step.get("run", "")):
            return step
    raise AssertionError(f"no step in {SWEEP_JOB} runs {needle!r}")


def test_the_sweep_is_scheduled_and_not_only_manual():
    """A control that runs only when someone remembers is not a control."""
    triggers = _workflow()[True] if True in _workflow() else _workflow()["on"]
    assert "schedule" in triggers, (
        "the nightly workflow lost its schedule, so the sweep would only ever run on "
        "workflow_dispatch -- which is the state SEC-01 was closed to get out of"
    )
    assert triggers["schedule"], "the schedule block is empty"


def test_the_sweep_job_exists_and_runs_the_script():
    _step_running(_sweep_job(), SWEEP_SCRIPT)


def test_the_sweep_checks_out_the_full_history():
    """THE LOAD-BEARING ONE.

    The script greps every commit. At the default ``fetch-depth: 1`` it would scan a
    single commit, find nothing, and report clean -- a green check that cannot fail,
    which for this particular question is the worst available outcome.
    """
    checkout = next(
        s for s in _sweep_job()["steps"] if "actions/checkout" in str(s.get("uses", ""))
    )
    assert str(checkout.get("with", {}).get("fetch-depth")) == "0", (
        "the sweep's checkout is not fetch-depth: 0, so it would scan a shallow clone "
        "and report clean regardless of what is in history"
    )


def test_the_sweep_refuses_to_run_against_a_shallow_clone():
    """Belt and braces: the job asserts at runtime what the test asserts statically."""
    guard = _step_running(_sweep_job(), "is-shallow-repository")
    assert "exit 1" in guard["run"], (
        "the shallow-clone guard must fail the job rather than warn"
    )


def test_the_sweep_is_blocking_rather_than_continue_on_error():
    """Every step in the audit job above is continue-on-error, which is right for a
    dependency report and wrong for "a live production credential is readable from git"."""
    step = _step_running(_sweep_job(), SWEEP_SCRIPT)
    assert step.get("continue-on-error") in (None, False), (
        "the sweep is continue-on-error, so a found credential would leave the run green"
    )


def test_the_sweep_has_aws_credentials_and_the_right_region():
    """The sweep's whole value is comparing history against the LIVE secret values; with
    no credentials it degrades to a scan that can only report coverage 0."""
    job = _sweep_job()
    creds = next(
        s for s in job["steps"]
        if "configure-aws-credentials" in str(s.get("uses", ""))
    )
    with_block = creds["with"]
    assert "AWS_ACCESS_KEY_ID" in str(with_block.get("aws-access-key-id"))
    assert "AWS_SECRET_ACCESS_KEY" in str(with_block.get("aws-secret-access-key"))
    assert job["env"]["AWS_REGION"] == "ap-southeast-1", (
        "the region must match where the secrets actually live. Measured this session: a "
        "default of us-east-1 made list-secrets return an unrelated account's secrets and "
        "ecs list-task-definition-families return empty, which read exactly like 'nothing "
        "is deployed'."
    )


def test_the_sweep_script_still_exists():
    assert pathlib.Path(SWEEP_SCRIPT).exists(), (
        f"{SWEEP_SCRIPT} is gone but the workflow still calls it; the job would fail on a "
        f"missing file rather than on a finding, which reads the same in a red run"
    )
