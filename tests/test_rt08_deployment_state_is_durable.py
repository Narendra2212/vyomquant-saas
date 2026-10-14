"""RT-08 / RT-03 -- the deployment row follows the runtime, in both directions.

THE DEFECTS (audit)
    * a bot that crashed, failed to boot or gave up left its row at ``running`` (the fleet logged
      "exited cleanly" and wrote nothing);
    * ``stop_deployment`` passed the exchange id where the symbol belongs, and could only ever
      reach a bot in the same process, yet wrote ``stopped`` regardless;
    * pause only flipped the row, so a "paused" bot kept trading;
    * a restart lost every bot and nothing started them again;
    * (RT-03) the legacy ``/deploy`` route started a bot that could never emit an order.

WHAT THIS FILE PROVES (in-memory Supabase double, fake bots; no network)
    * every death path writes ``failed`` + the reason, conditionally (never over a stop);
    * a stop is routed by the real symbol; when the bot is not local it becomes a durable
      ``stopping`` request that the OWNER honours and confirms; ``stopped`` is only written by a
      runtime that stopped;
    * pause/resume reach the owning bot;
    * resume-after-restart starts paper deployments, finalises stop requests, fails (with the
      reason) rows that cannot be rebuilt, and does NOT auto-resume live deployments by default;
    * the legacy routes delegate to the canonical paper deploy.

NOT SHOWN: behaviour across real processes/pods (the WORK-03 claim is exercised by its own
Postgres-gated suite); a real restart of a real worker.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.backend import deployment_runtime as dr
from backend_app.backend.deployment_state import DeploymentStateStore
from backend_app.backend.fleet_manager import FleetManager

REPO = Path(__file__).resolve().parent.parent


# ── an in-memory PostgREST-shaped double ─────────────────────────────────────


class _Query:
    def __init__(self, rows, op, payload=None):
        self._rows, self._op, self._payload = rows, op, payload
        self._filters = []

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        hit = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
        return SimpleNamespace(data=[dict(r) for r in hit])


class FakeSB:
    def __init__(self, deployments):
        self.deployments = deployments

    def table(self, name):
        assert name == "strategy_deployments", name
        return _Query(self.deployments, "select")


def _row(id_="d1", status="running", **kw):
    return {"id": id_, "user_id": "u1", "strategy_id": "s1", "version_id": "v1",
            "exchange_symbol": "ETH/USDT", "exchange_id": "binance", "mode": "paper",
            "environment": "paper", "status": status, **kw}


@pytest.fixture(autouse=True)
def _no_quota(monkeypatch):
    monkeypatch.setattr(
        "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
        AsyncMock(return_value=None),
    )


# ── the conditional store ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStore:
    async def test_failed_is_written_only_over_a_live_state(self):
        sb = FakeSB([_row("a", "running"), _row("b", "stopped"), _row("c", "stopping")])
        store = DeploymentStateStore(lambda: sb)
        assert await store.mark_failed("a", "boom") is True
        assert await store.mark_failed("b", "late report") is False
        assert await store.mark_failed("c", "late report") is False
        rows = {r["id"]: r for r in sb.deployments}
        assert rows["a"]["status"] == "failed" and rows["a"]["error_message"] == "boom"
        assert rows["b"]["status"] == "stopped", "a stale failure report must not overwrite a stop"
        assert rows["c"]["status"] == "stopping"

    async def test_a_failure_releases_the_bot_slot_once(self):
        sb = FakeSB([_row("a", "running")])
        store = DeploymentStateStore(lambda: sb)
        from backend_app.core.subscription_engine import SubscriptionEngine
        await store.mark_failed("a", "x")
        await store.mark_failed("a", "x again")
        assert SubscriptionEngine.decrement_quota_usage.await_count == 1

    async def test_stop_request_and_confirmation(self):
        sb = FakeSB([_row("a", "running")])
        store = DeploymentStateStore(lambda: sb)
        assert await store.request_stop("a") is True
        assert sb.deployments[0]["status"] == "stopping"
        assert await store.request_stop("a") is False, "already stopping"
        assert await store.mark_stopped("a") is True
        assert sb.deployments[0]["status"] == "stopped"

    async def test_an_unreadable_row_is_unknown_not_a_stop(self):
        def boom():
            raise RuntimeError("db down")
        assert await DeploymentStateStore(boom).status_of("a") is None


# ── fleet: every death is reported ───────────────────────────────────────────


class _Store:
    def __init__(self, statuses=None):
        self.failed, self.stopped = [], []
        self.statuses = statuses or {}
        self.stopping_rows, self.live_rows = [], []

    async def mark_failed(self, dep, reason):
        self.failed.append((dep, reason))
        return True

    async def mark_stopped(self, dep):
        self.stopped.append(dep)
        return True

    async def status_of(self, dep):
        return self.statuses.get(dep)

    async def rows_in(self, statuses):
        return list(self.stopping_rows if statuses == ("stopping",) else self.live_rows)


def _bot(dep="d1", **kw):
    bot = SimpleNamespace(blueprint={"deployment_id": dep} if dep else {}, user_id="u1",
                          symbol="ETH/USDT", exit_reason=None, paused=False,
                          shutdown=AsyncMock(), **kw)
    return bot


def _fleet(store):
    fleet = FleetManager(state_store=store)
    return fleet


@pytest.mark.asyncio
class TestFleetReportsDeaths:
    async def _cleanup(self, fleet, bot, exc=None):
        async def go():
            if exc:
                raise exc

        task = asyncio.create_task(go())
        await asyncio.gather(task, return_exceptions=True)
        fleet._active_fleet["k"] = {"task": task, "bot": bot, "symbol": "ETH/USDT",
                                    "claim": None, "heartbeat": None}
        with patch("backend_app.core.state.app_state", SimpleNamespace(alert=SimpleNamespace(send_critical=AsyncMock()))):
            await fleet._cleanup_dead_bot("k", task)

    async def test_a_crash_is_recorded_as_failed(self):
        store = _Store()
        await self._cleanup(_fleet(store), _bot(), RuntimeError("kaboom"))
        assert store.failed and "kaboom" in store.failed[0][1] and store.failed[0][0] == "d1"

    async def test_a_return_nobody_asked_for_is_recorded_with_the_runtimes_reason(self):
        store = _Store()
        bot = _bot()
        bot.exit_reason = "gave up after 5 reconnection attempts: feed down"
        await self._cleanup(_fleet(store), bot)
        assert store.failed == [("d1", "gave up after 5 reconnection attempts: feed down")]

    async def test_a_return_with_no_reason_is_still_a_failure_not_a_clean_exit(self):
        store = _Store()
        await self._cleanup(_fleet(store), _bot())
        assert store.failed and "without a stop request" in store.failed[0][1]

    async def test_a_requested_stop_is_not_reported_as_a_failure(self):
        store = _Store()
        fleet = _fleet(store)
        task = asyncio.create_task(asyncio.sleep(3600))
        fleet._active_fleet["k"] = {"task": task, "bot": _bot(), "symbol": "ETH/USDT",
                                    "claim": None, "heartbeat": None}
        # stop_bot pops the record BEFORE cancelling -- which is how the cleanup tells them apart
        record = fleet._active_fleet.pop("k")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await fleet._cleanup_dead_bot("k", task)
        assert store.failed == [] and record["bot"].blueprint["deployment_id"] == "d1"

    async def test_a_bot_with_no_deployment_row_reports_nothing(self):
        store = _Store()
        await self._cleanup(_fleet(store), _bot(dep=None), RuntimeError("x"))
        assert store.failed == []

    async def test_a_boot_failure_is_recorded(self):
        store = _Store()
        fleet = _fleet(store)
        bot = _bot()
        bot._recover_live_state = AsyncMock(side_effect=ConnectionError("venue unreachable"))
        fleet._active_fleet["k"] = {"task": None, "bot": bot, "symbol": "ETH/USDT",
                                    "claim": None, "heartbeat": None}
        await fleet._boot_and_run(bot, "k")
        assert store.failed and "boot failed" in store.failed[0][1] and "venue unreachable" in store.failed[0][1]
        assert "k" not in fleet._active_fleet


# ── fleet: the row is the source of truth for stop / pause ───────────────────


@pytest.mark.asyncio
class TestOwnerFollowsTheRow:
    def _with_bot(self, store):
        fleet = _fleet(store)
        bot = _bot()
        fleet._active_fleet["k"] = {"task": MagicMock(), "bot": bot, "symbol": "ETH/USDT",
                                    "claim": None, "heartbeat": None}
        return fleet, bot

    async def test_a_stopping_row_makes_the_owner_stop(self):
        fleet, _ = self._with_bot(_Store({"d1": "stopping"}))
        assert await fleet._stop_requested("k") is True

    async def test_a_running_row_does_not(self):
        fleet, _ = self._with_bot(_Store({"d1": "running"}))
        assert await fleet._stop_requested("k") is False

    async def test_an_unreadable_row_is_not_a_stop(self):
        fleet, _ = self._with_bot(_Store({}))     # status_of -> None
        assert await fleet._stop_requested("k") is False

    async def test_pause_and_resume_reach_the_bot(self):
        store = _Store({"d1": "paused"})
        fleet, bot = self._with_bot(store)
        await fleet._stop_requested("k")
        assert bot.paused is True
        store.statuses["d1"] = "running"
        await fleet._stop_requested("k")
        assert bot.paused is False

    async def test_honouring_a_stop_stops_the_bot_and_confirms_it(self):
        store = _Store({"d1": "stopping"})
        fleet, bot = self._with_bot(store)
        fleet.stop_bot = AsyncMock(return_value=(True, "ok"))
        await fleet._honour_stop_request("k")
        fleet.stop_bot.assert_awaited_once_with("u1", "ETH/USDT")
        assert store.stopped == ["d1"], "'stopped' is written only after the runtime stopped"

    async def test_a_failed_local_stop_is_not_confirmed(self):
        store = _Store({"d1": "stopping"})
        fleet, _ = self._with_bot(store)
        fleet.stop_bot = AsyncMock(return_value=(False, "no bot"))
        await fleet._honour_stop_request("k")
        assert store.stopped == []

    def test_a_paused_bot_trades_nothing(self):
        src = (REPO / "backend_app/backend/master_executor.py").read_text()
        assert "if self.paused:" in src and src.index("if self.paused:") < src.index("risk_manager.validate_trade_request")


# ── resume after a restart ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestResume:
    async def test_paper_rows_are_started_and_stop_requests_are_finalised(self):
        store = _Store()
        store.stopping_rows = [_row("s1", "stopping")]
        store.live_rows = [_row("p1"), _row("p2")]
        fleet = _fleet(store)
        fleet.start_bot = AsyncMock(side_effect=[(True, "ETH/USDT"), (False, "Another process already owns")])
        rebuild = lambda row: ({"symbol": "ETH/USDT", "deployment_id": row["id"]}, True)  # noqa: E731

        outcome = await fleet.resume_deployments(rebuild)

        assert outcome == {"started": 1, "refused": 1, "failed": 0, "finalised_stop": 1}
        assert store.stopped == ["s1"], "a requested stop is not restarted"
        assert fleet.start_bot.await_args_list[0].kwargs == {"paper_trading": True}

    async def test_a_row_that_cannot_be_rebuilt_is_failed_with_the_reason_not_left_running(self):
        store = _Store()
        store.live_rows = [_row("p1")]
        fleet = _fleet(store)
        fleet.start_bot = AsyncMock()

        def rebuild(row):
            raise dr.RuntimeBlueprintError("the deployed strategy version no longer exists")

        outcome = await fleet.resume_deployments(rebuild)

        assert outcome["failed"] == 1 and fleet.start_bot.await_count == 0
        assert store.failed and "no longer exists" in store.failed[0][1]

    async def test_live_deployments_are_not_auto_resumed_by_default(self, monkeypatch):
        monkeypatch.delenv("RESUME_LIVE_DEPLOYMENTS", raising=False)
        with pytest.raises(dr.RuntimeBlueprintError, match="not resumed automatically"):
            await dr.rebuild_for_resume(_row("l1", mode="live", environment="live"),
                                        load_version=lambda _id: {"id": "v1"})

    async def test_live_resume_needs_the_operators_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("RESUME_LIVE_DEPLOYMENTS", "true")
        # opted in: it proceeds as far as loading the version (which here does not exist)
        with pytest.raises(dr.RuntimeBlueprintError, match="no longer exists"):
            await dr.rebuild_for_resume(_row("l1", mode="live", environment="live"),
                                        load_version=lambda _id: None)

    async def test_a_paper_row_is_rebuilt_from_its_version(self):
        from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import \
            version_row as _unused  # noqa: F401  (fixture module import keeps one source)
        from backend_app.backend import strategy_compiler as SC
        from backend_app.backend.strategy_dag import registry as registry_module
        from tests.test_plan_engine_adaptation_fidelity import gate_graph

        reg = registry_module.build_registry()
        graph, _ = gate_graph(reg)
        plan = SC.compile_graph(graph, reg)
        version = {"id": "v1", "version": "1.0", "blueprint": graph.to_dict(),
                   "compiled_plan": plan.to_json()}

        blueprint, paper = await dr.rebuild_for_resume(_row("p1"), load_version=AsyncMock(return_value=version))

        assert paper is True and blueprint["runtime"] == dr.RUNTIME_DAG
        assert blueprint["deployment_id"] == "p1" and blueprint["symbol"] == "ETH/USDT"
        assert blueprint["timeframe"] == "5m" and blueprint["exchange_id"] == "binance"
        dr.GraphSignalEvaluator(blueprint)      # and it is actually startable


# ── stop_deployment ──────────────────────────────────────────────────────────


from tests.test_strategy_status_publish import (  # noqa: E402
    DEPLOYMENT_ID, STRATEGY_ID, _deployed_row, _service_with, _Supabase as _StatusSB, _user)


@pytest.mark.asyncio
class TestStopDeployment:
    async def _stop(self, row, *, fleet_result):
        sb = _StatusSB({"strategy_deployments": [row]})
        ws = MagicMock()
        ws.broadcast_user = AsyncMock(return_value=None)
        service, app_state = _service_with(sb, ws)
        service._get_supabase = AsyncMock(return_value=sb)
        app_state.fleet.stop_bot = AsyncMock(return_value=fleet_result)
        with patch("backend_app.core.state.app_state", app_state):
            assert await service.stop_deployment(user=_user(), deployment_id=DEPLOYMENT_ID) is True
        return sb, ws, app_state

    async def test_the_bot_is_stopped_by_its_symbol_not_its_venue(self):
        row = _deployed_row()
        sb, _ws, state = await self._stop(row, fleet_result=(True, "stopped"))
        kwargs = state.fleet.stop_bot.await_args.kwargs
        assert kwargs["symbol"] == "BTC/USDT" and kwargs["symbol"] != row["exchange_id"]

    async def test_a_local_stop_is_stopped(self):
        sb, ws, _ = await self._stop(_deployed_row(), fleet_result=(True, "stopped"))
        updates = [p for t, p, _f in sb.updates if t == "strategy_deployments"]
        assert updates[-1]["status"] == "stopped" and "stopped_at" in updates[-1]
        assert ws.broadcast_user.await_args[0][1]["status"] == "stopped"

    async def test_a_bot_owned_elsewhere_gets_a_durable_stop_request_not_a_false_stopped(self):
        sb, ws, _ = await self._stop(_deployed_row(), fleet_result=(False, "No active bot"))
        updates = [p for t, p, _f in sb.updates if t == "strategy_deployments"]
        assert updates[-1] == {"status": "stopping"}, "only the owning runtime may write 'stopped'"
        assert ws.broadcast_user.await_args[0][1]["status"] == "stopping"


# ── RT-03: the legacy routes delegate ────────────────────────────────────────


def _calls(path, attr):
    tree = ast.parse(path.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


class TestLegacyRoutesDelegate:
    ROUTER = REPO / "backend_app/routers/strategies.py"

    def test_the_legacy_router_no_longer_starts_or_stops_bots_itself(self):
        assert _calls(self.ROUTER, "start_bot") == []
        assert _calls(self.ROUTER, "stop_bot") == []

    def test_deploy_and_resume_use_the_one_canonical_helper(self):
        tree = ast.parse(self.ROUTER.read_text())
        users = {f.name for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef)
                 and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_deploy_current_version_paper"
                         for n in ast.walk(f))}
        assert users == {"deploy_bot", "resume_strategy"}

    @pytest.mark.asyncio
    async def test_the_helper_deploys_the_current_version_in_paper_mode_only(self):
        from fastapi import HTTPException

        from backend_app.routers import strategies as router

        service = MagicMock()
        service.get_strategy = AsyncMock(return_value={"version": {"version": "v7"}})
        service.deploy_version = AsyncMock(return_value={"success": True, "deployment": {"id": "d9"}})
        with patch("backend_app.backend.strategy_service.get_strategy_service",
                   AsyncMock(return_value=service)):
            out = await router._deploy_current_version_paper({"id": "u1"}, "s1")
        assert out["deployment"]["id"] == "d9"
        kw = service.deploy_version.await_args.kwargs
        assert kw["version"] == "v7" and kw["environment"] == "paper"
        assert kw["binding_request"].mode == "paper"

        service.get_strategy = AsyncMock(return_value={"version": None})
        with patch("backend_app.backend.strategy_service.get_strategy_service",
                   AsyncMock(return_value=service)), pytest.raises(HTTPException) as e:
            await router._deploy_current_version_paper({"id": "u1"}, "s1")
        assert e.value.status_code == 409

    @pytest.mark.asyncio
    async def test_a_refused_deploy_surfaces_its_own_status_and_a_failed_start_is_a_400(self):
        from fastapi import HTTPException

        from backend_app.backend.deployment_binding import DeployRejected
        from backend_app.routers import strategies as router

        service = MagicMock()
        service.get_strategy = AsyncMock(return_value={"version": {"version": "v7"}})
        with patch("backend_app.backend.strategy_service.get_strategy_service",
                   AsyncMock(return_value=service)):
            service.deploy_version = AsyncMock(side_effect=DeployRejected("X", "no", http_status=422))
            with pytest.raises(HTTPException) as e:
                await router._deploy_current_version_paper({"id": "u1"}, "s1")
            assert e.value.status_code == 422
            service.deploy_version = AsyncMock(return_value={"success": False, "message": "no feed"})
            with pytest.raises(HTTPException) as e:
                await router._deploy_current_version_paper({"id": "u1"}, "s1")
            assert e.value.status_code == 400 and "no feed" in str(e.value.detail)
