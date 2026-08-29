# -*- coding: utf-8 -*-
"""
Read-time schema migration: version 1 rows -> canonical version 2 graphs.

Spec: strategy-builder task 1.6 (`design.md` "Schema versioning and migration").
Requirements 1.9 (migrate at read time, leave the stored record unchanged), 1.10 (an
unresolvable block loads INVALID with an `UNRESOLVED_BLOCK` error naming the node) and
1.11 (any schema version other than 1 or 2 is rejected naming the declared version).

The descriptor source used here is the *real* one: `INDICATOR_SPECS`, `FEATURE_SPECS` and
`MODEL_SPECS` as published by the engines that own them. Nothing is faked, so a port
asserted below is a port the platform actually runs.
"""

import copy
import json

import pytest

from backend_app.backend.feature_engineering import FEATURE_SPECS_BY_ID
from backend_app.backend.indicators_backend import INDICATOR_SPECS_BY_ID
from backend_app.backend.ml_models import MODEL_SPECS
from backend_app.backend.strategy_dag.schema import (
    CODE_EXCHANGE_FIELD_DROPPED,
    CODE_UNRESOLVED_BLOCK,
    CODE_UNRESOLVED_EDGE_PORTS,
    CURRENT_SCHEMA_VERSION,
    DEFAULT_DATA_BLOCK_ID,
    BlockCategory,
    GraphParseError,
    NodeSpec,
    PortType,
    StrategyGraph,
    UnsupportedSchemaVersion,
    ValidationState,
    load_graph,
    migrate_v1_to_v2,
    migration_errors,
    migration_issues,
    parse_v2,
    unresolved_node_ids,
)


class RealDescriptorSource:
    """The descriptor sources that exist today, exposed as one ``get(block_id)``.

    This stands in for `strategy_dag/registry.py` (task 1.7), which is not implemented
    yet. It invents nothing: every descriptor comes from the engine that owns it, and
    model blocks whose library is absent are omitted exactly as the registry will omit
    them.
    """

    def __init__(self):
        self._by_id = {}
        self._by_id.update(INDICATOR_SPECS_BY_ID)
        self._by_id.update(FEATURE_SPECS_BY_ID)
        for block_id, spec in MODEL_SPECS.items():
            if spec.backend_available:
                self._by_id[block_id] = spec

    def get(self, block_id):
        return self._by_id.get(block_id)


RESOLVER = RealDescriptorSource()

#: A model block whose library really is importable here, or None.
AVAILABLE_MODEL_ID = next(
    (block_id for block_id, spec in MODEL_SPECS.items() if spec.backend_available),
    None,
)


def v1_graph():
    """A realistic schema version 1 graph, in the shape `buy_logic._nodes` really holds.

    Configuration is spread across legacy scalar columns (`symbol`, `timeframe`,
    `order_type`, `amount`, `confidence_threshold`) rather than `params`, node types come
    from the five-value vocabulary, presentation sits next to semantics, and the DATA node
    carries the hardcoded `exchange` that SB-06 is about.
    """
    return {
        "schema_version": 1,
        "strategy_id": "strat-1",
        "name": "RSI reversion",
        "nodes": [
            {
                "id": "n_data_1",
                "type": "input",
                "label": "BTC 15m",
                "symbol": "BTC/USDT",
                "timeframe": "15m",
                "exchange": "binance",
                "position": {"x": 10, "y": 20},
            },
            {
                "id": "n_rsi_1",
                "type": "indicator",
                "indicator": "rsi",
                "params": {"period": 21},
                "label": "RSI 21",
            },
            {
                "id": "n_feat_1",
                "type": "feature",
                "indicator": "feat_lag",
                "params": {"lags": [1, 2, 3]},
            },
            {
                "id": "n_logic_1",
                "type": "logic",
                "operator": "AND",
            },
            {
                "id": "n_action_1",
                "type": "action",
                "action": "buy",
                "order_type": "market",
                "amount": 0.25,
                "collapsed": True,
            },
        ],
        "edges": [
            {"id": "e1", "source": "n_data_1", "target": "n_rsi_1"},
            {"id": "e2", "source": "n_rsi_1", "target": "n_feat_1"},
            {"id": "e3", "source": "n_feat_1", "target": "n_logic_1"},
            {"id": "e4", "source": "n_logic_1", "target": "n_action_1"},
        ],
    }


def issue_codes(graph):
    return [issue["code"] for issue in migration_issues(graph)]


# ── categories, block ids, params, ports ─────────────────────────────────


def test_v1_graph_migrates_to_a_valid_v2_graph():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)

    assert graph.schema_version == CURRENT_SCHEMA_VERSION
    # The migrated graph is a *canonical* graph: it survives the strict v2 parser
    # unchanged, so every consumer of the canonical schema can read it.
    assert parse_v2(graph.to_dict()) == graph
    # Node identity is preserved verbatim; ids are never renumbered.
    assert [node.id for node in graph.nodes] == [
        "n_data_1",
        "n_rsi_1",
        "n_feat_1",
        "n_logic_1",
        "n_action_1",
    ]


def test_legacy_node_types_map_to_canonical_categories():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    categories = {node.id: node.category for node in graph.nodes}

    assert categories == {
        "n_data_1": BlockCategory.DATA,
        "n_rsi_1": BlockCategory.INDICATOR,
        "n_feat_1": BlockCategory.FEATURE_ENGINEERING,
        "n_logic_1": BlockCategory.LOGIC,
        "n_action_1": BlockCategory.ACTION,
    }


def test_block_ids_are_resolved_from_the_legacy_fields():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    block_ids = {node.id: node.block_id for node in graph.nodes}

    assert block_ids["n_rsi_1"] == "rsi"            # from `indicator`
    assert block_ids["n_feat_1"] == "feat_lag"      # from `indicator`
    assert block_ids["n_logic_1"] == "AND"          # from `operator`
    assert block_ids["n_action_1"] == "buy"         # from `action`
    # A version 1 DATA node named no block at all; ohlcv_feed is the one
    # substitution the design mandates, and it applies to DATA only.
    assert block_ids["n_data_1"] == DEFAULT_DATA_BLOCK_ID


def test_legacy_scalar_fields_are_merged_into_params():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    params = {node.id: node.params for node in graph.nodes}

    assert params["n_data_1"] == {"symbol": "BTC/USDT", "timeframe": "15m"}
    assert params["n_rsi_1"] == {"period": 21}
    assert params["n_feat_1"] == {"lags": [1, 2, 3]}
    assert params["n_action_1"] == {"order_type": "market", "amount": 0.25}


def test_presentation_fields_land_in_ui_not_in_params():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    by_id = graph.node_index()

    assert by_id["n_data_1"].ui == {"label": "BTC 15m", "position": {"x": 10, "y": 20}}
    assert by_id["n_action_1"].ui == {"collapsed": True}
    for node in graph.nodes:
        assert "label" not in node.params
        assert "position" not in node.params


def test_ports_are_resolved_from_the_descriptor():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    by_id = graph.node_index()

    rsi_spec = INDICATOR_SPECS_BY_ID["rsi"]
    assert [p.name for p in by_id["n_rsi_1"].inputs] == [p.name for p in rsi_spec.inputs]
    assert [p.name for p in by_id["n_rsi_1"].outputs] == [
        p.name for p in rsi_spec.outputs
    ]
    assert by_id["n_rsi_1"].inputs[0].type is PortType.PRICE_SERIES
    assert by_id["n_rsi_1"].outputs[0].type is PortType.SCALAR_SERIES

    feature_spec = FEATURE_SPECS_BY_ID["feat_lag"]
    assert [p.name for p in by_id["n_feat_1"].outputs] == [
        p.name for p in feature_spec.outputs
    ]
    assert by_id["n_feat_1"].outputs[0].type is PortType.FEATURE_MATRIX


def test_edges_are_port_addressed_on_both_ends():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    addressed = {edge.id: (edge.source_port, edge.target_port) for edge in graph.edges}

    # Both endpoints resolved, so the edge is fully port-addressed.
    assert addressed["e2"] == ("value", "series")
    assert graph.node("n_rsi_1").port("value", direction="output") is not None
    assert graph.node("n_feat_1").port("series", direction="input") is not None
    # The advisory edge type is taken from the source port, never invented.
    edge = next(e for e in graph.edges if e.id == "e2")
    assert edge.type is PortType.SCALAR_SERIES


@pytest.mark.skipif(AVAILABLE_MODEL_ID is None, reason="no model library in this image")
def test_ml_node_resolves_its_model_block_and_keeps_the_model_reference():
    raw = v1_graph()
    raw["nodes"].append(
        {
            "id": "n_ml_1",
            "type": "ml",
            "model_id": AVAILABLE_MODEL_ID,
            "confidence_threshold": 0.8,
        }
    )
    raw["edges"].append({"id": "e5", "source": "n_feat_1", "target": "n_ml_1"})

    graph = migrate_v1_to_v2(raw, resolver=RESOLVER)
    node = graph.node("n_ml_1")
    spec = MODEL_SPECS[AVAILABLE_MODEL_ID]

    assert node.category is BlockCategory.ML_DL
    assert node.block_id == AVAILABLE_MODEL_ID
    assert [p.name for p in node.inputs] == [p.name for p in spec.inputs]
    assert [p.name for p in node.outputs] == [p.name for p in spec.outputs]
    # The trained-artifact reference survives as a parameter; it is not consumed.
    assert node.params["model_id"] == AVAILABLE_MODEL_ID
    assert node.params["confidence_threshold"] == 0.8


# ── marking, never guessing ──────────────────────────────────────────────


def test_unresolvable_block_is_marked_and_never_guessed():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)

    # `AND` and `buy` are real version 1 blocks with no descriptor published yet.
    assert set(unresolved_node_ids(graph)) == {"n_data_1", "n_logic_1", "n_action_1"}
    for node_id in unresolved_node_ids(graph):
        node = graph.node(node_id)
        # Marked, not guessed: no port contract was invented for these nodes.
        assert node.inputs == []
        assert node.outputs == []
        # The block the row named is preserved verbatim so the error can name it.
        assert node.block_id

    unresolved = [
        issue for issue in migration_issues(graph) if issue["code"] == CODE_UNRESOLVED_BLOCK
    ]
    assert {issue["node_id"] for issue in unresolved} == {
        "n_data_1",
        "n_logic_1",
        "n_action_1",
    }
    for issue in unresolved:
        assert issue["severity"] == "error"
        assert issue["fix_hint"]


def test_unresolved_block_loads_the_version_invalid():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)
    assert graph.validation_state is ValidationState.INVALID
    assert migration_errors(graph)


def test_fully_resolvable_graph_is_unvalidated_not_invalid():
    raw = {
        "schema_version": 1,
        "nodes": [
            {"id": "a", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
            {"id": "b", "type": "feature", "indicator": "feat_lag"},
        ],
        "edges": [{"id": "e1", "source": "a", "target": "b"}],
    }
    graph = migrate_v1_to_v2(raw, resolver=RESOLVER)

    # Migration is not validation: the graph is loadable, and the validator decides.
    assert graph.validation_state is ValidationState.UNVALIDATED
    assert migration_errors(graph) == []
    assert len(graph.edges) == 1


def test_ambiguous_source_port_is_reported_not_picked():
    """A multi-output indicator cannot have its output port guessed."""
    raw = {
        "schema_version": 1,
        "nodes": [
            {"id": "a", "type": "indicator", "indicator": "macd"},
            {"id": "b", "type": "feature", "indicator": "feat_lag"},
        ],
        "edges": [{"id": "e1", "source": "a", "target": "b"}],
    }
    graph = migrate_v1_to_v2(raw, resolver=RESOLVER)

    assert len(INDICATOR_SPECS_BY_ID["macd"].outputs) > 1
    assert graph.edges == []
    assert CODE_UNRESOLVED_EDGE_PORTS in issue_codes(graph)
    assert graph.validation_state is ValidationState.INVALID
    # Nothing is silently discarded: the edge is kept verbatim in the report.
    report = graph.metadata["migration"]
    assert report["unmigrated_edges"] == [{"id": "e1", "source": "a", "target": "b"}]


def test_explicit_port_identity_on_a_v1_edge_is_honoured():
    raw = {
        "schema_version": 1,
        "nodes": [
            {"id": "a", "type": "indicator", "indicator": "macd"},
            {"id": "b", "type": "feature", "indicator": "feat_lag"},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "a",
                "sourceHandle": "histogram",
                "target": "b",
                "targetHandle": "series",
            }
        ],
    }
    graph = migrate_v1_to_v2(raw, resolver=RESOLVER)

    assert [(e.source_port, e.target_port) for e in graph.edges] == [
        ("histogram", "series")
    ]


def test_no_registry_marks_every_block_instead_of_inventing_ports():
    graph = migrate_v1_to_v2(v1_graph(), resolver=lambda block_id: None)

    assert set(unresolved_node_ids(graph)) == {node.id for node in graph.nodes}
    assert all(node.inputs == [] and node.outputs == [] for node in graph.nodes)
    assert graph.validation_state is ValidationState.INVALID


def test_exchange_identity_is_dropped_and_reported():
    graph = migrate_v1_to_v2(v1_graph(), resolver=RESOLVER)

    assert "exchange" not in graph.node("n_data_1").params
    assert "exchange" not in graph.metadata
    dropped = [
        issue
        for issue in migration_issues(graph)
        if issue["code"] == CODE_EXCHANGE_FIELD_DROPPED
    ]
    assert [issue["node_id"] for issue in dropped] == ["n_data_1"]
    assert dropped[0]["severity"] == "warning"
    assert dropped[0]["actual"] == "binance"


# ── read-only ────────────────────────────────────────────────────────────


def test_migration_does_not_mutate_the_input_dict():
    raw = v1_graph()
    before = json.dumps(raw, sort_keys=True)

    graph = migrate_v1_to_v2(raw, resolver=RESOLVER)

    assert json.dumps(raw, sort_keys=True) == before
    assert raw["schema_version"] == 1
    # The migrated graph shares no mutable state with the row it came from.
    graph.node("n_rsi_1").params["period"] = 999
    graph.metadata["migration"]["issues"].clear()
    assert raw["nodes"][1]["params"] == {"period": 21}
    assert json.dumps(raw, sort_keys=True) == before


def test_loading_a_stored_row_leaves_the_row_unchanged():
    row = {
        "id": "strat-7",
        "name": "Stored v1",
        "version": 3,
        "buy_logic": {
            "_nodes": v1_graph()["nodes"],
            "_edges": v1_graph()["edges"],
            "_dag_version": 3,
            "_dag_schema_version": 1,
        },
    }
    snapshot = copy.deepcopy(row)

    graph = load_graph(row, resolver=RESOLVER)

    assert row == snapshot
    assert graph.schema_version == CURRENT_SCHEMA_VERSION
    assert graph.strategy_id == "strat-7"
    assert graph.name == "Stored v1"
    assert graph.version == "3"
    assert len(graph.nodes) == 5


def test_migration_of_the_same_row_is_reproducible():
    raw = v1_graph()
    first = migrate_v1_to_v2(raw, resolver=RESOLVER)
    second = migrate_v1_to_v2(raw, resolver=RESOLVER)

    # No minted-on-read identifiers: two reads of one row agree, so a row cannot
    # change identity merely by being loaded twice.
    assert first.to_dict() == second.to_dict()
    assert first.dag_hash == second.dag_hash


# ── version routing ──────────────────────────────────────────────────────


def v2_row():
    graph = StrategyGraph(
        strategy_id="strat-2",
        version="1",
        name="Already canonical",
        nodes=[
            NodeSpec(
                id="n_rsi_1",
                block_id="rsi",
                category=BlockCategory.INDICATOR,
                params={"period": 14},
                inputs=[],
                outputs=[],
                ui={"label": "RSI"},
            )
        ],
        edges=[],
        metadata={"note": "kept"},
        validation_state=ValidationState.VALID,
    )
    return {"graph_json": graph.to_dict()}, graph


def test_v2_graph_passes_through_load_graph_unchanged():
    row, expected = v2_row()
    snapshot = copy.deepcopy(row)

    loaded = load_graph(row, resolver=RESOLVER)

    assert loaded == expected
    assert loaded.to_dict() == expected.to_dict()
    assert loaded.validation_state is ValidationState.VALID
    # No migration ran, so there is no migration report and the row is untouched.
    assert "migration" not in loaded.metadata
    assert row == snapshot


def test_v2_graph_is_not_re_migrated_even_when_blocks_are_unresolvable():
    row, _ = v2_row()
    loaded = load_graph(row, resolver=lambda block_id: None)
    assert loaded.node("n_rsi_1").block_id == "rsi"
    assert migration_issues(loaded) == []


def test_absent_schema_version_is_treated_as_version_1():
    raw = v1_graph()
    del raw["schema_version"]
    graph = load_graph(raw, resolver=RESOLVER)
    assert graph.schema_version == CURRENT_SCHEMA_VERSION
    assert graph.metadata["migration"]["from_schema_version"] == 1


@pytest.mark.parametrize("declared", [0, 3, 99, -1, "2", "1", 2.0, 2.5, True, [2]])
def test_unsupported_schema_version_is_rejected_naming_the_version(declared):
    raw = v1_graph()
    raw["schema_version"] = declared

    with pytest.raises(UnsupportedSchemaVersion) as excinfo:
        load_graph(raw, resolver=RESOLVER)

    assert excinfo.value.version == declared
    assert repr(declared) in str(excinfo.value)
    assert excinfo.value.code == "UNSUPPORTED_SCHEMA_VERSION"


@pytest.mark.parametrize("declared", [0, 3, "1", 2.5])
def test_migrate_rejects_an_unsupported_version_directly(declared):
    raw = v1_graph()
    raw["schema_version"] = declared
    with pytest.raises(UnsupportedSchemaVersion):
        migrate_v1_to_v2(raw, resolver=RESOLVER)


def test_v2_payload_handed_straight_to_the_migrator_is_refused():
    _, graph = v2_row()
    with pytest.raises(GraphParseError):
        migrate_v1_to_v2(graph.to_dict(), resolver=RESOLVER)


def test_row_with_no_graph_at_all_is_a_parse_error():
    with pytest.raises(GraphParseError):
        load_graph({"id": "strat-9", "name": "empty"}, resolver=RESOLVER)


def test_node_without_an_id_is_refused_rather_than_invented():
    raw = {"schema_version": 1, "nodes": [{"type": "indicator", "indicator": "rsi"}], "edges": []}
    with pytest.raises(GraphParseError):
        migrate_v1_to_v2(raw, resolver=RESOLVER)
