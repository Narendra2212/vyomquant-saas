"""
core/tenant.py — Multi-Tenant Context System.

Core infrastructure for multi-tenant isolation across all platform layers.
Provides tenant context extraction, resource quotas, and security enforcement.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime
import hashlib
import hmac


class TenantPlan(Enum):
    """Subscription plans with different quotas."""
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass
class TenantQuota:
    """
    Resource quotas per tenant.
    
    These limits are enforced at all layers to ensure fair resource
    usage and system stability.
    """
    # DAG Limits
    max_dag_sessions: int = 3
    max_dag_nodes: int = 20
    max_symbols_per_dag: int = 5
    max_concurrent_backtests: int = 1
    
    # Execution Limits
    max_orders_per_minute: int = 30
    max_positions: int = 10
    max_daily_trades: int = 50
    max_active_orders: int = 20
    
    # Portfolio Limits
    max_capital: float = 10000.0
    max_leverage: float = 1.0
    max_symbol_concentration: float = 0.5  # 50%
    
    # Storage Limits
    max_redis_memory_mb: int = 50
    max_db_storage_gb: float = 0.5
    data_retention_days: int = 30
    
    # Compute Limits
    max_backtest_parallel: int = 1
    max_websocket_connections: int = 2
    max_indicators_per_dag: int = 10
    
    @classmethod
    def for_plan(cls, plan: TenantPlan) -> "TenantQuota":
        """Get quota configuration for subscription plan."""
        quotas = {
            TenantPlan.FREE: cls(
                max_dag_sessions=1,
                max_dag_nodes=10,
                max_symbols_per_dag=3,
                max_positions=5,
                max_capital=5000.0,
                data_retention_days=7,
            ),
            TenantPlan.BASIC: cls(
                max_dag_sessions=3,
                max_dag_nodes=30,
                max_symbols_per_dag=8,
                max_positions=20,
                max_capital=50000.0,
                data_retention_days=30,
            ),
            TenantPlan.PROFESSIONAL: cls(
                max_dag_sessions=10,
                max_dag_nodes=100,
                max_symbols_per_dag=20,
                max_positions=100,
                max_capital=250000.0,
                max_leverage=3.0,
                data_retention_days=90,
            ),
            TenantPlan.ENTERPRISE: cls(
                max_dag_sessions=50,
                max_dag_nodes=500,
                max_symbols_per_dag=100,
                max_positions=500,
                max_capital=1000000.0,
                max_leverage=10.0,
                data_retention_days=365,
            ),
        }
        return quotas.get(plan, cls())


@dataclass
class TenantContext:
    """
    Context object for all tenant-scoped operations.
    
    This is the primary mechanism for ensuring tenant isolation
    across all services. Extracted from JWT and passed through
    all service layers.
    """
    user_id: str
    tenant_id: str
    email: str
    plan: TenantPlan
    permissions: List[str] = field(default_factory=list)
    quota: TenantQuota = field(default_factory=TenantQuota)
    
    # Session metadata
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def can(self, permission: str) -> bool:
        """Check if tenant has specific permission."""
        return permission in self.permissions or "admin:*" in self.permissions
    
    def owns_resource(self, resource_owner_id: str) -> bool:
        """Check if tenant owns a resource."""
        return self.user_id == resource_owner_id or self.tenant_id == resource_owner_id
    
    def is_admin(self) -> bool:
        """Check if tenant has admin privileges."""
        return "admin:*" in self.permissions or "tenant:admin" in self.permissions
    
    def get_resource_prefix(self, resource_type: str) -> str:
        """Get Redis/database key prefix for this tenant."""
        return f"user:{self.user_id}:{resource_type}"
    
    def get_db_filter(self) -> Dict[str, str]:
        """Get database filter for tenant-scoped queries."""
        return {"tenant_id": self.tenant_id}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "plan": self.plan.value,
            "permissions": self.permissions,
            "quota": {
                "max_dag_sessions": self.quota.max_dag_sessions,
                "max_positions": self.quota.max_positions,
                "max_capital": self.quota.max_capital,
            },
        }


class TenantKeyBuilder:
    """
    Builder for tenant-scoped Redis/database keys.
    
    Ensures consistent namespacing across all services.
    """
    
    @staticmethod
    def dag_session(user_id: str, session_id: str) -> str:
        return f"user:{user_id}:dag:session:{session_id}"
    
    @staticmethod
    def dag_sessions_set(user_id: str) -> str:
        return f"user:{user_id}:dag:sessions"
    
    @staticmethod
    def dag_state(user_id: str, symbol: str) -> str:
        return f"user:{user_id}:dag:state:{symbol}"
    
    @staticmethod
    def state_snapshot(user_id: str, session_id: str, symbol: str) -> str:
        return f"user:{user_id}:state:{session_id}:{symbol}:snapshot"
    
    @staticmethod
    def state_meta(user_id: str, session_id: str, symbol: str) -> str:
        return f"user:{user_id}:state:{session_id}:{symbol}:meta"
    
    @staticmethod
    def event_stream(user_id: str, session_id: str, symbol: str) -> str:
        return f"user:{user_id}:events:{session_id}:{symbol}"
    
    @staticmethod
    def position(user_id: str, position_id: str) -> str:
        return f"user:{user_id}:position:{position_id}"
    
    @staticmethod
    def positions_set(user_id: str) -> str:
        return f"user:{user_id}:positions"
    
    @staticmethod
    def portfolio_allocation(user_id: str, strategy_id: str) -> str:
        return f"user:{user_id}:portfolio:allocation:{strategy_id}"
    
    @staticmethod
    def execution_order(user_id: str, order_id: str) -> str:
        return f"user:{user_id}:execution:order:{order_id}"
    
    @staticmethod
    def execution_orders_set(user_id: str) -> str:
        return f"user:{user_id}:execution:orders"
    
    @staticmethod
    def validation_report(user_id: str, symbol: str, timeframe: str) -> str:
        return f"user:{user_id}:validation:{symbol}:{timeframe}"
    
    @staticmethod
    def risk_limits(user_id: str, strategy_id: str) -> str:
        return f"user:{user_id}:risk:limits:{strategy_id}"
    
    @staticmethod
    def kill_switch(user_id: str) -> str:
        return f"user:{user_id}:risk:kill_switch"
    
    @staticmethod
    def quota_usage(user_id: str, resource_type: str) -> str:
        return f"user:{user_id}:quota:{resource_type}"
    
    @staticmethod
    def rate_limit(user_id: str, action: str, window: str = "1m") -> str:
        return f"rate_limit:user:{user_id}:{action}:{window}"
    
    @staticmethod
    def websocket_connection(user_id: str, connection_id: str) -> str:
        return f"user:{user_id}:ws:{connection_id}"
    
    @staticmethod
    def websocket_connections_set(user_id: str) -> str:
        return f"user:{user_id}:ws:connections"


class QuotaExceededError(Exception):
    """Raised when tenant exceeds resource quota."""
    
    def __init__(self, resource: str, limit: int, current: int, user_id: str):
        self.resource = resource
        self.limit = limit
        self.current = current
        self.user_id = user_id
        super().__init__(
            f"Quota exceeded for {resource}: {current}/{limit} "
            f"(tenant: {user_id})"
        )


class RateLimitExceededError(Exception):
    """Raised when tenant exceeds rate limit."""
    
    def __init__(self, action: str, limit: int, window: str, user_id: str):
        self.action = action
        self.limit = limit
        self.window = window
        self.user_id = user_id
        super().__init__(
            f"Rate limit exceeded for {action}: {limit}/{window} "
            f"(tenant: {user_id})"
        )


class CrossTenantAccessError(Exception):
    """Raised when tenant attempts to access another tenant's resources."""
    
    def __init__(self, accessor: str, owner: str, resource_id: str):
        self.accessor = accessor
        self.owner = owner
        self.resource_id = resource_id
        super().__init__(
            f"Cross-tenant access denied: {accessor} tried to access "
            f"{owner}'s resource {resource_id}"
        )
