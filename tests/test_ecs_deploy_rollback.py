"""
tests/test_ecs_deploy_rollback.py

Unit tests verifying automated ECS deployment rollback logic and Terraform configuration.

Rollout-classification coverage was added after the 2026-09-29 incident in which two
concurrent pipeline runs each reported "DEPLOYMENT FAILED + ROLLBACK FAILED" while every
ECS task was HEALTHY with exitCode=0. The deploy engine now tracks the specific
deployment id it created so it can tell "still rolling out" and "someone else took over"
apart from a genuine failure.
"""

import json
import pytest
from unittest.mock import patch

from scripts.ecs_deploy_and_diagnose import (
    ROLLOUT_COMPLETED,
    ROLLOUT_FAILED,
    ROLLOUT_IN_PROGRESS,
    ROLLOUT_SUPERSEDED,
    ROLLOUT_TIMEOUT,
    classify_rollout,
    deploy_new_image,
    execute_deployment,
    extract_deployment_id,
    get_current_task_definition_arn,
    rollback_to_task_def,
)


def _service(
    deployments,
    desired=1,
    running=1,
    pending=0,
):
    return {
        "desiredCount": desired,
        "runningCount": running,
        "pendingCount": pending,
        "deployments": deployments,
    }


def _deployment(
    dep_id,
    status="PRIMARY",
    rollout_state="COMPLETED",
    task_def="arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-api:170",
    failed_tasks=0,
    reason="deployment completed.",
):
    return {
        "id": dep_id,
        "status": status,
        "rolloutState": rollout_state,
        "rolloutStateReason": reason,
        "taskDefinition": task_def,
        "failedTasks": failed_tasks,
    }


class TestECSDeployRollback:
    def test_get_current_task_definition_arn_success(self):
        mock_output = json.dumps({
            "services": [
                {
                    "serviceName": "vyomquant-api-service",
                    "taskDefinition": "arn:aws:ecs:ap-southeast-1:123456789012:task-definition/vyomquant-api:45"
                }
            ]
        })
        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(0, mock_output, "")):
            arn = get_current_task_definition_arn()
            assert arn == "arn:aws:ecs:ap-southeast-1:123456789012:task-definition/vyomquant-api:45"

    def test_get_current_task_definition_arn_empty_service(self):
        mock_output = json.dumps({"services": []})
        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(0, mock_output, "")):
            arn = get_current_task_definition_arn()
            assert arn == ""

    @patch("scripts.ecs_deploy_and_diagnose.wait_for_idle_service")
    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_rollout")
    def test_execute_deployment_success_flow(
        self, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev, mock_idle
    ):
        mock_idle.return_value = (True, "service idle (COMPLETED)")
        mock_get_prev.return_value = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:10"
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_deploy.return_value = (True, "Updated service")
        mock_wait.return_value = ROLLOUT_COMPLETED

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v2")
        assert result is True
        # deploy_new_image called once with new ARN; rollback never called
        mock_deploy.assert_called_once_with("arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_rollback.assert_not_called()

    @patch("scripts.ecs_deploy_and_diagnose.wait_for_idle_service")
    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_rollout")
    @patch("scripts.ecs_deploy_and_diagnose.collect_diagnostics")
    def test_execute_deployment_rollback_flow_on_unstable(
        self, mock_diag, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev, mock_idle
    ):
        prev_arn = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:10"
        new_arn = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11"
        mock_idle.return_value = (True, "service idle (COMPLETED)")
        mock_get_prev.return_value = prev_arn
        mock_register.return_value = (True, new_arn)
        mock_deploy.return_value = (True, "Updated to 11")
        # First wait (forward deploy) times out; second (rollback) completes
        mock_wait.side_effect = [ROLLOUT_TIMEOUT, ROLLOUT_COMPLETED]
        mock_rollback.return_value = (True, "Rolled back to 10")
        mock_diag.return_value = {"cloudwatch_logs": ["Crash log"]}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:broken")
        assert result is False
        # deploy_new_image called once; rollback_to_task_def called once with the previous ARN
        mock_deploy.assert_called_once_with(new_arn)
        mock_rollback.assert_called_once_with(prev_arn)

    @patch("scripts.ecs_deploy_and_diagnose.wait_for_idle_service")
    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_rollout")
    @patch("scripts.ecs_deploy_and_diagnose.collect_diagnostics")
    def test_execute_deployment_initial_deployment_failure_no_rollback(
        self, mock_diag, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev, mock_idle
    ):
        # No previous task definition ARN (first ever deployment)
        mock_idle.return_value = (True, "service idle (COMPLETED)")
        mock_get_prev.return_value = ""
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:1")
        mock_deploy.return_value = (True, "Updated")
        mock_wait.return_value = ROLLOUT_TIMEOUT
        mock_diag.return_value = {"cloudwatch_logs": []}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v1")
        assert result is False
        # deploy_new_image called once; rollback must NOT be called
        mock_deploy.assert_called_once()
        mock_rollback.assert_not_called()

    @patch("scripts.ecs_deploy_and_diagnose.wait_for_idle_service")
    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_rollout")
    @patch("scripts.ecs_deploy_and_diagnose.collect_diagnostics")
    def test_superseded_rollout_does_not_trigger_rollback(
        self, mock_diag, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev, mock_idle
    ):
        """Regression guard for the 2026-09-29 double-failure incident.

        When a concurrent run takes over the service, rolling back would clobber a
        possibly-newer deployment and restart the fight. The engine must report
        failure WITHOUT reverting anything.
        """
        mock_idle.return_value = (True, "service idle (COMPLETED)")
        mock_get_prev.return_value = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:169"
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:170")
        mock_deploy.return_value = (True, "Updated to 170")
        mock_wait.return_value = ROLLOUT_SUPERSEDED
        mock_diag.return_value = {"cloudwatch_logs": []}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v3")
        assert result is False
        mock_rollback.assert_not_called()

    @patch("scripts.ecs_deploy_and_diagnose.wait_for_idle_service")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    def test_concurrent_rollout_aborts_before_touching_service(
        self, mock_deploy, mock_register, mock_idle
    ):
        """If a rollout is already in flight, do not register or update anything."""
        mock_idle.return_value = (False, "a pre-existing rollout was still in progress")

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v4")
        assert result is False
        mock_register.assert_not_called()
        mock_deploy.assert_not_called()


class TestRolloutClassification:
    def test_completed_when_single_deployment_at_capacity(self):
        svc = _service([_deployment("ecs-svc/1")])
        state, _detail = classify_rollout(svc, "ecs-svc/1")
        assert state == ROLLOUT_COMPLETED

    def test_in_progress_while_old_deployment_still_draining(self):
        """The real 315s rollout profile: new deployment COMPLETED but old one draining."""
        svc = _service([
            _deployment("ecs-svc/new"),
            _deployment("ecs-svc/old", status="ACTIVE", rollout_state="COMPLETED"),
        ])
        state, detail = classify_rollout(svc, "ecs-svc/new")
        assert state == ROLLOUT_IN_PROGRESS
        assert "draining" in detail

    def test_in_progress_while_task_not_yet_running(self):
        svc = _service([_deployment("ecs-svc/new", rollout_state="IN_PROGRESS")], running=0, pending=1)
        state, _detail = classify_rollout(svc, "ecs-svc/new")
        assert state == ROLLOUT_IN_PROGRESS

    def test_superseded_when_another_deployment_becomes_primary(self):
        svc = _service([_deployment("ecs-svc/theirs")])
        state, detail = classify_rollout(svc, "ecs-svc/ours")
        assert state == ROLLOUT_SUPERSEDED
        assert "ecs-svc/theirs" in detail

    def test_failed_when_circuit_breaker_trips(self):
        svc = _service(
            [_deployment("ecs-svc/1", rollout_state="FAILED", failed_tasks=3, reason="tasks failed")],
            running=0,
        )
        state, detail = classify_rollout(svc, "ecs-svc/1")
        assert state == ROLLOUT_FAILED
        assert "circuit breaker" in detail

    def test_no_deployment_id_falls_back_to_primary(self):
        svc = _service([_deployment("ecs-svc/whatever")])
        state, _detail = classify_rollout(svc, "")
        assert state == ROLLOUT_COMPLETED


class TestDeploymentIdExtraction:
    def test_extracts_primary_deployment_id(self):
        payload = json.dumps({
            "service": {
                "deployments": [
                    {"id": "ecs-svc/old", "status": "ACTIVE"},
                    {"id": "ecs-svc/new", "status": "PRIMARY"},
                ]
            }
        })
        assert extract_deployment_id(payload) == "ecs-svc/new"

    def test_returns_empty_on_non_json(self):
        assert extract_deployment_id("Updated service") == ""

    def test_returns_empty_when_no_primary(self):
        payload = json.dumps({"service": {"deployments": [{"id": "x", "status": "ACTIVE"}]}})
        assert extract_deployment_id(payload) == ""


class TestTerraformCircuitBreakerConfig:
    def test_ecs_tf_contains_deployment_circuit_breaker(self):
        with open("terraform/ecs.tf", "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "deployment_circuit_breaker" in content
        assert "enable   = true" in content
        assert "rollback = true" in content


class TestDeployWaitWindows:
    def test_wait_windows_exceed_measured_rollout_floor(self):
        """The service's measured uncontested rollout takes ~315s.

        ROLLBACK_WAIT_SECONDS used to be 240s, which made a successful rollback
        physically impossible and produced the "ROLLBACK FAILED" reports.
        """
        from scripts.ecs_deploy_and_diagnose import (
            DEPLOY_WAIT_SECONDS,
            ROLLBACK_WAIT_SECONDS,
        )

        measured_floor_seconds = 315
        assert DEPLOY_WAIT_SECONDS > measured_floor_seconds
        assert ROLLBACK_WAIT_SECONDS > measured_floor_seconds
