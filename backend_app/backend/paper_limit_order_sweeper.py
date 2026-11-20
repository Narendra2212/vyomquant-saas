"""backend/paper_limit_order_sweeper.py -- PAPER-07's missing caller for check_limit_orders.

THE DEFECT (audit)
    ``PaperTradingService.check_limit_orders`` fills a resting default-account (legacy)
    paper LIMIT order when the current price crosses its limit. Its own docstring already
    says the whole problem: "Nothing in the application called this method... The
    per-session matcher tasks 26.x/27.x add is what drives fills for a Paper_Session." A
    user who places a legacy limit order via ``POST /api/paper/orders`` -- a real,
    currently-reachable route, not dead code -- gets an order that sits on the book
    forever, regardless of how far the price moves through it.

THE FIX
    A periodic sweep, following the SAME shape every other background safety/reconciliation
    component in this codebase already uses (``ConsistencyChecker``, ``OrderWatchdog``,
    ``ReconciliationScheduler``): a ``start()``/``stop()`` class running an asyncio loop,
    started from ``main.py``'s lifespan hook alongside them. Each cycle:

      1. ``paper_repository.get_open_legacy_limit_order_pairs`` -- the ONE sanctioned
         cross-tenant read this module's own docstring explains, via a SERVICE-ROLE client
         (never a per-request one; there is no caller identity to scope to here).
      2. For each DISTINCT symbol across every pair, one
         ``paper_trading_service.get_fresh_market_price`` read -- the SAME real last-price
         cache PAPER-02 wired this session, not a second price source.
      3. For each ``(user_id, symbol)`` pair with a fresh price, ``check_limit_orders`` --
         scoped to that one identity, exactly as its own docstring requires.

    A symbol with no fresh price (nothing currently streaming it) is correctly, honestly
    skipped -- never filled at an invented price. This is the SAME bounded, honest
    limitation PAPER-02's own docstring already names: a legacy limit order fills only
    when a measured price happens to be available for its symbol, same as a manual market
    order's fill price does.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Dict, Optional

logger = logging.getLogger("PaperLimitOrderSweeper")

#: Matches OrderWatchdog's own interval order of magnitude; limit fills are not latency-
#: sensitive the way a live order confirmation is, and a shorter interval would only read
#: the same (service-role query + cache) results more often for no new information.
DEFAULT_SWEEP_INTERVAL_SECONDS = 30


class PaperLimitOrderSweeper:
    """Periodically drives :meth:`PaperTradingService.check_limit_orders` for every
    tenant's resting legacy limit orders. See this module's own docstring for why."""

    def __init__(self, interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS):
        self._interval = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.cycles_run = 0
        self.orders_filled = 0

    async def start(self) -> None:
        if self._running:
            logger.warning("PaperLimitOrderSweeper already running - start() ignored")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="paper_limit_order_sweeper")
        logger.info(
            "PaperLimitOrderSweeper started (interval=%ss)", self._interval
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PaperLimitOrderSweeper stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
                self.cycles_run += 1
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 - one bad cycle must not kill the loop
                logger.error("PaperLimitOrderSweeper cycle error: %s", e, exc_info=True)
            await asyncio.sleep(self._interval)

    async def run_once(self) -> int:
        """One sweep cycle. Returns how many orders were filled. Public for tests and for
        an operator-triggered one-off sweep outside the loop's own cadence."""
        from backend_app.backend.paper import paper_repository as repo
        from backend_app.backend.paper_trading_service import (
            get_fresh_market_price, get_paper_trading_service)
        from backend_app.core.dependencies import get_service_supabase

        sb = get_service_supabase()
        if sb is None:
            logger.warning(
                "PaperLimitOrderSweeper: no service-role client available; skipping this cycle"
            )
            return 0

        pairs = repo.get_open_legacy_limit_order_pairs(sb)
        if not pairs:
            return 0

        prices: Dict[str, Optional[Decimal]] = {}
        paper_svc = get_paper_trading_service()
        filled_count = 0

        for user_id, symbol in pairs:
            if symbol not in prices:
                prices[symbol] = await get_fresh_market_price(symbol)
            price = prices[symbol]
            if price is None:
                # Honest, not an error: nothing is currently streaming this symbol, so
                # there is no measured price to fill at. Same PAPER-02 principle: never
                # fabricate one.
                continue
            try:
                filled = await paper_svc.check_limit_orders(
                    symbol, price, user_id=user_id
                )
                filled_count += len(filled or [])
            except Exception as e:  # noqa: BLE001 - one tenant's failure must not stop the sweep
                logger.error(
                    "PaperLimitOrderSweeper: check_limit_orders failed for user=%s symbol=%s: %s",
                    user_id, symbol, e,
                )

        self.orders_filled += filled_count
        return filled_count


_sweeper: Optional[PaperLimitOrderSweeper] = None


def get_paper_limit_order_sweeper() -> PaperLimitOrderSweeper:
    global _sweeper
    if _sweeper is None:
        _sweeper = PaperLimitOrderSweeper()
    return _sweeper


__all__ = ["PaperLimitOrderSweeper", "get_paper_limit_order_sweeper"]
