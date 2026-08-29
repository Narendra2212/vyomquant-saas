"""
tests/test_task_2_4_call_site_migration.py

The behaviour task 2.4 buys, held in place.

Spec: strategy-builder task 2.4 (`design.md` -> The single compiler -> Migration path for
callers). Requirements 3.1, 3.2, 22.3, 22.5, plus the SB-02 and SB-06 regressions.

What these tests hold in place:

* **SB-01 (Requirement 3.2).** One graph submitted through the validate path, the save
  path, the clone path, the strategy-operations compile path and the plan load path gets
  the *same* validity verdict and the *same* error code set from every one of them. That
  is the whole defect: the router carried a second compiler with a drifted rule set, so a
  graph could be accepted on save and rejected on clone. The verdict is compared as a set
  of codes rather than as a message, because a message is prose and a code is a contract.

* **SB-02.** A clone carries a non-empty `dag_hash`. The pre-fix path called
  `compiled.get("dag_hash")` on a `CompiledDAG` *object*, and the `AttributeError` was
  swallowed by `except Exception`, so every clone was persisted with no hash while the
  comment above it claimed otherwise. The test asserts the persisted payload, not the
  return value, because the return value was never the thing that was wrong.

* **SB-06.** A saved strategy carries the market identity its DATA node declares and no
  exchange identity at all. The pre-fix save path wrote
  `body.get("symbol", "BTC/USDT")`, `body.get("timeframe", "5m")` and
  `body.get("exchange_id", "binance")`, so a payload that omitted them was silently
  persisted against BTC/USDT on Binance. The test asserts on the insert payload and, for
  the fallbacks specifically, on the source of the handler - a literal that is not in the
  function cannot fire under any input.

* **Requirement 22.5.** The plan load path reuses a persisted `compiled_plan` when its
  identity hash matches the version's graph and recompiles when it does not. Both
  branches are asserted, because a load path that always recompiles is as wrong as one
  that never does - the first discards the shared artifact, the second executes a
  strategy the plan does not describe. A missing or NULL `compiled_plan` is exercised
  too: migration 004 part 1 is not applied, so that is currently *every* row.

* **The compiler is singular.** `DAGCompiler` and `CompiledDAG` are gone from every
  module under `backend_app/routers/`, so the two-compiler state cannot be reintroduced
  without this test failing.

Nothing is faked. The registry is the real assembled one and the graphs are real
canonical graphs built from published descriptors; only the database client is a double,
because these tests are about what gets written, not about Supabase.
"""

import ast
import inspect
import os
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
)
from backend_app.core.dependencies import get_current_user, get_request_supabase

ROUTERS_DIR = pathlib.Path(__file__).resolve().parents[1] / "backend_app" / "routers"


# ---------------------------------------------------------------------------
# Fixtures and graph builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def valid_graph(reg, symbol="ETH/USDT", timeframe="1h") -> StrategyGraph:
    """data -> ema -> gt(vs constant) -> buy. The smallest executable strategy."""
    data = _node(
        reg,
        "ohlcv_feed",
        symbol=symbol,
        timeframe=timeframe,
        market_type="spot",
        mode="streaming",
    )
    ema = _node(reg, "ema", window=20, source="close")
    const = _node(reg, "constant", value=30.0)
    gt = _node(reg, "gt")
    act = _node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    return StrategyGraph(
        nodes=[data, ema, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )


def orphan_graph(reg) -> StrategyGraph:
    """A valid strategy plus a node nothing reaches. Stage 7: ORPHAN_NODE."""
    graph = valid_graph(reg)
    graph.nodes.append(_node(reg, "sma", window=50, source="close"))
    return graph


def action_fed_by_indicator_graph(reg) -> StrategyGraph:
    """An ACTION wired straight off an indicator. The provenance rule (stage 4/9)."""
    data = _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="1h",
        market_type="spot",
        mode="streaming",
    )
    ema = _node(reg, "ema", window=20, source="close")
    act = _node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    return StrategyGraph(
        nodes=[data, ema, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", act.id, "signal"),
        ],
    )


def single_node_graph(reg) -> StrategyGraph:
    """One DATA node, no edges. No action path exists."""
    return StrategyGraph(
        nodes=[
            _node(
                reg,
                "ohlcv_feed",
                symbol="ETH/USDT",
                timeframe="1h",
                market_type="spot",
                mode="streaming",
            )
        ],
        edges=[],
    )


def unknown_block_payload() -> dict:
    """A version 1 payload naming a node type the platform does not publish."""
    return {
        "nodes": [
            {"id": "n1", "type": "quantum_oracle", "params": {}},
            {"id": "n2", "type": "action", "action": "buy"},
        ],
        "edges": [{"source": "n1", "target": "n2"}],
    }


#: The graphs that previously disagreed between the two compilers, plus one that
#: previously agreed, so a test that only ever sees rejections is not mistaken for proof.
def divergent_cases(reg):
    return {
        "valid": valid_graph(reg).to_dict(),
        "orphan_node": orphan_graph(reg).to_dict(),
        "action_fed_by_indicator": action_fed_by_indicator_graph(reg).to_dict(),
        "single_node_no_edges": single_node_graph(reg).to_dict(),
        "unknown_node_type": unknown_block_payload(),
    }


def _code_only(source: str) -> str:
    """``source`` with its docstrings and comments removed.

    These modules deliberately record what was deleted and why, so a source-text
    assertion has to look at the executable code rather than at the prose about it -
    otherwise the explanation of a removed fallback reads as the fallback.
    """
    import io
    import tokenize

    kept = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            continue  # a bare string statement: a docstring
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token.type
    return " ".join(kept)


def _codes_from_validate(payload) -> tuple:
    from backend_app.routers.strategies import _validate_payload

    report, _graph = _validate_payload(payload)
    return report.valid, frozenset(report.codes())


def _codes_from_compile(payload) -> tuple:
    """The verdict the save/clone/compile paths reach, as (valid, code set)."""
    from backend_app.routers.strategies import _compile_payload

    try:
        compiled = _compile_payload(payload)
    except SC.ValidationError as exc:
        report = exc.report
        assert report is not None, (
            "the canonical path must carry a structured report, not a bare string"
        )
        return False, frozenset(report.codes())
    return True, frozenset(compiled.report.codes())


def _codes_from_plan_load(payload) -> tuple:
    """The verdict the worker / backtester load path reaches for the same graph."""
    row = {"graph_json": payload} if payload.get("schema_version") else dict(payload)
    try:
        loaded = SC.load_plan(row)
    except SC.ValidationError as exc:
        assert exc.report is not None
        return False, frozenset(exc.report.codes())
    report = V.validate(loaded.graph)
    return True, frozenset(report.codes())


# ---------------------------------------------------------------------------
# SB-01 — one graph, one verdict, from every path (Requirement 3.2)
# ---------------------------------------------------------------------------


class TestSB01OneVerdictEverywhere:
    def test_validate_save_and_clone_agree_on_every_divergent_graph(self, reg):
        """The SB-01 property. Same graph, same verdict, same code set, every path."""
        for name, payload in divergent_cases(reg).items():
            validate_verdict = _codes_from_validate(payload)
            compile_verdict = _codes_from_compile(payload)
            load_verdict = _codes_from_plan_load(payload)

            assert validate_verdict == compile_verdict, (
                f"{name}: validate said {validate_verdict} but the save/clone path "
                f"said {compile_verdict}. That divergence IS defect SB-01."
            )
            assert validate_verdict == load_verdict, (
                f"{name}: validate said {validate_verdict} but the plan load path "
                f"said {load_verdict}."
            )

    def test_the_divergent_cases_actually_split_valid_from_invalid(self, reg):
        """A same-verdict test is worthless if every case has the same verdict."""
        verdicts = {
            name: _codes_from_validate(payload)[0]
            for name, payload in divergent_cases(reg).items()
        }
        assert verdicts["valid"] is True, verdicts
        assert not any(
            valid for name, valid in verdicts.items() if name != "valid"
        ), verdicts

    def test_each_rejection_names_a_code_and_a_fix_hint(self, reg):
        """A rejection an author cannot act on is not much better than a crash."""
        from backend_app.routers.strategies import _validate_payload

        report, _ = _validate_payload(orphan_graph(reg).to_dict())
        assert not report.valid
        assert V.CODE_ORPHAN_NODE in report.codes()
        for issue in report.errors:
            assert issue.get("code")
            assert issue.get("message")
            assert "fix_hint" in issue

    def test_strategy_operations_compile_uses_the_same_compiler(self, reg):
        """`POST /strategy-operations/strategies/compile` is the fourth caller."""
        from backend_app.routers import strategy_operations as ops

        source = inspect.getsource(ops.compile_strategy)
        assert "compile_plan" in source, (
            "the compile endpoint must go through StrategyCompiler.compile_plan"
        )
        assert "DAGConfig" not in _code_only(source), (
            "the compile endpoint must no longer build a legacy DAGConfig"
        )


# ---------------------------------------------------------------------------
# The compiler is singular
# ---------------------------------------------------------------------------


class TestCompilerSingularity:
    def test_no_router_defines_dagcompiler_or_compileddag(self):
        """No class or function by those names is DEFINED under backend_app/routers/.

        Parsed rather than grepped, so a docstring recording the removal - which several
        of these modules deliberately carry - does not read as a reintroduction.
        """
        offenders = []
        for path in ROUTERS_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name in {"DAGCompiler", "CompiledDAG"}:
                    offenders.append(f"{path.name}:{node.lineno} {node.name}")
        assert offenders == [], offenders

    def test_the_router_module_exposes_no_compiler_attribute(self):
        import backend_app.routers.strategies as strategies_router

        assert not hasattr(strategies_router, "DAGCompiler")
        assert not hasattr(strategies_router, "CompiledDAG")
        # The private edge-legality table and the enum that keyed it went with them.
        assert not hasattr(strategies_router, "TYPE_COMPATIBILITY")
        assert not hasattr(strategies_router, "NodeType")

    def test_no_router_name_is_referenced_as_a_compiler(self):
        """`DAGCompiler.compile(...)` must not appear as executable code anywhere."""
        offenders = []
        for path in ROUTERS_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in {
                    "DAGCompiler",
                    "CompiledDAG",
                }:
                    offenders.append(f"{path.name}:{node.lineno} {node.id}")
        assert offenders == [], offenders


# ---------------------------------------------------------------------------
# Requirement 22.5 — reuse the persisted plan, recompile only on a mismatch
# ---------------------------------------------------------------------------


class TestPlanLoadPath:
    def test_a_matching_persisted_plan_is_reused_verbatim(self, reg):
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)
        row = {"graph_json": graph.to_dict(), "compiled_plan": plan.to_dict()}

        loaded = SC.load_plan(row, reg)

        assert loaded.reused is True
        assert loaded.reason == "compiled_plan_hash_match"
        # Reused means the STORED bytes, not a fresh compile that happens to agree.
        assert loaded.plan.to_dict() == plan.to_dict()

    def test_every_consumer_is_served_the_same_stored_plan(self, reg):
        """Requirement 22.3: one version, one plan, whoever asks."""
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)
        row = {"graph_json": graph.to_dict(), "compiled_plan": plan.to_dict()}

        first = SC.load_plan(row, reg)
        second = SC.load_plan(row, reg)

        assert first.plan.to_dict() == second.plan.to_dict()
        assert first.dag_hash == second.dag_hash == plan.dag_hash

    def test_a_stale_plan_is_recompiled_not_executed(self, reg):
        """Requirement 22.5. A hash that disagrees means the plan is not this graph."""
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)
        stale = plan.to_dict()
        stale["dag_hash"] = "0" * 16
        row = {"graph_json": graph.to_dict(), "compiled_plan": stale}

        loaded = SC.load_plan(row, reg)

        assert loaded.reused is False
        assert loaded.reason == "compiled_plan_hash_mismatch"
        assert loaded.plan.dag_hash == plan.dag_hash != stale["dag_hash"]

    def test_a_plan_from_a_different_graph_is_not_served(self, reg):
        """The mismatch that matters: a real plan, for a different strategy."""
        graph = valid_graph(reg, symbol="ETH/USDT")
        other = valid_graph(reg, symbol="BTC/USDT")
        other_plan = SC.compile_graph(other, reg)
        assert other_plan.dag_hash != SC.compile_graph(graph, reg).dag_hash

        loaded = SC.load_plan(
            {"graph_json": graph.to_dict(), "compiled_plan": other_plan.to_dict()}, reg
        )

        assert loaded.reused is False
        assert loaded.plan.matches_graph(graph)

    @pytest.mark.parametrize("stored", [None, {}, "", {"nodes": [], "edges": []}])
    def test_an_absent_plan_compiles_from_the_graph_rather_than_crashing(
        self, reg, stored
    ):
        """Migration 004 part 1 is unapplied, so this is currently every row."""
        graph = valid_graph(reg)
        row = {"graph_json": graph.to_dict(), "compiled_plan": stored}

        loaded = SC.load_plan(row, reg)

        assert loaded.reused is False
        assert loaded.plan.matches_graph(graph)

    def test_a_row_with_no_plan_column_at_all_compiles_from_the_graph(self, reg):
        graph = valid_graph(reg)
        loaded = SC.load_plan({"graph_json": graph.to_dict()}, reg)
        assert loaded.reused is False
        assert loaded.reason == "no_stored_plan"
        assert loaded.plan.matches_graph(graph)

    def test_the_legacy_execution_graph_column_is_read_as_a_fallback(self, reg):
        """`StrategyService` puts the plan there while migration 004 is unapplied."""
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)

        loaded = SC.load_plan(
            {"graph_json": graph.to_dict(), "execution_graph": plan.to_dict()}, reg
        )

        assert loaded.reused is True
        assert loaded.reason == "execution_graph_hash_match"

    def test_a_legacy_execution_graph_document_is_ignored_not_misread(self, reg):
        """That column also holds `ExecutionGraph.to_dict()`, which is not a plan."""
        graph = valid_graph(reg)
        loaded = SC.load_plan(
            {
                "graph_json": graph.to_dict(),
                "execution_graph": {
                    "id": "eg1",
                    "version": "v1.0",
                    "nodes": [],
                    "edges": [],
                    "execution_order": [],
                    "metadata": {},
                },
            },
            reg,
        )
        assert loaded.reused is False
        assert loaded.plan.matches_graph(graph)

    def test_a_reused_plan_round_trips_through_the_one_serialization_path(self, reg):
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)
        restored = CompiledPlan.from_dict(plan.to_dict())
        assert restored.dag_hash == plan.dag_hash
        assert restored.matches_graph(graph)


class TestPlanToEngineGraph:
    def test_nodes_come_out_in_the_plans_execution_order(self, reg):
        plan = SC.compile_graph(valid_graph(reg), reg)
        nodes, _edges = SC.plan_to_engine_graph(plan)
        assert [n["id"] for n in nodes] == list(plan.execution_order)

    def test_every_category_maps_onto_an_executor_the_engine_registers(self, reg):
        from backend_app.backend.dag_engine import DAGEngine

        engine_types = set(DAGEngine(enable_tracing=False).executors)
        assert set(SC.ENGINE_NODE_TYPE) == set(BlockCategory)
        assert set(SC.ENGINE_NODE_TYPE.values()) <= engine_types

    def test_edges_are_reconstructed_from_the_plans_own_wiring(self, reg):
        graph = valid_graph(reg)
        plan = SC.compile_graph(graph, reg)
        _nodes, edges = SC.plan_to_engine_graph(plan)
        assert len(edges) == len(graph.edges)
        assert {(e["source"], e["target"]) for e in edges} == {
            (e.source, e.target) for e in graph.edges
        }

    def test_the_backtester_load_path_exists_and_uses_the_loader(self):
        from backend_app.backend.backtest_runtime import BacktestRuntime

        source = inspect.getsource(BacktestRuntime.load_version_plan)
        assert "load_plan" in source
        assert "matches_graph" in inspect.getsource(SC.load_plan)

    def test_the_worker_orders_by_the_plan_not_by_the_payload(self):
        from backend_app.backend import dag_worker

        source = inspect.getsource(dag_worker.DAGWorker._run_dag)
        assert "task_execution_nodes" in source
        assert 'dag_config.get("nodes"' not in source

    def test_the_event_loop_accepts_a_compiled_plan(self, reg):
        from backend_app.backend.dag_event_loop import DAGEventLoop

        plan = SC.compile_graph(valid_graph(reg), reg)
        loop = DAGEventLoop(symbols=["ETH/USDT"], timeframe="1h", plan=plan)

        assert loop.dag_hash == plan.dag_hash
        assert [n["id"] for n in loop.dag_nodes] == list(plan.execution_order)
        assert loop.warmup_bars == plan.warmup_bars

    def test_the_event_loop_still_accepts_the_legacy_loose_lists(self):
        from backend_app.backend.dag_event_loop import DAGEventLoop

        loop = DAGEventLoop(
            dag_nodes=[{"id": "n1", "type": "indicator"}],
            dag_edges=[],
            symbols=["ETH/USDT"],
        )
        assert loop.plan is None
        assert loop.dag_hash is None


# ---------------------------------------------------------------------------
# HTTP surface: auth, limits and scoping, plus SB-02 and SB-06
# ---------------------------------------------------------------------------


@pytest.fixture
def user():
    return {
        "id": "usr_task24_owner",
        "email": "owner@example.com",
        "role": "authenticated",
        "access_token": "token_owner",
    }


@pytest.fixture
def client(user):
    from backend_app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _capturing_sb(select_data=None, insert_data=None):
    """A Supabase double that records what would be written.

    Only the database is a double. The compile, the hash and the verdict are real.
    """
    captured = {}
    sb = MagicMock()

    select_chain = MagicMock()
    select_chain.execute = AsyncMock(return_value=MagicMock(data=select_data or []))
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value = (
        select_chain
    )

    def _insert(payload):
        captured["insert"] = payload
        chain = MagicMock()
        chain.execute = AsyncMock(
            return_value=MagicMock(data=insert_data or [{"id": "new_strategy_id"}])
        )
        return chain

    sb.table.return_value.insert.side_effect = _insert
    return sb, captured


class TestValidateEndpoint:
    def test_an_invalid_graph_returns_200_with_the_structured_report(
        self, client, reg
    ):
        response = client.post(
            "/api/strategies/validate", json={"dag": orphan_graph(reg).to_dict()}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert body["dag_hash"] is None
        codes = [issue["code"] for issue in body["errors"]]
        assert V.CODE_ORPHAN_NODE in codes
        first = body["errors"][0]
        for key in (
            "code",
            "severity",
            "node_id",
            "edge_id",
            "field",
            "message",
            "expected",
            "actual",
            "fix_hint",
        ):
            assert key in first, key

    def test_a_valid_graph_reports_a_hash_and_an_execution_path(self, client, reg):
        graph = valid_graph(reg)
        response = client.post("/api/strategies/validate", json={"dag": graph.to_dict()})

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True, body["errors"]
        assert body["dag_hash"]
        assert len(body["execution_path"]) == len(graph.nodes)

    def test_the_legacy_response_keys_survive(self, client, reg):
        body = client.post(
            "/api/strategies/validate", json={"dag": valid_graph(reg).to_dict()}
        ).json()
        for key in ("valid", "errors", "warnings", "execution_path", "node_types", "stats"):
            assert key in body, key
        assert body["stats"]["total_nodes"] == 5

    def test_an_empty_payload_still_answers_200_with_an_error(self, client):
        response = client.post("/api/strategies/validate", json={"dag": {}})
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_the_endpoint_keeps_its_auth_dependency(self):
        from backend_app.routers.strategies import validate_strategy

        assert "get_current_user" in str(
            inspect.signature(validate_strategy).parameters["user"].default
        )


class TestSaveEndpointSB06:
    def test_market_identity_comes_from_the_data_node(self, client, reg, user):
        graph = valid_graph(reg, symbol="SOL/USDT", timeframe="4h")
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post(
                "/api/strategies/", json={"name": "SB-06", **graph.to_dict()}
            )

        assert response.status_code == 200, response.text
        written = captured["insert"]
        assert written["symbol"] == "SOL/USDT"
        assert written["timeframe"] == "4h"

    def test_no_exchange_identity_is_persisted_with_a_strategy(self, client, reg):
        graph = valid_graph(reg)
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            client.post(
                "/api/strategies/",
                json={"name": "SB-06", "exchange_id": "kraken", **graph.to_dict()},
            )

        written = captured["insert"]
        assert "exchange_id" not in written, (
            "a saved strategy must stay exchange-agnostic; exchange identity belongs to "
            "a deployment binding (SB-06)"
        )

    def test_the_persisted_payload_contains_no_hardcoded_market(self, client, reg):
        graph = valid_graph(reg, symbol="SOL/USDT", timeframe="4h")
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            client.post("/api/strategies/", json={"name": "SB-06", **graph.to_dict()})

        rendered = repr(captured["insert"])
        for literal in ("BTC/USDT", "binance", '"5m"', "'5m'"):
            assert literal not in rendered, literal

    def test_the_save_handler_holds_no_market_literals_at_all(self):
        """A default that is not in the source cannot fire for any input."""
        from backend_app.routers.strategies import create_strategy

        source = _code_only(inspect.getsource(create_strategy))
        for literal in ("BTC/USDT", "5m", "binance"):
            assert literal not in source, (
                f"{literal!r} is still a fallback on the save path (SB-06)"
            )

    def test_a_graphless_save_omits_the_columns_rather_than_inventing_them(
        self, client
    ):
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post("/api/strategies/", json={"name": "Shell only"})

        assert response.status_code == 200, response.text
        written = captured["insert"]
        assert "symbol" not in written
        assert "timeframe" not in written
        assert "exchange_id" not in written

    def test_an_invalid_graph_returns_422_and_writes_nothing(self, client, reg):
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post(
                "/api/strategies/", json={"name": "bad", **orphan_graph(reg).to_dict()}
            )

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_GRAPH_INVALID"
        assert V.CODE_ORPHAN_NODE in detail["codes"]
        assert "insert" not in captured, (
            "an invalid graph must persist nothing at all (Requirement 3.6)"
        )

    def test_a_saved_graph_carries_its_hash_and_plan(self, client, reg):
        graph = valid_graph(reg)
        expected = SC.compile_graph(graph, reg)
        sb, captured = _capturing_sb()

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post(
                "/api/strategies/", json={"name": "hashed", **graph.to_dict()}
            )

        buy_logic = captured["insert"]["buy_logic"]
        assert buy_logic["_dag_hash"] == expected.dag_hash
        assert buy_logic["_execution_order"] == list(expected.execution_order)
        assert buy_logic["_compiled_plan"]["dag_hash"] == expected.dag_hash
        assert response.json()["dag_hash"] == expected.dag_hash

    def test_the_endpoint_keeps_its_auth_quota_and_rate_limit(self):
        from backend_app.routers.strategies import create_strategy

        params = inspect.signature(create_strategy).parameters
        assert "get_current_user" in str(params["user"].default)
        assert "check_strategy_quota" in str(params["_quota"].default)
        # slowapi rewrites the handler, so the decorator is asserted on the source.
        assert '@limiter.limit("20/minute")' in _handler_decorators("create_strategy")


def _handler_decorators(function_name: str) -> str:
    """The decorator lines immediately above a handler, read from the source."""
    path = ROUTERS_DIR / "strategies.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"async def {function_name}(") or line.startswith(
            f"def {function_name}("
        ):
            decorators = []
            cursor = index - 1
            while cursor >= 0 and (
                lines[cursor].startswith("@") or lines[cursor].strip() == ""
            ):
                decorators.append(lines[cursor])
                cursor -= 1
            return "\n".join(decorators)
    raise AssertionError(f"handler {function_name} not found")


class TestCloneEndpointSB02:
    def _stored_row(self, reg, user_id, graph):
        """A strategy row as the save path writes one.

        An unexecutable graph has no plan and therefore no stored hash - which is exactly
        the state a pre-fix clone left behind, so it is worth cloning from.
        """
        try:
            plan = SC.compile_graph(graph, reg)
            stored_hash, stored_schema = plan.dag_hash, plan.schema_version
        except SC.ValidationError:
            stored_hash, stored_schema = None, graph.schema_version
        canonical = graph.to_dict()
        return {
            "id": "strategy_source",
            "user_id": user_id,
            "name": "Source",
            "symbol": "ETH/USDT",
            "timeframe": "1h",
            "buy_logic": {
                "_nodes": canonical["nodes"],
                "_edges": canonical["edges"],
                "_dag_version": 1,
                "_dag_schema_version": stored_schema,
                "_dag_hash": stored_hash,
            },
            "sell_logic": {},
            "risk": {},
            "indicators": [],
            "ml_model_path": None,
        }

    def test_a_clone_carries_a_non_empty_dag_hash(self, client, reg, user):
        """The SB-02 regression. The pre-fix path persisted no hash at all."""
        graph = valid_graph(reg)
        expected = SC.compile_graph(graph, reg)
        sb, captured = _capturing_sb(
            select_data=[self._stored_row(reg, user["id"], graph)],
            insert_data=[{"id": "strategy_clone"}],
        )

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post("/api/strategies/strategy_source/clone")

        assert response.status_code == 200, response.text
        written = captured["insert"]
        stored_hash = written["buy_logic"]["_dag_hash"]
        assert stored_hash, "a clone must not be persisted without an identity hash"
        assert stored_hash == expected.dag_hash
        assert written["buy_logic"]["_execution_order"] == list(
            expected.execution_order
        )

    def test_the_clone_hash_goes_where_the_schema_keeps_it(self, client, reg, user):
        """`strategies` has no `dag_hash` column; the pre-fix code wrote to one."""
        graph = valid_graph(reg)
        sb, captured = _capturing_sb(
            select_data=[self._stored_row(reg, user["id"], graph)],
            insert_data=[{"id": "strategy_clone"}],
        )

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            client.post("/api/strategies/strategy_source/clone")

        written = captured["insert"]
        assert "dag_hash" not in written
        assert "execution_order" not in written

    def test_cloning_does_not_mutate_the_source_rows_blob(self, client, reg, user):
        graph = valid_graph(reg)
        row = self._stored_row(reg, user["id"], graph)
        original_keys = set(row["buy_logic"])
        sb, _captured = _capturing_sb(
            select_data=[row], insert_data=[{"id": "strategy_clone"}]
        )

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            client.post("/api/strategies/strategy_source/clone")

        assert set(row["buy_logic"]) == original_keys

    def test_an_uncompilable_source_is_reported_not_swallowed(self, client, reg, user):
        """The `except Exception` that hid the AttributeError is gone (SB-02).

        Requirement 10.2 supersedes the pre-fix expectation that an uncompilable clone
        source was rejected with a 422 and nothing written. Cloning an existing, already
        saved strategy must never silently discard it: the recompile failure is now
        reported by PERSISTING the clone with an INVALID marker and the full structured
        report attached, and by surfacing the failure in the response `warnings[]`
        (Requirement 10.3's typed error path) - not by swallowing it, and not by
        rejecting it outright either. `insert` DOES happen; that is the point.
        """
        row = self._stored_row(reg, user["id"], orphan_graph(reg))
        sb, captured = _capturing_sb(
            select_data=[row], insert_data=[{"id": "strategy_clone"}]
        )

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post("/api/strategies/strategy_source/clone")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["warnings"], "a recompile failure must surface in warnings[]"
        codes = [issue["code"] for issue in body["warnings"][0]["errors"]]
        assert V.CODE_ORPHAN_NODE in codes

        assert "insert" in captured, (
            "Requirement 10.2: an uncompilable clone is persisted, not discarded"
        )
        written_buy_logic = captured["insert"]["buy_logic"]
        assert written_buy_logic["_dag_validation_state"] == "INVALID"
        assert written_buy_logic["_dag_hash"] is None
        assert written_buy_logic["_dag_validation_report"] is not None

    def test_the_clone_verdict_intentionally_diverges_from_the_save_verdict(
        self, client, reg, user
    ):
        """Requirement 10 makes save and clone diverge for the same invalid graph.

        This is NOT a resurgence of SB-01. SB-01 was two compilers disagreeing about
        whether a graph IS valid - the verdict itself. Here both paths reach the exact
        same verdict (invalid, same error codes); they differ only in what they DO with
        that verdict, which is required by two different requirements:

        * Save (Requirement 3.6) is creating a brand new strategy from scratch, so an
          invalid graph persists nothing at all and answers 422.
        * Clone (Requirement 10.2) is copying an existing, already-saved strategy, so an
          invalid recompile is persisted anyway as INVALID and un-deployable, and answers
          200 with the failure in `warnings[]` - the author opens the clone, sees the
          errors, and fixes them, rather than losing the clone outright.
        """
        graph = orphan_graph(reg)
        sb_save, _ = _capturing_sb()
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb_save)
        ):
            save = client.post(
                "/api/strategies/", json={"name": "bad", **graph.to_dict()}
            )

        sb_clone, captured_clone = _capturing_sb(
            select_data=[self._stored_row(reg, user["id"], graph)],
            insert_data=[{"id": "strategy_clone"}],
        )
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb_clone)
        ):
            clone = client.post("/api/strategies/strategy_source/clone")

        assert save.status_code == 422
        assert clone.status_code == 200
        assert "insert" in captured_clone, "the clone must still be persisted"

        save_codes = set(save.json()["detail"]["codes"])
        clone_codes = set(
            issue["code"] for issue in clone.json()["warnings"][0]["errors"]
        )
        assert save_codes == clone_codes, (
            "the verdict - which codes fire - must still agree between the two paths; "
            "only the persistence outcome is allowed to differ"
        )

    def test_the_endpoint_keeps_its_auth_and_owner_scoping(self):
        from backend_app.routers.strategies import clone_strategy

        params = inspect.signature(clone_strategy).parameters
        assert "get_current_user" in str(params["user"].default)
        source = inspect.getsource(clone_strategy)
        assert '.eq("user_id", user["id"])' in source, (
            "the clone lookup must stay scoped to the caller's own strategies"
        )


class TestBlocksAliasDeprecation:
    def test_the_alias_still_responds_and_declares_itself_deprecated(self, client):
        response = client.get("/api/strategies/blocks")

        assert response.status_code == 200
        body = response.json()
        assert body["deprecated"] is True
        assert body["replacement"] == "/api/strategy-operations/registry/blocks"

    def test_the_existing_response_keys_are_unchanged(self, client):
        body = client.get("/api/strategies/blocks").json()
        for key in ("indicators", "ml_models", "dl_models", "total_blocks"):
            assert key in body, key
        assert body["total_blocks"] == (
            len(body["indicators"]) + len(body["ml_models"]) + len(body["dl_models"])
        )

    def test_openapi_marks_the_alias_deprecated(self):
        from backend_app.main import app

        schema = app.openapi()
        blocks = schema["paths"]["/api/strategies/blocks"]["get"]
        assert blocks.get("deprecated") is True
