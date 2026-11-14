"""LIVE-LAUNCH-01: the live-trading launch policy is applied by the deploy, not by hand.

Found in production on 2026-11-14:

* only ONE tenant was in ``VYOMQUANT_LIVE_TENANT_ALLOWLIST``, so every other customer on a
  plan that includes live trading was refused ``LIVE_TENANT_NOT_ALLOWLISTED``;
* ``RESUME_LIVE_DEPLOYMENTS`` was unset, so every restart (each deploy, each task
  replacement) marked every live deployment ``failed`` -- three that day.

Both were hand-set ECS console values. The deploy now sets them from a reviewed constant.
The testnet-only guard (``VYOMQUANT_LIVE_SANDBOX_ONLY``) is deliberately NOT part of it:
moving to real money stays an explicit operator action.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _deploy_module():
    spec = importlib.util.spec_from_file_location(
        "ecs_deploy_and_diagnose", REPO / "scripts" / "ecs_deploy_and_diagnose.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env(container):
    return {e["name"]: e["value"] for e in container["environment"]}


def test_the_policy_opens_live_to_entitled_plans_and_resumes_live_bots():
    deploy = _deploy_module()
    assert deploy.LIVE_TRADING_POLICY_ENV == {
        "VYOMQUANT_LIVE_TENANT_ALLOWLIST": "*",
        "RESUME_LIVE_DEPLOYMENTS": "true",
    }


def test_the_policy_never_touches_the_testnet_only_guard():
    deploy = _deploy_module()
    assert "VYOMQUANT_LIVE_SANDBOX_ONLY" not in deploy.LIVE_TRADING_POLICY_ENV
    container = {"environment": [{"name": "VYOMQUANT_LIVE_SANDBOX_ONLY", "value": "true"}]}
    deploy.apply_live_trading_policy_env(container)
    assert _env(container)["VYOMQUANT_LIVE_SANDBOX_ONLY"] == "true"


def test_a_hand_set_allowlist_is_replaced_and_missing_values_are_added():
    deploy = _deploy_module()
    container = {"environment": [
        {"name": "VYOMQUANT_LIVE_TENANT_ALLOWLIST", "value": "d0a4f657-092f-4def-96f1-858e2f7b63c1"},
        {"name": "VYOMQUANT_MODE", "value": "live"},
    ]}
    changed = deploy.apply_live_trading_policy_env(container)
    env = _env(container)
    assert env["VYOMQUANT_LIVE_TENANT_ALLOWLIST"] == "*"
    assert env["RESUME_LIVE_DEPLOYMENTS"] == "true"
    assert env["VYOMQUANT_MODE"] == "live"
    assert {name for name, _old, _new in changed} == set(deploy.LIVE_TRADING_POLICY_ENV)
    # No duplicate entries: ECS rejects a name declared twice.
    names = [e["name"] for e in container["environment"]]
    assert len(names) == len(set(names))


def test_it_is_idempotent_across_deploys():
    deploy = _deploy_module()
    container = {"environment": []}
    deploy.apply_live_trading_policy_env(container)
    assert deploy.apply_live_trading_policy_env(container) == []


def test_a_name_supplied_as_a_secret_is_left_alone():
    deploy = _deploy_module()
    container = {
        "environment": [],
        "secrets": [{"name": "VYOMQUANT_LIVE_TENANT_ALLOWLIST", "valueFrom": "arn:aws:ssm:x"}],
    }
    deploy.apply_live_trading_policy_env(container)
    assert "VYOMQUANT_LIVE_TENANT_ALLOWLIST" not in _env(container)
    assert _env(container)["RESUME_LIVE_DEPLOYMENTS"] == "true"


def test_register_task_definition_applies_it_to_the_api_container():
    src = (REPO / "scripts" / "ecs_deploy_and_diagnose.py").read_text()
    body = src.split("def register_task_definition", 1)[1].split("\ndef ", 1)[0]
    assert "apply_live_trading_policy_env(container)" in body


def test_the_gate_reads_star_as_every_tenant(monkeypatch):
    """'*' must mean "all tenants" to the gate, or the policy would lock everyone out."""
    from backend_app.core import live_execution_gate as gate

    monkeypatch.setenv(gate.LIVE_TENANT_ALLOWLIST_ENV, "*")
    monkeypatch.setenv("VYOMQUANT_MODE", "live")
    monkeypatch.setenv("VYOMQUANT_ENABLE_LIVE_TRADING", "true")
    gate.assert_platform_live_trading_enabled("any-tenant-id")  # does not raise
