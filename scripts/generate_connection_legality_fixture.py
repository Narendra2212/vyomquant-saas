"""Export the backend's edge-legality verdicts as a JSON fixture for the client test.

Task 3.8 adds `algo22-terminal/src/lib/connectionLegality.js`, a client-side read of rules
R1-R8 that exists purely to spare a network round trip while an author drags a connection.
`backend_app/backend/strategy_dag/validator.py::is_edge_legal` stays the authority.

The one way those two can silently diverge is if nobody ever compares them. So this script
runs the *real* validator over the *real* assembled registry for a table of candidate edges,
and writes both the served registry payload and the resulting verdicts to

    algo22-terminal/tests/fixtures/backend_edge_verdicts.json

`tests/unit/connectionLegality.test.js` asserts the JS module reproduces every code, and the
issue's node/edge/field/expected/actual, for every case in that file. The verdicts are
exported rather than hand-copied, so re-running this script after a rule change is the only
way the fixture ever moves.

Reads only. Nothing here modifies a backend module; it imports them and serialises output.

Usage
-----
    python scripts/generate_connection_legality_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as validator_module
from backend_app.backend.strategy_dag.schema import EdgeSpec, StrategyGraph

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPO_ROOT / "algo22-terminal" / "tests" / "fixtures" / "backend_edge_verdicts.json"
)

# Blocks the cases below reference. Every descriptor is exported verbatim from the registry,
# so the client test reads the same declared ports, categories and terminality the backend
# read - not a transcription of them.
USED_BLOCKS = (
    "ohlcv_feed",          # DATA, no inputs, several output types
    "ema",                 # INDICATOR, PRICE_SERIES in, SCALAR_SERIES out
    "sma",                 # INDICATOR, second one so R7 has two candidate sources
    "add",                 # MATH, input 'a' variadic and input 'b' not
    "multiply",            # MATH, for a two-node cycle
    "divide",              # MATH, for a three-node cycle
    "gt",                  # LOGIC, SCALAR_SERIES in, BOOLEAN_SERIES out
    "and",                 # LOGIC, variadic 'a', non-variadic 'b'
    "feat_lag",            # FEATURE_ENGINEERING, SCALAR_SERIES in, FEATURE_MATRIX out
    "xgboost",             # ML_DL, FEATURE_MATRIX in, PREDICTION out
    "action_buy_market",   # ACTION, TERMINAL, no outputs
)


def node(node_id: str, block_id: str, registry: Any) -> Dict[str, Any]:
    """A canonical node carrying **no** port list.

    Deliberate: the validator reads ports from the registry descriptor and never from the
    submitted node, so a fixture whose nodes declare no ports proves the client does the
    same. A client reading `node.inputs` would pass a fixture that declared them.
    """
    descriptor = registry.get(block_id)
    assert descriptor is not None, f"registry publishes no '{block_id}'"
    return {
        "id": node_id,
        "block_id": block_id,
        "category": descriptor.category.value,
        "params": {},
        "inputs": [],
        "outputs": [],
        "ui": {},
    }


def edge(
    edge_id: str, source: str, source_port: str, target: str, target_port: str
) -> Dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "source_port": source_port,
        "target": target,
        "target_port": target_port,
    }


def graph(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": 2,
        "strategy_id": "fixture",
        "version": "1",
        "name": "connection legality fixture",
        "nodes": nodes,
        "edges": edges,
        "metadata": {},
        "validation_state": "UNVALIDATED",
    }


def build_cases(registry: Any) -> List[Dict[str, Any]]:
    """One rejecting and one accepting case per rule, plus the ordering cases."""
    n = lambda node_id, block_id: node(node_id, block_id, registry)  # noqa: E731

    data = n("n_data", "ohlcv_feed")
    ema = n("n_ema", "ema")
    sma = n("n_sma", "sma")
    add = n("n_add", "add")
    mul = n("n_mul", "multiply")
    div = n("n_div", "divide")
    gt = n("n_gt", "gt")
    and_ = n("n_and", "and")
    feat = n("n_feat", "feat_lag")
    ml = n("n_ml", "xgboost")
    action = n("n_action", "action_buy_market")

    chain = [data, ema, sma, add, gt, and_, feat, ml, action]

    cases: List[Dict[str, Any]] = [
        # -- R1 endpoints resolve ------------------------------------------
        {
            "name": "R1 rejects an edge naming a node that is not in the graph",
            "rule": "R1",
            "graph": graph([data, ema], []),
            "edge": edge("e_r1", "n_ghost", "close", "n_ema", "series"),
        },
        {
            "name": "R1 accepts an edge whose endpoints both resolve",
            "rule": "R1",
            "graph": graph([data, ema], []),
            "edge": edge("e_r1_ok", "n_data", "close", "n_ema", "series"),
        },
        # -- R2 no self loop ------------------------------------------------
        {
            "name": "R2 rejects a node joined to itself",
            "rule": "R2",
            "graph": graph([data, add], []),
            "edge": edge("e_r2", "n_add", "out", "n_add", "b"),
        },
        {
            "name": "R2 accepts two different blocks",
            "rule": "R2",
            "graph": graph([ema, add], []),
            "edge": edge("e_r2_ok", "n_ema", "value", "n_add", "a"),
        },
        # -- R3 ports exist and face the right way -------------------------
        {
            "name": "R3 rejects an output port the descriptor does not publish",
            "rule": "R3",
            "graph": graph([data, ema], []),
            "edge": edge("e_r3_src", "n_data", "not_a_port", "n_ema", "series"),
        },
        {
            "name": "R3 rejects an input port the descriptor does not accept",
            "rule": "R3",
            "graph": graph([data, ema], []),
            "edge": edge("e_r3_dst", "n_data", "close", "n_ema", "not_a_port"),
        },
        {
            "name": "R3 accepts ports both descriptors declare",
            "rule": "R3",
            "graph": graph([data, ema], []),
            "edge": edge("e_r3_ok", "n_data", "close", "n_ema", "series"),
        },
        # -- R4 type compatibility ------------------------------------------
        {
            "name": "R4 rejects SCALAR_SERIES feeding a PRICE_SERIES input",
            "rule": "R4",
            "graph": graph([data, ema], []),
            "edge": edge("e_r4", "n_data", "volume", "n_ema", "series"),
        },
        {
            "name": "R4 accepts OHLCV_FRAME feeding a PRICE_SERIES input",
            "rule": "R4",
            "graph": graph([data, ema], []),
            "edge": edge("e_r4_ok", "n_data", "frame", "n_ema", "series"),
        },
        {
            "name": "R4 accepts SCALAR_SERIES feeding a SCALAR_SERIES input",
            "rule": "R4",
            "graph": graph([ema, add], []),
            "edge": edge("e_r4_ok2", "n_ema", "value", "n_add", "a"),
        },
        # -- R5 category adjacency -----------------------------------------
        {
            "name": "R5 rejects INDICATOR feeding ACTION",
            "rule": "R5",
            "graph": graph([ema, action], []),
            "edge": edge("e_r5", "n_ema", "value", "n_action", "signal"),
        },
        {
            "name": "R5 rejects ML_DL feeding DATA, before R3 blames the missing port",
            "rule": "R5",
            "graph": graph([ml, data], []),
            "edge": edge("e_r5_ml_data", "n_ml", "prediction", "n_data", "series"),
        },
        {
            "name": "R5 accepts LOGIC feeding ACTION",
            "rule": "R5",
            "graph": graph([and_, action], []),
            "edge": edge("e_r5_ok", "n_and", "out", "n_action", "signal"),
        },
        # -- R6 terminal source, and the ordering it exists for ------------
        {
            "name": "R6 rejects an ACTION source with TERMINAL_HAS_NO_OUTPUT, not UNKNOWN_SOURCE_PORT",
            "rule": "R6",
            "graph": graph([action, ema], []),
            "edge": edge("e_r6_ind", "n_action", "signal", "n_ema", "series"),
        },
        {
            "name": "R6 rejects ACTION feeding DATA",
            "rule": "R6",
            "graph": graph([action, data], []),
            "edge": edge("e_r6_data", "n_action", "out", "n_data", "series"),
        },
        {
            "name": "R6 rejects ACTION feeding ML_DL",
            "rule": "R6",
            "graph": graph([action, ml], []),
            "edge": edge("e_r6_ml", "n_action", "out", "n_ml", "features"),
        },
        {
            "name": "R6 accepts a non-terminal source",
            "rule": "R6",
            "graph": graph([gt, and_], []),
            "edge": edge("e_r6_ok", "n_gt", "out", "n_and", "a"),
        },
        # -- R7 single-arity input already occupied ------------------------
        {
            "name": "R7 rejects a second connection into a non-variadic input",
            "rule": "R7",
            "graph": graph(
                [ema, sma, add],
                [edge("e_existing", "n_ema", "value", "n_add", "b")],
            ),
            "edge": edge("e_r7", "n_sma", "value", "n_add", "b"),
        },
        {
            "name": "R7 accepts a second connection into a variadic input",
            "rule": "R7",
            "graph": graph(
                [ema, sma, add],
                [edge("e_existing", "n_ema", "value", "n_add", "a")],
            ),
            "edge": edge("e_r7_ok", "n_sma", "value", "n_add", "a"),
        },
        {
            "name": "R7 accepts re-judging the edge that already occupies the port",
            "rule": "R7",
            "graph": graph(
                [ema, add],
                [edge("e_existing", "n_ema", "value", "n_add", "b")],
            ),
            "edge": edge("e_existing", "n_ema", "value", "n_add", "b"),
        },
        # -- R8 cycle -------------------------------------------------------
        {
            "name": "R8 rejects an edge closing a two-node loop, naming the path",
            "rule": "R8",
            "graph": graph([add, mul], [edge("e_fwd", "n_add", "out", "n_mul", "a")]),
            "edge": edge("e_r8", "n_mul", "out", "n_add", "b"),
        },
        {
            "name": "R8 rejects an edge closing a three-node loop, naming the path",
            "rule": "R8",
            "graph": graph(
                [add, mul, div],
                [
                    edge("e_1", "n_add", "out", "n_mul", "a"),
                    edge("e_2", "n_mul", "out", "n_div", "numerator"),
                ],
            ),
            "edge": edge("e_r8_3", "n_div", "out", "n_add", "b"),
        },
        {
            "name": "R4 is evaluated before R8: a type-mismatched edge that would also close a loop reports TYPE_MISMATCH",
            "rule": "order",
            "graph": graph(
                [add, mul, gt],
                [
                    edge("e_1", "n_add", "out", "n_mul", "a"),
                    edge("e_2", "n_mul", "out", "n_gt", "left"),
                ],
            ),
            "edge": edge("e_order_r4_r8", "n_gt", "out", "n_add", "b"),
        },
        {
            "name": "R8 accepts a fan-out that closes nothing",
            "rule": "R8",
            "graph": graph([add, mul], [edge("e_fwd", "n_add", "out", "n_mul", "a")]),
            "edge": edge("e_r8_ok", "n_add", "out", "n_mul", "b"),
        },
        # -- a whole legal strategy path, edge by edge ----------------------
        {
            "name": "the preferred DATA -> INDICATOR -> LOGIC -> ACTION path is legal, edge 1",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p1", "n_data", "close", "n_ema", "series"),
        },
        {
            "name": "the preferred path is legal, edge 2 (INDICATOR -> LOGIC)",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p2", "n_ema", "value", "n_gt", "left"),
        },
        {
            "name": "the preferred path is legal, edge 3 (LOGIC -> LOGIC merge)",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p3", "n_gt", "out", "n_and", "a"),
        },
        {
            "name": "the preferred path is legal, edge 4 (LOGIC -> ACTION)",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p4", "n_and", "out", "n_action", "signal"),
        },
        {
            "name": "DATA -> FEATURE_ENGINEERING is legal (branching, Requirement 7.4)",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p5", "n_data", "volume", "n_feat", "series"),
        },
        {
            "name": "FEATURE_ENGINEERING -> ML_DL is legal",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p6", "n_feat", "matrix", "n_ml", "features"),
        },
        {
            "name": "ML_DL -> MATH is legal (Requirement 7.4)",
            "rule": "path",
            "graph": graph(chain, []),
            "edge": edge("e_p7", "n_ml", "confidence", "n_add", "a"),
        },
        {
            "name": "an unresolved source block is reported as an unknown source port",
            "rule": "R3",
            "graph": graph(
                [
                    {
                        "id": "n_ghostblock",
                        "block_id": "block_that_does_not_exist",
                        "category": "INDICATOR",
                        "params": {},
                        "inputs": [],
                        "outputs": [],
                        "ui": {},
                    },
                    ema,
                ],
                [],
            ),
            "edge": edge("e_unresolved", "n_ghostblock", "value", "n_ema", "series"),
        },
    ]

    return cases


def verdict_of(case: Dict[str, Any], registry: Any) -> Optional[Dict[str, Any]]:
    """The real validator's answer for one case: `None`, or one structured issue."""
    parsed = StrategyGraph.from_dict(case["graph"])
    candidate = EdgeSpec.from_dict(case["edge"])
    return validator_module.is_edge_legal(parsed, candidate, registry)


def main() -> None:
    registry = registry_module.get_registry()

    blocks = []
    for block_id in USED_BLOCKS:
        descriptor = registry.get(block_id)
        if descriptor is None:
            raise SystemExit(f"registry publishes no '{block_id}'; fixture cannot be built")
        blocks.append(descriptor.to_dict())

    cases = build_cases(registry)
    exported = []
    for case in cases:
        exported.append(
            {
                "name": case["name"],
                "rule": case["rule"],
                "graph": case["graph"],
                "edge": case["edge"],
                "verdict": verdict_of(case, registry),
            }
        )

    payload = {
        "generated_by": "scripts/generate_connection_legality_fixture.py",
        "validator_version": validator_module.VALIDATOR_VERSION,
        "registry": {
            "registry_version": registry.registry_version,
            # Only the descriptors the cases touch, exported verbatim from the registry.
            "blocks": blocks,
            # The full served matrix, so R4 on the client reads exactly what the backend read.
            "compatibility_matrix": registry.compatibility_matrix,
            "port_types": registry_module.port_type_vocabulary(),
        },
        "cases": exported,
    }

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    FIXTURE_PATH.write_bytes(text.encode("utf-8"))

    rejected = sum(1 for case in exported if case["verdict"] is not None)
    print(f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)}")
    print(f"  registry_version : {registry.registry_version}")
    print(f"  descriptors      : {len(blocks)}")
    print(f"  cases            : {len(exported)} ({rejected} rejected, {len(exported) - rejected} legal)")
    for case in exported:
        code = "LEGAL" if case["verdict"] is None else case["verdict"]["code"]
        print(f"    {code:<24} {case['name']}")


if __name__ == "__main__":
    main()
