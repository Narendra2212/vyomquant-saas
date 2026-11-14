"""
tests/test_perf01_public_market_data_uses_the_pool.py

PERF-01: the latency of leaving the Strategy Builder.

The builder polls ``GET /strategy-operations/strategies/{id}/data-quality`` every 15 s. That
handler (and the backtest feed and the training window) built a BRAND-NEW ccxt.pro client per
call - ``await ConnectionEngine(exchange_id=venue).connect()`` - whose ``load_markets``
downloads and parses the venue's whole market list on the event loop, and never closed it.
Every poll stalled the worker's other requests for seconds (the Strategies page's, when the
user switched to it) and leaked a session (Sentry "Unclosed client session" / "Unclosed
connector", culprit .../data-quality).

Asserted: none of the three public-data paths constructs a client itself; all go through
``connection_engine.get_public_exchange``, which returns ONE pooled client per venue.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SITES = {
    "backend_app/routers/strategy_operations.py": ("_backtest_exchange_instance", "_fetch_preview_bars"),
    "backend_app/backend/strategy_service.py": None,
}


def _fresh_connects(path):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        # `ConnectionEngine(exchange_id=...).connect()` - a fresh client built per call.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect" and isinstance(node.func.value, ast.Call)
                and getattr(node.func.value.func, "id", None) == "ConnectionEngine"):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("path", sorted(SITES))
def test_public_market_data_paths_do_not_build_a_client_per_call(path):
    assert _fresh_connects(path) == [], (
        f"{path} builds a fresh ConnectionEngine per call at lines {_fresh_connects(path)}; "
        "use connection_engine.get_public_exchange (pooled, loads markets once)."
    )


def test_get_public_exchange_returns_one_pooled_client_per_venue(monkeypatch):
    from backend_app.backend import connection_engine as ce

    built = []

    class _Engine:
        def __init__(self, exchange_id, *a, **k):
            built.append(exchange_id)

        async def connect(self, *a, **k):
            return object()

    monkeypatch.setattr(ce, "ConnectionEngine", _Engine)
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    ce._pool_sandbox.clear()

    async def scenario():
        first = await ce.get_public_exchange("binance")
        again = [await ce.get_public_exchange("binance") for _ in range(5)]
        return first, again

    first, again = asyncio.run(scenario())
    assert all(x is first for x in again), "each call must reuse the pooled client"
    assert built == ["binance"], f"expected ONE client build, got {built}"
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    ce._pool_sandbox.clear()
