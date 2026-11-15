"""LIVE-HIST-01: Trade History's LIVE ledger is read from ``execution_records``.

Found on 2026-11-15: ``GET /api/orders/history`` read only QuestDB's ``executions`` table --
unreachable in production (QUESTDB_HOST=127.0.0.1 on the ECS task), and ``execute_query``
never binds the ``%s`` parameters it is given -- then answered ``[]`` because the page sends
no ``exchange_id``. The owner's 92 filled live testnet orders showed as "No trades recorded".
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend_app.routers import orders as routes

USER = "d0a4f657-092f-4def-96f1-858e2f7b63c1"


def _row(**over):
    base = {
        "execution_id": "exec_1", "order_id": "7304857", "strategy_id": "s1",
        "strategy": "FROM", "symbol": "BTC/USDC", "side": "buy", "quantity": "0.0001",
        "price": "64731.4", "exchange_id": "binance",
        "executed_at": datetime(2026, 11, 15, 4, 2, 51, tzinfo=timezone.utc),
        "raw_fee": "0.0647", "fee_currency": "USDC", "fill_state": None,
    }
    base.update(over)
    return base


class _Session:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    def execute(self, statement, params):
        self.seen.append((str(statement), params))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))


def _read(rows, symbol=None):
    session = _Session(rows)

    @contextlib.contextmanager
    def _ctx():
        yield session

    with patch("backend_app.core.database.get_db_context", _ctx):
        out = routes._live_history_rows(USER, symbol, 100)
    return out, session


def test_a_filled_live_execution_is_a_trade_row_with_its_strategy():
    out, session = _read([_row()])
    assert out == [{
        "execution_id": "exec_1", "order_id": "7304857", "strategy_id": "s1",
        "strategy": "FROM", "symbol": "BTC/USDC", "side": "buy", "quantity": "0.0001",
        "price": "64731.4", "fee": "0.0647", "fee_currency": "USDC", "status": "FILLED",
        "exchange_id": "binance", "executed_at": "2026-11-15T04:02:51+00:00",
        "execution_environment": "LIVE", "is_simulated": False,
    }]
    sql, params = session.seen[0]
    assert "FROM execution_records" in sql and "exchange_id IS NOT NULL" in sql
    assert "status::text = 'COMPLETED'" in sql and "filled_size::numeric > 0" in sql
    assert str(params["tenant_id"]) == USER


def test_a_fee_in_the_base_asset_is_not_reported_as_dollars():
    out, _ = _read([_row(raw_fee="1E-7", fee_currency=None), _row(raw_fee="1E-7", fee_currency="BTC")])
    assert [r["fee"] for r in out] == [None, None]


def test_the_placeholder_fee_of_an_accepted_order_is_not_a_fee():
    out, _ = _read([_row(raw_fee="0", fee_currency="USDC", fill_state="accepted")])
    assert out[0]["fee"] is None


def test_a_symbol_filter_is_bound_not_formatted():
    _, session = _read([], symbol="BTC_USDC")
    sql, params = session.seen[0]
    assert ":symbol" in sql and params["symbol"] == "BTC/USDC"
    assert "BTC" not in sql


def test_a_non_uuid_user_reads_nothing():
    assert routes._live_history_rows("not-a-uuid", None, 10) == []


def test_the_route_answers_from_execution_records_before_questdb():
    telemetry = SimpleNamespace(execute_query=AsyncMock(side_effect=AssertionError("questdb")))
    rows = [{"execution_id": "exec_1"}]
    with patch.object(routes, "_live_history_rows", return_value=rows), \
         patch("backend_app.core.cache.redis_manager.redis_manager.get", AsyncMock(return_value=None)), \
         patch("backend_app.core.cache.redis_manager.redis_manager.set", AsyncMock()):
        out = asyncio.run(routes.get_history(symbol=None, limit=500, exchange_id=None,
                                             user={"id": USER}, vault=None, telemetry=telemetry))
    assert out == rows
    telemetry.execute_query.assert_not_awaited()
