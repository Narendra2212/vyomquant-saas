"""OBS-01: production never sent Sentry a single event.

The backend initialises Sentry only when SENTRY_DSN is set, and no ECS task definition carried
it (the remediation ledger listed it as a manual step that was never done). Sentry showed zero
events for vyomquant-api-production while production answered unhandled 500s (PAPER-08, REF-01).
The deploy script now gives every API and worker container SENTRY_DSN / SENTRY_ENVIRONMENT
unless the operator already set them. A second, module-level init in main.py (100% traces and
profiles, no scrubbing) is gone; the lifespan's `initialize_sentry` is the one init.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _deploy_module():
    spec = importlib.util.spec_from_file_location(
        "ecs_deploy_and_diagnose", REPO / "scripts" / "ecs_deploy_and_diagnose.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_container_without_a_dsn_gets_one() -> None:
    deploy = _deploy_module()
    container = {"name": "vyomquant-api", "environment": [{"name": "ENV", "value": "production"}]}

    added = deploy.ensure_observability_env(container)

    env = {e["name"]: e["value"] for e in container["environment"]}
    assert added == ["SENTRY_DSN", "SENTRY_ENVIRONMENT"]
    assert env["SENTRY_DSN"].startswith("https://") and "ingest" in env["SENTRY_DSN"]
    assert env["SENTRY_ENVIRONMENT"] == "production"
    assert env["ENV"] == "production"


def test_an_operator_set_value_is_never_overwritten() -> None:
    deploy = _deploy_module()
    container = {
        "environment": [{"name": "SENTRY_ENVIRONMENT", "value": "staging"}],
        "secrets": [{"name": "SENTRY_DSN", "valueFrom": "arn:aws:secretsmanager:x"}],
    }

    added = deploy.ensure_observability_env(container)

    assert added == []
    assert container["environment"] == [{"name": "SENTRY_ENVIRONMENT", "value": "staging"}]


def test_it_is_idempotent_across_deploys() -> None:
    deploy = _deploy_module()
    container = {"environment": []}
    deploy.ensure_observability_env(container)

    assert deploy.ensure_observability_env(container) == []
    assert len(container["environment"]) == 2


def test_main_has_exactly_one_sentry_init_and_it_is_the_scrubbing_one() -> None:
    source = (REPO / "backend_app" / "main.py").read_text(encoding="utf-8")

    assert "sentry_sdk.init(" not in source, "the unscrubbed module-level init is back"
    assert len(re.findall(r"\binitialize_sentry\(", source)) == 1
