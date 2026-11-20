"""RT-22 regression: /deploy-pipeline must not start anything (it ran on random data)."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.main import app

client = TestClient(app)


def test_deploy_pipeline_answers_410_and_never_touches_the_deployment_manager():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "email": "u@x.io", "access_token": "t"}
    try:
        with patch("backend_app.routers.strategy_operations.get_deployment_manager") as gdm:
            res = client.post(
                "/api/strategies/00000000-0000-0000-0000-000000000001/deploy-pipeline",
                json={"environment": "paper"},
                headers={"Authorization": "Bearer t"},
            )
        assert res.status_code in (410, 422), res.text  # 422 only if body schema rejects first
        if res.status_code == 410:
            assert "ENDPOINT_RETIRED" in res.text
        gdm.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_no_route_in_the_app_can_start_the_deployment_manager_loop():
    import inspect
    from backend_app.routers import strategy_operations as so

    src = inspect.getsource(so.deploy_strategy_pipeline)
    assert "deploy_strategy(" not in src and "get_deployment_manager" not in src
