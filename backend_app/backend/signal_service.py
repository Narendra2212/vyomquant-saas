"""
backend/signal_service.py — Signal Trace Service

Professional execution audit system.
Complete signal lifecycle tracking from strategy decision to final execution.
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

import inspect
from backend_app.core.dependencies import create_request_supabase_async

logger = logging.getLogger("SignalService")


class SignalStatus:
    """Signal status enumeration."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SignalDecision:
    """Signal decision enumeration."""
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class SignalService:
    """
    Central service for signal trace operations.
    
    Tracks complete signal lifecycle:
    - Signal generation
    - Risk evaluation
    - Order generation
    - Exchange validation
    - Exchange response
    - Execution
    - PnL recording
    """
    
    def __init__(self):
        pass
    
    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res
    
    async def create_signal(
        self,
        user: dict,
        strategy_id: str,
        strategy_version: str,
        deployment_id: str,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        worker_id: str,
        decision: str,
        indicators: Dict,
        market_info: Dict,
        ml_info: Optional[Dict] = None
    ) -> Dict:
        """
        Create a new signal record.
        
        Args:
            user: User dict with id and access_token
            strategy_id: Strategy ID
            strategy_version: Strategy version
            deployment_id: Deployment ID
            exchange_id: Exchange ID
            symbol: Trading pair
            timeframe: Timeframe
            worker_id: Worker ID
            decision: Signal decision (BUY, SELL, EXIT, CLOSE, HOLD)
            indicators: Indicator values
            market_info: Market information
            ml_info: ML/DL information (if applicable)
            
        Returns:
            Signal record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        signal_id = str(uuid4())
        
        signal_data = {
            "id": signal_id,
            "user_id": user["id"],
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "deployment_id": deployment_id,
            "exchange_id": exchange_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "worker_id": worker_id,
            "decision": decision,
            "status": SignalStatus.PENDING,
            "indicators": indicators,
            "market_info": market_info,
            "ml_info": ml_info,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
        
        query_res = sb.table("signals").insert(signal_data).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        logger.info(f"Created signal {signal_id} for strategy {strategy_id}")
        
        return result.data[0] if result.data else signal_data
    
    async def update_risk_decision(
        self,
        user: dict,
        signal_id: str,
        risk_passed: bool,
        risk_reason: str,
        position_size: Optional[float] = None,
        capital: Optional[float] = None,
        exposure: Optional[float] = None,
        expected_loss: Optional[float] = None,
        expected_reward: Optional[float] = None,
        drawdown_check: Optional[bool] = None
    ) -> Dict:
        """
        Update signal with risk decision.
        
        Args:
            user: User dict with id and access_token
            signal_id: Signal ID
            risk_passed: Whether risk check passed
            risk_reason: Reason for risk decision
            position_size: Calculated position size
            capital: Available capital
            exposure: Current exposure
            expected_loss: Expected loss
            expected_reward: Expected reward
            drawdown_check: Drawdown check result
            
        Returns:
            Updated signal record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        update_data = {
            "risk_passed": risk_passed,
            "risk_reason": risk_reason,
            "position_size": position_size,
            "capital": capital,
            "exposure": exposure,
            "expected_loss": expected_loss,
            "expected_reward": expected_reward,
            "drawdown_check": drawdown_check,
            "risk_evaluated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update status based on risk decision
        if not risk_passed:
            update_data["status"] = SignalStatus.REJECTED
        else:
            update_data["status"] = SignalStatus.ACCEPTED
        
        query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        logger.info(f"Updated risk decision for signal {signal_id}: {risk_passed}")
        
        return result.data[0] if result.data else {}
    
    async def update_order(
        self,
        user: dict,
        signal_id: str,
        order_id: str,
        exchange_order_id: Optional[str],
        order_status: str,
        quantity: float,
        filled: float,
        remaining: float,
        average_price: float,
        fees: float,
        slippage: float,
        latency_ms: float
    ) -> Dict:
        """
        Update signal with order information.
        
        Args:
            user: User dict with id and access_token
            signal_id: Signal ID
            order_id: Order ID
            exchange_order_id: Exchange order ID
            order_status: Order status
            quantity: Order quantity
            filled: Filled quantity
            remaining: Remaining quantity
            average_price: Average fill price
            fees: Trading fees
            slippage: Slippage
            latency_ms: Order latency
            
        Returns:
            Updated signal record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        update_data = {
            "order_id": order_id,
            "exchange_order_id": exchange_order_id,
            "order_status": order_status,
            "quantity": quantity,
            "filled": filled,
            "remaining": remaining,
            "average_price": average_price,
            "fees": fees,
            "slippage": slippage,
            "latency_ms": latency_ms,
            "order_updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update status based on order status
        if order_status in ("FILLED", "PARTIALLY_FILLED"):
            update_data["status"] = SignalStatus.EXECUTED
        elif order_status == "CANCELLED":
            update_data["status"] = SignalStatus.CANCELLED
        elif order_status == "FAILED":
            update_data["status"] = SignalStatus.FAILED
        
        query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        logger.info(f"Updated order for signal {signal_id}: {order_status}")
        
        return result.data[0] if result.data else {}
    
    async def update_execution(
        self,
        user: dict,
        signal_id: str,
        trade_id: str,
        pnl: float,
        realized_pnl: Optional[float] = None
    ) -> Dict:
        """
        Update signal with execution and PnL information.
        
        Args:
            user: User dict with id and access_token
            signal_id: Signal ID
            trade_id: Trade ID
            pnl: PnL
            realized_pnl: Realized PnL (if position closed)
            
        Returns:
            Updated signal record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        update_data = {
            "trade_id": trade_id,
            "pnl": pnl,
            "realized_pnl": realized_pnl,
            "executed_at": datetime.now(timezone.utc).isoformat()
        }
        
        query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        logger.info(f"Updated execution for signal {signal_id}: pnl={pnl}")
        
        return result.data[0] if result.data else {}
    
    async def get_signal(
        self,
        user: dict,
        signal_id: str
    ) -> Optional[Dict]:
        """
        Get complete signal data.
        
        Args:
            user: User dict with id and access_token
            signal_id: Signal ID
            
        Returns:
            Signal record with all details
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        query_res = sb.table("signals").select("*").eq("id", signal_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        if not result.data:
            return None
        
        return result.data[0]
    
    async def list_signals(
        self,
        user: dict,
        strategy_id: Optional[str] = None,
        exchange_id: Optional[str] = None,
        symbol: Optional[str] = None,
        worker_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        decision: Optional[str] = None,
        status: Optional[str] = None,
        ml_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """
        List signals with filters.
        
        Args:
            user: User dict with id and access_token
            strategy_id: Filter by strategy
            exchange_id: Filter by exchange
            symbol: Filter by symbol
            worker_id: Filter by worker
            deployment_id: Filter by deployment
            decision: Filter by decision
            status: Filter by status
            ml_type: Filter by ML type (ml, rule_based)
            date_from: Start date
            date_to: End date
            search: Search in signal_id
            limit: Max results
            offset: Pagination offset
            
        Returns:
            List of signal records
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        query = sb.table("signals").select("*").eq("user_id", user["id"])
        
        if strategy_id:
            query = query.eq("strategy_id", strategy_id)
        if exchange_id:
            query = query.eq("exchange_id", exchange_id)
        if symbol:
            query = query.eq("symbol", symbol)
        if worker_id:
            query = query.eq("worker_id", worker_id)
        if deployment_id:
            query = query.eq("deployment_id", deployment_id)
        if decision:
            query = query.eq("decision", decision)
        if status:
            query = query.eq("status", status)
        if ml_type:
            if ml_type == "ml":
                query = query.not_.is_("ml_info", None)
            elif ml_type == "rule_based":
                query = query.is_("ml_info", None)
        if date_from:
            query = query.gte("generated_at", date_from)
        if date_to:
            query = query.lte("generated_at", date_to)
        if search:
            query = query.ilike("id", f"%{search}%")
        
        query_res = query.order("generated_at", desc=True).range(offset, offset + limit - 1).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        return result.data or []
    
    async def get_signal_timeline(
        self,
        user: dict,
        signal_id: str
    ) -> List[Dict]:
        """
        Get complete signal timeline.
        
        Returns all events in chronological order.
        """
        signal = await self.get_signal(user, signal_id)
        if not signal:
            return []
        
        timeline = []
        
        # Signal generated
        timeline.append({
            "event": "SIGNAL_GENERATED",
            "timestamp": signal.get("generated_at"),
            "data": {
                "decision": signal.get("decision"),
                "indicators": signal.get("indicators"),
                "market_info": signal.get("market_info")
            }
        })
        
        # Risk evaluated
        if signal.get("risk_evaluated_at"):
            timeline.append({
                "event": "RISK_EVALUATED",
                "timestamp": signal.get("risk_evaluated_at"),
                "data": {
                    "risk_passed": signal.get("risk_passed"),
                    "risk_reason": signal.get("risk_reason"),
                    "position_size": signal.get("position_size")
                }
            })
        
        # Order created
        if signal.get("order_id"):
            timeline.append({
                "event": "ORDER_CREATED",
                "timestamp": signal.get("order_updated_at") or signal.get("risk_evaluated_at"),
                "data": {
                    "order_id": signal.get("order_id"),
                    "quantity": signal.get("quantity")
                }
            })
        
        # Exchange response
        if signal.get("exchange_order_id"):
            timeline.append({
                "event": "EXCHANGE_RESPONSE",
                "timestamp": signal.get("order_updated_at"),
                "data": {
                    "exchange_order_id": signal.get("exchange_order_id"),
                    "order_status": signal.get("order_status")
                }
            })
        
        # Executed
        if signal.get("executed_at"):
            timeline.append({
                "event": "EXECUTED",
                "timestamp": signal.get("executed_at"),
                "data": {
                    "trade_id": signal.get("trade_id"),
                    "pnl": signal.get("pnl")
                }
            })
        
        # Sort by timestamp
        timeline.sort(key=lambda x: x["timestamp"] or "")
        
        return timeline
    
    async def export_signals(
        self,
        user: dict,
        filters: Dict,
        format: str = "json"
    ) -> str:
        """
        Export signals with filters.
        
        Args:
            user: User dict with id and access_token
            filters: Filter criteria
            format: Export format (json, csv)
            
        Returns:
            Exported data
        """
        signals = await self.list_signals(user, limit=1000, **filters)
        
        if format == "csv":
            import csv
            from io import StringIO
            
            output = StringIO()
            if signals:
                fieldnames = list(signals[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for signal in signals:
                    writer.writerow(signal)
            
            return output.getvalue()
        
        import json
        return json.dumps(signals, default=str, indent=2)


# Singleton instance
_signal_service = None

async def get_signal_service() -> SignalService:
    """Get singleton SignalService instance."""
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service