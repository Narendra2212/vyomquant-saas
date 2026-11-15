"""PAPER-STRAT-01 / PAPER-SLIP-01: paper trades name their strategy and carry their slippage.

Found on 2026-11-15: every paper trade in Trade History said "Strategy: not available" and
"Slippage: not available". ``_trade_body`` wrote ``strategy_id: None`` unconditionally, though
every bot paper order's ``idempotency_key`` is ``botrunner:<execution_id>`` and that execution
record names the strategy; and ``paper_fills.slippage_minor`` was read but never returned.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from backend_app.backend import paper_trading_service as svc
from backend_app.backend.paper_trading_service import PaperTradingService

USER = "25ec37ec-d241-4b85-b7a1-c348f6300eb1"


class _Session:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    def execute(self, statement, params):
        self.seen.append((str(statement), params))
        wanted = set(params["ids"])
        hits = [r for r in self.rows if r["execution_id"] in wanted]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: hits))


def _lookup(orders, rows):
    session = _Session(rows)

    @contextlib.contextmanager
    def _ctx():
        yield session

    with patch("backend_app.core.database.get_db_context", _ctx):
        return svc.get_bot_order_strategies(USER, orders), session


def test_a_bot_order_is_resolved_to_its_strategy_and_a_manual_order_is_not():
    orders = [
        {"id": "o1", "idempotency_key": "botrunner:exec_a"},
        {"id": "o2", "idempotency_key": "manual:123"},
        {"id": "o3", "idempotency_key": None},
    ]
    out, session = _lookup(orders, [{"execution_id": "exec_a", "strategy_id": "s1", "strategy": "Launch test 1m"}])
    assert out == {"o1": {"strategy_id": "s1", "strategy": "Launch test 1m"}}
    sql, params = session.seen[0]
    assert "er.tenant_id = :tenant_id" in sql and "s.user_id = er.tenant_id" in sql
    assert str(params["tenant_id"]) == USER and params["ids"] == ["exec_a"]


def test_no_bot_orders_issue_no_query():
    out, session = _lookup([{"id": "o2", "idempotency_key": "manual:1"}], [])
    assert out == {} and session.seen == []


def test_the_trade_body_carries_strategy_and_slippage():
    service = PaperTradingService.__new__(PaperTradingService)
    service.config = SimpleNamespace(money=lambda d: d.quantize(Decimal("0.01")))
    service._paper_provenance = lambda: {}
    fill = {"fill_event_id": "f", "order_id": "o1", "user_id": USER, "quantity": "1",
            "price": "70.465215", "fee_minor": 25, "slippage_minor": 12, "filled_at": "t"}
    body = service._trade_body(fill, {"symbol": "SOL-USDT", "side": "buy"}, Decimal("0"),
                               {"strategy_id": "s1", "strategy": "Launch test 1m"})
    assert body["strategy_id"] == "s1" and body["strategy"] == "Launch test 1m"
    assert body["fee"] == "0.25" and body["slippage"] == "0.12"
    manual = service._trade_body(fill, {"symbol": "SOL-USDT", "side": "buy"}, Decimal("0"))
    assert manual["strategy_id"] is None and manual["strategy"] is None
