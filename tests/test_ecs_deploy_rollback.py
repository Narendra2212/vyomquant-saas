"""
tests/test_ecs_deploy_rollback.py

Unit tests verifying automated ECS deployment rollback logic and Terraform configuration.
"""

import json
import pytest
from unittest.mock import patch

from scripts.ecs_deploy_and_diagnose import (
    get_current_task_definition_arn,
    execute_deployment,
    deploy_new_image,
    rollback_to_task_def,
)


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

    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_service_stable")
    def test_execute_deployment_success_flow(
        self, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev
    ):
        mock_get_prev.return_value = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:10"
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_deploy.return_value = (True, "Updated service")
        mock_wait.return_value = True

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v2")
        assert result is True
        # deploy_new_image called once with new ARN; rollback never called
        mock_deploy.assert_called_once_with("arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_rollback.assert_not_called()

    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_service_stable")
    @patch("scripts.ecs_deploy_and_diagnose.collect_diagnostics")
    def test_execute_deployment_rollback_flow_on_unstable(
        self, mock_diag, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev
    ):
        prev_arn = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:10"
        new_arn = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11"
        mock_get_prev.return_value = prev_arn
        mock_register.return_value = (True, new_arn)
        mock_deploy.return_value = (True, "Updated to 11")
        # First wait (forward deploy) fails; second (rollback) succeeds
        mock_wait.side_effect = [False, True]
        mock_rollback.return_value = (True, "Rolled back to 10")
        mock_diag.return_value = {"cloudwatch_logs": ["Crash log"]}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:broken")
        assert result is False
        # deploy_new_image called once; rollback_to_task_def called once with the previous ARN
        mock_deploy.assert_called_once_with(new_arn)
        mock_rollback.assert_called_once_with(prev_arn)

    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_service_stable")
    @patch("scripts.ecs_deploy_and_diagnose.collect_diagnostics")
    def test_execute_deployment_initial_deployment_failure_no_rollback(
        self, mock_diag, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev
    ):
        # No previous task definition ARN (first ever deployment)
        mock_get_prev.return_value = ""
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:1")
        mock_deploy.return_value = (True, "Updated")
        mock_wait.return_value = False
        mock_diag.return_value = {"cloudwatch_logs": []}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v1")
        assert result is False
        # deploy_new_image called once; rollback must NOT be called
        mock_deploy.assert_called_once()
        mock_rollback.assert_not_called()


class TestTerraformCircuitBreakerConfig:
    def test_ecs_tf_contains_deployment_circuit_breaker(self):
        with open("terraform/ecs.tf", "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "deployment_circuit_breaker" in content
        assert "enable   = true" in content
        assert "rollback = true" in content
