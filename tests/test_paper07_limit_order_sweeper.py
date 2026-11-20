"""PAPER-07 -- resting legacy paper limit orders now actually get checked for a fill.

THE DEFECT
    ``PaperTradingService.check_limit_orders`` fills a resting default-account LIMIT order
    when the current price crosses its limit. Its own docstring already said the problem:
    "Nothing in the application called this method... The per-session matcher tasks
    26.x/27.x add is what drives fills for a Paper_Session." A user who places a legacy
    limit order via the real, reachable ``POST /api/paper/orders`` route got an order that
    sat on the book forever, however far the price moved through it.

THE FIX
    ``PaperLimitOrderSweeper`` (new): a periodic sweep, the same shape every other
    background component here already uses (``ConsistencyChecker``, ``OrderWatchdog``,
    ``ReconciliationScheduler``). Each cycle: a new, deliberately-the-only cross-tenant
    repository read (``get_open_legacy_limit_order_pairs``, service-role only) finds every
    ``(user_id, symbol)`` with a resting legacy limit order; one ``get_fresh_market_price``
    read per distinct symbol (the SAME real last-price cache PAPER-02 wired this session);
    and ``check_limit_orders`` is called per pair, scoped to that one identity.

WHAT THIS FILE PROVES (fakes; no real Supabase, no real Redis, no real exchange)
    * a pair with a fresh price available actually calls check_limit_orders and counts the
      fill;
    * a symbol with NO fresh price is skipped, honestly, rather than filling at an invented
      price -- the same PAPER-02 principle;
    * one tenant's check_limit_orders failure does not stop the sweep for the others;
    * a missing service-role client is a clean no-op cycle, not a crash of the loop;
    * the repository's new cross-tenant read only matches OPEN, LIMIT, default-account
      (session_id IS NULL) rows, and de-duplicates (user_id, symbol) pairs.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.backend.paper_limit_order_sweeper import PaperLimitOrderSweeper


@pytest.mark.asyncio
class TestRunOnceDrivesRealFills:
    async def test_a_pair_with_a_fresh_price_is_checked_and_counted(self):
        sweeper = PaperLimitOrderSweeper()

        fake_repo_pairs = [("user-1", "BTC/USDT")]
        fake_price = Decimal("50000")
        fake_fill = [{"order_id": "o1"}]

        mock_check = AsyncMock(return_value=fake_fill)
        mock_paper_svc = type("S", (), {"check_limit_orders": mock_check})()

        with patch(
            "backend_app.backend.paper.paper_repository.get_open_legacy_limit_order_pairs",
            return_value=fake_repo_pairs,
        ), patch(
            "backend_app.backend.paper_trading_service.get_fresh_market_price",
            AsyncMock(return_value=fake_price),
        ), patch(
            "backend_app.backend.paper_trading_service.get_paper_trading_service",
            return_value=mock_paper_svc,
        ), patch(
            "backend_app.core.dependencies.get_service_supabase", return_value=object(),
        ):
            filled = await sweeper.run_once()

        assert filled == 1
        mock_check.assert_awaited_once_with("BTC/USDT", fake_price, user_id="user-1")

    async def test_a_symbol_with_no_fresh_price_is_skipped_not_filled_at_random(self):
        sweeper = PaperLimitOrderSweeper()
        mock_check = AsyncMock(return_value=[{"order_id": "o1"}])
        mock_paper_svc = type("S", (), {"check_limit_orders": mock_check})()

        with patch(
            "backend_app.backend.paper.paper_repository.get_open_legacy_limit_order_pairs",
            return_value=[("user-1", "DOGE/USDT")],
        ), patch(
            "backend_app.backend.paper_trading_service.get_fresh_market_price",
            AsyncMock(return_value=None),
        ), patch(
            "backend_app.backend.paper_trading_service.get_paper_trading_service",
            return_value=mock_paper_svc,
        ), patch(
            "backend_app.core.dependencies.get_service_supabase", return_value=object(),
        ):
            filled = await sweeper.run_once()

        assert filled == 0
        mock_check.assert_not_awaited()

    async def test_one_tenants_failure_does_not_stop_the_others(self):
        sweeper = PaperLimitOrderSweeper()

        async def _check(symbol, price, user_id):
            if user_id == "user-bad":
                raise RuntimeError("account read failed")
            return [{"order_id": "o1"}]

        mock_paper_svc = type("S", (), {"check_limit_orders": staticmethod(_check)})()

        with patch(
            "backend_app.backend.paper.paper_repository.get_open_legacy_limit_order_pairs",
            return_value=[("user-bad", "BTC/USDT"), ("user-good", "BTC/USDT")],
        ), patch(
            "backend_app.backend.paper_trading_service.get_fresh_market_price",
            AsyncMock(return_value=Decimal("50000")),
        ), patch(
            "backend_app.backend.paper_trading_service.get_paper_trading_service",
            return_value=mock_paper_svc,
        ), patch(
            "backend_app.core.dependencies.get_service_supabase", return_value=object(),
        ):
            filled = await sweeper.run_once()

        assert filled == 1  # only user-good's fill counted; user-bad's error was swallowed

    async def test_no_service_role_client_is_a_clean_no_op_not_a_crash(self):
        sweeper = PaperLimitOrderSweeper()
        with patch(
            "backend_app.core.dependencies.get_service_supabase", return_value=None,
        ):
            filled = await sweeper.run_once()
        assert filled == 0

    async def test_distinct_symbols_each_get_one_price_read(self):
        sweeper = PaperLimitOrderSweeper()
        mock_check = AsyncMock(return_value=[])
        mock_paper_svc = type("S", (), {"check_limit_orders": mock_check})()
        price_reads = []

        async def _fake_price(symbol):
            price_reads.append(symbol)
            return Decimal("100")

        with patch(
            "backend_app.backend.paper.paper_repository.get_open_legacy_limit_order_pairs",
            return_value=[("u1", "BTC/USDT"), ("u2", "BTC/USDT"), ("u1", "ETH/USDT")],
        ), patch(
            "backend_app.backend.paper_trading_service.get_fresh_market_price", _fake_price,
        ), patch(
            "backend_app.backend.paper_trading_service.get_paper_trading_service",
            return_value=mock_paper_svc,
        ), patch(
            "backend_app.core.dependencies.get_service_supabase", return_value=object(),
        ):
            await sweeper.run_once()

        assert sorted(price_reads) == ["BTC/USDT", "ETH/USDT"]  # one read per distinct symbol
        assert mock_check.await_count == 3  # still one check_limit_orders call per pair


class TestGetOpenLegacyLimitOrderPairsFiltersAndDeduplicates:
    def test_only_open_limit_default_account_rows_match_and_pairs_are_unique(self):
        from backend_app.backend.paper import paper_repository as repo

        class _Query:
            def __init__(self, rows):
                self._rows = rows
                self._filters = {}

            def select(self, *_a, **_k):
                return self

            def eq(self, col, val):
                self._filters[col] = val
                return self

            def is_(self, col, val):
                self._filters[col] = None if val == "null" else val
                return self

            def execute(self):
                hits = [
                    r for r in self._rows
                    if r.get("legacy_status") == self._filters.get("legacy_status", r.get("legacy_status"))
                    and r.get("order_type") == self._filters.get("order_type", r.get("order_type"))
                    and r.get("session_id") == self._filters.get("session_id")
                ]
                from types import SimpleNamespace
                return SimpleNamespace(data=hits)

        rows = [
            {"user_id": "u1", "symbol": "BTC/USDT", "legacy_status": "OPEN", "order_type": "limit", "session_id": None},
            {"user_id": "u1", "symbol": "BTC/USDT", "legacy_status": "OPEN", "order_type": "limit", "session_id": None},  # dup
            {"user_id": "u2", "symbol": "ETH/USDT", "legacy_status": "OPEN", "order_type": "limit", "session_id": None},
            {"user_id": "u3", "symbol": "SOL/USDT", "legacy_status": "FILLED", "order_type": "limit", "session_id": None},  # not OPEN
            {"user_id": "u4", "symbol": "ADA/USDT", "legacy_status": "OPEN", "order_type": "market", "session_id": None},  # not limit
            {"user_id": "u5", "symbol": "XRP/USDT", "legacy_status": "OPEN", "order_type": "limit", "session_id": "sess-1"},  # session-scoped
        ]

        class _FakeSB:
            def table(self, name):
                assert name == "paper_orders"
                return _Query(rows)

        pairs = repo.get_open_legacy_limit_order_pairs(_FakeSB())
        assert pairs == [("u1", "BTC/USDT"), ("u2", "ETH/USDT")]
