"""MKT-05: the publish check must see the action nodes of a builder-saved strategy.

The builder persists ``strategies.buy_logic`` as the v2 graph with underscored keys (``_nodes``)
and marks actions with ``category: ACTION``. The check read ``buy_logic['nodes']`` and looked for
``type == 'action'``, so 0 of 164 production strategies could be published (seen 2026-11-11:
"Strategy DAG has no action nodes" for a strategy with buy and sell actions and 207 backtest
trades).
"""

from __future__ import annotations

from backend_app.routers.library import _graph_nodes, _is_action_node

V2 = {
    "_edges": [],
    "_nodes": [
        {"id": "n-1", "category": "DATA", "block_id": "ohlcv_feed"},
        {"id": "n-6", "category": "ACTION", "block_id": "action_buy_market"},
        {"id": "n-7", "category": "ACTION", "block_id": "action_sell_market"},
    ],
}
LEGACY = {"nodes": [{"id": "a", "type": "indicator"}, {"id": "b", "type": "action"}]}


def test_the_builders_v2_graph_has_its_action_nodes_found() -> None:
    nodes = _graph_nodes(V2)
    assert len(nodes) == 3
    assert [n["id"] for n in nodes if _is_action_node(n)] == ["n-6", "n-7"]


def test_a_legacy_graph_is_read_exactly_as_before() -> None:
    assert [n["id"] for n in _graph_nodes(LEGACY) if _is_action_node(n)] == ["b"]


def test_a_graph_without_actions_is_still_refused() -> None:
    no_action = {"_nodes": [{"id": "n-1", "category": "DATA"}]}
    assert not [n for n in _graph_nodes(no_action) if _is_action_node(n)]
    assert _graph_nodes(None) == [] and _graph_nodes({}) == []
