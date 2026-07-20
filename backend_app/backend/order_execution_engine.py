"""
╔══════════════════════════════════════════════════════════════════════════╗
║  🚫 DEPRECATED — DO NOT USE — STEP 8 BLOCK UNSAFE EXECUTION           ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  This module is DEPRECATED and BLOCKED from use.                        ║
║                                                                          ║
║  STEP 8: BLOCK UNSAFE EXECUTION PATHS                                    ║
║                                                                          ║
║  This class previously allowed direct exchange execution, bypassing      ║
║  all safety checks and idempotency guarantees.                           ║
║                                                                          ║
║  MANDATORY REPLACEMENT:                                                  ║
║  from core.execution_engine import ExecutionEngine                       ║
║  await engine.execute_with_idempotency(...)                              ║
║                                                                          ║
║  Any attempt to use this class will raise RuntimeError.                ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
from typing import Optional

import ccxt.pro as ccxt

from backend_app.backend.services.logging_config import get_logger

logger = get_logger("OrderEngine")


class DeprecatedOrderEngine:
    """
    🚫 DEPRECATED AND BLOCKED - DO NOT USE
    
    This class previously allowed direct exchange execution, bypassing
    all safety checks and idempotency guarantees.
    
    STEP 8: Any attempt to use this class will raise RuntimeError.
    """

    def __init__(self, *args, **kwargs):
        # ═══════════════════════════════════════════════════════════
        # STEP 8: BLOCK UNSAFE EXECUTION - COMPLETELY DISABLED
        # ═══════════════════════════════════════════════════════════
        raise RuntimeError(
            "❌ OrderEngine is DEPRECATED and BLOCKED. "
            "Use UnifiedExecutionEngine (core.execution_engine.ExecutionEngine) instead. "
            "All execution MUST go through execute_with_idempotency()."
        )
        logger.critical(
            "🚫 DEPRECATED: OrderEngine instantiated. "
            "This engine is NOT SAFE for production. "
            "Use UnifiedExecutionEngine instead."
        )
        # ═══════════════════════════════════════════════════════════
        
        self.exchange = exchange_instance
        self.max_retries = max_retries
        self.user_id = user_id

    # ══════════════════════════════════════════════════════════════════════
    #  CORE EXECUTION
    # ══════════════════════════════════════════════════════════════════════

    async def execute_trade(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount,  # str or float — CCXT accepts strings
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """
        Places an order with precision formatting, pre-flight validation,
        and retry logic for transient network failures.

        FIX OE-6: Returns the order dict with `filled` field populated.
                  Callers should use order['filled'] not order['amount']
                  to update exposure tracking.
        """
        if params is None:
            params = {}

        try:
            # Format to exchange precision (keep as strings — CCXT recommendation)
            fmt_amount = self.exchange.amount_to_precision(symbol, float(amount))
            fmt_price = (
                self.exchange.price_to_precision(symbol, float(price))
                if price is not None
                else None
            )
            # Pre-flight: min lot size and notional checks
            self._pre_flight_check(
                symbol, float(fmt_amount), float(fmt_price) if fmt_price else None
            )

        except Exception as e:
            logger.error(
                "Pre-flight validation failed",
                symbol=symbol,
                user_id=self.user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ValueError(f"Pre-flight failed: {e}")

        for attempt in range(self.max_retries):
            try:
                logger.info(
                    "Order execution attempt",
                    attempt=attempt + 1,
                    max_attempts=self.max_retries,
                    order_type=order_type.upper(),
                    side=side.upper(),
                    symbol=symbol,
                    amount=fmt_amount,
                    price=fmt_price,
                    user_id=self.user_id,
                )
                order = await self.exchange.create_order(
                    symbol=symbol,
                    type=order_type,
                    side=side,
                    amount=fmt_amount,
                    price=fmt_price,
                    params=params,
                )
                logger.info(
                    "Order placed successfully",
                    order_id=order["id"],
                    status=order.get("status"),
                    symbol=symbol,
                    user_id=self.user_id,
                    filled=order.get("filled"),
                )
                return order  # FIX OE-6: caller reads order['filled']

            except (
                ccxt.InsufficientFunds,
                ccxt.InvalidOrder,
                ccxt.OrderNotFound,
                ccxt.PermissionDenied,
            ) as e:
                # Fatal — do not retry
                logger.error(
                    "Fatal order error - no retry",
                    symbol=symbol,
                    user_id=self.user_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

            except (ccxt.NetworkError, ccxt.RateLimitExceeded, ccxt.ExchangeError) as e:
                wait = 2**attempt
                logger.warning(
                    "Transient order error - retrying",
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    symbol=symbol,
                    user_id=self.user_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(wait)

        logger.error(
            "Order execution failed after all retries",
            symbol=symbol,
            user_id=self.user_id,
            max_retries=self.max_retries,
        )
        raise ConnectionError(
            f"Order execution failed after {self.max_retries} attempts on {symbol}."
        )

    # ══════════════════════════════════════════════════════════════════════
    #  CONDITIONAL ORDER WRAPPERS
    # ══════════════════════════════════════════════════════════════════════

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        is_futures: bool = False,
    ) -> dict:
        """
        Places a conditional stop-loss order.
        FIX OE-1: Changed type from 'market' (fires immediately) to
                  'stop_market' / 'stop' (fires only when price reaches stop).
                  CCXT unifies the trigger via 'triggerPrice' param.
        """
        fmt_stop = float(self.exchange.price_to_precision(symbol, stop_price))

        # CCXT unified conditional order type
        # 'stop_market' is the standard name; falls back to exchange-specific
        order_type = "stop_market"
        params: dict = {"triggerPrice": fmt_stop, "stopPrice": fmt_stop}

        if is_futures:
            params["reduceOnly"] = True  # prevent opening inverse position

        try:
            logger.info(
                "Placing stop-loss order",
                symbol=symbol,
                side=side,
                amount=amount,
                stop_price=stop_price,
                is_futures=is_futures,
                user_id=self.user_id,
            )
            return await self.execute_trade(
                symbol, order_type, side, amount, params=params
            )
        except ccxt.InvalidOrder:
            # Some exchanges use 'stop' not 'stop_market'
            logger.warning(
                "stop_market not accepted, trying 'stop'",
                exchange=self.exchange.id,
                symbol=symbol,
                user_id=self.user_id,
            )
            return await self.execute_trade(symbol, "stop", side, amount, params=params)

    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        amount: float,
        take_profit_price: float,
        is_futures: bool = False,
    ) -> dict:
        """
        Places a conditional take-profit order.
        FIX OE-2: Same fix as place_stop_loss — type changed from 'market'
                  (fires instantly) to 'take_profit_market'.
        """
        fmt_tp = float(self.exchange.price_to_precision(symbol, take_profit_price))

        order_type = "take_profit_market"
        params: dict = {"triggerPrice": fmt_tp, "stopPrice": fmt_tp}

        if is_futures:
            params["reduceOnly"] = True

        try:
            logger.info(
                "Placing take-profit order",
                symbol=symbol,
                side=side,
                amount=amount,
                take_profit_price=take_profit_price,
                is_futures=is_futures,
                user_id=self.user_id,
            )
            return await self.execute_trade(
                symbol, order_type, side, amount, params=params
            )
        except ccxt.InvalidOrder:
            logger.warning(
                "take_profit_market not accepted, trying 'take_profit'",
                exchange=self.exchange.id,
                symbol=symbol,
                user_id=self.user_id,
            )
            return await self.execute_trade(
                symbol, "take_profit", side, amount, params=params
            )

    async def place_native_trailing_stop(
        self,
        symbol: str,
        side: str,
        amount: float,
        callback_rate_pct: float,  # e.g. 2.0 = trail by 2%
        activation_price: Optional[float] = None,
        is_futures: bool = True,
    ) -> dict:
        """
        FIX OE-3: Checks exchange capability before attempting.
        Original always used 'TRAILING_STOP_MARKET' (Binance-only).
        Now uses CCXT unified 'trailingAmount' / 'trailingPercent' params.
        """
        # Check if exchange supports any trailing stop mechanism
        has_trailing = (
            self.exchange.has.get("createTrailingAmountOrder")
            or self.exchange.has.get("createTrailingPercentOrder")
            or "trailingPercent"
            in (self.exchange.options.get("supportedOrderTypes", {}))
        )

        if not has_trailing and not is_futures:
            raise NotImplementedError(
                f"{self.exchange.id} does not support native trailing stops on spot. "
                "Use a software trailing stop in the strategy engine instead."
            )

        params: dict = {"callbackRate": callback_rate_pct}
        if activation_price:
            params["activationPrice"] = float(
                self.exchange.price_to_precision(symbol, activation_price)
            )
        if is_futures:
            params["reduceOnly"] = True

        # Try Binance-style first, fall back to unified CCXT param
        try:
            return await self.execute_trade(
                symbol, "TRAILING_STOP_MARKET", side, amount, params=params
            )
        except ccxt.InvalidOrder:
            # CCXT unified trailing stop
            params["trailingPercent"] = callback_rate_pct
            return await self.execute_trade(
                symbol, "market", side, amount, params=params
            )

    # ══════════════════════════════════════════════════════════════════════
    #  ORDER MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════

    async def edit_order(
        self,
        order_id: str,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        FIX OE-7: NEW — Modifies an existing open order (price or qty).
        Falls back to cancel + replace if exchange doesn't support editOrder.
        """
        if params is None:
            params = {}

        if self.exchange.has.get("editOrder"):
            try:
                fmt_amount = self.exchange.amount_to_precision(symbol, amount)
                fmt_price = (
                    self.exchange.price_to_precision(symbol, float(price))
                    if price
                    else None
                )
                return await self.exchange.edit_order(
                    order_id, symbol, order_type, side, fmt_amount, fmt_price, params
                )
            except Exception as e:
                logger.warning(
                    f"editOrder failed: {e}. Falling back to cancel+replace."
                )

        # Cancel + replace fallback
        await self.cancel_order(order_id, symbol)
        return await self.execute_trade(symbol, order_type, side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancels a specific order by ID."""
        try:
            logger.info(
                "Cancelling order",
                order_id=order_id,
                symbol=symbol,
                user_id=self.user_id,
            )
            result = await self.exchange.cancel_order(order_id, symbol)
            logger.info(
                "Order cancelled successfully",
                order_id=order_id,
                symbol=symbol,
                user_id=self.user_id,
            )
            return result
        except Exception as e:
            logger.error(
                "Failed to cancel order",
                order_id=order_id,
                symbol=symbol,
                user_id=self.user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    async def cancel_all_safety_switch(self, symbol: Optional[str] = None) -> dict:
        """
        Emergency kill switch — cancels ALL open orders.
        FIX OE-4: Checks exchange.has before calling cancel_all_orders()
                  to avoid AttributeError on exchanges that don't support it.
        Falls back to fetching open orders and cancelling individually.
        """
        logger.warning(
            "KILL SWITCH: Cancelling all open orders",
            symbol=symbol,
            user_id=self.user_id,
        )

        if self.exchange.has.get("cancelAllOrders"):
            try:
                result = await self.exchange.cancel_all_orders(symbol)
                logger.info(
                    "Kill switch executed via cancel_all_orders",
                    symbol=symbol,
                    user_id=self.user_id,
                )
                return result
            except Exception as e:
                logger.warning(
                    "cancelAllOrders failed, falling back to individual cancel",
                    error=str(e),
                    symbol=symbol,
                    user_id=self.user_id,
                )

        # Fallback: cancel each open order individually
        try:
            open_orders = await self.exchange.fetch_open_orders(symbol)
        except Exception as e:
            logger.error(
                "Could not fetch open orders for individual cancel",
                error=str(e),
                symbol=symbol,
                user_id=self.user_id,
            )
            return {"cancelled": 0, "error": str(e)}

        cancelled = 0
        for order in open_orders:
            try:
                await self.exchange.cancel_order(order["id"], order["symbol"])
                cancelled += 1
            except Exception as e:
                logger.warning(
                    "Could not cancel order in kill switch",
                    order_id=order["id"],
                    error=str(e),
                    user_id=self.user_id,
                )

        logger.info(
            "Kill switch completed",
            cancelled=cancelled,
            total=len(open_orders),
            symbol=symbol,
            user_id=self.user_id,
        )
        return {"cancelled": cancelled, "total": len(open_orders)}

    # ══════════════════════════════════════════════════════════════════════
    #  PRE-FLIGHT VALIDATION
    # ══════════════════════════════════════════════════════════════════════

    def _pre_flight_check(
        self,
        symbol: str,
        amount_float: float,
        price_float: Optional[float],
    ):
        """
        Validates min lot size and min notional value before sending to exchange.
        Prevents instant API rejections that waste rate limit budget.
        """
        try:
            market = self.exchange.market(symbol)
        except Exception:
            return  # market info not available — skip check

        limits = market.get("limits", {})

        # Minimum lot size check
        min_amount = limits.get("amount", {}).get("min")
        if min_amount and amount_float < min_amount:
            raise ValueError(
                f"Amount {amount_float} is below exchange minimum lot size of {min_amount} "
                f"for {symbol}."
            )

        # Maximum lot size check
        max_amount = limits.get("amount", {}).get("max")
        if max_amount and amount_float > max_amount:
            raise ValueError(
                f"Amount {amount_float} exceeds exchange maximum lot size of {max_amount} "
                f"for {symbol}."
            )

        # Minimum notional value check
        if price_float:
            notional = amount_float * price_float
            min_cost = limits.get("cost", {}).get("min")
            max_cost = limits.get("cost", {}).get("max")

            if min_cost and notional < min_cost:
                raise ValueError(
                    f"Order notional ${notional:.2f} is below exchange minimum "
                    f"of ${min_cost:.2f} for {symbol}."
                )
            if max_cost and notional > max_cost:
                raise ValueError(
                    f"Order notional ${notional:.2f} exceeds exchange maximum "
                    f"of ${max_cost:.2f} for {symbol}."
                )
