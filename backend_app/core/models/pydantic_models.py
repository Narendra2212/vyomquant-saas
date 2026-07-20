"""
core/models.py — All Pydantic request/response models.

FIXES:
  MDL-1: UserStatusRequest.status uses Literal — rejects invalid states
  MDL-2: ExecuteOrderRequest.params documented with allowed keys
"""

from pydantic import BaseModel, Field, EmailStr, validator, ConfigDict
from typing import Optional, List, Any, Dict, Literal
from enum import Enum
from datetime import datetime

# ── AUTH ──────────────────────────────────────────────────────────────────


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class TwoFAVerifyRequest(BaseModel):
    factor_id: str
    code: str = Field(..., min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict | None = None


# ── EXCHANGE VAULT ────────────────────────────────────────────────────────


class ExchangeKeysRequest(BaseModel):
    exchange_id: str
    api_key: str
    secret_key: str
    password: Optional[str] = None


class TestConnectionRequest(BaseModel):
    exchange_id: str


class ExchangeResponse(BaseModel):
    exchange_id: str
    masked_key: str
    permissions: List[str]
    status: str
    volume_usd: Optional[float] = None


# ── ORDER EXECUTION ───────────────────────────────────────────────────────


class OrderSide(str, Enum):
    buy = "buy"
    sell = "sell"

    @classmethod
    def _missing_(cls, value):
        """Handle case-insensitive enum values - accepts BUY/buy, SELL/sell."""
        if not isinstance(value, str):
            return None
        value = value.lower()
        for member in cls:
            if member.value == value:
                return member
        return None


class OrderType(str, Enum):
    market = "market"
    limit = "limit"
    stop = "stop"
    take_profit = "take_profit"

    @classmethod
    def _missing_(cls, value):
        """Handle case-insensitive enum values - accepts MARKET/market, LIMIT/limit."""
        if not isinstance(value, str):
            return None
        value = value.lower()
        for member in cls:
            if member.value == value:
                return member
        return None


class ExecuteOrderRequest(BaseModel):
    symbol: str
    order_type: OrderType
    side: OrderSide
    amount: float = Field(..., gt=0)
    price: Optional[float] = None
    # MDL-2: Document allowed safe CCXT params only
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional CCXT order parameters. "
            "Safe values: postOnly, timeInForce (GTC/IOC/FOK), reduceOnly, clientOrderId."
        ),
    )


class StopLossRequest(BaseModel):
    symbol: str
    side: OrderSide
    amount: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)


class TakeProfitRequest(BaseModel):
    symbol: str
    side: OrderSide
    amount: float = Field(..., gt=0)
    take_profit_price: float = Field(..., gt=0)


class CancelAllRequest(BaseModel):
    symbol: Optional[str] = None


class CancelOrderRequest(BaseModel):
    order_id: str
    symbol: str


# ── STRATEGIES ────────────────────────────────────────────────────────────


class ConditionModel(BaseModel):
    left: str
    op: str
    right: float


class LogicBlock(BaseModel):
    operator: str
    conditions: List[ConditionModel]


class RiskParameters(BaseModel):
    position_size_pct: float = Field(0.05, gt=0, le=1)
    stop_loss_pct: float = Field(0.02, gt=0, le=1)
    take_profit_pct: float = Field(0.06, gt=0)


class StrategyBlueprint(BaseModel):
    name: str
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    buy_logic: LogicBlock
    sell_logic: LogicBlock
    risk: RiskParameters
    ml_model_path: Optional[str] = None
    indicators: List[str] = Field(default_factory=list)


# ── DAG NODE TYPES ────────────────────────────────────────────────────────


class NodeType(str, Enum):
    INDICATOR = "indicator"
    ML = "ml"
    LOGIC = "logic"
    ACTION = "action"
    INPUT = "input"


class LogicOperator(str, Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    GT = "GT"  # Greater than
    LT = "LT"  # Less than
    EQ = "EQ"  # Equal
    GTE = "GTE"  # Greater than or equal
    LTE = "LTE"  # Less than or equal


class DAGNode(BaseModel):
    """A node in the strategy DAG."""
    id: str
    type: NodeType
    label: Optional[str] = None
    
    # For indicator nodes
    indicator: Optional[str] = None  # rsi, macd, sma, etc.
    params: Dict[str, Any] = Field(default_factory=dict)  # period, fast, slow, etc.
    
    # For ML nodes
    model_id: Optional[str] = None
    confidence_threshold: float = Field(0.7, ge=0.0, le=1.0)
    
    # For logic nodes
    operator: Optional[LogicOperator] = None
    
    # For action nodes
    action: Optional[str] = None  # buy, sell, hold
    order_type: Optional[str] = "market"
    amount: Optional[float] = None
    
    # For input nodes
    symbol: Optional[str] = None
    timeframe: Optional[str] = None


class DAGEdge(BaseModel):
    """An edge connecting two nodes in the DAG."""
    id: str
    source: str  # Source node id
    target: str  # Target node id
    label: Optional[str] = None  # Condition label (e.g., "true", "false")
    condition: Optional[str] = None  # Value condition for this edge


class DAGConfig(BaseModel):
    """DAG configuration model."""
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    
    nodes: List[Dict[str, Any]] = Field(..., description="DAG nodes")
    edges: List[Dict[str, Any]] = Field(..., description="DAG edges")
    symbols: List[str] = Field(default_factory=list, description="Trading symbols")
    timeframe: Optional[str] = Field(None, description="Data timeframe")


class BacktestRequest(BaseModel):
    """
    Backtest request schema supporting full DAG execution.
    
    DAG-based execution is now the primary mode.
    Simple strategy list is maintained for backward compatibility.
    """
    # DAG configuration (PRIMARY)
    dag: Optional[DAGConfig] = None
    
    # Legacy strategy configuration (backward compatibility)
    strategies: Optional[List[str]] = None  # Strategy names from registry
    strategy_id: Optional[str] = None  # Legacy single strategy ID
    
    # Trading parameters
    symbols: List[str] = Field(default_factory=lambda: ["BTCUSDT"])
    timeframe: str = "1h"
    
    # Backtest parameters (decimals, not percentages)
    initial_capital: float = Field(10000.0, gt=0)
    trade_size_pct: float = Field(0.1, gt=0, le=1.0)  # 0.1 = 10%
    stop_loss_pct: float = Field(0.02, ge=0, le=1.0)  # 0.02 = 2%
    take_profit_pct: float = Field(0.04, ge=0, le=1.0)  # 0.04 = 4%
    ml_threshold: float = Field(0.0, ge=0.0, le=1.0)  # ML confidence threshold
    
    # Extended parameters
    params: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('trade_size_pct', 'stop_loss_pct', 'take_profit_pct')
    def validate_percentages(cls, v):
        """Ensure percentages are in decimal format (0.1 not 10)"""
        if v > 1.0:
            raise ValueError(f"Percentage should be decimal (e.g., 0.1 for 10%), got {v}")
        return v


class TrainModelRequest(BaseModel):
    strategy_name: str
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    indicators: List[str] = Field(..., min_length=5)


class BacktestResponse(BaseModel):
    stats: Dict[str, Any]
    equity_curve: List[Dict]


class OrderExecutionResponse(BaseModel):
    """
    Standardized order execution response.
    
    🔴 STEP 4: Response contract alignment - order_id aliases to execution_id
    for frontend/backend compatibility.
    """
    status: str
    execution_id: str
    order_id: str = Field(alias="execution_id")
    message: str
    result: Optional[Dict[str, Any]] = None


class DeployRequest(BaseModel):
    exchange_id: str


# ── RISK MANAGER ──────────────────────────────────────────────────────────


class KillSwitchConfig(BaseModel):
    label: str
    enabled: bool


class RiskSettingsRequest(BaseModel):
    max_daily_loss: float = Field(500.0, gt=0)
    max_positions: int = Field(10, gt=0, le=50)
    max_leverage: int = Field(3, gt=0, le=20)
    kill_switches: List[KillSwitchConfig] = Field(default_factory=list)


class KillSwitchRequest(BaseModel):
    scope: str = "user"
    confirm_code: str


class GlobalKillRequest(BaseModel):
    confirm_code: str


# ── STRATEGY LIMITS ───────────────────────────────────────────────────────


class StrategyLimit(BaseModel):
    strategy_id: str
    max_position_size: float = Field(1000.0, gt=0)
    max_daily_trades: int = Field(100, gt=0)
    allowed_symbols: List[str] = Field(default_factory=list)
    max_drawdown_pct: float = Field(0.1, ge=0, le=1)
    enabled: bool = True


class StrategyLimitsRequest(BaseModel):
    limits: List[StrategyLimit]


# ── PORTFOLIO ─────────────────────────────────────────────────────────────


class CloseAllPositionsRequest(BaseModel):
    symbol: Optional[str] = None  # If None, close all positions
    exchange_id: str


# ── BILLING ───────────────────────────────────────────────────────────────


class PaymentMethod(BaseModel):
    id: str
    type: str  # 'card', 'bank_transfer', 'crypto'
    last4: Optional[str] = None
    brand: Optional[str] = None  # visa, mastercard, etc.
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None
    is_default: bool = False


class AddPaymentMethodRequest(BaseModel):
    payment_method_id: str  # Stripe payment method ID (e.g., pm_xxx)
    set_as_default: bool = False
    # Real card metadata — must be sourced from Stripe PaymentMethod response,
    # NOT hardcoded. Required to prevent fake/test card data persisting in prod.
    brand: str = Field(..., description="Card network brand from Stripe (e.g., 'visa', 'mastercard')")
    last4: str = Field(..., min_length=4, max_length=4, description="Last 4 digits of card from Stripe")
    expiry_month: int = Field(..., ge=1, le=12, description="Expiry month (1-12) from Stripe")
    expiry_year: int = Field(..., ge=2024, description="Expiry year (4-digit) from Stripe")


# ── SUPPORT ───────────────────────────────────────────────────────────────


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    category: str = Field(..., pattern="^(general|technical|billing|security|feature)$")
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketComment(BaseModel):
    id: str
    ticket_id: str
    user_id: str
    message: str
    is_staff: bool = False
    created_at: datetime


class Ticket(BaseModel):
    id: str
    user_id: str
    subject: str
    description: str
    category: str
    priority: TicketPriority
    status: TicketStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class AddCommentRequest(BaseModel):
    ticket_id: str
    message: str = Field(..., min_length=1, max_length=2000)


# ── USER / NOTIFICATIONS ──────────────────────────────────────────────────


class NotificationChannels(BaseModel):
    email: bool = True
    telegram: bool = True
    mobile: bool = False


class NotificationEvents(BaseModel):
    trade_executed: bool = True
    stop_loss_triggered: bool = True
    daily_pnl_summary: bool = True
    bot_state_change: bool = False
    kill_switch_activated: bool = True
    new_login_detected: bool = True
    api_key_expiring: bool = True
    backtest_complete: bool = False


class NotificationSettingsRequest(BaseModel):
    channels: NotificationChannels
    events: NotificationEvents


# ── ADMIN ─────────────────────────────────────────────────────────────────


class UserStatusRequest(BaseModel):
    # MDL-1: Literal prevents accepting invalid states like 'deleted' or 'banned'
    status: Literal["active", "frozen"]
