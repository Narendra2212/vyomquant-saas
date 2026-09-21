"""
tests/test_task_13_2_signal_trace_detail.py

Task 13.2 - ``GET /api/signal-trace/signals/{signal_id}``.

Requirement 17.6 (the full trace: DAG node trace, ML inference where applicable, risk
validation, execution outcome), Requirement 16.7 (the transition history, chronological),
Requirements 20.1/20.2 (a non-owner gets the missing-resource response) and 20.3 (no
credential or exchange identity on the wire).

Six things are under test and nothing else:

  1. ROUTE IDENTITY, against the REAL ``backend_app.main.app``. There is exactly ONE
     handler for this path (13.2 extended the pre-spec one rather than registering a
     second, which would be unreachable), and extending it did not shadow
     ``/signals/export`` - task 13.1's structural guard still has to hold.

  2. THE JOIN. The persisted row is the driving side; ``signal_trace_engine``'s record
     supplies the four sections of Requirement 17.6 when it has one, and each section
     names the source it came from.

  3. THE DEGRADED JOIN. A signal with no engine record - the common case, since that
     store keeps an hour and is per-process - still answers with a DAG node trace, a risk
     verdict and an execution outcome read from the row's own columns.

  4. REQUIREMENT 16.7's HISTORY. Read from ``order_lifecycle_transitions``, ordered by
     ``occurred_at``, owner-scoped; absent table degrades rather than erroring.

  5. OWNERSHIP. A non-owner's request is indistinguishable from a nonexistent
     identifier's (Requirements 20.1, 20.2).

  6. THE 005b DEGRADATION and CONTAINMENT. No 500 when ``order_lifecycle_state`` is
     absent, the warning names the migration, ``degraded`` names the three canonical
     states the legacy vocabulary cannot express, and nothing on the wire is a credential
     or a free-form engine field (Requirement 20.3).

What is NOT tested here: the list's filters and pagination (task 13.1, already covered)
and the export body (task 13.3).
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from starlette.routing import Match

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.backend.signal_service import (
    LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING,
    SIGNAL_LIFECYCLE_MIGRATION,
    TRACE_SOURCE_ENGINE,
    TRACE_SOURCE_ROW,
    SignalService,
    mint_signal,
)
from backend_app.backend.signal_trace_engine import (
    DAGNodeTrace,
    ExecutionTrace,
    MLInferenceTrace,
    NodeIO,
    NodeType,
    RiskValidationTrace,
    SignalTraceRecord,
    TraceStatus,
    ValidationResult,
)

OWNER = {"id": "user-aaaa", "access_token": "token-aaaa"}
INTRUDER = {"id": "user-zzzz", "access_token": "token-zzzz"}

SIGNAL_ID = "sig-1111"


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


@dataclass
class _Call:
    table: str
    verb: str
    columns: str


class FakeQuery:
    """Applies the predicates it is given, per table, so an ownership claim is a real one."""

    def __init__(self, client, table, columns):
        self.client = client
        self.table_name = table
        self.columns = columns
        self.predicates = []
        self._limit = None
        self._order = None

    def eq(self, column, value):
        self.predicates.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self.predicates.append(("is", column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        self.client.calls.append(
            _Call(table=self.table_name, verb="select", columns=self.columns)
        )
        self.client.queries.append(self)

        if self.table_name == "signals":
            if not self.client.lifecycle_columns and "order_lifecycle_state" in self.columns:
                raise Exception(
                    "ERROR: 42703: column signals.order_lifecycle_state does not exist"
                )
            rows = [self._project(r) for r in self.client.rows if self._matches(r)]
            return _Result(data=rows)

        if self.table_name == svc.STRATEGY_OWNER_TABLE:
            # Task 29.4 resolves the viewer's role by comparing the authenticated identity
            # with ``strategies.user_id`` for the signal's ``strategy_id``. Answered here so
            # this module's premise is stated rather than assumed: the caller in these tests
            # OWNS ``strat-1``, which is why they are entitled to the full trace below.
            # ``self.client.strategies`` is what a test varies to make the caller a non-owner.
            rows = [r for r in self.client.strategies if self._matches(r)]
            return _Result(data=[dict(r) for r in rows])

        if self.table_name == svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE:
            if not self.client.transitions_table:
                raise Exception(
                    "PGRST205 Could not find the table "
                    "'public.order_lifecycle_transitions' in the schema cache"
                )
            rows = [r for r in self.client.transitions if self._matches(r)]
            if self._order is not None:
                # Deliberately returned in REVERSE of the requested order, so the
                # chronological claim is a claim about the ANSWER and not about the hint.
                rows = sorted(
                    rows, key=lambda r: r.get(self._order[0]) or "", reverse=True
                )
            return _Result(data=[dict(r) for r in rows])

        raise AssertionError(f"unexpected table {self.table_name}")

    def _project(self, row):
        if self.client.lifecycle_columns:
            return dict(row)
        return {k: v for k, v in row.items() if k != "order_lifecycle_state"}

    def _matches(self, row):
        for kind, column, value in self.predicates:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "is" and actual is not None:
                return False
        return True


class FakeSupabase:
    def __init__(
        self,
        rows,
        *,
        transitions=(),
        lifecycle_columns=True,
        transitions_table=True,
        strategies=None,
    ):
        self.rows = list(rows)
        self.transitions = list(transitions)
        # The strategy the signals below belong to, owned by the caller. See FakeQuery's
        # ``strategies`` branch: task 29.4 reads this to decide the viewer's role.
        self.strategies = (
            [{"id": "strat-1", "user_id": OWNER["id"]}]
            if strategies is None
            else list(strategies)
        )
        self.lifecycle_columns = lifecycle_columns
        self.transitions_table = transitions_table
        self.calls = []
        self.queries = []
        self._table = None

    def table(self, name):
        self._table = name
        return self

    def select(self, columns):
        return FakeQuery(self, self._table, columns)


def service_for(rows, **kwargs):
    service = SignalService()
    client = FakeSupabase(rows, **kwargs)

    async def _client(_user):
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


def signal_row(
    signal_id=SIGNAL_ID,
    *,
    user_id=OWNER["id"],
    strategy_id="strat-1",
    state="EXECUTED",
    status="executed",
    ml_info=None,
):
    """One ``public.signals`` row, in the shape ``Signal.to_row`` writes."""
    return {
        "id": signal_id,
        "user_id": user_id,
        "strategy_id": strategy_id,
        "strategy_version": "v3",
        "deployment_id": "dep-1",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "worker_id": "worker-7",
        "decision": "BUY",
        "status": status,
        "order_lifecycle_state": state,
        "quantity": 0.25,
        "generated_at": "2024-05-01T12:00:00+00:00",
        "indicators": {"rsi-1": 28.4, "ema-2": 61000.0},
        "market_info": {
            "signal_type": "ENTRY",
            "side": "BUY",
            "mode": "paper",
            "strategy_version_id": "ver-1",
            "exchange_account_id": "acct-1",
            "source_node_ids": ["action-1"],
            "closure_ready": True,
            "sizing_intention": None,
            "price": 61234.5,
        },
        "ml_info": ml_info,
        "risk_passed": True,
        "risk_reason": "within limits",
        "position_size": 0.25,
        "capital": 10000.0,
        "exposure": 12.5,
        "expected_loss": 50.0,
        "expected_reward": 150.0,
        "drawdown_check": True,
        "risk_evaluated_at": "2024-05-01T12:00:01+00:00",
        "order_id": "ord-1",
        "exchange_order_id": "xch-1",
        "order_status": "FILLED",
        "filled": 0.25,
        "remaining": 0.0,
        "average_price": 61000.0,
        "fees": 1.23,
        "slippage": 0.02,
        "latency_ms": 84.0,
        "trade_id": "trd-1",
        "pnl": 42.0,
        "realized_pnl": 42.0,
        "order_updated_at": "2024-05-01T12:00:03+00:00",
        "executed_at": "2024-05-01T12:00:04+00:00",
    }


def transition_rows():
    """Requirement 16.7's history for ``SIGNAL_ID``, in the order it happened."""
    return [
        {
            "signal_id": SIGNAL_ID,
            "user_id": OWNER["id"],
            "from_state": None,
            "to_state": "GENERATED",
            "reason": "signal generated",
            "occurred_at": "2024-05-01T12:00:00+00:00",
        },
        {
            "signal_id": SIGNAL_ID,
            "user_id": OWNER["id"],
            "from_state": "GENERATED",
            "to_state": "PENDING",
            "reason": "risk approved",
            "occurred_at": "2024-05-01T12:00:01+00:00",
        },
        {
            "signal_id": SIGNAL_ID,
            "user_id": OWNER["id"],
            "from_state": "PENDING",
            "to_state": "SUBMITTED",
            "reason": "submitted to venue",
            "occurred_at": "2024-05-01T12:00:02+00:00",
        },
        {
            "signal_id": SIGNAL_ID,
            "user_id": OWNER["id"],
            "from_state": "SUBMITTED",
            "to_state": "EXECUTED",
            "reason": "filled",
            "occurred_at": "2024-05-01T12:00:04+00:00",
        },
        # Another user's row for another signal. Present so the owner predicate has
        # something to exclude.
        {
            "signal_id": "sig-other",
            "user_id": INTRUDER["id"],
            "from_state": None,
            "to_state": "GENERATED",
            "reason": "not yours",
            "occurred_at": "2024-05-01T12:00:00+00:00",
        },
    ]


def engine_record(*, signal_id=SIGNAL_ID, strategy_id="strat-1", with_ml=True):
    """A ``SignalTraceRecord`` as ``signal_trace_engine`` builds one, fully populated."""
    now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    record = SignalTraceRecord(
        trace_id="trace-9999",
        signal_id=signal_id,
        strategy_id=strategy_id,
        strategy_name="Momentum",
        bot_id="dep-1",
        symbol="BTC/USDT",
        exchange="kraken",
        created_at=now,
        started_at=now,
        status=TraceStatus.COMPLETED,
        final_decision="EXECUTE",
        total_latency_ms=137.5,
        # A free-form field the projection must NOT put on the wire (Requirement 20.3).
        metadata={"api_secret": "s3cr3t-shhh"},
    )

    node = DAGNodeTrace(
        node_id="rsi-1",
        node_type=NodeType.INDICATOR,
        node_label="RSI(14)",
        start_time=now,
        inputs=[NodeIO(key="close", value=Decimal("61000"), dtype="float")],
        outputs=[NodeIO(key="value", value=28.4, dtype="float")],
    )
    node.complete(ValidationResult.PASS, node.outputs)
    node.execution_ms = 3.5
    record.add_node_trace(node)

    action = DAGNodeTrace(
        node_id="action-1",
        node_type=NodeType.EXECUTION,
        node_label="Buy",
        start_time=now,
    )
    action.complete(ValidationResult.PASS, [NodeIO(key="decision", value="BUY", dtype="string")])
    record.add_node_trace(action)

    if with_ml:
        ml = MLInferenceTrace(
            model_id="model-7",
            model_version="1.2.0",
            inference_start=now,
            features={"rsi": 28.4},
            feature_vector=[28.4],
        )
        ml.complete("BUY", 0.83, {"BUY": 0.83, "SELL": 0.17})
        record.ml_trace = ml

    risk = RiskValidationTrace(
        checks=["position_limit", "drawdown", "exposure"],
        current_exposure=Decimal("1250"),
        exposure_limit=Decimal("10000"),
        exposure_pct=12.5,
    )
    risk.complete(True, False, None)
    record.risk_trace = risk

    execution = ExecutionTrace(
        signal_type="BUY",
        symbol="BTC/USDT",
        order_type="market",
        requested_size=Decimal("0.25"),
        exchange="kraken",
        order_id="ord-1",
        status="filled",
        exchange_latency_ms=61.0,
    )
    execution.record_fill(Decimal("0.25"), Decimal("61000"), Decimal("1.23"), Decimal("0.02"))
    record.execution_trace = execution
    return record


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Each test probes 005b and the transition log for itself: both verdicts are cached."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


@pytest.fixture
def engine(monkeypatch):
    """``load_signal_trace_record``'s store, as a per-test dict keyed by strategy id."""
    store = {}

    class _FakeEngine:
        async def get_recent_traces(self, strategy_id=None, limit=50):
            return list(store.get(strategy_id, ()))[:limit]

    import backend_app.backend.signal_trace_engine as engine_module

    monkeypatch.setattr(engine_module, "trace_engine", _FakeEngine())
    return store


# ══════════════════════════════════════════════════════════════════════════
# 1. ROUTE IDENTITY ON THE REAL APP
# ══════════════════════════════════════════════════════════════════════════


def _resolve(path, method="GET"):
    from backend_app.main import app

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in app.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route
    return None


def test_the_detail_route_exists_and_is_the_extended_pre_spec_handler():
    route = _resolve("/api/signal-trace/signals/sig-1111")
    assert route is not None
    assert route.path == "/api/signal-trace/signals/{signal_id}"
    assert route.name == "get_signal"


def test_the_detail_path_is_registered_exactly_once():
    """A second handler for the same path would be permanently unreachable.

    FastAPI stops at the first match, so 13.2 had to EXTEND the pre-spec handler rather
    than add one beside it. This asserts it did.
    """
    from fastapi.routing import APIRoute

    from backend_app.main import app

    handlers = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/signal-trace/signals/{signal_id}"
        and "GET" in route.methods
    ]
    assert len(handlers) == 1, (
        f"{len(handlers)} GET handlers are registered for the detail path; every one after "
        "the first is dead code."
    )


def test_adding_the_detail_route_did_not_shadow_the_literal_paths():
    """Task 13.1's structural obligation, re-asserted from this task's side."""
    from backend_app.routers.signal_trace import SIGNAL_TRACE_LITERAL_PATHS

    for path in SIGNAL_TRACE_LITERAL_PATHS:
        route = _resolve(path)
        assert route is not None and route.path == path, (
            f"{path} is now shadowed by {route.path if route else None}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE JOIN - Requirement 17.6's four sections, from the engine
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_signal_key_is_task_13_1s_projection_unchanged(engine):
    """One shape reaches the frontend: the detail's `signal` IS the list's item."""
    row = signal_row()
    service, _ = service_for([row], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert detail["signal"] == svc.signal_trace_item(row)


@pytest.mark.asyncio
async def test_the_dag_node_trace_comes_from_the_engine_when_it_has_one(engine):
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)
    nodes = detail["trace"]["dag_nodes"]

    assert nodes["source"] == TRACE_SOURCE_ENGINE
    by_id = {node["node_id"]: node for node in nodes["nodes"]}
    assert set(by_id) == {"rsi-1", "action-1"}
    assert by_id["rsi-1"]["node_type"] == "indicator"
    assert by_id["rsi-1"]["node_label"] == "RSI(14)"
    assert by_id["rsi-1"]["status"] == "pass"
    assert by_id["rsi-1"]["execution_ms"] == 3.5
    assert by_id["rsi-1"]["inputs"] == [
        {"key": "close", "value": 61000.0, "dtype": "float", "shape": None}
    ]
    assert by_id["rsi-1"]["outputs"] == [
        {"key": "value", "value": 28.4, "dtype": "float", "shape": None}
    ]


@pytest.mark.asyncio
async def test_the_ml_inference_detail_comes_from_the_engine_when_it_has_one(engine):
    engine["strat-1"] = [engine_record(with_ml=True)]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    ml = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["ml_inference"]

    assert ml["applicable"] is True
    assert ml["source"] == TRACE_SOURCE_ENGINE
    assert ml["detail"]["model_id"] == "model-7"
    assert ml["detail"]["model_version"] == "1.2.0"
    assert ml["detail"]["prediction"] == "BUY"
    assert ml["detail"]["confidence"] == 0.83
    assert ml["detail"]["probabilities"] == {"BUY": 0.83, "SELL": 0.17}


@pytest.mark.asyncio
async def test_the_risk_detail_merges_the_engines_checks_under_the_persisted_verdict(engine):
    """The persisted verdict wins; the fields only the engine saw are not lost."""
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    risk = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["risk_validation"]

    assert risk["source"] == TRACE_SOURCE_ENGINE
    # Only the engine had these.
    assert risk["detail"]["checks"] == ["position_limit", "drawdown", "exposure"]
    assert risk["detail"]["blocked"] is False
    assert risk["detail"]["exposure_limit"] == 10000.0
    # The row's own verdict is what is reported for the fields both carry.
    assert risk["detail"]["passed"] is True
    assert risk["detail"]["reason"] == "within limits"
    assert risk["detail"]["position_size"] == 0.25


@pytest.mark.asyncio
async def test_the_execution_outcome_is_the_rows_columns_and_the_engine_is_reported_beside_it(
    engine,
):
    """Two different questions about the same order, labelled rather than merged."""
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    execution = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["execution"]

    assert execution["source"] == TRACE_SOURCE_ROW
    assert execution["outcome"] == svc._execution_outcome_of(
        signal_row(), OrderLifecycleState.EXECUTED
    )
    assert execution["outcome"]["execution_price"] == 61000.0
    assert execution["outcome"]["filled_quantity"] == 0.25
    assert execution["exchange_response"]["order_id"] == "ord-1"
    assert execution["exchange_response"]["exchange_latency_ms"] == 61.0
    assert execution["exchange_response"]["fill_percent"] == 100.0


@pytest.mark.asyncio
async def test_the_trace_envelope_reports_the_engine_records_own_identity(engine):
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    trace = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]

    assert trace["available"] is True
    assert trace["trace_id"] == "trace-9999"
    assert trace["status"] == "completed"
    assert trace["final_decision"] == "EXECUTE"
    assert trace["total_latency_ms"] == 137.5


@pytest.mark.asyncio
async def test_another_signals_engine_record_is_not_reported_for_this_signal(engine):
    """The store is indexed by strategy, so the signal_id match has to do the work."""
    engine["strat-1"] = [engine_record(signal_id="sig-someone-else")]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    trace = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]

    assert trace["available"] is False
    assert trace["trace_id"] is None


# ══════════════════════════════════════════════════════════════════════════
# 3. THE DEGRADED JOIN - no engine record, which is the common case
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_without_an_engine_record_the_dag_trace_is_read_from_the_persisted_closure(
    engine,
):
    """A signal older than the store's hour still has a node-level trace: its closure."""
    service, _ = service_for([signal_row()], transitions=transition_rows())

    nodes = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["dag_nodes"]

    assert nodes["source"] == TRACE_SOURCE_ROW
    by_id = {node["node_id"]: node for node in nodes["nodes"]}
    assert set(by_id) == {"rsi-1", "ema-2", "action-1"}
    assert by_id["rsi-1"]["reading"] == 28.4
    assert by_id["action-1"]["is_source_node"] is True
    assert by_id["rsi-1"]["is_source_node"] is False
    # Timing was never persisted, and is not invented.
    assert by_id["rsi-1"]["execution_ms"] is None


@pytest.mark.asyncio
async def test_without_an_engine_record_the_risk_and_execution_still_answer(engine):
    service, _ = service_for([signal_row()], transitions=transition_rows())

    trace = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]

    assert trace["available"] is False
    assert trace["risk_validation"]["source"] == TRACE_SOURCE_ROW
    assert trace["risk_validation"]["detail"]["passed"] is True
    assert trace["risk_validation"]["detail"]["capital"] == 10000.0
    assert trace["execution"]["outcome"]["execution_id"] == "trd-1"
    assert trace["execution"]["exchange_response"] is None


@pytest.mark.asyncio
async def test_ml_inference_is_not_applicable_for_a_rule_based_signal(engine):
    """Requirement 17.6's "where the strategy version includes an ML node", as a fact.

    ``applicable: false`` is not the same answer as "the ML detail is missing", and a page
    that cannot tell them apart shows an empty ML panel for a strategy that has no ML node.
    """
    service, _ = service_for([signal_row(ml_info=None)], transitions=transition_rows())

    ml = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["ml_inference"]

    assert ml["applicable"] is False
    assert ml["detail"] is None


@pytest.mark.asyncio
async def test_ml_inference_falls_back_to_the_persisted_ml_info(engine):
    row = signal_row(ml_info={"model_id": "model-7", "confidence": 0.83})
    service, _ = service_for([row], transitions=transition_rows())

    ml = (await service.get_signal_trace(OWNER, SIGNAL_ID))["trace"]["ml_inference"]

    assert ml["applicable"] is True
    assert ml["source"] == TRACE_SOURCE_ROW
    assert ml["detail"]["model_id"] == "model-7"


@pytest.mark.asyncio
async def test_an_unreadable_trace_store_does_not_fail_the_detail(monkeypatch):
    """A diagnostic that cannot be read is not a reason to 500 an audit page."""
    import backend_app.backend.signal_trace_engine as engine_module

    class _Broken:
        async def get_recent_traces(self, strategy_id=None, limit=50):
            raise RuntimeError("the trace engine was never started")

    monkeypatch.setattr(engine_module, "trace_engine", _Broken())
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert detail["trace"]["available"] is False
    assert detail["trace"]["dag_nodes"]["nodes"], "the row's closure still answers"


# ══════════════════════════════════════════════════════════════════════════
# 4. REQUIREMENT 16.7 - the transition history
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_transition_history_is_returned_in_chronological_order(engine):
    """Requirement 16.7: "retrievable in chronological order by timestamp".

    The fake returns the rows REVERSED relative to the requested order, so passing this
    means the answer is chronological rather than the ordering hint merely being sent.
    """
    service, _ = service_for([signal_row()], transitions=transition_rows())

    history = (await service.get_signal_trace(OWNER, SIGNAL_ID))["lifecycle_transitions"]

    assert history["available"] is True
    assert [t["to_state"] for t in history["transitions"]] == [
        "GENERATED",
        "PENDING",
        "SUBMITTED",
        "EXECUTED",
    ]
    assert [t["occurred_at"] for t in history["transitions"]] == sorted(
        t["occurred_at"] for t in history["transitions"]
    )


@pytest.mark.asyncio
async def test_each_transition_carries_the_prior_value_the_new_value_and_the_timestamp(engine):
    """Requirement 16.7's stated minimum."""
    service, _ = service_for([signal_row()], transitions=transition_rows())

    history = (await service.get_signal_trace(OWNER, SIGNAL_ID))["lifecycle_transitions"]

    assert history["transitions"][0] == {
        "from_state": None,
        "to_state": "GENERATED",
        "reason": "signal generated",
        "occurred_at": "2024-05-01T12:00:00+00:00",
    }
    assert history["transitions"][1]["from_state"] == "GENERATED"


@pytest.mark.asyncio
async def test_the_transition_history_is_owner_scoped(engine):
    service, client = service_for([signal_row()], transitions=transition_rows())

    history = (await service.get_signal_trace(OWNER, SIGNAL_ID))["lifecycle_transitions"]

    log_queries = [
        q for q in client.queries if q.table_name == svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE
    ]
    assert log_queries, "the transition log was never read"
    assert ("eq", "user_id", OWNER["id"]) in log_queries[-1].predicates
    assert ("eq", "signal_id", SIGNAL_ID) in log_queries[-1].predicates
    assert all(t["reason"] != "not yours" for t in history["transitions"])


@pytest.mark.asyncio
async def test_an_absent_transition_table_degrades_rather_than_erroring(engine, caplog):
    """Requirement 16.7 is conditioned on the audit store being available."""
    service, _ = service_for([signal_row()], transitions_table=False)

    with caplog.at_level("WARNING"):
        detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    history = detail["lifecycle_transitions"]
    assert history["available"] is False
    assert history["transitions"] == []
    assert history["degraded"]["migration"] == SIGNAL_LIFECYCLE_MIGRATION
    assert "005b_signal_lifecycle_and_idempotency.sql" in caplog.text
    # And the rest of the detail is unaffected.
    assert detail["signal"]["id"] == SIGNAL_ID


@pytest.mark.asyncio
async def test_the_derived_timeline_is_retained_beside_the_audit_history(engine):
    """SignalTrace.jsx renders `timeline` today; task 19 is what re-points the page.

    AUTHORISED UPDATE - vyomquant-ui-redesign BC-6 (task 12.6; Requirements 9.1, 9.2,
    19.1, 19.2). This assertion was a CLOSED-WORLD one: it pinned the derived timeline to
    exactly the five pre-spec events. BC-6 appends a sixth, `POSITION_UPDATED`, because
    Requirement 9.1's ninth stage - the position change a signal produced - had no backing
    record at all (design.md §10.1's stage table registers it as the one place the
    requirement asked for something the backend did not track). Task 12.6's own
    verification is that this timeline carries it.

    What this test still guards is what it was written to guard, and BC-6 changed none of
    it: the derived timeline is RETAINED beside `lifecycle_transitions` rather than
    replaced by it, and the five pre-spec events keep their names and their order. Those
    five are asserted separately below so a rename or a reorder of them still fails here,
    which an assertion over the six as one list would not distinguish from an append.

    BC-6's own behaviour - exactly-once, always after `EXECUTED`, absent for a signal that
    never executed, and nothing fabricated in the payload - is covered by
    tests/test_position_updated_projection.py.
    """
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    events = [event["event"] for event in detail["timeline"]]
    assert events == [
        "SIGNAL_GENERATED",
        "RISK_EVALUATED",
        "ORDER_CREATED",
        "EXCHANGE_RESPONSE",
        "EXECUTED",
        svc.POSITION_UPDATED_EVENT,
    ]
    # The pre-spec five, unchanged in name and relative order (Requirement 19.1).
    assert [event for event in events if event != svc.POSITION_UPDATED_EVENT] == [
        "SIGNAL_GENERATED",
        "RISK_EVALUATED",
        "ORDER_CREATED",
        "EXCHANGE_RESPONSE",
        "EXECUTED",
    ]


@pytest.mark.asyncio
async def test_get_signal_timeline_still_answers_after_the_refactor(engine):
    """The pre-spec method delegates to the extracted helper; its contract is unchanged."""
    service, _ = service_for([signal_row()])

    timeline = await service.get_signal_timeline(OWNER, SIGNAL_ID)

    assert timeline == svc.signal_event_timeline(signal_row())
    assert await service.get_signal_timeline(OWNER, "no-such-signal") == []


# ══════════════════════════════════════════════════════════════════════════
# 5. OWNERSHIP (Requirements 20.1, 20.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_non_owner_gets_the_identical_non_existence_answer(engine):
    """Requirement 20.2: no aspect of the response may reveal that the signal exists."""
    engine["strat-1"] = [engine_record()]

    service, _ = service_for([signal_row()], transitions=transition_rows())
    foreign = await service.get_signal_trace(INTRUDER, SIGNAL_ID)

    service, _ = service_for([signal_row()], transitions=transition_rows())
    missing = await service.get_signal_trace(INTRUDER, "signal-that-never-existed")

    assert foreign is None
    assert missing is None
    assert foreign == missing


@pytest.mark.asyncio
async def test_the_owner_predicate_is_on_the_signal_read(engine):
    service, client = service_for([signal_row()], transitions=transition_rows())

    await service.get_signal_trace(OWNER, SIGNAL_ID)

    signal_queries = [q for q in client.queries if q.table_name == "signals" and q.predicates]
    assert signal_queries
    assert ("eq", "user_id", OWNER["id"]) in signal_queries[0].predicates


@pytest.mark.asyncio
async def test_the_owner_still_gets_the_full_trace(engine):
    """The ownership assertions above must not be passing because everything is empty."""
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert detail is not None
    assert detail["signal"]["id"] == SIGNAL_ID
    assert detail["trace"]["available"] is True
    assert detail["lifecycle_transitions"]["available"] is True


@pytest.mark.asyncio
async def test_the_trace_store_is_not_read_for_a_signal_the_caller_does_not_own(engine):
    """SignalTraceRecord has no owner column, so it must never be reached first."""
    reads = []

    class _Watching:
        async def get_recent_traces(self, strategy_id=None, limit=50):
            reads.append(strategy_id)
            return [engine_record()]

    import backend_app.backend.signal_trace_engine as engine_module

    engine_module.trace_engine = _Watching()
    try:
        service, _ = service_for([signal_row()], transitions=transition_rows())
        assert await service.get_signal_trace(INTRUDER, SIGNAL_ID) is None
    finally:
        engine_module.trace_engine = engine_module.SignalTraceEngine()

    assert reads == [], "the unowned trace store was read before ownership was settled"


# ══════════════════════════════════════════════════════════════════════════
# 6. THE 005b DEGRADATION AND CONTAINMENT (Requirements 16.2, 20.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_without_005b_the_detail_still_answers_and_names_the_migration(engine, caplog):
    row = signal_row(state=None, status="executed")
    service, _ = service_for(
        [row], transitions=transition_rows(), lifecycle_columns=False
    )

    with caplog.at_level("WARNING"):
        detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert detail["lifecycle_state_source"] == "legacy_status_map"
    assert detail["degraded"]["migration"] == SIGNAL_LIFECYCLE_MIGRATION
    assert "005b_signal_lifecycle_and_idempotency.sql" in caplog.text
    # Reconciled from the legacy status column, not invented and not a 500.
    assert detail["signal"]["order_lifecycle_state"] == "EXECUTED"


@pytest.mark.asyncio
async def test_the_degradation_names_the_states_the_legacy_vocabulary_cannot_express(engine):
    """Task 13.1's finding, reported where it changes what the detail view can claim."""
    service, _ = service_for(
        [signal_row(state=None, status="executed")], lifecycle_columns=False
    )

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert detail["degraded"]["unrepresentable_lifecycle_states"] == [
        state.value for state in LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING
    ]
    assert set(detail["degraded"]["unrepresentable_lifecycle_states"]) == {
        "GENERATED",
        "PARTIALLY_EXECUTED",
        "CLOSED",
    }


@pytest.mark.asyncio
async def test_with_005b_applied_nothing_is_reported_as_degraded(engine):
    service, _ = service_for([signal_row()], transitions=transition_rows())
    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)
    assert detail["lifecycle_state_source"] == "canonical"
    assert detail["degraded"] is None


@pytest.mark.asyncio
async def test_no_credential_and_no_free_form_engine_field_reaches_the_wire(engine):
    """Requirement 20.3, on a store that HAS a free-form field.

    ``SignalTraceRecord.metadata`` is a ``Dict[str, Any]`` filled by whatever called
    ``start_trace``, so containment here is not structural the way it is for ``Signal``:
    it holds because every projected field is read by name.
    """
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)
    body = json.dumps(detail, default=str)

    assert "s3cr3t-shhh" not in body
    assert "api_secret" not in body
    # The internal Exchange_Account reference is exempt by name (Requirement 20.3).
    assert detail["signal"]["exchange_account_id"] == "acct-1"


@pytest.mark.asyncio
async def test_the_whole_detail_is_json_serialisable(engine):
    """Decimal, datetime and Enum all reach this projection from the engine record."""
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    json.dumps(detail)  # no default=; a non-JSON value here is a bug, not a formatting nit


@pytest.mark.asyncio
async def test_the_response_carries_every_key_requirement_17_6_asks_for(engine):
    engine["strat-1"] = [engine_record()]
    service, _ = service_for([signal_row()], transitions=transition_rows())

    detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

    assert set(detail) == {
        "signal",
        "trace",
        "lifecycle_transitions",
        "timeline",
        "lifecycle_state_source",
        "degraded",
    }
    # Requirement 17.6's four named sections.
    for section in ("dag_nodes", "ml_inference", "risk_validation", "execution"):
        assert section in detail["trace"], section
        assert "source" in detail["trace"][section], section


# ══════════════════════════════════════════════════════════════════════════
# 7. THE PURE PROJECTIONS - no database, no engine
# ══════════════════════════════════════════════════════════════════════════


def minted_signal():
    deployment = {
        "id": "dep-1111",
        "user_id": OWNER["id"],
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "paper",
        "worker_id": "worker-7",
        "api_secret": "s3cr3t-shhh",
    }
    node_output = {
        "decision": "BUY",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "quantity": 0.25,
        "price": 61234.5,
        "source_node_ids": ["action-1"],
        "closure_ready": True,
        "node_closure": {"rsi-1": 28.4},
        "risk_validation": {"passed": True, "reason": "within limits"},
    }
    return mint_signal(deployment, node_output)


def test_an_emitting_action_node_outside_the_closure_still_appears_in_the_trace():
    """It is the node the decision came FROM; omitting it would hide the emitter."""
    row = signal_row()
    row["indicators"] = {"rsi-1": 28.4}
    row["market_info"] = dict(row["market_info"], source_node_ids=["action-1", "action-2"])

    nodes = {node["node_id"]: node for node in svc._dag_nodes_from_row(row)}

    assert set(nodes) == {"rsi-1", "action-1", "action-2"}
    assert nodes["action-2"]["is_source_node"] is True
    assert nodes["action-2"]["reading"] is None


def test_both_dag_node_sources_render_into_one_key_set():
    """The page renders one component either way and reads `source` to know which it got."""
    record = engine_record()
    from_engine = svc._dag_nodes_from_record(record)
    from_row = svc._dag_nodes_from_row(signal_row())

    assert from_engine and from_row
    assert set(from_engine[0]) == set(from_row[0])
    assert from_engine[0]["source"] == TRACE_SOURCE_ENGINE
    assert from_row[0]["source"] == TRACE_SOURCE_ROW


def test_a_minted_signals_row_renders_a_dag_trace_of_its_own_closure():
    row = minted_signal().to_row()
    nodes = {node["node_id"]: node for node in svc._dag_nodes_from_row(row)}
    assert nodes["rsi-1"]["reading"] == 28.4
    assert nodes["action-1"]["is_source_node"] is True
