# -*- coding: utf-8 -*-
"""
Guards the widened `NodeType` vocabulary (spec: strategy-builder task 1.8).

Two things must hold simultaneously:
  * the seven canonical categories exist (schema version 2 target model), and
  * every schema version 1 value still deserializes with its original wire
    value, so persisted strategy rows keep loading.
"""

import pytest

from backend_app.core.models.pydantic_models import (
    CANONICAL_NODE_TYPES,
    LEGACY_NODE_TYPE_MAP,
    DAGNode,
    NodeType,
)

CANONICAL_NAMES = [
    "DATA",
    "INDICATOR",
    "MATH",
    "LOGIC",
    "FEATURE_ENGINEERING",
    "ML_DL",
    "ACTION",
]

# The five values persisted by schema version 1 rows.
LEGACY_VALUES = ["indicator", "ml", "logic", "action", "input"]


@pytest.mark.parametrize("name", CANONICAL_NAMES)
def test_canonical_category_exists(name):
    assert hasattr(NodeType, name)


def test_canonical_tuple_is_the_seven_categories():
    assert [member.name for member in CANONICAL_NODE_TYPES] == CANONICAL_NAMES


@pytest.mark.parametrize("value", LEGACY_VALUES)
def test_legacy_value_still_deserializes(value):
    member = NodeType(value)
    assert member.value == value


@pytest.mark.parametrize("value", LEGACY_VALUES)
def test_legacy_value_survives_dag_node_parsing(value):
    node = DAGNode(id="n1", type=value)
    assert node.type == value


def test_legacy_string_comparison_is_unchanged():
    # strategy_compiler.py compares raw dict values against these members.
    assert "input" == NodeType.INPUT
    assert "ml" == NodeType.ML


@pytest.mark.parametrize(
    "value,expected",
    [
        ("input", NodeType.DATA),
        ("ml", NodeType.ML_DL),
        ("indicator", NodeType.INDICATOR),
        ("logic", NodeType.LOGIC),
        ("action", NodeType.ACTION),
    ],
)
def test_legacy_value_maps_to_canonical_category(value, expected):
    assert NodeType(value).canonical is expected
    assert LEGACY_NODE_TYPE_MAP[value] is expected


def test_only_input_and_ml_are_legacy():
    assert {m.name for m in NodeType if m.is_legacy} == {"INPUT", "ML"}


def test_unknown_value_is_still_rejected():
    with pytest.raises(ValueError):
        NodeType("not_a_category")
