"""
core/safety_config.py — SYSTEM FREEZE PROTOCOL

This module implements emergency safety controls to prevent
accidental trade execution during system fixes.

⚠️ WARNING: DO NOT MODIFY WITHOUT CTO APPROVAL
"""

import os
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger("SafetyConfig")


class SystemMode(Enum):
    """System operating modes."""
    SAFE_MODE = "safe_mode"          # No execution, read-only
    PAPER_TRADING = "paper_trading"  # Simulated execution only
    LIVE_TRADING = "live_trading"    # Real execution (DANGER)


class ExecutionFlags:
    """
    Global execution control flags.
    
    These flags provide emergency stops for different execution paths.
    All flags default to FALSE (blocked) for safety.
    """
    
    # Master kill switch - overrides all other settings
    LIVE_TRADING_ENABLED: bool = False
    PAPER_TRADING_ENABLED: bool = False
    
    # Individual execution pathways
    MANUAL_ORDER_EXECUTION: bool = False
    STRATEGY_SIGNAL_EXECUTION: bool = False
    BACKTEST_CAN_SUBMIT_ORDERS: bool = False
    
    # ML System safety
    ALLOW_ML_INFERENCE: bool = False
    FALLBACK_TO_MOCK_PREDICTIONS: bool = False  # NEVER enable this in production
    
    # Validation requirements
    REQUIRE_SIGNAL_VALIDATION: bool = True
    REQUIRE_IDEMPOTENCY_KEY: bool = True
    
    # ---------------------------------------------------------
    PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"

    
    @classmethod
    def freeze_all(cls) -> None:
        """Emergency freeze - block all execution."""
        cls.LIVE_TRADING_ENABLED = False
        cls.PAPER_TRADING_ENABLED = False
        cls.MANUAL_ORDER_EXECUTION = False
        cls.STRATEGY_SIGNAL_EXECUTION = False
        cls.BACKTEST_CAN_SUBMIT_ORDERS = False
        cls.ALLOW_ML_INFERENCE = False
        cls.FALLBACK_TO_MOCK_PREDICTIONS = False
        logger.critical("🚨 SYSTEM FREEZE ACTIVATED - All execution blocked")
    
    @classmethod
    def enable_paper_trading(cls) -> None:
        """Enable paper trading mode only."""
        env_mode = os.getenv("AERORA_MODE", "safe").lower()
        if env_mode != "paper":
            raise RuntimeError(
                "Paper trading requires AERORA_MODE=paper environment variable"
            )
        cls.LIVE_TRADING_ENABLED = False
        cls.PAPER_TRADING_ENABLED = True
        cls.MANUAL_ORDER_EXECUTION = True
        cls.STRATEGY_SIGNAL_EXECUTION = True
        cls.BACKTEST_CAN_SUBMIT_ORDERS = False
        cls.ALLOW_ML_INFERENCE = True  # Allow with mock fallback disabled
        cls.FALLBACK_TO_MOCK_PREDICTIONS = False
        logger.info("📋 Paper trading mode enabled")
    
    @classmethod
    def enable_live_trading(cls) -> None:
        """
        Enable live trading - DANGER.
        Only call after ALL safety checks pass.
        """
        # Require explicit environment confirmation
        env_mode = os.getenv("AERORA_MODE", "safe").lower()
        if env_mode != "live":
            raise RuntimeError(
                "Live trading requires AERORA_MODE=live environment variable"
            )
        env_confirm = os.getenv("AERORA_ENABLE_LIVE_TRADING", "false").lower()
        if env_confirm != "true":
            raise RuntimeError(
                "Live trading requires AERORA_ENABLE_LIVE_TRADING=true environment variable"
            )
        
        cls.PAPER_TRADING_ENABLED = False
        cls.LIVE_TRADING_ENABLED = True
        cls.MANUAL_ORDER_EXECUTION = True
        cls.STRATEGY_SIGNAL_EXECUTION = True
        cls.ALLOW_ML_INFERENCE = True
        logger.critical("💰 LIVE TRADING ENABLED - Financial risk active")


class SafetyMonitor:
    """
    Runtime safety monitoring.
    
    Checks that can be performed before any execution.
    """
    
    @staticmethod
    def check_execution_allowed(operation: str) -> Optional[str]:
        """
        Check if execution is allowed.
        
        Returns:
            None if allowed, error message if blocked
        """
        # Environment check
        env_mode = os.getenv("AERORA_MODE", "safe").lower()
        if env_mode == "safe":
            return f"🚫 EXECUTION BLOCKED: AERORA_MODE=safe (operation: {operation})"
        
        if env_mode not in ["paper", "live"]:
            return f"🚫 EXECUTION BLOCKED: Invalid AERORA_MODE={env_mode} (operation: {operation})"

        if env_mode == "paper":
            if ExecutionFlags.LIVE_TRADING_ENABLED:
                return f"🚫 EXECUTION BLOCKED: Live flag cannot be enabled in paper mode (operation: {operation})"
            if not ExecutionFlags.PAPER_TRADING_ENABLED:
                return f"🚫 EXECUTION BLOCKED: Paper trading flag disabled (operation: {operation})"
            if operation == "live_order":
                return f"🚫 EXECUTION BLOCKED: Live execution is not allowed in paper mode (operation: {operation})"

        if env_mode == "live":
            if os.getenv("AERORA_ENABLE_LIVE_TRADING", "false").lower() != "true":
                return f"🚫 EXECUTION BLOCKED: AERORA_ENABLE_LIVE_TRADING is not true (operation: {operation})"
            if not ExecutionFlags.LIVE_TRADING_ENABLED:
                return f"🚫 EXECUTION BLOCKED: Live trading flag disabled (operation: {operation})"
        
        # Operation-specific checks
        if operation == "manual_order" and not ExecutionFlags.MANUAL_ORDER_EXECUTION:
            return "🚫 EXECUTION BLOCKED: Manual orders disabled"
        
        if operation == "strategy_signal" and not ExecutionFlags.STRATEGY_SIGNAL_EXECUTION:
            return "🚫 EXECUTION BLOCKED: Strategy signals disabled"
        
        if operation == "ml_inference" and not ExecutionFlags.ALLOW_ML_INFERENCE:
            return "🚫 EXECUTION BLOCKED: ML inference disabled"
        
        return None  # Execution allowed
    
    @staticmethod
    def assert_safe_mode() -> None:
        """Assert system is in safe mode - no execution possible."""
        if ExecutionFlags.LIVE_TRADING_ENABLED:
            raise RuntimeError(
                "System is NOT in safe mode - LIVE_TRADING_ENABLED is True"
            )
        if ExecutionFlags.PAPER_TRADING_ENABLED:
            raise RuntimeError(
                "System is NOT in safe mode - PAPER_TRADING_ENABLED is True"
            )
        
        env_mode = os.getenv("AERORA_MODE", "safe")
        if env_mode != "safe":
            raise RuntimeError(
                f"System is NOT in safe mode - AERORA_MODE={env_mode}"
            )


# Initialize on import - FREEZE BY DEFAULT
ExecutionFlags.freeze_all()

logger.critical("=" * 60)
logger.critical("🔒 AERORA SYSTEM FREEZE PROTOCOL ACTIVATED")
logger.critical("=" * 60)
logger.critical("All order execution is BLOCKED until safety fixes complete")
logger.critical("To check status: ExecutionFlags.LIVE_TRADING_ENABLED = False")
logger.critical("To enable: Set AERORA_MODE=paper or AERORA_MODE=live")
logger.critical("=" * 60)
