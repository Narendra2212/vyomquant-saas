# -*- coding: utf-8 -*-
"""tests/test_task_6_1_backtest_execute.py

A backtest runs the version's own artifact, with the caller's own configuration.

Trading-lifecycle-integration task 6.1 — ``POST .../strategies/{id}/backtests/execute``.
Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 22.3, plus Requirement 3.3's archived
refusal (task 5.1 deferred it to this endpoint) and Requirement 6.2's "no parameter is
accepted and then ignored".

THE THREE DEFECTS THIS FILE PINS DOWN
-------------------------------------
1. **The graph came from the client.** The handler required an ``execution_graph`` in the
   request body and ran *that*, so the backtest executed an artifact assembled in the
   browser instead of the version's persisted ``compiled_plan`` — the disagreement between
   backtest and live that Requirements 5.2/5.7/22.3 exist to prevent. Asserted here as: the
   run receives the **version row**, the version row's own plan is what the runtime is
   handed, and a hostile ``execution_graph`` in the body reaches nothing and is reported
   back as ignored.
2. **Every configuration field was discarded.** The endpoint accepted
   ``initial_capital``, ``commission``, ``slippage``, ``risk_per_trade``, ``max_drawdown``
   and ``daily_loss_limit`` and then ran the shared singleton runtime, which is constructed
   once with the class defaults. Asserted against the **engines**, not against the response:
   a real ``BacktestRuntime`` built by the endpoint's own factory carries the submitted
   numbers on its ``BacktestEngine`` and its ``RiskEngine``.
3. **The venue was ``ccxt.binance()``, hardcoded in a router.** Asserted structurally (the
   handler's source names no exchange) and behaviourally (the feed comes from the module's
   one seam, which refuses when the server names no venue).

And the one refusal task 5.1 left for this task: an archived strategy is not backtestable
(Requirement 3.3), answered by the same ``ArchiveRejected`` → HTTP mapping the deploy route
already uses, so "archived" cannot mean one thing here and another there.

WHAT IS REAL AND WHAT IS SUPPLIED
---------------------------------
Real: the FastAPI app and its routing, the request model and its validation, the archive
gate, the ``ArchiveRejected`` → status mapping, the ownership predicate (the fake service
applies ``id`` **and** ``user_id`` exactly as ``StrategyService.get_strategy``'s two
``.eq`` filters do), the ``BacktestRuntime``/``BacktestEngine``/``RiskEngine`` constructors,
and ``BacktestService.create_backtest``'s payload builder.

Supplied: the PostgREST client (this environment has no instance of one), the market-data
connection (``_backtest_exchange_instance``), and — for the HTTP tests only — the run
itself, through the endpoint's ``_backtest_runtime_for`` seam. A VectorBT simulation over a
live feed is not what these assertions are about; *which artifact, which configuration and
which venue reach it* is.

WHAT THIS FILE CANNOT PROVE
---------------------------
There is no PostgreSQL here, so nothing here proves the ``strategy_backtests`` insert
satisfies its NOT NULL constraints or its foreign keys; the ``version_id`` assertion is
about the payload the service builds. Requirement 5.6's VectorBT-versus-fallback marking
lives in ``backtesting_engine`` and is not this task's surface. Property tests for
configuration bounds, entry-path independence and result immutability are tasks 6.2-6.4.
"""

import inspect
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_archive as archive
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.routers import strategy_operations as SO

OWNER = {
    "id": "usr_backtest_owner",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}

INTRUDER = {
    "id": "usr_backtest_intruder",
    "email": "intruder@example.com",
    "role": "authenticated",
    "access_token": "token_intruder",
}

STRATEGY_ID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
CURRENT_VERSION_ID = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
OLDER_VERSION_ID = "cccccccc-3333-4333-8333-cccccccccccc"
FOREIGN_VERSION_ID = "dddddddd-4444-4444-8444-dddddddddddd"
BACKTEST_ID = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee"

CANONICAL_PATH = (
    f"/api/strategy-operations/strategies/{STRATEGY_ID}/backtests/execute"
)
COMPAT_PATH = f"/api/strategies/{STRATEGY_ID}/backtests/execute"

#: A complete, valid body. Every numeric value is deliberately unlike the runtime's own
#: default, so "the default was used" and "the submitted value was used" cannot be
#: confused for one another.
BODY = {
    "start_date": "2024-01-01",
    "end_date": "2024-03-01",
    "initial_capital": 54_321.0,
    "commission": 0.0025,
    "slippage": 0.0031,
    "spread": 0.0007,
    "risk_per_trade": 0.02,
    "max_drawdown": 0.33,
    "daily_loss_limit": 0.11,
}


# ---------------------------------------------------------------------------
# Doubles: the store, the service, the feed, the run
# ---------------------------------------------------------------------------


def _version_row(version_id: str, label: str, strategy_id: str = STRATEGY_ID) -> Dict[str, Any]:
    """A ``strategy_versions`` row carrying a persisted plan, the way a saved version does.

    ``compiled_plan`` is opaque here on purpose: this file asserts that the **row** reaches
    the runtime, not what the compiler makes of it. Whether the persisted plan is reused or
    recompiled on a hash mismatch is ``strategy_compiler.load_plan``'s own contract and is
    covered where that lives.
    """
    return {
        "id": version_id,
        "strategy_id": strategy_id,
        "version": label,
        "graph_json": {"schema_version": 2, "nodes": [], "edges": []},
        "compiled_plan": {"dag_hash": f"hash-{label}"},
        "lifecycle_state": "READY",
    }


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters: Dict[str, Any] = {}

    def select(self, *a, **kw):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    async def execute(self):
        self._parent.reads.append((self._table, dict(self._filters)))
        rows = self._parent.rows.get(self._table, [])
        return _Result(
            [
                dict(row)
                for row in rows
                if all(str(row.get(k)) == str(v) for k, v in self._filters.items())
            ]
        )


class _Supabase:
    """A PostgREST stand-in that honours ``.eq`` and records what was read."""

    def __init__(self, rows: Dict[str, List[Dict[str, Any]]]):
        self.rows = {k: [dict(r) for r in v] for k, v in rows.items()}
        self.reads: List[Any] = []

    def table(self, name):
        return _Query(self, name)


class FakeStrategyService:
    """``StrategyService``'s two reads this endpoint uses, with the real ownership rule.

    ``get_strategy`` applies ``id`` **and** ``user_id``, exactly as the real method's two
    ``.eq`` filters do, so the cross-tenant case here is a real 404 rather than a mocked
    one.
    """

    def __init__(self, strategies: List[Dict[str, Any]], versions: List[Dict[str, Any]]):
        self.strategies = strategies
        self.supabase = _Supabase({"strategy_versions": versions})

    async def get_strategy(self, user: dict, strategy_id: str):
        for row in self.strategies:
            if str(row["id"]) == str(strategy_id) and str(row["user_id"]) == str(user["id"]):
                return {
                    "strategy": {k: v for k, v in row.items() if k != "current_version"},
                    "version": row.get("current_version"),
                    "deployments": [],
                    "performance": {},
                }
        return None

    async def _get_supabase(self, user: dict):
        return self.supabase


class RecordingRuntime:
    """The endpoint's run seam. Records the call; runs no simulation."""

    def __init__(self, body, result=None, raises=None):
        self.body = body
        self.calls: List[Dict[str, Any]] = []
        self.raises = raises
        self.result = (
            result
            if result is not None
            else {
                "backtest_id": BACKTEST_ID,
                "status": "completed",
                "results": {"Total Trades": 7, "final_capital": 61_000.0},
            }
        )

    async def run_version_backtest(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result

    async def run_backtest(self, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError(
            "The canonical endpoint must go through run_version_backtest, so that the "
            "version's own persisted plan is what executes (Requirement 5.2)."
        )


class RecordingFeed:
    """The one market-data boundary. Returns a sentinel the run is asserted to receive."""

    SENTINEL = object()

    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.SENTINEL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_rate_limit():
    """The route's 10/minute limit is real and shared; no test may inherit another's count."""
    from backend_app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def fresh_archive_probe():
    """The 005a column verdict is cached per process."""
    archive.reset_archive_column_support()
    yield
    archive.reset_archive_column_support()


def _strategy_row(**overrides) -> Dict[str, Any]:
    row = {
        "id": STRATEGY_ID,
        "user_id": OWNER["id"],
        "name": "Task 6.1 Strategy",
        "status": "stopped",
        archive.ARCHIVED_AT_COLUMN: None,
        "current_version": _version_row(CURRENT_VERSION_ID, "v2.0"),
    }
    row.update(overrides)
    return row


@pytest.fixture
def service(monkeypatch):
    state = FakeStrategyService(
        strategies=[_strategy_row()],
        versions=[
            _version_row(CURRENT_VERSION_ID, "v2.0"),
            _version_row(OLDER_VERSION_ID, "v1.0"),
            # Another strategy's version, so "any id at all" cannot be backtested here.
            _version_row(FOREIGN_VERSION_ID, "v9.0", strategy_id="other-strategy"),
        ],
    )

    async def factory():
        return state

    monkeypatch.setattr(SO, "get_strategy_service", factory)
    return state


@pytest.fixture
def feed(monkeypatch):
    recorder = RecordingFeed()
    monkeypatch.setattr(SO, "_backtest_exchange_instance", recorder)
    return recorder


@pytest.fixture
def runtime(monkeypatch):
    """Captures the body the factory was handed and the run it was asked for."""
    holder: Dict[str, Any] = {}

    def factory(body):
        holder["body"] = body
        holder["runtime"] = RecordingRuntime(body, raises=holder.get("raises"))
        return holder["runtime"]

    monkeypatch.setattr(SO, "_backtest_runtime_for", factory)
    return holder


@pytest.fixture
def as_owner():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def as_intruder():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: INTRUDER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def post(client, body=None, path=CANONICAL_PATH):
    return client.post(path, json={**BODY, **(body or {})})


# ---------------------------------------------------------------------------
# 1. Requirements 5.1, 5.2, 5.7 — the version's own artifact is what runs
# ---------------------------------------------------------------------------


class TestTheVersionsOwnArtifactIsWhatRuns:
    def test_the_current_version_row_is_handed_to_the_runtime(
        self, as_owner, service, feed, runtime
    ):
        response = post(as_owner)

        assert response.status_code == 200, response.text
        call = runtime["runtime"].calls[0]
        assert call["version_row"] == _version_row(CURRENT_VERSION_ID, "v2.0")
        assert call["version_id"] == CURRENT_VERSION_ID
        assert call["version"] == "v2.0"
        assert call["strategy_id"] == STRATEGY_ID
        assert call["exchange_instance"] is RecordingFeed.SENTINEL

    def test_the_persisted_plan_travels_on_that_row(self, as_owner, service, feed, runtime):
        """Requirement 5.2/22.3: the run resolves the plan off the row, not off the request."""
        post(as_owner)

        row = runtime["runtime"].calls[0]["version_row"]
        assert row["compiled_plan"] == {"dag_hash": "hash-v2.0"}

    def test_a_named_version_is_loaded_and_scoped_to_this_strategy(
        self, as_owner, service, feed, runtime
    ):
        """Requirement 4.3: any persisted version of this strategy may be backtested."""
        response = post(as_owner, {"version_id": OLDER_VERSION_ID})

        assert response.status_code == 200, response.text
        call = runtime["runtime"].calls[0]
        assert call["version_id"] == OLDER_VERSION_ID
        assert call["version"] == "v1.0"
        # The read is filtered on the strategy as well as the version id.
        reads = [r for r in service.supabase.reads if r[0] == "strategy_versions"]
        assert reads[0][1] == {"id": OLDER_VERSION_ID, "strategy_id": STRATEGY_ID}

    def test_another_strategys_version_id_is_not_backtestable_here(
        self, as_owner, service, feed, runtime
    ):
        response = post(as_owner, {"version_id": FOREIGN_VERSION_ID})

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "BACKTEST_VERSION_UNAVAILABLE"
        assert "runtime" not in runtime, "nothing may run once the version is refused"

    def test_a_strategy_with_no_saved_version_is_refused_naming_the_version(
        self, as_owner, service, feed, runtime
    ):
        """Requirement 5.4: the Strategy_Version is named as unavailable for backtesting."""
        service.strategies = [_strategy_row(current_version=None)]

        response = post(as_owner)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "BACKTEST_VERSION_UNAVAILABLE"
        assert detail["strategy_id"] == STRATEGY_ID
        assert "runtime" not in runtime

    def test_run_backtest_is_never_the_entry_point(self, as_owner, service, feed, runtime):
        """``RecordingRuntime.run_backtest`` asserts if reached; a 200 proves it was not."""
        assert post(as_owner).status_code == 200


# ---------------------------------------------------------------------------
# 2. Requirement 5.7 — a client-supplied execution graph reaches nothing
# ---------------------------------------------------------------------------


class TestTheCallerNoLongerReconstructsTheGraph:
    def test_execution_graph_is_no_longer_required(self, as_owner, service, feed, runtime):
        """It used to be a mandatory field. A body without it is now a complete request."""
        assert "execution_graph" not in BODY
        assert post(as_owner).status_code == 200

    def test_a_supplied_execution_graph_changes_nothing_and_is_named_as_ignored(
        self, as_owner, service, feed, runtime
    ):
        hostile = {
            "id": "not-the-version",
            "nodes": [{"id": "n1", "type": "ACTION"}],
            "edges": [],
            "execution_order": ["n1"],
            "metadata": {"symbols": ["DOGE/USDT"]},
        }

        response = post(as_owner, {"execution_graph": hostile})

        assert response.status_code == 200, response.text
        assert response.json()["ignored_fields"] == ["execution_graph"]
        call = runtime["runtime"].calls[0]
        assert call["version_row"]["id"] == CURRENT_VERSION_ID
        assert hostile not in call.values()

    def test_nothing_is_reported_as_ignored_when_nothing_was(
        self, as_owner, service, feed, runtime
    ):
        assert post(as_owner).json()["ignored_fields"] == []

    def test_a_market_or_venue_field_is_refused_rather_than_silently_dropped(
        self, as_owner, service, feed, runtime
    ):
        """Requirement 6.2 / SB-06: market identity is the version's, the venue the server's."""
        for smuggled in ("exchange", "symbol", "timeframe", "api_key", "exchange_account_id"):
            response = post(as_owner, {smuggled: "binance"})
            assert response.status_code == 422, smuggled


# ---------------------------------------------------------------------------
# 3. Requirement 6.2 — every accepted configuration field reaches the engines
# ---------------------------------------------------------------------------


class TestSubmittedConfigurationReachesTheEngines:
    def _body(self, **overrides):
        return SO.BacktestExecuteRequest(**{**BODY, **overrides})

    def test_the_runtime_the_factory_builds_carries_every_submitted_value(self):
        """Asserted on the ``BacktestEngine`` and the ``RiskEngine``, not on a response field."""
        runtime = SO._backtest_runtime_for(self._body())

        engine = runtime.vectorbt_engine
        assert engine.initial_capital == 54_321.0
        assert engine.fees == 0.0025
        assert engine.slippage == 0.0031
        assert engine.spread == 0.0007

        risk = runtime.risk_engine
        assert risk.risk_per_trade == Decimal("0.02")
        assert risk.max_drawdown == Decimal("0.33")
        assert risk.daily_loss_limit == Decimal("0.11")

    def test_it_is_not_the_shared_singleton_running_on_the_class_defaults(self):
        from backend_app.backend.backtest_runtime import get_backtest_runtime

        built = SO._backtest_runtime_for(self._body())

        assert built is not get_backtest_runtime()
        assert built.vectorbt_engine.initial_capital != (
            get_backtest_runtime().vectorbt_engine.initial_capital
        )

    def test_two_different_configurations_produce_two_differently_configured_runtimes(self):
        a = SO._backtest_runtime_for(self._body(initial_capital=1_000.0, commission=0.001))
        b = SO._backtest_runtime_for(self._body(initial_capital=2_000.0, commission=0.009))

        assert (a.vectorbt_engine.initial_capital, a.vectorbt_engine.fees) == (1_000.0, 0.001)
        assert (b.vectorbt_engine.initial_capital, b.vectorbt_engine.fees) == (2_000.0, 0.009)

    def test_the_response_states_the_configuration_that_was_applied(
        self, as_owner, service, feed, runtime
    ):
        body = response = post(as_owner).json()

        assert body["configuration"] == {
            "initial_capital": 54_321.0,
            "commission": 0.0025,
            "slippage": 0.0031,
            "spread": 0.0007,
            "risk_per_trade": 0.02,
            "max_drawdown": 0.33,
            "daily_loss_limit": 0.11,
            "start_date": "2024-01-01",
            "end_date": "2024-03-01",
        }
        # And the same values are what the factory was handed.
        assert runtime["body"].initial_capital == 54_321.0
        assert response["configuration"]["commission"] == runtime["body"].commission


# ---------------------------------------------------------------------------
# 4. Requirement 3.3 — an archived strategy is not backtestable
# ---------------------------------------------------------------------------


class TestArchivedStrategiesAreRefused:
    def test_it_answers_409_naming_the_operation_and_the_timestamp(
        self, as_owner, service, feed, runtime
    ):
        stamp = "2024-05-05T10:00:00+00:00"
        service.strategies = [_strategy_row(**{archive.ARCHIVED_AT_COLUMN: stamp})]

        response = post(as_owner)

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_ARCHIVED"
        assert detail[archive.ARCHIVED_AT_COLUMN] == stamp
        assert detail["operation"] == archive.OPERATION_BACKTEST

    def test_nothing_runs_and_no_feed_is_opened(self, as_owner, service, feed, runtime):
        service.strategies = [
            _strategy_row(**{archive.ARCHIVED_AT_COLUMN: "2024-05-05T10:00:00+00:00"})
        ]

        post(as_owner)

        assert "runtime" not in runtime
        assert feed.calls == 0

    def test_backtest_is_one_of_the_operations_the_archive_gate_refuses(self):
        """The vocabulary is the archive module's, not a second spelling invented here."""
        assert archive.OPERATION_BACKTEST in archive.ARCHIVED_REFUSED_OPERATIONS

    def test_a_row_with_no_archival_column_is_treated_as_active(
        self, as_owner, service, feed, runtime
    ):
        """005a is applied by hand: an absent key means nothing has been archived."""
        row = _strategy_row()
        row.pop(archive.ARCHIVED_AT_COLUMN)
        service.strategies = [row]

        assert post(as_owner).status_code == 200


# ---------------------------------------------------------------------------
# 5. Requirement 20.2 — ownership before anything else
# ---------------------------------------------------------------------------


class TestOwnership:
    def test_another_tenant_gets_404_and_nothing_runs(
        self, as_intruder, service, feed, runtime
    ):
        response = post(as_intruder)

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"
        assert "runtime" not in runtime
        assert feed.calls == 0

    def test_an_unknown_strategy_answers_identically(self, as_owner, service, feed, runtime):
        missing = "ffffffff-6666-4666-8666-ffffffffffff"

        response = as_owner.post(
            f"/api/strategy-operations/strategies/{missing}/backtests/execute", json=BODY
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"


# ---------------------------------------------------------------------------
# 6. One response, carrying the persisted result
# ---------------------------------------------------------------------------


class TestOneSynchronousResponse:
    def test_it_returns_the_persisted_result_directly(self, as_owner, service, feed, runtime):
        body = post(as_owner).json()

        assert body["backtest_id"] == BACKTEST_ID
        assert body["status"] == "completed"
        assert body["results"] == {"Total Trades": 7, "final_capital": 61_000.0}
        assert body["strategy_id"] == STRATEGY_ID
        assert body["version_id"] == CURRENT_VERSION_ID
        assert body["version"] == "v2.0"

    def test_it_carries_no_job_id_to_poll(self, as_owner, service, feed, runtime):
        body = post(as_owner).json()

        assert "job_id" not in body
        assert body["status"] != "queued"

    def test_create_backtest_records_the_version_that_ran(self):
        """The seam the canonical run depends on, and that used to raise ``TypeError``.

        ``BacktestRuntime.run_backtest`` has always called ``create_backtest`` with
        ``version_id=…``; the signature did not accept it, so the canonical path could not
        reach the simulator at all. ``strategy_backtests.version_id`` is NOT NULL, so the
        row could not be written without it either.
        """
        from backend_app.backend.backtest_service import BacktestService

        signature = inspect.signature(BacktestService.create_backtest)
        assert "version_id" in signature.parameters

        recorded: Dict[str, Any] = {}

        class _Insert:
            def __init__(self, payload):
                recorded.update(payload)

            async def execute(self):
                return _Result([dict(recorded)])

        class _Table:
            def insert(self, payload):
                return _Insert(payload)

        class _Client:
            def table(self, name):
                recorded["__table__"] = name
                return _Table()

        service = BacktestService()

        async def _client(user):
            return _Client()

        service._get_supabase = _client

        import asyncio

        row = asyncio.run(
            service.create_backtest(
                user=OWNER,
                strategy_id=STRATEGY_ID,
                version_id=CURRENT_VERSION_ID,
                version="v2.0",
                blueprint={"schema_version": 2, "dag_hash": "hash-v2.0"},
                dataset="BTC/USDT",
                start_date="2024-01-01",
                end_date="2024-03-01",
                initial_capital=54_321.0,
                commission=0.0025,
                slippage=0.0031,
            )
        )

        assert recorded["__table__"] == "strategy_backtests"
        assert recorded["version_id"] == CURRENT_VERSION_ID
        assert recorded["version"] == "v2.0", "the row names the version that ran"
        assert recorded["strategy_id"] == STRATEGY_ID
        assert recorded["initial_capital"] == 54_321.0
        assert row["id"] == recorded["id"]


# ---------------------------------------------------------------------------
# 7. The venue is the server's public feed, never a hardcoded exchange
# ---------------------------------------------------------------------------


class TestTheVenueIsResolvedNotHardcoded:
    def test_the_handler_names_no_exchange(self):
        """The code, not the prose: the docstring is allowed to say what was removed."""
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(SO.execute_backtest)))
        function = tree.body[0]
        first = function.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            function.body = function.body[1:]
        code = ast.unparse(function).lower()

        assert "ccxt" not in code
        assert "binance" not in code

    def test_the_feed_seam_refuses_when_the_server_names_no_venue(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.delenv("DEFAULT_EXCHANGE", raising=False)

        import asyncio

        with pytest.raises(HTTPException) as caught:
            asyncio.run(SO._backtest_exchange_instance())

        assert caught.value.status_code == 503
        assert caught.value.detail["error"] == "BACKTEST_FEED_UNCONFIGURED"

    def test_the_feed_seam_reads_the_servers_setting(self, monkeypatch):
        """The venue comes from ``DEFAULT_EXCHANGE`` — the same resolution the preview uses."""
        seen = {}

        class _Engine:
            def __init__(self, exchange_id=None, **kw):
                seen["exchange_id"] = exchange_id

            async def connect(self):
                return "connected"

        monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
        monkeypatch.setattr(
            "backend_app.backend.connection_engine.ConnectionEngine", _Engine
        )

        import asyncio

        assert asyncio.run(SO._backtest_exchange_instance()) == "connected"
        assert seen["exchange_id"] == "kraken"


# ---------------------------------------------------------------------------
# 8. Failure modes are answers about the request, not 500s
# ---------------------------------------------------------------------------


class TestRefusalsFromTheRuntime:
    def test_a_version_that_no_longer_compiles_names_the_version(
        self, as_owner, service, feed, runtime, monkeypatch
    ):
        """Requirement 5.4: reported as the version being unavailable for backtesting."""
        from backend_app.backend.strategy_compiler import CompilerError

        runtime["raises"] = CompilerError("Row carries no loadable strategy graph")

        response = post(as_owner)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "BACKTEST_VERSION_UNAVAILABLE"
        assert detail["version_id"] == CURRENT_VERSION_ID

    def test_a_version_declaring_no_market_is_refused_not_substituted(
        self, as_owner, service, feed, runtime
    ):
        """SB-06: nothing substitutes BTC/USDT for a version that names no symbol."""
        runtime["raises"] = ValueError(
            "Version v2.0 declares no symbol on any DATA node; there is nothing to "
            "backtest against."
        )

        response = post(as_owner)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "BACKTEST_NOT_RUNNABLE"
        assert "no symbol" in detail["message"]

    def test_an_unexpected_failure_is_still_a_500_with_its_code(
        self, as_owner, service, feed, runtime
    ):
        runtime["raises"] = RuntimeError("the simulator exploded")

        response = post(as_owner)

        assert response.status_code == 500
        assert response.json()["detail"]["error"] == "BACKTEST_EXECUTE_FAILED"


# ---------------------------------------------------------------------------
# 9. Requirement 22.3 — nothing that already worked stopped working
# ---------------------------------------------------------------------------


class TestNothingElseMoved:
    def _paths(self):
        from backend_app.main import app

        return {
            (path, method)
            for route in app.routes
            for path in [getattr(route, "path", "")]
            for method in (getattr(route, "methods", None) or set())
        }

    def test_the_endpoint_is_reachable_at_the_canonical_path(self):
        assert (
            "/api/strategy-operations/strategies/{strategy_id}/backtests/execute",
            "POST",
        ) in self._paths()

    def test_the_pre_existing_path_still_resolves_to_the_same_handler(
        self, as_owner, service, feed, runtime
    ):
        response = post(as_owner, path=COMPAT_PATH)

        assert response.status_code == 200, response.text
        assert response.json()["version_id"] == CURRENT_VERSION_ID

    def test_the_legacy_job_queue_endpoints_are_untouched(self):
        """``design.md``: RETAINED, NOT EXTENDED. Requirement 22.3, and task 6.5's subject."""
        paths = self._paths()

        assert ("/api/strategies/backtest", "POST") in paths
        assert ("/api/strategies/backtest/{job_id}", "GET") in paths
        assert ("/api/strategies/{strategy_id}/deploy", "POST") in paths

    def test_the_two_step_save_endpoints_are_still_there(self):
        """Task 17.2 removes the client-side glue; the endpoints stay for other callers."""
        paths = self._paths()

        assert ("/api/strategies/{strategy_id}/backtests", "POST") in paths
        assert ("/api/backtests/{backtest_id}/results", "PUT") in paths
