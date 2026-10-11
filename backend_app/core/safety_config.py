"""
core/safety_config.py - SYSTEM FREEZE PROTOCOL

This module implements emergency safety controls to prevent
accidental trade execution during system fixes.

WARNING: DO NOT MODIFY WITHOUT CTO APPROVAL
"""

import logging
import os
from enum import Enum
from typing import Optional

logger = logging.getLogger("SafetyConfig")


# ══════════════════════════════════════════════════════════════════════════════════════════
#  PRODUCTION_ROUTER_ENABLED — ONE DEFINITION, FAIL CLOSED  (ENV-04)
# ══════════════════════════════════════════════════════════════════════════════════════════

#: The only spelling that mounts the production execution router. Compared
#: case-insensitively after stripping.
#:
#: DELIBERATELY NARROW. ``1`` / ``yes`` / ``on`` are NOT accepted, because widening the
#: enabling vocabulary is a permissiveness increase on a switch whose entire purpose is
#: explicit enablement. They are not silently ignored either: an unrecognised value is
#: refused AND logged by name, so ``PRODUCTION_ROUTER_ENABLED=1`` leaves the router off and
#: says so, rather than leaving an operator to believe they enabled something.
_ROUTER_ENABLE_VALUE = "true"

#: Spellings that mean "off" and need no warning, because they are unambiguous.
_ROUTER_DISABLE_VALUES = frozenset({"false", "0", "no", "off", ""})


def production_router_enabled() -> bool:
    """Whether ``execution_router`` may be mounted at ``/api/execution``.

    THE SINGLE AUTHORITATIVE DEFINITION (ENV-04). This value used to be computed by two
    identical lines in two modules::

        # core/safety_config.py:131  AND  core/feature_flags.py:39
        PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"

    and five other places in this repository stated a different answer:

        core/feature_flags.py docstring ......... "PRODUCTION_ROUTER_ENABLED: False
                                                   (unverified safety)"
        the code on the next line ............... default "True"
        ecs-task-definition-full.json ........... "False"
        new_task_def.json ....................... "False"
        td15.json ............................... "False"
        infra/rollback/taskdef-...-145-....json . "False"
        .env.example ............................ False
        deployment_go_no_go.md .................. True   (corrected with this change)

    THE DEFAULT IS NOW OFF, which is the direction this switch has to fail. A flag whose
    ABSENCE mounts an execution surface is a flag that mounts it on every deployment that
    forgot to set it, and "forgot" is the normal case for a new environment, a rollback to
    an older task definition, or a local process. Explicit enablement is required: unset
    means off, in every environment.

    ENVIRONMENT DOES NOT ENTER INTO IT, on purpose. Making the default depend on ``ENV``
    would give the flag three behaviours to reason about instead of one, and it is exactly
    the shape that produced RUNTIME-01 (a ``DEV_MODE`` read that quietly overrode
    ``ENV=production``). The flag is the only switch, and it is read the same way
    everywhere.

    WHAT IS ON THE OTHER SIDE OF IT, STATED ACCURATELY. ``execution_router`` exposes one
    endpoint, ``POST /api/execution/signal``, and that endpoint constructs
    ``ExecutionEngine()`` with no ``portfolio_state`` on the line BEFORE its ``try``, so it
    raises ``ValueError`` and returns 500 on every request without reaching any venue
    (measured in ``tests/test_execution_paths_that_claim_an_exchange.py``). So this finding
    is a configuration-coherence and fail-open-DIRECTION defect, not a live-order exposure,
    and it is not being reported as one. The direction still matters: the endpoint is an
    order-submission surface by construction and one repair to its constructor is all that
    separates it from reaching ``execute_trade``.

    Regression: tests/test_env04_production_router_requires_explicit_enablement.py
    """
    raw = os.getenv("PRODUCTION_ROUTER_ENABLED")
    if raw is None:
        return False

    normalised = raw.strip().lower()
    if normalised == _ROUTER_ENABLE_VALUE:
        return True
    if normalised not in _ROUTER_DISABLE_VALUES:
        logger.warning(
            "PRODUCTION_ROUTER_ENABLED is set to %r, which is not a recognised value. "
            "The production execution router is DISABLED. The only value that enables it "
            "is 'true' (case-insensitive).",
            raw,
        )
    return False


def validate_safety_config_integrity() -> None:
    """
    Validate safety configuration integrity.

    THIS FUNCTION HAS NO CALL SITES (TRADE-06). Nothing in the application, the entrypoints
    or CI invokes it, and it has never run. It is kept as a record rather than deleted, and
    it must not be wired up as-is: its live-trading branch compares ``VYOMQUANT_MODE`` /
    ``AERORA_MODE`` against ``"live_trading"``, which is the ``SystemMode`` ENUM value and
    not a value either variable ever holds — the trading modes are ``safe`` / ``paper`` /
    ``live`` (see ``get_vyomquant_mode`` and its callers). So the branch cannot fire even in
    production.

    ITS ``PRODUCTION_ROUTER_ENABLED`` ASSERTION WAS REMOVED (ENV-04). It read::

        production_router = os.getenv("PRODUCTION_ROUTER_ENABLED", "true").lower()
        if production_router != "true":
            raise ValueError("CRITICAL: Production router disabled. ...
                             Production requires PRODUCTION_ROUTER_ENABLED=true")

    which demanded the OPPOSITE of the policy every other source in this repository states:
    it treated a DISABLED execution router as evidence of environment-variable tampering and
    refused to start, while the ``feature_flags`` docstring, ``.env.example`` and all four
    task definitions say the router should be off. Both directions could not be right, and
    the one worth keeping is the one that does not require an execution surface to be
    mounted. Had this function ever been called, it would have refused to boot the four
    deployments this repository actually ships.

    Nothing else here is changed, and nothing new is enforced: the live control for the
    router is :func:`production_router_enabled`, read at import by both ``ExecutionFlags``
    classes and by ``main.py``'s mount decision.
    """
    env = os.getenv("ENV", "development").lower()
    
    if env == "production":
        # In production, enforce strict safety validation
        unsafe_mode = os.getenv("VYOMQUANT_MODE", "").lower()
        legacy_mode = os.getenv("AERORA_MODE", "").lower()
        
        if unsafe_mode == "live_trading" or legacy_mode == "live_trading":
            # Allow live trading only if explicitly enabled via proper secure mechanism
            # This check ensures environment variable manipulation doesn't bypass safety
            enable_trading = os.getenv("VYOMQUANT_ENABLE_LIVE_TRADING", "false").lower()
            legacy_enable = os.getenv("AERORA_ENABLE_LIVE_TRADING", "false").lower()
            
            if enable_trading != "true" and legacy_enable != "true":
                raise ValueError(
                    "CRITICAL: Safety configuration violation. "
                    "Live trading mode set without proper authorization. "
                    "Environment variable manipulation detected."
                )
        
        # The PRODUCTION_ROUTER_ENABLED assertion that used to sit here has been removed
        # (ENV-04). See this function's docstring: it required the execution router to be
        # MOUNTED in production, which is the opposite of what the feature-flag docstring,
        # .env.example and all four task definitions say, and it would have refused to boot
        # every deployment this repository ships. The authoritative definition is
        # production_router_enabled() above, and omission leaves the router off.

        logger.info("[SAFETY_CONFIG] Production safety configuration validated")
    else:
        logger.info(f"[SAFETY_CONFIG] Development environment detected ({env}), safety validation relaxed")


def get_vyomquant_mode(default: str = "safe") -> str:
    """
    Retrieves the operating mode of the application.
    Prefer 'VYOMQUANT_MODE'. Fallback to 'AERORA_MODE' with a deprecation warning.
    """
    mode = os.getenv("VYOMQUANT_MODE")
    if mode is not None:
        return mode
        
    legacy_mode = os.getenv("AERORA_MODE")
    if legacy_mode is not None:
        logger.warning(
            "DEPRECATION WARNING: 'AERORA_MODE' environment variable is deprecated. "
            "Please update your deployment configuration to use 'VYOMQUANT_MODE' instead."
        )
        return legacy_mode
        
    return default


def get_live_trading_confirmation(default: str = "false") -> str:
    """
    Retrieves the live trading confirmation flag.
    Prefer 'VYOMQUANT_ENABLE_LIVE_TRADING'. Fallback to 'AERORA_ENABLE_LIVE_TRADING'.
    """
    val = os.getenv("VYOMQUANT_ENABLE_LIVE_TRADING")
    if val is not None:
        return val
        
    legacy_val = os.getenv("AERORA_ENABLE_LIVE_TRADING")
    if legacy_val is not None:
        logger.warning(
            "DEPRECATION WARNING: 'AERORA_ENABLE_LIVE_TRADING' environment variable is deprecated. "
            "Please update your deployment configuration to use 'VYOMQUANT_ENABLE_LIVE_TRADING' instead."
        )
        return legacy_val
        
    return default


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
    #: ENV-04: one definition, read from production_router_enabled() above rather than
    #: recomputed here. This used to be a second, identical copy of the same
    #: os.getenv(..., "True") line that core/feature_flags.py also carried, so the default
    #: had to be changed in two places to change at all.
    PRODUCTION_ROUTER_ENABLED: bool = production_router_enabled()

    
    @classmethod
    def freeze_all(cls, authorized: bool = False) -> None:
        """
        Emergency freeze - block all execution.
        
        Args:
            authorized: Authorization flag. In production, this must be explicitly authorized.
        
        SECURITY: In production, requires explicit authorization to prevent unauthorized shutdowns.
        """
        env = os.getenv("ENV", "development").lower()
        
        if env == "production" and not authorized:
            raise PermissionError(
                "CRITICAL: Unauthorized attempt to freeze all execution. "
                "Production requires explicit authorization for safety control operations."
            )
        
        cls.LIVE_TRADING_ENABLED = False
        cls.PAPER_TRADING_ENABLED = False
        cls.MANUAL_ORDER_EXECUTION = False
        cls.STRATEGY_SIGNAL_EXECUTION = False
        logger.warning("[SAFETY_CONFIG] Emergency freeze activated")
        cls.BACKTEST_CAN_SUBMIT_ORDERS = False
        cls.ALLOW_ML_INFERENCE = False
        cls.FALLBACK_TO_MOCK_PREDICTIONS = False
        logger.critical("[FREEZE] SYSTEM FREEZE ACTIVATED - All execution blocked")
    
    @classmethod
    def enable_paper_trading(cls) -> None:
        """Enable paper trading mode only."""
        env_mode = get_vyomquant_mode("safe").lower()
        if env_mode != "paper":
            raise RuntimeError(
                "Paper trading requires VYOMQUANT_MODE=paper environment variable"
            )
        cls.LIVE_TRADING_ENABLED = False
        cls.PAPER_TRADING_ENABLED = True
        cls.MANUAL_ORDER_EXECUTION = True
        cls.STRATEGY_SIGNAL_EXECUTION = True
        cls.BACKTEST_CAN_SUBMIT_ORDERS = False
        cls.ALLOW_ML_INFERENCE = True  # Allow with mock fallback disabled
        logger.info("[PAPER] Paper trading mode enabled")
    
    @classmethod
    def enable_live_trading(cls) -> None:
        """
        Enable live trading - DANGER.
        Only call after ALL safety checks pass.
        """
        # Require explicit environment confirmation
        env_mode = get_vyomquant_mode("safe").lower()
        if env_mode != "live":
            raise RuntimeError(
                "Live trading requires VYOMQUANT_MODE=live environment variable"
            )
        env_confirm = get_live_trading_confirmation("false").lower()
        if env_confirm != "true":
            raise RuntimeError(
                "Live trading requires VYOMQUANT_ENABLE_LIVE_TRADING=true environment variable"
            )
        
        cls.PAPER_TRADING_ENABLED = False
        cls.LIVE_TRADING_ENABLED = True
        cls.MANUAL_ORDER_EXECUTION = True
        cls.STRATEGY_SIGNAL_EXECUTION = True
        cls.ALLOW_ML_INFERENCE = True
        logger.critical("[LIVE] LIVE TRADING ENABLED - Financial risk active")


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
        env_mode = get_vyomquant_mode("safe").lower()
        if env_mode == "safe":
            return f"[BLOCKED] EXECUTION BLOCKED: VYOMQUANT_MODE=safe (operation: {operation})"
        
        if env_mode not in ["paper", "live"]:
            return f"[BLOCKED] EXECUTION BLOCKED: Invalid VYOMQUANT_MODE={env_mode} (operation: {operation})"

        if env_mode == "paper":
            if ExecutionFlags.LIVE_TRADING_ENABLED:
                return f"[BLOCKED] EXECUTION BLOCKED: Live flag cannot be enabled in paper mode (operation: {operation})"
            if not ExecutionFlags.PAPER_TRADING_ENABLED:
                return f"[BLOCKED] EXECUTION BLOCKED: Paper trading flag disabled (operation: {operation})"
            if operation == "live_order":
                return f"[BLOCKED] EXECUTION BLOCKED: Live execution is not allowed in paper mode (operation: {operation})"

        if env_mode == "live":
            if get_live_trading_confirmation("false").lower() != "true":
                return f"[BLOCKED] EXECUTION BLOCKED: VYOMQUANT_ENABLE_LIVE_TRADING is not true (operation: {operation})"
            if not ExecutionFlags.LIVE_TRADING_ENABLED:
                return f"[BLOCKED] EXECUTION BLOCKED: Live trading flag disabled (operation: {operation})"
        
        # Operation-specific checks
        if operation == "manual_order" and not ExecutionFlags.MANUAL_ORDER_EXECUTION:
            return "[BLOCKED] EXECUTION BLOCKED: Manual orders disabled"
        
        if operation == "strategy_signal" and not ExecutionFlags.STRATEGY_SIGNAL_EXECUTION:
            return "[BLOCKED] EXECUTION BLOCKED: Strategy signals disabled"
        
        if operation == "ml_inference" and not ExecutionFlags.ALLOW_ML_INFERENCE:
            return "[BLOCKED] EXECUTION BLOCKED: ML inference disabled"
        
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
        
        env_mode = get_vyomquant_mode("safe")
        if env_mode != "safe":
            raise RuntimeError(
                f"System is NOT in safe mode - VYOMQUANT_MODE={env_mode}"
            )


# Initialize on import - FREEZE BY DEFAULT
ExecutionFlags.LIVE_TRADING_ENABLED = False
ExecutionFlags.PAPER_TRADING_ENABLED = False
ExecutionFlags.MANUAL_ORDER_EXECUTION = False
ExecutionFlags.STRATEGY_SIGNAL_EXECUTION = False
ExecutionFlags.BACKTEST_CAN_SUBMIT_ORDERS = False
ExecutionFlags.ALLOW_ML_INFERENCE = False
ExecutionFlags.FALLBACK_TO_MOCK_PREDICTIONS = False
