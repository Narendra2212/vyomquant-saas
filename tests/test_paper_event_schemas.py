"""
tests/test_paper_event_schemas.py - tasks 26.1 and 26.2.

Spec: marketplace-subscriptions-paper-trading tasks 26.1, 26.2. ``design.md`` ->
"``paper/paper_events.py`` and the Paper_Channel". Requirements 19.2, 19.3, 19.7, 26.3.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **Sixteen event types, and no seventeenth** (Requirement 19.2). Asserted on the count, on the
   exact value set, and - the assertion that actually matters - on **set equality with 009's
   ``chk_paper_event_type``**, parsed out of the migration file itself. The migration comment says
   these "MUST equal the type set ``paper_events.py`` emits"; this file is what makes that a
   mechanical fact rather than a comment. A seventeenth Python member would be a ``23514`` in
   production and is a failure here instead.

2. **Every payload model refuses an unlisted field.** ``design.md``'s payload table is the
   contract, and a model that silently accepted an extra key would let a producer put a node id,
   an indicator value or a token in a frame that a subscriber then renders. ``extra="forbid"`` is
   asserted per model, not once for the family.

3. **``signal_generated`` carries exactly nine fields** (Requirement 19.7). The requirement is a
   prohibition - no node id, no indicator value, no feature value, no ML detail - so it is
   asserted as an exhaustive field set rather than as the absence of a few names somebody
   remembered.

4. **No payload carries a credential, token or payment reference** (Requirements 19.7, 26.4),
   checked over the field names of all sixteen models against a deny-list.

5. **The envelope's eight fields, and the microsecond timestamp** (Requirement 19.3).

6. **The sequence allocator over PostgREST** (Requirement 19.3, task 26.2). Three separate
   claims, because they are three separate mechanisms: the compare-and-swap on
   ``paper_sessions.event_sequence`` increments contiguously from 1; a writer that moves the row
   between the read and the swap makes the swap match zero rows and the allocator retries rather
   than reusing the number; and a sequence that nonetheless collides is refused by
   ``uq_paper_event_seq`` at insert time. That last one is the guarantee this transport actually
   delivers - see ``paper_events.next_sequence``'s docstring for the residual gap.

THE PERSISTENCE_LAYER DOUBLE
----------------------------
``tests/test_paper_repository.FakeSupabase``, the one double this repository has, which enforces
``uq_paper_event_seq`` and ``uq_paper_event_id`` itself. No second double and no mock of the
repository: every statement observed here is issued by the code production issues it from.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest
from pydantic import BaseModel, ValidationError

from backend_app.backend.paper import paper_events as events
from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from tests.test_paper_repository import FakeSupabase

USER = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"
SYMBOL = "BTC/USDT"

NOW = datetime(2025, 6, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)

MIGRATION = Path(__file__).resolve().parents[1] / "backend_app" / "migrations" / (
    "009_paper_trading.sql"
)

#: The sixteen, verbatim from Requirement 19.2 and in 009's order. Spelled out once, here, so the
#: assertions below compare the implementation against the *requirement* rather than against
#: itself.
REQUIREMENT_19_2_TYPES = (
    "paper_session_started",
    "paper_session_paused",
    "paper_session_resumed",
    "paper_session_stopped",
    "market_tick",
    "signal_generated",
    "paper_order_created",
    "paper_order_accepted",
    "paper_order_partially_filled",
    "paper_order_filled",
    "paper_order_rejected",
    "paper_position_updated",
    "paper_balance_updated",
    "paper_pnl_updated",
    "paper_drawdown_updated",
    "paper_error",
)

#: Substrings that must not appear in any payload field name (Requirements 19.7, 26.4).
FORBIDDEN_FIELD_SUBSTRINGS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "private_key",
    "payment",
    "card",
    "invoice",
    "charge",
    "stripe",
    "checkout",
    "access_key",
    "bearer",
)


def _session_row(**overrides: Any) -> Dict[str, Any]:
    """One ``paper_sessions`` row, as the session-start path would create it."""
    row: Dict[str, Any] = {
        "id": SESSION,
        "user_id": USER,
        "environment": "PAPER",
        "session_state": "RUNNING",
        "exchange_id": "binance",
        "symbol": SYMBOL,
        "timeframe": "1m",
        "market_data_source": "validated_ohlcv_pipeline",
        "feed_state": "HEALTHY",
        "feed_transport": "WEBSOCKET",
        "event_sequence": 0,
    }
    row.update(overrides)
    return row


def _client(**kwargs: Any) -> FakeSupabase:
    repo.reset_persistence_probe()
    return FakeSupabase(sessions=[_session_row()], **kwargs)


def _check_constraint_types(block: str) -> List[str]:
    """Every quoted literal inside one ``chk_paper_event_type CHECK (... IN (...))``."""
    return re.findall(r"'([a-z_]+)'", block)


def _migration_check_blocks() -> List[List[str]]:
    """Both spellings of ``chk_paper_event_type`` in 009 - the inline one and section 12b's."""
    text = MIGRATION.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(
        r"chk_paper_event_type\s+CHECK\s*\(\s*event_type\s+IN\s*\((.*?)\)\s*\)",
        text,
        re.DOTALL,
    )
    assert blocks, "009 no longer declares chk_paper_event_type; this test cannot be trusted"
    return [_check_constraint_types(block) for block in blocks]


# ══════════════════════════════════════════════════════════════════════════
# 1. THE SIXTEEN TYPES (Requirement 19.2)
# ══════════════════════════════════════════════════════════════════════════


class TestTheSixteenEventTypes:
    def test_the_enum_holds_exactly_sixteen_members(self) -> None:
        assert len(events.PaperEvent) == 16
        assert len(events.PAPER_CHANNEL_EVENTS) == 16
        assert len(events.PAPER_EVENT_TYPES) == 16

    def test_the_members_are_requirement_19_2s_sixteen_in_009s_order(self) -> None:
        assert events.PAPER_EVENT_TYPES == REQUIREMENT_19_2_TYPES
        assert events.PAPER_CHANNEL_EVENTS == set(REQUIREMENT_19_2_TYPES)

    def test_the_vocabulary_is_derived_from_the_enum_and_not_restated(self) -> None:
        """One definition. A second list is a second thing to forget to change."""
        assert events.PAPER_EVENT_TYPES == tuple(m.value for m in events.PaperEvent)
        assert events.PAPER_CHANNEL_EVENTS == frozenset(
            m.value for m in events.PaperEvent
        )

    def test_the_schema_version_is_paper_v1(self) -> None:
        assert events.PAPER_EVENT_SCHEMA_VERSION == "paper.v1"

    def test_the_check_constraints_value_list_and_the_vocabulary_are_the_same_set(
        self,
    ) -> None:
        """009's comment says these MUST be equal. This is what makes that mechanical.

        Both spellings in the migration are checked - the inline ``CONSTRAINT`` in section 12's
        ``CREATE TABLE`` and the guarded ``ALTER TABLE`` in 12b - because a database created before
        12b existed takes the second one, and a divergence between them would mean two databases
        with two different vocabularies.
        """
        for declared in _migration_check_blocks():
            assert set(declared) == set(events.PAPER_CHANNEL_EVENTS)
            assert len(declared) == 16

    def test_the_repository_and_the_feed_import_the_same_vocabulary(self) -> None:
        """Requirement 19.2 has one vocabulary, so the three modules hold one object.

        ``paper_repository.PAPER_EVENT_TYPES`` validates the ``event_type`` column and
        ``paper_market_feed`` writes one of these records. Both now read this module rather than
        spelling the list a second and third time, and ``is`` - not ``==`` - is what says so.
        """
        assert repo.PAPER_EVENT_TYPES is events.PAPER_EVENT_TYPES
        assert feed.PAPER_EVENT_SCHEMA_VERSION is events.PAPER_EVENT_SCHEMA_VERSION
        assert feed.PAPER_ERROR_EVENT_TYPE == events.PaperEvent.ERROR.value

    def test_every_type_has_exactly_one_payload_model(self) -> None:
        assert set(events.PAYLOAD_MODEL_FOR_EVENT) == set(events.PAPER_CHANNEL_EVENTS)
        for event_type, model in events.PAYLOAD_MODEL_FOR_EVENT.items():
            assert isinstance(model, type) and issubclass(model, BaseModel), event_type

    def test_the_member_values_are_readable_as_plain_strings(self) -> None:
        """A ``(str, Enum)`` member is what ``_require_text`` and ``_one_of`` accept."""
        for member in events.PaperEvent:
            assert isinstance(member, str)
            assert member == member.value


# ══════════════════════════════════════════════════════════════════════════
# 2. THE PAYLOAD SCHEMAS (Requirement 19.7, design.md's payload table)
# ══════════════════════════════════════════════════════════════════════════

#: ``design.md`` -> "Payload schemas, one per type", transcribed field for field.
DESIGN_PAYLOAD_FIELDS: Dict[str, Set[str]] = {
    "paper_session_started": {
        "session_id",
        "strategy_ref",
        "symbol",
        "timeframe",
        "initial_capital_minor",
        "currency",
        "market_data_source",
        "feed_transport",
        "config_digest",
        "started_at",
        "actor_id",
    },
    "paper_session_paused": {"session_id", "session_state", "at", "actor_id"},
    "paper_session_resumed": {"session_id", "session_state", "at", "actor_id"},
    "paper_session_stopped": {
        "session_id",
        "session_state",
        "at",
        "actor_id",
        "final_metrics",
    },
    "market_tick": {
        "symbol",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source_event_id",
        "latency_ms",
        "feed_state",
    },
    "signal_generated": {
        "signal_id",
        "decision",
        "symbol",
        "side",
        "quantity",
        "price",
        "order_lifecycle_state",
        "generated_at",
        "environment",
    },
    "paper_position_updated": {
        "symbol",
        "side",
        "size",
        "entry_price",
        "current_price",
        "unrealized_pnl",
        "price_at",
        "stale",
    },
    "paper_balance_updated": {
        "available_balance",
        "locked_balance",
        "total_equity",
        "currency",
        "stale",
    },
    "paper_pnl_updated": {
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "total_return_pct",
        "price_at",
        "stale",
    },
    "paper_drawdown_updated": {
        "max_drawdown_amount",
        "max_drawdown_fraction",
        "peak_equity",
        "snapshot_count",
    },
    "paper_error": {"code", "message", "recoverable", "at"},
}

_ORDER_FIELDS = {
    "order_id",
    "symbol",
    "side",
    "order_type",
    "quantity",
    "limit_price",
    "order_state",
    "filled_quantity",
    "avg_fill_price",
    "fee_minor",
    "slippage_minor",
    "rejection_reason",
    "at",
}
for _order_event in (
    "paper_order_created",
    "paper_order_accepted",
    "paper_order_partially_filled",
    "paper_order_filled",
    "paper_order_rejected",
):
    DESIGN_PAYLOAD_FIELDS[_order_event] = set(_ORDER_FIELDS)


#: One valid payload per type. **These live here and not in ``paper_events``** - Requirement 28.1
#: keeps sample data out of production code, and a module-level examples dict beside the models
#: would be exactly the "sample data on a production code path" it forbids.
EXAMPLES: Dict[str, Dict[str, Any]] = {
    "paper_session_started": {
        "session_id": SESSION,
        "strategy_ref": "strategy-1@v3",
        "symbol": SYMBOL,
        "timeframe": "1m",
        "initial_capital_minor": 10_000_000,
        "currency": "USD",
        "market_data_source": "validated_ohlcv_pipeline",
        "feed_transport": "WEBSOCKET",
        "config_digest": "sha256:abc",
        "started_at": NOW,
        "actor_id": USER,
    },
    "paper_session_paused": {
        "session_id": SESSION,
        "session_state": "PAUSED",
        "at": NOW,
        "actor_id": USER,
    },
    "paper_session_resumed": {
        "session_id": SESSION,
        "session_state": "RUNNING",
        "at": NOW,
        "actor_id": USER,
    },
    "paper_session_stopped": {
        "session_id": SESSION,
        "session_state": "STOPPED",
        "at": NOW,
        "actor_id": USER,
        "final_metrics": {"realized_pnl": Decimal("12.5")},
    },
    "market_tick": {
        "symbol": SYMBOL,
        "timestamp": NOW,
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("12.25"),
        "source_event_id": "sha256:candle",
        "latency_ms": Decimal("42.125"),
        "feed_state": "HEALTHY",
    },
    "signal_generated": {
        "signal_id": "sig-1",
        "decision": "BUY",
        "symbol": SYMBOL,
        "side": "buy",
        "quantity": Decimal("0.5"),
        "price": Decimal("100.5"),
        "order_lifecycle_state": "GENERATED",
        "generated_at": NOW,
        "environment": "PAPER",
    },
    "paper_position_updated": {
        "symbol": SYMBOL,
        "side": "LONG",
        "size": Decimal("0.5"),
        "entry_price": Decimal("100.5"),
        "current_price": Decimal("101"),
        "unrealized_pnl": Decimal("0.25"),
        "price_at": NOW,
        "stale": False,
    },
    "paper_balance_updated": {
        "available_balance": Decimal("99949.75"),
        "locked_balance": Decimal("0"),
        "total_equity": Decimal("100000.25"),
        "currency": "USD",
        "stale": False,
    },
    "paper_pnl_updated": {
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0.25"),
        "total_pnl": Decimal("0.25"),
        "total_return_pct": Decimal("0.00025"),
        "price_at": NOW,
        "stale": False,
    },
    "paper_drawdown_updated": {
        "max_drawdown_amount": Decimal("15.5"),
        "max_drawdown_fraction": Decimal("0.000155"),
        "peak_equity": Decimal("100015.75"),
        "snapshot_count": 12,
    },
    "paper_error": {
        "code": "FEED_DISCONNECTED",
        "message": "The market data feed disconnected; the session is not filling orders.",
        "recoverable": True,
        "at": NOW,
    },
}

for _order_event in (
    "paper_order_created",
    "paper_order_accepted",
    "paper_order_partially_filled",
    "paper_order_filled",
    "paper_order_rejected",
):
    EXAMPLES[_order_event] = {
        "order_id": "ord-1",
        "symbol": SYMBOL,
        "side": "buy",
        "order_type": "limit",
        "quantity": Decimal("0.5"),
        "limit_price": Decimal("100"),
        "order_state": "FILLED",
        "filled_quantity": Decimal("0.5"),
        "avg_fill_price": Decimal("100"),
        "fee_minor": 5,
        "slippage_minor": 0,
        "rejection_reason": None,
        "at": NOW,
    }


class TestThePayloadSchemas:
    def test_the_transcribed_table_covers_all_sixteen(self) -> None:
        """A guard on this file: an event whose payload nobody transcribed is untested."""
        assert set(DESIGN_PAYLOAD_FIELDS) == set(events.PAPER_CHANNEL_EVENTS)

    @pytest.mark.parametrize("event_type", sorted(REQUIREMENT_19_2_TYPES))
    def test_each_model_declares_exactly_the_designs_fields(
        self, event_type: str
    ) -> None:
        model = events.PAYLOAD_MODEL_FOR_EVENT[event_type]
        assert set(model.model_fields) == DESIGN_PAYLOAD_FIELDS[event_type], event_type

    @pytest.mark.parametrize("event_type", sorted(REQUIREMENT_19_2_TYPES))
    def test_each_model_rejects_an_unlisted_field(self, event_type: str) -> None:
        """``extra="forbid"``, per model. An accepted extra key is a leak waiting to happen."""
        model = events.PAYLOAD_MODEL_FOR_EVENT[event_type]
        assert model.model_config.get("extra") == "forbid", event_type

        with pytest.raises(ValidationError) as caught:
            model.model_validate(
                {**EXAMPLES[event_type], "node_id": "n-1"}
            )
        assert "node_id" in str(caught.value)

    def test_signal_generated_carries_no_node_indicator_feature_or_ml_detail(
        self,
    ) -> None:
        """Requirement 19.7, asserted as an exhaustive field set.

        The requirement is a prohibition, and a prohibition checked by listing a few names
        somebody remembered is a prohibition that the next field added will slip past.
        """
        fields = set(events.SignalGeneratedPayload.model_fields)
        assert fields == {
            "signal_id",
            "decision",
            "symbol",
            "side",
            "quantity",
            "price",
            "order_lifecycle_state",
            "generated_at",
            "environment",
        }
        for banned in (
            "node_id",
            "node",
            "indicator",
            "indicator_value",
            "feature",
            "feature_value",
            "ml",
            "ml_detail",
            "inference",
            "confidence",
            "weights",
            "compiled_plan",
            "blueprint",
            "risk_rule",
        ):
            assert banned not in fields

    def test_signal_generated_is_labelled_paper_and_nothing_else(self) -> None:
        payload = events.SignalGeneratedPayload.model_validate(
            EXAMPLES["signal_generated"]
        )
        assert payload.environment == "PAPER"
        for bad in ("LIVE", "BACKTEST", "paper", ""):
            with pytest.raises(ValidationError):
                events.SignalGeneratedPayload.model_validate(
                    {**EXAMPLES["signal_generated"], "environment": bad}
                )

    def test_no_payload_field_names_a_credential_token_or_payment_reference(
        self,
    ) -> None:
        for event_type, model in sorted(events.PAYLOAD_MODEL_FOR_EVENT.items()):
            for field in model.model_fields:
                lowered = field.lower()
                for banned in FORBIDDEN_FIELD_SUBSTRINGS:
                    assert banned not in lowered, f"{event_type}.{field}"

    @pytest.mark.parametrize(
        "event_type,field",
        [
            ("market_tick", "close"),
            ("signal_generated", "quantity"),
            ("paper_order_filled", "quantity"),
            ("paper_position_updated", "entry_price"),
            ("paper_balance_updated", "total_equity"),
            ("paper_pnl_updated", "realized_pnl"),
            ("paper_drawdown_updated", "peak_equity"),
        ],
    )
    def test_a_money_or_price_field_refuses_a_float(
        self, event_type: str, field: str
    ) -> None:
        """Requirement 18.1: a ``float`` price is a price the session cannot reproduce."""
        model = events.PAYLOAD_MODEL_FOR_EVENT[event_type]
        with pytest.raises(ValidationError):
            model.model_validate({**EXAMPLES[event_type], field: 1.25})

    def test_a_money_field_round_trips_as_an_exact_decimal_string(self) -> None:
        payload = events.PaperBalanceUpdatedPayload.model_validate(
            {
                "available_balance": Decimal("1000.0000000001"),
                "locked_balance": Decimal("0"),
                "total_equity": Decimal("1000.0000000001"),
                "currency": "USD",
                "stale": False,
            }
        )
        rendered = events.payload_jsonb(payload)
        assert rendered["available_balance"] == "1000.0000000001"
        assert rendered["locked_balance"] == "0"

    def test_an_unavailable_price_stays_absent_rather_than_becoming_zero(self) -> None:
        """Requirement 28.5, on the two payloads that carry a price that may not exist yet."""
        position = events.PaperPositionUpdatedPayload.model_validate(
            {
                **EXAMPLES["paper_position_updated"],
                "current_price": None,
                "unrealized_pnl": None,
                "price_at": None,
                "stale": True,
            }
        )
        rendered = events.payload_jsonb(position)
        assert rendered["current_price"] is None
        assert rendered["unrealized_pnl"] is None
        assert rendered["stale"] is True

    def test_every_example_payload_validates_against_its_own_model(self) -> None:
        """The examples are used by the assertions above, so they have to be real."""
        assert set(EXAMPLES) == set(events.PAPER_CHANNEL_EVENTS)
        assert not hasattr(events, "EXAMPLE_PAYLOADS"), (
            "Requirement 28.1 keeps sample data in test code; the examples live in this file"
        )
        for event_type, example in sorted(EXAMPLES.items()):
            model = events.PAYLOAD_MODEL_FOR_EVENT[event_type]
            assert isinstance(model.model_validate(example), model), event_type


# ══════════════════════════════════════════════════════════════════════════
# 3. THE ENVELOPE (Requirement 19.3)
# ══════════════════════════════════════════════════════════════════════════


class TestTheEnvelope:
    def test_it_carries_the_designs_eight_fields_and_no_others(self) -> None:
        assert events.ENVELOPE_FIELDS == (
            "schema_version",
            "channel",
            "session_id",
            "type",
            "sequence",
            "event_id",
            "emitted_at",
            "payload",
        )
        assert tuple(events.PaperEventEnvelope.model_fields) == events.ENVELOPE_FIELDS

    def test_the_frame_is_the_designs_json_shape(self) -> None:
        envelope = events.build_envelope(
            session_id=SESSION,
            event_type=events.PaperEvent.ORDER_FILLED,
            payload=EXAMPLES["paper_order_filled"],
            sequence=42,
            emitted_at=NOW,
            event_id="e-1",
        )
        frame = envelope.frame()

        assert list(frame) == list(events.ENVELOPE_FIELDS)
        assert frame["schema_version"] == "paper.v1"
        assert frame["channel"] == f"paper.{SESSION}"
        assert frame["session_id"] == SESSION
        assert frame["type"] == "paper_order_filled"
        assert frame["sequence"] == 42
        assert frame["event_id"] == "e-1"
        assert frame["emitted_at"] == "2025-06-15T12:00:00.123456Z"
        assert isinstance(frame["payload"], dict)

    def test_the_emitted_at_is_utc_at_microsecond_resolution(self) -> None:
        """009 declares ``emitted_at TIMESTAMPTZ(6)``: two events in one millisecond order."""
        rendered = events.format_emitted_at(
            datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        )
        assert rendered == "2025-01-01T00:00:00.000000Z"
        assert events.format_emitted_at(NOW).endswith("123456Z")

        # A naive instant is read as UTC rather than as local time, matching
        # ``paper_repository._instant`` and 009's refusal of ``timestamp without time zone``.
        assert events.format_emitted_at(
            datetime(2025, 1, 1, 0, 0, 0, 5)
        ) == "2025-01-01T00:00:00.000005Z"

        # A non-UTC instant is converted, not relabelled.
        moment = datetime(2025, 1, 1, 2, 0, 0, tzinfo=timezone.utc).astimezone(
            timezone(offset=NOW.utcoffset() or NOW.tzinfo.utcoffset(NOW))
        )
        assert events.format_emitted_at(moment).startswith("2025-01-01T02:00:00")

    def test_a_now_reading_is_microsecond_resolution_and_aware(self) -> None:
        moment = events.utc_now()
        assert moment.tzinfo == timezone.utc
        assert events.format_emitted_at(moment).endswith("Z")

    def test_the_event_id_is_a_uuid_and_not_a_draw_from_random(self) -> None:
        first, second = events.new_event_id(), events.new_event_id()
        assert first != second
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            first,
        ), first

    def test_the_envelope_refuses_an_unlisted_field(self) -> None:
        assert events.PaperEventEnvelope.model_config.get("extra") == "forbid"
        with pytest.raises(ValidationError):
            events.PaperEventEnvelope.model_validate(
                {
                    "schema_version": "paper.v1",
                    "channel": f"paper.{SESSION}",
                    "session_id": SESSION,
                    "type": "paper_error",
                    "sequence": 1,
                    "event_id": "e-1",
                    "emitted_at": NOW,
                    "payload": EXAMPLES["paper_error"],
                    "access_token": "t",
                }
            )

    def test_the_envelope_refuses_an_event_type_outside_the_sixteen(self) -> None:
        with pytest.raises(ValueError):
            events.build_envelope(
                session_id=SESSION,
                event_type="paper_order_vaporised",
                payload=EXAMPLES["paper_error"],
                sequence=1,
                emitted_at=NOW,
            )

    def test_the_envelope_refuses_a_payload_of_the_wrong_type(self) -> None:
        """The type and the payload are one claim; a mismatched pair is a producer bug."""
        with pytest.raises(ValidationError):
            events.build_envelope(
                session_id=SESSION,
                event_type=events.PaperEvent.BALANCE_UPDATED,
                payload=EXAMPLES["market_tick"],
                sequence=1,
                emitted_at=NOW,
            )

    def test_the_envelope_refuses_a_sequence_below_one(self) -> None:
        """``chk_paper_event_sequence`` is ``>= 1``: a log starts at 1, not at 0."""
        for bad in (0, -1):
            with pytest.raises(ValidationError):
                events.build_envelope(
                    session_id=SESSION,
                    event_type=events.PaperEvent.ERROR,
                    payload=EXAMPLES["paper_error"],
                    sequence=bad,
                    emitted_at=NOW,
                )

    def test_the_channel_name_is_the_familys_and_not_a_second_spelling(self) -> None:
        from backend_app.backend import ws_channels as C

        assert events.paper_channel(SESSION) == C.PAPER_FAMILY.channel(SESSION)

    def test_the_frame_carries_no_credential_or_token_key(self) -> None:
        envelope = events.build_envelope(
            session_id=SESSION,
            event_type=events.PaperEvent.SESSION_STARTED,
            payload=EXAMPLES["paper_session_started"],
            sequence=1,
            emitted_at=NOW,
        )
        blob = repr(envelope.frame()).lower()
        for banned in FORBIDDEN_FIELD_SUBSTRINGS:
            assert banned not in blob


# ══════════════════════════════════════════════════════════════════════════
# 4. THE SEQUENCE ALLOCATOR (Requirement 19.3, task 26.2)
# ══════════════════════════════════════════════════════════════════════════


class TestTheSequenceAllocator:
    def test_the_first_event_of_a_session_takes_sequence_one(self) -> None:
        client = _client()
        assert events.next_sequence(client, USER, SESSION) == 1

    def test_it_increases_by_exactly_one_and_persists_on_the_session_row(self) -> None:
        client = _client()

        allocated = [events.next_sequence(client, USER, SESSION) for _ in range(5)]

        assert allocated == [1, 2, 3, 4, 5]
        assert client.sessions[0]["event_sequence"] == 5

    def test_it_continues_the_counter_rather_than_restarting_it(self) -> None:
        """A session that resumes after a restart continues its log."""
        client = _client()
        client.sessions[0]["event_sequence"] = 999

        assert events.next_sequence(client, USER, SESSION) == 1000

    def test_the_allocation_is_a_guarded_update_and_not_an_in_process_counter(
        self,
    ) -> None:
        """Task 26.2 forbids an in-process counter outright.

        The evidence is the statement: an UPDATE on ``paper_sessions`` carrying the read
        ``event_sequence`` as a predicate. An in-process counter would issue no UPDATE at all.
        """
        client = _client()

        events.next_sequence(client, USER, SESSION)

        updates = client.statements_on(repo.SESSIONS_TABLE, "update")
        assert len(updates) == 1
        assert updates[0].filter_value("id") == SESSION
        assert updates[0].filter_value("user_id") == USER
        assert updates[0].filter_value("event_sequence") == 0
        assert updates[0].payload["event_sequence"] == 1

    def test_a_writer_that_moves_the_row_makes_the_swap_match_no_row_and_it_retries(
        self,
    ) -> None:
        """The contiguity mechanism, exercised.

        ``after_select`` fires once the read's rows are already copied out, so the allocator holds
        a correct image of a row that has since moved - exactly the race the predicate exists to
        detect. It must not reuse the number it read.
        """
        client = _client()
        fired: List[int] = []

        def _competitor(store: FakeSupabase, query: Any) -> None:
            if query.table_name != repo.SESSIONS_TABLE or fired:
                return
            fired.append(1)
            store.sessions[0]["event_sequence"] = 7

        client.after_select = _competitor

        allocated = events.next_sequence(client, USER, SESSION)

        assert allocated == 8, "the loser of the swap must re-read, not reuse its number"
        assert client.sessions[0]["event_sequence"] == 8
        assert len(client.statements_on(repo.SESSIONS_TABLE, "update")) == 2

    def test_an_unresolvable_contention_is_a_conflict_and_writes_nothing(self) -> None:
        client = _client()

        def _always_moves(store: FakeSupabase, query: Any) -> None:
            if query.table_name != repo.SESSIONS_TABLE:
                return
            current = int(store.sessions[0]["event_sequence"])
            store.sessions[0]["event_sequence"] = current + 100

        client.after_select = _always_moves

        with pytest.raises(repo.PaperConcurrencyConflict):
            events.next_sequence(client, USER, SESSION)

        assert client.events == []

    def test_another_users_session_cannot_be_advanced(self) -> None:
        """Requirement 21.5: ``user_id`` is a predicate on the allocation, not a filter."""
        client = _client()

        with pytest.raises(repo.PaperPersistenceError):
            events.next_sequence(client, OTHER_USER, SESSION)

        assert client.sessions[0]["event_sequence"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 5. RECORDING AN EVENT, AND WHAT THE DATABASE REFUSES
# ══════════════════════════════════════════════════════════════════════════


class TestRecordingAnEvent:
    def test_it_allocates_writes_and_returns_the_envelope(self) -> None:
        client = _client()

        envelope = events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.SESSION_STARTED,
            payload=EXAMPLES["paper_session_started"],
            emitted_at=NOW,
        )

        assert envelope.sequence == 1
        assert envelope.type == "paper_session_started"
        assert len(client.events) == 1
        row = client.events[0]
        assert row["session_id"] == SESSION
        assert row["user_id"] == USER
        assert row["sequence"] == 1
        assert row["event_type"] == "paper_session_started"
        assert row["schema_version"] == "paper.v1"
        assert row["emitted_at"] == "2025-06-15T12:00:00.123456Z"
        assert row["event_id"] == envelope.event_id

    def test_a_money_value_is_stored_as_an_exact_decimal_string(self) -> None:
        client = _client()

        events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.BALANCE_UPDATED,
            payload={
                "available_balance": Decimal("99999.1234567890"),
                "locked_balance": Decimal("0.0000000001"),
                "total_equity": Decimal("99999.1234567891"),
                "currency": "USD",
                "stale": False,
            },
            emitted_at=NOW,
        )

        stored = client.events[0]["payload"]
        assert stored["available_balance"] == "99999.1234567890"

        # ``Decimal.__str__`` renders a small magnitude in scientific notation, and this file
        # deliberately asserts THAT rather than a prettier form: ``paper_repository._jsonb`` writes
        # this very column with ``str(Decimal)`` too, so a value written by the feed's ``paper_error``
        # path and the same value written by the Paper_Channel emitter are the same characters. A
        # second rendering here would make one column hold two spellings of one number.
        assert stored["locked_balance"] == str(Decimal("0.0000000001")) == "1E-10"
        assert Decimal(stored["locked_balance"]) == Decimal("0.0000000001")
        assert stored == repo._jsonb(stored, "payload"), (
            "the rendering must be a fixed point of the column's own JSONB conversion"
        )

    def test_a_duplicated_sequence_is_refused_by_the_database(self) -> None:
        """``uq_paper_event_seq`` UNIQUE ``(session_id, sequence)``.

        The row counter is rewound to model the only way two allocators can hold the same number
        over this transport - a lost swap that both writers nonetheless acted on. The insert is
        then refused rather than appended, which is the guarantee this transport actually
        delivers (see ``next_sequence``'s residual-gap note).
        """
        client = _client()
        events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.ERROR,
            payload=EXAMPLES["paper_error"],
            emitted_at=NOW,
        )
        client.sessions[0]["event_sequence"] = 0

        with pytest.raises(repo.PaperDuplicateSessionEvent) as caught:
            events.record_event(
                client,
                user_id=USER,
                session_id=SESSION,
                event_type=events.PaperEvent.ERROR,
                payload=EXAMPLES["paper_error"],
                emitted_at=NOW,
            )

        assert "uq_paper_event_seq" in str(caught.value)
        assert len(client.events) == 1, "the colliding row must not be appended"

    def test_a_duplicated_event_id_is_refused_by_the_database(self) -> None:
        """``uq_paper_event_id`` UNIQUE ``(session_id, event_id)`` (Requirement 19.8)."""
        client = _client()
        events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.ERROR,
            payload=EXAMPLES["paper_error"],
            emitted_at=NOW,
            event_id="fixed-id",
        )

        with pytest.raises(repo.PaperDuplicateSessionEvent) as caught:
            events.record_event(
                client,
                user_id=USER,
                session_id=SESSION,
                event_type=events.PaperEvent.ERROR,
                payload=EXAMPLES["paper_error"],
                emitted_at=NOW,
                event_id="fixed-id",
            )

        assert "uq_paper_event_id" in str(caught.value)
        assert len(client.events) == 1

    def test_two_sessions_keep_their_own_sequences(self) -> None:
        other_session = "44444444-4444-4444-8444-444444444444"
        client = _client()
        client.sessions.append(
            _session_row(id=other_session, user_id=OTHER_USER, event_sequence=0)
        )

        first = events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.ERROR,
            payload=EXAMPLES["paper_error"],
            emitted_at=NOW,
        )
        second = events.record_event(
            client,
            user_id=OTHER_USER,
            session_id=other_session,
            event_type=events.PaperEvent.ERROR,
            payload=EXAMPLES["paper_error"],
            emitted_at=NOW,
        )

        assert first.sequence == second.sequence == 1
        assert {r["session_id"] for r in client.events} == {SESSION, other_session}

    def test_it_writes_through_the_repository_and_issues_no_statement_of_its_own(
        self,
    ) -> None:
        """``paper_repository`` is the one module in this package that issues statements."""
        client = _client()

        events.record_event(
            client,
            user_id=USER,
            session_id=SESSION,
            event_type=events.PaperEvent.ERROR,
            payload=EXAMPLES["paper_error"],
            emitted_at=NOW,
        )

        # The migration probe's read of ``paper_accounts`` is ``require_persistence``'s and belongs
        # to every repository call; what this asserts is that the emitter itself issues exactly
        # three statements and that none of them touches a table it has no business in.
        assert [op for op in client.ops if op[1] != repo.ACCOUNTS_TABLE] == [
            ("select", repo.SESSIONS_TABLE),
            ("update", repo.SESSIONS_TABLE),
            ("insert", repo.EVENTS_TABLE),
        ]
