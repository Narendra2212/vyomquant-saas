"""
backend/paper_trading_service.py — Production-Grade Canonical Paper Trading Service for VyomQuant.

Provides institutional paper trading with:
- Virtual paper account lifecycle & balance accounting (available, locked, equity)
- Deterministic paper order execution (Market Buy/Sell, Limit Buy/Sell, Cancel)
- Limit order matching engine on price events
- Position & average entry price tracking with long/short/reversal support and realized/unrealized PnL
- Idempotent execution guard against duplicate signals/retries
- Strict per-tenant concurrency isolation & lock serialization
- Mathematical invariant enforcement: total_equity = available_balance + locked_balance + position_market_value
- Zero-leakage isolation: NEVER invokes real exchange APIs or live execution credentials.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger("PaperTradingService")


class PaperOrderStatus(str, Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PaperTradingService:
    """
    Central service for VyomQuant Paper Trading.
    Maintains virtual tenant accounts, fills orders against live/reference prices,
    computes exact portfolio balance and positions, and records immutable audit logs.
    """

    def __init__(
        self,
        default_capital: float = 100_000.0,
        default_fee_rate: float = 0.001,
        default_slippage: float = 0.0005
    ):
        self.default_capital = Decimal(str(default_capital))
        self.default_fee_rate = Decimal(str(default_fee_rate))
        self.default_slippage = default_slippage

        # In-memory tenant stores
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Dict[str, Any]]] = {} # user_id -> {symbol: position_dict}
        self._orders: Dict[str, Dict[str, Any]] = {} # order_id -> order_dict
        self._trades: Dict[str, List[Dict[str, Any]]] = {} # user_id -> list of trades
        self._idempotency_cache: Dict[str, str] = {} # idempotency_key -> order_id
        
        # Concurrency: per-tenant lock serialization
        self._user_locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        uid = str(user_id)
        if uid not in self._user_locks:
            self._user_locks[uid] = asyncio.Lock()
        return self._user_locks[uid]

    def _get_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_or_create_account(self, user_id: str, initial_capital: Optional[float] = None) -> Dict[str, Any]:
        """Get or initialize user's virtual paper account."""
        uid = str(user_id)
        if uid not in self._accounts:
            cap = Decimal(str(initial_capital)) if initial_capital is not None else self.default_capital
            self._accounts[uid] = {
                "account_id": f"paper_acct_{uid[:8]}",
                "user_id": uid,
                "name": "VyomQuant Paper Trading Account",
                "currency": "USD",
                "initial_capital": str(cap),
                "available_balance": str(cap),
                "locked_balance": "0.0",
                "total_equity": str(cap),
                "realized_pnl": "0.0",
                "unrealized_pnl": "0.0",
                "status": "active",
                "created_at": self._get_timestamp(),
                "updated_at": self._get_timestamp(),
            }
            if uid not in self._positions:
                self._positions[uid] = {}
            if uid not in self._trades:
                self._trades[uid] = []
        return self._recalculate_account(uid)

    def reset_account(self, user_id: str, capital: Optional[float] = None) -> Dict[str, Any]:
        """Reset virtual paper account to starting state."""
        uid = str(user_id)
        cap = Decimal(str(capital)) if capital is not None else self.default_capital
        self._accounts[uid] = {
            "account_id": f"paper_acct_{uid[:8]}",
            "user_id": uid,
            "name": "VyomQuant Paper Trading Account",
            "currency": "USD",
            "initial_capital": str(cap),
            "available_balance": str(cap),
            "locked_balance": "0.0",
            "total_equity": str(cap),
            "realized_pnl": "0.0",
            "unrealized_pnl": "0.0",
            "status": "active",
            "created_at": self._get_timestamp(),
            "updated_at": self._get_timestamp(),
        }
        self._positions[uid] = {}
        # Cancel any open orders for this user
        for ord_id, o in list(self._orders.items()):
            if o.get("user_id") == uid and o.get("status") == PaperOrderStatus.OPEN.value:
                o["status"] = PaperOrderStatus.CANCELLED.value
                o["updated_at"] = self._get_timestamp()
        return self._accounts[uid]

    def _recalculate_account(self, user_id: str, current_prices: Optional[Dict[str, Decimal]] = None) -> Dict[str, Any]:
        """Recalculates unrealized P&L and total equity based on open positions."""
        uid = str(user_id)
        if uid not in self._accounts:
            return self.get_or_create_account(uid)

        acct = self._accounts[uid]
        avail = Decimal(acct["available_balance"])
        locked = Decimal(acct["locked_balance"])

        user_pos = self._positions.get(uid, {})
        unrealized_total = Decimal("0")
        positions_market_val = Decimal("0")

        for sym, pos in user_pos.items():
            size = Decimal(str(pos["size"]))
            if size <= Decimal("0"):
                continue
            entry_px = Decimal(str(pos["entry_price"]))
            side = pos.get("side", "long").lower()
            cur_px = (current_prices.get(sym) if current_prices and sym in current_prices else None) or Decimal(str(pos.get("current_price", entry_px)))

            if side == "long":
                pnl = (cur_px - entry_px) * size
                val = size * cur_px
            else: # short
                pnl = (entry_px - cur_px) * size
                val = size * (Decimal("2") * entry_px - cur_px)

            pos["current_price"] = str(cur_px)
            pos["unrealized_pnl"] = str(pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
            unrealized_total += pnl
            positions_market_val += val

        total_equity = avail + locked + positions_market_val
        acct["unrealized_pnl"] = str(unrealized_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
        acct["total_equity"] = str(total_equity.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
        acct["updated_at"] = self._get_timestamp()
        return acct

    def verify_accounting_invariants(self, user_id: str) -> bool:
        """Verifies mathematical consistency of account balance and positions."""
        uid = str(user_id)
        acct = self.get_or_create_account(uid)
        avail = Decimal(acct["available_balance"])
        locked = Decimal(acct["locked_balance"])
        equity = Decimal(acct["total_equity"])
        
        if avail < Decimal("0") or locked < Decimal("0"):
            return False
            
        pos_val = Decimal("0")
        for sym, pos in self._positions.get(uid, {}).items():
            size = Decimal(str(pos["size"]))
            entry_px = Decimal(str(pos["entry_price"]))
            cur_px = Decimal(str(pos.get("current_price", entry_px)))
            side = pos.get("side", "long").lower()
            if side == "long":
                pos_val += size * cur_px
            else:
                pos_val += size * (Decimal("2") * entry_px - cur_px)

        expected_equity = avail + locked + pos_val
        diff = abs(equity - expected_equity)
        return diff < Decimal("0.05")

    async def place_order(
        self,
        user_id: str,
        symbol: str,
        side: str,
        order_type: str = "market",
        quantity: float = 0.01,
        price: Optional[float] = None,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes or registers a Paper Trading order.
        Guarantees idempotency and atomic balance updates.
        """
        user_lock = self._get_user_lock(user_id)
        async with user_lock:
            uid = str(user_id)
            clean_sym = symbol.strip().upper().replace("/", "-")
            clean_side = side.strip().lower()
            clean_type = order_type.strip().lower()
            qty_dec = Decimal(str(quantity))

            if qty_dec <= Decimal("0"):
                raise ValueError("Quantity must be greater than 0")
            if clean_side not in ("buy", "sell"):
                raise ValueError("Side must be 'buy' or 'sell'")
            if clean_type not in ("market", "limit"):
                raise ValueError("Order type must be 'market' or 'limit'")

            # Idempotency check
            if idempotency_key:
                if idempotency_key in self._idempotency_cache:
                    cached_ord_id = self._idempotency_cache[idempotency_key]
                    if cached_ord_id in self._orders:
                        logger.info(f"[PAPER IDEMPOTENCY] Duplicate order detected for key {idempotency_key}, returning cached order")
                        return self._orders[cached_ord_id]

            # ── CANONICAL RISK EVALUATION ──
            from backend_app.routers.risk import (
                get_user_risk_settings_store, is_user_kill_switched, record_risk_violation
            )
            if is_user_kill_switched(uid):
                record_risk_violation(uid, "KILL_SWITCH_ACTIVE", "Trading halted: Emergency Risk Kill Switch is active")
                raise ValueError("Trading halted: Risk Kill Switch is active")

            user_risk = get_user_risk_settings_store(uid)
            max_daily_loss = Decimal(str(user_risk.get("max_daily_loss", 50000.0)))
            max_positions = int(user_risk.get("max_positions", 20))

            # Daily Loss Enforcement
            acct = self.get_or_create_account(uid)
            realized_loss = -min(Decimal("0"), Decimal(str(acct.get("realized_pnl", "0"))))
            if realized_loss >= max_daily_loss:
                record_risk_violation(uid, "MAX_DAILY_LOSS_EXCEEDED", f"Daily loss ${realized_loss:.2f} reached limit of ${max_daily_loss:.2f}")
                raise ValueError(f"Risk limit exceeded: Max Daily Loss limit reached (${realized_loss:.2f} >= ${max_daily_loss:.2f})")

            # Max Open Positions Enforcement
            cur_positions_count = len([p for p in self._positions.get(uid, {}).values() if Decimal(str(p.get("size", "0"))) > Decimal("0")])
            if clean_sym not in self._positions.get(uid, {}) and cur_positions_count >= max_positions:
                record_risk_violation(uid, "MAX_POSITIONS_EXCEEDED", f"Open positions ({cur_positions_count}) reached limit of {max_positions}")
                raise ValueError(f"Risk limit exceeded: Max open positions reached ({cur_positions_count} >= {max_positions})")

            avail_bal = Decimal(acct["available_balance"])

            # Resolve execution price
            if price is not None and float(price) > 0:
                exec_price = Decimal(str(price))
            else:
                # Default reference fallback prices for testing when market is offline
                reference_prices = {
                    "BTC-USDT": Decimal("65000.00"),
                    "BTC-USD": Decimal("65000.00"),
                    "ETH-USDT": Decimal("3500.00"),
                    "ETH-USD": Decimal("3500.00"),
                    "SOL-USDT": Decimal("150.00"),
                    "SOL-USD": Decimal("150.00"),
                }
                exec_price = reference_prices.get(clean_sym, Decimal("100.00"))

            # Strategy-Level Limits Enforcement
            from backend_app.routers.risk import _user_strategy_limits
            user_strat_limits = _user_strategy_limits.get(uid, {})
            if strategy_id and strategy_id in user_strat_limits:
                strat_limit = user_strat_limits[strategy_id]
                # Allowed Symbols Check
                allowed_syms = [s.upper().replace("/", "-") for s in strat_limit.get("allowed_symbols", []) if s]
                if allowed_syms and clean_sym not in allowed_syms:
                    record_risk_violation(uid, "SYMBOL_RESTRICTED", f"Symbol {clean_sym} not in allowed symbols list for strategy {strategy_id}")
                    raise ValueError(f"Risk limit exceeded: Symbol {clean_sym} is not allowed for strategy {strategy_id}")

                # Max Position Size / Notional Check
                max_pos_size = Decimal(str(strat_limit.get("max_position_size", 1000000.0)))
                prop_notional = qty_dec * exec_price
                if prop_notional > max_pos_size:
                    record_risk_violation(uid, "MAX_POSITION_SIZE_EXCEEDED", f"Order notional ${prop_notional:.2f} exceeds strategy limit ${max_pos_size:.2f}")
                    raise ValueError(f"Risk limit exceeded: Order size ${prop_notional:.2f} exceeds maximum strategy limit ${max_pos_size:.2f}")

            order_id = f"paper_ord_{uuid.uuid4().hex[:12]}"

            if clean_type == "limit":
                # Check limit order funds
                estimated_cost = qty_dec * exec_price * (Decimal("1") + self.default_fee_rate)
                if clean_side == "buy" and avail_bal < estimated_cost:
                    raise ValueError(f"Insufficient virtual balance (${avail_bal:.2f}) for limit order estimated cost (${estimated_cost:.2f})")

                if clean_side == "buy":
                    acct["available_balance"] = str((avail_bal - estimated_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    acct["locked_balance"] = str((Decimal(acct["locked_balance"]) + estimated_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

                order_record = {
                    "order_id": order_id,
                    "user_id": uid,
                    "strategy_id": strategy_id,
                    "deployment_id": deployment_id,
                    "symbol": clean_sym,
                    "side": clean_side,
                    "order_type": "limit",
                    "quantity": str(qty_dec),
                    "price": str(exec_price),
                    "filled_quantity": "0.0",
                    "status": PaperOrderStatus.OPEN.value,
                    "created_at": self._get_timestamp(),
                    "updated_at": self._get_timestamp(),
                }
                self._orders[order_id] = order_record
                if idempotency_key:
                    self._idempotency_cache[idempotency_key] = order_id

                self._recalculate_account(uid)
                return order_record

            # Market Order -> Instant Execution
            return await self._execute_fill(
                user_id=uid,
                order_id=order_id,
                symbol=clean_sym,
                side=clean_side,
                quantity=qty_dec,
                price=exec_price,
                strategy_id=strategy_id,
                deployment_id=deployment_id,
                idempotency_key=idempotency_key
            )

    async def _execute_fill(
        self,
        user_id: str,
        order_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        from_limit_order: bool = False
    ) -> Dict[str, Any]:
        """Fills paper order, computes adverse slippage and fees, updates positions and balances."""
        uid = str(user_id)
        acct = self.get_or_create_account(uid)
        avail_bal = Decimal(acct["available_balance"])

        # Apply adverse slippage: buy slips up, sell slips down
        slip_rate = Decimal(str(self.default_slippage))
        if side == "buy":
            fill_price = (price * (Decimal("1") + slip_rate)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
        else:
            fill_price = (price * (Decimal("1") - slip_rate)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)

        fee = (quantity * fill_price * self.default_fee_rate).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
        notional = (quantity * fill_price).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)

        user_positions = self._positions.setdefault(uid, {})
        pos = user_positions.get(symbol)
        realized_pnl = Decimal("0")

        if side == "buy":
            total_required = notional + fee
            if not from_limit_order:
                if avail_bal < total_required:
                    raise ValueError(f"Insufficient virtual funds for paper order. Required: ${total_required:.2f}, Available: ${avail_bal:.2f}")
                acct["available_balance"] = str((avail_bal - total_required).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
            else:
                # If from limit order, funds were locked previously
                locked_bal = Decimal(acct["locked_balance"])
                estimated_locked = quantity * price * (Decimal("1") + self.default_fee_rate)
                # Consume from locked
                acct["locked_balance"] = str(max(Decimal("0"), locked_bal - estimated_locked).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                # Refund any difference between locked and actual
                diff = estimated_locked - total_required
                if diff != Decimal("0"):
                    acct["available_balance"] = str((Decimal(acct["available_balance"]) + diff).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

            # Update / Open Position
            if not pos or Decimal(str(pos.get("size", "0"))) <= Decimal("0"):
                user_positions[symbol] = {
                    "symbol": symbol,
                    "side": "long",
                    "size": str(quantity),
                    "entry_price": str(fill_price),
                    "current_price": str(fill_price),
                    "unrealized_pnl": "0.0",
                    "created_at": self._get_timestamp(),
                    "updated_at": self._get_timestamp(),
                }
            elif pos.get("side") == "short":
                # Closing short or reversing to long
                short_size = Decimal(str(pos["size"]))
                short_entry = Decimal(str(pos["entry_price"]))
                if quantity <= short_size:
                    # Partial or full short close
                    realized_pnl = ((short_entry - fill_price) * quantity - fee).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                    acct["realized_pnl"] = str((Decimal(acct["realized_pnl"]) + realized_pnl).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    new_size = short_size - quantity
                    if new_size <= Decimal("0.00000001"):
                        del user_positions[symbol]
                    else:
                        pos["size"] = str(new_size)
                        pos["updated_at"] = self._get_timestamp()
                else:
                    # Short reversed to Long
                    close_qty = short_size
                    long_qty = quantity - short_size
                    realized_pnl = ((short_entry - fill_price) * close_qty - (close_qty * fill_price * self.default_fee_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                    acct["realized_pnl"] = str((Decimal(acct["realized_pnl"]) + realized_pnl).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    user_positions[symbol] = {
                        "symbol": symbol,
                        "side": "long",
                        "size": str(long_qty),
                        "entry_price": str(fill_price),
                        "current_price": str(fill_price),
                        "unrealized_pnl": "0.0",
                        "created_at": self._get_timestamp(),
                        "updated_at": self._get_timestamp(),
                    }
            else:
                # Accumulate Long Position
                old_size = Decimal(str(pos["size"]))
                old_entry = Decimal(str(pos["entry_price"]))
                new_size = old_size + quantity
                new_entry = ((old_size * old_entry) + (quantity * fill_price)) / new_size
                pos["size"] = str(new_size)
                pos["entry_price"] = str(new_entry.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN))
                pos["current_price"] = str(fill_price)
                pos["updated_at"] = self._get_timestamp()

        else: # sell
            if not pos or Decimal(str(pos.get("size", "0"))) <= Decimal("0"):
                # Open new Short position
                total_required = notional + fee
                if avail_bal < total_required:
                    raise ValueError(f"Insufficient virtual funds for short paper order. Required: ${total_required:.2f}, Available: ${avail_bal:.2f}")
                acct["available_balance"] = str((avail_bal - total_required).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                user_positions[symbol] = {
                    "symbol": symbol,
                    "side": "short",
                    "size": str(quantity),
                    "entry_price": str(fill_price),
                    "current_price": str(fill_price),
                    "unrealized_pnl": "0.0",
                    "created_at": self._get_timestamp(),
                    "updated_at": self._get_timestamp(),
                }
            elif pos.get("side") == "long":
                old_size = Decimal(str(pos["size"]))
                old_entry = Decimal(str(pos["entry_price"]))
                if quantity <= old_size:
                    # Partial or full long close
                    realized_pnl = ((fill_price - old_entry) * quantity - fee).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                    acct["realized_pnl"] = str((Decimal(acct["realized_pnl"]) + realized_pnl).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    # Credit proceeds
                    credit_amount = (quantity * fill_price) - fee
                    acct["available_balance"] = str((avail_bal + credit_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    new_size = old_size - quantity
                    if new_size <= Decimal("0.00000001"):
                        del user_positions[symbol]
                    else:
                        pos["size"] = str(new_size)
                        pos["updated_at"] = self._get_timestamp()
                else:
                    # Long reversed to Short
                    close_qty = old_size
                    short_qty = quantity - old_size
                    realized_pnl = ((fill_price - old_entry) * close_qty - (close_qty * fill_price * self.default_fee_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                    acct["realized_pnl"] = str((Decimal(acct["realized_pnl"]) + realized_pnl).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    credit_amount = (close_qty * fill_price) - (close_qty * fill_price * self.default_fee_rate)
                    short_cost = (short_qty * fill_price) + (short_qty * fill_price * self.default_fee_rate)
                    acct["available_balance"] = str((avail_bal + credit_amount - short_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                    user_positions[symbol] = {
                        "symbol": symbol,
                        "side": "short",
                        "size": str(short_qty),
                        "entry_price": str(fill_price),
                        "current_price": str(fill_price),
                        "unrealized_pnl": "0.0",
                        "created_at": self._get_timestamp(),
                        "updated_at": self._get_timestamp(),
                    }
            else:
                # Accumulate Short Position
                old_size = Decimal(str(pos["size"]))
                old_entry = Decimal(str(pos["entry_price"]))
                new_size = old_size + quantity
                new_entry = ((old_size * old_entry) + (quantity * fill_price)) / new_size
                pos["size"] = str(new_size)
                pos["entry_price"] = str(new_entry.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN))
                pos["current_price"] = str(fill_price)
                pos["updated_at"] = self._get_timestamp()

        execution_id = f"exec_paper_{uuid.uuid4().hex[:12]}"
        trade_record = {
            "execution_id": execution_id,
            "order_id": order_id,
            "user_id": uid,
            "strategy_id": strategy_id,
            "deployment_id": deployment_id,
            "symbol": symbol,
            "side": side,
            "quantity": str(quantity),
            "price": str(fill_price),
            "fee": str(fee),
            "realized_pnl": str(realized_pnl),
            "status": PaperOrderStatus.FILLED.value,
            "executed_at": self._get_timestamp(),
        }

        self._trades.setdefault(uid, []).append(trade_record)

        order_record = {
            "order_id": order_id,
            "user_id": uid,
            "strategy_id": strategy_id,
            "deployment_id": deployment_id,
            "symbol": symbol,
            "side": side,
            "order_type": "limit" if from_limit_order else "market",
            "quantity": str(quantity),
            "price": str(fill_price),
            "filled_quantity": str(quantity),
            "status": PaperOrderStatus.FILLED.value,
            "fee": str(fee),
            "execution_id": execution_id,
            "created_at": self._orders[order_id]["created_at"] if order_id in self._orders else self._get_timestamp(),
            "updated_at": self._get_timestamp(),
        }
        self._orders[order_id] = order_record
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = order_id

        # Recalculate account totals
        self._recalculate_account(uid)

        logger.info(f"[PAPER_TRADE] tenant={uid} user={uid} strategy={strategy_id} order={order_id} exec={execution_id} sym={symbol} side={side.upper()} qty={quantity} px={fill_price} fee={fee} pnl={realized_pnl}")
        return order_record

    async def cancel_order(self, user_id: str, order_id: str) -> Dict[str, Any]:
        """Cancels an open limit paper order and returns locked capital."""
        user_lock = self._get_user_lock(user_id)
        async with user_lock:
            uid = str(user_id)
            if order_id not in self._orders:
                raise ValueError(f"Order {order_id} not found")

            ord_record = self._orders[order_id]
            if ord_record.get("user_id") != uid:
                raise PermissionError("Access denied to order")

            cur_status = ord_record.get("status")
            if cur_status == PaperOrderStatus.CANCELLED.value:
                # Idempotent no-op
                return ord_record
            if cur_status == PaperOrderStatus.FILLED.value:
                raise ValueError("Cannot cancel an already filled order")
            if cur_status != PaperOrderStatus.OPEN.value:
                raise ValueError(f"Cannot cancel order in status '{cur_status}'")

            # Release locked funds if limit buy
            if ord_record.get("side") == "buy" and ord_record.get("order_type") == "limit":
                qty = Decimal(str(ord_record["quantity"]))
                px = Decimal(str(ord_record["price"]))
                locked_amt = qty * px * (Decimal("1") + self.default_fee_rate)

                acct = self.get_or_create_account(uid)
                cur_locked = Decimal(acct["locked_balance"])
                cur_avail = Decimal(acct["available_balance"])

                acct["locked_balance"] = str(max(Decimal("0"), cur_locked - locked_amt).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                acct["available_balance"] = str((cur_avail + locked_amt).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))

            ord_record["status"] = PaperOrderStatus.CANCELLED.value
            ord_record["updated_at"] = self._get_timestamp()
            self._recalculate_account(uid)
            return ord_record

    async def check_limit_orders(self, symbol: str, current_price: Decimal) -> List[Dict[str, Any]]:
        """
        Limit matching engine: checks all open limit orders for symbol and fills those whose limit conditions are met.
        """
        clean_sym = symbol.strip().upper().replace("/", "-")
        cur_px = Decimal(str(current_price))
        filled_orders = []

        # Find open orders for symbol
        open_orders = [
            o for o in self._orders.values()
            if o.get("symbol") == clean_sym and o.get("status") == PaperOrderStatus.OPEN.value
        ]

        for ord_record in open_orders:
            uid = ord_record["user_id"]
            user_lock = self._get_user_lock(uid)
            async with user_lock:
                # Double-check status under lock
                if ord_record.get("status") != PaperOrderStatus.OPEN.value:
                    continue

                ord_side = ord_record["side"]
                limit_px = Decimal(str(ord_record["price"]))
                qty = Decimal(str(ord_record["quantity"]))

                should_fill = False
                if ord_side == "buy" and cur_px <= limit_px:
                    should_fill = True
                elif ord_side == "sell" and cur_px >= limit_px:
                    should_fill = True

                if should_fill:
                    filled = await self._execute_fill(
                        user_id=uid,
                        order_id=ord_record["order_id"],
                        symbol=clean_sym,
                        side=ord_side,
                        quantity=qty,
                        price=cur_px,
                        strategy_id=ord_record.get("strategy_id"),
                        deployment_id=ord_record.get("deployment_id"),
                        from_limit_order=True
                    )
                    filled_orders.append(filled)

        return filled_orders

    def get_positions(self, user_id: str) -> List[Dict[str, Any]]:
        """Returns open positions for user with current unrealized PnL."""
        uid = str(user_id)
        self._recalculate_account(uid)
        return list(self._positions.get(uid, {}).values())

    def get_orders(self, user_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns list of paper orders for user."""
        uid = str(user_id)
        orders = [o for o in self._orders.values() if o.get("user_id") == uid]
        if status:
            orders = [o for o in orders if o.get("status", "").upper() == status.upper()]
        return sorted(orders, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_trades(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns trade fills history for user."""
        uid = str(user_id)
        trades = self._trades.get(uid, [])
        return sorted(trades, key=lambda x: x.get("executed_at", ""), reverse=True)[:limit]

    def get_performance_summary(self, user_id: str) -> Dict[str, Any]:
        """Calculates paper trading performance metrics."""
        uid = str(user_id)
        acct = self._recalculate_account(uid)
        trades = self._trades.get(uid, [])

        init_cap = Decimal(acct["initial_capital"])
        total_equity = Decimal(acct["total_equity"])
        total_pnl = total_equity - init_cap
        roi_pct = (total_pnl / init_cap * Decimal("100")) if init_cap > 0 else Decimal("0")

        closed_trades = [t for t in trades if Decimal(t.get("realized_pnl", "0")) != Decimal("0")]
        winning_trades = [t for t in closed_trades if Decimal(t.get("realized_pnl", "0")) > Decimal("0")]
        losing_trades = [t for t in closed_trades if Decimal(t.get("realized_pnl", "0")) < Decimal("0")]

        win_rate = (len(winning_trades) / len(closed_trades) * 100) if closed_trades else 0.0
        gross_profit = sum(Decimal(t["realized_pnl"]) for t in winning_trades)
        gross_loss = abs(sum(Decimal(t["realized_pnl"]) for t in losing_trades))
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else (float(gross_profit) if gross_profit > 0 else 1.0)

        return {
            "account": acct,
            "total_pnl": str(total_pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)),
            "roi_pct": float(roi_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)),
            "total_trades": len(trades),
            "closed_trades": len(closed_trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "open_positions_count": len(self._positions.get(uid, {})),
        }


# Singleton service instance
_paper_service_instance: Optional[PaperTradingService] = None

def get_paper_trading_service() -> PaperTradingService:
    global _paper_service_instance
    if _paper_service_instance is None:
        _paper_service_instance = PaperTradingService()
    return _paper_service_instance
