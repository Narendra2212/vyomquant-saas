"""
Task 10.3 - the Live_Runtime's ACTION-node output, wired into ``signal_service``.

Requirements 14.3, 14.5, 14.6, 14.7, 14.8, 15.5. Five things are under test and nothing
else:

  1. THE ADAPTER. One ACTION-node evaluation, in either vocabulary the runtime actually
     produces (``dag_engine.TradeIntent`` and ``dag_event_loop.Signal``), projected onto
     the named keys task 10.1 reads - including the two cases that are decisions rather
     than translations: an exit block that states no side is a ``CLOSE``, and a quantity
     sized as a percentage is a sizing intention rather than a unit count.

  2. THE CLOSURE GATE (Requirement 14.3). A Signal is generated only when the emitting
     action node AND every node in its upstream closure hold ``READY``. Three verdicts,
     not two: ready, not ready, and **no evidence** - which refuses.

  3. THE FEED GATE (Requirement 14.6). Generation is suspended while the feed state is
     not ``LIVE`` under ``feed_state.py``'s own thresholds, the deployment's live-data
     health state is marked when that happens, and generation resumes automatically once
     the feed classifies ``LIVE`` again. An unmeasured feed refuses.

  4. PER-EVENT CONTAINMENT (Requirements 14.7, 14.8, 15.5). One event's failure - a node
     evaluation error, an unpersistable signal, an engine that raised - aborts that event
     only, is recorded against the Deployment, and never propagates.

  5. THE WIRING. ``DAGEventLoop`` routes a plan-bound evaluation's Trade_Intents to the
     path, keeps one ``PlanRuntimeState`` per symbol across events, and refuses to attach
     a signal path to a loop that has no compiled plan (which therefore cannot report
     closure readiness at all).

What is NOT tested here, because it is not this task's:
  * The Signal record's shape, its id and its persistence - task 10.1,
    ``tests/test_task_10_1_generate_signal.py``.
  * The submission route, the transition log and the idempotency guard - task 10.2,
    ``tests/test_task_10_2_submit_signal.py``.
  * ``feed_state.py``'s own boundaries and precedence -
    ``tests/test_feed_state_and_data_quality.py``. This file asserts that the gate
    *consults* that classifier, never that ``1.5 x`` is the right multiple.
  * ``execute_plan``'s readiness state machine - ``tests/test_task_8_4_runtime_readiness_gate.py``.
  * WebSocket frames for these transitions - task 14.2. The crash-recovery sweep - task 12.
  * The universal-quantifier versions of the stale-feed and approved-signal-only claims -
    tasks 10.4 and 10.5 (Properties 7 and 8).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.feed_state import FeedState
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.backend.signal_service import (
    OUTCOME_CLOSURE_NOT_READY,
    OUTCOME_CONTAINED_ERROR,
    OUTCOME_FEED_SUSPENDED,
    OUTCOME_GENERATION_REFUSED,
    OUTCOME_NODE_EVALUATION_FAILED,
    OUTCOME_NOT_PERSISTED,
    OUTCOME_RISK_REFUSED,
    OUTCOME_SUBMITTED,
    ExecutionOutcome,
    LiveSignalPath,
    RiskVerdict,
    SignalSubmissionRefused,
    action_node_output,
    closure_readiness,
)

NOW = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES - the deployment, the runtime's two output shapes, the database
# ══════════════════════════════════════════════════════════════════════════


def deployment_row(**overrides) -> Dict[str, Any]:
    row = {
        "id": "dep-1111",
        "user_id": "user-aaaa",
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "mode": "live",
        "worker_id": "worker-7",
    }
    row.update(overrides)
    return row


@dataclass(frozen=True)
class FakeIntent:
    """``dag_engine.TradeIntent``'s field set, without importing the executor stack.

    Field-for-field what ``build_intent`` returns, including the two shapes that matter
    here: ``side=None`` for an exit block (whose side is the inverse of the open position
    and which the engine refuses to guess) and ``quantity_type`` naming what the quantity
    is measured in.
    """

    node_id: str = "action-1"
    triggered: bool = True
    bar: str = "2024-05-01 11:59:00"
    signal: float = 1.0
    symbol: Optional[str] = "BTC/USDT"
    timeframe: Optional[str] = "1m"
    side: Optional[str] = "buy"
    order_type: Optional[str] = "market"
    order_intent: Optional[str] = "entry"
    reduce_only: bool = False
    quantity_type: Optional[str] = "base_amount"
    quantity: Any = 0.25
    price: Any = None
    trigger_price: Any = None
    limit_price: Any = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class FakePlan:
    """Only the two things this path asks a plan: its warmup and its edges."""

    edges: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    warmup_bars: int = 5
    dag_hash: str = "hash-golden"
    action_nodes: Tuple[str, ...] = ("action-1",)

    def predecessors(self, node_id: str) -> Tuple[str, ...]:
        return tuple(self.edges.get(str(node_id), ()))


@dataclass
class FakeRuntimeState:
    """``PlanRuntimeState``'s one field this gate reads."""

    node_states: Dict[str, str] = field(default_factory=dict)


def a_plan() -> FakePlan:
    """action-1 <- logic-1 <- rsi-1 <- data-1. A closure three hops deep."""
    return FakePlan(
        edges={
            "action-1": ("logic-1",),
            "logic-1": ("rsi-1",),
            "rsi-1": ("data-1",),
            "data-1": (),
        }
    )


def all_ready() -> FakeRuntimeState:
    return FakeRuntimeState(
        node_states={
            "action-1": "READY",
            "logic-1": "READY",
            "rsi-1": "READY",
            "data-1": "READY",
        }
    )


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class FakeQuery:
    def __init__(self, client, table, verb, payload=None):
        self.client = client
        self.table = table
        self.verb = verb
        self.payload = payload
        self.filters: List[Tuple[str, str, Any]] = []

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def limit(self, _n):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return self.client._execute(self)


class FakeSupabase:
    """A PostgREST stand-in that records every call, including the deployment writes.

    Records the VERB per table for the same reason task 10.2's double does: the claims
    under test here are claims about which writes happen and which do not - a refused
    candidate must produce no ``signals`` INSERT at all, and a suspension must produce
    exactly one ``strategy_deployments`` UPDATE however many stale events arrive.
    """

    def __init__(self, *, insert_error: Optional[str] = None):
        self.calls: List[Dict[str, Any]] = []
        self.signals_row: Dict[str, Any] = {}
        self.insert_error = insert_error

    def table(self, name):
        self._table = name
        return self

    def select(self, columns):
        return FakeQuery(self, self._table, "select", columns)

    def insert(self, payload):
        return FakeQuery(self, self._table, "insert", payload)

    def update(self, payload):
        return FakeQuery(self, self._table, "update", payload)

    def delete(self):
        return FakeQuery(self, self._table, "delete", None)

    def _execute(self, query):
        self.calls.append(
            {
                "table": query.table,
                "verb": query.verb,
                "payload": query.payload,
                "filters": list(query.filters),
            }
        )
        if query.table == "signals":
            if query.verb == "select":
                columns = str(query.payload)
                if columns.strip() == "idempotency_key,order_lifecycle_state":
                    return _Result(data=[])  # the 005b probe
                return _Result(data=[dict(self.signals_row)] if self.signals_row else [])
            if query.verb == "insert":
                if self.insert_error:
                    raise Exception(self.insert_error)
                self.signals_row = dict(query.payload)
                return _Result(data=[dict(query.payload)])
            if query.verb == "update":
                self.signals_row.update(query.payload)
                return _Result(data=[dict(self.signals_row)])
        if query.table == "order_lifecycle_transitions":
            if query.verb == "select":
                return _Result(data=[])
            return _Result(data=[dict(query.payload or {})])
        if query.table == "strategy_deployments":
            return _Result(data=[dict(query.payload or {})])
        return _Result(data=[])

    # ── assertion helpers ──
    def writes_on(self, table: str, verb: str) -> List[Any]:
        return [
            call["payload"]
            for call in self.calls
            if call["table"] == table and call["verb"] == verb
        ]

    def deployment_notes(self) -> List[Optional[str]]:
        return [
            payload.get("error_message")
            for payload in self.writes_on("strategy_deployments", "update")
        ]


class FakeRisk:
    def __init__(self, verdict=None, raises=None):
        self.verdict = verdict or RiskVerdict(approved=True, reason="within limits")
        self.raises = raises
        self.calls: List[str] = []

    async def validate_signal(self, signal):
        self.calls.append(signal.id)
        if self.raises is not None:
            raise self.raises
        return self.verdict


class FakeExecution:
    def __init__(self, outcome=None):
        self.outcome = outcome or ExecutionOutcome(
            accepted=True, order_id="ord-1", reason="accepted"
        )
        self.calls: List[str] = []

    async def submit_order(self, signal):
        self.calls.append(signal.id)
        return self.outcome


class FakeIdempotencyLayer:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def execute_with_idempotency(
        self, *, tenant_id, client_order_id, operation, result_ttl=None, **_kw
    ):
        self.calls.append({"tenant_id": tenant_id, "key": client_order_id})
        return await operation()


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


def a_path(sb=None, *, risk=None, execution=None, plan=None, **overrides) -> LiveSignalPath:
    return LiveSignalPath(
        deployment_row(**overrides.pop("deployment", {})),
        risk_engine=risk if risk is not None else FakeRisk(),
        execution_engine=execution if execution is not None else FakeExecution(),
        sb=sb if sb is not None else FakeSupabase(),
        idempotency_layer=FakeIdempotencyLayer(),
        plan=plan if plan is not None else a_plan(),
        clock=lambda: NOW,
        **overrides,
    )


def live_feed(path: LiveSignalPath, *, age_seconds: float = 5.0, bars: int = 50):
    """A feed report this path will classify LIVE: 5s old on a 1m bar, warmup satisfied."""
    return path.observe_feed_state(
        symbol="BTC/USDT",
        connected=True,
        age_seconds=age_seconds,
        available_bars=bars,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE ADAPTER - one evaluation, projected onto what task 10.1 reads
# ══════════════════════════════════════════════════════════════════════════


def test_an_entry_intent_becomes_a_sided_decision_with_its_base_quantity():
    output = action_node_output(FakeIntent())

    assert output["decision"] == "BUY"
    assert output["quantity"] == 0.25
    assert "sizing_intention" not in output
    assert output["source_node_ids"] == ["action-1"]
    assert output["symbol"] == "BTC/USDT"
    assert output["market_context"]["bar_time"] == "2024-05-01 11:59:00"
    assert output["market_context"]["order_intent"] == "entry"


def test_an_exit_intent_with_no_side_becomes_a_close_and_not_a_guess():
    """``action_close_position`` states no side: it is the inverse of the open position."""
    output = action_node_output(
        FakeIntent(side=None, order_intent="exit", reduce_only=True, quantity_type="percent_of_position")
    )

    assert output["decision"] == "CLOSE"
    # And minting leaves `side` NULL rather than inventing one.
    signal = svc.mint_signal(
        deployment_row(), {**output, "closure_ready": True}, now=NOW
    )
    assert signal.decision == "CLOSE"
    assert signal.signal_type == "EXIT"
    assert signal.side is None


def test_a_percentage_quantity_travels_as_a_sizing_intention_not_as_units():
    """A percent of equity written into a units column would be a wrong number."""
    output = action_node_output(
        FakeIntent(quantity_type="percent_of_equity", quantity=2.5)
    )

    assert "quantity" not in output
    assert output["sizing_intention"] == {
        "quantity_type": "percent_of_equity",
        "quantity": 2.5,
        "basis": "declared_by_action_node",
    }

    signal = svc.mint_signal(deployment_row(), {**output, "closure_ready": True}, now=NOW)
    assert signal.quantity is None
    assert signal.sizing_intention["quantity_type"] == "percent_of_equity"


def test_the_streaming_loops_own_signal_shape_is_read_unchanged():
    """``dag_event_loop.Signal``: ``action`` / ``strength`` / ``trigger_node``."""
    from backend_app.backend.dag_event_loop import Signal as LoopSignal

    output = action_node_output(
        LoopSignal(
            timestamp=NOW,
            symbol="ETH/USDT",
            action="sell",
            strength=0.82,
            trigger_node="action-9",
        )
    )

    assert output["decision"] == "SELL"
    assert output["strength"] == 0.82
    assert output["source_node_ids"] == ["action-9"]


def test_the_adapter_invents_no_size_for_an_intent_that_declares_none():
    """No default quantity and no guessed type: the adapter emits neither key."""
    output = action_node_output(FakeIntent(quantity=None, quantity_type=None))
    assert "quantity" not in output and "sizing_intention" not in output

    # 10.1 then derives Requirement 15.2's "sizing intention" from the strength the
    # emitter did state - which is a reconstructible intention, not a fabricated unit
    # count, and is why the record carries no `quantity`.
    signal = svc.mint_signal(deployment_row(), {**output, "closure_ready": True})
    assert signal.quantity is None
    assert signal.sizing_intention == {
        "strength": 1.0,
        "basis": "reported_by_action_node",
    }


def test_a_decision_with_nothing_at_all_about_size_is_refused():
    with pytest.raises(svc.SignalGenerationRefused) as excinfo:
        svc.mint_signal(
            deployment_row(), {"decision": "BUY", "closure_ready": True}
        )
    assert excinfo.value.code == "SIGNAL_SIZING_UNSPECIFIED"


def test_the_adapter_attaches_the_closure_verdict_it_could_determine():
    output = action_node_output(
        FakeIntent(), plan=a_plan(), runtime_state=all_ready()
    )
    assert output["closure_ready"] is True
    assert set(output["node_closure"]) == {"action-1", "logic-1", "rsi-1", "data-1"}


# ══════════════════════════════════════════════════════════════════════════
# 2. THE CLOSURE-READY GATE (Requirement 14.3)
# ══════════════════════════════════════════════════════════════════════════


def test_the_whole_transitive_closure_is_checked_not_just_direct_predecessors():
    verdict = closure_readiness(
        FakeIntent(), plan=a_plan(), runtime_state=all_ready(), action_node_id="action-1"
    )
    assert verdict.ready is True
    assert verdict.source == svc.CLOSURE_SOURCE_PLAN_STATE
    # data-1 is three hops upstream and is still in the closure.
    assert "data-1" in verdict.node_states


def test_one_warming_node_three_hops_upstream_holds_the_action():
    state = all_ready()
    state.node_states["data-1"] = "WARMING"

    verdict = closure_readiness(
        FakeIntent(), plan=a_plan(), runtime_state=state, action_node_id="action-1"
    )
    assert verdict.ready is False
    assert verdict.not_ready == ("data-1",)


def test_an_unreported_node_is_not_a_ready_one():
    """A node absent from the state map has not been reached, which is never ready."""
    state = all_ready()
    del state.node_states["rsi-1"]

    verdict = closure_readiness(
        FakeIntent(), plan=a_plan(), runtime_state=state, action_node_id="action-1"
    )
    assert verdict.ready is False
    assert verdict.node_states["rsi-1"] == "UNREPORTED"


def test_the_emitting_action_node_is_itself_part_of_the_gate():
    state = all_ready()
    state.node_states["action-1"] = "NOT_READY"

    verdict = closure_readiness(
        FakeIntent(), plan=a_plan(), runtime_state=state, action_node_id="action-1"
    )
    assert verdict.ready is False
    assert "action-1" in verdict.not_ready


def test_no_evidence_at_all_is_undetermined_and_never_ready():
    verdict = closure_readiness({"decision": "BUY", "quantity": 1})
    assert verdict.ready is None
    assert verdict.determined is False
    assert verdict.source == svc.CLOSURE_SOURCE_NONE


def test_an_emitter_that_reports_its_own_verdict_is_believed():
    ready = closure_readiness({"decision": "BUY", "closure_ready": True})
    assert (ready.ready, ready.source) == (True, svc.CLOSURE_SOURCE_REPORTED_FLAG)

    blocked = closure_readiness({"decision": "BUY", "closure_ready": False})
    assert blocked.ready is False


def test_bare_indicator_readings_are_not_a_readiness_claim():
    """A number is a value, not a verdict; a closure of numbers determines nothing."""
    verdict = closure_readiness({"decision": "BUY", "node_closure": {"rsi-1": 28.4}})
    assert verdict.ready is None


def test_per_node_readings_that_state_readiness_are_evidence():
    verdict = closure_readiness(
        {
            "decision": "BUY",
            "node_closure": {
                "rsi-1": {"value": 28.4, "ready": True},
                "logic-1": {"value": 1.0, "status": "READY"},
            },
        }
    )
    assert verdict.ready is True

    blocked = closure_readiness(
        {
            "decision": "BUY",
            "node_closure": {
                "rsi-1": {"value": 28.4, "ready": True},
                "logic-1": {"value": 0.0, "ready": False},
            },
        }
    )
    assert blocked.ready is False
    assert blocked.not_ready == ("logic-1",)


@pytest.mark.asyncio
async def test_a_not_ready_closure_generates_nothing_and_writes_no_signal_row():
    sb = FakeSupabase()
    path = a_path(sb)
    state = all_ready()
    state.node_states["rsi-1"] = "WARMING"

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=state, feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_CLOSURE_NOT_READY
    assert outcome.code == "CLOSURE_NOT_READY"
    assert outcome.signal is None
    assert sb.writes_on("signals", "insert") == []
    assert path.counters["suppressed_closure"] == 1


@pytest.mark.asyncio
async def test_an_undeterminable_closure_refuses_rather_than_assuming_ready():
    sb = FakeSupabase()
    path = a_path(sb)

    outcome = await path.on_action_output(
        {"decision": "BUY", "quantity": 1.0}, feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_CLOSURE_NOT_READY
    assert outcome.code == "CLOSURE_READINESS_UNDETERMINED"
    assert sb.writes_on("signals", "insert") == []


# ══════════════════════════════════════════════════════════════════════════
# 3. THE FEED GATE (Requirement 14.6)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_fresh_feed_permits_generation_and_marks_nothing():
    sb = FakeSupabase()
    path = a_path(sb)

    assert await path.apply_feed_state(live_feed(path)) is True
    assert path.suspended is False
    assert sb.deployment_notes() == []


@pytest.mark.asyncio
async def test_a_stale_feed_suspends_generation_and_marks_the_deployment():
    """3 x the 1m interval is ``feed_state``'s own STALE boundary; the threshold is its."""
    sb = FakeSupabase()
    path = a_path(sb)

    report = path.observe_feed_state(
        symbol="BTC/USDT", connected=True, age_seconds=200.0, available_bars=50
    )
    assert report.state is FeedState.STALE

    assert await path.apply_feed_state(report) is False
    assert path.suspended is True
    assert path.counters["feed_suspensions"] == 1

    (note,) = sb.deployment_notes()
    assert note.startswith(f"{svc.DEPLOYMENT_HEALTH_PREFIX}:FEED_NOT_LIVE")
    assert "STALE" in note
    update = sb.calls[-1]
    assert ("eq", "id", "dep-1111") in update["filters"]
    assert ("eq", "user_id", "user-aaaa") in update["filters"]


@pytest.mark.asyncio
async def test_a_gapped_feed_short_of_warmup_also_suspends():
    """A deployment that has just started reads INSUFFICIENT_DATA, which is not LIVE."""
    sb = FakeSupabase()
    path = a_path(sb, plan=FakePlan(warmup_bars=200))

    report = path.observe_feed_state(
        symbol="BTC/USDT", connected=True, age_seconds=5.0, available_bars=12
    )
    assert report.state is FeedState.INSUFFICIENT_DATA
    assert await path.apply_feed_state(report) is False


@pytest.mark.asyncio
async def test_a_disconnected_feed_suspends():
    path = a_path()
    report = path.observe_feed_state(symbol="BTC/USDT", connected=False, age_seconds=1.0)
    assert report.state is FeedState.DISCONNECTED
    assert await path.apply_feed_state(report) is False


@pytest.mark.asyncio
async def test_a_delayed_feed_suspends_too():
    """1.5 x the interval is DELAYED, and DELAYED is not LIVE (Requirement 14.6)."""
    path = a_path()
    report = path.observe_feed_state(
        symbol="BTC/USDT", connected=True, age_seconds=95.0, available_bars=50
    )
    assert report.state is FeedState.DELAYED
    assert await path.apply_feed_state(report) is False


@pytest.mark.asyncio
async def test_suspension_writes_the_deployment_once_however_many_stale_events_arrive():
    """A per-event UPDATE would be one write per bar for a whole outage, saying nothing new."""
    sb = FakeSupabase()
    path = a_path(sb)

    for _ in range(5):
        await path.apply_feed_state(
            path.observe_feed_state(
                symbol="BTC/USDT", connected=True, age_seconds=400.0, available_bars=50
            )
        )

    assert len(sb.deployment_notes()) == 1
    assert path.counters["feed_suspensions"] == 1


@pytest.mark.asyncio
async def test_generation_resumes_automatically_and_clears_only_its_own_note():
    sb = FakeSupabase()
    path = a_path(sb)

    await path.apply_feed_state(
        path.observe_feed_state(
            symbol="BTC/USDT", connected=True, age_seconds=400.0, available_bars=50
        )
    )
    assert path.suspended is True

    # No operator action, no restart: the next LIVE reading resumes it.
    assert await path.apply_feed_state(live_feed(path)) is True
    assert path.suspended is False
    assert path.counters["feed_resumes"] == 1

    notes = sb.deployment_notes()
    assert len(notes) == 2
    assert notes[0].startswith(f"{svc.DEPLOYMENT_HEALTH_PREFIX}:FEED_NOT_LIVE")
    assert notes[1] is None  # its own note, cleared


@pytest.mark.asyncio
async def test_a_resume_leaves_a_note_this_process_did_not_write_alone():
    """Clearing unconditionally would erase a startup failure reason (Requirement 11.4)."""
    sb = FakeSupabase()
    path = a_path(sb)

    # Never suspended by this object, so it has no note of its own to clear.
    assert await path.apply_feed_state(live_feed(path)) is True
    assert sb.writes_on("strategy_deployments", "update") == []


@pytest.mark.asyncio
async def test_no_signal_is_generated_while_the_feed_is_suspended():
    sb = FakeSupabase()
    path = a_path(sb)

    stale = path.observe_feed_state(
        symbol="BTC/USDT", connected=True, age_seconds=400.0, available_bars=50
    )
    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=stale
    )

    assert outcome.status == OUTCOME_FEED_SUSPENDED
    assert outcome.signal is None
    assert sb.writes_on("signals", "insert") == []


@pytest.mark.asyncio
async def test_an_unmeasured_feed_refuses_rather_than_assuming_live():
    """``feed_state``'s own disposition, carried into the decision it informs."""
    sb = FakeSupabase()
    path = a_path(sb)

    outcome = await path.on_action_output(FakeIntent(), runtime_state=all_ready())

    assert outcome.status == OUTCOME_FEED_SUSPENDED
    assert outcome.code == "FEED_STATE_UNMEASURED"
    assert sb.writes_on("signals", "insert") == []


def test_the_feed_gate_state_is_feed_states_own_live_and_not_a_second_threshold():
    assert svc.FEED_GATE_STATE is FeedState.LIVE


# ══════════════════════════════════════════════════════════════════════════
# 4. THE ROUTE, WHEN EVERY GATE PASSES (Requirements 14.3, 14.5, 11.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_ready_closure_on_a_live_feed_generates_persists_and_submits():
    sb = FakeSupabase()
    risk, execution = FakeRisk(), FakeExecution()
    path = a_path(sb, risk=risk, execution=execution)

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_SUBMITTED
    assert outcome.generated is True
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED

    # persisted BEFORE it was routed (Requirements 14.5, 15.5)
    (row,) = sb.writes_on("signals", "insert")
    assert row["decision"] == "BUY"
    assert row["order_lifecycle_state"] == "GENERATED"
    assert row["idempotency_key"] == f"signal:{outcome.signal.id}"
    # the closure the decision was made on is the decision metadata (Requirement 15.2)
    assert set(row["indicators"]) == {"action-1", "logic-1", "rsi-1", "data-1"}
    assert row["market_info"]["closure_ready"] is True
    # and the risk verdict this path obtained before minting
    assert row["risk_passed"] is True
    assert row["risk_reason"] == "within limits"
    assert row["risk_evaluated_at"] == NOW.isoformat()

    assert execution.calls == [outcome.signal.id]
    assert path.counters == {
        **path.counters,
        "signals_generated": 1,
        "signals_submitted": 1,
    }


@pytest.mark.asyncio
async def test_one_decision_consumes_one_identifier_across_the_two_mints():
    """The candidate risk validation saw and the persisted row are the same signal."""
    sb = FakeSupabase()
    risk = FakeRisk()
    path = a_path(sb, risk=risk)

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    (row,) = sb.writes_on("signals", "insert")
    assert row["id"] == outcome.signal.id
    # validate_signal ran twice - once on the candidate here, once under the submission
    # lock in task 10.2 - and both times on the same signal id.
    assert risk.calls == [outcome.signal.id, outcome.signal.id]


@pytest.mark.asyncio
async def test_a_risk_reduced_size_is_recorded_without_rewriting_the_request():
    sb = FakeSupabase()
    path = a_path(
        sb,
        risk=FakeRisk(
            RiskVerdict(approved=True, reason="reduced", adjusted_quantity=0.1)
        ),
    )

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    (row,) = sb.writes_on("signals", "insert")
    assert row["quantity"] == 0.25  # what the strategy asked for
    assert row["position_size"] == 0.1  # what risk validation approved
    assert outcome.status == OUTCOME_SUBMITTED


@pytest.mark.asyncio
async def test_a_risk_refusal_generates_no_signal_and_records_against_the_deployment():
    """Requirement 14.3: no Signal. The refusal belongs on the deployment, not on a row."""
    sb = FakeSupabase()
    execution = FakeExecution()
    path = a_path(
        sb,
        risk=FakeRisk(RiskVerdict(approved=False, reason="daily loss limit reached")),
        execution=execution,
    )

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_RISK_REFUSED
    assert outcome.signal is None
    assert sb.writes_on("signals", "insert") == []
    assert execution.calls == []
    (note,) = sb.deployment_notes()
    assert "daily loss limit reached" in note
    assert path.counters["refused_risk"] == 1


@pytest.mark.asyncio
async def test_a_hold_is_not_a_signal_and_is_not_a_failure():
    sb = FakeSupabase()
    path = a_path(sb)

    outcome = await path.on_action_output(
        {"decision": "HOLD", "quantity": 1.0, "closure_ready": True},
        feed=live_feed(path),
    )

    assert outcome.status == OUTCOME_GENERATION_REFUSED
    assert outcome.code == "SIGNAL_DECISION_NOT_ACTIONABLE"
    assert sb.writes_on("signals", "insert") == []
    # Not recorded against the deployment: a HOLD is a strategy saying no, not a fault.
    assert sb.deployment_notes() == []


@pytest.mark.asyncio
async def test_a_missing_risk_or_execution_component_is_refused_at_wiring_time():
    """Requirement 11.2: an absent validator is never an approval, and it fails early."""
    for missing in ("risk_engine", "execution_engine"):
        kwargs = {"risk_engine": FakeRisk(), "execution_engine": FakeExecution()}
        kwargs[missing] = None
        with pytest.raises(SignalSubmissionRefused):
            LiveSignalPath(deployment_row(), **kwargs)


# ══════════════════════════════════════════════════════════════════════════
# 5. PER-EVENT CONTAINMENT (Requirements 14.7, 14.8, 15.5)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unpersistable_signal_is_not_routed_and_is_recorded():
    """Requirement 15.5: not reported, not routed to risk validation or order submission."""
    sb = FakeSupabase(insert_error="500 could not write to public.signals")
    execution = FakeExecution()
    path = a_path(sb, execution=execution)

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_NOT_PERSISTED
    assert outcome.signal is None
    assert execution.calls == []
    assert path.counters["not_persisted"] == 1
    assert any("could not be persisted" in (note or "") for note in sb.deployment_notes())


@pytest.mark.asyncio
async def test_the_next_event_still_runs_after_one_that_could_not_be_persisted():
    """Requirement 14.7's containment, as the sequence it is actually about."""
    sb = FakeSupabase(insert_error="500 could not write to public.signals")
    path = a_path(sb)

    first = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )
    assert first.status == OUTCOME_NOT_PERSISTED

    sb.insert_error = None
    second = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )
    assert second.status == OUTCOME_SUBMITTED


@pytest.mark.asyncio
async def test_a_raising_risk_component_is_contained_and_recorded():
    sb = FakeSupabase()
    path = a_path(sb, risk=FakeRisk(raises=RuntimeError("risk service unreachable")))

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )

    assert outcome.status == OUTCOME_CONTAINED_ERROR
    assert "risk service unreachable" in outcome.reason
    assert sb.writes_on("signals", "insert") == []
    assert any("risk service unreachable" in (n or "") for n in sb.deployment_notes())


@pytest.mark.asyncio
async def test_a_node_evaluation_error_aborts_one_event_and_is_recorded():
    sb = FakeSupabase()
    path = a_path(sb)

    outcome = await path.record_evaluation_failure(
        RuntimeError("indicator rsi-1 raised"), symbol="BTC/USDT", event_time=NOW
    )

    assert outcome.status == OUTCOME_NODE_EVALUATION_FAILED
    assert outcome.signal is None
    (note,) = sb.deployment_notes()
    assert note.startswith(f"{svc.DEPLOYMENT_HEALTH_PREFIX}:NODE_EVALUATION_FAILED")
    assert "indicator rsi-1 raised" in note
    assert path.counters["evaluation_failures"] == 1


@pytest.mark.asyncio
async def test_one_actions_failure_does_not_skip_the_other_actions_of_the_same_bar():
    """A plan may hold several ACTION nodes; containment is per event, not per bar."""
    sb = FakeSupabase()
    path = a_path(sb)

    outcomes = await path.on_action_outputs(
        [
            {"decision": "HOLD", "quantity": 1.0, "closure_ready": True},
            action_node_output(FakeIntent(node_id="action-2"), plan=a_plan()),
        ],
        runtime_state=FakeRuntimeState(
            node_states={"action-2": "READY", **all_ready().node_states}
        ),
        plan=FakePlan(edges={"action-2": ("logic-1",), "logic-1": ("rsi-1",), "rsi-1": ()}),
        feed=live_feed(path),
    )

    assert [o.status for o in outcomes] == [OUTCOME_GENERATION_REFUSED, OUTCOME_SUBMITTED]


@pytest.mark.asyncio
async def test_the_feed_verdict_is_applied_once_per_event_not_once_per_action():
    sb = FakeSupabase()
    path = a_path(sb)
    stale = path.observe_feed_state(
        symbol="BTC/USDT", connected=True, age_seconds=400.0, available_bars=50
    )

    outcomes = await path.on_action_outputs(
        [FakeIntent(), FakeIntent(node_id="action-2")],
        runtime_state=all_ready(),
        feed=stale,
    )

    assert [o.status for o in outcomes] == [OUTCOME_FEED_SUSPENDED] * 2
    assert path.counters["feed_suspensions"] == 1
    assert len(sb.deployment_notes()) == 1


@pytest.mark.asyncio
async def test_a_recorder_that_cannot_write_never_becomes_the_reason_an_event_dies():
    """The containment must not depend on the thing that records the containment."""

    class BrokenSupabase(FakeSupabase):
        def _execute(self, query):
            if query.table == "strategy_deployments":
                raise Exception("deployment row unreachable")
            return super()._execute(query)

    sb = BrokenSupabase()
    path = a_path(sb, risk=FakeRisk(RiskVerdict(approved=False, reason="blocked")))

    outcome = await path.on_action_output(
        FakeIntent(), runtime_state=all_ready(), feed=live_feed(path)
    )
    assert outcome.status == OUTCOME_RISK_REFUSED

    assert (
        await svc.record_deployment_failure(sb, deployment_row(), "anything") is False
    )
    assert await svc.record_deployment_failure(None, deployment_row(), "no client") is False


def test_the_outcome_wire_form_carries_no_credential_field():
    """Structural, as everywhere else on this path: there is no field to omit."""
    outcome = svc.SignalPathOutcome(status=OUTCOME_FEED_SUSPENDED, reason="stale")
    payload = repr(outcome.to_dict()).lower()
    for forbidden in ("api_key", "secret", "passphrase", "access_token", "private_key"):
        assert forbidden not in payload


# ══════════════════════════════════════════════════════════════════════════
# 6. THE WIRING - DAGEventLoop -> LiveSignalPath
# ══════════════════════════════════════════════════════════════════════════


def _redis_singleton():
    """The ``SharedRedisManager`` instance ``_acquire_dag_lock`` reaches.

    Resolved out of ``sys.modules`` rather than by attribute access on the package,
    because ``backend_app.core.cache.__init__`` re-exports the singleton under the same
    name as the submodule - so ``from backend_app.core.cache import redis_manager`` hands
    back the object, not the module, and patching "its" ``redis_manager`` attribute
    silently targets the wrong thing.
    """
    import sys

    import backend_app.core.cache.redis_manager  # noqa: F401 - ensures it is imported

    return sys.modules["backend_app.core.cache.redis_manager"].redis_manager


class FakeEngine:
    """A DAGEngine stand-in that records what ``execute_plan`` was asked."""

    def __init__(self, intents=None, raises=None):
        self.intents = intents if intents is not None else []
        self.raises = raises
        self.node_results: Dict[str, Any] = {}
        self.calls: List[Dict[str, Any]] = []

    def execute_plan(self, plan, window, state, *, registry=None, **_kw):
        self.calls.append(
            {"plan": plan, "bars": len(window.index), "state": state, "registry": registry}
        )
        if self.raises is not None:
            raise self.raises
        # What the real engine does: mark the plan's nodes and return intents.
        for node_id in ("data-1", "rsi-1", "logic-1", "action-1"):
            state.node_states[node_id] = "READY"
        return list(self.intents)


def a_loop(symbols=("BTC/USDT",), timeframe="1m"):
    from backend_app.backend.dag_event_loop import DAGEventLoop

    return DAGEventLoop(
        dag_nodes=[{"id": "n1", "type": "indicator", "indicator": "rsi"}],
        dag_edges=[],
        symbols=list(symbols),
        timeframe=timeframe,
        tenant_id="tenant-1",
    )


def fill_window(loop, symbol="BTC/USDT", bars=30, start=None):
    """``bars`` closed 1m bars ending one minute before :data:`NOW`, unless told otherwise.

    Ending one minute back is what makes the default window read ``LIVE``: the feed gate
    measures the newest ADMITTED bar's age against the 1m interval, and 60s is inside
    ``feed_state``'s ``1.5 x`` boundary.
    """
    if start is None:
        start = NOW - timedelta(minutes=bars)
    window = loop.rolling_windows[symbol]
    for index in range(bars):
        moment = pd.Timestamp(start.replace(tzinfo=None)) + pd.Timedelta(minutes=index)
        window.append_bar(moment, 100.0, 101.0, 99.0, 100.5 + index, 10.0)
    return window


def a_candle_event(symbol="BTC/USDT", minutes_ago=1):
    from backend_app.backend.dag_event_loop import EventType, MarketEvent

    moment = NOW - timedelta(minutes=minutes_ago)
    return MarketEvent(
        event_type=EventType.CANDLE,
        symbol=symbol,
        timestamp=moment,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        timeframe="1m",
    )


def test_a_loop_with_no_compiled_plan_refuses_a_signal_path():
    """``execute_dag`` cannot report closure readiness, so it may not feed a signal path."""
    loop = a_loop()
    with pytest.raises(ValueError) as excinfo:
        loop.attach_signal_path(a_path())
    assert "READY" in str(excinfo.value)


def test_detaching_is_explicit_rather_than_passing_none():
    loop = a_loop()
    loop.plan = a_plan()
    path = a_path()
    loop.attach_signal_path(path)
    assert loop.signal_path is path

    with pytest.raises(ValueError):
        loop.attach_signal_path(None)

    loop.detach_signal_path()
    assert loop.signal_path is None


@pytest.mark.asyncio
async def test_the_loop_routes_its_plan_evaluations_intents_to_the_signal_path():
    sb = FakeSupabase()
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine(intents=[FakeIntent()])
    loop.dag_engines["BTC/USDT"] = engine
    path = a_path(sb, plan=a_plan())
    loop.attach_signal_path(path)
    fill_window(loop)

    outcomes = await loop._route_deployment_signals(
        "BTC/USDT", engine, loop.rolling_windows["BTC/USDT"].to_dataframe(),
        a_candle_event(),
    )

    assert [o.status for o in outcomes] == [OUTCOME_SUBMITTED]
    (row,) = sb.writes_on("signals", "insert")
    assert row["decision"] == "BUY"
    # The market context is the BAR the plan was evaluated on, not the event that
    # arrived: after task 7.10 those are different things, and the audit answer to "what
    # did this decision see" is the closed bar the executors read.
    assert row["market_info"]["price"] == pytest.approx(129.5)  # the newest close
    assert row["market_info"]["bar_time"] == str(
        loop.rolling_windows["BTC/USDT"].timestamps[-1]
    )
    assert row["market_info"]["reported"]["bars_in_window"] == 30


@pytest.mark.asyncio
async def test_the_loop_keeps_one_runtime_state_per_symbol_across_events():
    """``execute_plan``'s own contract: warmup and readiness accumulate, they do not reset."""
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine()
    loop.dag_engines["BTC/USDT"] = engine
    loop.attach_signal_path(a_path(plan=a_plan()))
    fill_window(loop)
    df = loop.rolling_windows["BTC/USDT"].to_dataframe()

    await loop._route_deployment_signals("BTC/USDT", engine, df, a_candle_event())
    await loop._route_deployment_signals("BTC/USDT", engine, df, a_candle_event())

    assert engine.calls[0]["state"] is engine.calls[1]["state"]
    assert engine.calls[0]["state"] is loop.plan_runtime_states["BTC/USDT"]


@pytest.mark.asyncio
async def test_a_plan_evaluation_that_raises_is_contained_and_the_next_event_runs():
    """Requirement 14.7, at the seam the error actually comes out of."""
    sb = FakeSupabase()
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine(raises=RuntimeError("indicator rsi-1 raised"))
    loop.dag_engines["BTC/USDT"] = engine
    path = a_path(sb, plan=a_plan())
    loop.attach_signal_path(path)
    fill_window(loop)
    df = loop.rolling_windows["BTC/USDT"].to_dataframe()

    first = await loop._route_deployment_signals(
        "BTC/USDT", engine, df, a_candle_event()
    )
    assert [o.status for o in first] == [OUTCOME_NODE_EVALUATION_FAILED]
    assert any("indicator rsi-1 raised" in (n or "") for n in sb.deployment_notes())

    engine.raises = None
    engine.intents = [FakeIntent()]
    second = await loop._route_deployment_signals(
        "BTC/USDT", engine, df, a_candle_event()
    )
    assert [o.status for o in second] == [OUTCOME_SUBMITTED]


@pytest.mark.asyncio
async def test_a_suspended_feed_still_evaluates_but_generates_nothing():
    """Readiness stays observable during an outage; what is suspended is generation."""
    sb = FakeSupabase()
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine(intents=[FakeIntent()])
    loop.dag_engines["BTC/USDT"] = engine
    loop.attach_signal_path(a_path(sb, plan=a_plan()))
    # A window whose newest bar is an hour old on a 1m timeframe: STALE by any measure.
    fill_window(loop, bars=30, start=NOW - timedelta(minutes=90))
    assert loop.rolling_windows["BTC/USDT"].timestamps[-1] < pd.Timestamp(
        NOW.replace(tzinfo=None)
    ) - pd.Timedelta(minutes=59)

    outcomes = await loop._route_deployment_signals(
        "BTC/USDT", engine, loop.rolling_windows["BTC/USDT"].to_dataframe(),
        a_candle_event(minutes_ago=61),
    )

    assert [o.status for o in outcomes] == [OUTCOME_FEED_SUSPENDED]
    assert len(engine.calls) == 1  # it WAS evaluated
    assert sb.writes_on("signals", "insert") == []


@pytest.mark.asyncio
async def test_process_event_dispatches_to_the_signal_path_and_not_to_the_legacy_emission(
    monkeypatch,
):
    """Requirement 11.2: one evaluation per event, not two."""

    class FakeRedis:
        async def set(self, *_a, **_k):
            return True

    async def _client():
        return FakeRedis()

    monkeypatch.setattr(_redis_singleton(), "get_client", _client)

    sb = FakeSupabase()
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine(intents=[FakeIntent()])
    loop.dag_engines["BTC/USDT"] = engine
    loop.attach_signal_path(a_path(sb, plan=a_plan()))
    fill_window(loop, bars=30, start=NOW - timedelta(minutes=40))

    emitted: List[Any] = []
    loop.add_signal_callback(emitted.append)

    await loop._process_event(a_candle_event(minutes_ago=0))

    assert len(engine.calls) == 1
    assert emitted == []  # the legacy path did not also fire
    assert len(sb.writes_on("signals", "insert")) == 1
    assert loop.events_processed == 1
    assert loop.signals_emitted == 1


@pytest.mark.asyncio
async def test_an_unreachable_dag_lock_leaves_the_event_unevaluated(monkeypatch):
    async def _broken():
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(_redis_singleton(), "get_client", _broken)

    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine(intents=[FakeIntent()])
    loop.dag_engines["BTC/USDT"] = engine
    loop.attach_signal_path(a_path(plan=a_plan()))
    fill_window(loop)

    assert (
        await loop._process_event_for_deployment(
            "BTC/USDT", loop.rolling_windows["BTC/USDT"], a_candle_event()
        )
        == []
    )
    assert engine.calls == []


def test_a_loop_with_no_signal_path_attached_is_unchanged():
    """Every existing caller must see the loop it had before task 10.3."""
    loop = a_loop()
    assert loop.signal_path is None
    assert loop.plan_runtime_states == {}
    assert loop.model_versions == {}
    assert "signal_path" not in loop.get_stats()


@pytest.mark.asyncio
async def test_the_loops_stats_republish_the_paths_suspension_and_node_readiness():
    loop = a_loop()
    loop.plan = a_plan()
    engine = FakeEngine()
    loop.dag_engines["BTC/USDT"] = engine
    path = a_path(plan=a_plan())
    loop.attach_signal_path(path)
    fill_window(loop, bars=30, start=NOW - timedelta(minutes=90))

    await loop._route_deployment_signals(
        "BTC/USDT", engine, loop.rolling_windows["BTC/USDT"].to_dataframe(),
        a_candle_event(minutes_ago=61),
    )

    stats = loop.get_stats()
    assert stats["signal_path"]["suspended"] is True
    assert stats["signal_path"]["feed_state"]["state"] == "STALE"
    assert stats["node_runtime_states"]["BTC/USDT"]["nodes"]["action-1"]["state"] == "READY"
