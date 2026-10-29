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
    @patch("scripts.ecs_deploy_and_diagnose.redeploy_workers")
    @patch("scripts.ecs_deploy_and_diagnose.get_current_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.register_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.deploy_new_image")
    @patch("scripts.ecs_deploy_and_diagnose.rollback_to_task_def")
    @patch("scripts.ecs_deploy_and_diagnose.wait_for_rollout")
    def test_execute_deployment_success_flow(
        self, mock_wait, mock_rollback, mock_deploy, mock_register, mock_get_prev,
        mock_redeploy_workers, mock_idle,
    ):
        mock_idle.return_value = (True, "service idle (COMPLETED)")
        mock_get_prev.return_value = "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:10"
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_deploy.return_value = (True, "Updated service")
        mock_wait.return_value = ROLLOUT_COMPLETED
        mock_redeploy_workers.return_value = {}

        result = execute_deployment("273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v2")
        assert result is True
        # deploy_new_image called once with new ARN; rollback never called
        mock_deploy.assert_called_once_with("arn:aws:ecs:ap-southeast-1:1234:task-definition/api:11")
        mock_rollback.assert_not_called()
        # INF-03: workers are redeployed onto the same image only after the API's own
        # deployment is confirmed stable.
        mock_redeploy_workers.assert_called_once_with(
            "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/api:v2"
        )

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


class TestWorkerRedeployment:
    """INF-03: nothing redeployed a worker's code at all once CI-02 stopped
    02-build.yml from pushing the mutable `:latest` tag workers used to (eventually)
    pick new code up through. execute_deployment now rolls every worker ECS service
    onto the same image the API just deployed, after the API's own rollout is confirmed
    stable.
    """

    def test_list_worker_services_filters_by_prefix(self):
        from scripts.ecs_deploy_and_diagnose import list_worker_services

        payload = json.dumps({
            "serviceArns": [
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-api-service-cjema2sl",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-worker-backtest-service-production",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-worker-training-service-production",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-tee-service-production",
            ]
        })
        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(0, payload, "")):
            services = list_worker_services()

        assert services == [
            "vyomquant-worker-backtest-service-production",
            "vyomquant-worker-training-service-production",
        ]

    def test_list_worker_services_returns_empty_on_aws_failure(self):
        from scripts.ecs_deploy_and_diagnose import list_worker_services

        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(1, "", "access denied")):
            assert list_worker_services() == []

    def test_list_worker_services_includes_the_mds_sidecar(self):
        """`vyomquant-mds-service` runs the api image under a command override, exactly
        like a worker, but its name does not match 'vyomquant-worker-'. Measured on the
        live cluster: it sat on vyomquant-api:f39012da while the other six services had
        moved to c9d94509, because this list was the only thing feeding redeploy_workers
        and the prefix never selected it. It fell one deploy further behind per push.
        """
        from scripts.ecs_deploy_and_diagnose import list_worker_services

        payload = json.dumps({
            "serviceArns": [
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-api-service-cjema2sl",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-worker-dag-service",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-mds-service",
                "arn:aws:ecs:ap-southeast-1:1234:service/cluster/vyomquant-tee-service-production",
            ]
        })
        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(0, payload, "")):
            services = list_worker_services()

        assert "vyomquant-mds-service" in services, (
            "the mds sidecar is not selected for redeployment, so every push to main "
            "leaves it one commit further behind the rest of the cluster"
        )
        # The api service has already deployed itself by the time redeploy_workers runs;
        # rolling it again here would re-deploy the thing that just proved itself healthy.
        assert "vyomquant-api-service-cjema2sl" not in services
        # tee was deleted (RT-23). A stale service surviving in the cluster must not be
        # revived by being handed a fresh image.
        assert "vyomquant-tee-service-production" not in services

    def test_sidecar_service_names_agree_with_the_deploy_script(self):
        """One declaration site, enforced rather than documented.

        scripts/deploy_worker_services.py's SIDECAR_SERVICES is where a non-worker
        service is declared; ecs_deploy_and_diagnose holds its own literal copy of the
        service NAMES because 03-deploy.yml runs it as `python
        scripts/ecs_deploy_and_diagnose.py`, which puts scripts/ on sys.path[0] and
        leaves the repository root unimportable -- so a cross-module import would raise
        ModuleNotFoundError at deploy time while passing here. This test is what stops
        the two from drifting: add a sidecar to one and not the other and it fails.
        """
        from scripts.ecs_deploy_and_diagnose import SIDECAR_SERVICE_NAMES
        from scripts.deploy_worker_services import SIDECAR_SERVICES

        declared = sorted(spec["service"] for spec in SIDECAR_SERVICES.values())

        assert sorted(SIDECAR_SERVICE_NAMES) == declared, (
            f"ecs_deploy_and_diagnose.SIDECAR_SERVICE_NAMES "
            f"{sorted(SIDECAR_SERVICE_NAMES)} does not match the services declared in "
            f"deploy_worker_services.SIDECAR_SERVICES {declared}. A name present only "
            f"in the deploy script is a service no push to main ever updates."
        )

    @patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd")
    @patch("scripts.ecs_deploy_and_diagnose.register_worker_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.get_service_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.list_worker_services")
    def test_redeploy_workers_rolls_the_sidecar_onto_the_api_image(
        self, mock_list, mock_get_arn, mock_register, mock_run_aws_cmd
    ):
        """The sidecar goes down the same path as a worker: re-register the service's
        CURRENT task definition with only the image replaced, then force a new
        deployment. Re-registering the current revision is what preserves mds's command
        override and its 1024/3072 sizing without this file having to know either.
        """
        from scripts.ecs_deploy_and_diagnose import redeploy_workers

        mock_list.return_value = ["vyomquant-mds-service"]
        mock_get_arn.return_value = "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-mds:4"
        mock_register.return_value = (True, "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-mds:5")
        mock_run_aws_cmd.return_value = (0, "Updated service", "")

        results = redeploy_workers("ecr/vyomquant-api:newsha")

        assert results == {"vyomquant-mds-service": True}
        mock_register.assert_called_once_with(
            "ecr/vyomquant-api:newsha",
            "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-mds:4",
        )
        args = mock_run_aws_cmd.call_args[0][0]
        assert "update-service" in args
        assert "vyomquant-mds-service" in args
        assert "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-mds:5" in args
        assert "--force-new-deployment" in args

    def test_register_worker_task_definition_sets_image_on_every_container(self, tmp_path, monkeypatch):
        from scripts.ecs_deploy_and_diagnose import register_worker_task_definition

        monkeypatch.chdir(tmp_path)

        # Worker-shaped: no port mapping, cpu/memory that would fail validate_critical_config
        # outright (which this function must NOT call -- that check is API-specific).
        describe_td = json.dumps({
            "taskDefinition": {
                "family": "vyomquant-worker-training-production",
                "revision": 3,
                "taskDefinitionArn": "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-worker-training-production:3",
                "cpu": "2048",
                "memory": "8192",
                "containerDefinitions": [
                    {"name": "worker-training", "image": "old-image:old-sha"},
                ],
            }
        })
        register_response = json.dumps({
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-worker-training-production:4"
            }
        })

        with patch(
            "scripts.ecs_deploy_and_diagnose.run_aws_cmd",
            side_effect=[(0, describe_td, ""), (0, register_response, "")],
        ) as mock_run:
            ok, arn = register_worker_task_definition(
                "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:newsha",
                "arn:aws:ecs:ap-southeast-1:1234:task-definition/vyomquant-worker-training-production:3",
            )

        assert ok is True
        assert arn.endswith(":4")

        # The rendered task-def file (second run_aws_cmd call's --cli-input-json target)
        # must carry the new image on the one container, and none of the fields
        # register-task-definition rejects.
        register_call_args = mock_run.call_args_list[1][0][0]
        cli_input = next(a for a in register_call_args if a.startswith("file://"))
        rendered_path = cli_input.removeprefix("file://")
        with open(rendered_path, encoding="utf-8") as f:
            rendered = json.load(f)
        assert rendered["containerDefinitions"][0]["image"] == (
            "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:newsha"
        )
        assert "taskDefinitionArn" not in rendered
        assert "revision" not in rendered

    def test_register_worker_task_definition_fails_cleanly_with_no_containers(self):
        from scripts.ecs_deploy_and_diagnose import register_worker_task_definition

        describe_td = json.dumps({"taskDefinition": {"family": "empty", "containerDefinitions": []}})
        with patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd", return_value=(0, describe_td, "")):
            ok, msg = register_worker_task_definition("some:image", "some-arn")

        assert ok is False
        assert "container" in msg.lower()

    @patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd")
    @patch("scripts.ecs_deploy_and_diagnose.register_worker_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.get_service_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.list_worker_services")
    def test_redeploy_workers_happy_path(
        self, mock_list, mock_get_arn, mock_register, mock_run_aws_cmd
    ):
        from scripts.ecs_deploy_and_diagnose import redeploy_workers

        mock_list.return_value = ["vyomquant-worker-backtest-service-production", "vyomquant-worker-training-service-production"]
        mock_get_arn.return_value = "arn:...:3"
        mock_register.return_value = (True, "arn:...:4")
        mock_run_aws_cmd.return_value = (0, "Updated service", "")

        results = redeploy_workers("ecr/vyomquant-api:newsha")

        assert results == {
            "vyomquant-worker-backtest-service-production": True,
            "vyomquant-worker-training-service-production": True,
        }
        assert mock_run_aws_cmd.call_count == 2
        for call in mock_run_aws_cmd.call_args_list:
            args = call.args[0]
            assert "--force-new-deployment" in args
            assert "--task-definition" in args
            assert "arn:...:4" in args

    @patch("scripts.ecs_deploy_and_diagnose.run_aws_cmd")
    @patch("scripts.ecs_deploy_and_diagnose.register_worker_task_definition")
    @patch("scripts.ecs_deploy_and_diagnose.get_service_task_definition_arn")
    @patch("scripts.ecs_deploy_and_diagnose.list_worker_services")
    def test_redeploy_workers_isolates_one_failure_from_the_rest(
        self, mock_list, mock_get_arn, mock_register, mock_run_aws_cmd
    ):
        """One worker failing to register must not stop the others from being attempted,
        and must not be mistaken for the API's own (already-confirmed-good) deployment
        failing."""
        from scripts.ecs_deploy_and_diagnose import redeploy_workers

        mock_list.return_value = ["vyomquant-worker-backtest-service-production", "vyomquant-worker-dag-service-production"]
        mock_get_arn.return_value = "arn:...:3"
        mock_register.side_effect = [
            (False, "registration failed"),
            (True, "arn:...:9"),
        ]
        mock_run_aws_cmd.return_value = (0, "Updated service", "")

        results = redeploy_workers("ecr/vyomquant-api:newsha")

        assert results == {
            "vyomquant-worker-backtest-service-production": False,
            "vyomquant-worker-dag-service-production": True,
        }
        # update-service was only attempted for the worker whose registration succeeded.
        mock_run_aws_cmd.assert_called_once()

    @patch("scripts.ecs_deploy_and_diagnose.list_worker_services")
    def test_redeploy_workers_returns_empty_dict_when_none_found(self, mock_list):
        from scripts.ecs_deploy_and_diagnose import redeploy_workers

        mock_list.return_value = []
        assert redeploy_workers("ecr/vyomquant-api:newsha") == {}
