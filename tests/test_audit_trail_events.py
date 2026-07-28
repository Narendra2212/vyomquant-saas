"""
tests/test_audit_trail_events.py

Unit tests for order lifecycle audit trail event hierarchy and refactored
OrderAuditLogger interface.
"""

import asyncio
from datetime import datetime
import json
import pytest
from unittest.mock import AsyncMock, patch

from backend_app.core.audit_trail import (
    AuditEventType,
    BaseOrderLifecycleEvent,
    DecisionEvent,
    FillEvent,
    OrderAuditLogger,
    OrderAuditRecord,
    OrderConfirmedEvent,
    OrderFailedEvent,
    OrderSubmittedEvent,
    SignalReceivedEvent,
    get_order_audit_logger,
)


def test_event_hierarchy_inheritance():
    """Verify dataclass hierarchy inheritance."""
    events = [
        SignalReceivedEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
        DecisionEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
        OrderSubmittedEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
        OrderConfirmedEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
        OrderFailedEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
        FillEvent(user_id="u1", strategy_id="s1", symbol="BTC/USDT", side="buy"),
    ]
    for ev in events:
        assert isinstance(ev, BaseOrderLifecycleEvent)
        assert ev.user_id == "u1"
        assert ev.strategy_id == "s1"
        assert ev.symbol == "BTC/USDT"
        assert ev.side == "buy"


def test_log_signal_received_with_event():
    """Verify log_signal_received logs correctly using SignalReceivedEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = SignalReceivedEvent(
            user_id="user_123",
            strategy_id="strat_abc",
            symbol="BTC/USDT",
            side="buy",
            size=1.5,
            signal={"action": "BUY", "confidence": 0.95},
            metadata={"source": "unit_test"},
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            audit_id = await logger.log_signal_received(event)

            assert audit_id.startswith("AUDIT-")
            assert mock_store.called
            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.audit_id == audit_id
            assert record.event_type == AuditEventType.SIGNAL_RECEIVED
            assert record.user_id == "user_123"
            assert record.strategy_id == "strat_abc"
            assert record.symbol == "BTC/USDT"
            assert record.side == "buy"
            assert record.size == 1.5
            assert record.signal == {"action": "BUY", "confidence": 0.95}

    asyncio.run(_run())


def test_log_decision_with_event():
    """Verify log_decision logs correctly using DecisionEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = DecisionEvent(
            audit_id="AUDIT-12345",
            user_id="user_123",
            strategy_id="strat_abc",
            execution_id="exec_999",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
            price=50000.0,
            order_type="limit",
            exchange_id="binance",
            signal={"sig": 1},
            decision_reason="RSI oversold",
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            await logger.log_decision(event)

            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.audit_id == "AUDIT-12345"
            assert record.event_type == AuditEventType.DECISION_MADE
            assert record.execution_id == "exec_999"
            assert record.decision_reason == "RSI oversold"
            assert record.status == "decided"

    asyncio.run(_run())


def test_log_order_submitted_with_event():
    """Verify log_order_submitted logs correctly using OrderSubmittedEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = OrderSubmittedEvent(
            audit_id="AUDIT-12345",
            user_id="user_123",
            strategy_id="strat_abc",
            execution_id="exec_999",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
            price=50000.0,
            order_type="limit",
            exchange_id="binance",
            client_order_id="client_ord_1",
            exchange_response={"id": "ex_ord_100", "status": "submitted"},
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            await logger.log_order_submitted(event)

            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.event_type == AuditEventType.ORDER_SUBMITTED
            assert record.client_order_id == "client_ord_1"
            assert record.exchange_order_id == "ex_ord_100"
            assert record.status == "submitted"

    asyncio.run(_run())


def test_log_order_confirmed_with_event():
    """Verify log_order_confirmed logs correctly using OrderConfirmedEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = OrderConfirmedEvent(
            audit_id="AUDIT-12345",
            user_id="user_123",
            strategy_id="strat_abc",
            execution_id="exec_999",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
            price=50000.0,
            order_type="limit",
            exchange_id="binance",
            client_order_id="client_ord_1",
            exchange_order_id="ex_ord_100",
            exchange_response={"id": "ex_ord_100", "status": "closed"},
            status="closed",
            filled_amount=1.0,
            remaining_amount=0.0,
            average_price=50000.0,
            fee=0.001,
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            await logger.log_order_confirmed(event)

            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.event_type == AuditEventType.ORDER_CONFIRMED
            assert record.filled_amount == 1.0
            assert record.remaining_amount == 0.0
            assert record.fee == 0.001

    asyncio.run(_run())


def test_log_order_failed_with_event():
    """Verify log_order_failed logs correctly using OrderFailedEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = OrderFailedEvent(
            audit_id="AUDIT-12345",
            user_id="user_123",
            strategy_id="strat_abc",
            execution_id="exec_999",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
            price=50000.0,
            order_type="limit",
            exchange_id="binance",
            client_order_id="client_ord_1",
            error_message="Insufficient balance",
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            await logger.log_order_failed(event)

            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.event_type == AuditEventType.ORDER_FAILED
            assert record.error_message == "Insufficient balance"
            assert record.status == "failed"

    asyncio.run(_run())


def test_log_fill_with_event():
    """Verify log_fill logs correctly using FillEvent."""
    async def _run():
        logger = OrderAuditLogger()
        event = FillEvent(
            audit_id="AUDIT-12345",
            user_id="user_123",
            strategy_id="strat_abc",
            execution_id="exec_999",
            symbol="BTC/USDT",
            side="buy",
            exchange_id="binance",
            exchange_order_id="ex_ord_100",
            trade_id="tr_555",
            fill_amount=0.5,
            fill_price=50010.0,
            fee=0.0005,
            exchange_response={"id": "ex_ord_100"},
            metadata={"tag": "partial_fill"},
        )

        with patch.object(logger, "_store_record", new_callable=AsyncMock) as mock_store:
            await logger.log_fill(event)

            record: OrderAuditRecord = mock_store.call_args[0][0]
            assert record.event_type == AuditEventType.FILL_RECEIVED
            assert record.filled_amount == 0.5
            assert record.average_price == 50010.0
            assert record.metadata["trade_id"] == "tr_555"
            assert record.metadata["tag"] == "partial_fill"

    asyncio.run(_run())
