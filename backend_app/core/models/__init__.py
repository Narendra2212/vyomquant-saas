"""
core/models/ — Pydantic models package

All Pydantic request/response models for the application.
"""

from .pydantic_models import (
    # Base types
    BaseModel,
    Field,
    EmailStr,
    validator,
    Optional,
    List,
    Any,
    Dict,
    Literal,
    Enum,
    datetime,
    # Auth
    SignInRequest,
    MagicLinkRequest,
    TwoFAVerifyRequest,
    TokenResponse,
    # Exchange
    ExchangeKeysRequest,
    TestConnectionRequest,
    ExchangeResponse,
    # Orders
    OrderType,
    OrderSide,
    ExecuteOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
    CancelAllRequest,
    CancelOrderRequest,
    # Strategies
    ConditionModel,
    LogicBlock,
    RiskParameters,
    StrategyBlueprint,
    NodeType,
    LogicOperator,
    DAGNode,
    DAGEdge,
    DAGConfig,
    BacktestRequest,
    TrainModelRequest,
    BacktestResponse,
    OrderExecutionResponse,
    DeployRequest,
    # Risk / Admin
    KillSwitchConfig,
    RiskSettingsRequest,
    KillSwitchRequest,
    GlobalKillRequest,
    StrategyLimit,
    StrategyLimitsRequest,
    # Portfolio
    CloseAllPositionsRequest,
    # Billing
    PaymentMethod,
    AddPaymentMethodRequest,
    # Support
    TicketPriority,
    TicketStatus,
    CreateTicketRequest,
    TicketComment,
    Ticket,
    AddCommentRequest,
    # Notifications
    NotificationChannels,
    NotificationEvents,
    NotificationSettingsRequest,
    # Admin
    UserStatusRequest,
)

from .billing import (
    SubscriptionModel,
    InvoiceModel,
    PaymentMethodModel,
)

# Re-export execution_record models for backward compatibility
from .execution_record import (
    ExecutionRecordRepository,
    ExecutionStatus,
    ExecutionSide,
    ExecutionRecordModel,
)

# Re-export reconciliation mismatch model
from .reconciliation import (
    ReconciliationMismatchModel,
    ReconciliationMismatchCreate,
    ReconciliationMismatchUpdate,
    ReconciliationMismatchRepository,
    MismatchSeverity,
    MismatchStatus,
    generate_mismatch_id,
    get_reconciliation_repository,
)

__all__ = [
    # Auth
    "SignInRequest",
    "MagicLinkRequest",
    "TwoFAVerifyRequest",
    "TokenResponse",
    # Exchange
    "ExchangeKeysRequest",
    "TestConnectionRequest",
    "ExchangeResponse",
    # Orders
    "OrderType",
    "OrderSide",
    "ExecuteOrderRequest",
    "StopLossRequest",
    "TakeProfitRequest",
    "CancelAllRequest",
    "CancelOrderRequest",
    # Strategies
    "ConditionModel",
    "LogicBlock",
    "RiskParameters",
    "StrategyBlueprint",
    "NodeType",
    "LogicOperator",
    "DAGNode",
    "DAGEdge",
    "DAGConfig",
    "BacktestRequest",
    "TrainModelRequest",
    "BacktestResponse",
    "OrderExecutionResponse",
    "DeployRequest",
    # Risk / Admin
    "KillSwitchConfig",
    "RiskSettingsRequest",
    "KillSwitchRequest",
    "GlobalKillRequest",
    "StrategyLimit",
    "StrategyLimitsRequest",
    "UserStatusRequest",
    # Portfolio
    "CloseAllPositionsRequest",
    # Billing
    "PaymentMethod",
    "AddPaymentMethodRequest",
    "SubscriptionModel",
    "InvoiceModel",
    "PaymentMethodModel",
    # Support
    "TicketPriority",
    "TicketStatus",
    "CreateTicketRequest",
    "TicketComment",
    "Ticket",
    "AddCommentRequest",
    # Notifications
    "NotificationChannels",
    "NotificationEvents",
    "NotificationSettingsRequest",
    # Execution (SQLAlchemy models)
    "ExecutionRecordRepository",
    "ExecutionStatus",
    "ExecutionSide",
    "ExecutionRecordModel",
    # Reconciliation (SQLAlchemy models)
    "ReconciliationMismatchModel",
    "ReconciliationMismatchCreate",
    "ReconciliationMismatchUpdate",
    "ReconciliationMismatchRepository",
    "MismatchSeverity",
    "MismatchStatus",
    "generate_mismatch_id",
    "get_reconciliation_repository",
]
