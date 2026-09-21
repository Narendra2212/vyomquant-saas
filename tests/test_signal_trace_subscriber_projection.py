"""
tests/test_signal_trace_subscriber_projection.py - task 29.4.

Spec: marketplace-subscriptions-paper-trading. Requirements 7.9, 23.2, 23.3 (and 19.7's
disclosure rule, read against a REST body).

THE FILE THE TASK AND THE DESIGN BOTH NAME
    Task 29.4's own verification line runs ``pytest tests/test_signal_trace_subscriber_projection.py``
    and ``design.md`` says this module "applies ``assert_contains_no_protected_logic`` to the
    subscriber view", so it is a file of that name rather than a section of
    ``tests/test_task_29_signal_environments.py``. Task 29.3's filter tests live in
    ``tests/test_task_29_3_signal_environment_filter.py`` for the same reason: one subject each.

WHO THE NON-OWNER IS, AND WHY THEY CAN REACH THE ROW AT ALL
    A subscriber runs a purchased Listing in their OWN Paper_Session. Every signal that session
    records carries ``user_id`` = the subscriber (they ran it) and ``strategy_id`` = the
    creator's strategy (they wrote it). So the owner-scoped read admits them - it is their
    signal - and the full trace of it is a description of somebody else's Protected_Logic.
    That is the case Requirement 23.3 is about, and it is the case constructed below.

WHAT IS ASSERTED, SECTION BY SECTION
    1. THE ALLOW-LIST. Fourteen fields, Requirement 23.2's list, and NONE of the ten internals
       Requirement 23.3 excludes. Asserted as a set, because Criterion 3 is an upper bound as
       well as a lower one.
    2. THE PROJECTION. A non-owner's ``signal`` key set IS the allow-list - not a superset, not
       a subset - and its values are the same facts the owner sees, not a second rendering.
    3. CONTAINMENT. ``listing_projection.assert_contains_no_protected_logic`` over a strategy
       whose graph, logic, indicators and risk configuration are seeded with distinctive
       tokens, plus a scan for the ten excluded names at any nesting depth, plus the
       NON-VACUITY control: the owner's own view really does carry them.
    4. THE OWNER'S VIEW, FIELD FOR FIELD. Six envelope keys, ``signal`` identical to
       ``signal_trace_item`` of the same row, and the four Requirement 17.6 trace sections.
    5. WHERE THE ROLE COMES FROM. ``strategies.user_id`` versus the authenticated identity,
       resolved server-side, and NOT from any request field. Fails closed on every answer that
       is not a positive match.
    6. NOTHING STORED IS NARROWED. The row still carries all thirteen Requirement 23.2 facts
       and the three JSONB documents; the restriction happens at read time.

``_run_coroutine`` is imported from ``tests/test_paper_order_lifecycle_writes.py`` rather than
re-derived, and no test here calls ``asyncio.run``: see that module's ``_HARNESS_LOOP`` note.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, FrozenSet, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import signal_service as svc
from backend_app.backend.marketplace import listing_projection
from backend_app.backend.signal_service import (
    SAFE_REASON_CANCELLED,
    SAFE_REASON_FAILED,
    SAFE_REASON_NOT_SUBMITTED,
    SAFE_REASON_REJECTED,
    SAFE_REASON_RISK_REFUSED,
    SIGNAL_SOURCE_LIVE_DEPLOYMENT,
    SIGNAL_SOURCE_PAPER_SESSION,
    SUBSCRIBER_SIGNAL_FIELDS,
    VIEWER_ROLE_OWNER,
    VIEWER_ROLE_SUBSCRIBER,
    SignalService,
    project_subscriber_signal,
    resolve_signal_viewer_role,
    signal_trace_item,
)
from tests.test_paper_order_lifecycle_writes import _run_coroutine

#: The creator: they wrote the strategy, so they see everything.
CREATOR = {"id": "user-creator", "access_token": "token-creator"}
#: The subscriber: they RAN the strategy in their own Paper_Session, so the signal is theirs
#: and the strategy is not.
SUBSCRIBER = {"id": "user-subscriber", "access_token": "token-subscriber"}

STRATEGY_ID = "strat-protected-1"
SIGNAL_ID = "sig-paper-1"
SESSION_ID = "sess-7f1c9a44"

#: The ten internals Requirement 23.3 excludes, spelled as the keys they would arrive under.
#: This is the same list ``tests/test_subscriber_restricted_operations.py`` holds the surface
#: to; it is repeated here because this module is the one that must catch a leak FIRST, and a
#: cross-module import of a test constant would make one file's edit silently weaken the other.
REQUIREMENT_23_3_EXCLUSIONS = (
    "indicators",
    "market_info",
    "ml_info",
    "dag_nodes",
    "ml_inference",
    "risk_reason",
    "drawdown_check",
    "exposure",
    "expected_loss",
    "expected_reward",
)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


def protected_strategy_row() -> Dict[str, Any]:
    """A ``strategies`` row whose Protected_Logic columns carry distinctive tokens.

    Every value here is something a response must never echo, and every one is long enough to
    clear ``listing_projection.PROTECTED_LOGIC_TOKEN_MIN_LENGTH`` - so "the response carries
    none of them" is a real statement rather than a vacuous one.
    """
    return {
        "id": STRATEGY_ID,
        "user_id": CREATOR["id"],
        "name": "Momentum Reversal",
        "buy_logic": {
            "node_zzq_entry": {"indicator": "rsi_zzq_14", "threshold": 27.44},
        },
        "sell_logic": {"node_zzq_exit": {"indicator": "ema_zzq_200"}},
        "indicators": {"rsi_zzq_14": {"period": 14}, "ema_zzq_200": {"period": 200}},
        "risk": {"stop_loss_pct_zzq": 1.75, "max_drawdown_pct_zzq": 4.25},
        "ml_model_path": "models/creator/zzq_classifier_v7.pkl",
    }


def protected_version_row() -> Dict[str, Any]:
    return {
        "id": "ver-zzq-3",
        "version": "v3",
        "blueprint": {"nodes": [{"id": "node_zzq_entry", "type": "logic_zzq_and"}]},
        "graph_json": {"edges": [["node_zzq_data", "node_zzq_entry"]]},
        "execution_graph": {"order": ["node_zzq_data", "node_zzq_entry"]},
    }


def protected_tokens() -> FrozenSet[str]:
    return listing_projection.protected_logic_tokens(
        protected_strategy_row(), protected_version_row()
    )


def paper_signal_row(
    *,
    signal_id: str = SIGNAL_ID,
    user_id: str = SUBSCRIBER["id"],
    environment: Optional[str] = "PAPER",
    state: str = "EXECUTED",
    status: str = "executed",
    risk_passed: bool = True,
    ml_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One ``public.signals`` row the subscriber's Paper_Session recorded.

    It carries the creator's Protected_Logic in exactly the places a real row does:
    ``indicators`` is the per-node closure keyed by NODE ID, ``market_info.source_node_ids``
    names the emitting ACTION node, ``ml_info`` is the model's own output and ``risk_reason``
    is the risk engine's free text naming the rule it applied.
    """
    return {
        "id": signal_id,
        "user_id": user_id,
        "strategy_id": STRATEGY_ID,
        "strategy_version": "v3",
        "deployment_id": None,
        "exchange_id": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "worker_id": "paper-session-worker-3",
        "decision": "BUY",
        "status": status,
        "order_lifecycle_state": state,
        "quantity": 0.25,
        "generated_at": "2024-05-01T12:00:00+00:00",
        "environment": environment,
        "paper_session_id": SESSION_ID if environment == "PAPER" else None,
        "indicators": {"node_zzq_entry": {"value": 27.44, "ready": True}},
        "market_info": {
            "signal_type": "ENTRY",
            "side": "BUY",
            "mode": "paper" if environment == "PAPER" else "live",
            "strategy_version_id": "ver-zzq-3",
            "exchange_account_id": None,
            "source_node_ids": ["node_zzq_entry"],
            "closure_ready": True,
            "sizing_intention": None,
            "price": 61234.5,
        },
        "ml_info": ml_info,
        "risk_passed": risk_passed,
        "risk_reason": "max_drawdown_pct_zzq 4.25 not breached",
        "position_size": 0.25,
        "capital": 10000.0,
        "exposure": 0.125,
        "expected_loss": 21.5,
        "expected_reward": 64.5,
        "drawdown_check": True,
        "risk_evaluated_at": "2024-05-01T12:00:01+00:00",
        "order_id": "paper-ord-1",
        "order_status": "filled",
        "filled": 0.25,
        "remaining": 0.0,
        "average_price": 61240.0,
        "fees": 1.53,
        "slippage": 5.5,
        "latency_ms": 12.0,
        "trade_id": "paper-trd-1",
        "pnl": 42.0,
        "realized_pnl": 42.0,
        "order_updated_at": "2024-05-01T12:00:02+00:00",
        "executed_at": "2024-05-01T12:00:03+00:00",
    }


class _Result:
    def __init__(self, data: Any = None, error: Any = None) -> None:
        self.data = data
        self.error = error


class FakeQuery:
    def __init__(self, client: "FakeSupabase", table: str, columns: str) -> None:
        self.client = client
        self.table_name = table
        self.columns = columns
        self.predicates: List[Any] = []
        self._limit: Optional[int] = None

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.predicates.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "FakeQuery":
        self.predicates.append(("in", column, list(values)))
        return self

    def order(self, column: str, desc: bool = False) -> "FakeQuery":
        return self

    def limit(self, n: int) -> "FakeQuery":
        self._limit = n
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        return self

    def execute(self) -> Any:
        self.client.queries.append(self)

        if self.table_name == svc.STRATEGY_OWNER_TABLE:
            self.client.strategy_reads += 1
            if self.client.strategy_read_raises:
                raise Exception("PGRST000 could not connect to the strategies table")
            rows = [row for row in self.client.strategies if self._matches(row)]
            return _Result(data=[{"user_id": row.get("user_id")} for row in rows])

        if self.table_name == "signals":
            if self._limit is not None and not self.predicates:
                return _Result(data=[])  # a migration probe
            rows = [row for row in self.client.rows if self._matches(row)]
            return _Result(data=[dict(row) for row in rows])

        if self.table_name == svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE:
            rows = [row for row in self.client.transitions if self._matches(row)]
            return _Result(data=[dict(row) for row in rows])

        raise AssertionError(f"unexpected table {self.table_name}")

    def _matches(self, row: Dict[str, Any]) -> bool:
        for kind, column, value in self.predicates:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
        return True


class FakeSupabase:
    """Signals, the strategy that owns them, and the transition log. RLS is not emulated.

    Not emulating RLS is deliberate: the ``strategies`` read answers the creator's row to
    WHOEVER asks, so the ownership decision under test has to be made by comparing the
    ``user_id`` it returns - a handler that trusted the read's mere success would be caught.
    """

    def __init__(
        self,
        rows: Any,
        *,
        strategies: Any = None,
        transitions: Any = (),
        strategy_read_raises: bool = False,
    ) -> None:
        self.rows = list(rows)
        self.strategies = (
            [protected_strategy_row()] if strategies is None else list(strategies)
        )
        self.transitions = list(transitions)
        self.strategy_read_raises = strategy_read_raises
        self.strategy_reads = 0
        self.queries: List[FakeQuery] = []
        self._table: Optional[str] = None

    def table(self, name: str) -> "FakeSupabase":
        self._table = name
        return self

    def select(self, columns: str) -> FakeQuery:
        return FakeQuery(self, self._table or "", columns)


def service_for(rows: Any, **kwargs: Any):
    service = SignalService()
    client = FakeSupabase(rows, **kwargs)

    async def _client(_user: Any) -> Any:
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()


@pytest.fixture(autouse=True)
def _no_engine_record(monkeypatch):
    """The in-memory trace store is empty, so the trace is read from the row.

    Which is the common case for an audit read anyway (the store retains an hour), and it
    keeps the owner-versus-subscriber comparison about the ROW rather than about whichever
    process happened to hold a record.
    """

    async def _none(_signal_id: Any, _strategy_id: Any) -> Any:
        return None

    monkeypatch.setattr(svc, "load_signal_trace_record", _none)


def values_at_any_depth(payload: Any) -> List[Any]:
    """Every leaf VALUE of ``payload``, at any depth. Mapping keys are not values."""
    found: List[Any] = []
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(values_at_any_depth(value))
    elif isinstance(payload, (list, tuple, set, frozenset)):
        for item in payload:
            found.extend(values_at_any_depth(item))
    elif payload is not None:
        found.append(payload)
    return found


def keys_at_any_depth(payload: Any) -> FrozenSet[str]:
    found: set = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= keys_at_any_depth(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= keys_at_any_depth(item)
    return frozenset(found)


def containment_keys() -> FrozenSet[str]:
    """The key vocabulary this body declares, exempt in KEY position only.

    ``assert_contains_no_protected_logic`` searches mapping keys as well as values unless they
    are the response's own fixed vocabulary - and these keys are emitted for every signal of
    every strategy, including one with no indicators at all, so they cannot be a disclosure.
    Every VALUE is still searched, at every depth.
    """
    return frozenset(SUBSCRIBER_SIGNAL_FIELDS) | frozenset(
        {"signal", "viewer_role", "lifecycle_state_source", "degraded", "migration", "reason"}
    ) | listing_projection.STRUCTURAL_RESPONSE_KEYS


# ══════════════════════════════════════════════════════════════════════════
# 1. THE ALLOW-LIST (Requirements 23.2, 23.3)
# ══════════════════════════════════════════════════════════════════════════


def test_the_allow_list_is_requirement_23_2s_fourteen_facts():
    assert set(SUBSCRIBER_SIGNAL_FIELDS) == {
        "strategy_id",
        "strategy_version",
        "session_or_deployment_id",
        "environment",
        "generated_at",
        "decision",
        "symbol",
        "side",
        "quantity",
        "price",
        "order_lifecycle_state",
        "order_id",
        "signal_source",
        "safe_reason",
    }
    assert len(SUBSCRIBER_SIGNAL_FIELDS) == len(set(SUBSCRIBER_SIGNAL_FIELDS)) == 14


@pytest.mark.parametrize("excluded", REQUIREMENT_23_3_EXCLUSIONS)
def test_the_allow_list_names_none_of_requirement_23_3s_internals(excluded):
    assert excluded not in set(SUBSCRIBER_SIGNAL_FIELDS)


def test_the_allow_list_names_no_free_text_reason_column():
    """Requirement 23.2 asks for a SAFE reason; ``risk_reason`` is the risk engine's own text."""
    assert "risk_reason" not in SUBSCRIBER_SIGNAL_FIELDS
    assert "safe_reason" in SUBSCRIBER_SIGNAL_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 2. THE PROJECTION - the allow-list, exactly
# ══════════════════════════════════════════════════════════════════════════


def test_the_projection_emits_exactly_the_allow_list_in_its_declared_order():
    projected = project_subscriber_signal(paper_signal_row())
    assert list(projected) == list(SUBSCRIBER_SIGNAL_FIELDS)


def test_the_projected_values_are_the_facts_the_owner_sees():
    """A SUBSET of the owner's view, not a second rendering that could drift from it."""
    row = paper_signal_row()
    owner_item = signal_trace_item(row)
    projected = project_subscriber_signal(row)

    assert projected["strategy_id"] == owner_item["strategy_id"]
    assert projected["strategy_version"] == owner_item["strategy_version"]
    assert projected["environment"] == owner_item["environment"] == "PAPER"
    assert projected["generated_at"] == owner_item["generated_at"]
    assert projected["decision"] == owner_item["decision"] == "BUY"
    assert projected["symbol"] == owner_item["symbol"]
    assert projected["side"] == owner_item["side"]
    assert projected["quantity"] == owner_item["quantity"]
    assert projected["order_lifecycle_state"] == owner_item["order_lifecycle_state"]
    assert projected["order_id"] == owner_item["order_id"] == "paper-ord-1"
    assert projected["price"] == owner_item["execution"]["execution_price"] == 61240.0


def test_the_applicable_identifier_is_the_paper_session_for_a_paper_signal():
    """Requirement 23.2's "Paper_Session OR deployment identifier as applicable"."""
    projected = project_subscriber_signal(paper_signal_row())
    assert projected["session_or_deployment_id"] == SESSION_ID
    assert projected["environment"] == "PAPER"


def test_the_applicable_identifier_is_the_deployment_for_a_live_signal():
    row = paper_signal_row(environment="LIVE")
    row["deployment_id"] = "dep-9"
    projected = project_subscriber_signal(row)
    assert projected["session_or_deployment_id"] == "dep-9"
    assert projected["environment"] == "LIVE"


def test_the_price_falls_back_to_the_decision_price_before_a_fill():
    row = paper_signal_row(state="GENERATED", status="pending")
    row["average_price"] = None
    assert project_subscriber_signal(row)["price"] == 61234.5


def test_the_signal_source_is_the_runtime_and_never_a_node_identifier():
    assert (
        project_subscriber_signal(paper_signal_row())["signal_source"]
        == SIGNAL_SOURCE_PAPER_SESSION
    )
    assert (
        project_subscriber_signal(paper_signal_row(environment="LIVE"))["signal_source"]
        == SIGNAL_SOURCE_LIVE_DEPLOYMENT
    )


def test_the_signal_source_of_a_pre_010_row_falls_back_to_the_recorded_mode():
    row = paper_signal_row(environment=None)
    row["market_info"]["mode"] = "paper"
    assert project_subscriber_signal(row)["signal_source"] == SIGNAL_SOURCE_PAPER_SESSION


def test_an_executed_signal_has_no_reason_at_all():
    assert project_subscriber_signal(paper_signal_row())["safe_reason"] is None


@pytest.mark.parametrize(
    "state,status,risk_passed,expected",
    [
        ("REJECTED", "rejected", False, SAFE_REASON_RISK_REFUSED),
        ("REJECTED", "rejected", True, SAFE_REASON_REJECTED),
        ("FAILED", "failed", True, SAFE_REASON_FAILED),
        ("CANCELLED", "cancelled", True, SAFE_REASON_CANCELLED),
        ("GENERATED", "pending", True, SAFE_REASON_NOT_SUBMITTED),
    ],
)
def test_the_safe_reason_is_a_closed_vocabulary_and_never_the_risk_text(
    state, status, risk_passed, expected
):
    row = paper_signal_row(state=state, status=status, risk_passed=risk_passed)
    projected = project_subscriber_signal(row)
    assert projected["safe_reason"] == expected
    assert "max_drawdown_pct_zzq" not in json.dumps(projected)


def test_the_key_set_is_identical_for_an_ml_strategy_and_a_rule_based_one():
    """Requirement 19.7: the SHAPE of the answer must disclose nothing about the strategy."""
    rule_based = project_subscriber_signal(paper_signal_row(ml_info=None))
    with_ml = project_subscriber_signal(
        paper_signal_row(
            ml_info={"model_id": "zzq_classifier_v7", "confidence": 0.91, "prediction": "BUY"}
        )
    )
    assert list(rule_based) == list(with_ml)
    assert "zzq_classifier_v7" not in json.dumps(with_ml)


def test_a_field_added_to_the_owners_item_does_not_appear_in_the_projection():
    """The allow-list's whole point: a new field is withheld BY OMISSION.

    Simulated the way it actually happens - a new column on the row, which
    ``signal_trace_item`` renders and this projection must not.
    """
    row = paper_signal_row()
    row["some_future_audit_column"] = "node_zzq_entry"
    projected = project_subscriber_signal(row)
    assert "some_future_audit_column" not in projected
    assert list(projected) == list(SUBSCRIBER_SIGNAL_FIELDS)


# ══════════════════════════════════════════════════════════════════════════
# 3. CONTAINMENT, WITH ITS NON-VACUITY CONTROL (Requirements 7.9, 23.3)
# ══════════════════════════════════════════════════════════════════════════


def test_the_token_set_is_not_empty_so_the_containment_check_is_not_vacuous():
    tokens = protected_tokens()
    assert tokens
    assert "node_zzq_entry" in tokens
    assert "rsi_zzq_14" in tokens


def test_the_source_row_itself_fails_the_containment_check():
    """The control on the ORACLE: it really does find these tokens when they are there."""
    with pytest.raises(AssertionError):
        listing_projection.assert_contains_no_protected_logic(
            paper_signal_row(), protected_tokens(), containment_keys()
        )


def test_the_owners_own_detail_carries_the_internals_the_subscriber_must_not_see():
    """The control on the TEST: without it, every exclusion below could pass on an empty body."""
    service, _ = service_for([paper_signal_row(user_id=CREATOR["id"])])
    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))

    keys = keys_at_any_depth(detail)
    present = [name for name in REQUIREMENT_23_3_EXCLUSIONS if name in keys]
    assert present, (
        "the owner's own trace detail carries none of the internals Requirement 23.3 "
        f"excludes, so the subscriber assertions are vacuous. Keys: {sorted(keys)[:40]}"
    )


def test_the_subscribers_view_discloses_no_protected_logic():
    service, _ = service_for([paper_signal_row()])
    detail = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    listing_projection.assert_contains_no_protected_logic(
        detail, protected_tokens(), containment_keys()
    )


def test_no_protected_token_appears_in_any_value_the_subscriber_receives():
    """Every VALUE at every depth, by substring - keys excluded, and only keys.

    Stronger than the check above in one respect and weaker in none: it does not need the
    structural-key exemption at all, so it cannot be relaxed by adding a key to that set. Keys
    are excluded because this body's key names are fixed English words emitted for every
    strategy, and one of them ("order_lifecycle_state") legitimately contains the word "order"
    - which IS a token of any strategy whose execution graph has an ``order`` member.
    """
    service, _ = service_for(
        [
            paper_signal_row(
                ml_info={"model_id": "zzq_classifier_v7", "prediction": "BUY"}
            )
        ]
    )
    detail = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    leaked = sorted(
        token
        for token in protected_tokens()
        if any(token in str(value) for value in values_at_any_depth(detail))
    )
    assert not leaked, (
        f"the subscriber's view carries the Protected_Logic token(s) {leaked} in a value "
        f"(Requirements 7.9, 23.3). Body: {json.dumps(detail, default=str)[:400]}"
    )


def test_the_value_scan_finds_a_token_when_there_is_one_to_find():
    """The control on the scan above: run over the ROW, it reports the leak it is looking for."""
    leaked = [
        token
        for token in protected_tokens()
        if any(token in str(value) for value in values_at_any_depth(paper_signal_row()))
    ]
    assert "node_zzq_entry" in leaked
    assert "max_drawdown_pct_zzq" in leaked


@pytest.mark.parametrize("excluded", REQUIREMENT_23_3_EXCLUSIONS)
def test_no_excluded_internal_reaches_the_subscriber_under_any_name_at_any_depth(excluded):
    service, _ = service_for([paper_signal_row()])
    detail = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))
    assert excluded not in keys_at_any_depth(detail)


def test_the_subscriber_envelope_omits_the_trace_the_history_and_the_timeline():
    """Omitted ENTIRELY, not emptied: an empty ``dag_nodes`` still says the strategy has a DAG."""
    service, _ = service_for([paper_signal_row()])
    detail = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    assert set(detail) == {"signal", "viewer_role", "lifecycle_state_source", "degraded"}
    assert detail["viewer_role"] == VIEWER_ROLE_SUBSCRIBER
    assert list(detail["signal"]) == list(SUBSCRIBER_SIGNAL_FIELDS)


def test_the_subscriber_envelope_shape_does_not_depend_on_the_strategy():
    """Requirement 19.7 again, at the envelope level."""
    service, _ = service_for([paper_signal_row(ml_info=None)])
    without_ml = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    service, _ = service_for(
        [paper_signal_row(ml_info={"model_id": "zzq_classifier_v7", "confidence": 0.9})]
    )
    with_ml = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    assert keys_at_any_depth(without_ml) == keys_at_any_depth(with_ml)


def test_the_trace_store_is_not_even_read_for_a_non_owner(monkeypatch):
    """Nothing below the ownership decision runs, so there is nothing to leak from."""
    reads: List[Any] = []

    async def _record(signal_id: Any, strategy_id: Any) -> Any:
        reads.append((signal_id, strategy_id))
        return None

    monkeypatch.setattr(svc, "load_signal_trace_record", _record)

    service, client = service_for([paper_signal_row()])
    _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    assert reads == []
    assert not [
        q for q in client.queries if q.table_name == svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE
    ]


# ══════════════════════════════════════════════════════════════════════════
# 4. THE OWNER'S VIEW IS UNCHANGED, FIELD FOR FIELD (task 29.4 is additive)
# ══════════════════════════════════════════════════════════════════════════


def test_the_owner_gets_the_same_six_keys_the_detail_has_always_returned():
    service, _ = service_for([paper_signal_row(user_id=CREATOR["id"])])
    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))

    assert set(detail) == {
        "signal",
        "trace",
        "lifecycle_transitions",
        "timeline",
        "lifecycle_state_source",
        "degraded",
    }
    assert "viewer_role" not in detail, (
        "the owner's envelope gained a key; an owner's view is unchanged by this task"
    )


def test_the_owners_signal_is_the_full_item_field_for_field():
    row = paper_signal_row(user_id=CREATOR["id"])
    service, _ = service_for([row])

    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))

    assert detail["signal"] == signal_trace_item(row)
    assert detail["signal"]["decision_metadata"]["node_closure"] == row["indicators"]


def test_the_owner_still_gets_requirement_17_6s_four_sections():
    service, _ = service_for([paper_signal_row(user_id=CREATOR["id"])])
    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))

    for section in ("dag_nodes", "ml_inference", "risk_validation", "execution"):
        assert section in detail["trace"], section
        assert "source" in detail["trace"][section], section


def test_the_owner_of_the_strategy_who_is_not_the_owner_of_the_signal_is_still_admitted():
    """The creator reading their OWN signal for their OWN strategy: nothing changed for them."""
    service, _ = service_for([paper_signal_row(user_id=CREATOR["id"], environment="LIVE")])
    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))
    assert detail["signal"]["id"] == SIGNAL_ID
    assert "trace" in detail


# ══════════════════════════════════════════════════════════════════════════
# 5. WHERE THE ROLE COMES FROM (Requirements 21.1, 23.3)
# ══════════════════════════════════════════════════════════════════════════


def test_the_role_is_resolved_by_comparing_the_identity_with_the_strategys_owner():
    service, client = service_for([paper_signal_row()])
    _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    reads = [q for q in client.queries if q.table_name == svc.STRATEGY_OWNER_TABLE]
    assert len(reads) == 1, "the ownership question was asked once, or not at all"
    assert ("eq", "id", STRATEGY_ID) in reads[0].predicates
    assert reads[0].columns == svc.STRATEGY_OWNER_COLUMN


def test_the_creator_resolves_to_owner_and_the_subscriber_to_subscriber():
    client = FakeSupabase([paper_signal_row()])
    row = paper_signal_row()

    assert (
        _run_coroutine(resolve_signal_viewer_role(client, CREATOR, row)) == VIEWER_ROLE_OWNER
    )
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, SUBSCRIBER, row))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_the_role_does_not_come_from_the_signal_rows_own_user_id():
    """``signals.user_id`` says who RAN the strategy - for a subscriber, themselves."""
    client = FakeSupabase([paper_signal_row()])
    row = paper_signal_row(user_id=SUBSCRIBER["id"])
    assert row["user_id"] == SUBSCRIBER["id"]
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, SUBSCRIBER, row))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_no_field_of_the_row_can_claim_ownership():
    """A row carrying every plausible role claim still resolves from the strategy read."""
    client = FakeSupabase([])
    row = paper_signal_row()
    row.update({"viewer_role": "owner", "is_owner": True, "role": "owner"})
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, SUBSCRIBER, row))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_an_absent_strategy_row_withholds():
    """RLS answering nothing is the NORMAL case for a subscriber, and it must fail closed."""
    client = FakeSupabase([], strategies=[])
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, SUBSCRIBER, paper_signal_row()))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_an_unreadable_strategies_table_withholds(caplog):
    client = FakeSupabase([], strategy_read_raises=True)
    with caplog.at_level("WARNING"):
        role = _run_coroutine(
            resolve_signal_viewer_role(client, CREATOR, paper_signal_row())
        )
    assert role == VIEWER_ROLE_SUBSCRIBER
    assert "subscriber-safe projection" in caplog.text


def test_an_unreadable_strategies_table_serves_the_restricted_view_to_the_creator_too():
    """Fails CLOSED: an unanswerable ownership question withholds rather than discloses."""
    service, _ = service_for(
        [paper_signal_row(user_id=CREATOR["id"])], strategy_read_raises=True
    )
    detail = _run_coroutine(service.get_signal_trace(CREATOR, SIGNAL_ID))
    assert set(detail) == {"signal", "viewer_role", "lifecycle_state_source", "degraded"}


def test_an_unidentifiable_caller_withholds():
    client = FakeSupabase([])
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, {}, paper_signal_row()))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_a_signal_with_no_strategy_attribution_withholds():
    client = FakeSupabase([])
    row = paper_signal_row()
    row.pop("strategy_id")
    assert (
        _run_coroutine(resolve_signal_viewer_role(client, CREATOR, row))
        == VIEWER_ROLE_SUBSCRIBER
    )


def test_with_no_database_client_the_local_store_answers_as_it_always_did():
    """The one documented exemption - see ``resolve_signal_viewer_role``'s docstring."""
    assert (
        _run_coroutine(resolve_signal_viewer_role(None, CREATOR, paper_signal_row()))
        == VIEWER_ROLE_OWNER
    )


def test_a_caller_that_has_already_resolved_ownership_may_say_so():
    """The parameter is for a SERVER-side caller; the router passes nothing.

    Both directions, on the SAME row and the same reader, so the parameter is shown to be what
    decided the answer - and in both the ``strategies`` read is skipped, which is the only
    reason for the parameter to exist.
    """
    service, client = service_for([paper_signal_row()])
    restricted = _run_coroutine(
        service.get_signal_trace(
            SUBSCRIBER, SIGNAL_ID, viewer_role=VIEWER_ROLE_SUBSCRIBER
        )
    )
    assert set(restricted) == {"signal", "viewer_role", "lifecycle_state_source", "degraded"}
    assert not [q for q in client.queries if q.table_name == svc.STRATEGY_OWNER_TABLE]

    service, client = service_for([paper_signal_row()])
    full = _run_coroutine(
        service.get_signal_trace(SUBSCRIBER, SIGNAL_ID, viewer_role=VIEWER_ROLE_OWNER)
    )
    assert "trace" in full and "timeline" in full
    assert not [q for q in client.queries if q.table_name == svc.STRATEGY_OWNER_TABLE]


def test_an_unrecognised_viewer_role_is_treated_as_a_non_owner():
    """Anything that is not exactly ``owner`` withholds - the safe direction to be wrong in."""
    service, _ = service_for([paper_signal_row(user_id=CREATOR["id"])])
    detail = _run_coroutine(
        service.get_signal_trace(CREATOR, SIGNAL_ID, viewer_role="administrator")
    )
    assert set(detail) == {"signal", "viewer_role", "lifecycle_state_source", "degraded"}


def test_the_router_declares_no_parameter_that_could_carry_a_role():
    """Requirement 21.1, at the request surface: there is nothing for a client to claim."""
    from backend_app.main import app
    from fastapi.routing import APIRoute

    route = next(
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path == "/api/signal-trace/signals/{signal_id}"
        and "GET" in r.methods
    )
    declared = {p.name for p in route.dependant.query_params}
    assert not declared & {
        "viewer_role",
        "viewer",
        "is_owner",
        "owner_view",
        "role",
        "as_owner",
    }, f"the detail route accepts a role claim from the query string: {sorted(declared)}"


# ══════════════════════════════════════════════════════════════════════════
# 6. A READ RULE THAT NARROWS NOTHING STORED (Requirement 23.2)
# ══════════════════════════════════════════════════════════════════════════


def test_the_row_the_subscriber_read_still_carries_every_recorded_field():
    """29.4 projects at READ time. The audit record keeps all thirteen facts and the JSONB."""
    row = paper_signal_row()
    service, _ = service_for([row])

    _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    for column in (
        "strategy_id",
        "strategy_version",
        "paper_session_id",
        "generated_at",
        "decision",
        "symbol",
        "quantity",
        "order_lifecycle_state",
        "order_id",
        "environment",
        "risk_reason",
        "indicators",
        "market_info",
    ):
        assert column in row, f"{column} was removed from the stored row"
    assert row["indicators"] == {"node_zzq_entry": {"value": 27.44, "ready": True}}


def test_the_projection_does_not_mutate_the_row_it_reads():
    row = paper_signal_row()
    before = json.dumps(row, sort_keys=True, default=str)
    project_subscriber_signal(row)
    assert json.dumps(row, sort_keys=True, default=str) == before


def test_the_owner_of_the_strategy_sees_the_same_signal_the_subscriber_saw_a_subset_of():
    """One record, two views. The subscriber's is a strict subset of the owner's facts."""
    row = paper_signal_row()

    service, _ = service_for([row], strategies=[protected_strategy_row()])
    subscriber_view = _run_coroutine(service.get_signal_trace(SUBSCRIBER, SIGNAL_ID))

    owner_item = signal_trace_item(row)
    projected = subscriber_view["signal"]

    assert projected["decision"] == owner_item["decision"]
    assert projected["quantity"] == owner_item["quantity"]
    assert projected["order_lifecycle_state"] == owner_item["order_lifecycle_state"]
    assert len(projected) < len(owner_item)
