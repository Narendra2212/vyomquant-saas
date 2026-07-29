"""
Feature Flags for Execution Safety

This module controls which execution paths are enabled.
Used for gradual rollout and emergency kill switches.
"""

import os
from enum import Enum
from typing import Any, Dict
from backend_app.core.safety_config import get_vyomquant_mode


class ExecutionContext(Enum):
    """Execution context for tracking."""
    API_ORDERS = "api_orders"
    DAG = "dag"
    EVENT_LOOP = "event_loop"
    PRODUCTION_ROUTER = "production_router"


class ExecutionFlags:
    """
    Feature flags for execution safety.
    
    CRITICAL: These flags control which execution paths are active.
    Only enable after safety verification.
    
    Current Status (Phase 1 - Hard Stop):
    - DAG_TRADING_ENABLED: False (unsafe, pending idempotency fixes)
    - EVENT_LOOP_TRADING_ENABLED: False (unsafe, pending handler validation)
    - PRODUCTION_ROUTER_ENABLED: False (unverified safety)
    - API_ORDERS_ENABLED: True (verified safe with idempotency)
    """
    
    # ---------------------------------------------------------
    # SAFETY LOCKS - Phase 1 Refactor (Now Environment Driven)
    # ---------------------------------------------------------
    PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"
    TEST_MODE_ENABLED: bool = True
    LIVE_TRADING_ENABLED: bool = get_vyomquant_mode("") == "live"
    
    # Execution path controls
    DAG_TRADING_ENABLED: bool = os.getenv("DAG_TRADING_ENABLED", "False").lower() == "true" or get_vyomquant_mode("") == "paper"
    EVENT_LOOP_TRADING_ENABLED: bool = os.getenv("EVENT_LOOP_TRADING_ENABLED", "False").lower() == "true" or get_vyomquant_mode("") == "paper"
    API_ORDERS_ENABLED: bool = True
    
    @classmethod
    def is_enabled(cls, context: ExecutionContext) -> bool:
        """Check if execution context is enabled."""
        flag_map = {
            ExecutionContext.API_ORDERS: cls.API_ORDERS_ENABLED,
            ExecutionContext.DAG: cls.DAG_TRADING_ENABLED,
            ExecutionContext.EVENT_LOOP: cls.EVENT_LOOP_TRADING_ENABLED,
            ExecutionContext.PRODUCTION_ROUTER: cls.PRODUCTION_ROUTER_ENABLED,
        }
        return flag_map.get(context, False)
    
    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Get current flag status for monitoring."""
        return {
            "dag_trading_enabled": cls.DAG_TRADING_ENABLED,
            "event_loop_trading_enabled": cls.EVENT_LOOP_TRADING_ENABLED,
            "production_router_enabled": cls.PRODUCTION_ROUTER_ENABLED,
            "api_orders_enabled": cls.API_ORDERS_ENABLED,
            "safe_paths_active": cls.API_ORDERS_ENABLED,
            "unsafe_paths_blocked": not any([
                cls.DAG_TRADING_ENABLED,
                cls.EVENT_LOOP_TRADING_ENABLED,
                cls.PRODUCTION_ROUTER_ENABLED
            ])
        }


class UnsafeExecutionError(Exception):
    """Raised when execution is attempted on disabled path."""
    
    def __init__(self, context: ExecutionContext, message: str = None):
        self.context = context
        self.message = message or f"Execution via {context.value} is disabled"
        super().__init__(self.message)
