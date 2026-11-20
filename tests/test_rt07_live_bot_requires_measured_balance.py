"""RT-07 regression: a live BotRunner must refuse to start without a positive MEASURED
quote balance - never substitute 10,000."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from backend_app.backend.master_executor import BotRunner


class _Vault:
    def __init__(self, api_key="k"):
        self.api_key = api_key

    def load_decrypted_keys(self, user_id, exchange_id):
        # A VERIFIED key: EXCH-STATE-02 refuses to start a live bot on a key with no recorded
        # verdict, before the balance checks this file exists for are reached. These tests are
        # about the balance, so the key is modelled as one that passed verification.
        return {"api_key": self.api_key, "secret_key": "s", "password": None,
                "validation_status": "verified", "can_trade": True, "sandbox": False}


class _Conn:
    def __init__(self, exchange_id=None, api_key=None, secret_key=None, password=None,
                 testnet=False):
        # `testnet` mirrors the real ConnectionEngine signature: the live path now routes by
        # the stored key's environment (EXCH-STATE-02).
        self.api_key = api_key
        self.testnet = testnet

    async def connect(self):
        return object()


def _runner(api_key="k"):
    gs = SimpleNamespace(vault=_Vault(api_key), telemetry=SimpleNamespace(), risk=SimpleNamespace(), alert=SimpleNamespace())
    return BotRunner(
        str(uuid.uuid4()), "BTC/USDT",
        {"exchange_id": "binance", "timeframe": "1m"}, gs, paper_trading=False,
    )


def _patch(monkeypatch, wallet):
    import backend_app.backend.connection_engine as ce
    import backend_app.backend.data_seeking_engine as de

    class Data:
        def __init__(self, exchange):
            pass

        async def fetch_wallet_balance_snapshot(self):
            return wallet

        async def fetch_historical_ohlcv(self, *a, **k):
            return []

    monkeypatch.setattr(ce, "ConnectionEngine", _Conn)
    monkeypatch.setattr(de, "DataEngine", Data)


@pytest.mark.parametrize(
    "wallet",
    [
        {"USDT": {"total": 0}},      # zero balance used to become 10,000
        {},                          # missing quote coin used to become 10,000
        {"USDT": {"total": -5}},
        {"USDT": None},
    ],
)
def test_zero_or_missing_quote_balance_refuses_to_start(monkeypatch, wallet):
    _patch(monkeypatch, wallet)
    bot = _runner()
    with pytest.raises(RuntimeError, match="Refusing to start live bot"):
        asyncio.run(bot._recover_live_state())
    assert bot.execution_engine is None, "no engine may be built from a substituted balance"


def test_missing_api_key_refuses_to_start_instead_of_fabricating_a_wallet(monkeypatch):
    _patch(monkeypatch, {"USDT": {"total": 1000}})
    bot = _runner(api_key=None)
    with pytest.raises(RuntimeError, match="no exchange API key"):
        asyncio.run(bot._recover_live_state())
    assert bot.execution_engine is None


def test_positive_measured_balance_is_used_as_is(monkeypatch):
    _patch(monkeypatch, {"USDT": {"total": 1234.5}, "BTC": {"total": 0.25}})
    import backend_app.backend.exchange_executor as ex
    monkeypatch.setattr(ex, "get_exchange_executor", lambda **k: object())
    captured = {}

    import backend_app.core.execution_engine as ee

    class FakeEngine:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(ee, "ExecutionEngine", FakeEngine)
    bot = _runner()
    try:
        asyncio.run(bot._recover_live_state())
    except Exception:
        pass  # later warm-up steps are not under test
    assert captured["portfolio_state"] == {"total_equity": 1234.5}
    # POS-01: the 0.25 BTC the account already holds is NOT this bot's position. A new
    # deployment with no fills of its own starts flat (it used to adopt the wallet total,
    # so an exit signal would have sold coins the bot never bought).
    assert bot.recovered_exposure_coins == 0
