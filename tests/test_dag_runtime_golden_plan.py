"""
tests/test_dag_runtime_golden_plan.py

The golden-file runtime-plan test: one fixed canonical graph, one fixed candle series,
one fixed set of runtime bytes.

Spec: strategy-builder task 2.10 (``design.md`` -> DAG runtime contract, and the
acceptance gate item "Runtime: golden-file test where a fixed graph over fixed candles
produces a fixed intent sequence"). Requirements 22.3 and 22.4.

* **Requirement 22.3** - every version consumer is served the *same* stored
  ``compiled_plan`` and the *same* identity hash. So both consumers here are driven from
  the persisted artifact: the graph and the plan are serialized into a row shaped like a
  ``strategy_versions`` record (``compiled_plan`` held as the JSON text a column holds)
  and read back through ``strategy_compiler.load_plan``. Nothing executes a freshly
  compiled in-memory object.
* **Requirement 22.4** - two consumers evaluating one version over identical market data
  produce the same trade-intent sequence. So the backtester path
  (``BacktestRuntime.load_version_plan`` -> ``plan_to_engine_graph`` ->
  ``BacktestRuntime.dag_engine``) and the live path (``DAGEventLoop.from_version_row`` ->
  ``RollingWindow`` fed with real ``MarketEvent`` candles -> the loop's own per-symbol
  ``DAGEngine``) are both run over the same committed candles and compared to each other,
  not just to the file.

WHAT THE GOLDEN FILE PINS
-------------------------
``tests/golden/dag_runtime_plan_golden.json`` fixes, for the canonical graph over the
committed candles:

1. the graph identity: ``dag_hash``, node ids, port-addressed edge list;
2. the persisted plan bytes: ``sha256(CompiledPlan.to_json())``, compiler and schema
   version, ``warmup_bars``, ``execution_order``, ``execution_levels``, the DATA and
   ACTION node lists;
3. the engine payload the plan adapts to: ``sha256`` of the ``(nodes, edges)`` pair
   ``plan_to_engine_graph`` emits, the per-node engine ``type``, and the **edge order**
   (which decides the order the engine hands inputs to a multi-input executor);
4. the real per-node output series over those candles - one digest per node, computed from
   the real ``IndicatorExecutor``, ``BlockKernelExecutor`` (which runs the MATH and LOGIC
   kernels the descriptors publish), ``ActionExecutor`` and ``MarketDataExecutor``. This is
   what turns "we changed the EMA" into a red test;
5. the boolean gate series and the final signal series, run-length encoded so a human can
   read what the strategy actually did;
6. the derived trade-intent sequence, labelled through the live loop's own
   ``_signal_to_action``.

WHAT IT DOES **NOT** PIN - read this before trusting it
------------------------------------------------------
* **No order fires here, and exactly one production fact is why.**
  ``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe``, so
  ``SafetyMonitor.check_execution_allowed("strategy_signal")`` blocks order-placing
  execution and ``dag_engine.ActionExecutor`` returns an all-zero series by design. That is
  a financial-safety control and this test does not disable it. The golden therefore records
  ``safety.order_placing_execution_blocked`` and an empty ``intents`` list.

  The ACTION node itself now carries the authored side: the adaptation reads
  ``metadata["side"]`` / ``metadata["order_type"]`` off the descriptor (see
  ``block_specs._action_spec``) and hands the engine the ``action`` key
  ``ActionExecutor`` reads, so ``gold_buy`` is a ``buy`` that safe mode refuses rather than
  a ``hold`` that could never have traded. That the side arrives, and that a true gate then
  becomes a real buy intent when execution is permitted, is asserted in
  ``tests/test_plan_engine_adaptation_fidelity.py`` (which permits execution through a
  restoring fixture, never by weakening ``SafetyMonitor``). Both consumers agreeing on *no*
  intent is still Requirement 22.4 holding; the firing case lives in that file because
  proving it here would mean changing the mode this golden is recorded under.
* The canonical vocabulary the legacy executors could not express is now translated by
  ``plan_to_engine_graph``, and the golden pins the result. Closed since this file was
  first recorded:

  - a MATH node no longer reaches the engine as a pass-through that republishes ``close``;
    ``gold_floor`` publishes the ``constant`` block's 30.0 on every bar, which is what the
    gate compares against;
  - a LOGIC node no longer arrives without an ``operator`` and is no longer evaluated as
    ``AND``; ``gold_gate`` is a real ``between``, false through the RSI warmup and false
    wherever RSI leaves the band;
  - the adaptation carries the indicator window under both the canonical ``window`` key and
    the legacy ``period`` alias, so no executor substitutes its own default;
  - per-target edge order is the target's **declared input-port order**, so a positional
    executor receives ``value``/``lower``/``upper`` as wired rather than in edge-sort or
    (after a JSON round trip) alphabetical order;
  - **task 5.4** re-pointed ``IndicatorExecutor`` at ``indicators_backend`` and deleted its
    four private ``_calculate_*`` re-implementations. ``gold_rsi``, ``gold_ema`` and
    therefore ``gold_gate`` moved when it was re-recorded, deliberately; see "THE 5.4
    RE-RECORD" below. Every declared output port of a multi-output indicator is now
    published, so an edge from ``bollinger_bands.upper`` no longer receives %B - this graph
    uses single-output indicators, so nothing about it changed on that account.

  Still open, and pinned as-is rather than pretended away: ML_DL nodes are still
  pass-throughs, and ``validate_node_inputs`` back-fills a NaN warmup region for the
  executors that call it. The golden freezes today's behaviour so closing one is a visible,
  deliberate change.

THE 5.4 RE-RECORD - what moved and why it is correct
----------------------------------------------------
Task 5.4 deleted ``IndicatorExecutor._calculate_rsi`` / ``_calculate_macd`` /
``_calculate_bollinger`` / ``_calculate_atr`` and delegated to the module the descriptors'
``runtime_ref`` names. Those private copies **disagreed** with the published implementation,
so the engine and the registry were describing different numbers. Three digests moved and
each is the disagreement being resolved in the registry's favour:

* ``gold_rsi``. The deleted copy averaged gains and losses with a simple rolling mean and
  divided by ``loss + 1e-9``. Over this fixture's flat stretch (bars 40-61 have no price
  change, which the candle file says it exists to produce) that denominator collapses, so the
  series slammed between **100.0 and 0.0** with no price movement between them - a flat market
  read first as maximally overbought and then as maximally oversold. ``indicators_backend.rsi``
  uses Wilder smoothing and holds **93.835** across the same bars. Its warmup is also 14 bars
  rather than 13, which is what ``INDICATOR_SPECS`` publishes as this block's warmup; the
  deleted copy became "ready" one bar before its first trustworthy value.
* ``gold_ema``. The deleted branch was ``close.ewm(span=20, adjust=False).mean()``, which
  emits a value at **bar 0** - a 20-bar EMA computed from one bar - where the registry
  publishes a 20-bar warmup. ``indicators_backend.ema`` returns NaN for bars 0-18 and seeds
  bar 19 with the mean of the first 20 closes, so ``first`` moved from ``100.0`` to ``NaN``
  and the last bar moved by 2.5e-6 as that seed decayed. Requirement 20.1 depends on an
  absent value being absent: a readiness gate cannot hold a node "warming" if the node
  fabricates a number for a bar it has no window for.
* ``gold_gate``. ``between(rsi, 30.0, ema)`` is downstream of both, so it moved with them.
  It is now false for 19 bars (the longer of the two warmups, because a comparator reports
  false on an undefined bar) and true for the remaining 101. The previous false runs inside
  the true region were the RSI zero-denominator collapse, not the strategy.

``dag_hash``, the persisted plan bytes, ``execution_order``, ``execution_levels``, the engine
payload digest, ``gold_data``, ``gold_floor``, ``gold_buy``, ``signals_rle`` and ``intents``
are all unchanged: the graph's meaning did not move, and no trade decision moved.
* Nothing downstream of the engine is covered: ``DAGEventLoop._process_event`` (Redis
  advisory lock), ``_emit_signal`` (execution flags, Redis idempotency, websocket
  publish), ``BacktestRuntime.run_backtest``'s record creation and exchange OHLCV fetch,
  the risk engine and the portfolio simulator. Those need a database, Redis or an
  exchange; the committed candles stand in for the exchange fetch and the rest is out of
  scope for a determinism contract.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graph is a real canonical graph built
from published descriptors, the plan is produced by the real compiler, and the execution
is the real ``DAGEngine`` over real pandas. The only substitutions are the ones named
above, all of them genuinely external I/O.

DETERMINISM
-----------
Node and edge ids are fixed literals, never minted: ``dag_hash`` is a function of them, so
a minted id would make a golden file impossible. The candles are committed data, not
generated at run time. No wall clock and no randomness reaches an asserted value.
``test_two_evaluations_of_one_row_agree`` re-runs the whole pipeline in-process and
compares, and the file is asserted byte-for-byte across processes by construction.

REGENERATING
------------
Deliberately, never casually::

    set AERORA_UPDATE_GOLDEN=1
    .venv\\Scripts\\python.exe -m pytest tests/test_dag_runtime_golden_plan.py

Then read the diff. A changed ``dag_hash`` means the graph's meaning changed. A changed
node digest with an unchanged hash means an executor changed - i.e. live behaviour moved.
"""

import hashlib
import json
import math
import os
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_event_loop import (
    DAGEventLoop,
    EventType,
    MarketEvent,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import (
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
    compute_dag_hash,
)
from backend_app.core.safety_config import SafetyMonitor

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent / "golden"
CANDLE_PATH = GOLDEN_DIR / "dag_runtime_plan_candles.json"
GOLDEN_PATH = GOLDEN_DIR / "dag_runtime_plan_golden.json"

#: Set to "1" to rewrite the golden file instead of asserting against it.
UPDATE_ENV_VAR = "AERORA_UPDATE_GOLDEN"

REGENERATE_HINT = (
    "This is a GOLDEN file assertion. If the change is intended, regenerate it "
    "deliberately and review the diff:\n"
    f"    set {UPDATE_ENV_VAR}=1\n"
    "    .venv\\Scripts\\python.exe -m pytest tests/test_dag_runtime_golden_plan.py\n"
    "A changed dag_hash means the graph's meaning changed. A changed node-output digest "
    "with an unchanged dag_hash means an executor or the plan adaptation changed, which "
    "means live behaviour moved."
)


# ---------------------------------------------------------------------------
# The fixed canonical graph. Explicit ids, never minted.
# ---------------------------------------------------------------------------

#: Node ids are literals. ``compute_dag_hash`` hashes them, so ``NodeSpec.create`` (which
#: mints a fresh uuid) would change the hash on every run and no golden file could exist.
NODE_DATA = "gold_data"
NODE_RSI = "gold_rsi"
NODE_EMA = "gold_ema"
NODE_FLOOR = "gold_floor"
NODE_GATE = "gold_gate"
NODE_BUY = "gold_buy"


def _write_json(path: pathlib.Path, payload) -> None:
    """Write UTF-8 without a BOM and with LF endings, so the file is stable in git."""
    text = json.dumps(payload, indent=1, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _read_json(path: pathlib.Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _digest(payload) -> str:
    """A stable sha256 over a JSON-canonical form of ``payload``."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_graph(reg) -> StrategyGraph:
    """The one fixed canonical graph this file is about.

    ``ohlcv_feed`` fans out to ``rsi`` and ``ema``; a ``constant`` supplies the lower
    bound; ``between`` is the boolean gate; ``action_buy_market`` is the sink. Every id is
    a literal, every param is declared by the block's descriptor (the validator reports
    ``PARAM_UNKNOWN`` for anything else, and this graph produces no warnings).
    """

    def node(node_id: str, block_id: str, **params) -> NodeSpec:
        return NodeSpec(
            id=node_id,
            block_id=block_id,
            category=reg[block_id].category,
            params=dict(params),
        )

    return StrategyGraph(
        schema_version=2,
        strategy_id="golden-strategy",
        version="1.0.0",
        name="Golden runtime plan",
        nodes=[
            node(
                NODE_DATA,
                "ohlcv_feed",
                symbol="ETH/USDT",
                timeframe="5m",
                market_type="spot",
                mode="streaming",
            ),
            node(NODE_RSI, "rsi", window=14),
            node(NODE_EMA, "ema", window=20, source="close"),
            node(NODE_FLOOR, "constant", value=30.0),
            node(NODE_GATE, "between", inclusive=True),
            node(
                NODE_BUY,
                "action_buy_market",
                quantity_type="percent_of_equity",
                quantity=0.25,
            ),
        ],
        edges=[
            EdgeSpec(
                id="gold_e_data_rsi",
                source=NODE_DATA,
                source_port="close",
                target=NODE_RSI,
                target_port="series",
            ),
            EdgeSpec(
                id="gold_e_data_ema",
                source=NODE_DATA,
                source_port="close",
                target=NODE_EMA,
                target_port="series",
            ),
            EdgeSpec(
                id="gold_e_rsi_gate",
                source=NODE_RSI,
                source_port="value",
                target=NODE_GATE,
                target_port="value",
            ),
            EdgeSpec(
                id="gold_e_floor_gate",
                source=NODE_FLOOR,
                source_port="value",
                target=NODE_GATE,
                target_port="lower",
            ),
            EdgeSpec(
                id="gold_e_ema_gate",
                source=NODE_EMA,
                source_port="value",
                target=NODE_GATE,
                target_port="upper",
            ),
            EdgeSpec(
                id="gold_e_gate_buy",
                source=NODE_GATE,
                source_port="out",
                target=NODE_BUY,
                target_port="signal",
            ),
        ],
    )


def version_row(graph: StrategyGraph, plan: CompiledPlan) -> dict:
    """A ``strategy_versions``-shaped row holding the persisted artifact.

    ``compiled_plan`` is stored as JSON **text**, which is what a column holds and what
    ``strategy_compiler._decode_stored_plan`` reads back through
    ``CompiledPlan.from_json``. Storing the live object would defeat the purpose: the
    point is that the bytes on disk are what runs.
    """
    return {
        "id": "gold-version-0001",
        "strategy_id": graph.strategy_id,
        "version": graph.version,
        "graph_json": graph.to_dict(),
        "compiled_plan": plan.to_json(),
        "dag_hash": plan.dag_hash,
    }


# ---------------------------------------------------------------------------
# The fixed candles
# ---------------------------------------------------------------------------


def candle_fixture() -> dict:
    """The committed OHLCV fixture. Data, not a generator: no seed is re-run here."""
    return _read_json(CANDLE_PATH)


def candles_dataframe(fixture: dict) -> pd.DataFrame:
    """The fixture as the ``market_data`` frame the backtester hands the engine.

    This stands in for ``DataEngine.fetch_historical_ohlcv`` - the one piece of
    ``run_backtest`` that genuinely reaches an exchange.
    """
    bars = fixture["bars"]
    index = pd.DatetimeIndex([pd.Timestamp(bar["t"]) for bar in bars], name="timestamp")
    return pd.DataFrame(
        {
            "open": [bar["o"] for bar in bars],
            "high": [bar["h"] for bar in bars],
            "low": [bar["l"] for bar in bars],
            "close": [bar["c"] for bar in bars],
            "volume": [bar["v"] for bar in bars],
        },
        index=index,
    )


def candle_events(fixture: dict) -> list:
    """The fixture as the closed-candle ``MarketEvent`` stream a live feed delivers."""
    symbol = fixture["symbol"]
    timeframe = fixture["timeframe"]
    return [
        MarketEvent(
            event_type=EventType.CANDLE,
            symbol=symbol,
            timestamp=pd.Timestamp(bar["t"]).to_pydatetime(),
            open=bar["o"],
            high=bar["h"],
            low=bar["l"],
            close=bar["c"],
            volume=bar["v"],
            timeframe=timeframe,
            source="golden-fixture",
        )
        for bar in fixture["bars"]
    ]


# ---------------------------------------------------------------------------
# Turning a run into comparable, readable values
# ---------------------------------------------------------------------------


def _numbers(series: pd.Series) -> list:
    """``series`` as JSON-safe numbers, rounded so no platform float tail leaks in.

    ``NaN`` is emitted as the string ``"NaN"`` rather than a bare JSON ``NaN``: the RSI
    block returns its warmup region unfilled, and a golden file has to be valid JSON.
    """
    out = []
    for value in series.tolist():
        if isinstance(value, bool):
            out.append(bool(value))
        elif value is None:
            out.append(None)
        else:
            number = float(value)
            out.append("NaN" if math.isnan(number) else round(number, 8))
    return out


def _rle(values: list) -> list:
    """Run-length encode ``values`` as ``[[value, count], ...]``: readable in a diff."""
    runs = []
    for value in values:
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
    return runs


def _series_record(series: pd.Series) -> dict:
    numbers = _numbers(series)
    return {
        "dtype": str(series.dtype),
        "length": len(numbers),
        "sha256": _digest(numbers),
        "first": numbers[0],
        "last": numbers[-1],
    }


def _intents(loop: DAGEventLoop, signals: pd.Series) -> list:
    """The trade-intent sequence, labelled by the live loop's own ``_signal_to_action``.

    A bar contributes an intent only when the engine's final signal is non-zero, which is
    the same gate ``_process_event`` applies before it builds a ``Signal``. The labelling
    is production code, not a restatement of it.
    """
    intents = []
    for timestamp, value in signals.items():
        action = loop._signal_to_action(float(value))
        if action == "hold":
            continue
        intents.append(
            {
                "bar": pd.Timestamp(timestamp).isoformat(),
                "action": action,
                "strength": round(abs(float(value)), 8),
            }
        )
    return intents


def _run_record(engine_nodes, engine_edges, result, loop: DAGEventLoop) -> dict:
    """Everything about one execution that two consumers must agree on."""
    signals = result["signals"]
    node_results = result["node_results"]
    return {
        "engine_node_ids": [node["id"] for node in engine_nodes],
        "engine_node_types": {node["id"]: node["type"] for node in engine_nodes},
        "engine_edge_order": [edge["id"] for edge in engine_edges],
        "engine_payload_sha256": _digest([engine_nodes, engine_edges]),
        "engine_execution_order": list(result["execution_order"]),
        "action_nodes": list(result["action_nodes"]),
        "node_outputs": {
            node_id: _series_record(node_results[node_id])
            for node_id in sorted(node_results)
        },
        "gate_rle": _rle(_numbers(node_results[NODE_GATE])),
        "signals_rle": _rle(_numbers(signals)),
        "signals_sha256": _digest(_numbers(signals)),
        "intents": _intents(loop, signals),
    }


# ---------------------------------------------------------------------------
# The two consumers
# ---------------------------------------------------------------------------


def backtester_run(row: dict, reg, fixture: dict) -> tuple:
    """The backtester's half of Requirement 22.4.

    Exactly the sequence inside ``BacktestRuntime.run_version_backtest`` /
    ``run_backtest``: resolve the version's plan through ``load_version_plan``, adapt it
    with ``plan_to_engine_graph``, execute it on ``BacktestRuntime.dag_engine``. Left out:
    ``BacktestService.create_backtest`` (a database write) and
    ``DataEngine.fetch_historical_ohlcv`` (an exchange call, replaced by the committed
    candles).
    """
    from backend_app.backend.backtest_runtime import BacktestRuntime

    loaded = BacktestRuntime.load_version_plan(row, registry=reg)
    nodes, edges = SC.plan_to_engine_graph(loaded.plan)
    runtime = BacktestRuntime()
    result = runtime.dag_engine.execute(
        nodes=nodes,
        edges=edges,
        market_data=candles_dataframe(fixture),
    )
    return loaded, nodes, edges, result


def live_run(row: dict, reg, fixture: dict) -> tuple:
    """The live consumer's half of Requirement 22.4.

    ``DAGEventLoop.from_version_row`` resolves the persisted plan and derives its own
    engine payload, symbols and timeframe from it. The candles are pushed through the real
    ``RollingWindow.add_candle``, and the loop's own per-symbol ``DAGEngine`` executes the
    loop's own ``dag_nodes`` / ``dag_edges`` - the same call
    ``DAGEventLoop._process_event`` makes. Left out: the Redis advisory lock that guards
    that method, and ``_emit_signal``'s execution flags, Redis idempotency set and
    websocket publish.
    """
    loop = DAGEventLoop.from_version_row(row, registry=reg, tenant_id="golden")
    symbol = fixture["symbol"]
    assert loop.symbols == [symbol], (
        "the live loop must subscribe to the market the graph's DATA node declares "
        f"(SB-06): got {loop.symbols!r}"
    )
    window = loop.rolling_windows[symbol]
    for event in candle_events(fixture):
        window.add_candle(event)
    result = loop.dag_engines[symbol].execute_dag(
        nodes=loop.dag_nodes,
        edges=loop.dag_edges,
        market_data=window.to_dataframe(),
    )
    return loop, loop.dag_nodes, loop.dag_edges, result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def fixture_candles():
    return candle_fixture()


@pytest.fixture(scope="module")
def compiled(reg):
    """The canonical graph, its compiled plan and the row that persists both."""
    graph = canonical_graph(reg)
    report = V.validate(graph, reg)
    assert report.valid, f"the canonical graph must validate: {report.codes()}"
    assert not report.warnings, (
        "the canonical graph must produce no warnings, or the golden pins a graph an "
        f"author would be nagged about: {[w.get('code') for w in report.warnings]}"
    )
    plan = SC.compile_graph(graph, reg)
    return graph, plan, version_row(graph, plan)


@pytest.fixture(scope="module")
def observed(reg, compiled, fixture_candles):
    """The full observation: what both consumers did, and the plan-level facts."""
    graph, plan, row = compiled
    loaded_bt, bt_nodes, bt_edges, bt_result = backtester_run(row, reg, fixture_candles)
    loop, live_nodes, live_edges, live_result = live_run(row, reg, fixture_candles)

    record = {
        "candles": {
            "sha256": _digest(fixture_candles),
            "bar_count": fixture_candles["bar_count"],
            "symbol": fixture_candles["symbol"],
            "timeframe": fixture_candles["timeframe"],
        },
        "safety": {
            # Recorded, not asserted away: the golden's meaning depends on it.
            "order_placing_execution_blocked": bool(
                SafetyMonitor.check_execution_allowed("strategy_signal")
            )
        },
        "graph": {
            "dag_hash": compute_dag_hash(graph),
            "node_ids": sorted(node.id for node in graph.nodes),
            "edge_addresses": sorted(edge.address for edge in graph.edges),
        },
        "plan": {
            "dag_hash": plan.dag_hash,
            "sha256": hashlib.sha256(plan.to_json().encode("utf-8")).hexdigest(),
            "schema_version": plan.schema_version,
            "compiler_version": plan.compiler_version,
            "warmup_bars": plan.warmup_bars,
            "execution_order": list(plan.execution_order),
            "execution_levels": [list(level) for level in plan.execution_levels],
            "data_nodes": list(plan.data_nodes),
            "action_nodes": list(plan.action_nodes),
            "reused_from_row": loaded_bt.reused,
            "load_reason": loaded_bt.reason,
        },
        "backtester": _run_record(bt_nodes, bt_edges, bt_result, loop),
        "live": _run_record(live_nodes, live_edges, live_result, loop),
    }
    return record


@pytest.fixture(scope="module")
def golden(observed):
    """The golden file, rewritten first when regeneration was explicitly requested."""
    if os.environ.get(UPDATE_ENV_VAR) == "1":
        _write_json(GOLDEN_PATH, observed)
    assert GOLDEN_PATH.exists(), (
        f"the golden file {GOLDEN_PATH} is missing. Create it deliberately:\n"
        f"    set {UPDATE_ENV_VAR}=1\n"
        "    .venv\\Scripts\\python.exe -m pytest tests/test_dag_runtime_golden_plan.py"
    )
    return _read_json(GOLDEN_PATH)


# ---------------------------------------------------------------------------
# Helpers that make a golden failure explain itself
# ---------------------------------------------------------------------------


def _flatten(payload, prefix="") -> dict:
    """``payload`` as ``{"dotted.path": leaf}``, so a diff names what moved."""
    flat = {}
    if isinstance(payload, dict):
        for key in payload:
            flat.update(_flatten(payload[key], f"{prefix}{key}."))
    elif isinstance(payload, list) and any(
        isinstance(item, (dict, list)) for item in payload
    ):
        for index, item in enumerate(payload):
            flat.update(_flatten(item, f"{prefix}{index}."))
    else:
        flat[prefix.rstrip(".")] = payload
    return flat


def _describe_drift(expected, actual, what: str) -> str:
    """A failure message that says what changed, not just that something did."""
    expected_flat = _flatten(expected)
    actual_flat = _flatten(actual)
    lines = [f"{what} no longer matches the golden file."]
    for key in sorted(set(expected_flat) | set(actual_flat)):
        was = expected_flat.get(key, "<absent>")
        now = actual_flat.get(key, "<absent>")
        if was != now:
            lines.append(f"  {key}: golden={was!r} now={now!r}")
    lines.append("")
    lines.append(REGENERATE_HINT)
    return "\n".join(lines)


def _assert_golden(section: str, golden: dict, observed: dict) -> None:
    expected = golden[section]
    actual = observed[section]
    assert actual == expected, _describe_drift(expected, actual, section)


# ---------------------------------------------------------------------------
# The golden assertions
# ---------------------------------------------------------------------------


class TestGoldenPlanIdentity:
    """The persisted artifact: same bytes, same hash, for every consumer (22.3)."""

    def test_graph_identity_matches_golden(self, golden, observed):
        _assert_golden("graph", golden, observed)

    def test_persisted_plan_matches_golden(self, golden, observed):
        """The plan's bytes and its ``dag_hash`` are both pinned.

        The hash is asserted explicitly and not only through the byte digest, so a
        semantic change to the graph surfaces as "the hash moved" rather than as an
        unreadable digest diff (task 2.10, point 5).
        """
        assert observed["plan"]["dag_hash"] == golden["plan"]["dag_hash"], (
            "dag_hash drifted: golden="
            f"{golden['plan']['dag_hash']} now={observed['plan']['dag_hash']}. The "
            "graph's meaning changed - node set, block ids, params, categories or port "
            "wiring.\n\n" + REGENERATE_HINT
        )
        _assert_golden("plan", golden, observed)

    def test_both_consumers_read_the_persisted_plan(self, observed):
        """Requirement 22.3: the stored plan is served, not recompiled behind our back."""
        assert observed["plan"]["reused_from_row"] is True, (
            "the row's compiled_plan was not reused (reason="
            f"{observed['plan']['load_reason']!r}). A consumer that recompiles is not "
            "serving the shared artifact Requirement 22.3 requires."
        )
        assert observed["plan"]["load_reason"] == "compiled_plan_hash_match"

    def test_candle_fixture_is_unchanged(self, golden, observed):
        """The golden values only mean anything against the candles they were taken on."""
        _assert_golden("candles", golden, observed)


class TestGoldenRuntimeOutputs:
    """A fixed graph over fixed candles produces a fixed sequence (task 2.10)."""

    def test_safety_mode_matches_the_recorded_one(self, golden, observed):
        expected = golden["safety"]["order_placing_execution_blocked"]
        actual = observed["safety"]["order_placing_execution_blocked"]
        assert actual == expected, (
            "the golden was recorded with order-placing execution "
            f"blocked={expected} but this environment reports {actual}. "
            "SafetyMonitor.check_execution_allowed('strategy_signal') decides whether "
            "dag_engine.ActionExecutor produces a side or an all-zero series, so every "
            "action-node value below depends on it. tests/conftest.py sets "
            "VYOMQUANT_MODE=safe; do not change that to make this pass."
        )

    def test_backtester_outputs_match_golden(self, golden, observed):
        _assert_golden("backtester", golden, observed)

    def test_live_outputs_match_golden(self, golden, observed):
        _assert_golden("live", golden, observed)


class TestBacktesterAndLiveAgree:
    """Requirement 22.4, asserted directly rather than inferred from a shared file."""

    def test_same_node_set_and_execution_order(self, observed):
        backtester = observed["backtester"]
        live = observed["live"]
        assert set(backtester["engine_node_ids"]) == set(live["engine_node_ids"])
        assert backtester["engine_node_ids"] == live["engine_node_ids"], (
            "the two consumers derived the node list in different orders from one "
            f"persisted plan: backtester={backtester['engine_node_ids']} "
            f"live={live['engine_node_ids']}"
        )
        assert backtester["engine_node_types"] == live["engine_node_types"]
        assert backtester["engine_execution_order"] == live["engine_execution_order"]
        assert backtester["action_nodes"] == live["action_nodes"]

    def test_same_engine_payload(self, observed):
        """Same nodes, same edges, same edge order.

        Edge order is not cosmetic: ``DAGEngine.get_node_inputs`` walks the edge list, so
        it decides the order a multi-input executor receives its inputs.
        """
        assert (
            observed["backtester"]["engine_edge_order"]
            == observed["live"]["engine_edge_order"]
        )
        assert (
            observed["backtester"]["engine_payload_sha256"]
            == observed["live"]["engine_payload_sha256"]
        )

    def test_same_per_node_outputs(self, observed):
        backtester = observed["backtester"]["node_outputs"]
        live = observed["live"]["node_outputs"]
        assert sorted(backtester) == sorted(live)
        drifted = [
            node_id
            for node_id in sorted(backtester)
            if backtester[node_id] != live[node_id]
        ]
        assert not drifted, (
            "a backtest and a live run of one version disagree on these nodes over "
            f"identical candles (Requirement 22.4): {drifted}\n"
            + "\n".join(
                f"  {node_id}: backtest={backtester[node_id]} live={live[node_id]}"
                for node_id in drifted
            )
        )

    def test_same_intent_sequence(self, observed):
        """The requirement in one line: the same version, the same intents."""
        assert (
            observed["backtester"]["signals_sha256"]
            == observed["live"]["signals_sha256"]
        )
        assert observed["backtester"]["signals_rle"] == observed["live"]["signals_rle"]
        assert observed["backtester"]["intents"] == observed["live"]["intents"], (
            "the trade-intent sequences differ:\n"
            f"  backtest={observed['backtester']['intents']}\n"
            f"  live={observed['live']['intents']}"
        )


class TestDeterminism:
    """Two evaluations of one row must not differ. Twice in-process, once per process."""

    def test_two_evaluations_of_one_row_agree(self, reg, compiled, fixture_candles):
        _graph, _plan, row = compiled
        first = backtester_run(row, reg, fixture_candles)
        second = backtester_run(row, reg, fixture_candles)
        loop, _n, _e, _r = live_run(row, reg, fixture_candles)
        first_record = _run_record(first[1], first[2], first[3], loop)
        second_record = _run_record(second[1], second[2], second[3], loop)
        assert first_record == second_record, _describe_drift(
            first_record, second_record, "a second evaluation of the same row"
        )

    def test_plan_bytes_survive_the_round_trip(self, compiled):
        """The row's text and the plan it reads back to are the same artifact."""
        _graph, plan, row = compiled
        reloaded = CompiledPlan.from_json(row["compiled_plan"])
        assert reloaded.to_json() == plan.to_json()
        assert reloaded.dag_hash == plan.dag_hash
