"""
tests/test_live_quote01_bot_sizes_from_its_own_quote_coin.py

LIVE-QUOTE-01: a live bot on a non-USDT pair was sized from its USDT balance.

At boot ``BotRunner`` measured equity in the pair's own quote coin (``BTC/USDC`` -> USDC),
but the tick loop then read ``wallet["USDT"]["free"]`` for every pair and passed it to the
evaluator as ``free_quote``. On an account holding only USDC that is zero, so a USDC-quoted
strategy was sized from nothing. Found during the live testnet run (ADA/USDC, BTC/USDC).
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.master_executor import quote_coin_of  # noqa: E402

MASTER_EXECUTOR = Path(__file__).resolve().parents[1] / "backend_app" / "backend" / "master_executor.py"


@pytest.mark.parametrize(
    "symbol, quote",
    [
        ("BTC/USDC", "USDC"),
        ("ADA/USDC", "USDC"),
        ("BTC/USDT", "USDT"),
        ("ETH/BTC", "BTC"),
        ("BTC/USDT:USDT", "USDT"),
        ("btc/usdc", "USDC"),
        ("BTCUSDT", "USDT"),
        ("", "USDT"),
        (None, "USDT"),
    ],
)
def test_quote_coin_of(symbol, quote):
    assert quote_coin_of(symbol) == quote


def test_the_tick_loop_reads_no_hardcoded_usdt_wallet_entry():
    """No ``wallet.get("USDT", ...)`` anywhere in the runtime: the quote is the pair's."""
    tree = ast.parse(MASTER_EXECUTOR.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "wallet"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "USDT"
    ]
    assert not offenders, (
        f"master_executor.py reads wallet['USDT'] at line(s) {offenders}; a BTC/USDC bot "
        f"would be sized from the wrong currency. Use quote_coin_of(self.symbol)."
    )
