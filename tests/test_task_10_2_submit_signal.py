"""
Task 10.2 - ``submit_signal(signal) -> OrderLifecycleState``.

Requirements 11.2, 11.3, 16.6, 16.7, 19.1, 19.5. Four things are under test and nothing
else:

  1. THE ROUTE. Every submission goes through ``execute_with_idempotency`` under the
     signal's own derived key and with the signal path's result TTL, then through the risk
     validation component, then through the order validation/execution component - and
     through nothing else. An absent or unusable component is a refusal, never an
     approval (Requirement 11.2).

  2. THE TRANSITIONS. Gate, then write, then audit, for every transition including the
     log's genesis row (``NULL -> GENERATED``). An illegal transition writes nothing
     (Requirement 16.6). The log is append-only - no UPDATE, no DELETE ever reaches it
     (Requirement 16.7).

  3. THE OUTCOMES. A risk refusal and an execution refusal both persist ``REJECTED`` and
     submit nothing (Requirement 11.3); a DETERMINATE failure - the venue answered and
     refused - is ``FAILED``, while an INDETERMINATE one - no answer, so the order may be
     live - is HELD at ``PENDING`` for the Requirement 19.2 sweep and writes nothing at
     all; a fill is ``PARTIALLY_EXECUTED``/``EXECUTED`` reached through ``SUBMITTED``.

  4. THE TWO ESCAPE HATCHES. ``DuplicateOrderError`` returns the persisted state rather
     than reporting a failure, including when it arrives as a 23505 on
     ``uq_signals_idempotency_key`` (migration 005b's stated contract); an unavailable
     idempotency store holds the signal at ``PENDING`` and never submits unguarded
     (Requirement 19.5).

Plus one regression test for a pre-existing bug in the layer this task routes through:
``execute_with_idempotency``'s failure path referenced ``lock_token``, a name bound only
inside its retry loop, so any exception from the operation on the common path raised
``UnboundLocalError`` instead of releasing the lock and re-raising the real error.

What is NOT tested here, because it is not this task's:
  * ``generate_signal`` and the record's shape - task 10.1,
    ``tests/test_task_10_1_generate_signal.py``.
  * ``idempotency_key_for``'s determinism and the ``result_ttl`` plumbing - task 9.1,
    ``tests/test_task_9_1_signal_idempotency_key.py``.
  * The Live_Runtime wiring, stale-feed suspension, per-event error containment - 10.3.
  * The crash-recovery sweep (query the exchange by Idempotency_Key on restart) - task 12.
  * The universal-quantifier version of the approved-signal-only claim - task 10.4
    (Property 7).
"""

import asyncio

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleRejected,
    OrderLifecycleState,
)
from backend_app.backend.signal_service import (
    ExecutionOutcome,
    RiskVerdict,
    Signal,
    SignalPersistenceError,
    SignalSubmissionRefused,
    apply_order_lifecycle_state,
    is_duplicate_idempotency_key_error,
    lifecycle_path,
    mint_signal,
    resolve_execution_state,
    submit_signal,
)
from backend_app.core.distributed_idempotency import (
    SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    DuplicateOrderError,
)


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


def deployment_row(**overrides):
    row = {
        "id": "dep-1111",
        "user_id": "user-aaaa",
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "paper",
        "worker_id": "worker-7",
    }
    row.update(overrides)
    return row


def action_output(**overrides):
    output = {
        "decision": "BUY",
        "quantity": 0.25,
        "price": 61234.5,
        "source_node_ids": ["action-1"],
        "closure_ready": True,
        "node_closure": {"rsi-1": 28.4},
        "risk_validation": {"passed": True, "reason": "within limits"},
    }
    output.update(overrides)
    return output


def a_signal(**overrides) -> Signal:
    """A minted (and, for these tests, notionally already-persisted) signal at GENERATED."""
    return mint_signal(deployment_row(), action_output(**overrides))


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class FakeQuery:
    """One chained PostgREST query, recorded in full so the test can assert on it."""

    def __init__(self, client, table, verb, payload=None):
        self.client = client
        self.table = table
        self.verb = verb
        self.payload = payload
        self.filters = []

    # ── filters ──
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
    """A PostgREST stand-in that records every call and can be told to fail specific ones.

    Deliberately records the VERB per table, because the append-only claim in Requirement
    16.7 is a claim about which verbs ever reach ``order_lifecycle_transitions`` - and the
    only way to test "no UPDATE and no DELETE" is to notice one if it happens.
    """

    def __init__(
        self,
        *,
        signals_row=None,
        lifecycle_columns=True,
        transitions_table=True,
        genesis_exists=False,
        raise_on=None,
        error_on=None,
    ):
        self.signals_row = dict(signals_row or {})
        self.lifecycle_columns = lifecycle_columns
        self.transitions_table = transitions_table
        self.genesis_exists = genesis_exists
        #: {(table, verb): "error text"} - raised as an exception.
        self.raise_on = dict(raise_on or {})
        #: {(table, verb): "error text"} - returned as ``result.error``.
        self.error_on = dict(error_on or {})
        self.calls = []

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

    # ── the recorder ──
    def _execute(self, query):
        self.calls.append(
            {
                "table": query.table,
                "verb": query.verb,
                "payload": query.payload,
                "filters": list(query.filters),
            }
        )
        signature = (query.table, query.verb)
        if signature in self.raise_on:
            raise Exception(self.raise_on[signature])
        if signature in self.error_on:
            return _Result(error=self.error_on[signature])

        if query.table == "signals":
            if query.verb == "select":
                columns = str(query.payload)
                if "order_lifecycle_state" in columns and not self.lifecycle_columns:
                    raise Exception(
                        "42703 column signals.order_lifecycle_state does not exist"
                    )
                if columns.strip() == "idempotency_key,order_lifecycle_state":
                    return _Result(data=[])  # the 005b probe
                return _Result(data=[dict(self.signals_row)] if self.signals_row else [])
            if query.verb == "update":
                self.signals_row.update(query.payload)
                return _Result(data=[dict(self.signals_row)])

        if query.table == "order_lifecycle_transitions":
            if not self.transitions_table:
                raise Exception(
                    "PGRST205 Could not find the table "
                    "'public.order_lifecycle_transitions' in the schema cache"
                )
            if query.verb == "select":
                return _Result(data=[{"id": "olt-0"}] if self.genesis_exists else [])
            if query.verb == "insert":
                return _Result(data=[dict(query.payload)])

        return _Result(data=[])

    # ── assertions helpers ──
    def transitions(self):
        return [
            call["payload"]
            for call in self.calls
            if call["table"] == "order_lifecycle_transitions" and call["verb"] == "insert"
        ]

    def state_writes(self):
        return [
            call["payload"]
            for call in self.calls
            if call["table"] == "signals" and call["verb"] == "update"
        ]

    def verbs_on(self, table):
        return {call["verb"] for call in self.calls if call["table"] == table}


class FakeRisk:
    """A risk validation component exposing the explicit per-signal seam."""

    def __init__(self, verdict=None):
        self.verdict = verdict or RiskVerdict(approved=True, reason="within limits")
        self.calls = []

    async def validate_signal(self, signal):
        self.calls.append(signal.id)
        return self.verdict


class FakeExecution:
    """An execution component exposing the explicit per-signal seam."""

    def __init__(self, outcome=None, raises=None):
        self.outcome = outcome or ExecutionOutcome(
            accepted=True, order_id="ord-1", reason="accepted"
        )
        self.raises = raises
        self.calls = []

    async def submit_order(self, signal):
        self.calls.append(signal.id)
        if self.raises is not None:
            raise self.raises
        return self.outcome


class FakeIdempotencyLayer:
    """Records what the signal path asked the real layer for, and can simulate its refusals.

    The locking algorithm itself is NOT re-implemented here - it is reused verbatim and
    ``tests/test_atomic_idempotency_fix.py`` and task 9.1's suite already cover it. What
    this double exists to observe is the three things this task is responsible for: the
    key, the TTL, and that the operation runs exactly once inside the guard.
    """

    def __init__(self, *, raises=None, cached=None):
        self.raises = raises
        self.cached = cached
        self.calls = []
        self.operation_runs = 0

    async def execute_with_idempotency(
        self, *, tenant_id, client_order_id, operation, result_ttl=None, **kwargs
    ):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "client_order_id": client_order_id,
                "result_ttl": result_ttl,
            }
        )
        if self.raises is not None:
            raise self.raises
        if self.cached is not None:
            return self.cached
        self.operation_runs += 1
        return await operation()


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Each test probes 005b for itself: both verdicts are cached per process."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


async def submit(signal, *, sb, risk=None, execution=None, layer=None):
    return await submit_signal(
        signal,
        risk_engine=risk if risk is not None else FakeRisk(),
        execution_engine=execution if execution is not None else FakeExecution(),
        sb=sb,
        idempotency_layer=layer if layer is not None else FakeIdempotencyLayer(),
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE TRANSITION PATH (Requirement 16.4's table, consulted not transcribed)
# ══════════════════════════════════════════════════════════════════════════


def test_lifecycle_path_routes_a_fill_through_submitted():
    """PENDING -> EXECUTED is not an edge; the signal really passes through SUBMITTED."""
    assert lifecycle_path("PENDING", "EXECUTED") == (
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.EXECUTED,
    )
    assert lifecycle_path("PENDING", "PARTIALLY_EXECUTED") == (
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_EXECUTED,
    )


def test_lifecycle_path_is_direct_where_the_table_has_an_edge():
    assert lifecycle_path("GENERATED", "PENDING") == (OrderLifecycleState.PENDING,)
    assert lifecycle_path("PENDING", "REJECTED") == (OrderLifecycleState.REJECTED,)
    assert lifecycle_path("PENDING", "FAILED") == (OrderLifecycleState.FAILED,)


def test_lifecycle_path_honours_the_partial_fill_self_edge_and_nothing_else():
    """Requirement 16.4 asks for that one self-transition explicitly, and only that one."""
    assert lifecycle_path("PARTIALLY_EXECUTED", "PARTIALLY_EXECUTED") == (
        OrderLifecycleState.PARTIALLY_EXECUTED,
    )
    assert lifecycle_path("SUBMITTED", "SUBMITTED") == ()
    assert lifecycle_path("PENDING", "PENDING") == ()


def test_lifecycle_path_reports_a_terminal_state_as_unreachable():
    assert lifecycle_path("REJECTED", "SUBMITTED") == ()
    assert lifecycle_path("CLOSED", "EXECUTED") == ()


# ══════════════════════════════════════════════════════════════════════════
# 2. GATE, THEN WRITE, THEN AUDIT (Requirements 16.6, 16.7)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_transition_writes_the_column_then_the_append_only_log():
    sb = FakeSupabase()
    signal = a_signal()

    moved = await apply_order_lifecycle_state(
        sb, signal, OrderLifecycleState.PENDING, reason="accepted for submission"
    )

    assert moved.order_lifecycle_state is OrderLifecycleState.PENDING
    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED  # frozen original

    # the canonical column, owner-scoped
    (write,) = sb.state_writes()
    assert write == {"order_lifecycle_state": "PENDING"}
    update_call = [c for c in sb.calls if c["verb"] == "update"][0]
    assert ("eq", "id", signal.id) in update_call["filters"]
    assert ("eq", "user_id", signal.user_id) in update_call["filters"]

    # then the audit row
    (row,) = sb.transitions()
    assert row["from_state"] == "GENERATED"
    assert row["to_state"] == "PENDING"
    assert row["signal_id"] == signal.id
    assert row["user_id"] == signal.user_id
    assert row["reason"] == "accepted for submission"
    assert row["occurred_at"]

    # and in that order
    order = [(c["table"], c["verb"]) for c in sb.calls if c["verb"] in ("update", "insert")]
    assert order == [
        ("signals", "update"),
        ("order_lifecycle_transitions", "insert"),
    ]


@pytest.mark.asyncio
async def test_an_illegal_transition_writes_nothing_at_all():
    """Requirement 16.6: the prior value is retained by never reaching a write."""
    sb = FakeSupabase()
    signal = a_signal()

    with pytest.raises(OrderLifecycleRejected) as excinfo:
        await apply_order_lifecycle_state(sb, signal, OrderLifecycleState.EXECUTED)

    detail = excinfo.value.to_detail()
    assert detail["error"] == "ORDER_LIFECYCLE_TRANSITION_INVALID"
    assert detail["order_lifecycle_state"] == "GENERATED"
    assert detail["requested_state"] == "EXECUTED"
    assert "PENDING" in detail["legal_transitions"]
    assert sb.state_writes() == []
    assert sb.transitions() == []


@pytest.mark.asyncio
async def test_a_state_outside_the_vocabulary_is_refused_before_any_write():
    sb = FakeSupabase()
    with pytest.raises(OrderLifecycleRejected) as excinfo:
        await apply_order_lifecycle_state(sb, a_signal(), "HALF_DONE")
    assert excinfo.value.code == "ORDER_LIFECYCLE_STATE_UNRECOGNISED"
    assert sb.calls == []


@pytest.mark.asyncio
async def test_moving_to_the_state_it_is_already_in_writes_no_phantom_transition():
    sb = FakeSupabase()
    signal = a_signal()
    same = await apply_order_lifecycle_state(sb, signal, OrderLifecycleState.GENERATED)
    assert same.order_lifecycle_state is OrderLifecycleState.GENERATED
    assert sb.transitions() == []


@pytest.mark.asyncio
async def test_the_genesis_row_is_null_to_generated_and_written_once():
    sb = FakeSupabase()
    signal = a_signal()

    assert await svc.anchor_generated_transition(sb, signal) is True
    (row,) = sb.transitions()
    assert row["from_state"] is None
    assert row["to_state"] == "GENERATED"
    assert row["occurred_at"] == signal.generated_at

    # A retried submission must not append a second genesis row: the log is append-only,
    # so a duplicate could never be cleaned up afterwards.
    sb.genesis_exists = True
    assert await svc.anchor_generated_transition(sb, signal) is False
    assert len(sb.transitions()) == 1


@pytest.mark.asyncio
async def test_a_null_from_state_is_refused_for_anything_but_generated():
    """The narrow substitute for the gate on the log's one ungateable row."""
    sb = FakeSupabase()
    with pytest.raises(svc.SignalRejected) as excinfo:
        await svc._insert_transition_row(
            sb,
            a_signal(),
            from_state=None,
            to_state=OrderLifecycleState.SUBMITTED,
            reason="smuggled",
            occurred_at="2024-05-01T12:00:00+00:00",
        )
    assert excinfo.value.code == "ORDER_LIFECYCLE_TRANSITION_UNANCHORED"
    assert sb.transitions() == []


@pytest.mark.asyncio
async def test_the_transition_log_is_only_ever_inserted_into():
    """Requirement 16.7 / 005b section 2: SELECT and INSERT policies only."""
    sb = FakeSupabase()
    signal = a_signal()
    await submit(signal, sb=sb)
    assert sb.verbs_on("order_lifecycle_transitions") <= {"select", "insert"}


@pytest.mark.asyncio
async def test_an_absent_transition_log_degrades_and_does_not_fail_the_submission():
    """Requirement 16.7 is conditioned on the audit store being reachable."""
    sb = FakeSupabase(transitions_table=False)
    reached = await submit(a_signal(), sb=sb)
    assert reached is OrderLifecycleState.SUBMITTED
    assert sb.transitions() == []
    assert svc.transitions_table_support_state() is False


@pytest.mark.asyncio
async def test_a_real_audit_write_failure_is_reported_not_swallowed():
    sb = FakeSupabase(
        error_on={("order_lifecycle_transitions", "insert"): "permission denied"}
    )
    with pytest.raises(SignalPersistenceError) as excinfo:
        await apply_order_lifecycle_state(sb, a_signal(), OrderLifecycleState.PENDING)
    assert excinfo.value.code == "ORDER_LIFECYCLE_TRANSITION_NOT_AUDITED"


@pytest.mark.asyncio
async def test_an_absent_005b_column_degrades_the_state_write_with_a_warning(caplog):
    sb = FakeSupabase(lifecycle_columns=False, signals_row={"status": "pending"})
    with caplog.at_level("WARNING"):
        reached = await submit(a_signal(), sb=sb)
    assert reached is OrderLifecycleState.SUBMITTED
    assert svc.SIGNAL_LIFECYCLE_MIGRATION in caplog.text
    # No order_lifecycle_state column reached the row, but the audit trail still did.
    assert all("order_lifecycle_state" not in w for w in sb.state_writes())
    assert [r["to_state"] for r in sb.transitions()] == [
        "GENERATED",
        "PENDING",
        "SUBMITTED",
    ]


# ══════════════════════════════════════════════════════════════════════════
# 3. THE ROUTE (Requirement 11.2) AND THE OUTCOMES (Requirement 11.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_submission_is_guarded_by_the_signals_own_key_and_the_signal_path_ttl():
    """Requirement 19.1. The key is derived, never invented per attempt."""
    sb = FakeSupabase()
    signal = a_signal()
    layer = FakeIdempotencyLayer()

    await submit(signal, sb=sb, layer=layer)

    (call,) = layer.calls
    assert call["client_order_id"] == signal.idempotency_key == f"signal:{signal.id}"
    assert call["tenant_id"] == signal.user_id
    assert call["result_ttl"] == SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    assert layer.operation_runs == 1


@pytest.mark.asyncio
async def test_a_clean_submission_walks_generated_pending_submitted():
    sb = FakeSupabase()
    risk, execution = FakeRisk(), FakeExecution()
    signal = a_signal()

    reached = await submit(signal, sb=sb, risk=risk, execution=execution)

    assert reached is OrderLifecycleState.SUBMITTED
    assert risk.calls == [signal.id]
    assert execution.calls == [signal.id]
    assert [(r["from_state"], r["to_state"]) for r in sb.transitions()] == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
    ]
    assert sb.signals_row["order_id"] == "ord-1"


@pytest.mark.asyncio
async def test_a_full_fill_reaches_executed_through_submitted():
    sb = FakeSupabase()
    execution = FakeExecution(
        ExecutionOutcome(
            accepted=True,
            order_id="ord-2",
            execution_id="exe-2",
            filled=0.25,
            quantity=0.25,
        )
    )
    reached = await submit(a_signal(), sb=sb, execution=execution)

    assert reached is OrderLifecycleState.EXECUTED
    assert [r["to_state"] for r in sb.transitions()] == [
        "GENERATED",
        "PENDING",
        "SUBMITTED",
        "EXECUTED",
    ]
    # The execution reference is written with the state it justifies, not after it.
    assert sb.signals_row["order_id"] == "ord-2"
    assert sb.signals_row["trade_id"] == "exe-2"


@pytest.mark.asyncio
async def test_a_partial_fill_reaches_partially_executed():
    sb = FakeSupabase()
    execution = FakeExecution(
        ExecutionOutcome(accepted=True, order_id="ord-3", filled=0.1, quantity=0.25)
    )
    assert await submit(a_signal(), sb=sb, execution=execution) is (
        OrderLifecycleState.PARTIALLY_EXECUTED
    )


@pytest.mark.asyncio
async def test_an_order_state_word_is_reconciled_through_the_mapping_not_guessed():
    """Requirement 16.2: the reconciliation tables are what translate a source reading."""
    sb = FakeSupabase()
    execution = FakeExecution(
        ExecutionOutcome(accepted=True, order_id="ord-4", order_state="partial")
    )
    assert await submit(a_signal(), sb=sb, execution=execution) is (
        OrderLifecycleState.PARTIALLY_EXECUTED
    )

    # A word outside the OrderState vocabulary is dropped, not mapped to something
    # plausible; the outcome falls back to what the fill quantities actually say.
    sb2 = FakeSupabase()
    execution2 = FakeExecution(
        ExecutionOutcome(accepted=True, order_id="ord-5", order_state="halfway")
    )
    assert await submit(a_signal(), sb=sb2, execution=execution2) is (
        OrderLifecycleState.SUBMITTED
    )


@pytest.mark.asyncio
async def test_a_risk_refusal_persists_rejected_and_submits_nothing():
    """Requirement 11.3."""
    sb = FakeSupabase()
    risk = FakeRisk(RiskVerdict(approved=False, reason="daily loss limit exceeded"))
    execution = FakeExecution()

    reached = await submit(a_signal(), sb=sb, risk=risk, execution=execution)

    assert reached is OrderLifecycleState.REJECTED
    assert execution.calls == []  # nothing reached the execution component
    rows = sb.transitions()
    assert [r["to_state"] for r in rows] == ["GENERATED", "PENDING", "REJECTED"]
    assert "daily loss limit exceeded" in rows[-1]["reason"]


@pytest.mark.asyncio
async def test_an_execution_refusal_persists_rejected():
    """Requirement 11.3 covers the order validation component's refusal too."""
    sb = FakeSupabase()
    execution = FakeExecution(
        ExecutionOutcome(accepted=False, refused=True, reason="below minimum order size")
    )
    reached = await submit(a_signal(), sb=sb, execution=execution)
    assert reached is OrderLifecycleState.REJECTED
    assert "below minimum order size" in sb.transitions()[-1]["reason"]


@pytest.mark.asyncio
async def test_a_determinate_execution_error_persists_failed_and_names_the_cause():
    """The venue ANSWERED and refused, so no order exists and FAILED is the truth.

    ``InvalidOrder`` is ccxt's own name for it and ``classify_execution_failure`` reads the
    exception's TYPE, so a client that raises it - rather than returning a refusal report -
    still lands on Requirement 11.3's terminal state.
    """

    class InvalidOrder(Exception):
        """The venue rejected the order itself."""

    sb = FakeSupabase()
    execution = FakeExecution(raises=InvalidOrder("price below the tick size"))
    reached = await submit(a_signal(), sb=sb, execution=execution)
    assert reached is OrderLifecycleState.FAILED
    assert "price below the tick size" in sb.transitions()[-1]["reason"]


@pytest.mark.asyncio
async def test_an_indeterminate_execution_error_holds_the_signal_at_pending():
    """The order MAY be live, so the record is not closed over it. Requirement 19.2.

    THIS TEST REPLACES an earlier one that asserted ``FAILED`` for exactly this input
    (``ConnectionError("exchange unreachable")``). That disposition was the defect the
    Requirement 19.4 crash-recovery suite found: the venue can accept an order and lose the
    response, the execution seam raises an ordinary network error either way, and a terminal
    ``FAILED`` then strands a live order for good - ``FAILED`` has no outgoing transition,
    so ``recover_signal`` returns ``RECOVERY_TERMINAL`` without ever asking the venue.

    So an indeterminate failure reports ``PENDING``, which is the state the signal already
    reached before the execution call. That means NOTHING is written for it - no state
    change, no transition row - and the signal is left where Requirement 19.2's sweep can
    resolve it against the venue's own record.
    """
    sb = FakeSupabase()
    execution = FakeExecution(raises=ConnectionError("exchange unreachable"))
    reached = await submit(a_signal(), sb=sb, execution=execution)

    assert reached is OrderLifecycleState.PENDING
    assert [(t["from_state"], t["to_state"]) for t in sb.transitions()] == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
    ], "an in-doubt submission writes no transition of its own; it holds"


def test_an_unrecognised_execution_error_defaults_to_indeterminate():
    """The safe direction, pinned. A false PENDING costs a lookup; a false FAILED is final."""

    class SomethingNobodyHasSeen(Exception):
        pass

    assert svc.classify_execution_failure(SomethingNobodyHasSeen("?")) == (
        svc.EXECUTION_FAILURE_INDETERMINATE
    )
    assert svc.classify_execution_failure(TimeoutError("read timed out")) == (
        svc.EXECUTION_FAILURE_INDETERMINATE
    )
    assert svc.classify_execution_failure(OSError("connection reset by peer")) == (
        svc.EXECUTION_FAILURE_INDETERMINATE
    )

    class InsufficientFunds(Exception):
        pass

    assert svc.classify_execution_failure(InsufficientFunds("no")) == (
        svc.EXECUTION_FAILURE_DETERMINATE
    )

    class DeliveredAndRefused(Exception):
        """A client that KNOWS its request was delivered outranks any inference."""

        order_may_be_live = False

    assert svc.classify_execution_failure(DeliveredAndRefused("no")) == (
        svc.EXECUTION_FAILURE_DETERMINATE
    )


def test_the_outcome_shape_reads_an_unstated_failure_as_in_doubt():
    """``failure_kind=None`` means "nobody said", and nobody said is not "no order exists".

    Being terminal has to be argued for; it is not what a caller gets by omission.
    """
    unstated = ExecutionOutcome(accepted=False)
    assert unstated.indeterminate is True
    assert resolve_execution_state(unstated) is OrderLifecycleState.PENDING

    determinate = ExecutionOutcome(
        accepted=False, failure_kind=svc.EXECUTION_FAILURE_DETERMINATE
    )
    assert determinate.indeterminate is False
    assert resolve_execution_state(determinate) is OrderLifecycleState.FAILED

    # A refusal and an acceptance are answers, so neither is ever in doubt.
    assert ExecutionOutcome(accepted=False, refused=True).indeterminate is False
    assert ExecutionOutcome(accepted=True).indeterminate is False


@pytest.mark.asyncio
async def test_an_unreadable_execution_report_holds_rather_than_closing_the_record():
    """A report nobody could read says nothing about whether an order exists.

    The component answered - it returned something - but not in any shape this path can
    read, so "the order is not on the exchange" is not among the things that answer
    establishes. The signal is held; a report that DID state a negative outcome is terminal.
    """
    sb = FakeSupabase()
    reached = await submit(a_signal(), sb=sb, execution=FakeExecution({"note": "who knows"}))
    assert reached is OrderLifecycleState.PENDING
    assert [(t["from_state"], t["to_state"]) for t in sb.transitions()] == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
    ]

    sb2 = FakeSupabase()
    stated = await submit(
        a_signal(),
        sb=sb2,
        execution=FakeExecution({"success": False, "message": "the venue said no"}),
    )
    assert stated is OrderLifecycleState.FAILED
    assert "the venue said no" in sb2.transitions()[-1]["reason"]


@pytest.mark.asyncio
async def test_an_unreadable_risk_report_is_a_refusal_not_an_approval():
    """A component that answered in a shape nobody understands has not said yes."""

    class Vague:
        async def validate_signal(self, signal):
            return {"note": "hmm"}

    sb = FakeSupabase()
    execution = FakeExecution()
    reached = await submit(a_signal(), sb=sb, risk=Vague(), execution=execution)
    assert reached is OrderLifecycleState.REJECTED
    assert execution.calls == []


@pytest.mark.asyncio
async def test_a_missing_risk_engine_is_refused_and_nothing_is_written():
    """Requirement 11.2. An absent validator cannot be read as "no checks configured"."""
    sb = FakeSupabase()
    with pytest.raises(SignalSubmissionRefused) as excinfo:
        await submit_signal(
            a_signal(), risk_engine=None, execution_engine=FakeExecution(), sb=sb
        )
    assert excinfo.value.code == "SIGNAL_RISK_ENGINE_MISSING"
    assert sb.calls == []


@pytest.mark.asyncio
async def test_a_missing_execution_engine_is_refused():
    sb = FakeSupabase()
    with pytest.raises(SignalSubmissionRefused) as excinfo:
        await submit_signal(a_signal(), risk_engine=FakeRisk(), execution_engine=None, sb=sb)
    assert excinfo.value.code == "SIGNAL_EXECUTION_ENGINE_MISSING"
    assert sb.calls == []


@pytest.mark.asyncio
async def test_an_engine_with_no_recognised_seam_is_refused_rather_than_bypassed():
    sb = FakeSupabase()
    with pytest.raises(SignalSubmissionRefused) as excinfo:
        await submit(a_signal(), sb=sb, risk=object())
    assert excinfo.value.code == "SIGNAL_RISK_ENGINE_UNUSABLE"

    sb2 = FakeSupabase()
    with pytest.raises(SignalSubmissionRefused) as excinfo:
        await submit(a_signal(), sb=sb2, execution=object())
    assert excinfo.value.code == "SIGNAL_EXECUTION_ENGINE_UNUSABLE"


@pytest.mark.asyncio
async def test_submission_without_a_database_client_is_refused():
    with pytest.raises(SignalSubmissionRefused) as excinfo:
        await submit_signal(
            a_signal(),
            risk_engine=FakeRisk(),
            execution_engine=FakeExecution(),
            sb=None,
            user=None,
        )
    assert excinfo.value.code == "SIGNAL_NO_PERSISTENCE_CLIENT"


@pytest.mark.asyncio
async def test_the_check_risk_seam_is_accepted_and_its_size_reduction_is_honoured():
    """``dag_risk_integration.RiskIntegratedExecutionPipeline``'s own method shape."""

    class Pipeline:
        def __init__(self):
            self.proposed = None

        def check_risk(self, signal, proposed_size):
            self.proposed = proposed_size
            return {
                "approved": True,
                "adjusted_size": 0.1,
                "reason": "Position size reduced from 0.25 to 0.1",
            }

    class SizeRecordingExecution:
        def __init__(self):
            self.size = None

        async def execute_trade(self, *, size, **kwargs):
            self.size = float(size)
            return {"success": True, "status": "submitted", "order_id": "ord-6"}

    sb = FakeSupabase()
    pipeline, execution = Pipeline(), SizeRecordingExecution()
    reached = await submit(a_signal(), sb=sb, risk=pipeline, execution=execution)

    assert reached is OrderLifecycleState.SUBMITTED
    assert pipeline.proposed == 0.25
    assert execution.size == 0.1


@pytest.mark.asyncio
async def test_the_can_trade_seam_blocks_when_the_portfolio_gate_says_no():
    """``core.risk_engine.RiskEngine``'s portfolio-level gate."""

    class PortfolioRisk:
        def can_trade(self):
            return False

        def check_drawdown(self):
            return False, "Max drawdown exceeded: 21.00% >= 20.00%"

        def check_daily_loss(self):
            return True, "Daily loss OK"

    sb = FakeSupabase()
    execution = FakeExecution()
    reached = await submit(a_signal(), sb=sb, risk=PortfolioRisk(), execution=execution)
    assert reached is OrderLifecycleState.REJECTED
    assert "Max drawdown exceeded" in sb.transitions()[-1]["reason"]
    assert execution.calls == []


# ══════════════════════════════════════════════════════════════════════════
# 4. DUPLICATE SUPPRESSION (Requirement 19.1) AND WITHHOLDING (19.5)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_duplicate_order_error_returns_the_persisted_state_not_a_failure():
    """Requirement 19.1 holding is not a failure - it is the guarantee working."""
    sb = FakeSupabase(signals_row={"order_lifecycle_state": "EXECUTED"})
    layer = FakeIdempotencyLayer(raises=DuplicateOrderError("already in flight"))

    reached = await submit(a_signal(), sb=sb, layer=layer)

    assert reached is OrderLifecycleState.EXECUTED
    # Nothing was re-submitted and no state was overwritten.
    assert sb.state_writes() == []


@pytest.mark.asyncio
async def test_a_cached_result_from_a_prior_attempt_is_reported_verbatim():
    """The layer returns the FIRST attempt's outcome; the operation must not run again."""
    sb = FakeSupabase()
    layer = FakeIdempotencyLayer(
        cached={"signal_id": "s", "order_lifecycle_state": "EXECUTED", "order_id": "ord-9"}
    )
    execution = FakeExecution()

    reached = await submit(a_signal(), sb=sb, layer=layer, execution=execution)

    assert reached is OrderLifecycleState.EXECUTED
    assert layer.operation_runs == 0
    assert execution.calls == []


@pytest.mark.asyncio
async def test_a_23505_on_the_idempotency_index_is_translated_to_duplicate_order_error():
    """Migration 005b section 1's stated contract for the caller of that INSERT."""
    assert is_duplicate_idempotency_key_error(
        Exception(
            '23505: duplicate key value violates unique constraint '
            '"uq_signals_idempotency_key"'
        )
    )
    # Narrow on purpose: a 23505 on any other index is a different fact.
    assert not is_duplicate_idempotency_key_error(
        Exception('23505: duplicate key value violates unique constraint "signals_pkey"')
    )
    assert not is_duplicate_idempotency_key_error(Exception("42703 undefined_column"))

    # And end to end: the INSERT that carries the key raises DuplicateOrderError, which
    # generate_signal's caller can answer with the signal's current state.
    sb = FakeSupabase(
        raise_on={
            ("signals", "insert"): '23505 duplicate key value violates unique constraint '
            '"uq_signals_idempotency_key"'
        }
    )
    with pytest.raises(DuplicateOrderError):
        await svc.generate_signal(deployment_row(), action_output(), sb=sb)


@pytest.mark.asyncio
async def test_an_unavailable_idempotency_store_holds_the_signal_at_pending():
    """Requirement 19.5: withheld, never submitted unguarded, retried under the same key."""
    sb = FakeSupabase(signals_row={"order_lifecycle_state": "GENERATED"})
    layer = FakeIdempotencyLayer(raises=RuntimeError("CRITICAL: Redis unavailable"))
    execution = FakeExecution()

    reached = await submit(a_signal(), sb=sb, layer=layer, execution=execution)

    assert reached is OrderLifecycleState.PENDING
    assert execution.calls == []
    assert [r["to_state"] for r in sb.transitions()] == ["GENERATED", "PENDING"]
    assert "idempotency store unavailable" in sb.transitions()[-1]["reason"]


@pytest.mark.asyncio
async def test_an_unavailable_store_never_moves_a_signal_backwards():
    """Requirement 19.3: an in-flight signal's state is not overwritten with an earlier one.

    The store can fail while STORING the result of a submission that already reached the
    exchange, and writing PENDING over SUBMITTED there would strand a live order behind a
    state that claims it was never placed.
    """
    sb = FakeSupabase(signals_row={"order_lifecycle_state": "SUBMITTED"})
    layer = FakeIdempotencyLayer(raises=RuntimeError("CRITICAL: Redis unavailable"))

    reached = await submit(a_signal(), sb=sb, layer=layer)

    assert reached is OrderLifecycleState.SUBMITTED
    assert sb.state_writes() == []


@pytest.mark.asyncio
async def test_the_persisted_state_is_reconciled_from_legacy_columns_when_005b_is_absent():
    """Requirement 16.2's mapping is what answers when the canonical column does not exist."""
    sb = FakeSupabase(lifecycle_columns=False, signals_row={"status": "accepted"})
    layer = FakeIdempotencyLayer(raises=DuplicateOrderError("already in flight"))
    assert await submit(a_signal(), sb=sb, layer=layer) is OrderLifecycleState.SUBMITTED


@pytest.mark.asyncio
async def test_an_unreconcilable_legacy_row_falls_back_to_the_known_state():
    """A state about real money is never invented; the last known one is reported instead."""
    sb = FakeSupabase(
        lifecycle_columns=False, signals_row={"status": "something-nobody-mapped"}
    )
    layer = FakeIdempotencyLayer(raises=DuplicateOrderError("already in flight"))
    assert await submit(a_signal(), sb=sb, layer=layer) is OrderLifecycleState.GENERATED


# ══════════════════════════════════════════════════════════════════════════
# 5. REGRESSION - the lock release on the failure path
# ══════════════════════════════════════════════════════════════════════════


class _RecordingRedis:
    """The same minimal store task 9.1's suite uses, plus a delete log."""

    def __init__(self):
        self.store = {}
        self.deleted = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False, **kwargs):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.deleted.append(key)
            self.store.pop(key, None)
        return True

    async def eval_lua(self, script, keys, args):
        key = keys[0]
        lock_payload, processing_ttl = args[0], args[1]
        current = self.store.get(key)
        if current is not None and not str(current).startswith("processing"):
            return [1, current]
        if current is None:
            await self.set(key, lock_payload, ex=int(processing_ttl), nx=True)
            return [0, lock_payload]
        return [0, False]


@pytest.mark.asyncio
async def test_an_operation_failure_releases_the_lock_and_reraises_the_real_error(
    monkeypatch,
):
    """Regression: the failure path referenced ``lock_token``, bound only in the retry loop.

    On the COMMON path - the lock acquired atomically on the first try - that name does
    not exist in the frame, so any exception from the operation surfaced as
    ``UnboundLocalError``: the lock stayed held for the full PROCESSING_TTL and the actual
    failure (a risk rejection, an exchange error, a failed write) was replaced by an error
    that named none of it.
    """
    from backend_app.core import distributed_idempotency as di

    redis = _RecordingRedis()
    monkeypatch.setattr(di, "redis_manager", redis)
    layer = di.DistributedIdempotencyLayer()

    async def failing_operation():
        raise ValueError("the real failure, which must not be masked")

    with pytest.raises(ValueError, match="the real failure"):
        await layer.execute_with_idempotency(
            tenant_id="user-aaaa",
            client_order_id="signal:abc",
            operation=failing_operation,
            result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
        )

    key = "idempotency:user-aaaa:signal:abc"
    assert key in redis.deleted, "the processing lock must be released on failure"
    assert key not in redis.store


@pytest.mark.asyncio
async def test_a_released_lock_lets_the_next_attempt_under_the_same_key_proceed(
    monkeypatch,
):
    """The consequence that made the bug expensive: a held lock blocks the retry.

    Requirement 19.5 requires a withheld submission to be retried UNDER THE SAME
    Idempotency_Key. If a failed attempt leaves the lock held, that retry cannot run until
    PROCESSING_TTL expires - so releasing on failure is what makes the retry contract work.
    """
    from backend_app.core import distributed_idempotency as di

    redis = _RecordingRedis()
    monkeypatch.setattr(di, "redis_manager", redis)
    layer = di.DistributedIdempotencyLayer()
    attempts = []

    async def flaky():
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise ConnectionError("exchange unreachable")
        return {"order_lifecycle_state": "SUBMITTED"}

    with pytest.raises(ConnectionError):
        await layer.execute_with_idempotency(
            tenant_id="t", client_order_id="signal:xyz", operation=flaky
        )

    result = await layer.execute_with_idempotency(
        tenant_id="t", client_order_id="signal:xyz", operation=flaky
    )
    assert result == {"order_lifecycle_state": "SUBMITTED"}
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_a_risk_rejection_inside_the_real_layer_surfaces_as_the_state_not_an_error(
    monkeypatch,
):
    """The bug's concrete cost on this path, exercised through the REAL layer.

    Before the fix, a signal whose transition write failed inside the guarded operation
    came back as ``UnboundLocalError`` from the idempotency layer instead of the underlying
    persistence error - so the caller could neither report the real cause nor retry.
    """
    from backend_app.core import distributed_idempotency as di

    redis = _RecordingRedis()
    monkeypatch.setattr(di, "redis_manager", redis)
    layer = di.DistributedIdempotencyLayer()

    sb = FakeSupabase(error_on={("signals", "update"): "permission denied for table signals"})

    with pytest.raises(SignalPersistenceError) as excinfo:
        await submit_signal(
            a_signal(),
            risk_engine=FakeRisk(),
            execution_engine=FakeExecution(),
            sb=sb,
            idempotency_layer=layer,
        )
    assert excinfo.value.code == "SIGNAL_STATE_NOT_PERSISTED"


@pytest.mark.asyncio
async def test_concurrent_submissions_of_one_signal_place_at_most_one_order(monkeypatch):
    """Requirement 19.1, through the real layer: one signal, many attempts, one order."""
    from backend_app.core import distributed_idempotency as di

    redis = _RecordingRedis()
    monkeypatch.setattr(di, "redis_manager", redis)
    layer = di.DistributedIdempotencyLayer()

    signal = a_signal()
    placements = []

    class CountingExecution:
        async def submit_order(self, sig):
            placements.append(sig.id)
            await asyncio.sleep(0)
            return ExecutionOutcome(accepted=True, order_id="ord-once")

    execution = CountingExecution()
    results = await asyncio.gather(
        *(
            submit_signal(
                signal,
                risk_engine=FakeRisk(),
                execution_engine=execution,
                sb=FakeSupabase(signals_row={"order_lifecycle_state": "SUBMITTED"}),
                idempotency_layer=layer,
            )
            for _ in range(5)
        ),
        return_exceptions=True,
    )

    assert len(placements) == 1, f"expected one placement, got {placements}"
    assert all(isinstance(r, OrderLifecycleState) for r in results), results
