"""Offline stand-ins for a venue's PUBLIC market data (no network, no credential).

Used wherever a paper BotRunner must boot: after RT-01 the paper path connects to real public
market data and no longer uses the DEV_MODE mock, so a test supplies this instead of reaching
the network. Deterministic: prices are an expression of the bar index, never random.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Sequence

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def make_closed_ohlcv(count: int, timeframe: str = "1m", *, end_ms: Optional[int] = None,
                      base: float = 100.0) -> List[List[float]]:
    """``count`` CLOSED bars ending one bar before ``end_ms`` (default: now). Wave-shaped."""
    step = TF_MS[timeframe]
    end = (end_ms if end_ms is not None else int(time.time() * 1000)) // step * step
    rows = []
    price = base
    for i in range(count):
        price += 1.5 if (i // 30) % 2 == 0 else -1.25
        ts = end - (count - i) * step
        rows.append([ts, price - 0.2, price + 0.5, price - 0.5, price, 10.0])
    return rows


class FakePublicExchange:
    """The slice of a ccxt.pro client the paper boot path and ``DataEngine`` touch."""

    def __init__(self, ohlcv: Sequence[Sequence[float]], trades: Optional[List[Dict[str, Any]]] = None,
                 *, watch_trades: bool = True):
        self.id = "fakevenue"
        self.has = {"watchTrades": watch_trades}
        self._ohlcv = [list(r) for r in ohlcv]
        self._trades = list(trades or [])
        self.api_key_seen: Optional[str] = None
        self.balance_calls = 0

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        rows = self._ohlcv
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return [list(r) for r in rows[-(limit or len(rows)):]]

    async def watch_trades(self, symbol):
        if self._trades:
            return [self._trades.pop(0)]
        await asyncio.sleep(3600)   # no more data: block like a quiet market
        return []

    async def fetch_balance(self):   # a keyless client cannot do this; a paper bot must not try
        self.balance_calls += 1
        raise PermissionError("authentication required")

    async def close(self):
        return None


class FakeBridge:
    api_key = None

    def __init__(self, exchange):
        self.exchange = exchange

    async def disconnect(self):
        return None


def public_factory(exchange: FakePublicExchange):
    async def _factory(exchange_id: str):
        return FakeBridge(exchange), exchange
    return _factory
