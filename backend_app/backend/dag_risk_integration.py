"""
backend/dag_risk_integration.py — Risk-Integrated DAG Execution Pipeline.

Integrates risk engine into DAG execution flow:
  Signal → Risk Check → Position Sizing → Execution

Features:
  - Strategy-specific risk limits
  - Max drawdown protection
  - Allowed symbols filtering
  - Position sizing based on risk parameters
  - Kill switch integration
  - Real-time risk monitoring

Execution Flow:
  DAG Signal
      ↓
  [Risk Filter] ──▶ Blocked? → Log & Skip
      ↓ Pass
  [Position Sizing] ──▶ Calculate size based on risk %
      ↓
  [Execution] ──▶ Send order to exchange
      ↓
  [Post-Trade Risk] ──▶ Update exposure, drawdown, limits
"""
import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.dag_event_loop import (DAGEventLoop, MarketEvent,
                                                Signal)
from backend_app.core.risk_engine import RiskEngine

logger = logging.getLogger("DAGRiskIntegration")


class RiskDecision(Enum):
    """Risk check decision outcomes."""
    APPROVED = "approved"
    BLOCKED_SYMBOL = "blocked_symbol"
    BLOCKED_POSITION_SIZE = "blocked_position_size"
    BLOCKED_DRAWDOWN = "blocked_drawdown"
    BLOCKED_DAILY_LIMIT = "blocked_daily_limit"
    BLOCKED_KILL_SWITCH = "blocked_kill_switch"
    REDUCED_SIZE = "reduced_size"


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    decision: RiskDecision
    approved: bool
    original_size: float
    adjusted_size: float
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyRiskLimits:
    """Risk limits for a specific strategy."""
    strategy_id: str
    max_position_size: float = 1000.0
    max_daily_trades: int = 100
    allowed_symbols: List[str] = field(default_factory=list)
    max_drawdown_pct: float = 0.1  # 10%
    enabled: bool = True
    
    # Additional constraints
    max_position_pct: float = 0.1  # 10% of portfolio
    risk_per_trade_pct: float = 0.01  # 1% risk per trade
    min_position_size: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict) -> "StrategyRiskLimits":
        return cls(
            strategy_id=data.get("strategy_id", ""),
            max_position_size=data.get("max_position_size", 1000.0),
            max_daily_trades=data.get("max_daily_trades", 100),
            allowed_symbols=data.get("allowed_symbols", []),
            max_drawdown_pct=data.get("max_drawdown_pct", 0.1),
            enabled=data.get("enabled", True),
        )


@dataclass
class PositionSizingParams:
    """Parameters for position sizing."""
    account_equity: float
    risk_per_trade_pct: float = 0.01  # 1%
    stop_loss_pct: float = 0.02  # 2%
    max_position_pct: float = 0.1  # 10%
    leverage: float = 1.0
    
    def calculate_position_size(self, signal_strength: float, entry_price: float) -> float:
        """
        Calculate position size based on risk parameters.
        
        Formula: Risk Amount / (Stop Loss % × Entry Price)
        """
        # Risk amount in currency
        risk_amount = self.account_equity * self.risk_per_trade_pct * signal_strength
        
        # Position size in base currency
        if self.stop_loss_pct > 0:
            position_size = risk_amount / (self.stop_loss_pct * entry_price)
        else:
            position_size = 0
        
        # Apply max position limit
        max_position_value = self.account_equity * self.max_position_pct
        max_position_size = max_position_value / entry_price
        
        # Apply leverage
        max_position_size *= self.leverage
        
        return min(position_size, max_position_size)


class RiskIntegratedExecutionPipeline:
    """
    Risk-integrated execution pipeline for DAG signals.
    
    Manages the full flow from signal generation to order execution
    with comprehensive risk checks at each stage.
    """
    
    def __init__(
        self,
        strategy_id: str,
        risk_limits: Optional[StrategyRiskLimits] = None,
        initial_capital: float = 10000.0,
    ):
        self.strategy_id = strategy_id
        self.risk_limits = risk_limits or StrategyRiskLimits(strategy_id=strategy_id)
        
        # Risk engine for portfolio-level checks
        self.risk_engine = RiskEngine(
            initial_capital=initial_capital,
            risk_per_trade=self.risk_limits.risk_per_trade_pct,
            max_drawdown=self.risk_limits.max_drawdown_pct,
            daily_loss_limit=0.05
        )
        
        # Position sizing
        self.position_sizer = PositionSizingParams(
            account_equity=initial_capital,
            risk_per_trade_pct=self.risk_limits.risk_per_trade_pct,
            max_position_pct=self.risk_limits.max_position_pct,
        )
        
        # Execution tracking
        self.daily_stats = {
            "trades_count": 0,
            "trades_date": datetime.now().date(),
            "total_exposure": 0.0,
            "symbols_traded": set(),
        }
        
        # Kill switch state
        self.kill_switch_active = False
        
        # Callbacks
        self.execution_callbacks: List[Callable[[Dict], None]] = []
        self.blocked_signal_callbacks: List[Callable[[Signal, RiskCheckResult], None]] = []
        
        # Thread safety
        self._lock = threading.Lock()
        
        logger.info(f"Risk pipeline initialized for strategy {strategy_id}")
    
    def set_kill_switch(self, active: bool):
        """Activate or deactivate kill switch."""
        self.kill_switch_active = active
        if active:
            logger.warning(f"Kill switch ACTIVATED for strategy {self.strategy_id}")
        else:
            logger.info(f"Kill switch DEACTIVATED for strategy {self.strategy_id}")
    
    def update_equity(self, equity: float):
        """Update account equity for position sizing."""
        self.position_sizer.account_equity = equity
        self.risk_engine.update_equity(equity)
    
    def check_risk(self, signal: Signal, proposed_size: float) -> RiskCheckResult:
        """
        Comprehensive risk check for a trading signal.
        
        Checks (in order):
        1. Kill switch
        2. Strategy enabled
        3. Allowed symbols
        4. Max drawdown
        5. Daily trade limit
        6. Position size limits
        """
        with self._lock:
            # 1. Kill switch check
            if self.kill_switch_active:
                return RiskCheckResult(
                    decision=RiskDecision.BLOCKED_KILL_SWITCH,
                    approved=False,
                    original_size=proposed_size,
                    adjusted_size=0.0,
                    reason="Kill switch is active"
                )
            
            # 2. Strategy enabled check
            if not self.risk_limits.enabled:
                return RiskCheckResult(
                    decision=RiskDecision.BLOCKED_KILL_SWITCH,
                    approved=False,
                    original_size=proposed_size,
                    adjusted_size=0.0,
                    reason="Strategy is disabled"
                )
            
            # 3. Allowed symbols check
            if self.risk_limits.allowed_symbols:
                if signal.symbol not in self.risk_limits.allowed_symbols:
                    return RiskCheckResult(
                        decision=RiskDecision.BLOCKED_SYMBOL,
                        approved=False,
                        original_size=proposed_size,
                        adjusted_size=0.0,
                        reason=f"Symbol {signal.symbol} not in allowed list"
                    )
            
            # 4. Max drawdown check
            risk_status = self.risk_engine.get_status()
            current_drawdown = risk_status.get("current_drawdown_pct", 0)
            if current_drawdown >= self.risk_limits.max_drawdown_pct:
                return RiskCheckResult(
                    decision=RiskDecision.BLOCKED_DRAWDOWN,
                    approved=False,
                    original_size=proposed_size,
                    adjusted_size=0.0,
                    reason=f"Max drawdown exceeded: {current_drawdown:.2%} >= {self.risk_limits.max_drawdown_pct:.2%}"
                )
            
            # 5. Daily trade limit check
            today = datetime.now().date()
            if self.daily_stats["trades_date"] != today:
                # Reset for new day
                self.daily_stats = {
                    "trades_count": 0,
                    "trades_date": today,
                    "total_exposure": 0.0,
                    "symbols_traded": set(),
                }
            
            if self.daily_stats["trades_count"] >= self.risk_limits.max_daily_trades:
                return RiskCheckResult(
                    decision=RiskDecision.BLOCKED_DAILY_LIMIT,
                    approved=False,
                    original_size=proposed_size,
                    adjusted_size=0.0,
                    reason=f"Daily trade limit reached: {self.daily_stats['trades_count']}/{self.risk_limits.max_daily_trades}"
                )
            
            # 6. Position size check
            if proposed_size > self.risk_limits.max_position_size:
                # Auto-reduce size instead of blocking
                adjusted_size = self.risk_limits.max_position_size
                return RiskCheckResult(
                    decision=RiskDecision.REDUCED_SIZE,
                    approved=True,
                    original_size=proposed_size,
                    adjusted_size=adjusted_size,
                    reason=f"Position size reduced from {proposed_size} to {adjusted_size}",
                    metadata={"size_reduction_reason": "max_position_limit"}
                )
            
            if proposed_size < self.risk_limits.min_position_size:
                return RiskCheckResult(
                    decision=RiskDecision.BLOCKED_POSITION_SIZE,
                    approved=False,
                    original_size=proposed_size,
                    adjusted_size=0.0,
                    reason=f"Position size {proposed_size} below minimum {self.risk_limits.min_position_size}"
                )
            
            # All checks passed
            return RiskCheckResult(
                decision=RiskDecision.APPROVED,
                approved=True,
                original_size=proposed_size,
                adjusted_size=proposed_size,
                reason="All risk checks passed"
            )
    
    def calculate_position_size(self, signal: Signal, entry_price: float) -> float:
        """Calculate position size based on risk parameters."""
        return self.position_sizer.calculate_position_size(
            signal_strength=signal.strength,
            entry_price=entry_price
        )
    
    async def process_signal(
        self,
        signal: Signal,
        entry_price: float,
        executor: Optional[Callable] = None
    ) -> Optional[Dict]:
        """
        Process a trading signal through the risk pipeline.
        
        Full flow:
        1. Calculate position size
        2. Run risk checks
        3. Execute if approved
        4. Update tracking stats
        
        Returns execution result or None if blocked.
        """
        logger.info(f"Processing signal: {signal.action} {signal.symbol} @ {entry_price}")
        
        # 1. Calculate position size
        calculated_size = self.calculate_position_size(signal, entry_price)
        
        if calculated_size <= 0:
            logger.warning(f"Invalid position size calculated: {calculated_size}")
            return None
        
        # 2. Risk check
        risk_result = self.check_risk(signal, calculated_size)
        
        if not risk_result.approved:
            logger.warning(
                f"Signal BLOCKED: {signal.symbol} {signal.action} | "
                f"Reason: {risk_result.reason}"
            )
            
            # Notify blocked callbacks
            for callback in self.blocked_signal_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(signal, risk_result)
                    else:
                        callback(signal, risk_result)
                except Exception as e:
                    logger.error(f"Blocked callback error: {e}")
            
            return None
        
        # Use adjusted size (may be reduced)
        final_size = risk_result.adjusted_size
        
        logger.info(
            f"Signal APPROVED: {signal.symbol} {signal.action} | "
            f"Size: {final_size:.4f} ({risk_result.decision.value})"
        )
        
        # 3. Execute
        execution_result = None
        if executor:
            try:
                execution_result = await executor(
                    symbol=signal.symbol,
                    side=signal.action,
                    size=final_size,
                    price=entry_price,
                    metadata={
                        "signal_strength": signal.strength,
                        "risk_check": risk_result.to_dict() if hasattr(risk_result, 'to_dict') else str(risk_result),
                        "strategy_id": self.strategy_id,
                    }
                )
                
                # 4. Update stats on successful execution
                if execution_result:
                    with self._lock:
                        self.daily_stats["trades_count"] += 1
                        self.daily_stats["symbols_traded"].add(signal.symbol)
                        self.daily_stats["total_exposure"] += final_size * entry_price
                    
                    # Update risk engine
                    pnl = execution_result.get("realized_pnl", 0)
                    self.risk_engine.update_equity(pnl)
                    
                    # Notify callbacks
                    for callback in self.execution_callbacks:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(execution_result)
                            else:
                                callback(execution_result)
                        except Exception as e:
                            logger.error(f"Execution callback error: {e}")
                
            except Exception as e:
                logger.error(f"Execution failed: {e}")
                execution_result = {"error": str(e), "status": "failed"}
        
        return execution_result
    
    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get current risk metrics."""
        risk_status = self.risk_engine.get_status()
        
        with self._lock:
            return {
                "strategy_id": self.strategy_id,
                "kill_switch_active": self.kill_switch_active,
                "strategy_enabled": self.risk_limits.enabled,
                "current_drawdown_pct": risk_status.get("current_drawdown_pct", 0),
                "max_drawdown_limit_pct": self.risk_limits.max_drawdown_pct,
                "daily_trades": self.daily_stats["trades_count"],
                "daily_trades_limit": self.risk_limits.max_daily_trades,
                "total_exposure": self.daily_stats["total_exposure"],
                "symbols_traded_count": len(self.daily_stats["symbols_traded"]),
                "allowed_symbols_count": len(self.risk_limits.allowed_symbols),
                "risk_per_trade_pct": self.risk_limits.risk_per_trade_pct,
                "max_position_size": self.risk_limits.max_position_size,
                "account_equity": self.position_sizer.account_equity,
            }
    
    def add_execution_callback(self, callback: Callable[[Dict], None]):
        """Add callback for successful executions."""
        self.execution_callbacks.append(callback)
    
    def add_blocked_callback(self, callback: Callable[[Signal, RiskCheckResult], None]):
        """Add callback for blocked signals."""
        self.blocked_signal_callbacks.append(callback)


class RiskIntegratedEventLoop(DAGEventLoop):
    """
    Event-driven DAG with integrated risk management.
    
    Extends DAGEventLoop with risk checks and position sizing
    before order execution.
    """
    
    def __init__(
        self,
        dag_nodes: List[Dict],
        dag_edges: List[Dict],
        symbols: List[str],
        timeframe: str = "1m",
        strategy_id: str = "default",
        risk_limits: Optional[StrategyRiskLimits] = None,
        initial_capital: float = 10000.0,
    ):
        super().__init__(dag_nodes, dag_edges, symbols, timeframe)
        
        # Risk pipeline
        self.risk_pipeline = RiskIntegratedExecutionPipeline(
            strategy_id=strategy_id,
            risk_limits=risk_limits,
            initial_capital=initial_capital,
        )
        
        # Symbol -> current price mapping (for position sizing)
        self.last_prices: Dict[str, float] = {}
        
        # Execution stats
        self.signals_blocked = 0
        self.signals_approved = 0
        
        logger.info(
            f"Risk-integrated event loop created for strategy {strategy_id}"
        )
    
    async def _process_event(self, event: MarketEvent):
        """
        Process market event with risk-integrated execution.
        
        Overrides parent to add risk checks before signal emission.
        """
        symbol = event.symbol
        
        # Update last price
        if event.price:
            self.last_prices[symbol] = event.price
        elif event.close:
            self.last_prices[symbol] = event.close
        
        # Get base signal from DAG
        # For risk integration, we intercept the signal and run it through risk pipeline
        window = self.rolling_windows.get(symbol)
        if not window:
            return
        
        # Task 7.10: the window is fed through the market data contract, not from the event
        # directly. `DAGEventLoop._ingest_event` offers the event to that symbol's
        # `ClosedBarGate` (`market_data_contract.ClosedBarIngest`, `drop_late=True`) and
        # appends only a bar the contract admitted, so a forming bar, a duplicate or a late
        # arrival cannot reach the engine below (Requirements 19.2 - 19.4). It returns False
        # for a tick that has not completed an interval: `self.last_prices` above is already
        # updated from that tick, so position sizing still sees the live price while indicator
        # computation waits for the bar to close.
        if not self._ingest_event(event):
            return
        
        if len(window) < 20:
            return
        
        # Execute DAG
        df = window.to_dataframe()
        
        try:
            result = self.execute_dag_for_symbol(symbol, df)
            signals = result.get("signals", [])
            
            if len(signals) > 0:
                latest_signal = signals.iloc[-1]
                
                if latest_signal != 0:  # Non-hold signal
                    # Create Signal object
                    signal = Signal(
                        timestamp=datetime.now(),
                        symbol=symbol,
                        action="buy" if latest_signal > 0 else "sell",
                        strength=abs(latest_signal),
                        metadata={"execution_mode": "risk_integrated"}
                    )
                    
                    # Get current price for position sizing
                    entry_price = self.last_prices.get(symbol, event.close or event.price)
                    
                    if entry_price:
                        # Process through risk pipeline
                        execution_result = await self.risk_pipeline.process_signal(
                            signal=signal,
                            entry_price=entry_price,
                            executor=self._execute_order
                        )
                        
                        if execution_result:
                            self.signals_approved += 1
                            # Emit approved signal
                            await self._emit_signal(signal)
                        else:
                            self.signals_blocked += 1
                    else:
                        logger.warning(f"No price available for {symbol}, skipping risk check")
                        
        except Exception as e:
            logger.error(f"Risk-integrated processing failed for {symbol}: {e}")
    
    def execute_dag_for_symbol(self, symbol: str, df: pd.DataFrame) -> Dict:
        """Execute DAG for a specific symbol."""
        engine = self.dag_engines.get(symbol)
        if not engine:
            engine = DAGEngine()
            self.dag_engines[symbol] = engine
        
        return engine.execute_dag(self.dag_nodes, self.dag_edges, df)
    
    async def _execute_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        metadata: Dict
    ) -> Dict:
        """
        Execute order via canonical ExecutionEngine gateway.
        """
        logger.info(
            f"Executing order via Canonical Gateway: {side.upper()} {size:.4f} {symbol} @ {price}"
        )
        from backend_app.core.execution_engine import ExecutionEngine
        from decimal import Decimal
        from uuid import UUID

        raw_tenant = (metadata or {}).get("tenant_id", "00000000-0000-0000-0000-000000000001")
        tenant_id = UUID(str(raw_tenant)) if isinstance(raw_tenant, (str, UUID)) else raw_tenant
        strategy_id = (metadata or {}).get("strategy_id", "dag_strategy")
        portfolio_state = (metadata or {}).get("portfolio_state", {"total_equity": Decimal("100000.0")})

        engine = getattr(self, "execution_engine", None)
        if not engine:
            engine = ExecutionEngine(portfolio_state=portfolio_state)

        res = await engine.execute_trade(
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            size=Decimal(str(size)),
            price=Decimal(str(price)),
            metadata=metadata or {}
        )

        return {
            "order_id": res.execution_id or f"ord-{datetime.now().timestamp()}",
            "symbol": symbol,
            "side": side,
            "size": size,
            "price": price,
            "status": "filled" if res.success else "failed",
            "filled_size": size if res.success else 0.0,
            "filled_price": price if res.success else 0.0,
            "realized_pnl": 0.0,
            "metadata": metadata or {},
            "executed_at": datetime.now().isoformat(),
            "details": res.details or {}
        }
    
    def get_integrated_stats(self) -> Dict[str, Any]:
        """Get stats including risk metrics."""
        base_stats = self.get_stats()
        risk_metrics = self.risk_pipeline.get_risk_metrics()
        
        return {
            **base_stats,
            "risk": risk_metrics,
            "signals_blocked": self.signals_blocked,
            "signals_approved": self.signals_approved,
            "signal_pass_rate": (
                self.signals_approved / (self.signals_approved + self.signals_blocked)
                if (self.signals_approved + self.signals_blocked) > 0 else 0
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS FOR RISK-INTEGRATED DAG
# ═══════════════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/api/strategies/risk", tags=["risk-integrated-dag"])

# Active risk-integrated loops registry
risk_integrated_loops: Dict[str, RiskIntegratedEventLoop] = {}


class RiskIntegratedStartRequest(BaseModel):
    """Request to start risk-integrated DAG execution."""
    dag_nodes: List[Dict[str, Any]]
    dag_edges: List[Dict[str, Any]]
    symbols: List[str]
    timeframe: str = "1m"
    strategy_id: str = "default"
    
    # Risk parameters
    max_position_size: float = 1000.0
    max_daily_trades: int = 100
    allowed_symbols: List[str] = []
    max_drawdown_pct: float = 0.1
    risk_per_trade_pct: float = 0.01
    initial_capital: float = 10000.0
    
    # Execution mode
    simulation_mode: bool = True


class RiskMetricsResponse(BaseModel):
    """Risk metrics response."""
    strategy_id: str
    kill_switch_active: bool
    current_drawdown_pct: float
    daily_trades: int
    daily_trades_limit: int
    signals_blocked: int
    signals_approved: int
    signal_pass_rate: float


@router.post("/start")
async def start_risk_integrated_loop(request: RiskIntegratedStartRequest):
    """Start a risk-integrated event-driven DAG session."""
    import uuid
    
    session_id = str(uuid.uuid4())
    
    # Create risk limits
    risk_limits = StrategyRiskLimits(
        strategy_id=request.strategy_id,
        max_position_size=request.max_position_size,
        max_daily_trades=request.max_daily_trades,
        allowed_symbols=request.allowed_symbols,
        max_drawdown_pct=request.max_drawdown_pct,
    )
    risk_limits.risk_per_trade_pct = request.risk_per_trade_pct
    
    # Create event loop with risk integration
    loop = RiskIntegratedEventLoop(
        dag_nodes=request.dag_nodes,
        dag_edges=request.dag_edges,
        symbols=request.symbols,
        timeframe=request.timeframe,
        strategy_id=request.strategy_id,
        risk_limits=risk_limits,
        initial_capital=request.initial_capital,
    )
    
    # Create event source
    if request.simulation_mode:
        from backend_app.backend.dag_event_loop import SimulatedEventSource
        source = SimulatedEventSource(
            symbols=request.symbols,
            timeframe=request.timeframe
        )
    else:
        from backend_app.backend.dag_event_loop import WebSocketEventSource
        source = WebSocketEventSource(
            ws_url="wss://stream.exchange.com/ws",
            symbols=request.symbols,
            timeframe=request.timeframe
        )
    
    source.connect(loop)
    
    # Start
    await loop.start()
    await source.start()
    
    # Store
    risk_integrated_loops[session_id] = {
        "loop": loop,
        "source": source,
        "created_at": datetime.now()
    }
    
    return {
        "session_id": session_id,
        "status": "started",
        "strategy_id": request.strategy_id,
        "risk_config": {
            "max_position_size": request.max_position_size,
            "max_daily_trades": request.max_daily_trades,
            "max_drawdown_pct": request.max_drawdown_pct,
            "risk_per_trade_pct": request.risk_per_trade_pct,
        }
    }


@router.get("/metrics/{session_id}", response_model=RiskMetricsResponse)
async def get_risk_metrics(session_id: str):
    """Get risk metrics for a session."""
    if session_id not in risk_integrated_loops:
        raise HTTPException(404, "Session not found")
    
    session = risk_integrated_loops[session_id]
    stats = session["loop"].get_integrated_stats()
    
    return {
        "strategy_id": stats["risk"]["strategy_id"],
        "kill_switch_active": stats["risk"]["kill_switch_active"],
        "current_drawdown_pct": stats["risk"]["current_drawdown_pct"],
        "daily_trades": stats["risk"]["daily_trades"],
        "daily_trades_limit": stats["risk"]["daily_trades_limit"],
        "signals_blocked": stats["signals_blocked"],
        "signals_approved": stats["signals_approved"],
        "signal_pass_rate": stats["signal_pass_rate"],
    }


@router.post("/kill-switch/{session_id}")
async def toggle_kill_switch(session_id: str, active: bool):
    """Activate or deactivate kill switch for a session."""
    if session_id not in risk_integrated_loops:
        raise HTTPException(404, "Session not found")
    
    session = risk_integrated_loops[session_id]
    session["loop"].risk_pipeline.set_kill_switch(active)
    
    return {
        "session_id": session_id,
        "kill_switch": active,
        "status": "activated" if active else "deactivated"
    }


@router.post("/stop/{session_id}")
async def stop_risk_integrated_loop(session_id: str):
    """Stop a risk-integrated session."""
    if session_id not in risk_integrated_loops:
        raise HTTPException(404, "Session not found")
    
    session = risk_integrated_loops[session_id]
    
    await session["source"].stop()
    await session["loop"].stop()
    
    final_stats = session["loop"].get_integrated_stats()
    
    del risk_integrated_loops[session_id]
    
    return {
        "session_id": session_id,
        "status": "stopped",
        "final_stats": final_stats
    }
