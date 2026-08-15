# -*- coding: utf-8 -*-
"""
core/models/ - Pydantic models package

All Pydantic request/response models for the application.
"""

from .billing import PaymentMethodModel
# Re-export execution_record models for backward compatibility
from .execution_record import (ExecutionRecordModel, ExecutionRecordRepository,
                               ExecutionSide, ExecutionStatus)
from .pydantic_models import (  # Base types; Portfolio; Strategies; Exchange; Risk / Admin; Notifications; Orders; Billing; Auth; Support; Admin
    AddCommentRequest, AddPaymentMethodRequest, BacktestRequest,
    BacktestResponse, CancelAllRequest, CancelOrderRequest,
    CloseAllPositionsRequest, ConditionModel, CreateTicketRequest, DAGConfig,
    DAGEdge, DAGNode, DeployRequest, ExchangeKeysRequest,
    ExchangeResponse, ExecuteOrderRequest, GlobalKillRequest,
    KillSwitchConfig, KillSwitchRequest, LogicBlock,
    LogicOperator, MagicLinkRequest, NodeType, NotificationChannels,
    NotificationEvents, NotificationSettingsRequest, OrderExecutionResponse, OrderSide, OrderType, PaymentMethod,
    RiskParameters, RiskSettingsRequest, SignInRequest, StopLossRequest,
    StrategyBlueprint, StrategyLimit, StrategyLimitsRequest, TakeProfitRequest,
    TestConnectionRequest, Ticket, TicketComment, TicketPriority, TicketStatus,
    TokenResponse, TrainModelRequest, TwoFAVerifyRequest, UserStatusRequest)
# Re-export reconciliation mismatch model
from .reconciliation import (MismatchSeverity, MismatchStatus,
                             ReconciliationMismatchCreate,
                             ReconciliationMismatchModel,
                             ReconciliationMismatchRepository,
                             ReconciliationMismatchUpdate,
                             generate_mismatch_id,
                             get_reconciliation_repository)

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
