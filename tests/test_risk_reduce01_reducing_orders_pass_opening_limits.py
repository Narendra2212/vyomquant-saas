"""RISK-REDUCE-01 / PAPER-EXP-01: the opening limits never trap an account in its exposure.

Found 2026-11-19 in the final launch certification, on the production QA paper account:

1. PAPER-EXP-01 -- the account held 1,122.6 SOL (~$80,000) on a plan whose exposure cap is
   $5,000. The bot judged the cap against its in-memory ``recovered_exposure_coins``, which
   the paper branch of ``_recover_live_state`` never set: every restart (every deploy)
   reset it to 0 and the bot bought another full cap. Now seeded from the ledger's open
   long at boot.
2. RISK-REDUCE-01 -- at 80.14% utilization, a 0.01 SOL SELL against that long was refused
   "New position opens are locked". The four opening limits (drawdown, daily loss,
   consecutive losses, capital utilization) gated reducing orders too, so the account could
   not de-risk. A reducing order now passes them; opening and adding are still refused.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from backend_app.backend import paper_trading_service as pts
from backend_app.backend.master_executor import BotRunner
from backend_app.backend.paper import paper_repository as paper_repo
from tests.test_paper_repository import FakeSupabase


@pytest.fixture
def paper_service():
    paper_repo.reset_persistence_probe()
    service = pts.get_paper_trading_service()
    service.bind_persistence(FakeSupabase())
    try:
        yield service
    finally:
        service.bind_persistence(None)
        paper_repo.reset_persistence_probe()


class TestReducingOrdersPassTheOpeningLimits:
    def _open_long(self, paper_service, uid):
        asyncio.run(paper_service.place_order(uid, "BTC-USDT", "buy", "market", 1.0, price=100))

    @pytest.mark.parametrize(
        "guard, value",
        [
            ("get_capital_utilization_pct", 0.95),
            ("get_consecutive_loss_count", 99),
            ("get_current_drawdown_pct", 0.99),
        ],
    )
    def test_a_sell_that_reduces_the_long_is_allowed(
        self, paper_service, fresh_paper_quote, monkeypatch, guard, value
    ):
        uid = f"risk-reduce-{guard}"
        self._open_long(paper_service, uid)
        monkeypatch.setattr(paper_service, guard, lambda u, **kw: value)
        asyncio.run(paper_service.place_order(uid, "BTC-USDT", "sell", "market", 0.5, price=100))

    def test_adding_to_the_long_is_still_refused(self, paper_service, fresh_paper_quote, monkeypatch):
        uid = "risk-reduce-add"
        self._open_long(paper_service, uid)
        monkeypatch.setattr(paper_service, "get_capital_utilization_pct", lambda u, **kw: 0.95)
        with pytest.raises(ValueError, match="capital utilization"):
            asyncio.run(paper_service.place_order(uid, "BTC-USDT", "buy", "market", 0.5, price=100))

    def test_a_sell_larger_than_the_long_is_not_treated_as_reducing(
        self, paper_service, fresh_paper_quote, monkeypatch
    ):
        uid = "risk-reduce-reverse"
        self._open_long(paper_service, uid)
        monkeypatch.setattr(paper_service, "get_capital_utilization_pct", lambda u, **kw: 0.95)
        with pytest.raises(ValueError, match="capital utilization"):
            asyncio.run(paper_service.place_order(uid, "BTC-USDT", "sell", "market", 5.0, price=100))

    def test_a_sell_with_no_position_is_still_refused(self, paper_service, fresh_paper_quote, monkeypatch):
        uid = "risk-reduce-naked"
        monkeypatch.setattr(paper_service, "get_capital_utilization_pct", lambda u, **kw: 0.95)
        with pytest.raises(ValueError, match="capital utilization"):
            asyncio.run(paper_service.place_order(uid, "BTC-USDT", "sell", "market", 1.0, price=100))


def test_paper_exp01_the_paper_bot_seeds_its_exposure_from_the_ledger():
    src = inspect.getsource(BotRunner._seed_paper_engine_from_ledger)
    assert "paper_ledger.open_long_size" in src
    assert "self.recovered_exposure_coins = max(Decimal(\"0\"), Decimal(str(held)))" in src


def test_paper_exp01_seeding_sets_the_exposure_a_restart_used_to_lose():
    from decimal import Decimal
    from types import SimpleNamespace

    bot = BotRunner.__new__(BotRunner)
    bot.user_id, bot.symbol = "u1", "SOL/USDT"
    bot.recovered_exposure_coins = Decimal("0")
    bot.execution_engine = SimpleNamespace(_reconcile_from_paper_ledger=lambda snap: None)
    ledger = SimpleNamespace(
        snapshot=lambda symbol: SimpleNamespace(cash=Decimal("20000")),
        ledger_symbol=lambda symbol: "SOL-USDT",
        open_long_size=lambda sym: Decimal("1122.634695") if sym == "SOL-USDT" else Decimal("0"),
    )
    asyncio.run(bot._seed_paper_engine_from_ledger(ledger))
    assert bot.recovered_exposure_coins == Decimal("1122.634695")
