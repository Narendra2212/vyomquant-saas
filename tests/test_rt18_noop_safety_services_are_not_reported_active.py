"""RT-18: services that do no work must not be reported ACTIVE ("kill-switch armed")."""
from fastapi.testclient import TestClient

from backend_app import main


def test_noop_services_report_not_implemented(monkeypatch):
    monkeypatch.setattr(main, "_runtime_service_status", {
        "consistency_checker": "ACTIVE", "order_watchdog": "ACTIVE",
        "pnl_engine": "ACTIVE", "reconciliation_scheduler": "ACTIVE",
        "_captured_at": "2026-01-01T00:00:00+00:00",
    })
    body = TestClient(main.app).get("/health/services").json()
    assert body["services"]["consistency_checker"] == "NOT_IMPLEMENTED"
    assert body["services"]["reconciliation_scheduler"] == "NOT_IMPLEMENTED"
    assert body["services"]["order_watchdog"] == "ACTIVE"
    assert body["summary"]["not_implemented"] == 2
    assert body["status"] == "degraded"


def test_startup_log_no_longer_claims_kill_switch_armed():
    import inspect
    src = inspect.getsource(main)
    assert "kill-switch armed" not in src
