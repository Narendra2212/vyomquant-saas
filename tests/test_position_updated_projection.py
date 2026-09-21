"""
tests/test_position_updated_projection.py - vyomquant-ui-redesign BC-6 (task 12.6).

Requirements 9.1, 9.2, 19.1, 19.2. `design.md` §10.1's stage table, §16's BC-6 row.

WHAT THIS FILE EXISTS FOR
-------------------------
Requirement 9.1 names nine stages of a signal's life and the Signal Trace page renders all
nine (9.2: a stage with no record is shown as unavailable, never omitted). Eight had a
backing record. The ninth - "position update" - had none: `PUT /signals/{id}/execution`
accepts `trade_id`, `pnl` and `realized_pnl`, which is a P&L OUTCOME. A P&L figure is not a
position transition, so stage 9 rendered permanently not-available and design.md registered
it as the one place the requirement asked for something the backend did not track at all.

BC-6 records it as a SIXTH `timeline` event, `POSITION_UPDATED`, derived from the same
`public.signals` row the other five come from and gated on the same `executed_at` column
`EXECUTED` is gated on.

THE FOUR LOAD-BEARING GROUPS HERE
    1. `TestTheSpecsNamedVerification` - a filled execution carries EXACTLY ONE
       `POSITION_UPDATED`, positioned AFTER `EXECUTED`; a signal that never executed
       carries NONE. Those are the three clauses task 12.6 names, and the second is why
       the sort in `signal_event_timeline` has to stay stable.

    2. `TestNothingIsFabricated` - the event reports the position CHANGE the row actually
       carries and declares the absolute holding not-available WITH A REASON. This is the
       group that matters most: an event asserting a position nobody reported would be
       worse than the missing stage it replaces. `position_size` (the risk-approved
       INTENDED size) is asserted NOT to be republished as an outcome, and an unreported
       fill is asserted to be named in `not_available` rather than zeroed - a zero fill and
       an unknown fill are different facts about a trader's position.

    3. `TestRequirement19_1NothingExistingMoved` - the five pre-spec events keep their
       names, their order, their gates and their payloads. The five names and payloads are
       TRANSCRIBED here rather than sliced out of the module, following
       `test_task_29_signal_environments.py::THE_THIRTY_FOUR`: an assertion that compares a
       constant with itself proves nothing.

    4. `TestARepeatedExecutionUpdate` - `PUT /signals/{id}/execution` is callable twice.
       Driven through the REAL `SignalService.update_execution` against a `signals` table
       double that answers `update` as well as `select`, so the idempotency claim is about
       the write path and not about a hand-built row.
"""

import inspect
import itertools
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.signal_service import (
    MANUAL_RECONCILIATION_EVENT,
    POSITION_RESULTING_FIELD,
    POSITION_RESULTING_UNREPORTED_REASON,
    POSITION_TRANSITION_FIELDS,
    POSITION_UPDATED_EVENT,
    SIGNAL_TIMELINE_EVENTS,
    SignalService,
    signal_event_timeline,
)

# The row shape, the service double and the transition history task 13.2 already pins, so
# BC-6's assertions are about the same `public.signals` row that suite is about rather than
# about a second, drifting copy of it.
from tests.test_task_13_2_signal_trace_detail import (
    OWNER,
    SIGNAL_ID,
    service_for,
    signal_row,
    transition_rows,
)

# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

#: The five pre-spec `timeline` event names, in the order `signal_event_timeline` derived
#: them before BC-6. Transcribed ON PURPOSE rather than sliced out of
#: `SIGNAL_TIMELINE_EVENTS`: "BC-6 appended and reordered nothing" is only checkable
#: against an independent copy of the list.
THE_FIVE = (
    "SIGNAL_GENERATED",
    "RISK_EVALUATED",
    "ORDER_CREATED",
    "EXCHANGE_RESPONSE",
    "EXECUTED",
)

#: Each pre-spec event's `data` keys, transcribed for the same reason. Requirement 19.1
#: forbids changing an existing event's payload, and this is what makes that checkable.
THE_FIVE_PAYLOAD_KEYS = {
    "SIGNAL_GENERATED": {"decision", "indicators", "market_info"},
    "RISK_EVALUATED": {"risk_passed", "risk_reason", "position_size"},
    "ORDER_CREATED": {"order_id", "quantity"},
    "EXCHANGE_RESPONSE": {"exchange_order_id", "order_status"},
    "EXECUTED": {"trade_id", "pnl"},
}

#: The columns `_position_updated_event` reads. The gate is the first one; the other four
#: are the transition it may describe.
EVENT_INPUT_COLUMNS = ("executed_at", "filled", "average_price", "decision", "symbol")


def timeline_of(**overrides: Any) -> List[Dict[str, Any]]:
    """`signal_event_timeline` for `signal_row()` with `overrides` applied."""
    row = signal_row()
    row.update(overrides)
    return signal_event_timeline(row)


def events_of(timeline: List[Dict[str, Any]]) -> List[str]:
    return [event["event"] for event in timeline]


def position_events(timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in timeline if e["event"] == POSITION_UPDATED_EVENT]


def only_position_event(timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    found = position_events(timeline)
    assert len(found) == 1, f"expected exactly one {POSITION_UPDATED_EVENT}, got {len(found)}"
    return found[0]


# ══════════════════════════════════════════════════════════════════════════
# A `signals` TABLE THAT ANSWERS `update` AS WELL AS `select`
#
# Task 13.2's `FakeSupabase` is a read double - it has no `update`, so
# `update_execution` against it silently falls back to the in-process map and the
# idempotency question would never reach a write. This one applies the update to the row
# it then serves, which is what makes `TestARepeatedExecutionUpdate` a test of the write
# path.
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data: Optional[List[Dict[str, Any]]] = None) -> None:
        self.data = data


class _WritableQuery:
    def __init__(self, client: "WritableSignals", verb: str, payload: Any) -> None:
        self.client = client
        self.verb = verb
        self.payload = payload
        self.predicates: List[Any] = []

    def eq(self, column: str, value: Any) -> "_WritableQuery":
        self.predicates.append(("eq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "_WritableQuery":
        self.predicates.append(("in", column, list(values)))
        return self

    def _matches(self, row: Dict[str, Any]) -> bool:
        for kind, column, value in self.predicates:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
        return True

    def execute(self) -> _Result:
        matched = [row for row in self.client.rows if self._matches(row)]
        if self.verb == "update":
            self.client.updates.append(dict(self.payload))
            for row in matched:
                row.update(self.payload)
        return _Result(data=[dict(row) for row in matched])


class WritableSignals:
    """The `signals` table, readable and writable, plus the two tables the trace read joins."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = [dict(row) for row in rows]
        self.strategies = [{"id": "strat-1", "user_id": OWNER["id"]}]
        self.transitions = transition_rows()
        self.updates: List[Dict[str, Any]] = []
        self._table = ""

    def table(self, name: str) -> "WritableSignals":
        self._table = name
        return self

    def _rows_for(self, name: str) -> List[Dict[str, Any]]:
        if name == "signals":
            return self.rows
        if name == "strategies":
            return self.strategies
        return self.transitions

    def select(self, columns: str) -> "_TableScopedQuery":
        return _TableScopedQuery(self, "select", None, self._rows_for(self._table))

    def update(self, payload: Dict[str, Any]) -> "_TableScopedQuery":
        return _TableScopedQuery(self, "update", payload, self._rows_for(self._table))


class _TableScopedQuery(_WritableQuery):
    """`_WritableQuery` bound to one table's row list."""

    def __init__(self, client: WritableSignals, verb: str, payload: Any, rows) -> None:
        super().__init__(client, verb, payload)
        self._rows = rows

    def order(self, column: str, desc: bool = False) -> "_TableScopedQuery":
        return self

    def limit(self, n: int) -> "_TableScopedQuery":
        return self

    def execute(self) -> _Result:
        matched = [row for row in self._rows if self._matches(row)]
        if self.verb == "update":
            self.client.updates.append(dict(self.payload))
            for row in matched:
                row.update(self.payload)
        return _Result(data=[dict(row) for row in matched])


def writable_service_for(rows):
    service = SignalService()
    client = WritableSignals(rows)

    async def _client(_user):
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


# ══════════════════════════════════════════════════════════════════════════
# 1. THE SPEC'S NAMED VERIFICATION
# ══════════════════════════════════════════════════════════════════════════


class TestTheSpecsNamedVerification:
    """Task 12.6: "exactly one POSITION_UPDATED event after EXECUTED", "none" otherwise."""

    def test_a_filled_execution_carries_exactly_one_position_updated_event(self):
        timeline = timeline_of()

        assert events_of(timeline).count(POSITION_UPDATED_EVENT) == 1

    def test_the_event_comes_after_executed(self):
        """Requirement 9.1's "in order". Stage 9 may not sort ahead of stage 8.

        It carries `EXECUTED`'s own timestamp - there is no later one to carry, and
        inventing one would be a fabricated latency - so this holds because
        `signal_event_timeline`'s sort is STABLE and the event is appended last. A switch to
        an unstable sort, or an insert before the `EXECUTED` branch, fails here.
        """
        events = events_of(timeline_of())

        assert events.index(POSITION_UPDATED_EVENT) > events.index("EXECUTED")
        # And immediately after, with nothing wedged between the two halves of one fill.
        assert events.index(POSITION_UPDATED_EVENT) == events.index("EXECUTED") + 1

    def test_a_signal_that_never_executed_carries_none(self):
        timeline = timeline_of(executed_at=None, trade_id=None, pnl=None, realized_pnl=None)

        assert POSITION_UPDATED_EVENT not in events_of(timeline)
        # The premise: this signal DID reach the exchange, so the absence above is about
        # stage 9 and not about an empty row.
        assert "EXCHANGE_RESPONSE" in events_of(timeline)

    def test_the_event_appears_at_no_earlier_stage(self):
        """Requirement 9.2's "not pending, not claimed": nothing speculative.

        The row is walked forward one stage at a time. `POSITION_UPDATED` must be absent at
        every step until the execution is recorded, including the step where the order came
        back FILLED from the exchange - a fill is not yet a recorded execution.
        """
        stages = [
            {},  # generated only
            {"risk_evaluated_at": "2024-05-01T12:00:01+00:00"},
            {"order_id": "ord-1"},
            {"exchange_order_id": "xch-1", "order_status": "FILLED", "filled": 0.25},
        ]
        row = {
            "id": SIGNAL_ID,
            "symbol": "BTC/USDT",
            "decision": "BUY",
            "generated_at": "2024-05-01T12:00:00+00:00",
        }
        for stage in stages:
            row.update(stage)
            assert POSITION_UPDATED_EVENT not in events_of(signal_event_timeline(row)), (
                f"stage 9 was claimed before the execution was recorded, at {row!r}"
            )

        row["executed_at"] = "2024-05-01T12:00:04+00:00"
        assert POSITION_UPDATED_EVENT in events_of(signal_event_timeline(row))

    @pytest.mark.parametrize(
        "present",
        list(itertools.product([True, False], repeat=len(EVENT_INPUT_COLUMNS))),
        ids=lambda combo: "".join("1" if flag else "0" for flag in combo),
    )
    def test_the_event_is_present_exactly_when_executed_is(self, present):
        """Across every combination of the five columns the event reads.

        Exhaustive rather than sampled - the space is 32 rows - and the assertion is a
        BI-IMPLICATION: `POSITION_UPDATED` and `EXECUTED` are present together or absent
        together, always. That is the structural form of both clauses of the spec's
        verification, and it is what makes "exactly once" a property of the shape rather
        than of a code path.
        """
        overrides = {
            column: (signal_row()[column] if flag else None)
            for column, flag in zip(EVENT_INPUT_COLUMNS, present)
        }
        events = events_of(timeline_of(**overrides))

        assert (POSITION_UPDATED_EVENT in events) == ("EXECUTED" in events)
        assert events.count(POSITION_UPDATED_EVENT) <= 1


# ══════════════════════════════════════════════════════════════════════════
# 2. NOTHING IS FABRICATED
# ══════════════════════════════════════════════════════════════════════════


class TestNothingIsFabricated:
    """Requirement 19.2. The event states what the row reports and nothing more."""

    def test_the_transition_is_read_from_the_row_it_is_derived_from(self):
        row = signal_row()
        data = only_position_event(signal_event_timeline(row))["data"]

        assert data["symbol"] == row["symbol"]
        assert data["direction"] == row["decision"]
        assert data["quantity_delta"] == row["filled"]
        assert data["average_price"] == row["average_price"]
        assert data["trade_id"] == row["trade_id"]
        assert data["realized_pnl"] == row["realized_pnl"]

    def test_the_absolute_resulting_position_is_declared_not_available(self):
        """The honest core of BC-6.

        No column, no request field and no other record in this domain carries the position
        a signal left behind. So the key is PRESENT and `null` - a declared absence a
        consumer can destructure (Requirement 19.2) - it is NAMED in `not_available`, and it
        carries the server's own reason so the page renders that rather than a hardcoded
        frontend sentence.
        """
        data = only_position_event(timeline_of())["data"]

        assert POSITION_RESULTING_FIELD in data
        assert data[POSITION_RESULTING_FIELD] is None
        assert POSITION_RESULTING_FIELD in data["not_available"]
        assert data["not_available_reason"] == POSITION_RESULTING_UNREPORTED_REASON
        assert data["not_available_reason"].strip()

    def test_the_risk_approved_intended_size_is_not_republished_as_an_outcome(self):
        """`position_size` is what risk APPROVED, not what the account ended up holding.

        The two are different numbers whenever a fill is partial, so the row is built with
        a partial fill and the event is required to report the FILL and to leave the
        resulting position unavailable - not to quietly publish the intended size as the
        outcome.
        """
        row = signal_row()
        row["position_size"] = 0.25
        row["filled"] = 0.10
        data = only_position_event(signal_event_timeline(row))["data"]

        assert data["quantity_delta"] == 0.10
        assert data[POSITION_RESULTING_FIELD] is None
        assert 0.25 not in [data[field] for field in POSITION_TRANSITION_FIELDS]

    def test_an_unreported_fill_is_named_not_available_rather_than_zeroed(self):
        """An unknown fill and a zero fill are different facts about a position."""
        data = only_position_event(timeline_of(filled=None))["data"]

        assert data["quantity_delta"] is None
        assert "quantity_delta" in data["not_available"]

    def test_a_genuine_zero_fill_is_reported_as_zero_rather_than_unavailable(self):
        """The inverse of the above, so "not available" cannot be the spelling of "0"."""
        data = only_position_event(timeline_of(filled=0.0))["data"]

        assert data["quantity_delta"] == 0.0
        assert "quantity_delta" not in data["not_available"]

    def test_every_unreported_transition_member_is_named_and_no_reported_one_is(self):
        row = signal_row()
        row.update({"filled": None, "average_price": None})
        data = only_position_event(signal_event_timeline(row))["data"]

        assert set(data["not_available"]) == {
            "quantity_delta",
            "average_price",
            POSITION_RESULTING_FIELD,
        }
        # Present-and-reported members are absent from the list, so a consumer branching on
        # it renders real values as real values.
        assert "symbol" not in data["not_available"]
        assert "direction" not in data["not_available"]

    def test_no_transition_member_is_ever_absent_from_the_payload(self):
        """Requirement 19.2: unavailable is a value, never a missing key."""
        row = {k: None for k in signal_row()}
        row["executed_at"] = "2024-05-01T12:00:04+00:00"
        row["generated_at"] = "2024-05-01T12:00:00+00:00"
        data = only_position_event(signal_event_timeline(row))["data"]

        for field in POSITION_TRANSITION_FIELDS + (POSITION_RESULTING_FIELD,):
            assert field in data, f"{field} is absent rather than declared unavailable"


# ══════════════════════════════════════════════════════════════════════════
# 3. REQUIREMENT 19.1 - NOTHING EXISTING MOVED
# ══════════════════════════════════════════════════════════════════════════


class TestRequirement19_1NothingExistingMoved:
    """Additive means additive: no rename, no reorder, no repointed consumer."""

    def test_the_five_pre_spec_events_keep_their_names_and_relative_order(self):
        events = events_of(timeline_of())

        assert tuple(e for e in events if e in THE_FIVE) == THE_FIVE

    def test_the_five_pre_spec_payloads_are_unchanged(self):
        for event in timeline_of():
            if event["event"] in THE_FIVE_PAYLOAD_KEYS:
                assert set(event["data"]) == THE_FIVE_PAYLOAD_KEYS[event["event"]], (
                    f"{event['event']}'s payload changed; Requirement 19.1 forbids it"
                )

    def test_the_vocabulary_constant_appends_rather_than_reorders(self):
        assert SIGNAL_TIMELINE_EVENTS[: len(THE_FIVE)] == THE_FIVE
        assert SIGNAL_TIMELINE_EVENTS[-1] == POSITION_UPDATED_EVENT
        assert len(SIGNAL_TIMELINE_EVENTS) == len(THE_FIVE) + 1

    def test_the_new_name_collides_with_nothing(self):
        """There is no fixed enum to extend here - the five were inline literals - so the
        only collision risk is a name already in use. Both vocabularies are checked: the
        derived `timeline` one and `public.signal_events.event_type`, which is a SEPARATE,
        persisted vocabulary and is untouched by BC-6."""
        assert POSITION_UPDATED_EVENT not in THE_FIVE
        assert POSITION_UPDATED_EVENT != MANUAL_RECONCILIATION_EVENT

    def test_every_event_the_timeline_can_emit_is_in_the_declared_vocabulary(self):
        assert set(events_of(timeline_of())) <= set(SIGNAL_TIMELINE_EVENTS)

    def test_the_execution_request_contract_gained_no_field(self):
        """Task 12.6's constraint: `PUT /signals/{id}/execution` keeps its request shape."""
        from backend_app.routers.signal_trace import ExecutionUpdateRequest

        fields = ExecutionUpdateRequest.model_fields
        assert set(fields) == {"trade_id", "pnl", "realized_pnl"}
        assert {name for name, f in fields.items() if f.is_required()} == {"trade_id", "pnl"}

    def test_the_service_write_signature_gained_no_parameter(self):
        parameters = inspect.signature(SignalService.update_execution).parameters
        assert list(parameters) == [
            "self",
            "user",
            "signal_id",
            "trade_id",
            "pnl",
            "realized_pnl",
        ]


# ══════════════════════════════════════════════════════════════════════════
# 4. THE WRITE PATH, AND A SECOND CALL TO IT
# ══════════════════════════════════════════════════════════════════════════


class TestARepeatedExecutionUpdate:
    """`PUT /signals/{id}/execution` is callable twice; the timeline must not double."""

    @pytest.mark.asyncio
    async def test_one_execution_update_produces_one_event(self):
        row = signal_row()
        row.update({"executed_at": None, "trade_id": None, "pnl": None, "realized_pnl": None})
        service, client = writable_service_for([row])

        assert POSITION_UPDATED_EVENT not in events_of(
            await service.get_signal_timeline(OWNER, SIGNAL_ID)
        )

        await service.update_execution(
            user=OWNER, signal_id=SIGNAL_ID, trade_id="trd-1", pnl=42.0, realized_pnl=42.0
        )

        timeline = await service.get_signal_timeline(OWNER, SIGNAL_ID)
        assert events_of(timeline).count(POSITION_UPDATED_EVENT) == 1
        assert client.updates, "the write never reached the table double"

    @pytest.mark.asyncio
    async def test_a_repeated_execution_update_leaves_exactly_one_event(self):
        """The idempotency question, answered structurally.

        The second call OVERWRITES `executed_at` rather than appending anything, and the
        timeline is a derivation from that one column - so two writes cannot become two
        events. An append-on-write design would need its own guard here; this one does not.
        """
        row = signal_row()
        row.update({"executed_at": None, "trade_id": None, "pnl": None, "realized_pnl": None})
        service, client = writable_service_for([row])

        for _ in range(3):
            await service.update_execution(
                user=OWNER, signal_id=SIGNAL_ID, trade_id="trd-1", pnl=42.0, realized_pnl=42.0
            )

        timeline = await service.get_signal_timeline(OWNER, SIGNAL_ID)

        assert len(client.updates) == 3, "the premise is three writes, not one"
        assert events_of(timeline).count(POSITION_UPDATED_EVENT) == 1
        assert events_of(timeline).count("EXECUTED") == 1

    @pytest.mark.asyncio
    async def test_the_repeat_reports_the_latest_execution_not_the_first(self):
        """Overwrite semantics, stated: the surviving event is the newest write's."""
        row = signal_row()
        row.update({"executed_at": None, "trade_id": None, "pnl": None, "realized_pnl": None})
        service, _ = writable_service_for([row])

        await service.update_execution(
            user=OWNER, signal_id=SIGNAL_ID, trade_id="trd-first", pnl=1.0, realized_pnl=1.0
        )
        await service.update_execution(
            user=OWNER, signal_id=SIGNAL_ID, trade_id="trd-second", pnl=2.0, realized_pnl=2.0
        )

        data = only_position_event(await service.get_signal_timeline(OWNER, SIGNAL_ID))["data"]
        assert data["trade_id"] == "trd-second"
        assert data["realized_pnl"] == 2.0


# ══════════════════════════════════════════════════════════════════════════
# 5. BOTH READ SURFACES CARRY IT
# ══════════════════════════════════════════════════════════════════════════


class TestBothReadSurfaces:
    """`GET /signals/{id}/timeline` and `GET /signals/{id}` build the same timeline."""

    @pytest.mark.asyncio
    async def test_the_timeline_endpoints_service_call_carries_the_event(self):
        service, _ = service_for([signal_row()])

        timeline = await service.get_signal_timeline(OWNER, SIGNAL_ID)

        assert events_of(timeline).count(POSITION_UPDATED_EVENT) == 1

    @pytest.mark.asyncio
    async def test_the_trace_detail_carries_the_same_event(self):
        """Stage 9 has to reach the page through the detail read, which is what
        `SignalTrace.jsx` calls - not only through the standalone timeline endpoint."""
        service, _ = service_for([signal_row()], transitions=transition_rows())

        detail = await service.get_signal_trace(OWNER, SIGNAL_ID)

        events = events_of(detail["timeline"])
        assert events.count(POSITION_UPDATED_EVENT) == 1
        assert events.index(POSITION_UPDATED_EVENT) == events.index("EXECUTED") + 1
