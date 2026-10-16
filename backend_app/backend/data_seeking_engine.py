"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 2 — CONNECTION: data_seeking_engine.py                            ║
║                                                                          ║
║  All market data — WebSocket streams and REST snapshots via CCXT.pro.   ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  DS-1  fetch_historical_ohlcv flat 1s retry — now exponential backoff   ║
║  DS-2  No pagination — silently returns ≤500 bars from limit=1000        ║
║  DS-3  Missing fetch_my_trades()                                          ║
║  DS-4  Missing fetch_trading_fees()                                       ║
║  DS-5  Missing fetch_open_positions()                                     ║
║  DS-6  Missing fetch_funding_rate()                                       ║
║  DS-7  stream_live_ticks yielded entire array — processed 50x per msg   ║
║  DS-8  Missing stream_my_trades() WebSocket                               ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
from typing import AsyncIterator, Optional

import ccxt as ccxt_base

logger = logging.getLogger("DataEngine")


class InsufficientHistoryError(RuntimeError):
    """The exchange holds fewer historical bars than the caller required (DATA-01)."""


class DataEngine:
    """
    All market data access for one authenticated exchange connection.
    Injected with a live CCXT exchange instance from ConnectionEngine.
    """

    def __init__(self, exchange_instance):
        self.exchange = exchange_instance

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC MARKET DATA — WebSocket streams
    # ══════════════════════════════════════════════════════════════════════

    async def stream_live_ticks(self, symbol: str) -> AsyncIterator[dict]:
        """
        Streams individual public trade ticks.
        FIX DS-7: Yields one tick at a time instead of the whole array,
                  so BotRunner processes exactly one tick per yield.
                  Original yielded the full list — N trades in one WS burst
                  caused N ML inference calls for a single candle close.
        """
        if not self.exchange.has.get("watchTrades"):
            logger.error(f"Exchange does not support watchTrades for {symbol}")
            return

        while True:
            try:
                trades = await self.exchange.watch_trades(symbol)
                # FIX DS-7: yield one trade at a time, not the full batch
                for trade in trades:
                    yield trade
            except asyncio.CancelledError:
                # RT-01: re-raise. This used to `break`, which made a CANCELLED stream look like
                # one that had simply ended, so the consumer could not tell a shutdown from the
                # venue dropping the feed.
                raise
            except Exception as e:
                await self._handle_stream_error("Tick Stream", e)

    async def stream_live_ohlcv(
        self, symbol: str, timeframe: str = "1m"
    ) -> AsyncIterator[list]:
        """Streams live forming candle updates from the centralized MDS."""
        import json

        from backend_app.core.cache import redis_manager
        
        exchange_id = self.exchange.id.lower()
        channel = f"mds:data:{exchange_id}:{symbol}"
        
        # Wake up MDS
        await redis_manager.redis.publish("mds:commands", json.dumps({
            "action": "subscribe",
            "exchange": exchange_id,
            "symbol": symbol
        }))
        
        pubsub = redis_manager.redis.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    payload = json.loads(message["data"])
                    # Yield in ccxt OHLCV format: [ts, open, high, low, close, volume]
                    # Note: ccxt watch_ohlcv yields a list of candles, so we wrap in list
                    candle = [
                        payload["timestamp"],
                        payload["open"],
                        payload["high"],
                        payload["low"],
                        payload["close"],
                        payload["volume"]
                    ]
                    yield [candle]
        except asyncio.CancelledError:
            await pubsub.unsubscribe(channel)
            raise
        except Exception as e:
            await self._handle_stream_error("MDS OHLCV Stream", e)

    async def stream_order_book(self, symbol: str) -> AsyncIterator[dict]:
        """Streams L2 order book depth updates."""
        if not self.exchange.has.get("watchOrderBook"):
            return
        while True:
            try:
                yield await self.exchange.watch_order_book(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_stream_error("OrderBook Stream", e)

    async def stream_ticker(self, symbol: str) -> AsyncIterator[dict]:
        """Streams 24h rolling ticker: last price, high, low, volume, %change."""
        if not self.exchange.has.get("watchTicker"):
            return
        while True:
            try:
                yield await self.exchange.watch_ticker(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_stream_error("Ticker Stream", e)

    # ══════════════════════════════════════════════════════════════════════
    #  PRIVATE USER DATA — WebSocket streams
    # ══════════════════════════════════════════════════════════════════════

    async def stream_user_balance(self) -> AsyncIterator[dict]:
        """Streams wallet balance updates after fills."""
        if not self.exchange.has.get("watchBalance"):
            return
        while True:
            try:
                yield await self.exchange.watch_balance()
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_stream_error("Balance Stream", e)

    async def stream_user_orders(
        self, symbol: Optional[str] = None
    ) -> AsyncIterator[list]:
        """Streams open order status changes (Open → Partially Filled → Filled)."""
        if not self.exchange.has.get("watchOrders"):
            return
        while True:
            try:
                yield await self.exchange.watch_orders(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_stream_error("Orders Stream", e)

    async def stream_my_trades(
        self, symbol: Optional[str] = None
    ) -> AsyncIterator[list]:
        """
        FIX DS-8: NEW — Streams private trade fills as they happen.
        Used by BotRunner for real-time filled quantity tracking
        instead of estimating from order qty.
        """
        if not self.exchange.has.get("watchMyTrades"):
            logger.warning(
                "Exchange does not support watchMyTrades. Falling back to watchOrders."
            )
            return
        while True:
            try:
                yield await self.exchange.watch_my_trades(symbol)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_stream_error("MyTrades Stream", e)

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC MARKET DATA — REST snapshots
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _timeframe_ms(timeframe: str) -> int:
        """Milliseconds per bar for a ccxt-style timeframe (``1m``, ``15m``, ``4h``, ``1d``...)."""
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 2_592_000}
        try:
            return int(timeframe[:-1]) * units[timeframe[-1]] * 1000
        except (ValueError, KeyError, IndexError):
            raise ValueError(f"unsupported timeframe {timeframe!r}") from None

    async def _fetch_ohlcv_page(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int],
        batch_size: int,
        max_retries: int,
    ) -> list:
        """One ``fetch_ohlcv`` call, retried with exponential backoff (FIX DS-1)."""
        for attempt in range(max_retries):
            try:
                return await self.exchange.fetch_ohlcv(
                    symbol, timeframe, since=since, limit=batch_size
                )
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"fetch_historical_ohlcv failed for {symbol} "
                        f"after {max_retries} attempts: {e}"
                    )
                    raise
                wait = 2**attempt
                logger.warning(f"OHLCV fetch error: {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
        return []  # unreachable: the loop above always returns or raises

    async def fetch_historical_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 1_000,
        max_retries: int = 3,
        require_full: bool = False,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
    ) -> list:
        """
        Fetches OHLCV bars, oldest first, in one of two modes.

        ``since_ms`` and ``until_ms`` both ``None`` (the default): up to ``limit`` of the
        MOST RECENT bars. DATA-01: this used to page FORWARD, which silently returned at
        most ~500 bars however large ``limit`` was ("since the newest page, there is
        nothing newer"). It now pages BACKWARD: after the latest page, each request starts
        one page-width before the oldest bar held and keeps only bars strictly older.

        ``since_ms`` given (BT-02): bars from that instant forward, up to ``until_ms``
        (exclusive) if given, else up to ``limit`` bars or the latest bar the exchange
        holds. This is the mode a caller with an explicit historical window needs -
        BEFORE this existed, every caller that asked for a specific ``start_date``/
        ``end_date`` (a backtest, an optimization trial, a walk-forward window) received
        this same "most recent" mode regardless, so a backtest requested over 2020 silently
        ran on whatever the most recent bars happened to be when it was called. That
        defect is NOT in this function - it is in every caller that had a date range to
        honour and called the no-argument mode anyway; this mode is what lets them stop.

        Coverage is never claimed silently in either mode. A short result logs a WARNING
        naming how many bars were wanted and how many exist; with ``require_full=True`` it
        raises ``InsufficientHistoryError`` instead, for consumers that must not run on
        less.

        FIX DS-2: Paginated fetch; most exchanges cap at 500 per call.
        """
        exchange_limit = 500  # conservative cap that works for all exchanges
        tf_ms = self._timeframe_ms(timeframe)

        if since_ms is not None:
            return await self._fetch_ohlcv_bounded(
                symbol, timeframe, since_ms, until_ms, limit, max_retries,
                exchange_limit, tf_ms, require_full,
            )

        all_bars: list[list] = []
        oldest: Optional[int] = None  # oldest bar timestamp collected so far

        while len(all_bars) < limit:
            batch_size = min(exchange_limit, limit - len(all_bars))
            since = None if oldest is None else oldest - batch_size * tf_ms

            batch = await self._fetch_ohlcv_page(
                symbol, timeframe, since, batch_size, max_retries
            )

            if oldest is not None:
                # Only bars strictly older than what we already hold are new information.
                batch = [bar for bar in (batch or []) if bar[0] < oldest]
            if not batch:
                break  # no older data available

            all_bars.extend(batch)
            batch_oldest = min(bar[0] for bar in batch)
            if oldest is not None and batch_oldest >= oldest:
                break  # defensive: no progress
            oldest = batch_oldest

        # Return sorted, deduplicated, most recent `limit` bars
        unique = {bar[0]: bar for bar in all_bars}
        sorted_bars = sorted(unique.values(), key=lambda b: b[0])[-limit:]

        if len(sorted_bars) < limit:
            msg = (
                f"fetch_historical_ohlcv({symbol}, {timeframe}): wanted {limit} bars but "
                f"only {len(sorted_bars)} are available from the exchange"
            )
            if require_full:
                raise InsufficientHistoryError(msg)
            logger.warning(msg)
        return sorted_bars

    async def _fetch_ohlcv_bounded(
        self,
        symbol: str,
        timeframe: str,
        since_ms: int,
        until_ms: Optional[int],
        limit: int,
        max_retries: int,
        exchange_limit: int,
        tf_ms: int,
        require_full: bool,
    ) -> list:
        """BT-02: bars from ``since_ms`` forward, bounded by ``until_ms`` and/or ``limit``.

        Pages FORWARD rather than backward: the window's start is known and fixed, so each
        page asks for the bars immediately after the last one collected, exactly as
        ``since=last_ts+one_bar`` naturally does for ccxt's own "bars at or after ``since``"
        contract. A page returning nothing, or a page entirely at or past ``until_ms``,
        ends the fetch - the latter trims rather than discards, so a window whose end
        falls mid-page is not silently rounded up to the next bar past it.
        """
        all_bars: list[list] = []
        since = since_ms

        while len(all_bars) < limit:
            if until_ms is not None and since >= until_ms:
                break
            batch_size = min(exchange_limit, limit - len(all_bars))
            batch = await self._fetch_ohlcv_page(
                symbol, timeframe, since, batch_size, max_retries
            )
            if not batch:
                break  # no more data at or after `since`

            if until_ms is not None:
                in_range = [bar for bar in batch if bar[0] < until_ms]
                hit_the_end = len(in_range) < len(batch)
                batch = in_range
            else:
                hit_the_end = False

            if not batch:
                break

            all_bars.extend(batch)
            newest = max(bar[0] for bar in batch)
            if newest < since:
                break  # defensive: no progress
            since = newest + tf_ms
            if hit_the_end:
                break

        unique = {bar[0]: bar for bar in all_bars}
        sorted_bars = sorted(unique.values(), key=lambda b: b[0])[:limit]

        if len(sorted_bars) < limit and until_ms is None:
            # An open-ended window ("from X onward") that came up short is the same
            # under-coverage DATA-01 already warns on. A CLOSED window (`until_ms` given)
            # legitimately returns fewer than `limit` bars whenever the window itself
            # holds fewer - that is not under-coverage, it is the window being short, so
            # it is not warned on here.
            msg = (
                f"fetch_historical_ohlcv({symbol}, {timeframe}, since_ms={since_ms}): wanted "
                f"{limit} bars but only {len(sorted_bars)} are available from the exchange"
            )
            if require_full:
                raise InsufficientHistoryError(msg)
            logger.warning(msg)
        return sorted_bars

    async def fetch_wallet_balance_snapshot(self) -> dict:
        """Fetches total, free, and used balances for all assets."""
        return await self.exchange.fetch_balance()

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list:
        """Fetches all currently open (unexecuted) orders."""
        if not self.exchange.has.get("fetchOpenOrders"):
            logger.warning("Exchange does not support fetchOpenOrders.")
            return []
        return await self.exchange.fetch_open_orders(symbol)

    async def fetch_order_by_id(self, order_id: str, symbol: str) -> dict:
        """Fetches a single order by ID for fill status verification."""
        return await self.exchange.fetch_order(order_id, symbol)

    # ══════════════════════════════════════════════════════════════════════
    #  PRIVATE USER DATA — REST snapshots (FIX DS-3 to DS-6: NEW methods)
    # ══════════════════════════════════════════════════════════════════════

    async def fetch_my_trades(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: int = 100,
    ) -> list:
        """
        FIX DS-3: NEW — Fetches the user's historical executed trades.
        Used for P&L calculation, trade history page, and audit logs.
        """
        if not self.exchange.has.get("fetchMyTrades"):
            logger.warning("Exchange does not support fetchMyTrades.")
            return []
        return await self.exchange.fetch_my_trades(symbol, since, limit)

    async def fetch_trading_fees(self, symbol: Optional[str] = None) -> dict:
        """
        FIX DS-4: NEW — Fetches real maker/taker fee rates.
        Critical for accurate P&L and position sizing.
        Without this, fee estimates may be wrong by 3-10x on VIP tiers.
        """
        try:
            if symbol and self.exchange.has.get("fetchTradingFee"):
                return await self.exchange.fetch_trading_fee(symbol)
            elif self.exchange.has.get("fetchTradingFees"):
                fees = await self.exchange.fetch_trading_fees()
                return fees.get(symbol, fees) if symbol else fees
            else:
                # Fallback: return the exchange's default fee from markets
                if symbol:
                    market = self.exchange.market(symbol)
                    return {
                        "maker": market.get("maker", 0.001),
                        "taker": market.get("taker", 0.001),
                    }
                return {"maker": 0.001, "taker": 0.001}
        except Exception as e:
            logger.warning(f"fetch_trading_fees error: {e}. Using defaults.")
            return {"maker": 0.001, "taker": 0.001}

    async def fetch_open_positions(self, symbols: Optional[list] = None) -> list:
        """
        FIX DS-5: NEW — Fetches active futures/margin positions.
        Required before opening a new position in hedge mode to avoid
        accidentally doubling up or exceeding position limits.
        """
        if not self.exchange.has.get("fetchPositions"):
            logger.warning("Exchange does not support fetchPositions (spot only?).")
            return []
        try:
            return await self.exchange.fetch_positions(symbols)
        except Exception as e:
            logger.error(f"fetch_open_positions error: {e}")
            return []

    async def fetch_funding_rate(self, symbol: str) -> Optional[dict]:
        """
        FIX DS-6: NEW — Fetches current perpetual futures funding rate.
        Perpetual bots must account for funding cost in P&L calculations.
        A 0.01% funding rate every 8 hours = 0.03% daily = ~11% annually.
        """
        if not self.exchange.has.get("fetchFundingRate"):
            return None
        try:
            return await self.exchange.fetch_funding_rate(symbol)
        except Exception as e:
            logger.warning(f"fetch_funding_rate error for {symbol}: {e}")
            return None

    async def fetch_funding_rate_history(
        self, symbol: str, since: Optional[int] = None, limit: int = 100
    ) -> list:
        """Fetches historical funding rates for strategy backtesting on perpetuals."""
        if not self.exchange.has.get("fetchFundingRateHistory"):
            return []
        try:
            return await self.exchange.fetch_funding_rate_history(symbol, since, limit)
        except Exception as e:
            logger.warning(f"fetch_funding_rate_history error: {e}")
            return []

    async def fetch_order_book_snapshot(self, symbol: str, limit: int = 20) -> dict:
        """One-time L2 order book snapshot (for dashboard display)."""
        return await self.exchange.fetch_order_book(symbol, limit)

    async def fetch_ticker_snapshot(self, symbol: str) -> dict:
        """One-time 24h ticker snapshot."""
        return await self.exchange.fetch_ticker(symbol)

    # ══════════════════════════════════════════════════════════════════════
    #  ERROR HANDLING
    # ══════════════════════════════════════════════════════════════════════

    async def _handle_stream_error(self, stream_name: str, error: Exception):
        """
        Classifies errors and applies appropriate recovery strategy.
        Fatal errors are re-raised to kill the bot (prevents IP ban).
        Network errors trigger a brief reconnection pause.
        """
        if isinstance(
            error,
            (ccxt_base.AuthenticationError, ccxt_base.PermissionDenied, ccxt_base.AccountSuspended),
        ):
            logger.critical(
                f"FATAL {stream_name} error: {error}. "
                "Killing stream to prevent IP ban / account action."
            )
            raise error  # propagates to BotRunner._run_trading_loop() catch

        elif isinstance(error, ccxt_base.NetworkError):
            logger.warning(
                f"{stream_name} network glitch: {error}. Reconnecting in 2s..."
            )
            await asyncio.sleep(2)

        elif isinstance(error, ccxt_base.RateLimitExceeded):
            logger.warning(f"{stream_name} rate limited. Cooling down for 10s...")
            await asyncio.sleep(10)

        else:
            logger.error(
                f"{stream_name} unexpected error: {error}. Cooling down for 5s..."
            )
            await asyncio.sleep(5)
