"""
PHASE 7: UNIFIED SAFETY + PRE-TRADE VALIDATION ENGINE
STEP 7.1 + 7.2 + 7.3 + 7.4 + 7.5 + 7.6 + 7.7 + 7.8 + 7.9 + 7.10 + 7.12 + 7.13: CENTRAL EXECUTION GUARD (MANDATORY)

STEP 7.1: Create unified safety system validating ALL trades
STEP 7.2: Signal validation (symbol, side, size checks)
STEP 7.3: Duplicate order check (Redis integration)
STEP 7.4: Balance validation (sufficient margin check)
STEP 7.5: Position + exposure validation (reuse Phase 5 limits)
STEP 7.6: Strategy conflict check (NET_POSITION logic)
STEP 7.7: Market conditions check (spread, volatility, liquidity)
STEP 7.8: Latency validation (signal age < 5 sec, integrate Step 6.9)
STEP 7.9: System health validation (integrate FailsafeExecutionGuard Step 6.10)
STEP 7.10: Final Decision Engine (standardized output format)
STEP 7.12: Audit Logging (log EVERY decision to Redis)
STEP 7.13: Risk Scoring System (ADVANCED - composite risk score)

This is the FINAL CONTROL LAYER that validates EVERY trade before execution.

Audit Key Format: audit:execution_guard:{tenant_id}
Stored: signal, decision, reason, timestamp

Risk Score Formula:
  risk_score = exposure_risk + volatility_risk + liquidity_risk + leverage_risk
  IF risk_score > threshold: BLOCK

Overrides:
- DAG signals
- API orders  
- Manual trades
- Strategy deployment

Goal: Single centralized safety system - no trade passes without validation.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Validation result severity levels."""
    PASS = "pass"
    WARNING = "warning"
    BLOCK = "block"


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    check_name: str
    passed: bool
    severity: ValidationSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass  
class TradeValidationReport:
    """Complete validation report for a trade."""
    tenant_id: str
    trade_id: str
    signal_id: str
    symbol: str
    side: str
    size: Decimal
    
    overall_passed: bool = False
    severity: ValidationSeverity = ValidationSeverity.BLOCK
    results: List[ValidationResult] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)
    warning_reasons: List[str] = field(default_factory=list)
    
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    execution_allowed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "tenant_id": self.tenant_id,
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "size": str(self.size),
            "overall_passed": self.overall_passed,
            "severity": self.severity.value,
            "execution_allowed": self.execution_allowed,
            "blocked_reasons": self.blocked_reasons,
            "warning_reasons": self.warning_reasons,
            "results": [
                {
                    "check_name": r.check_name,
                    "passed": r.passed,
                    "severity": r.severity.value,
                    "message": r.message,
                    "details": r.details,
                    "timestamp": r.timestamp,
                }
                for r in self.results
            ],
            "timestamp": self.timestamp,
        }


class ExecutionGuard:
    """
    STEP 7.1: CENTRAL EXECUTION GUARD (MANDATORY)
    
    Unified safety system that validates EVERY trade before execution.
    
    This is the FINAL GATE - no trade passes without passing all checks.
    
    Validates:
    1. Portfolio limits (exposure, concentration)
    2. Risk limits (drawdown, daily loss)
    3. Market conditions (spread, volatility, circuit breakers)
    4. System health (connectivity, data freshness)
    5. Order validity (size, price, symbol)
    6. Regulatory compliance (if applicable)
    
    Usage:
        guard = ExecutionGuard(redis_client)
        report = await guard.validate_trade(
            tenant_id="tenant-123",
            signal={...},
            portfolio_state={...},
            market_state={...}
        )
        
        if report.execution_allowed:
            execute_trade()
        else:
            log_rejection(report.blocked_reasons)
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
        # Validation thresholds
        self.max_portfolio_concentration = Decimal("0.25")  # 25% max in single position
        self.max_daily_drawdown_pct = Decimal("0.05")  # 5% max daily drawdown
        self.max_order_size_usd = Decimal("1000000")  # $1M max order
        self.min_order_size_usd = Decimal("10")  # $10 min order
        self.max_spread_pct = Decimal("0.01")  # 1% max spread
        
        # Statistics
        self.total_validations = 0
        self.passed_count = 0
        self.blocked_count = 0
        self.warning_count = 0
    
    async def validate_trade(
        self,
        tenant_id: str,
        signal: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        market_state: Dict[str, Any]
    ) -> TradeValidationReport:
        """
        STEP 7.1: Main validation entry point.
        
        Validates a trade signal against all safety checks.
        
        Args:
            tenant_id: Tenant identifier
            signal: Trade signal dict with symbol, side, size, price
            portfolio_state: Current portfolio metrics
            market_state: Current market data (spread, price, etc.)
            
        Returns:
            TradeValidationReport with complete validation results
        """
        self.total_validations += 1
        
        trade_id = signal.get("trade_id", f"trade_{datetime.utcnow().timestamp()}")
        signal_id = signal.get("signal_id", "unknown")
        symbol = signal.get("symbol", "unknown")
        side = signal.get("side", "unknown")
        size = Decimal(str(signal.get("size", 0)))
        
        logger.info(
            f"STEP 7.1: Validating trade {trade_id} | "
            f"{symbol} {side} {size} | Tenant: {tenant_id}"
        )
        
        report = TradeValidationReport(
            tenant_id=tenant_id,
            trade_id=trade_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            size=size,
        )
        
        # Run all validation checks
        checks = [
            self._validate_signal_basic(signal),  # STEP 7.2
            self._validate_signal_latency(signal),  # STEP 7.8
            self._validate_no_duplicate_order(tenant_id, signal),  # STEP 7.3
            self._validate_sufficient_balance(portfolio_state, signal),  # STEP 7.4
            self._validate_position_and_exposure_limits(portfolio_state, signal),  # STEP 7.5
            self._validate_strategy_conflict(portfolio_state, signal),  # STEP 7.6
            self._validate_order_size(signal),
            self._validate_symbol_allowed(tenant_id, symbol),
            self._validate_portfolio_concentration(portfolio_state, signal),
            self._validate_exposure_limits(portfolio_state, signal),
            self._validate_daily_drawdown(portfolio_state),
            self._validate_market_conditions_detailed(market_state, signal),  # STEP 7.7
            self._validate_composite_risk_score(portfolio_state, market_state, signal),  # STEP 7.13
            self._validate_circuit_breakers(tenant_id, symbol),
            self._validate_system_health(tenant_id, symbol, portfolio_state),  # STEP 7.9
            self._validate_risk_engine_status(),
        ]
        
        # Execute all checks concurrently
        results = await asyncio.gather(*checks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                # Check failed with exception - treat as BLOCK
                error_result = ValidationResult(
                    check_name="exception",
                    passed=False,
                    severity=ValidationSeverity.BLOCK,
                    message=f"Validation exception: {str(result)}",
                )
                report.results.append(error_result)
                report.blocked_reasons.append(f"Exception during validation: {result}")
            else:
                report.results.append(result)
                
                if result.severity == ValidationSeverity.BLOCK:
                    report.blocked_reasons.append(f"{result.check_name}: {result.message}")
                elif result.severity == ValidationSeverity.WARNING:
                    report.warning_reasons.append(f"{result.check_name}: {result.message}")
        
        # Determine overall result
        has_blocks = any(r.severity == ValidationSeverity.BLOCK for r in report.results)
        has_warnings = any(r.severity == ValidationSeverity.WARNING for r in report.results)
        all(r.passed for r in report.results)
        
        if has_blocks:
            report.overall_passed = False
            report.severity = ValidationSeverity.BLOCK
            report.execution_allowed = False
            self.blocked_count += 1
            
            logger.critical(
                f"🚫 STEP 7.1: TRADE BLOCKED | {trade_id} | {symbol} | "
                f"Reasons: {report.blocked_reasons}"
            )
        elif has_warnings:
            report.overall_passed = True
            report.severity = ValidationSeverity.WARNING
            report.execution_allowed = True  # Allow but warn
            self.warning_count += 1
            
            logger.warning(
                f"⚠️ STEP 7.1: TRADE WARNING | {trade_id} | {symbol} | "
                f"Warnings: {report.warning_reasons}"
            )
        else:
            report.overall_passed = True
            report.severity = ValidationSeverity.PASS
            report.execution_allowed = True
            self.passed_count += 1
            
            logger.info(
                f"✅ STEP 7.1: TRADE VALIDATED | {trade_id} | {symbol} | "
                f"All {len(report.results)} checks passed"
            )
        
        # Store validation report in Redis for audit
        await self._store_validation_report(report)
        
        # STEP 7.12: Log audit record (ALWAYS log, regardless of pass/fail)
        decision = self.make_final_decision(report)
        await self._log_audit_record(
            tenant_id=tenant_id,
            signal=signal,
            decision=decision,
            report=report
        )
        
        return report
    
    async def _validate_signal_latency(
        self,
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.8: Latency Validation (Integrate Step 6.9)
        
        Check:
        signal_age < threshold (5 seconds)
        
        IF stale: BLOCK
        
        Prevents execution of stale signals that may no longer be relevant.
        """
        # Get signal timestamp
        signal_timestamp = signal.get("timestamp") or signal.get("created_at")
        
        if not signal_timestamp:
            # No timestamp - cannot verify freshness
            return ValidationResult(
                check_name="signal_latency",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message="No signal timestamp - cannot verify freshness",
                details={"warning": "Signal age unknown, proceeding with caution"},
            )
        
        # Parse timestamp
        try:
            if isinstance(signal_timestamp, str):
                from datetime import datetime
                if 'Z' in signal_timestamp:
                    signal_timestamp = signal_timestamp.replace('Z', '+00:00')
                signal_time = datetime.fromisoformat(signal_timestamp)
            elif isinstance(signal_timestamp, (int, float)):
                # Unix timestamp
                signal_time = datetime.fromtimestamp(signal_timestamp)
            else:
                signal_time = signal_timestamp
            
            # Ensure both times are timezone-aware or both naive
            current_time = datetime.utcnow()
            if signal_time.tzinfo is not None:
                from datetime import timezone
                current_time = current_time.replace(tzinfo=timezone.utc)
            
            # Calculate age
            signal_age_seconds = (current_time - signal_time).total_seconds()
            
        except Exception as e:
            return ValidationResult(
                check_name="signal_latency",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Failed to parse signal timestamp: {e}",
                details={"timestamp_raw": str(signal_timestamp)},
            )
        
        # Threshold: 5 seconds (from Step 6.9)
        max_signal_age_seconds = 5.0
        
        if signal_age_seconds > max_signal_age_seconds:
            return ValidationResult(
                check_name="signal_latency",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Signal stale: age={signal_age_seconds:.2f}s > max={max_signal_age_seconds}s",
                details={
                    "signal_age_seconds": signal_age_seconds,
                    "max_allowed_seconds": max_signal_age_seconds,
                    "signal_time": signal_time.isoformat(),
                    "current_time": current_time.isoformat(),
                },
            )
        
        return ValidationResult(
            check_name="signal_latency",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Signal fresh: age={signal_age_seconds:.2f}s < {max_signal_age_seconds}s",
            details={
                "signal_age_seconds": signal_age_seconds,
                "max_allowed_seconds": max_signal_age_seconds,
            },
        )

    async def _validate_market_conditions_detailed(
        self,
        market_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.7: Market Conditions Check (DETAILED)
        
        Check:
        - spread < threshold (e.g., 50 bps)
        - volatility < max limit (e.g., 5%)
        - liquidity sufficient (e.g., 2x order size)
        
        IF bad market: BLOCK
        """
        symbol = signal.get("symbol", "unknown")
        size = Decimal(str(signal.get("size", 0)))
        price = Decimal(str(signal.get("price", 0)))
        notional = size * price
        
        # Get market data
        spread_bps = Decimal(str(market_state.get("spread_bps", 0)))
        Decimal(str(market_state.get("spread_pct", 0)))
        volatility = Decimal(str(market_state.get("volatility", 0)))
        Decimal(str(market_state.get("volume_24h", 0)))
        liquidity_depth = Decimal(str(market_state.get("liquidity_depth", 0)))
        
        # Thresholds (can be made configurable)
        max_spread_bps = Decimal("50")  # 50 bps = 0.5%
        max_volatility = Decimal("0.05")  # 5%
        min_liquidity_multiplier = Decimal("2")  # Need 2x order size in liquidity
        
        issues = []
        
        # Check 1: Spread
        if spread_bps > max_spread_bps:
            issues.append(f"Spread too wide: {spread_bps} bps > {max_spread_bps} bps max")
        
        # Check 2: Volatility
        if volatility > max_volatility:
            issues.append(f"Volatility too high: {volatility:.2%} > {max_volatility:.2%} max")
        
        # Check 3: Liquidity (if available)
        if liquidity_depth > 0:
            required_liquidity = notional * min_liquidity_multiplier
            if liquidity_depth < required_liquidity:
                issues.append(f"Insufficient liquidity: {liquidity_depth} < {required_liquidity} required")
        
        # Check 4: Market halted/abnormal (if status available)
        market_status = market_state.get("status", "open")
        if market_status not in ["open", "active", "normal"]:
            issues.append(f"Market not open: status={market_status}")
        
        if issues:
            return ValidationResult(
                check_name="market_conditions_detailed",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Bad market conditions: {'; '.join(issues)}",
                details={
                    "symbol": symbol,
                    "spread_bps": str(spread_bps),
                    "volatility": str(volatility),
                    "liquidity_depth": str(liquidity_depth),
                    "market_status": market_status,
                    "issues": issues,
                    "thresholds": {
                        "max_spread_bps": str(max_spread_bps),
                        "max_volatility": str(max_volatility),
                        "min_liquidity_multiplier": str(min_liquidity_multiplier),
                    },
                },
            )
        
        return ValidationResult(
            check_name="market_conditions_detailed",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Market conditions good for {symbol}",
            details={
                "spread_bps": str(spread_bps),
                "volatility": str(volatility),
                "liquidity_depth": str(liquidity_depth),
            },
        )

    async def _validate_strategy_conflict(
        self,
        portfolio_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.6: Strategy Conflict Check
        
        Check existing positions vs new signal for conflicts.
        
        Resolution Strategies:
        - NET_POSITION: Net out opposing positions (reduce/close first)
        - REJECT: Block if opposing position exists
        - ALLOW: Allow conflicting positions (hedging)
        
        IF conflict unresolved: BLOCK
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "").lower()
        strategy = signal.get("conflict_resolution", "NET_POSITION")  # Default: net position
        
        # Normalize side to position direction
        is_long_signal = side in {"buy", "long"}
        is_short_signal = side in {"sell", "short"}
        
        # Get existing position for this symbol
        positions = portfolio_state.get("positions", {})
        existing_position = None
        
        for pos_id, pos in positions.items():
            if pos.get("symbol") == symbol:
                existing_position = pos
                break
        
        # No existing position = no conflict
        if not existing_position:
            return ValidationResult(
                check_name="strategy_conflict",
                passed=True,
                severity=ValidationSeverity.PASS,
                message=f"No existing position for {symbol} - no conflict",
            )
        
        # Check if existing position conflicts with signal
        pos_size = Decimal(str(existing_position.get("size", 0)))
        pos_side = existing_position.get("side", "").lower()
        is_long_position = pos_side in {"buy", "long"}
        is_short_position = pos_side in {"sell", "short"}
        
        # Determine if there's a conflict
        has_conflict = False
        conflict_type = None
        
        if is_long_signal and is_short_position:
            has_conflict = True
            conflict_type = "long_signal_vs_short_position"
        elif is_short_signal and is_long_position:
            has_conflict = True
            conflict_type = "short_signal_vs_long_position"
        
        if not has_conflict:
            return ValidationResult(
                check_name="strategy_conflict",
                passed=True,
                severity=ValidationSeverity.PASS,
                message=f"Signal aligns with existing position ({pos_side})",
                details={
                    "existing_side": pos_side,
                    "signal_side": side,
                },
            )
        
        # Handle conflict based on strategy
        if strategy == "REJECT":
            return ValidationResult(
                check_name="strategy_conflict",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Conflict: {conflict_type}. Strategy=REJECT - blocking",
                details={
                    "conflict_type": conflict_type,
                    "existing_position": existing_position,
                    "signal": signal,
                    "resolution_strategy": strategy,
                },
            )
        
        elif strategy == "ALLOW":
            return ValidationResult(
                check_name="strategy_conflict",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Conflict: {conflict_type}. Strategy=ALLOW - proceeding with hedge",
                details={
                    "conflict_type": conflict_type,
                    "resolution_strategy": strategy,
                    "warning": "Opposing positions will exist (hedge)",
                },
            )
        
        elif strategy == "NET_POSITION":
            # Calculate net position after trade
            signal_size = Decimal(str(signal.get("size", 0)))
            
            if is_long_signal:
                # Selling short position or adding to long
                if pos_size > signal_size:
                    # Will reduce short position
                    new_size = pos_size - signal_size
                    new_side = pos_side
                    net_action = "reduce_short"
                else:
                    # Will flip to long
                    new_size = signal_size - pos_size
                    new_side = "long"
                    net_action = "flip_long"
            else:  # short signal
                # Selling long position or adding to short
                if pos_size > signal_size:
                    # Will reduce long position
                    new_size = pos_size - signal_size
                    new_side = pos_side
                    net_action = "reduce_long"
                else:
                    # Will flip to short
                    new_size = signal_size - pos_size
                    new_side = "short"
                    net_action = "flip_short"
            
            return ValidationResult(
                check_name="strategy_conflict",
                passed=True,
                severity=ValidationSeverity.PASS,
                message=f"Conflict resolved via NET_POSITION: {net_action}",
                details={
                    "conflict_type": conflict_type,
                    "resolution_strategy": strategy,
                    "net_action": net_action,
                    "current_size": str(pos_size),
                    "signal_size": str(signal_size),
                    "new_net_size": str(new_size),
                    "new_net_side": new_side,
                    "requires_close_first": net_action in ["reduce_short", "reduce_long"],
                },
            )
        
        else:
            # Unknown strategy - fail safe and block
            return ValidationResult(
                check_name="strategy_conflict",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Unknown conflict resolution strategy: {strategy}",
                details={
                    "conflict_type": conflict_type,
                    "strategy": strategy,
                    "valid_strategies": ["NET_POSITION", "REJECT", "ALLOW"],
                },
            )

    async def _validate_position_and_exposure_limits(
        self,
        portfolio_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.5: Position + Exposure Validation
        
        Reuse Phase 5 limits:
        - max_position_size: Maximum size for any single position
        - max_symbol_exposure: Maximum exposure per symbol
        - max_total_exposure: Maximum total portfolio exposure
        
        IF exceeded: BLOCK
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "").lower()
        size = Decimal(str(signal.get("size", 0)))
        price = Decimal(str(signal.get("price", 0)))
        notional = size * price
        
        # Phase 5 limits (can be loaded from config)
        max_position_size = Decimal(str(portfolio_state.get("max_position_size", "100")))  # Default 100 units
        max_symbol_exposure = Decimal(str(portfolio_state.get("max_symbol_exposure", "50000")))  # Default $50k
        max_total_exposure = Decimal(str(portfolio_state.get("max_total_exposure", "200000")))  # Default $200k
        
        # Get current position for this symbol
        positions = portfolio_state.get("positions", {})
        current_position_size = Decimal("0")
        current_symbol_exposure = Decimal("0")
        
        for pos_id, pos in positions.items():
            if pos.get("symbol") == symbol:
                pos_size = Decimal(str(pos.get("size", 0)))
                pos_price = Decimal(str(pos.get("current_price", price)))
                current_position_size = pos_size
                current_symbol_exposure = pos_size * pos_price
                break
        
        # Calculate new totals after this trade
        is_buying = side in {"buy", "long"}
        
        if is_buying:
            new_position_size = current_position_size + size
            new_symbol_exposure = current_symbol_exposure + notional
        else:
            # Selling reduces position
            new_position_size = max(Decimal("0"), current_position_size - size)
            new_symbol_exposure = max(Decimal("0"), current_symbol_exposure - notional)
        
        # Check 1: Max position size
        if new_position_size > max_position_size:
            return ValidationResult(
                check_name="position_exposure_limits",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Position size {new_position_size} exceeds max {max_position_size}",
                details={
                    "limit_type": "max_position_size",
                    "current_size": str(current_position_size),
                    "new_size": str(new_position_size),
                    "max_allowed": str(max_position_size),
                    "symbol": symbol,
                },
            )
        
        # Check 2: Max symbol exposure
        if new_symbol_exposure > max_symbol_exposure:
            return ValidationResult(
                check_name="position_exposure_limits",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Symbol exposure {new_symbol_exposure} USD exceeds max {max_symbol_exposure} USD",
                details={
                    "limit_type": "max_symbol_exposure",
                    "current_exposure": str(current_symbol_exposure),
                    "new_exposure": str(new_symbol_exposure),
                    "max_allowed": str(max_symbol_exposure),
                    "symbol": symbol,
                },
            )
        
        # Check 3: Max total exposure
        total_exposure = Decimal(str(portfolio_state.get("total_exposure", 0)))
        
        if is_buying:
            new_total_exposure = total_exposure + notional
        else:
            new_total_exposure = max(Decimal("0"), total_exposure - notional)
        
        if new_total_exposure > max_total_exposure:
            return ValidationResult(
                check_name="position_exposure_limits",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Total exposure {new_total_exposure} USD exceeds max {max_total_exposure} USD",
                details={
                    "limit_type": "max_total_exposure",
                    "current_total": str(total_exposure),
                    "new_total": str(new_total_exposure),
                    "max_allowed": str(max_total_exposure),
                },
            )
        
        return ValidationResult(
            check_name="position_exposure_limits",
            passed=True,
            severity=ValidationSeverity.PASS,
            message="Position and exposure limits satisfied",
            details={
                "new_position_size": str(new_position_size),
                "new_symbol_exposure": str(new_symbol_exposure),
                "new_total_exposure": str(new_total_exposure),
                "max_position_size": str(max_position_size),
                "max_symbol_exposure": str(max_symbol_exposure),
                "max_total_exposure": str(max_total_exposure),
            },
        )

    async def _validate_sufficient_balance(
        self,
        portfolio_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.4: Balance Validation
        
        Check:
        available_balance >= required_margin
        
        IF not: BLOCK
        """
        # Get available balance
        available_balance = Decimal(str(portfolio_state.get("available_balance", 0)))
        free_margin = Decimal(str(portfolio_state.get("free_margin", portfolio_state.get("available_balance", 0))))
        
        # Calculate required margin for this trade
        size = Decimal(str(signal.get("size", 0)))
        price = Decimal(str(signal.get("price", 0)))
        notional = size * price
        
        # Assume 10% margin requirement (adjustable)
        margin_requirement_pct = Decimal("0.10")
        required_margin = notional * margin_requirement_pct
        
        # Use the more conservative of available_balance or free_margin
        effective_balance = min(available_balance, free_margin)
        
        if effective_balance < required_margin:
            return ValidationResult(
                check_name="balance_validation",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Insufficient balance: {effective_balance} < required {required_margin}",
                details={
                    "available_balance": str(available_balance),
                    "free_margin": str(free_margin),
                    "required_margin": str(required_margin),
                    "notional_value": str(notional),
                    "margin_requirement_pct": str(margin_requirement_pct),
                    "shortfall": str(required_margin - effective_balance),
                },
            )
        
        # Check if leaving enough buffer (warn if using >90% of available)
        utilization_pct = required_margin / effective_balance if effective_balance > 0 else Decimal("1")
        
        if utilization_pct > Decimal("0.90"):
            return ValidationResult(
                check_name="balance_validation",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"High margin utilization: {utilization_pct:.1%} of available balance",
                details={
                    "available_balance": str(effective_balance),
                    "required_margin": str(required_margin),
                    "utilization_pct": str(utilization_pct),
                },
            )
        
        return ValidationResult(
            check_name="balance_validation",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Sufficient balance: {effective_balance} >= required {required_margin}",
            details={
                "available_balance": str(effective_balance),
                "required_margin": str(required_margin),
                "utilization_pct": str(utilization_pct),
            },
        )

    async def _validate_no_duplicate_order(
        self,
        tenant_id: str,
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.3: Duplicate Order Check (Integrate Step 6.1)
        
        Checks Redis for existing order with same client_order_id.
        Key format: orders:{tenant_id}:{client_order_id}
        
        IF exists: BLOCK (prevent duplicate)
        """
        client_order_id = signal.get("client_order_id") or signal.get("order_id")
        
        if not client_order_id:
            # No order ID provided - this is a new order, allow
            return ValidationResult(
                check_name="duplicate_order",
                passed=True,
                severity=ValidationSeverity.PASS,
                message="No client_order_id - new order",
            )
        
        # Check Redis for existing order
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            existing = await self.redis.get(redis_key)
            
            if existing:
                # Order already exists - BLOCK to prevent duplicate
                order_data = eval(existing.decode())
                return ValidationResult(
                    check_name="duplicate_order",
                    passed=False,
                    severity=ValidationSeverity.BLOCK,
                    message=f"Duplicate order detected: {client_order_id} already exists",
                    details={
                        "client_order_id": client_order_id,
                        "redis_key": redis_key,
                        "existing_status": order_data.get("status", "unknown"),
                        "existing_created_at": order_data.get("created_at"),
                    },
                )
            
            # Order does not exist - safe to proceed
            return ValidationResult(
                check_name="duplicate_order",
                passed=True,
                severity=ValidationSeverity.PASS,
                message=f"No duplicate found for {client_order_id}",
                details={"client_order_id": client_order_id},
            )
            
        except Exception as e:
            # If Redis check fails, fail safe and block
            return ValidationResult(
                check_name="duplicate_order",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Failed to check for duplicates: {e}",
                details={"client_order_id": client_order_id, "error": str(e)},
            )

    async def _validate_signal_basic(self, signal: Dict[str, Any]) -> ValidationResult:
        """
        STEP 7.2: Validate basic signal properties.
        
        Checks:
        - signal exists and is not empty
        - symbol is valid (non-empty string)
        - side is 'buy' or 'sell'
        - size > 0
        """
        # Check signal exists
        if not signal or not isinstance(signal, dict):
            return ValidationResult(
                check_name="signal_basic",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message="Signal is missing or invalid",
                details={"signal": str(signal)},
            )
        
        # Check symbol
        symbol = signal.get("symbol")
        if not symbol or not isinstance(symbol, str) or len(symbol.strip()) == 0:
            return ValidationResult(
                check_name="signal_basic",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Invalid or missing symbol: {symbol}",
                details={"symbol": str(symbol)},
            )
        
        # Check side
        side = signal.get("side", "").lower().strip()
        valid_sides = {"buy", "sell", "long", "short"}
        if side not in valid_sides:
            return ValidationResult(
                check_name="signal_basic",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Invalid side: '{side}'. Must be one of: {valid_sides}",
                details={"side": side, "valid_sides": list(valid_sides)},
            )
        
        # Normalize side to buy/sell
        normalized_side = "buy" if side in {"buy", "long"} else "sell"
        
        # Check size
        try:
            size = Decimal(str(signal.get("size", 0)))
            if size <= 0:
                return ValidationResult(
                    check_name="signal_basic",
                    passed=False,
                    severity=ValidationSeverity.BLOCK,
                    message=f"Invalid size: {size}. Must be > 0",
                    details={"size": str(size)},
                )
        except Exception as e:
            return ValidationResult(
                check_name="signal_basic",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Invalid size format: {signal.get('size')}. Error: {e}",
                details={"size_raw": str(signal.get("size"))},
            )
        
        return ValidationResult(
            check_name="signal_basic",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Signal valid: {symbol} {normalized_side} {size}",
            details={
                "symbol": symbol,
                "side": normalized_side,
                "size": str(size),
                "original_side": side,
            },
        )

    async def _validate_order_size(self, signal: Dict[str, Any]) -> ValidationResult:
        """Validate order size is within acceptable bounds."""
        size = Decimal(str(signal.get("size", 0)))
        price = Decimal(str(signal.get("price", 0)))
        
        if size <= 0:
            return ValidationResult(
                check_name="order_size",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Invalid order size: {size} (must be > 0)",
                details={"size": str(size)},
            )
        
        notional = size * price if price > 0 else Decimal("0")
        
        if notional > self.max_order_size_usd:
            return ValidationResult(
                check_name="order_size",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Order size {notional} USD exceeds maximum {self.max_order_size_usd} USD",
                details={"notional": str(notional), "max": str(self.max_order_size_usd)},
            )
        
        if notional < self.min_order_size_usd and notional > 0:
            return ValidationResult(
                check_name="order_size",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Order size {notional} USD below minimum {self.min_order_size_usd} USD",
                details={"notional": str(notional), "min": str(self.min_order_size_usd)},
            )
        
        return ValidationResult(
            check_name="order_size",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Order size valid: {size} @ {price} = {notional} USD",
            details={"notional": str(notional)},
        )
    
    async def _validate_symbol_allowed(self, tenant_id: str, symbol: str) -> ValidationResult:
        """Check if symbol is allowed for trading."""
        # TODO: Check against allowed symbols list from config/DB
        # For now, assume all symbols allowed
        
        if not symbol or symbol == "unknown":
            return ValidationResult(
                check_name="symbol_allowed",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message="Invalid or missing symbol",
                details={"symbol": symbol},
            )
        
        return ValidationResult(
            check_name="symbol_allowed",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Symbol {symbol} is allowed for trading",
            details={"symbol": symbol},
        )
    
    async def _validate_portfolio_concentration(
        self,
        portfolio_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """Validate position doesn't exceed concentration limits."""
        symbol = signal.get("symbol", "")
        total_equity = Decimal(str(portfolio_state.get("total_equity", 0)))
        
        if total_equity <= 0:
            return ValidationResult(
                check_name="portfolio_concentration",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message="Invalid portfolio equity for concentration check",
                details={"total_equity": str(total_equity)},
            )
        
        # Get current position size for symbol
        positions = portfolio_state.get("positions", {})
        current_position_value = Decimal("0")
        
        for pos_id, pos in positions.items():
            if pos.get("symbol") == symbol:
                current_position_value = Decimal(str(pos.get("market_value", 0)))
                break
        
        # Calculate new concentration after trade
        new_concentration = current_position_value / total_equity
        
        if new_concentration > self.max_portfolio_concentration:
            return ValidationResult(
                check_name="portfolio_concentration",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Position concentration {new_concentration:.2%} exceeds limit {self.max_portfolio_concentration:.2%}",
                details={
                    "current_concentration": str(new_concentration),
                    "max_allowed": str(self.max_portfolio_concentration),
                    "symbol": symbol,
                },
            )
        
        return ValidationResult(
            check_name="portfolio_concentration",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Position concentration {new_concentration:.2%} within limit {self.max_portfolio_concentration:.2%}",
            details={"concentration": str(new_concentration)},
        )
    
    async def _validate_exposure_limits(
        self,
        portfolio_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """Validate total exposure limits."""
        total_exposure = Decimal(str(portfolio_state.get("total_exposure", 0)))
        total_equity = Decimal(str(portfolio_state.get("total_equity", 1)))
        
        exposure_pct = total_exposure / total_equity if total_equity > 0 else Decimal("0")
        
        # Warn if exposure > 100% (leveraged)
        if exposure_pct > Decimal("1.0"):
            return ValidationResult(
                check_name="exposure_limits",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Portfolio is leveraged: {exposure_pct:.2%} exposure vs {total_equity} equity",
                details={"exposure_pct": str(exposure_pct)},
            )
        
        return ValidationResult(
            check_name="exposure_limits",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Exposure {exposure_pct:.2%} within normal limits",
            details={"exposure_pct": str(exposure_pct)},
        )
    
    async def _validate_daily_drawdown(self, portfolio_state: Dict[str, Any]) -> ValidationResult:
        """Validate daily drawdown hasn't exceeded limits."""
        daily_pnl = Decimal(str(portfolio_state.get("daily_pnl", 0)))
        starting_equity = Decimal(str(portfolio_state.get("starting_equity", 1)))
        
        if starting_equity <= 0:
            return ValidationResult(
                check_name="daily_drawdown",
                passed=True,
                severity=ValidationSeverity.PASS,
                message="No starting equity data for drawdown check",
            )
        
        drawdown_pct = abs(daily_pnl) / starting_equity
        
        if drawdown_pct > self.max_daily_drawdown_pct:
            return ValidationResult(
                check_name="daily_drawdown",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Daily drawdown {drawdown_pct:.2%} exceeds limit {self.max_daily_drawdown_pct:.2%}",
                details={
                    "drawdown_pct": str(drawdown_pct),
                    "max_allowed": str(self.max_daily_drawdown_pct),
                    "daily_pnl": str(daily_pnl),
                },
            )
        
        return ValidationResult(
            check_name="daily_drawdown",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Daily drawdown {drawdown_pct:.2%} within limit {self.max_daily_drawdown_pct:.2%}",
            details={"drawdown_pct": str(drawdown_pct)},
        )
    
    async def _validate_market_conditions(
        self,
        market_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """Validate market conditions are suitable for trading."""
        signal.get("symbol", "")
        spread_pct = Decimal(str(market_state.get("spread_pct", 0)))
        volatility = Decimal(str(market_state.get("volatility", 0)))
        
        issues = []
        
        # Check spread
        if spread_pct > self.max_spread_pct:
            issues.append(f"Spread {spread_pct:.2%} exceeds max {self.max_spread_pct:.2%}")
        
        # Check for extreme volatility (e.g., > 10%)
        if volatility > Decimal("0.10"):
            issues.append(f"Extreme volatility: {volatility:.2%}")
        
        if issues:
            return ValidationResult(
                check_name="market_conditions",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message="; ".join(issues),
                details={
                    "spread_pct": str(spread_pct),
                    "volatility": str(volatility),
                    "issues": issues,
                },
            )
        
        return ValidationResult(
            check_name="market_conditions",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Market conditions normal: spread {spread_pct:.3%}, volatility {volatility:.2%}",
            details={"spread_pct": str(spread_pct), "volatility": str(volatility)},
        )
    
    async def _validate_circuit_breakers(self, tenant_id: str, symbol: str) -> ValidationResult:
        """Check if circuit breakers are tripped."""
        try:
            cb_key = f"circuit_breaker:{tenant_id}:{symbol}"
            cb_status = await self.redis.get(cb_key)
            
            if cb_status:
                status_data = eval(cb_status.decode())
                if status_data.get("tripped", False):
                    return ValidationResult(
                        check_name="circuit_breakers",
                        passed=False,
                        severity=ValidationSeverity.BLOCK,
                        message=f"Circuit breaker tripped: {status_data.get('reason', 'unknown')}",
                        details=status_data,
                    )
            
            return ValidationResult(
                check_name="circuit_breakers",
                passed=True,
                severity=ValidationSeverity.PASS,
                message="No circuit breakers tripped",
            )
            
        except Exception as e:
            # Fail safe - if we can't check, assume circuit is tripped
            return ValidationResult(
                check_name="circuit_breakers",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Failed to check circuit breakers: {e}",
            )
    
    async def _validate_system_health(
        self,
        tenant_id: str,
        symbol: str,
        portfolio_state: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.9: System Health Validation (Integrate FailsafeExecutionGuard Step 6.10)
        
        Comprehensive system health checks:
        1. Redis alive - Can connect to Redis
        2. WebSocket alive - Market data fresh (<10s)
        3. Exchange reachable - No connectivity issues
        4. Portfolio consistent - No data corruption
        
        IF any fail: BLOCK
        """
        checks = {}
        issues = []
        
        # Check 1: Redis connectivity
        try:
            await self.redis.ping()
            checks["redis"] = "healthy"
        except Exception as e:
            checks["redis"] = f"unhealthy: {e}"
            issues.append(f"Redis unavailable: {e}")
        
        # Check 2: WebSocket market data freshness
        try:
            ws_key = f"ws:last_update:{tenant_id}:{symbol}"
            ws_last_update = await self.redis.get(ws_key)
            
            if ws_last_update:
                from datetime import datetime, timezone
                last_update = datetime.fromisoformat(ws_last_update.decode())
                current_time = datetime.now(timezone.utc) if last_update.tzinfo else datetime.utcnow()
                staleness_seconds = (current_time - last_update).total_seconds()
                
                if staleness_seconds > 10:  # 10 second threshold
                    checks["websocket"] = f"stale: {staleness_seconds:.1f}s"
                    issues.append(f"WebSocket stale: {staleness_seconds:.1f}s > 10s threshold")
                else:
                    checks["websocket"] = f"healthy: {staleness_seconds:.1f}s"
            else:
                checks["websocket"] = "unknown (no data)"
                # Don't block if no data, just warn
                
        except Exception as e:
            checks["websocket"] = f"error: {e}"
            issues.append(f"WebSocket check failed: {e}")
        
        # Check 3: Exchange connectivity
        try:
            exchange_key = f"exchange:status:{tenant_id}"
            exchange_status = await self.redis.get(exchange_key)
            
            if exchange_status:
                status = exchange_status.decode()
                if status not in ["connected", "operational", "ok"]:
                    checks["exchange"] = f"unhealthy: {status}"
                    issues.append(f"Exchange status: {status}")
                else:
                    checks["exchange"] = status
            else:
                # Assume OK if no status stored
                checks["exchange"] = "unknown (assumed OK)"
                
        except Exception as e:
            checks["exchange"] = f"error: {e}"
            issues.append(f"Exchange check failed: {e}")
        
        # Check 4: Portfolio consistency
        try:
            # Check for portfolio snapshot age
            snapshot_key = f"portfolio:snapshot:{tenant_id}"
            snapshot_data = await self.redis.get(snapshot_key)
            
            if snapshot_data:
                from datetime import datetime, timezone
                snapshot = eval(snapshot_data.decode())
                snapshot_time_str = snapshot.get("timestamp", "")
                
                if snapshot_time_str:
                    snapshot_time = datetime.fromisoformat(snapshot_time_str.replace('Z', '+00:00'))
                    current_time = datetime.now(timezone.utc) if snapshot_time.tzinfo else datetime.utcnow()
                    age_seconds = (current_time - snapshot_time).total_seconds()
                    
                    if age_seconds > 60:  # 60 second threshold
                        checks["portfolio"] = f"stale snapshot: {age_seconds:.1f}s"
                        issues.append(f"Portfolio snapshot stale: {age_seconds:.1f}s > 60s threshold")
                    else:
                        checks["portfolio"] = f"healthy: {age_seconds:.1f}s"
                else:
                    checks["portfolio"] = "healthy (no timestamp)"
            else:
                # Check in-memory portfolio state
                if portfolio_state:
                    checks["portfolio"] = "healthy (in-memory)"
                else:
                    checks["portfolio"] = "unknown (no data)"
                    issues.append("Portfolio state missing")
                    
        except Exception as e:
            checks["portfolio"] = f"error: {e}"
            issues.append(f"Portfolio check failed: {e}")
        
        # Determine result
        if issues:
            return ValidationResult(
                check_name="system_health",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"System health check failed: {'; '.join(issues)}",
                details={
                    "checks": checks,
                    "issues": issues,
                    "tenant_id": tenant_id,
                    "symbol": symbol,
                },
            )
        
        return ValidationResult(
            check_name="system_health",
            passed=True,
            severity=ValidationSeverity.PASS,
            message="System healthy: Redis, WebSocket, Exchange, Portfolio OK",
            details={
                "checks": checks,
                "tenant_id": tenant_id,
                "symbol": symbol,
            },
        )
    
    async def _validate_risk_engine_status(self) -> ValidationResult:
        """Validate risk engine is operational."""
        try:
            risk_key = "risk:engine:status"
            status = await self.redis.get(risk_key)
            
            if status:
                status_data = eval(status.decode())
                if not status_data.get("operational", True):
                    return ValidationResult(
                        check_name="risk_engine",
                        passed=False,
                        severity=ValidationSeverity.BLOCK,
                        message="Risk engine not operational",
                        details=status_data,
                    )
            
            return ValidationResult(
                check_name="risk_engine",
                passed=True,
                severity=ValidationSeverity.PASS,
                message="Risk engine operational",
            )
            
        except Exception as e:
            # If no status set, assume operational (default)
            return ValidationResult(
                check_name="risk_engine",
                passed=True,
                severity=ValidationSeverity.PASS,
                message=f"Risk engine status unknown (assumed OK): {e}",
            )

    async def _validate_composite_risk_score(
        self,
        portfolio_state: Dict[str, Any],
        market_state: Dict[str, Any],
        signal: Dict[str, Any]
    ) -> ValidationResult:
        """
        STEP 7.13: Risk Scoring System (ADVANCED)
        
        Compute composite risk score:
        risk_score = exposure_risk + volatility_risk + liquidity_risk + leverage_risk
        
        IF risk_score > threshold: BLOCK
        
        Risk Components:
        - exposure_risk: Based on position concentration (0-25 points)
        - volatility_risk: Based on market volatility (0-25 points)
        - liquidity_risk: Based on order size vs market depth (0-25 points)
        - leverage_risk: Based on portfolio leverage (0-25 points)
        
        Total: 0-100 scale
        Threshold: 75 (configurable)
        """
        # Risk thresholds
        Decimal("100")
        risk_threshold = Decimal("75")  # Block if score > 75
        
        # Calculate 1. Exposure Risk (concentration-based)
        total_equity = Decimal(str(portfolio_state.get("total_equity", 1)))
        symbol = signal.get("symbol", "")
        size = Decimal(str(signal.get("size", 0)))
        price = Decimal(str(signal.get("price", 0)))
        notional = size * price
        
        # Get current position for this symbol
        positions = portfolio_state.get("positions", {})
        current_position_value = Decimal("0")
        for pos_id, pos in positions.items():
            if pos.get("symbol") == symbol:
                pos_size = Decimal(str(pos.get("size", 0)))
                pos_price = Decimal(str(pos.get("current_price", price)))
                current_position_value = pos_size * pos_price
                break
        
        # Calculate new concentration
        new_position_value = current_position_value + notional
        concentration = new_position_value / total_equity if total_equity > 0 else Decimal("0")
        
        # Exposure risk: 0-25 points based on concentration (25% = max risk)
        exposure_risk = min(Decimal("25"), concentration * Decimal("100"))
        
        # Calculate 2. Volatility Risk
        volatility = Decimal(str(market_state.get("volatility", 0)))
        # Volatility risk: 0-25 points (5% vol = max risk)
        volatility_risk = min(Decimal("25"), volatility * Decimal("500"))
        
        # Calculate 3. Liquidity Risk
        liquidity_depth = Decimal(str(market_state.get("liquidity_depth", 0)))
        if liquidity_depth > 0:
            liquidity_ratio = notional / liquidity_depth
            # Liquidity risk: 0-25 points (40% of depth = max risk)
            liquidity_risk = min(Decimal("25"), liquidity_ratio * Decimal("62.5"))
        else:
            # No liquidity data - assume moderate risk
            liquidity_risk = Decimal("12.5")
        
        # Calculate 4. Leverage Risk
        total_exposure = Decimal(str(portfolio_state.get("total_exposure", 0)))
        new_total_exposure = total_exposure + notional
        leverage = new_total_exposure / total_equity if total_equity > 0 else Decimal("1")
        # Leverage risk: 0-25 points (2x leverage = max risk)
        leverage_risk = min(Decimal("25"), (leverage - Decimal("1")) * Decimal("25"))
        
        # Calculate total risk score
        total_risk_score = exposure_risk + volatility_risk + liquidity_risk + leverage_risk
        
        # Build risk details
        risk_details = {
            "total_risk_score": float(total_risk_score),
            "risk_threshold": float(risk_threshold),
            "exposure_risk": float(exposure_risk),
            "volatility_risk": float(volatility_risk),
            "liquidity_risk": float(liquidity_risk),
            "leverage_risk": float(leverage_risk),
            "concentration_pct": float(concentration * Decimal("100")),
            "volatility_pct": float(volatility * Decimal("100")),
            "liquidity_ratio": float(liquidity_ratio) if liquidity_depth > 0 else None,
            "leverage_ratio": float(leverage),
        }
        
        # Check if risk exceeds threshold
        if total_risk_score > risk_threshold:
            return ValidationResult(
                check_name="composite_risk_score",
                passed=False,
                severity=ValidationSeverity.BLOCK,
                message=f"Risk score {float(total_risk_score):.1f} exceeds threshold {float(risk_threshold):.1f}",
                details=risk_details,
            )
        
        # Check if risk is elevated (warning level: 50-75)
        warning_threshold = Decimal("50")
        if total_risk_score > warning_threshold:
            return ValidationResult(
                check_name="composite_risk_score",
                passed=True,
                severity=ValidationSeverity.WARNING,
                message=f"Elevated risk score: {float(total_risk_score):.1f} (threshold: {float(risk_threshold):.1f})",
                details=risk_details,
            )
        
        return ValidationResult(
            check_name="composite_risk_score",
            passed=True,
            severity=ValidationSeverity.PASS,
            message=f"Risk score acceptable: {float(total_risk_score):.1f}",
            details=risk_details,
        )
    
    async def _store_validation_report(self, report: TradeValidationReport):
        """Store validation report in Redis for audit."""
        try:
            key = f"validation_report:{report.tenant_id}:{report.trade_id}"
            await self.redis.setex(
                key,
                86400 * 7,  # 7 day retention
                str(report.to_dict())
            )
        except Exception as e:
            logger.error(f"Failed to store validation report: {e}")

    async def _log_audit_record(
        self,
        tenant_id: str,
        signal: Dict[str, Any],
        decision: Dict[str, Any],
        report: TradeValidationReport
    ):
        """
        STEP 7.12: Audit Logging - Log EVERY decision to Redis.
        
        Key: audit:execution_guard:{tenant_id}
        
        Stores:
        - signal: The original trade signal
        - decision: Final decision (allowed, reason, risk_score, metadata)
        - reason: List of blocking/warning reasons
        - timestamp: ISO format timestamp
        - trade_id: Unique trade identifier
        - severity: PASS/WARNING/BLOCK
        
        Uses Redis list for chronological ordering (LPUSH).
        Retention: 30 days (TTL on list items via separate cleanup)
        """
        try:
            audit_key = f"audit:execution_guard:{tenant_id}"
            
            # Build audit record
            audit_record = {
                "timestamp": datetime.utcnow().isoformat(),
                "trade_id": report.trade_id,
                "signal_id": report.signal_id,
                "symbol": report.symbol,
                "side": report.side,
                "size": str(report.size),
                "signal": {
                    "symbol": signal.get("symbol"),
                    "side": signal.get("side"),
                    "size": signal.get("size"),
                    "price": signal.get("price"),
                    "timestamp": signal.get("timestamp"),
                    "client_order_id": signal.get("client_order_id"),
                },
                "decision": {
                    "allowed": decision.get("allowed"),
                    "risk_score": decision.get("risk_score"),
                    "severity": report.severity.value,
                },
                "reason": decision.get("reason", []),
                "metadata": decision.get("metadata", {}),
                "overall_passed": report.overall_passed,
                "execution_allowed": report.execution_allowed,
                "total_checks": len(report.results),
                "checks_passed": sum(1 for r in report.results if r.passed),
                "checks_failed": sum(1 for r in report.results if not r.passed),
                "warnings": len(report.warning_reasons),
                "blocks": len(report.blocked_reasons),
            }
            
            # Store as JSON string
            import json
            audit_json = json.dumps(audit_record, default=str)
            
            # Add to Redis list (LPUSH = most recent first)
            await self.redis.lpush(audit_key, audit_json)
            
            # Trim to last 10,000 records to prevent unbounded growth
            await self.redis.ltrim(audit_key, 0, 9999)
            
            # Set TTL on the list (30 days)
            await self.redis.expire(audit_key, 86400 * 30)
            
            logger.info(
                f"STEP 7.12: Audit logged | {tenant_id} | {report.trade_id} | "
                f"Allowed: {decision.get('allowed')} | Key: {audit_key}"
            )
            
        except Exception as e:
            # Don't fail execution if audit logging fails
            logger.error(f"STEP 7.12: Failed to log audit record: {e}")
    
    def make_final_decision(
        self,
        report: TradeValidationReport
    ) -> Dict[str, Any]:
        """
        STEP 7.10: Final Decision Engine
        
        Returns standardized decision output:
        {
            "allowed": True/False,
            "reason": [],
            "risk_score": float,
            "metadata": {}
        }
        
        Decision Logic:
        - allowed: True only if NO BLOCK severity results
        - reason: List of all blocking reasons (if any)
        - risk_score: 0-100 score based on warnings and checks
        - metadata: Additional context (check counts, timing, etc.)
        """
        # Calculate risk score (0-100)
        # Base: 0 (perfect)
        # +10 for each WARNING
        # +30 for each BLOCK (though blocked anyway)
        # +5 for each check with issues
        
        risk_score = 0.0
        
        for result in report.results:
            if result.severity == ValidationSeverity.WARNING:
                risk_score += 10.0
            elif result.severity == ValidationSeverity.BLOCK:
                risk_score += 30.0
            elif not result.passed:
                risk_score += 5.0
        
        # Cap at 100
        risk_score = min(100.0, risk_score)
        
        # Determine reasons
        reasons = []
        
        if report.blocked_reasons:
            reasons.extend(report.blocked_reasons)
        
        if report.warning_reasons and report.execution_allowed:
            # Only include warnings if trade is still allowed
            reasons.extend([f"WARNING: {r}" for r in report.warning_reasons])
        
        # Build metadata
        metadata = {
            "validation_id": report.trade_id,
            "tenant_id": report.tenant_id,
            "symbol": report.symbol,
            "side": report.side,
            "size": str(report.size),
            "timestamp": report.timestamp,
            "total_checks": len(report.results),
            "checks_passed": sum(1 for r in report.results if r.passed),
            "checks_failed": sum(1 for r in report.results if not r.passed),
            "warnings": len(report.warning_reasons),
            "blocks": len(report.blocked_reasons),
            "severity": report.severity.value,
        }
        
        # Build final decision
        decision = {
            "allowed": report.execution_allowed,
            "reason": reasons,
            "risk_score": round(risk_score, 2),
            "metadata": metadata,
        }
        
        logger.info(
            f"STEP 7.10: Final Decision | Trade {report.trade_id} | "
            f"Allowed: {decision['allowed']} | Risk Score: {decision['risk_score']} | "
            f"Reasons: {len(reasons)}"
        )
        
        return decision

    def get_statistics(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return {
            "total_validations": self.total_validations,
            "passed": self.passed_count,
            "blocked": self.blocked_count,
            "warnings": self.warning_count,
            "pass_rate": (
                self.passed_count / self.total_validations * 100
                if self.total_validations > 0 else 0
            ),
        }


# Global instance for convenience
_execution_guard: Optional[ExecutionGuard] = None


def get_execution_guard(redis_client: redis.Redis = None) -> ExecutionGuard:
    """Get or create global ExecutionGuard instance."""
    global _execution_guard
    if _execution_guard is None:
        if redis_client is None:
            raise ValueError("Redis client required for first initialization")
        _execution_guard = ExecutionGuard(redis_client)
    return _execution_guard


async def validate_trade(
    tenant_id: str,
    signal: Dict[str, Any],
    portfolio_state: Dict[str, Any],
    market_state: Dict[str, Any],
    redis_client: redis.Redis = None
) -> TradeValidationReport:
    """
    Convenience function for trade validation.
    
    Usage:
        report = await validate_trade(
            tenant_id="tenant-123",
            signal={"symbol": "BTC", "side": "buy", "size": "0.5"},
            portfolio_state={...},
            market_state={...},
        )
        
        if report.execution_allowed:
            # Execute trade
        else:
            # Log rejection
    """
    guard = get_execution_guard(redis_client)
    return await guard.validate_trade(tenant_id, signal, portfolio_state, market_state)


async def validate_and_decide(
    tenant_id: str,
    signal: Dict[str, Any],
    portfolio_state: Dict[str, Any],
    market_state: Dict[str, Any],
    redis_client: redis.Redis = None
) -> Dict[str, Any]:
    """
    STEP 7.10: Convenience function that validates and returns standardized decision.
    
    Returns format:
    {
        "allowed": True/False,
        "reason": [],
        "risk_score": float,
        "metadata": {}
    }
    
    Usage:
        decision = await validate_and_decide(
            tenant_id="tenant-123",
            signal={"symbol": "BTC", "side": "buy", "size": "0.5"},
            portfolio_state={...},
            market_state={...},
        )
        
        if decision["allowed"]:
            execute_trade()
        else:
            print(f"Blocked: {decision['reason']}")
            print(f"Risk Score: {decision['risk_score']}")
    """
    guard = get_execution_guard(redis_client)
    report = await guard.validate_trade(tenant_id, signal, portfolio_state, market_state)
    return guard.make_final_decision(report)


async def get_audit_logs(
    tenant_id: str,
    limit: int = 100,
    redis_client: redis.Redis = None
) -> List[Dict[str, Any]]:
    """
    STEP 7.12: Retrieve audit logs from Redis.
    
    
    Args:
        tenant_id: The tenant to retrieve logs for
        redis_client: Optional Redis client (uses shared pool if not provided)
        limit: Maximum number of log entries to retrieve
        
    Returns:
        List of audit log entries
    """
    import json
    False
    
    if redis_client is None:
        # CRITICAL FIX C4: Use shared Redis pool instead of creating new connection
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        False  # Don't close - shared pool
    
    try:
        audit_key = f"audit:execution_guard:{tenant_id}"
        
        # Get records from Redis list (LRANGE 0 limit-1)
        records = await redis_client.lrange(audit_key, 0, limit - 1)
        
        # Parse JSON records
        parsed_records = []
        for record in records:
            try:
                parsed = json.loads(record)
                parsed_records.append(parsed)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse audit record: {record[:100]}...")
                continue
        
        return parsed_records
        
    except Exception as e:
        logger.error(f"Failed to retrieve audit logs: {e}")
        return []
    # CRITICAL FIX C4: No close() - shared pool!
