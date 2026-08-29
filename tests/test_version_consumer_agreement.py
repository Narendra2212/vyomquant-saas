"""
tests/test_version_consumer_agreement.py

One stored artifact, two consumers, one answer - and a recompile the moment the artifact
stops describing the version.

Spec: strategy-builder task 8.10 (``design.md`` -> "Migration path for callers", and the
acceptance-gate item "Backtester and live execution both load the same ``compiled_plan``
for a version and produce the same intent sequence on identical data").
Requirements 22.3, 22.4, 22.5.

**Property 25: Both version consumers load the same ``compiled_plan`` and observe the same
``dag_hash``, and produce the same intent sequence on identical data; a hash mismatch
triggers recompilation before execution.**

WHAT IS UNDER TEST, AND WHY IT IS NOT THE GOLDEN FILE AGAIN
-----------------------------------------------------------
``tests/test_dag_runtime_golden_plan.py`` pins *values*: one graph over one candle series
produces one recorded intent sequence. That file is the reference recording and it is not
touched here - not its golden JSON, not its candles, not one of its digests. This file
pins the *agreement rule* around it, over the four row shapes a version is actually stored
in and the branch each one drives:

============================================ ==========================================
row shape                                    what must happen
============================================ ==========================================
``graph_json`` + ``compiled_plan`` matching  served as-is, no recompile (22.3)
``blueprint`` + ``execution_graph``          the migration-004-unapplied shape: warn,
                                             degrade, serve the same artifact (22.3)
``compiled_plan`` whose hash disagrees       recompile BEFORE execution (22.5)
``compiled_plan`` unreadable / absent        compile from the graph, never crash
============================================ ==========================================

and, for each, that the **backtester** (``BacktestRuntime.load_version_plan``) and the
**live loop** (``DAGEventLoop.from_version_row``) reach byte-identical plans, one identity
hash and one intent sequence.

THE DOUBLES ARE BORROWED, NOT RE-SPELLED
----------------------------------------
The canonical graph, the ``strategy_versions``-shaped row, the committed candle fixture and
both consumer pipelines are imported from ``tests/test_dag_runtime_golden_plan.py``; the
deterministic OHLCV frame, the registry fixture and the ``unfrozen`` safety fixture are
imported from ``tests/test_task_8_4_runtime_readiness_gate.py``. Nothing about either file
is modified. A second spelling of "the graph both consumers agree about" would be the one
thing this file cannot afford: the whole claim is that two readers of one artifact agree, so
the artifact has to be the same object the reference recording uses.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs built
from published descriptors, the plans come from the real validator and the real compiler,
the rows are the real column values ``CompiledVersion.canonical_columns()`` produces, the
loaders are the two production entry points, and execution is the real ``DAGEngine`` -
``execute_dag`` on the backtester's frame, ``execute_dag`` on the live loop's own
``RollingWindow`` fed with real ``MarketEvent`` candles, and ``execute_plan`` for the
readiness-gated intent path. There is no database and no fake client: a ``strategy_versions``
row is a dict of column values, which is what both loaders take.

A REAL DEFECT WAS FOUND AND FIXED IN SOURCE
-------------------------------------------
``strategy_compiler._PLAN_COLUMNS`` reads ``compiled_plan`` and then the legacy
``execution_graph``, documenting the second entry as "where
``StrategyService._insert_version_row`` puts the serialized plan while migration 004 part 1
is unapplied". That fallback was unreachable. ``blueprint`` is ``NOT NULL`` in the
pre-canonical schema, so the degraded INSERT writes the graph to ``blueprint`` and the plan
to ``execution_graph`` and writes no ``graph_json`` at all - and ``schema.extract_raw_graph``
read ``graph_json``, then a version 1 ``buy_logic`` blob, then top-level ``nodes``, and
raised. So ``load_plan`` raised ``CompilerError`` before it could reach the plan column that
exists for exactly this case: for every row saved while 004 is unapplied - which is every
row in this environment - **both** version consumers refused the version outright instead of
degrading. ``extract_raw_graph`` now falls back to a graph-shaped ``blueprint`` last, and
``load_plan`` warns naming the file to apply. The refusal is preserved where it belongs: a
``blueprint`` that is the genuinely legacy entry/exit-condition blob is still unreadable
rather than half-parsed, and the deploy gate still refuses the degraded row for its missing
``dag_hash``, which :class:`TestDegradedPersistenceServesTheSameArtifact` asserts.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
* **No HTTP, and no database.** Requirement 22.3 names the Strategy_Builder_API; what is
  checkable without PostgreSQL is the projection the API serves from
  (``routers.strategies._dag_fields`` / ``_lift_dag_fields``) and the loaders every consumer
  goes through. That the row itself cannot be edited under a running deployment is
  ``004c_immutable_versions.sql``'s trigger, which needs a real database (Requirement 9.2,
  task 8.12).
* **Migrations 004-004e are unapplied here**, so the degraded shape is the *normal* shape in
  this environment and the canonical shape is the one constructed by hand. Neither
  ``chk_valid_requires_hash`` nor the immutability trigger is exercised.
* **No order is placed.** ``execute_plan`` returns Trade_Intents and sends nothing;
  ``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe`` and the intent-emission tests lift that
  through 8.4's own per-test ``unfrozen`` fixture, never in ``conftest`` and never by
  weakening ``SafetyMonitor``.
* **Agreement, not correctness.** That the intents are the *right* intents is the golden
  file's job. This file says the two consumers cannot disagree, and that a stale artifact
  cannot be the thing that runs.
"""

import contextlib
import copy
import inspect
import os
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend import strategy_lifecycle as SL
from backend_app.backend import strategy_service as SS
from backend_app.backend.backtest_runtime import BacktestRuntime
from backend_app.backend.dag_engine import DAGEngine, PlanRuntimeState
from backend_app.backend.dag_event_loop import DAGEventLoop
from backend_app.backend.strategy_builder import compile_version
from backend_app.backend.strategy_dag import schema as schema_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import (NodeSpec, StrategyGraph,
                                                     compute_dag_hash)
from backend_app.routers import strategies as strategies_router

# ---------------------------------------------------------------------------
# The doubles, borrowed from the two files that already own them.
#
# ``reg`` and ``unfrozen`` are fixtures; importing them into this module's namespace is how
# pytest registers them here, so the registry is assembled once and the platform SYSTEM
# FREEZE is lifted per test rather than globally.
# ---------------------------------------------------------------------------
from tests.test_dag_runtime_golden_plan import (NODE_BUY, NODE_EMA, NODE_FLOOR,
                                                NODE_GATE, NODE_RSI, _digest,
                                                _run_record, backtester_run,
                                                candle_fixture,
                                                canonical_graph, live_run,
                                                version_row)
from tests.test_task_8_4_runtime_readiness_gate import candles  # noqa: F401
from tests.test_task_8_4_runtime_readiness_gate import reg  # noqa: F401
from tests.test_task_8_4_runtime_readiness_gate import unfrozen  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Long enough that every node of the canonical graph is warm (``ema(20)`` composes to 60).
LONG_BARS = 140


# ---------------------------------------------------------------------------
# Row shapes. Every value comes from the real writer's own output.
# ---------------------------------------------------------------------------


def _respec(graph: StrategyGraph, overrides: dict) -> StrategyGraph:
    """``graph`` with the named nodes' params replaced. Ids and wiring are untouched.

    A *semantic* edit by construction: ``compute_dag_hash`` covers node params, so any
    override here moves the identity hash, which is what Requirement 22.5 is about. Node
    ids stay literal, so nothing here mints an id a hash could not be pinned against.
    """
    nodes = [
        NodeSpec(
            id=node.id,
            block_id=node.block_id,
            category=node.category,
            params=dict(overrides.get(node.id, node.params)),
        )
        for node in graph.nodes
    ]
    return StrategyGraph(
        schema_version=graph.schema_version,
        strategy_id=graph.strategy_id,
        version=graph.version,
        name=graph.name,
        nodes=nodes,
        edges=list(graph.edges),
    )


@pytest.fixture(scope="module")
def canonical(reg):
    """``(graph, plan, row)``: the canonical post-004 row shape.

    ``row`` is 2.10's own ``version_row`` - ``graph_json`` as a dict, ``compiled_plan`` as
    the JSON **text** a column holds, ``dag_hash`` alongside - so the bytes under test are
    the bytes a database round trip would hand back.
    """
    graph = canonical_graph(reg)
    report = V.validate(graph, reg)
    assert report.valid, f"the canonical graph must validate: {report.codes()}"
    plan = SC.compile_graph(graph, reg)
    return graph, plan, version_row(graph, plan)


@pytest.fixture(scope="module")
def degraded(reg):
    """``(graph, plan, row)`` in the shape ``_insert_version_row`` writes without 004.

    Built from ``CompiledVersion.canonical_columns()`` - the writer's own values - and
    assembled the way ``StrategyService._insert_version_row`` assembles the degraded row:
    ``blueprint`` carries the graph (it is ``NOT NULL`` in the pre-canonical schema) and
    ``execution_graph`` carries ``compiled_plan``. No ``graph_json``, no ``dag_hash``, no
    ``validation_state``: those columns do not exist yet.
    """
    graph = canonical_graph(reg)
    compiled = compile_version(graph, reg)
    columns = compiled.canonical_columns()
    row = {
        "id": "degraded-version-0001",
        "strategy_id": graph.strategy_id,
        "version": "v1.0",
        "blueprint": columns["graph_json"],
        "execution_graph": columns["compiled_plan"],
        "is_draft": True,
        "is_current": True,
    }
    return graph, compiled.plan, row


@pytest.fixture(scope="module")
def stale(reg, canonical):
    """``(graph_b, stored_plan_a, row)``: a row whose stored plan describes another graph.

    The RSI window moves 14 -> 21 in ``graph_json`` while ``compiled_plan`` and the
    ``dag_hash`` column still hold the plan compiled from the 14-bar graph. This is the
    exact state Requirement 22.5 exists for, and the only honest way to produce it: the
    plan is a *real* compile of a *real* graph, just not this row's graph.
    """
    graph_a, plan_a, _row = canonical
    graph_b = _respec(graph_a, {NODE_RSI: {"window": 21}})
    assert compute_dag_hash(graph_b) != plan_a.dag_hash
    report = V.validate(graph_b, reg)
    assert report.valid, f"the edited graph must still validate: {report.codes()}"
    row = {
        "id": "stale-version-0001",
        "strategy_id": graph_b.strategy_id,
        "version": graph_b.version,
        "graph_json": graph_b.to_dict(),
        "compiled_plan": plan_a.to_json(),
        "dag_hash": plan_a.dag_hash,
    }
    return graph_b, plan_a, row


# ---------------------------------------------------------------------------
# The two consumers, asked the same question
# ---------------------------------------------------------------------------


def load_as_backtester(row, reg):
    """The backtester's loader: ``BacktestRuntime.load_version_plan``. A staticmethod, so
    no simulator, risk engine or backtest service is constructed to ask it a question."""
    return BacktestRuntime.load_version_plan(row, registry=reg)


@contextlib.contextmanager
def _recording_load_plan():
    """Record the :class:`LoadedPlan` a consumer actually received.

    ``DAGEventLoop.from_version_row`` keeps ``plan`` and ``dag_hash`` but not the
    provenance, so ``reused`` / ``reason`` would otherwise have to be re-derived by calling
    ``load_plan`` a second time - which asserts what *this* test does, not what the loop
    did. The real function runs; only its result is observed on the way past.
    """
    seen = []
    real = SC.load_plan

    def spy(row, registry=None, **kwargs):
        loaded = real(row, registry, **kwargs)
        seen.append(loaded)
        return loaded

    SC.load_plan = spy
    try:
        yield seen
    finally:
        SC.load_plan = real


def load_as_live(row, reg):
    """The live consumer's loader, reached the way a deployment reaches it.

    ``DAGEventLoop.from_version_row`` resolves the plan and then derives its symbols,
    timeframe and engine payload from it, so the returned pair is "what the loop loaded"
    and "what the loop will execute" - the second is what makes "recompiled *before*
    execution" checkable rather than assumed.
    """
    with _recording_load_plan() as seen:
        loop = DAGEventLoop.from_version_row(row, registry=reg, tenant_id="agreement")
    assert len(seen) == 1, (
        "the live loop must resolve its plan through strategy_compiler.load_plan exactly "
        f"once; observed {len(seen)} calls"
    )
    loaded = seen[0]
    assert loop.plan is loaded.plan, (
        "the loop is holding a plan other than the one load_plan returned"
    )
    assert loop.dag_hash == loaded.dag_hash
    return loaded, loop


def _plan_bytes(plan: CompiledPlan) -> str:
    """The one serialization path, which is also what the column holds."""
    return plan.to_json()


def _engine_payload_digest(plan: CompiledPlan, reg) -> str:
    """A digest of the ``(nodes, edges)`` pair a consumer hands the engine.

    Edge **order** is inside the digest deliberately: ``DAGEngine.get_node_inputs`` walks
    the edge list, so it decides the order a multi-input executor receives its operands.
    """
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    return _digest([nodes, edges])


def _intents(plan: CompiledPlan, frame, reg):
    """The Trade_Intent sequence ``execute_plan`` emits for ``plan`` over ``frame``.

    Task 8.4's gate, called rather than restated: an intent exists only when every node in
    its upstream closure is READY. Returned as dicts so two sequences compare by value.
    """
    engine = DAGEngine(enable_event_buffer=False)
    state = PlanRuntimeState()
    intents = engine.execute_plan(plan, frame, state, registry=reg)
    return [intent.to_dict() for intent in intents], state


# ---------------------------------------------------------------------------
# Requirement 22.3 - one stored plan, one hash, every consumer
# ---------------------------------------------------------------------------


class TestOneArtifactManyConsumers:
    """The stored plan is served, not re-derived, and every reader gets the same bytes."""

    def test_both_consumers_reuse_the_stored_plan(self, canonical, reg):
        _graph, _plan, row = canonical
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)

        assert bt.reused is True and live.reused is True, (
            "a consumer that recompiles a matching plan is not serving the shared "
            f"artifact: backtester={bt.reason!r} live={live.reason!r}"
        )
        assert bt.reason == live.reason == "compiled_plan_hash_match"

    def test_both_consumers_see_byte_identical_plans(self, canonical, reg):
        _graph, plan, row = canonical
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)

        assert _plan_bytes(bt.plan) == _plan_bytes(live.plan), (
            "the two version consumers hold different plan bytes for one version"
        )
        assert _plan_bytes(bt.plan) == _plan_bytes(plan)
        assert _plan_bytes(bt.plan) == row["compiled_plan"], (
            "the served plan is not the bytes the column holds"
        )

    def test_both_consumers_observe_the_hash_the_row_records(self, canonical, reg):
        _graph, plan, row = canonical
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)

        assert bt.dag_hash == live.dag_hash == plan.dag_hash == row["dag_hash"]
        # Read as a FIELD on the way through, never ``plan.get("dag_hash")`` (SB-02).
        assert bt.plan.dag_hash == row["dag_hash"]

    def test_the_worker_seam_is_served_the_same_bytes(self, canonical, reg):
        """``dag_worker`` / ``dag_event_loop`` reach ``load_plan`` directly (``design.md``
        -> Migration path for callers). A third reader must not be a third answer."""
        _graph, plan, row = canonical
        loaded = SC.load_plan(row, reg)
        assert loaded.reused is True
        assert _plan_bytes(loaded.plan) == _plan_bytes(plan)
        assert loaded.dag_hash == plan.dag_hash

    def test_the_api_projection_serves_those_same_bytes_and_that_same_hash(
        self, canonical
    ):
        """Requirement 22.3's API half, at the seam that needs no database.

        ``routers.strategies._dag_fields`` is what the save path persists and
        ``_lift_dag_fields`` is what every reader of that row projects onto the response.
        A consumer handed the response must be able to rebuild the *same* plan.
        """
        _graph, plan, _row = canonical
        stored = strategies_router._dag_fields(plan)
        served = strategies_router._lift_dag_fields({"buy_logic": dict(stored)})

        assert served["dag_hash"] == plan.dag_hash
        assert served["execution_order"] == list(plan.execution_order)
        rebuilt = CompiledPlan.from_dict(served["compiled_plan"])
        assert _plan_bytes(rebuilt) == _plan_bytes(plan), (
            "the plan an API consumer rebuilds from the response is not the plan the "
            "runtime executes"
        )
        assert rebuilt.dag_hash == plan.dag_hash

    def test_no_consumer_recompiles_while_the_hash_matches(
        self, canonical, reg, monkeypatch
    ):
        """Asserted by making a recompile impossible rather than by reading a flag.

        ``reused=True`` is a report; this is the fact behind it. With ``compile_graph``
        replaced by something that raises, a matching row must still load - so the reuse
        branch genuinely skipped the compiler.
        """
        _graph, plan, row = canonical

        def _explode(*args, **kwargs):  # pragma: no cover - must never be reached
            raise AssertionError(
                "load_plan recompiled a version whose stored plan already matched its "
                "graph, which discards the artifact Requirement 22.3 exists to share"
            )

        monkeypatch.setattr(SC, "compile_graph", _explode)
        loaded = SC.load_plan(row, reg)
        assert loaded.reused is True
        assert loaded.dag_hash == plan.dag_hash

    def test_loading_a_version_never_rewrites_the_row(self, canonical, reg):
        """A version is immutable (Requirement 9.2). Reading it must not edit it, and a
        loader that mutated the row would make "the same bytes" depend on read order."""
        _graph, _plan, row = canonical
        before = copy.deepcopy(row)
        load_as_backtester(row, reg)
        load_as_live(row, reg)
        SC.load_plan(row, reg)
        assert row == before, "loading the version mutated the stored row"

    def test_the_two_consumers_derive_the_same_engine_payload(self, canonical, reg):
        """Same plan is not enough on its own: the payload each consumer adapts the plan
        to is what actually executes, down to edge order."""
        _graph, _plan, row = canonical
        bt = load_as_backtester(row, reg)
        live, loop = load_as_live(row, reg)

        assert _engine_payload_digest(bt.plan, reg) == _engine_payload_digest(
            live.plan, reg
        )
        assert _digest([loop.dag_nodes, loop.dag_edges]) == _engine_payload_digest(
            bt.plan, reg
        ), "the live loop executes a payload the backtester never sees"

    def test_a_plan_stored_as_a_dict_and_as_text_are_the_same_artifact(
        self, canonical, reg
    ):
        """A JSONB column may hand back either. Neither may be a different strategy."""
        _graph, plan, row = canonical
        as_dict = dict(row)
        as_dict["compiled_plan"] = plan.to_dict()
        from_text = SC.load_plan(row, reg)
        from_dict = SC.load_plan(as_dict, reg)

        assert from_dict.reused is True and from_dict.reason == "compiled_plan_hash_match"
        assert _plan_bytes(from_dict.plan) == _plan_bytes(from_text.plan)
        assert from_dict.dag_hash == from_text.dag_hash


# ---------------------------------------------------------------------------
# Requirement 22.3 while migration 004 part 1 is unapplied
# ---------------------------------------------------------------------------


class TestDegradedPersistenceServesTheSameArtifact:
    """The shape every row in this environment actually has: warn, degrade, agree."""

    def test_the_fixture_is_the_shape_the_writer_writes(self, degraded):
        """The degraded row is a claim about production code, so the claim is checked.

        ``_insert_version_row``'s degraded branch assigns ``compiled_plan``'s value to
        ``execution_graph`` and leaves ``base_row``'s ``blueprint`` as the only graph. If
        that branch is ever rewritten, this fixture stops describing it and the tests
        below stop meaning anything - so the assertion is against the real source.
        """
        source = inspect.getsource(SS.StrategyService._insert_version_row)
        assert 'row["execution_graph"] = canonical_columns["compiled_plan"]' in source
        _graph, _plan, row = degraded
        assert "graph_json" not in row and "dag_hash" not in row
        assert "compiled_plan" not in row and "validation_state" not in row
        assert isinstance(row["blueprint"], dict)
        assert {"nodes", "edges"} <= set(row["blueprint"])

    def test_both_consumers_read_it_rather_than_refusing_it(self, degraded, reg):
        """The defect this file found: this row used to raise ``CompilerError`` for every
        consumer, because the graph read gave up before the plan column was reached."""
        _graph, _plan, row = degraded
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)

        assert bt.reused is True and live.reused is True
        assert bt.reason == live.reason == "execution_graph_hash_match", (
            "the legacy plan column is what _PLAN_COLUMNS documents itself as reading "
            f"for this shape: got backtester={bt.reason!r} live={live.reason!r}"
        )

    def test_it_serves_the_same_plan_and_hash_as_the_canonical_row(
        self, degraded, canonical, reg
    ):
        """Requirement 22.3 does not have a migration clause: the artifact is the artifact
        whichever column it survived in."""
        _dg, _dp, degraded_row = degraded
        _cg, canonical_plan, canonical_row = canonical

        loaded = SC.load_plan(degraded_row, reg)
        assert _plan_bytes(loaded.plan) == _plan_bytes(canonical_plan)
        assert loaded.dag_hash == canonical_plan.dag_hash == canonical_row["dag_hash"]
        assert _engine_payload_digest(loaded.plan, reg) == _engine_payload_digest(
            canonical_plan, reg
        )

    def test_it_warns_and_names_the_file_to_apply(self, degraded, reg, caplog):
        _graph, _plan, row = degraded
        with caplog.at_level("WARNING"):
            SC.load_plan(row, reg)
        text = caplog.text
        assert SC.CANONICAL_COLUMN_MIGRATION in text, (
            "a degraded load must name the migration an operator has to apply; "
            f"captured: {text!r}"
        )
        assert "blueprint" in text and "graph_json" in text
        assert "dag_hash" in text, (
            "the warning must say what the row is missing, not only where the graph came "
            "from: without dag_hash the deploy-time hash gate cannot run"
        )

    def test_the_named_file_exists_and_every_module_spells_it_the_same_way(self):
        """Three modules name this file in their degradation warnings. One spelling, or an
        operator is told to apply a file that does not exist."""
        assert SC.CANONICAL_COLUMN_MIGRATION == SS.CANONICAL_COLUMN_MIGRATION
        assert SC.CANONICAL_COLUMN_MIGRATION == SL.CANONICAL_LIFECYCLE_MIGRATION
        assert (REPO_ROOT / SC.CANONICAL_COLUMN_MIGRATION).is_file(), (
            f"{SC.CANONICAL_COLUMN_MIGRATION} is named in a warning but is not on disk"
        )

    def test_the_two_consumers_agree_on_the_degraded_row_too(self, degraded, reg):
        _graph, _plan, row = degraded
        bt = load_as_backtester(row, reg)
        live, loop = load_as_live(row, reg)
        assert _plan_bytes(bt.plan) == _plan_bytes(live.plan)
        assert bt.dag_hash == live.dag_hash
        assert _digest([loop.dag_nodes, loop.dag_edges]) == _engine_payload_digest(
            bt.plan, reg
        )

    def test_a_legacy_non_graph_blueprint_is_still_refused(self, reg):
        """The fallback is gated, not blanket. ``blueprint`` also holds the pre-DAG
        entry/exit-condition blob, which is not a graph; half-parsing one would execute a
        strategy nobody described."""
        row = {
            "id": "legacy-0001",
            "blueprint": {
                "entry_conditions": [{"indicator": "rsi", "operator": "<", "value": 30}],
                "exit_conditions": [{"indicator": "rsi", "operator": ">", "value": 70}],
                "risk": {"stop_loss_pct": 2.0},
            },
        }
        with pytest.raises(SC.CompilerError) as excinfo:
            SC.load_plan(row, reg)
        assert "no loadable strategy graph" in str(excinfo.value)

    def test_a_row_with_no_graph_at_all_is_still_refused(self, reg):
        with pytest.raises(SC.CompilerError):
            SC.load_plan({"id": "empty-0001", "version": "v1.0"}, reg)

    def test_the_fallback_is_last_and_graph_json_still_wins(self, canonical, degraded):
        """Order matters: a row carrying both must be read from ``graph_json``, which is
        the column the immutability trigger and the hash gate are written against."""
        canonical_graph_json = canonical[0].to_dict()
        other = _respec(canonical[0], {NODE_RSI: {"window": 9}}).to_dict()
        row = {"graph_json": canonical_graph_json, "blueprint": other}
        extracted = schema_module.extract_raw_graph(row)
        assert extracted == canonical_graph_json

    def test_the_deploy_gate_still_refuses_the_degraded_row(self, degraded):
        """Degrading the *load* must not soften the *deploy*. This row has no recorded
        ``validation_state`` and no ``dag_hash``, so Requirement 10.4's gate refuses it -
        exactly as it did before this file existed."""
        _graph, _plan, row = degraded
        with pytest.raises(SS.DeployPrerequisiteError) as excinfo:
            SS.StrategyService()._assert_deploy_prerequisites(row, "s-1", "v1.0")
        assert excinfo.value.missing_prerequisite == "validation_state"


# ---------------------------------------------------------------------------
# Requirement 22.5 - a hash mismatch recompiles, before anything runs
# ---------------------------------------------------------------------------


class TestHashMismatchRecompilesBeforeExecution:
    """The stored plan stops being authoritative the moment it stops describing the row."""

    def test_the_mismatch_is_detected_and_reported(self, stale, reg):
        _graph_b, plan_a, row = stale
        loaded = SC.load_plan(row, reg)
        assert loaded.reused is False
        assert loaded.reason == "compiled_plan_hash_mismatch"
        assert loaded.dag_hash != plan_a.dag_hash

    def test_the_served_hash_is_the_graphs_and_not_the_columns(self, stale, reg):
        """The ``dag_hash`` column still holds the stale value. The consumer must report
        the hash of what it is about to execute, or the mismatch is merely renamed."""
        graph_b, plan_a, row = stale
        loaded = SC.load_plan(row, reg)
        assert loaded.dag_hash == compute_dag_hash(graph_b)
        assert row["dag_hash"] == plan_a.dag_hash, "the fixture stopped being stale"

    def test_the_recompiled_plan_describes_the_rows_graph(self, stale, reg):
        graph_b, _plan_a, row = stale
        loaded = SC.load_plan(row, reg)
        assert loaded.plan.node_index[NODE_RSI].params["window"] == 21
        assert loaded.plan.matches_graph(graph_b)

    def test_it_warns_naming_both_hashes(self, stale, reg, caplog):
        """A silent recompile is a silent behaviour change. Both hashes are logged, so an
        operator can tell "the author edited the graph" from "the artifact is corrupt"."""
        graph_b, plan_a, row = stale
        with caplog.at_level("WARNING"):
            SC.load_plan(row, reg)
        assert plan_a.dag_hash in caplog.text
        assert compute_dag_hash(graph_b) in caplog.text
        assert "22.5" in caplog.text

    def test_both_consumers_recompile_and_still_agree(self, stale, reg):
        _graph_b, _plan_a, row = stale
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)
        assert bt.reused is False and live.reused is False
        assert bt.reason == live.reason == "compiled_plan_hash_mismatch"
        assert _plan_bytes(bt.plan) == _plan_bytes(live.plan)
        assert bt.dag_hash == live.dag_hash

    def test_the_recompile_precedes_execution(self, stale, reg):
        """"Before execution" is the whole clause, so it is asserted structurally.

        The live loop derives its engine payload at construction, from the loaded plan.
        Reading it before a single ``MarketEvent`` is pushed shows the recompiled plan is
        what the engine will be handed - not a stale plan corrected afterwards.
        """
        _graph_b, plan_a, row = stale
        _live, loop = load_as_live(row, reg)
        assert loop.rolling_windows["ETH/USDT"].to_dataframe().empty, (
            "nothing may have executed yet for this assertion to mean anything"
        )
        rsi_node = next(node for node in loop.dag_nodes if node["id"] == NODE_RSI)
        assert rsi_node["params"]["window"] == 21
        assert rsi_node["params"]["period"] == 21, (
            "the legacy alias must carry the recompiled window too, or the executor "
            "substitutes its own default and runs neither graph"
        )
        assert _digest([loop.dag_nodes, loop.dag_edges]) != _engine_payload_digest(
            plan_a, reg
        )

    def test_the_stale_plan_never_reaches_the_engine(self, stale, reg, unfrozen):
        """The executed intents are the ones the row's graph produces, and they are not
        the ones the stored plan produces."""
        graph_b, plan_a, row = stale
        loaded = SC.load_plan(row, reg)
        frame = candles(LONG_BARS)

        recompiled_intents, recompiled_state = _intents(loaded.plan, frame, reg)
        fresh_intents, _fresh_state = _intents(SC.compile_graph(graph_b, reg), frame, reg)
        _stale_intents, stale_state = _intents(plan_a, frame, reg)

        assert recompiled_intents == fresh_intents, (
            "executing the row produced something other than executing its own graph"
        )
        # And the two plans are observably different at run time, so the equality above is
        # not "both plans happen to behave alike": the RSI warmup moved with the window.
        assert (
            recompiled_state.warmup_required[NODE_RSI]
            != stale_state.warmup_required[NODE_RSI]
        ), (
            "the fixture's two graphs must differ in a way execution can observe, or this "
            "test cannot tell the stale plan from the recompiled one"
        )

    def test_an_unreadable_stored_plan_recompiles_rather_than_raising(
        self, canonical, reg
    ):
        """A truncated blob is worse than no plan at all, so it is treated as none."""
        graph, plan, row = canonical
        broken = dict(row)
        broken["compiled_plan"] = plan.to_json()[:40]
        loaded = SC.load_plan(broken, reg)
        assert loaded.reused is False
        assert loaded.reason == "no_stored_plan"
        assert loaded.dag_hash == compute_dag_hash(graph)

    def test_a_null_plan_column_compiles_from_the_graph(self, canonical, reg):
        graph, _plan, row = canonical
        empty = dict(row)
        empty["compiled_plan"] = None
        loaded = SC.load_plan(empty, reg)
        assert loaded.reused is False and loaded.reason == "no_stored_plan"
        assert loaded.dag_hash == compute_dag_hash(graph)

    def test_a_graph_that_no_longer_compiles_is_refused_not_run(self, canonical, reg):
        """The recompile can fail. When it does, the answer is a refusal carrying the
        report - never the stale plan as a fallback, which would run a strategy the
        validator has just rejected."""
        graph, plan, _row = canonical
        broken_graph = StrategyGraph(
            schema_version=2,
            strategy_id=graph.strategy_id,
            version=graph.version,
            name=graph.name,
            nodes=list(graph.nodes),
            edges=[edge for edge in graph.edges if edge.id != "gold_e_floor_gate"],
        )
        row = {
            "id": "broken-0001",
            "graph_json": broken_graph.to_dict(),
            "compiled_plan": plan.to_json(),
            "dag_hash": plan.dag_hash,
        }
        with pytest.raises(SC.ValidationError) as excinfo:
            SC.load_plan(row, reg)
        assert "REQUIRED_INPUT_MISSING" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Requirement 22.4 - one version, one intent sequence
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden_candles():
    """The committed OHLCV fixture from task 2.10. Data, not a generator."""
    return candle_fixture()


@pytest.fixture(scope="module")
def matched_pair(canonical, reg, golden_candles):
    """Both real consumer pipelines over the same committed candles, matched row."""
    _graph, _plan, row = canonical
    loaded_bt, bt_nodes, bt_edges, bt_result = backtester_run(row, reg, golden_candles)
    loop, live_nodes, live_edges, live_result = live_run(row, reg, golden_candles)
    return {
        "loaded": loaded_bt,
        "backtester": _run_record(bt_nodes, bt_edges, bt_result, loop),
        "live": _run_record(live_nodes, live_edges, live_result, loop),
    }


@pytest.fixture(scope="module")
def recompiled_pair(stale, reg, golden_candles):
    """Both pipelines over the same candles, from the row whose plan was stale.

    The case the golden recording does not cover: agreement has to survive the recompile
    branch, or Requirement 22.4 holds only while nobody edits a graph.
    """
    _graph_b, _plan_a, row = stale
    loaded_bt, bt_nodes, bt_edges, bt_result = backtester_run(row, reg, golden_candles)
    loop, live_nodes, live_edges, live_result = live_run(row, reg, golden_candles)
    return {
        "loaded": loaded_bt,
        "backtester": _run_record(bt_nodes, bt_edges, bt_result, loop),
        "live": _run_record(live_nodes, live_edges, live_result, loop),
    }


class TestBothConsumersProduceTheSameSequence:
    """A backtest and a live run of one version cannot disagree on identical data."""

    def test_the_matched_row_gives_both_the_same_per_node_outputs(self, matched_pair):
        backtester = matched_pair["backtester"]["node_outputs"]
        live = matched_pair["live"]["node_outputs"]
        assert sorted(backtester) == sorted(live)
        drifted = [key for key in sorted(backtester) if backtester[key] != live[key]]
        assert not drifted, (
            "a backtest and a live run of one version disagree on these nodes over "
            f"identical candles (Requirement 22.4): {drifted}"
        )

    def test_the_matched_row_gives_both_the_same_intent_sequence(self, matched_pair):
        assert (
            matched_pair["backtester"]["signals_sha256"]
            == matched_pair["live"]["signals_sha256"]
        )
        assert matched_pair["backtester"]["intents"] == matched_pair["live"]["intents"]

    def test_the_recompiled_row_gives_both_the_same_sequence_too(self, recompiled_pair):
        assert recompiled_pair["loaded"].reused is False, (
            "this fixture is about the recompile branch; it stopped exercising it"
        )
        assert recompiled_pair["backtester"] == recompiled_pair["live"], (
            "the two consumers disagree after recompiling one version from its graph"
        )

    def test_the_recompiled_run_is_not_the_matched_run(self, matched_pair, recompiled_pair):
        """Both comparisons above would pass if the edit had changed nothing. It did."""
        assert (
            matched_pair["backtester"]["node_outputs"][NODE_RSI]
            != recompiled_pair["backtester"]["node_outputs"][NODE_RSI]
        )

    def test_the_runtime_gate_agrees_on_the_two_loaded_plans(
        self, canonical, reg, unfrozen
    ):
        """The same claim at the intent path task 8.4 owns: ``execute_plan`` over the plan
        each consumer loaded, with the SYSTEM FREEZE lifted so emission is observable."""
        _graph, _plan, row = canonical
        bt = load_as_backtester(row, reg)
        live, _loop = load_as_live(row, reg)
        frame = candles(LONG_BARS)

        bt_intents, bt_state = _intents(bt.plan, frame, reg)
        live_intents, live_state = _intents(live.plan, frame, reg)

        assert bt_intents == live_intents
        assert bt_state.node_states == live_state.node_states
        assert bt_state.warmup_required == live_state.warmup_required

    def test_that_comparison_is_not_vacuous(self, canonical, reg, unfrozen):
        """Two empty lists are equal. This graph really does emit, so the equality above
        is a statement about intents rather than about silence."""
        _graph, plan, _row = canonical
        intents, _state = _intents(plan, candles(LONG_BARS), reg)
        assert intents, (
            "the agreement tests must compare non-empty sequences; this graph emitted "
            "nothing, so either the gate or the fixture changed"
        )
        assert all(intent["node_id"] == NODE_BUY for intent in intents)
        assert all(intent["triggered"] is True for intent in intents)


# ---------------------------------------------------------------------------
# Property 25
# ---------------------------------------------------------------------------


def _parameterised_graph(base: StrategyGraph, draw: dict) -> StrategyGraph:
    """The canonical graph with the drawn params. Wiring and ids are fixed.

    A smart generator, not a random one: the free variables are exactly the ones that move
    ``dag_hash`` without making the graph invalid - two indicator windows, the comparator's
    lower bound and inclusivity, and the action's size. Node ids, block ids, ports and
    edges stay put, because a graph that does not validate says nothing about whether two
    consumers of a *stored* version agree.
    """
    return _respec(
        base,
        {
            NODE_RSI: {"window": draw["rsi_window"]},
            NODE_EMA: {"window": draw["ema_window"], "source": "close"},
            NODE_FLOOR: {"value": draw["floor"]},
            NODE_GATE: {"inclusive": draw["inclusive"]},
            NODE_BUY: {
                "quantity_type": "percent_of_equity",
                "quantity": draw["quantity"],
            },
        },
    )


class TestProperty25:
    """**Property 25: Both version consumers load the same ``compiled_plan`` and observe
    the same ``dag_hash``, and produce the same intent sequence on identical data; a hash
    mismatch triggers recompilation before execution.**

    **Validates: Requirements 22.3, 22.4, 22.5**
    """

    @settings(
        deadline=None,
        max_examples=12,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    )
    @given(
        rsi_window=st.integers(min_value=2, max_value=30),
        ema_window=st.integers(min_value=2, max_value=30),
        floor=st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
        inclusive=st.booleans(),
        quantity=st.floats(
            min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        bars=st.integers(min_value=100, max_value=170),
    )
    def test_two_consumers_of_one_version_cannot_disagree(
        self, reg, unfrozen, rsi_window, ema_window, floor, inclusive, quantity, bars
    ):
        base = canonical_graph(reg)
        graph = _parameterised_graph(
            base,
            {
                "rsi_window": rsi_window,
                "ema_window": ema_window,
                "floor": floor,
                "inclusive": inclusive,
                "quantity": quantity,
            },
        )
        report = V.validate(graph, reg)
        assume(report.valid)
        plan = SC.compile_graph(graph, reg)
        row = version_row(graph, plan)
        frame = candles(bars)

        # -- 22.3: one artifact, one hash, whichever consumer asks -------------
        bt = load_as_backtester(row, reg)
        live, loop = load_as_live(row, reg)
        assert bt.reused is True and live.reused is True
        assert _plan_bytes(bt.plan) == _plan_bytes(live.plan) == _plan_bytes(plan)
        assert bt.dag_hash == live.dag_hash == plan.dag_hash == row["dag_hash"]
        assert _digest([loop.dag_nodes, loop.dag_edges]) == _engine_payload_digest(
            bt.plan, reg
        )

        # -- 22.4: identical data, identical intent sequence -------------------
        bt_intents, bt_state = _intents(bt.plan, frame, reg)
        live_intents, live_state = _intents(live.plan, frame, reg)
        assert bt_intents == live_intents, (
            f"two consumers of one version disagreed at {bars} bars: "
            f"backtester={bt_intents} live={live_intents}"
        )
        assert bt_state.node_states == live_state.node_states

        # -- 22.5: a mismatch recompiles, and the recompile is what runs -------
        edited = _respec(graph, {NODE_RSI: {"window": rsi_window + 1}})
        assume(V.validate(edited, reg).valid)
        stale_row = dict(row)
        stale_row["graph_json"] = edited.to_dict()
        assert compute_dag_hash(edited) != plan.dag_hash

        stale_bt = load_as_backtester(stale_row, reg)
        stale_live, _stale_loop = load_as_live(stale_row, reg)
        assert stale_bt.reused is False and stale_live.reused is False
        assert stale_bt.reason == "compiled_plan_hash_mismatch"
        assert stale_bt.dag_hash == stale_live.dag_hash == compute_dag_hash(edited)
        assert _plan_bytes(stale_bt.plan) == _plan_bytes(stale_live.plan)
        assert _plan_bytes(stale_bt.plan) != _plan_bytes(plan)

        stale_intents, _state = _intents(stale_bt.plan, frame, reg)
        fresh_intents, _fresh = _intents(SC.compile_graph(edited, reg), frame, reg)
        assert stale_intents == fresh_intents, (
            "the row was executed as something other than its own graph"
        )
