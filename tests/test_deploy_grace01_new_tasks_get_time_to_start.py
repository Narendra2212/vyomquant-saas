"""DEPLOY-GRACE-01 -- a new ECS task gets enough grace to finish starting.

gunicorn binds :8000 before the uvicorn worker finishes the app lifespan, so ALB health
checks during startup connect and time out. With a 60 s service grace period, a task whose
startup ran a little over a minute was killed at registration + 72 s (deploys 584, 587,
591 attempt 1, 592: "(reason Request timed out)"). The deploy now sets 180 s on every
update-service it makes, forward and rollback.
"""
import importlib
import sys

import pytest


@pytest.fixture
def deploy(monkeypatch):
    monkeypatch.delenv("HEALTH_CHECK_GRACE_SECONDS", raising=False)
    sys.path.insert(0, "scripts")
    mod = importlib.import_module("ecs_deploy_and_diagnose")
    return importlib.reload(mod)


def _capture(monkeypatch, mod):
    calls = []
    monkeypatch.setattr(mod, "run_aws_cmd", lambda argv, *a, **k: (calls.append(argv), (0, "{}", ""))[1])
    return calls


def _grace(argv):
    return int(argv[argv.index("--health-check-grace-period-seconds") + 1])


def test_forward_deploy_sets_the_grace_period(monkeypatch, deploy):
    calls = _capture(monkeypatch, deploy)
    deploy.deploy_new_image("arn:td:1")
    assert _grace(calls[0]) == 180
    assert "--force-new-deployment" in calls[0]


def test_rollback_sets_it_too(monkeypatch, deploy):
    calls = _capture(monkeypatch, deploy)
    deploy.rollback_to_task_def("arn:td:0")
    assert _grace(calls[0]) == 180
    assert "--force-new-deployment" not in calls[0]


def test_the_grace_fits_inside_the_deploy_wait_window(deploy):
    # image pull (~110 s) + grace + ALB healthy threshold (2 x 15 s) must leave margin.
    assert 110 + deploy.HEALTH_CHECK_GRACE_SECONDS + 30 < deploy.DEPLOY_WAIT_SECONDS
    assert deploy.HEALTH_CHECK_GRACE_SECONDS > 72, "the observed kill point must be covered"


def test_it_is_configurable(monkeypatch):
    monkeypatch.setenv("HEALTH_CHECK_GRACE_SECONDS", "240")
    sys.path.insert(0, "scripts")
    mod = importlib.reload(importlib.import_module("ecs_deploy_and_diagnose"))
    assert mod.HEALTH_CHECK_GRACE_SECONDS == 240
    monkeypatch.delenv("HEALTH_CHECK_GRACE_SECONDS")
    importlib.reload(mod)
