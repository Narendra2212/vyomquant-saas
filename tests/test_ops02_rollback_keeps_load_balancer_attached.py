"""OPS-02: "rollback" used to mean "detach the load balancer", not "revert the version".

infra/rollback.sh ran `aws ecs update-service --load-balancers "[]"` -- that DETACHES the
ALB from the ECS service entirely, an outage command mislabeled as a rollback. The fix
makes it look up the currently running task definition, compute the previous revision,
and switch the service to it via the same safe `update-service --task-definition <arn>`
pattern scripts/ecs_deploy_and_diagnose.py's own rollback_to_task_def() already uses
(deliberately without --load-balancers or --force-new-deployment).

No real AWS account is reachable from this sandbox, so this exercises the actual script
end to end against a fake `aws` executable on PATH that logs every invocation and returns
canned JSON for `ecs describe-services` -- a real subprocess run of real bash, not a
reimplementation of its logic in Python.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROLLBACK_SH = REPO / "infra" / "rollback.sh"

CURRENT_TASK_DEF_ARN = (
    "arn:aws:ecs:ap-southeast-1:273709947018:"
    "task-definition/vyomquant-api-production:146"
)

FAKE_AWS = """#!/usr/bin/env bash
# Fake `aws` for testing infra/rollback.sh: logs every invocation, answers the two
# subcommands the script calls, and fails anything else loudly.
echo "aws $*" >> "$FAKE_AWS_LOG"

if [ "$1" = "ecs" ] && [ "$2" = "describe-services" ]; then
    cat <<'JSON'
{"services": [{"taskDefinition": "%(arn)s"}]}
JSON
    exit 0
elif [ "$1" = "ecs" ] && [ "$2" = "update-service" ]; then
    exit 0
elif [ "$1" = "ecs" ] && [ "$2" = "wait" ]; then
    exit 0
fi

echo "fake aws: unexpected invocation: $*" >&2
exit 1
""" % {"arn": CURRENT_TASK_DEF_ARN}


@pytest.fixture()
def fake_aws_bin(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws_path = bin_dir / "aws"
    aws_path.write_text(FAKE_AWS, encoding="utf-8")
    aws_path.chmod(aws_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run_rollback(fake_aws_bin: Path, log_path: Path, confirm: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{fake_aws_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_AWS_LOG"] = str(log_path)
    # python3 must still resolve for the script's own JSON parsing.
    env["AWS_REGION"] = "ap-southeast-1"
    env["ECS_CLUSTER"] = "vyomquant-cluster"
    env["API_SERVICE"] = "vyomquant-api-service-cjema2sl"

    return subprocess.run(
        ["bash", str(ROLLBACK_SH)],
        input=f"{confirm}\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_rollback_targets_the_previous_revision_without_touching_the_load_balancer(
    fake_aws_bin, tmp_path
):
    log_path = tmp_path / "aws_calls.log"
    result = _run_rollback(fake_aws_bin, log_path, confirm="y")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = log_path.read_text(encoding="utf-8")

    update_calls = [line for line in calls.splitlines() if "update-service" in line]
    assert len(update_calls) == 1, f"expected exactly one update-service call, got: {calls}"
    update_call = update_calls[0]

    assert "--task-definition vyomquant-api-production:145" in update_call, (
        f"expected a rollback to revision 145 (one before the running 146), got: {update_call}"
    )
    assert "--load-balancers" not in update_call, (
        "infra/rollback.sh must never detach the load balancer -- that was the original "
        f"bug this finding (OPS-02) is about. Got: {update_call}"
    )
    assert "--force-new-deployment" not in update_call, (
        "a rollback must not pass --force-new-deployment -- it interferes with the native "
        f"ECS deployment circuit breaker, same as rollback_to_task_def()'s own reasoning. Got: {update_call}"
    )


def test_rollback_declined_at_the_confirmation_prompt_makes_no_aws_mutation(
    fake_aws_bin, tmp_path
):
    log_path = tmp_path / "aws_calls.log"
    result = _run_rollback(fake_aws_bin, log_path, confirm="n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CANCELLED" in result.stdout

    calls = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert "update-service" not in calls, (
        f"declining the confirmation prompt must not call update-service at all. Got: {calls}"
    )


def test_rollback_script_is_syntactically_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(ROLLBACK_SH)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_deploy_script_is_syntactically_valid_bash():
    deploy_sh = REPO / "infra" / "deploy.sh"
    result = subprocess.run(
        ["bash", "-n", str(deploy_sh)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_readiness_score_is_no_longer_a_hardcoded_literal():
    """OPS-02: infra/deploy.sh used to print a bare `echo "[SUMMARY] Readiness Score: 100%"`
    after every run, regardless of how many checks above it printed [WARN]. Asserted
    directly against the script's source for the same reason test_inf01's checks parse
    terraform text directly: no way to actually run this script against a real AWS account
    from this sandbox."""
    deploy_sh = REPO / "infra" / "deploy.sh"
    source = deploy_sh.read_text(encoding="utf-8")

    assert 'Readiness Score:  100%' not in source, (
        "infra/deploy.sh still hardcodes a 100% readiness score regardless of outcome"
    )
    assert "CHECKS_WARNED" in source, (
        "infra/deploy.sh should track how many post-deployment checks warned so the "
        "final readiness score reflects what actually happened"
    )
    assert "READINESS_SCORE=" in source
