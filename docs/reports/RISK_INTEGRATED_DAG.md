# Risk-Integrated DAG Execution Pipeline

**Date:** May 1, 2026

---

## Overview

Integrated risk engine into DAG execution with comprehensive risk checks:

```
DAG Signal
    ↓
[Risk Check] ──▶ Blocked? → Log & Skip
    ↓ Pass
[Position Sizing] ──▶ Calculate size based on risk %
    ↓
[Execution] ──▶ Send order to exchange
    ↓
[Post-Trade] ──▶ Update exposure, drawdown, limits
```

**Risk Checks Applied:**
- ✅ Kill switch (emergency stop)
- ✅ Strategy enabled/disabled
- ✅ Allowed symbols filtering
- ✅ Max drawdown protection
- ✅ Daily trade limits
- ✅ Position size limits
- ✅ Position sizing based on risk %

---

## Architecture

### Execution Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                  RISK-INTEGRATED EXECUTION                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. DAG GENERATION                                               │
│     └─▶ Signal: BUY BTCUSDT @ strength=0.85                      │
│                                                                  │
│  2. RISK FILTER                                                  │
│     ├─▶ Kill switch? ──▶ NO                                     │
│     ├─▶ Strategy enabled? ──▶ YES                               │
│     ├─▶ Symbol allowed? ──▶ YES (BTCUSDT in list)             │
│     ├─▶ Drawdown OK? ──▶ YES (5% < 10% limit)                  │
│     ├─▶ Daily limit? ──▶ YES (45/100 trades)                   │
│     └─▶ Position size? ──▶ REDUCED (1500 → 1000 max)            │
│                                                                  │
│  3. POSITION SIZING                                              │
│     └─▶ Size = (Equity × Risk% × Strength) / (SL% × Price)     │
│         = ($10k × 1% × 0.85) / (2% × $45k)                      │
│         = 0.094 BTC (~$4,230)                                   │
│                                                                  │
│  4. EXECUTION                                                    │
│     └─▶ Order: BUY 0.094 BTCUSDT @ market                      │
│                                                                  │
│  5. POST-TRADE                                                   │
│     ├─▶ Update exposure: +$4,230                                 │
│     ├─▶ Increment trade count: 46                                 │
│     └─▶ Update drawdown if loss                                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. RiskCheckResult

```python
@dataclass
class RiskCheckResult:
    decision: RiskDecision      # APPROVED, BLOCKED_*, REDUCED_SIZE
    approved: bool
    original_size: float
    adjusted_size: float
    reason: str
    metadata: Dict
```

**Decision Types:**
- `APPROVED` - All checks passed
- `BLOCKED_SYMBOL` - Symbol not in allowed list
- `BLOCKED_POSITION_SIZE` - Position size exceeds limits
- `BLOCKED_DRAWDOWN` - Max drawdown exceeded
- `BLOCKED_DAILY_LIMIT` - Daily trade limit reached
- `BLOCKED_KILL_SWITCH` - Kill switch active
- `REDUCED_SIZE` - Position reduced to fit limits

### 2. StrategyRiskLimits

```python
@dataclass
class StrategyRiskLimits:
    strategy_id: str
    max_position_size: float = 1000.0
    max_daily_trades: int = 100
    allowed_symbols: List[str] = []
    max_drawdown_pct: float = 0.1  # 10%
    enabled: bool = True
    
    # Additional
    max_position_pct: float = 0.1  # 10% of portfolio
    risk_per_trade_pct: float = 0.01  # 1%
    min_position_size: float = 0.0
```

### 3. PositionSizingParams

```python
@dataclass
class PositionSizingParams:
    account_equity: float
    risk_per_trade_pct: float = 0.01
    stop_loss_pct: float = 0.02
    max_position_pct: float = 0.1
    leverage: float = 1.0
    
    def calculate_position_size(
        self, 
        signal_strength: float, 
        entry_price: float
    ) -> float
```

**Formula:**
```
Position Size = (Equity × Risk% × Signal Strength) / (Stop Loss% × Entry Price)
```

### 4. RiskIntegratedExecutionPipeline

```python
class RiskIntegratedExecutionPipeline:
    def __init__(
        self,
        strategy_id: str,
        risk_limits: StrategyRiskLimits,
        initial_capital: float
    )
    
    # Core methods
    def check_risk(signal, proposed_size) -> RiskCheckResult
    def calculate_position_size(signal, entry_price) -> float
    async def process_signal(signal, entry_price, executor) -> Optional[Dict]
    
    # Risk controls
    def set_kill_switch(active: bool)
    def update_equity(equity: float)
    def get_risk_metrics() -> Dict
```

### 5. RiskIntegratedEventLoop

```python
class RiskIntegratedEventLoop(DAGEventLoop):
    """Event-driven DAG with integrated risk management."""
    
    def __init__(
        self,
        dag_nodes: List[Dict],
        dag_edges: List[Dict],
        symbols: List[str],
        timeframe: str,
        strategy_id: str,
        risk_limits: StrategyRiskLimits,
        initial_capital: float
    )
    
    async def _process_event(event: MarketEvent)
    def get_integrated_stats() -> Dict
```

---

## Risk Check Flow

```python
def check_risk(self, signal: Signal, proposed_size: float) -> RiskCheckResult:
    # 1. Kill switch check (highest priority)
    if self.kill_switch_active:
        return BLOCKED_KILL_SWITCH
    
    # 2. Strategy enabled check
    if not self.risk_limits.enabled:
        return BLOCKED_KILL_SWITCH
    
    # 3. Allowed symbols check
    if signal.symbol not in self.risk_limits.allowed_symbols:
        return BLOCKED_SYMBOL
    
    # 4. Max drawdown check
    current_drawdown = self.risk_engine.get_status()["current_drawdown_pct"]
    if current_drawdown >= self.risk_limits.max_drawdown_pct:
        return BLOCKED_DRAWDOWN
    
    # 5. Daily trade limit check
    if self.daily_stats["trades_count"] >= self.risk_limits.max_daily_trades:
        return BLOCKED_DAILY_LIMIT
    
    # 6. Position size check
    if proposed_size > self.risk_limits.max_position_size:
        # Reduce size instead of blocking
        return REDUCED_SIZE(adjusted_size=max_position_size)
    
    # All checks passed
    return APPROVED
```

---

## API Endpoints

### Start Risk-Integrated Session

```http
POST /api/strategies/risk/start
Content-Type: application/json

{
  "dag_nodes": [...],
  "dag_edges": [...],
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "timeframe": "1m",
  "strategy_id": "rsi_strategy_v1",
  
  // Risk parameters
  "max_position_size": 1000.0,
  "max_daily_trades": 100,
  "allowed_symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
  "max_drawdown_pct": 0.1,
  "risk_per_trade_pct": 0.01,
  "initial_capital": 10000.0,
  
  // Mode
  "simulation_mode": true
}

Response:
{
  "session_id": "uuid",
  "status": "started",
  "strategy_id": "rsi_strategy_v1",
  "risk_config": {
    "max_position_size": 1000.0,
    "max_daily_trades": 100,
    "max_drawdown_pct": 0.1,
    "risk_per_trade_pct": 0.01
  }
}
```

### Get Risk Metrics

```http
GET /api/strategies/risk/metrics/{session_id}

Response:
{
  "strategy_id": "rsi_strategy_v1",
  "kill_switch_active": false,
  "current_drawdown_pct": 0.052,
  "daily_trades": 46,
  "daily_trades_limit": 100,
  "signals_blocked": 12,
  "signals_approved": 89,
  "signal_pass_rate": 0.881
}
```

### Toggle Kill Switch

```http
POST /api/strategies/risk/kill-switch/{session_id}?active=true

Response:
{
  "session_id": "uuid",
  "kill_switch": true,
  "status": "activated"
}
```

### Stop Session

```http
POST /api/strategies/risk/stop/{session_id}

Response:
{
  "session_id": "uuid",
  "status": "stopped",
  "final_stats": {
    "events_processed": 1523,
    "signals_emitted": 101,
    "signals_blocked": 12,
    "signals_approved": 89,
    "risk": {
      "current_drawdown_pct": 0.052,
      "daily_trades": 46,
      "total_exposure": 4230.50
    }
  }
}
```

---

## Usage Examples

### Example 1: Basic Risk-Integrated Trading

```python
from backend.dag_risk_integration import (
    RiskIntegratedEventLoop,
    StrategyRiskLimits
)

# Define risk limits
risk_limits = StrategyRiskLimits(
    strategy_id="trend_following_v1",
    max_position_size=5000.0,      # $5k max per trade
    max_daily_trades=50,           # 50 trades/day max
    allowed_symbols=["BTCUSDT", "ETHUSDT"],  # Only these
    max_drawdown_pct=0.15,         # Stop at 15% DD
    risk_per_trade_pct=0.02,       # 2% risk per trade
    enabled=True
)

# Create event loop with risk integration
loop = RiskIntegratedEventLoop(
    dag_nodes=dag_nodes,
    dag_edges=dag_edges,
    symbols=["BTCUSDT", "ETHUSDT"],
    timeframe="5m",
    strategy_id="trend_following_v1",
    risk_limits=risk_limits,
    initial_capital=50000.0
)

# Add custom executor (connect to exchange)
async def execute_on_binance(symbol, side, size, price, metadata):
    order = await binance_client.create_order(
        symbol=symbol,
        side=side.upper(),
        type="MARKET",
        quantity=size
    )
    return order

loop.risk_pipeline.add_execution_callback(
    lambda result: print(f"Order executed: {result}")
)

# Start
await loop.start()
```

### Example 2: Kill Switch Emergency Stop

```python
# In risk monitoring task
async def monitor_risk(loop: RiskIntegratedEventLoop):
    while loop.running:
        metrics = loop.risk_pipeline.get_risk_metrics()
        
        # Check critical conditions
        if metrics["current_drawdown_pct"] > 0.20:  # 20% DD
            logger.critical("Critical drawdown! Activating kill switch...")
            loop.risk_pipeline.set_kill_switch(True)
            
            # Send alert
            await send_alert(
                f"Kill switch activated for {loop.strategy_id}. "
                f"Drawdown: {metrics['current_drawdown_pct']:.2%}"
            )
            
            # Close all positions
            await close_all_positions(loop.symbols)
        
        await asyncio.sleep(1)

# Start monitoring
task = asyncio.create_task(monitor_risk(loop))
```

### Example 3: Handling Blocked Signals

```python
# Track why signals are blocked
blocked_reasons = defaultdict(int)

async def on_blocked(signal, risk_result):
    reason = risk_result.decision.value
    blocked_reasons[reason] += 1
    
    print(
        f"Signal blocked: {signal.symbol} {signal.action}\n"
        f"Reason: {risk_result.reason}\n"
        f"Total blocked ({reason}): {blocked_reasons[reason]}"
    )
    
    # Alert if too many blocked
    total_blocked = sum(blocked_reasons.values())
    if total_blocked > 50:
        await send_alert(
            f"High signal blockage rate: {total_blocked} signals blocked"
        )

loop.risk_pipeline.add_blocked_callback(on_blocked)
```

### Example 4: Real-Time Risk Dashboard

```python
async def risk_dashboard_updater(loop: RiskIntegratedEventLoop):
    while True:
        stats = loop.get_integrated_stats()
        
        dashboard_data = {
            "strategy": stats["risk"]["strategy_id"],
            "status": "🟢 LIVE" if not stats["risk"]["kill_switch_active"] else "🔴 KILLED",
            "drawdown": f"{stats['risk']['current_drawdown_pct']:.2%}",
            "trades_today": f"{stats['risk']['daily_trades']}/{stats['risk']['daily_trades_limit']}",
            "signals": {
                "approved": stats["signals_approved"],
                "blocked": stats["signals_blocked"],
                "pass_rate": f"{stats['signal_pass_rate']:.1%}"
            },
            "exposure": f"${stats['risk']['total_exposure']:,.2f}"
        }
        
        await broadcast_to_websocket(dashboard_data)
        await asyncio.sleep(1)
```

---

## Integration Points

### With Strategy Limits (from risk.py)

```python
# Fetch limits from database
limits_data = await api.risk.getStrategyLimits()

risk_limits = StrategyRiskLimits(
    strategy_id=limits_data.strategy_id,
    max_position_size=limits_data.max_position_size,
    max_daily_trades=limits_data.max_daily_trades,
    allowed_symbols=limits_data.allowed_symbols,
    max_drawdown_pct=limits_data.max_drawdown_pct,
)

# Use in pipeline
loop = RiskIntegratedEventLoop(
    ...,
    risk_limits=risk_limits
)
```

### With Portfolio for Real Equity

```python
# Update equity from portfolio
portfolio = await api.portfolio.getSummary()
current_equity = portfolio.total_value

loop.risk_pipeline.update_equity(current_equity)
```

### With Kill Switch from Risk Router

```python
# Risk router kill switch triggers DAG kill switch
@router.post("/risk/kill-switch")
async def activate_kill_switch():
    # Activate for all running strategies
    for session in risk_integrated_loops.values():
        session["loop"].risk_pipeline.set_kill_switch(True)
```

---

## Risk Metrics Tracked

| Metric | Description | Threshold |
|--------|-------------|-----------|
| Kill Switch | Emergency stop | Manual trigger |
| Strategy Enabled | On/off switch | Configurable |
| Symbol Filter | Allowed symbols | Per-strategy list |
| Max Drawdown | Peak-to-trough loss | Default 10% |
| Daily Trades | Trades per day | Default 100 |
| Position Size | Max per trade | Default $1000 |
| Risk Per Trade | % of equity risked | Default 1% |
| Position % | % of portfolio | Default 10% |

---

## Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `backend/dag_risk_integration.py` | ~650 | **NEW** - Risk-integrated pipeline |
| `main.py` | +3 | Added risk router import & registration |

---

## Status: ✅ COMPLETE

Risk-integrated DAG execution with:
- ✅ 6-tier risk check system
- ✅ Kill switch emergency stop
- ✅ Strategy limits integration
- ✅ Position sizing based on risk %
- ✅ Signal approval/block tracking
- ✅ Real-time risk metrics
- ✅ API endpoints for control
