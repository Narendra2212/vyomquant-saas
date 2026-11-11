"""PAPER-14 / PAPER-15: no paper session in production had ever produced a signal, order or fill.

PAPER-14. ``DAGEngine.execute_plan`` gated every evaluation on the LIVE execution gate, which in
``VYOMQUANT_MODE=safe`` (production's posture while live trading is off) refuses every
``strategy_signal``. A paper session reaches no exchange, so it is now gated only by the emergency
freeze. Production evidence: 11 sessions, 370 closed candles, 0 signals, 0 orders, 0 fills.

PAPER-15. The builder's default action sizes as ``percent_of_equity``. The paper runtime copied the
intent's ``quantity`` attribute (10 for "10%") as a BASE quantity, i.e. 10 BTC; and had it not, a
sizing rule produced a signal and no order. The rule is now resolved against the session account.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend_app.backend.paper.paper_session_service import resolve_sizing_quantity
from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor


@pytest.fixture
def safe_mode(monkeypatch):
    monkeypatch.setenv("VYOMQUANT_MODE", "safe")
    monkeypatch.setattr(ExecutionFlags, "FROZEN", False)
    monkeypatch.setattr(ExecutionFlags, "PAPER_TRADING_ENABLED", False)
    monkeypatch.setattr(ExecutionFlags, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(ExecutionFlags, "STRATEGY_SIGNAL_EXECUTION", False)


def test_safe_mode_still_blocks_anything_that_can_reach_an_exchange(safe_mode) -> None:
    assert SafetyMonitor.check_execution_allowed("strategy_signal") is not None


def test_a_simulated_evaluation_runs_in_safe_mode(safe_mode) -> None:
    assert SafetyMonitor.check_simulation_allowed("strategy_signal") is None


def test_the_emergency_freeze_still_stops_simulated_evaluation(safe_mode, monkeypatch) -> None:
    monkeypatch.setattr(ExecutionFlags, "FROZEN", True)
    assert "freeze" in SafetyMonitor.check_simulation_allowed("strategy_signal")


def test_freeze_all_sets_the_frozen_flag(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setattr(ExecutionFlags, "FROZEN", False)
    ExecutionFlags.freeze_all()
    try:
        assert ExecutionFlags.FROZEN is True
    finally:
        ExecutionFlags.FROZEN = False


def test_only_the_paper_runtime_asks_for_simulation() -> None:
    import inspect

    from backend_app.backend import dag_event_loop, paper_session_runtime

    assert "simulated=True" in inspect.getsource(paper_session_runtime.PaperDagRuntime._execute)
    assert "simulated=True" not in inspect.getsource(dag_event_loop)


ACCOUNT = {"total_equity": Decimal("10000.00"), "available_balance": Decimal("9000.00")}


@pytest.mark.parametrize(
    "sizing, kwargs, expected",
    [
        ({"quantity_type": "percent_of_equity", "quantity": 10}, {"price": Decimal("73800")}, "0.01355013"),
        ({"quantity_type": "percent_of_equity", "quantity": 10.0}, {"price": Decimal("73800")}, "0.01355013"),
        ({"quantity_type": "percent_of_free_balance", "quantity": 10}, {"price": Decimal("73800")}, "0.01219512"),
        ({"quantity_type": "quote_notional", "quantity": 500}, {"price": Decimal("73800")}, "0.00677506"),
        ({"quantity_type": "percent_of_position", "quantity": 50}, {"price": None, "position_size": Decimal("0.02")}, "0.01000000"),
    ],
)
def test_a_sizing_rule_resolves_to_an_exact_base_quantity(sizing, kwargs, expected) -> None:
    got = resolve_sizing_quantity(sizing, account=ACCOUNT, **kwargs)
    assert got == Decimal(expected) and isinstance(got, Decimal)


@pytest.mark.parametrize(
    "sizing, account, price",
    [
        ({"quantity_type": "percent_of_capital", "quantity": 10}, ACCOUNT, Decimal("1")),
        ({"quantity_type": "percent_of_equity", "quantity": 0}, ACCOUNT, Decimal("1")),
        ({"quantity_type": "percent_of_equity", "quantity": 10}, None, Decimal("1")),
        ({"quantity_type": "percent_of_equity", "quantity": 10}, ACCOUNT, None),
    ],
)
def test_an_unresolvable_rule_places_no_order(sizing, account, price) -> None:
    assert resolve_sizing_quantity(sizing, account=account, price=price) is None


def test_a_percent_sized_intent_is_not_sent_as_a_base_quantity() -> None:
    from backend_app.backend.paper_session_runtime import PaperDagRuntime

    runtime = PaperDagRuntime(session_id="s", symbol="BTC/USDT", timeframe="1m")
    intent = SimpleNamespace(
        triggered=True, node_id="n-6", quantity=10, quantity_type="percent_of_equity",
        order_type="market", limit_price=None, side="buy",
    )
    event = SimpleNamespace(event_timestamp="2026-11-11T01:55:00Z", source_event_id="e1")
    output = {"decision": "BUY", "symbol": "BTC/USDT",
              "sizing_intention": {"quantity_type": "percent_of_equity", "quantity": 10}}
    import backend_app.backend.signal_service as signal_service

    original = signal_service.action_node_output
    signal_service.action_node_output = lambda *a, **k: output
    try:
        signal = runtime._project(None, intent, event)
    finally:
        signal_service.action_node_output = original

    assert "quantity" not in signal, "10% of equity must not travel as 10 BTC"
    assert signal["sizing_intention"]["quantity_type"] == "percent_of_equity"
