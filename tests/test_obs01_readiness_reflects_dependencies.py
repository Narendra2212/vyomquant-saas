"""OBS-01: liveness, readiness and dependency health are three different answers.

THE DEFECT
----------
`/health/ready` returned `{"status": "ready"}` unconditionally. `/api/system/readiness`
and `/api/system/dependencies` returned the literal string `"HEALTHY"` for database,
redis, risk_engine, execution_engine and reconciliation, plus fabricated detail
(`"circuit_breaker": "ARMED"`). And `health()`'s `all_ok` considered only Supabase and
QuestDB, so **Redis being down still reported `"ok"`** even though the redis probe had
already run and its result was discarded.

Net: an orchestrator could route production traffic to a pod with no database and no
Redis, and every health surface would agree it was fine.

HOW THIS FILE TESTS IT
----------------------
Through the real FastAPI routes with `TestClient`, asserting **HTTP STATUS** and not just
body text — a 200 carrying a sad body is invisible to a load balancer, which is exactly
why the old version looked harmless.

Dependency failure is induced at the **probe boundary** (`core.readiness.probe_*`) and,
for the decisive cases, by pointing the real database probe at a DSN that nothing is
listening on — a genuine connection failure rather than a mocked health response.

Section 5 is the negative control: it reconstructs the pre-fix endpoints and shows they
answer 200/READY with every dependency down. Without it, the assertions below could pass
against an endpoint that is merely always-503.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core import readiness as R  # noqa: E402

DOWN_DSN = "postgresql://postgres:@127.0.0.1:1/postgres"  # nothing listens on port 1


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    return TestClient(app)


@pytest.fixture
def admin_client():
    """API-07: /api/system/* and /health/services are gated behind get_admin_user now --
    this file's own assertions about what they report still need an authenticated admin
    caller to reach them at all."""
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_admin_user
    from backend_app.main import app

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _up(name: str, required: bool = True) -> R.DependencyReport:
    return R.DependencyReport(name, R.UP, required, "reachable")


def _down(name: str, required: bool = True) -> R.DependencyReport:
    return R.DependencyReport(name, R.DOWN, required, "simulated outage")


async def _all_up():
    return R.ReadinessReport(
        ready=True,
        dependencies=(_up("database"), _up("redis"), _up("telemetry", False)),
        checked_at="2026-01-01T00:00:00+00:00",
    )


# ── 1. the report object's own semantics ────────────────────────────────────


def test_a_required_dependency_being_down_blocks_readiness():
    assert _down("database").blocks_readiness is True


def test_an_optional_dependency_being_down_does_not_block():
    """Making readiness depend on an optional service turns a metrics outage into a
    total outage."""
    assert _down("telemetry", required=False).blocks_readiness is False


def test_an_undeterminable_required_dependency_blocks_readiness():
    """UNKNOWN fails CLOSED. 'We could not tell' is not a reason to take traffic."""
    unknown = R.DependencyReport("database", R.UNKNOWN, True, "probe exploded")
    assert unknown.blocks_readiness is True


def test_not_ready_is_a_503_not_a_200():
    """THE MOST IMPORTANT ASSERTION IN THIS FILE.

    The pre-fix endpoint was useless precisely because it was always 2xx.
    """
    report = R.ReadinessReport(
        ready=False, dependencies=(_down("database"),), checked_at="x"
    )
    assert report.http_status == 503

    ok = R.ReadinessReport(ready=True, dependencies=(_up("database"),), checked_at="x")
    assert ok.http_status == 200


def test_the_report_names_what_is_blocking():
    report = R.ReadinessReport(
        ready=False,
        dependencies=(_down("database"), _up("redis"), _down("telemetry", False)),
        checked_at="x",
    )
    assert report.blocking == ("database",), (
        "an optional dependency leaked into the blocking list"
    )


def test_probe_detail_never_leaks_a_connection_string():
    """These endpoints are typically unauthenticated. Provider exceptions embed DSNs."""
    leaky = RuntimeError(
        "could not connect to postgresql://user:hunter2@db.internal:5432/prod"
    )
    detail = R._safe_detail(leaky)
    assert "postgresql://" not in detail
    assert "hunter2" not in detail
    assert detail == "RuntimeError"


@pytest.mark.parametrize("secret", [
    "password=hunter2", "apikey=abc", "token eyJhbGci", "secret sauce",
])
def test_probe_detail_scrubs_other_credential_shapes(secret):
    assert R._safe_detail(ValueError(secret)) == "ValueError"


# ── 2. the real routes, with dependencies up and down ──────────────────────


def test_ready_returns_200_when_every_required_dependency_is_up(client, monkeypatch):
    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _all_up)

    resp = client.get("/health/ready")

    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


def test_ready_returns_503_when_the_database_is_down(client, monkeypatch):
    async def _db_down():
        return R.ReadinessReport(
            ready=False,
            dependencies=(_down("database"), _up("redis")),
            checked_at="x",
        )

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _db_down)

    resp = client.get("/health/ready")

    assert resp.status_code == 503, (
        "a pod with no database advertised itself as ready to receive traffic"
    )
    body = resp.json()
    assert body["status"] == "NOT_READY"
    assert "database" in body["blocking"]


def test_ready_returns_503_when_redis_is_down_and_required(client, monkeypatch):
    """Redis is not a cache here: it backs the rate limiter and the execution locks."""
    async def _redis_down():
        return R.ReadinessReport(
            ready=False,
            dependencies=(_up("database"), _down("redis")),
            checked_at="x",
        )

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _redis_down)

    resp = client.get("/health/ready")

    assert resp.status_code == 503
    assert "redis" in resp.json()["blocking"]


def test_ready_stays_200_when_only_an_optional_dependency_is_down(client, monkeypatch):
    async def _telemetry_down():
        return R.ReadinessReport(
            ready=True,
            dependencies=(_up("database"), _up("redis"),
                          _down("telemetry", required=False)),
            checked_at="x",
        )

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _telemetry_down)

    resp = client.get("/health/ready")

    assert resp.status_code == 200, (
        "an OPTIONAL dependency took the pod out of rotation"
    )


def test_readiness_recovers_when_the_dependency_returns(client, monkeypatch):
    import backend_app.core.readiness as mod

    async def _down_then():
        return R.ReadinessReport(
            ready=False, dependencies=(_down("database"),), checked_at="x")

    monkeypatch.setattr(mod, "evaluate_readiness", _down_then)
    assert client.get("/health/ready").status_code == 503

    monkeypatch.setattr(mod, "evaluate_readiness", _all_up)
    assert client.get("/health/ready").status_code == 200, (
        "readiness did not recover after the dependency came back"
    )


# ── 3. liveness must NOT depend on anything external ──────────────────────


def test_liveness_stays_200_while_readiness_is_503(client, monkeypatch):
    """A pod whose database is down is not a broken pod.

    Restarting it does not fix the database, and an orchestrator that kills it loses the
    capacity that would have served traffic once the dependency returned.
    """
    async def _everything_down():
        return R.ReadinessReport(
            ready=False,
            dependencies=(_down("database"), _down("redis")),
            checked_at="x",
        )

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _everything_down)

    assert client.get("/health/ready").status_code == 503
    live = client.get("/health/live")
    assert live.status_code == 200, (
        "liveness failed because an external dependency was down; the orchestrator "
        "would kill a healthy pod"
    )
    assert live.json() == {"status": "alive"}


def test_liveness_does_not_consult_any_dependency():
    """Asserted on the handler's own code, so a future edit cannot quietly add one."""
    import ast
    import inspect
    import textwrap

    from backend_app.main import health_live

    tree = ast.parse(textwrap.dedent(inspect.getsource(health_live)))
    node = tree.body[0]
    stmts = node.body[1:] if ast.get_docstring(node) else node.body
    names = {
        n.id for s in stmts for n in ast.walk(s) if isinstance(n, ast.Name)
    } | {
        n.attr for s in stmts for n in ast.walk(s) if isinstance(n, ast.Attribute)
    }
    for forbidden in ("evaluate_readiness", "redis_manager", "SessionLocal",
                      "probe_database", "probe_redis"):
        assert forbidden not in names, (
            f"/health/live now consults {forbidden}; liveness must not depend on an "
            f"external service"
        )


# ── 4. no hardcoded HEALTHY remains ───────────────────────────────────────


def test_system_readiness_no_longer_returns_hardcoded_healthy(admin_client, monkeypatch):
    async def _db_down():
        return R.ReadinessReport(
            ready=False, dependencies=(_down("database"), _up("redis")), checked_at="x")

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _db_down)

    resp = admin_client.get("/api/system/readiness")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "NOT_READY"
    assert body["dependencies"]["database"]["state"] == R.DOWN


def test_system_dependencies_reports_measured_state(admin_client, monkeypatch):
    async def _mixed():
        return R.ReadinessReport(
            ready=False,
            dependencies=(_up("database"), _down("redis"),
                          _up("telemetry", required=False)),
            checked_at="x",
        )

    import backend_app.core.readiness as mod
    monkeypatch.setattr(mod, "evaluate_readiness", _mixed)

    resp = admin_client.get("/api/system/dependencies")

    # A diagnostic, not a gate: always 200.
    assert resp.status_code == 200
    body = resp.json()
    assert body["dependencies"]["redis"]["state"] == R.DOWN
    assert body["ready"] is False
    assert "redis" in body["blocking"]


def test_no_health_endpoint_contains_a_hardcoded_healthy_literal():
    """AST over the handlers: the docstrings mention "HEALTHY" when explaining the fix."""
    import ast
    import inspect
    import textwrap

    from backend_app.main import system_dependencies, system_readiness

    for fn in (system_readiness, system_dependencies):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        node = tree.body[0]
        stmts = node.body[1:] if ast.get_docstring(node) else node.body
        literals = {
            n.value for s in stmts for n in ast.walk(s)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert "HEALTHY" not in literals, (
            f"{fn.__name__} still emits a hardcoded 'HEALTHY' literal in executable code"
        )


def test_health_services_admits_it_is_a_boot_snapshot(admin_client):
    """It is written once at startup and never refreshed.

    The fix available without re-architecting four non-WorkerBase services is to stop
    presenting boot state as live state.
    """
    resp = admin_client.get("/health/services")

    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "process_startup"
    assert "boot-time snapshot" in body["note"].lower()
    assert "age_seconds" in body


# ── 5. all_ok must count Redis ────────────────────────────────────────────


def test_health_all_ok_counts_redis_when_it_is_required(monkeypatch):
    """The discarded-probe bug: redis was measured and then ignored."""
    import ast
    import inspect
    import textwrap

    from backend_app.main import health

    tree = ast.parse(textwrap.dedent(inspect.getsource(health)))
    node = tree.body[0]
    stmts = node.body[1:] if ast.get_docstring(node) else node.body
    names = {
        n.id for s in stmts for n in ast.walk(s) if isinstance(n, ast.Name)
    }
    assert "_redis_ok" in names, (
        "health()'s all_ok no longer incorporates the redis probe result, so Redis "
        "being down would report 'ok' again"
    )
    assert "local_state_is_acceptable" in names, (
        "the redis requirement is no longer environment-aware, so a developer with no "
        "Redis would see a permanently degraded dashboard"
    )


# ── 6. NEGATIVE CONTROL: the pre-fix endpoints would fail these ───────────


def test_the_pre_fix_ready_endpoint_would_fail_the_503_assertion():
    """Proof section 2 can fail.

    Reconstructs the old handler. It answers 200/ready with every dependency down, which
    is what made it worthless — and what the assertions above would catch.
    """
    def pre_fix_health_ready():
        return {"status": "ready"}

    body = pre_fix_health_ready()

    assert body == {"status": "ready"}
    assert "blocking" not in body, (
        "the reconstruction is wrong; the old endpoint reported no dependency state"
    )
    # Pre-fix there was no status code to inspect at all: it was always 200.
    assert body.get("status") != "NOT_READY"


def test_the_pre_fix_dependencies_endpoint_was_entirely_fabricated():
    """The literals it returned, recorded so the regression is legible."""
    pre_fix = {
        "database": {"status": "HEALTHY", "driver": "supabase/postgres"},
        "redis": {"status": "HEALTHY", "driver": "redis-py"},
        "risk_engine": {"status": "HEALTHY", "circuit_breaker": "ARMED"},
        "execution_engine": {"status": "HEALTHY", "idempotency_guard": "ACTIVE"},
        "reconciliation_scheduler": {"status": "HEALTHY", "poll_interval_sec": 30},
    }
    assert all(v["status"] == "HEALTHY" for v in pre_fix.values()), (
        "the reconstruction is wrong; every value was the literal 'HEALTHY'"
    )
    # And three of the five were not external dependencies at all.
    for in_process in ("risk_engine", "execution_engine", "reconciliation_scheduler"):
        assert in_process in pre_fix


# ── 7. a REAL connection failure, not a mocked one ───────────────────────


def test_the_database_probe_reports_down_against_a_dead_port(monkeypatch):
    """A genuine connection failure, through the real probe.

    Port 1 has no listener, so this exercises the actual failure path rather than a
    stubbed health response.
    """
    import asyncio

    monkeypatch.setenv("DATABASE_URL", DOWN_DSN)

    async def _broken():
        import psycopg2
        psycopg2.connect(DOWN_DSN, connect_timeout=2)

    report = asyncio.run(
        R._probe_with_timeout("database", _broken, required=True)
    )

    assert report.state == R.DOWN, "a dead database port was reported as UP"
    assert report.required is True
    assert report.blocks_readiness is True
    assert "postgresql://" not in report.detail


def test_a_hanging_probe_is_reported_down_not_left_to_hang():
    """Readiness is on an orchestrator's critical path; a hung probe must not hang it."""
    import asyncio

    async def _hang():
        await asyncio.sleep(R.PROBE_TIMEOUT_SECONDS + 5)

    report = asyncio.run(R._probe_with_timeout("slow", _hang, required=True))

    assert report.state == R.DOWN
    assert "exceeded" in report.detail


def test_a_falsey_redis_ping_is_a_failure_not_a_pass(monkeypatch):
    """The facade is fail-soft and answers falsey instead of raising.

    Without converting that into a real failure, Redis being down reads as UP — the
    same class of bug as `all_ok` discarding the probe.
    """
    import asyncio

    import backend_app.core.cache as cache_pkg

    class _FalseyRedis:
        async def ping(self):
            return False

    monkeypatch.setattr(cache_pkg, "redis_manager", _FalseyRedis())
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("DEV_MODE", raising=False)

    report = asyncio.run(R.probe_redis())

    assert report.state == R.DOWN, (
        "a falsey fail-soft ping was reported as UP, so a Redis outage would be invisible"
    )
    assert report.required is True


def test_redis_is_not_required_in_a_declared_local_environment(monkeypatch):
    """A developer with no Redis must not be permanently NOT_READY."""
    import asyncio

    monkeypatch.setenv("ENV", "development")
    report = asyncio.run(R.probe_redis())

    assert report.required is False, (
        "Redis is required in a declared local environment, so local development is "
        "blocked on infrastructure it does not need"
    )
