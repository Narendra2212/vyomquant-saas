"""LT-01: the Live Trading page listed no deployment while a bot was running.

``GET /api/strategies/{id}/deployments`` read only the in-process registry, which a
``deploy_version`` (fleet) deployment never enters. It now reads the owner's
``strategy_deployments`` rows first.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.routers import strategy_operations as ops

USER = {"id": "u-1", "access_token": "t"}
ROW = {"id": "dep-1", "strategy_id": "s-1", "user_id": "u-1", "status": "running",
       "environment": "paper", "worker_id": None, "started_at": None,
       "created_at": "2026-11-13T08:31:59Z"}


class _Q:
    def __init__(self, sink, rows):
        self.sink, self.rows, self.filters = sink, rows, {}

    def select(self, *a):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        self.sink.append(dict(self.filters))
        return SimpleNamespace(data=[r for r in self.rows
                                     if all(r.get(k) == v for k, v in self.filters.items())])


def _client(monkeypatch, rows, registry=(), boom=False):
    import backend_app.core.dependencies as deps
    calls = []

    class _SB:
        def table(self, name):
            assert name == "strategy_deployments"
            if boom:
                raise RuntimeError("db down")
            return _Q(calls, rows)

    async def _sb(token):
        return _SB()

    monkeypatch.setattr(deps, "create_request_supabase_async", _sb)
    monkeypatch.setattr(ops, "get_deployment_manager",
                        lambda: SimpleNamespace(list_deployments=lambda uid: list(registry)))
    app = FastAPI()
    app.include_router(ops.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app), calls


def test_a_fleet_deployment_is_listed_from_its_row(monkeypatch):
    client, calls = _client(monkeypatch, [ROW])
    body = client.get("/api/strategies/s-1/deployments").json()
    assert body["total"] == 1
    d = body["deployments"][0]
    assert d["deployment_id"] == "dep-1" and d["status"] == "running" and d["environment"] == "paper"
    assert d["started_at"] == "2026-11-13T08:31:59Z"
    assert d["health"] is None, "no runtime attached: health is absent, never a fabricated reading"
    assert calls == [{"user_id": "u-1", "strategy_id": "s-1"}], "owner- and strategy-scoped"


def test_another_users_row_is_not_listed(monkeypatch):
    client, _ = _client(monkeypatch, [dict(ROW, user_id="someone-else")])
    assert client.get("/api/strategies/s-1/deployments").json()["total"] == 0


def test_a_registry_only_deployment_is_still_listed(monkeypatch):
    legacy = SimpleNamespace(
        deployment_id="dep-legacy", status=SimpleNamespace(value="running"),
        config=SimpleNamespace(strategy_id="s-1", environment=SimpleNamespace(value="paper")),
        worker=None, started_at="t0",
        health=SimpleNamespace(cpu_percent=1, memory_percent=2, latency_ms=3, uptime_seconds=4))
    client, _ = _client(monkeypatch, [ROW], registry=[legacy])
    ids = [d["deployment_id"] for d in client.get("/api/strategies/s-1/deployments").json()["deployments"]]
    assert ids == ["dep-1", "dep-legacy"]


def test_an_unreadable_record_is_503_not_an_empty_list(monkeypatch):
    client, _ = _client(monkeypatch, [], boom=True)
    r = client.get("/api/strategies/s-1/deployments")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "DEPLOYMENTS_LIST_UNAVAILABLE"
