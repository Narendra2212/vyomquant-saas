"""ML-WORKER-01 -- production gets a training worker, through a manual, plan-first workflow.

Production had no process that runs training jobs: training_worker exists in
docker-compose.yml and terraform/ecs.tf (OPS-TRAIN-01) but no ECS service was created, so
every training job stayed QUEUED. 12-provision-training-worker.yml runs the reviewed
scripts/deploy_worker_services.py for the training worker only. These tests keep it
from becoming something that changes production on its own.
"""
import sys
from pathlib import Path

import yaml

WF = Path(".github/workflows/12-provision-training-worker.yml")


def _wf():
    return yaml.safe_load(WF.read_text())


def test_it_only_runs_when_a_person_starts_it():
    triggers = _wf().get(True) or _wf().get("on")  # PyYAML reads `on:` as True
    assert set(triggers) == {"workflow_dispatch"}


def test_the_default_mode_changes_nothing():
    inputs = (_wf().get(True) or _wf().get("on"))["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["default"] == "plan"
    assert inputs["update_existing"]["default"] is False


def test_it_touches_the_training_worker_only_at_one_replica():
    text = WF.read_text()
    assert "--worker training" in text
    assert "--desired-count 1" in text
    assert "--worker all" not in text and "i-understand-fan-out" not in text


def test_the_service_it_creates_is_kept_current_by_every_deploy():
    sys.path.insert(0, "scripts")
    import deploy_worker_services as d
    import ecs_deploy_and_diagnose as e

    spec = d.worker_definitions()["training"]
    assert spec["command"] == ["python", "-m", "backend_app.backend.training_worker"]
    assert spec["service"].startswith(e.WORKER_SERVICE_PREFIX)


def test_a_refused_step_fails_the_run():
    # run 3: create-service was AccessDenied but the job was green, because `| tee` hid it.
    assert "set -euo pipefail" in WF.read_text()


def test_the_deploy_role_policy_for_workers_is_narrow():
    import json
    pol = json.loads(Path("infra/iam/deploy-role-worker-services-policy.json").read_text())
    (stmt,) = pol["Statement"]
    assert set(stmt["Action"]) == {"ecs:CreateService", "ecs:UpdateService", "ecs:DescribeServices"}
    assert stmt["Resource"].endswith(":service/vyomquant-cluster/vyomquant-worker-*")
