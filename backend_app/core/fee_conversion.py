"""LIVE-FEE-01: one rule for stating a venue fee in the symbol's quote currency.

Binance charges spot commission in the asset RECEIVED unless the account pays in BNB: the
base coin on a buy (1E-7 BTC on a 0.0001 BTC buy), the quote coin on a sell. Trade History
sums fees and P&L in quote, so a fee is converted here or not stated at all:

* currency == quote             -> the fee as charged;
* currency == base              -> fee * fill price (the price the fee was charged at);
* any other asset (BNB, ...)    -> ``None``: no rate is known here, so no figure is guessed;
* no currency on a PAPER fill   -> the fee as charged (the paper ledger books fees in quote);
* no currency on a LIVE fill    -> ``None``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple


def split_symbol(symbol: Any) -> Tuple[str, str]:
    """``BTC/USDC`` / ``BTC-USDC`` / ``BTC_USDC`` / ``BTC/USDT:USDT`` -> ``("BTC", "USDC")``."""
    text = str(symbol or "").upper().split(":", 1)[0].replace("-", "/").replace("_", "/")
    if "/" not in text:
        return text, ""
    base, quote = text.split("/", 1)
    return base.strip(), quote.strip()


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def fee_in_quote(
    fee: Any, fee_currency: Any, symbol: Any, price: Any, *, paper: bool = False
) -> Optional[Decimal]:
    """The fee in the symbol's quote currency, or ``None`` when it cannot be stated."""
    amount = _decimal(fee)
    if amount is None:
        return None
    base, quote = split_symbol(symbol)
    currency = str(fee_currency or "").strip().upper()
    if not currency:
        return amount if paper else None
    if quote and currency == quote:
        return amount
    if base and currency == base:
        rate = _decimal(price)
        return None if rate is None or rate <= 0 else amount * rate
    return None
