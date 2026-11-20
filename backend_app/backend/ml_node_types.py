"""
backend/ml_node_types.py — the one answer to "is this a model node?"

WHAT THIS CLOSES
----------------
Three gates ask whether a strategy contains a model node, and therefore whether it needs a
trained model before it may run, and two of them used to ask with a narrower vocabulary
than the third:

    backend_app/routers/strategies.py      {"ml", "dl", "ml_dl", "ml_model"}
    backend_app/backend/strategy_service.py        ["ml", "dl"]
    backend_app/routers/library.py                 ["ml", "dl"]

(A fourth gate, ``backend_app/tee/main.py``, existed when this module was written. RT-23
deleted it as confirmed-dead code -- it crashed on import and was never actually deployed
-- so it no longer needs to agree with the other three.)

Each then refuses when it finds model nodes and the strategy carries no
``ml_model_path``. So a strategy whose model node is spelled ``ml_dl`` or ``ml_model`` was
refused by the router's deploy and resume paths and waved straight through by the
service's deploy path and the library clone path — two gates out of three, for a strategy
declaring a model it does not have.

The four spellings are not an invention of this module. They are the schema-version-1
``node["type"]`` values that denote a model block, and the canonical vocabulary folds all
four into the single ``ML_DL`` category; the router's set has always listed them, with a
comment explaining that it is kept explicit so stored v1 blueprints read byte-for-byte.
All four mean the same thing, so a gate that matches only two is simply wrong.

WHY THIS IS SAFE TO UNIFY WITHOUT A PRODUCT DECISION
----------------------------------------------------
The predicate is membership in a set, and the set only grows. Widening it cannot turn a
refusal into an acceptance: every strategy refused before is still refused, and some
additionally are. Nothing becomes deployable that was not. The strategies newly refused
are precisely those carrying a model node with no model — the condition all four gates
exist to catch, and one the router already refused on its own paths. What this change
removes is the disagreement, not the permission.

WHY THE DEFINITION LIVES HERE
-----------------------------
It could not stay in ``routers/strategies.py``. ``strategy_service`` is imported BY the
routers, so a router-level constant would invert the dependency.

``ml_models`` would have been the topical home and is deliberately not used: it probes for
tensorflow, joblib, xgboost and friends at import time to derive its model registry, and
a strategy clone should not pay for that. This module imports nothing
beyond ``typing`` and is meant to stay that way — ``tests/test_ml_node_detection_is_one_
vocabulary.py`` asserts it.

Regression: tests/test_ml_node_detection_is_one_vocabulary.py
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

__all__ = ["ML_NODE_TYPES", "detect_ml_nodes", "has_ml_nodes"]


#: The schema-version-1 ``node["type"]`` spellings that denote a model block.
#:
#: A ``frozenset`` rather than a ``set`` so an importer cannot narrow it in place and
#: quietly reintroduce the disagreement this module exists to end. Lowercase, because
#: every caller lowercases the stored value before comparing — stored blueprints are
#: unvalidated JSONB and have carried ``"ML"`` and ``"Ml"``.
ML_NODE_TYPES: frozenset = frozenset({"ml", "dl", "ml_dl", "ml_model"})


def detect_ml_nodes(nodes: Any) -> List[Dict[str, Any]]:
    """The model nodes in ``nodes``, by any of the spellings in :data:`ML_NODE_TYPES`.

    Parameters
    ----------
    nodes
        A list of node dicts, as lifted out of a stored blueprint's ``buy_logic._nodes``
        or a request payload's ``nodes``. ANY shape is accepted and anything unusable is
        skipped rather than raised on: the column is unvalidated JSONB written straight
        from a request body at three sites, so a ``None``, a bare string, a list of
        integers or a node whose ``type`` is a number are all states that occur. A gate
        that raised on them would convert "this blueprint is odd" into a 500 on the
        deploy path, and a gate that crashed would be a gate that did not run.

    Returns
    -------
    list
        The matching node dicts, in input order. Empty when there are none — which is the
        common case and is not an error.

    Notes
    -----
    Returns the nodes rather than a boolean because two of the four callers need the nodes
    themselves: the router reports the offending node's ``id`` in its
    ``ML_NODE_MISSING_MODEL_ID`` refusal. :func:`has_ml_nodes` is there for the callers
    that only need the question answered.
    """
    if not isinstance(nodes, Iterable) or isinstance(nodes, (str, bytes, dict)):
        return []

    found: List[Dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        if not isinstance(node_type, str):
            continue
        if node_type.lower() in ML_NODE_TYPES:
            found.append(node)
    return found


def has_ml_nodes(nodes: Any) -> bool:
    """Whether ``nodes`` contains any model node. See :func:`detect_ml_nodes`."""
    return bool(detect_ml_nodes(nodes))
