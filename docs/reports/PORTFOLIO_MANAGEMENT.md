# Portfolio Management System

**Date:** May 1, 2026

---

## Overview

Built a **comprehensive portfolio management system** with:
- ✅ Capital allocation per strategy (static & dynamic)
- ✅ Total exposure tracking (symbol, strategy, portfolio levels)
- ✅ Cross-strategy conflict resolution
- ✅ Real-time PnL aggregation
- ✅ Margin usage tracking
- ✅ Risk engine integration

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     PORTFOLIO MANAGEMENT                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Capital Allocation                                                    │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Total Capital: $100,000                                     │    │
│  │                                                             │    │
│  │ • Strategy A: 40% ($40,000) [Priority: 3]                  │    │
│  │ • Strategy B: 30% ($30,000) [Priority: 2]                  │    │
│  │ • Strategy C: 20% ($20,000) [Priority: 1]                  │    │
│  │ • Reserve: 10% ($10,000)                                    │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Position Tracking                                                   │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Strategy A:                                                 │    │
│  │   • BTCUSDT Long: $15,000 (37.5% of allocation)           │    │
│  │   • ETHUSDT Short: $10,000 (25% of allocation)            │    │
│  │   • Available: $15,000                                      │    │
│  │                                                             │    │
│  │ Strategy B:                                                 │    │
│  │   • ADAUSDT Long: $20,000 (66.7% of allocation)            │    │
│  │   • Available: $10,000                                      │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Conflict Resolution                                                 │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Conflict: Strategy C wants SHORT BTCUSDT                  │    │
│  │          Strategy A already has LONG BTCUSDT              │    │
│  │                                                             │    │
│  │ Resolution: Priority_Wins                                  │    │
│  │          Strategy A (Priority 3) > Strategy C (Priority 1) │    │
│  │ Action: BLOCK Strategy C signal                           │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. StrategyAllocation

```python
@dataclass
class StrategyAllocation:
    strategy_id: str
    allocation_pct: float = 0.1        # 10% of portfolio
    max_position_pct: float = 0.05     # 5% per position
    max_positions: int = 10            # Max concurrent
    
    # Dynamic scaling
    use_dynamic_allocation: bool = False
    performance_based_scaling: bool = True
    
    # Current state
    allocated_capital: float = 0.0
    used_capital: float = 0.0
    available_capital: float = 0.0
    
    # Performance
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    
    @property
    def utilization_pct(self) -> float
    @property
    def win_rate(self) -> float
```

### 2. Position

```python
@dataclass
class Position:
    id: str
    strategy_id: str
    symbol: str
    side: str  # "long" or "short"
    
    entry_price: float
    entry_time: datetime
    quantity: float
    
    # Current state
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    # Exit tracking
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    realized_pnl: Optional[float]
    
    @property
    def is_open(self) -> bool
    @property
    def market_value(self) -> float
    @property
    def cost_basis(self) -> float
    
    def update_price(self, price: float)
    def close(self, price: float, time: datetime)
```

### 3. ConflictDetector

```python
class ConflictDetector:
    def __init__(self, resolution_strategy: ConflictResolution)
    
    def detect_conflicts(
        self,
        new_position: Position,
        existing_positions: List[Position]
    ) -> List[Dict]
    
    def resolve_conflicts(
        self,
        conflicts: List[Dict],
        strategy_priorities: Dict[str, int]
    ) -> Dict[str, Any]  # Resolution plan
```

**Conflict Types:**
- `opposite_side` - Long vs Short on same symbol
- `concentration` - Combined position exceeds limit

**Resolution Strategies:**
- `FIRST_WINS` - Existing position wins
- `LAST_WINS` - New position wins (cancels existing)
- `LARGER_WINS` - Larger position wins
- `PRIORITY_WINS` - Higher priority strategy wins
- `SUM_POSITIONS` - Combine positions
- `BLOCK_ALL` - Block all conflicting orders

### 4. ExposureMetrics

```python
@dataclass
class ExposureMetrics:
    # Symbol-level
    symbol_exposure: Dict[str, float]
    symbol_notional: Dict[str, float]
    
    # Strategy-level
    strategy_exposure: Dict[str, float]
    
    # Portfolio-level
    gross_exposure: float          # Sum of absolute positions
    net_exposure: float            # Net long/short
    long_exposure: float
    short_exposure: float
    
    # Leverage
    gross_leverage: float
    net_leverage: float
    
    # Concentration
    max_symbol_concentration: float
    max_strategy_concentration: float
```

### 5. MarginMetrics

```python
@dataclass
class MarginMetrics:
    initial_margin_required: float
    maintenance_margin_required: float
    margin_used: float
    margin_available: float
    
    margin_utilization_pct: float
    margin_level: float            # Equity / Used Margin
    
    margin_call_threshold: float = 1.25  # 125%
    liquidation_threshold: float = 1.1    # 110%
    
    margin_call_warning: bool
    near_liquidation: bool
```

### 6. PortfolioPnL

```python
@dataclass
class PortfolioPnL:
    # Realized
    daily_realized_pnl: float
    total_realized_pnl: float
    
    # Unrealized
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    # Total
    total_pnl: float
    total_pnl_pct: float
    
    # Breakdown
    strategy_pnl: Dict[str, float]
    symbol_pnl: Dict[str, float]
    
    # Performance
    win_count: int
    loss_count: int
    win_rate: float
    profit_factor: float
```

---

## PortfolioManager

### Main Interface

```python
class PortfolioManager:
    def __init__(
        self,
        total_capital: float,
        risk_engine: Optional[Any] = None,
        conflict_resolution: ConflictResolution = ConflictResolution.PRIORITY_WINS
    )
    
    # Strategy management
    def register_strategy(...) -> StrategyAllocation
    def update_strategy_allocation(...) -> StrategyAllocation
    
    # Position management
    async def open_position(...) -> Tuple[Optional[Position], Dict]
    async def close_position(...) -> Optional[Position]
    
    # Price updates
    def update_prices(self, prices: Dict[str, float])
    
    # Metrics
    def get_exposure_metrics(self) -> ExposureMetrics
    def get_margin_metrics(self) -> MarginMetrics
    def get_pnl_summary(self) -> PortfolioPnL
    def get_portfolio_summary(self) -> Dict[str, Any]
```

### Position Opening Flow

```python
async def open_position(
    self,
    strategy_id: str,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    metadata: Optional[Dict] = None,
) -> Tuple[Optional[Position], Dict[str, Any]]:
    
    # 1. Check strategy exists and has allocation
    if strategy_id not in self.allocations:
        return None, {"error": "strategy_not_found"}
    
    allocation = self.allocations[strategy_id]
    
    # 2. Check strategy limits
    if len(self.strategy_positions[strategy_id]) >= allocation.max_positions:
        return None, {"error": "max_positions_reached"}
    
    # 3. Check capital availability
    required_capital = quantity * entry_price
    if required_capital > allocation.available_capital:
        return None, {"error": "insufficient_capital"}
    
    # 4. Check position size limit
    max_position_value = allocation.allocated_capital * allocation.max_position_pct
    if required_capital > max_position_value:
        return None, {"error": "position_size_exceeded"}
    
    # 5. Create position
    position = Position(...)
    
    # 6. Detect conflicts
    conflicts = self.conflict_detector.detect_conflicts(position, existing_positions)
    
    # 7. Resolve conflicts
    resolution = self.conflict_detector.resolve_conflicts(conflicts, self.strategy_priorities)
    
    # 8. Handle resolution (cancel existing if needed)
    if resolution["action"] == "block":
        return None, {"error": "conflict_blocked", "reason": resolution["reason"]}
    
    for cancel in resolution.get("cancellations", []):
        await self.close_position(cancel["position_id"], entry_price)
    
    # 9. Store position and update allocation
    self.positions[position_id] = position
    self.open_positions[symbol].add(position_id)
    allocation.used_capital += required_capital
    allocation.available_capital -= required_capital
    
    return position, {"resolution": resolution}
```

---

## API Endpoints

### Initialize Portfolio

```http
POST /api/portfolio/initialize?total_capital=100000

Response:
{
  "status": "initialized",
  "total_capital": 100000
}
```

### Register Strategy

```http
POST /api/portfolio/strategies/register
Content-Type: application/json

{
  "strategy_id": "trend_following_v1",
  "allocation_pct": 0.4,
  "priority": 3,
  "max_position_pct": 0.05,
  "max_positions": 10
}

Response:
{
  "strategy_id": "trend_following_v1",
  "allocation_pct": 0.4,
  "allocated_capital": 40000,
  "available_capital": 40000,
  "utilization_pct": 0,
  "max_position_pct": 0.05,
  "max_positions": 10
}
```

### Open Position

```http
POST /api/portfolio/positions/open
Content-Type: application/json

{
  "strategy_id": "trend_following_v1",
  "symbol": "BTCUSDT",
  "side": "long",
  "quantity": 0.5,
  "entry_price": 45000,
  "metadata": {"signal_strength": 0.85}
}

Response:
{
  "position": {
    "id": "trend_following_v1_BTCUSDT_...",
    "strategy_id": "trend_following_v1",
    "symbol": "BTCUSDT",
    "side": "long",
    "entry_price": 45000,
    "quantity": 0.5,
    "market_value": 22500,
    "unrealized_pnl": 0,
    "is_open": true
  },
  "resolution": {
    "action": "allow",
    "conflicts": []
  }
}
```

### Close Position

```http
POST /api/portfolio/positions/{position_id}/close
Content-Type: application/json

{
  "exit_price": 46000,
  "reason": "take_profit"
}

Response:
{
  "position": {
    "id": "...",
    "status": "closed",
    "exit_price": 46000,
    "realized_pnl": 500
  },
  "realized_pnl": 500
}
```

### Update Prices

```http
POST /api/portfolio/prices/update
Content-Type: application/json

{
  "prices": {
    "BTCUSDT": 45500,
    "ETHUSDT": 3200,
    "ADAUSDT": 0.45
  }
}

Response:
{
  "updated_symbols": 3
}
```

### Get Portfolio Summary

```http
GET /api/portfolio/summary

Response:
{
  "total_capital": 100000,
  "strategies": 3,
  "total_positions": 12,
  "open_positions": 8,
  "allocations": {
    "trend_following_v1": {
      "allocated_capital": 40000,
      "used_capital": 25000,
      "available_capital": 15000,
      "utilization_pct": 0.625,
      "total_pnl": 1200,
      "win_rate": 0.65
    }
  },
  "exposure": {
    "gross_exposure": 45000,
    "net_exposure": 15000,
    "long_exposure": 30000,
    "short_exposure": 15000,
    "gross_leverage": 0.45,
    "max_symbol_concentration": 0.25
  },
  "margin": {
    "margin_used": 4500,
    "margin_available": 95500,
    "margin_utilization_pct": 0.045,
    "margin_level": 22.2,
    "margin_call_warning": false
  },
  "pnl": {
    "total_realized_pnl": 2500,
    "unrealized_pnl": 800,
    "total_pnl": 3300,
    "total_pnl_pct": 0.033,
    "win_rate": 0.62
  }
}
```

### Get Exposure Metrics

```http
GET /api/portfolio/exposure

Response:
{
  "gross_exposure": 45000,
  "net_exposure": 15000,
  "long_exposure": 30000,
  "short_exposure": 15000,
  "gross_leverage": 0.45,
  "net_leverage": 0.15,
  "max_symbol_concentration": 0.25,
  "max_strategy_concentration": 0.4,
  "symbol_exposure": {
    "BTCUSDT": 22500,
    "ETHUSDT": 15000,
    "ADAUSDT": 7500
  },
  "strategy_exposure": {
    "trend_following_v1": 25000,
    "mean_reversion_v1": 15000,
    "breakout_v1": 5000
  }
}
```

### Get Margin Metrics

```http
GET /api/portfolio/margin

Response:
{
  "initial_margin_required": 4500,
  "maintenance_margin_required": 2250,
  "margin_used": 4500,
  "margin_available": 95500,
  "margin_utilization_pct": 0.045,
  "margin_level": 22.2,
  "margin_call_threshold": 1.25,
  "liquidation_threshold": 1.1,
  "margin_call_warning": false,
  "near_liquidation": false
}
```

### Get PnL Summary

```http
GET /api/portfolio/pnl

Response:
{
  "daily_realized_pnl": 350,
  "total_realized_pnl": 2500,
  "unrealized_pnl": 800,
  "total_pnl": 3300,
  "total_pnl_pct": 0.033,
  "win_rate": 0.62,
  "profit_factor": 1.8,
  "strategy_pnl": {
    "trend_following_v1": 1200,
    "mean_reversion_v1": 800,
    "breakout_v1": 300
  },
  "symbol_pnl": {
    "BTCUSDT": 1500,
    "ETHUSDT": 800,
    "ADAUSDT": 200
  }
}
```

### List Positions

```http
GET /api/portfolio/positions?strategy_id=trend_following_v1&open_only=true

Response:
{
  "count": 5,
  "positions": [
    {
      "id": "trend_following_v1_BTCUSDT_...",
      "symbol": "BTCUSDT",
      "side": "long",
      "entry_price": 45000,
      "current_price": 45500,
      "quantity": 0.5,
      "unrealized_pnl": 250,
      "unrealized_pnl_pct": 0.011,
      "market_value": 22750,
      "is_open": true
    }
  ]
}
```

---

## Usage Examples

### Example 1: Basic Portfolio Setup

```python
from backend.portfolio_management import PortfolioManager, ConflictResolution

# Initialize portfolio
manager = PortfolioManager(
    total_capital=100000.0,
    conflict_resolution=ConflictResolution.PRIORITY_WINS
)

# Register strategies
manager.register_strategy(
    strategy_id="trend_following",
    allocation_pct=0.4,
    priority=3,
    max_position_pct=0.05,
    max_positions=10
)

manager.register_strategy(
    strategy_id="mean_reversion",
    allocation_pct=0.3,
    priority=2,
    max_position_pct=0.05,
    max_positions=8
)

manager.register_strategy(
    strategy_id="breakout",
    allocation_pct=0.2,
    priority=1,
    max_position_pct=0.03,
    max_positions=5
)

# Check allocations
for strategy_id, allocation in manager.allocations.items():
    print(f"{strategy_id}: ${allocation.allocated_capital:,.2f}")

# Output:
# trend_following: $40,000.00
# mean_reversion: $30,000.00
# breakout: $20,000.00
```

### Example 2: Opening Positions with Conflict Detection

```python
# Open position for trend following
position1, info = await manager.open_position(
    strategy_id="trend_following",
    symbol="BTCUSDT",
    side="long",
    quantity=0.5,
    entry_price=45000,
    metadata={"signal_strength": 0.85}
)

print(f"Opened: {position1.id}")
print(f"Used capital: ${position1.market_value:,.2f}")

# Try to open opposite position (conflict)
position2, info = await manager.open_position(
    strategy_id="breakout",
    symbol="BTCUSDT",
    side="short",  # Opposite side!
    quantity=0.3,
    entry_price=45000,
)

if position2 is None:
    print(f"Blocked: {info['error']}")
    print(f"Reason: {info['reason']}")
    # Output: Blocked: conflict_blocked
    #         Reason: lower_priority_than_trend_following
```

### Example 3: Dynamic Allocation Based on Performance

```python
# Check initial allocation
trend_alloc = manager.allocations["trend_following"]
print(f"Initial: ${trend_alloc.allocated_capital:,.2f}")

# Simulate good performance
for _ in range(10):
    # Open and close winning positions
    pos, _ = await manager.open_position("trend_following", "BTCUSDT", "long", 0.1, 45000)
    await manager.close_position(pos.id, 46000, "profit")

# Performance after wins
print(f"Win rate: {trend_alloc.win_rate:.1%}")
print(f"Total PnL: ${trend_alloc.total_pnl:,.2f}")

# Scale up allocation based on performance
if trend_alloc.win_rate > 0.6 and trend_alloc.total_pnl > 2000:
    manager.update_strategy_allocation("trend_following", 0.5)  # Increase to 50%
    
new_alloc = manager.allocations["trend_following"]
print(f"New allocation: ${new_alloc.allocated_capital:,.2f}")
```

### Example 4: Real-Time Exposure Monitoring

```python
async def monitor_exposure():
    while True:
        # Update prices
        prices = await fetch_market_prices()
        manager.update_prices(prices)
        
        # Get metrics
        exposure = manager.get_exposure_metrics()
        margin = manager.get_margin_metrics()
        pnl = manager.get_pnl_summary()
        
        # Check limits
        if exposure.gross_leverage > 0.8:
            await send_alert(f"High leverage: {exposure.gross_leverage:.1%}")
        
        if margin.margin_call_warning:
            await send_alert("Margin call warning!")
        
        if exposure.max_symbol_concentration > 0.3:
            await send_alert(f"High concentration: {exposure.max_symbol_concentration:.1%}")
        
        # Print dashboard
        print(f"Gross Exposure: ${exposure.gross_exposure:,.2f}")
        print(f"Net Exposure: ${exposure.net_exposure:,.2f}")
        print(f"Leverage: {exposure.gross_leverage:.2f}x")
        print(f"Unrealized PnL: ${pnl.unrealized_pnl:,.2f}")
        
        await asyncio.sleep(5)
```

### Example 5: Integration with DAG Execution

```python
from backend.portfolio_management import PortfolioManager
from backend.execution_engine_production import DAGExecutionAdapter

# Initialize systems
portfolio = PortfolioManager(total_capital=100000)
portfolio.register_strategy("dag_strategy", allocation_pct=0.5)

# Connect to execution adapter
adapter = DAGExecutionAdapter(execution_engine)

# On DAG signal
async def on_dag_signal(signal):
    # Check portfolio capacity
    strategy_alloc = portfolio.allocations["dag_strategy"]
    
    if strategy_alloc.utilization_pct > 0.9:
        logger.warning("Strategy at capacity, skipping signal")
        return
    
    # Calculate position size based on allocation
    max_position_value = strategy_alloc.allocated_capital * strategy_alloc.max_position_pct
    position_size = min(
        signal.suggested_size,
        max_position_value / signal.price
    )
    
    # Open position in portfolio
    position, info = await portfolio.open_position(
        strategy_id="dag_strategy",
        symbol=signal.symbol,
        side=signal.action,
        quantity=position_size,
        entry_price=signal.price,
        metadata={"signal": signal.to_dict()}
    )
    
    if position:
        # Execute through adapter
        result = await adapter.execute_signal(
            signal, signal.symbol, signal.price, position_size
        )
        
        if not result["success"]:
            # Rollback portfolio position
            await portfolio.close_position(position.id, signal.price, "execution_failed")
```

### Example 6: Risk Engine Integration

```python
from backend.portfolio_management import PortfolioManager
from core.risk_engine import RiskEngine

# Initialize with risk engine
risk_engine = RiskEngine(
    initial_capital=100000,
    max_drawdown=0.2,
    daily_loss_limit=0.05
)

portfolio = PortfolioManager(
    total_capital=100000,
    risk_engine=risk_engine
)

# Risk engine can monitor portfolio
async def risk_monitor():
    while True:
        summary = portfolio.get_portfolio_summary()
        
        # Check drawdown
        total_pnl_pct = summary["pnl"]["total_pnl_pct"]
        if total_pnl_pct < -0.15:  # 15% drawdown
            risk_engine.trigger_kill_switch()
            
            # Close all positions
            for pos in list(portfolio.positions.values()):
                if pos.is_open:
                    await portfolio.close_position(
                        pos.id,
                        portfolio.current_prices.get(pos.symbol, pos.current_price),
                        "risk_kill_switch"
                    )
        
        await asyncio.sleep(1)
```

---

## Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `backend/portfolio_management.py` | ~700 | **NEW** - Portfolio management system |
| `main.py` | +3 | Added portfolio router import & registration |

---

## Status: ✅ COMPLETE

Portfolio management system with:
- ✅ Strategy-level capital allocation
- ✅ Multi-level exposure tracking
- ✅ Cross-strategy conflict resolution
- ✅ Real-time PnL aggregation
- ✅ Margin usage monitoring
- ✅ Risk engine integration
