"""PAPER-16: a Paper_Session's version read must carry the graph ``load_plan`` needs.

In production every session logged "the compiled plan for strategy version ... could not be
resolved (Row carries no loadable strategy graph)" and produced no signal: the row came from
``read_strategy_version``, whose projection held ``compiled_plan`` but no graph, and
``strategy_compiler.load_plan`` loads the graph first to check the stored plan against it.
These tests project a real builder-shaped row down to exactly ``STRATEGY_VERSION_SELECT``.
"""

from __future__ import annotations

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.paper.paper_session_service import STRATEGY_VERSION_SELECT
from backend_app.backend.paper_session_runtime import resolve_session_plan
from backend_app.backend.strategy_dag import registry as registry_module
from tests.test_plan_engine_adaptation_fidelity import gate_graph

SELECTED = [c.strip() for c in STRATEGY_VERSION_SELECT.split(",")]


def _projected(row):
    return {k: v for k, v in row.items() if k in SELECTED}


def _builder_row():
    reg = registry_module.build_registry()
    graph, _ids = gate_graph(reg)
    plan = SC.compile_graph(graph, reg)
    return reg, {
        "id": "ver-16",
        "strategy_id": "strat-16",
        "lifecycle_state": "READY",
        "is_draft": False,
        "graph_json": graph.to_dict(),
        "compiled_plan": plan.to_json(),
        "schema_version": 2,
        "dag_hash": plan.dag_hash,
        "validation_state": "VALID",
    }


def test_the_projection_names_the_graph_columns_load_plan_reads() -> None:
    for column in ("compiled_plan", "graph_json", "blueprint", "execution_graph"):
        assert column in SELECTED


def test_a_version_read_through_the_projection_resolves_a_plan() -> None:
    reg, row = _builder_row()
    assert resolve_session_plan(_projected(row), reg) is not None


def test_without_the_graph_the_plan_does_not_resolve() -> None:
    """Negative control: the pre-fix projection reproduces the production failure."""
    reg, row = _builder_row()
    old = {k: row[k] for k in ("id", "strategy_id", "lifecycle_state", "is_draft", "compiled_plan")}
    assert resolve_session_plan(old, reg) is None


def test_the_projection_carries_the_version_label_the_signal_trace_needs() -> None:
    """PROD-7: every paper signal's Signal_Trace was refused SIGNAL_ATTRIBUTION_INCOMPLETE
    (strategy_version) because this projection did not read the version label."""
    from backend_app.backend.signal_service import paper_session_facts

    assert "version" in SELECTED
    _reg, row = _builder_row()
    row["version"] = "v1.0"
    session = {"user_id": "u", "source_strategy_id": "strat-16", "version_id": "ver-16",
               "exchange_id": "binance", "symbol": "BTC/USDT", "timeframe": "1m"}
    facts = paper_session_facts(session, version=_projected(row))
    assert facts["strategy_version"] == "v1.0"
