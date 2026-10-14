"""
core/consistency_checker.py — Position Consistency Check Engine

🔴 STEP 4 — CONSISTENCY CHECK ENGINE

Provides automated position reconciliation between local database state
and exchange-reported positions.

TASK:
Every 10 seconds:
    1. Fetch local positions from database
    2. Fetch exchange positions via API
    3. Compare positions for each symbol
    4. IF MISMATCH: record it for ReconciliationScheduler to escalate (RT-16: no longer
       an unconditional direct kill-switch call -- see `_handle_mismatch`'s own docstring)

EXPECTED RESULT:
✔ No hidden drift between local and exchange state, once wired to real tenant data --
  see `_get_active_tenant_exchanges`'s own docstring for why that is not yet on.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_

logger = logging.getLogger("ConsistencyChecker")


class PositionStatus(Enum):
    """Status of position consistency check."""
    MATCHED = "matched"
    MISMATCH = "mismatch"
    MISSING_LOCAL = "missing_local"
    MISSING_EXCHANGE = "missing_exchange"


@dataclass
class PositionComparison:
    """Result of comparing local vs exchange position."""
    symbol: str
    local_size: Decimal
    exchange_size: Decimal
    difference: Decimal
    status: PositionStatus
    timestamp: datetime


@dataclass
class ConsistencyCheckResult:
    """Overall result of consistency check for a tenant/exchange."""
    tenant_id: str
    exchange_id: str
    timestamp: datetime
    all_matched: bool
    mismatches: List[PositionComparison]
    error_message: Optional[str] = None


class PositionConsistencyChecker:
    """
    🔴 STEP 4 — CONSISTENCY CHECK ENGINE
    
    Automated position reconciliation system that runs every 10 seconds
    to detect drift between local state and exchange state.
    
    ARCHITECTURE:
        ┌─────────────┐     ┌──────────────┐     ┌─────────────┐
        │ Local DB    │     │   Compare    │     │  Exchange   │
        │ Positions   │────→│  Positions   │←────│  Positions  │
        └─────────────┘     └──────────────┘     └─────────────┘
                                   ↓
                            ┌──────────────┐
                            │ IF MISMATCH  │
                            │ Kill Switch  │
                            │ ACTIVATE     │
                            └──────────────┘
    
    CHECK INTERVAL:
        10 seconds (configurable)
    
    MISMATCH DETECTION:
        - Position size difference > threshold (default: 0.0001)
        - Missing position on local side
        - Missing position on exchange side
    
    KILL SWITCH TRIGGER:
        RT-16: no longer called directly from here. A mismatch is recorded in
        `reconciliation_mismatches` at severity MEDIUM; ReconciliationScheduler's own,
        already-correct severity gate decides whether anything escalates to the kill
        switch. See `_handle_mismatch`'s own docstring for why this changed.

    EXPECTED RESULT:
        ✔ No hidden drift between local and exchange positions, once wired to real
          tenant data -- see `_get_active_tenant_exchanges`'s own docstring.
    """
    
    # Check interval in seconds
    CHECK_INTERVAL_SECONDS = 10
    
    # Position mismatch threshold (absolute difference)
    MISMATCH_THRESHOLD = Decimal("0.0001")
    
    # Maximum age of position data before considering stale
    MAX_POSITION_AGE_SECONDS = 60
    
    def __init__(self):
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._last_check_time: Dict[str, datetime] = {}
        self._check_count = 0
        self._mismatch_count = 0
    
    async def start(self):
        """Start the consistency checker background task."""
        self._running = True
        self._check_task = asyncio.create_task(
            self._consistency_check_loop(),
            name="consistency_checker"
        )
        logger.info(
            f"🔴 STEP 4: Position Consistency Checker started "
            f"(interval={self.CHECK_INTERVAL_SECONDS}s)"
        )
    
    async def stop(self):
        """Stop the consistency checker."""
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        logger.info("STEP 4: Position Consistency Checker stopped")
    
    async def _consistency_check_loop(self):
        """
        Main consistency check loop.
        
        Runs every CHECK_INTERVAL_SECONDS to compare positions.
        """
        while self._running:
            try:
                await self._run_consistency_checks()
                self._check_count += 1
                
                # Wait before next check
                await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)
                
            except asyncio.CancelledError:
                logger.info("STEP 4: Consistency check loop cancelled")
                break
            except Exception as e:
                logger.critical(f"🔴 STEP 4: Consistency check loop error: {e}", exc_info=True)
                # On error, wait shorter interval before retry
                await asyncio.sleep(1)
    
    async def _run_consistency_checks(self):
        """
        Run consistency checks for all active tenant/exchange pairs.
        """
        try:
            # Get all active tenant/exchange pairs
            tenant_exchanges = await self._get_active_tenant_exchanges()
            
            for tenant_id, exchange_id in tenant_exchanges:
                try:
                    result = await self._check_positions(tenant_id, exchange_id)

                    if result.error_message:
                        # RT-16: a failed CHECK (credentials, network, exchange API
                        # error) is not evidence of a real position mismatch -- it is
                        # evidence the check could not complete. `_check_positions`
                        # reports this as `all_matched=False` with an EMPTY mismatches
                        # list precisely so this branch can tell "couldn't check" apart
                        # from "checked, and it's wrong". Before this guard existed,
                        # this fell into the `not result.all_matched` branch below and
                        # called `_handle_mismatch` with zero mismatches to describe --
                        # a transient exchange timeout would have escalated exactly like
                        # a real drift. Log and move on; nothing was confirmed wrong.
                        logger.warning(
                            f"STEP 4: Could not complete position check for "
                            f"{tenant_id}:{exchange_id}: {result.error_message}"
                        )
                    elif not result.all_matched:
                        # POSITION MISMATCH DETECTED
                        self._mismatch_count += 1
                        await self._handle_mismatch(result)
                    else:
                        logger.debug(
                            f"STEP 4: Positions matched for {tenant_id}:{exchange_id}"
                        )
                
                except Exception as e:
                    logger.error(
                        f"STEP 4: Error checking positions for {tenant_id}:{exchange_id}: {e}"
                    )
        
        except Exception as e:
            logger.critical(f"🔴 STEP 4: Failed to run consistency checks: {e}")
    
    async def _check_positions(
        self,
        tenant_id: str,
        exchange_id: str
    ) -> ConsistencyCheckResult:
        """
        Compare local positions with exchange positions.
        
        Args:
            tenant_id: Tenant identifier
            exchange_id: Exchange identifier
            
        Returns:
            ConsistencyCheckResult with comparison details
        """
        timestamp = datetime.utcnow()
        mismatches: List[PositionComparison] = []
        
        try:
            # Fetch local positions from database
            local_positions = await self._get_local_positions(tenant_id, exchange_id)
            
            # Fetch exchange positions via API
            exchange_positions = await self._get_exchange_positions(tenant_id, exchange_id)
            
            # Get all unique symbols
            all_symbols = set(local_positions.keys()) | set(exchange_positions.keys())
            
            for symbol in all_symbols:
                local_size = local_positions.get(symbol, Decimal("0"))
                exchange_size = exchange_positions.get(symbol, Decimal("0"))
                
                # Calculate difference
                difference = abs(local_size - exchange_size)
                
                # Determine status
                if symbol not in local_positions:
                    status = PositionStatus.MISSING_LOCAL
                elif symbol not in exchange_positions:
                    status = PositionStatus.MISSING_EXCHANGE
                elif difference > self.MISMATCH_THRESHOLD:
                    status = PositionStatus.MISMATCH
                else:
                    status = PositionStatus.MATCHED
                
                # Record mismatch if not matched
                if status != PositionStatus.MATCHED:
                    mismatches.append(PositionComparison(
                        symbol=symbol,
                        local_size=local_size,
                        exchange_size=exchange_size,
                        difference=difference,
                        status=status,
                        timestamp=timestamp
                    ))
            
            all_matched = len(mismatches) == 0
            
            return ConsistencyCheckResult(
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                timestamp=timestamp,
                all_matched=all_matched,
                mismatches=mismatches
            )
        
        except Exception as e:
            logger.error(
                f"STEP 4: Position check failed for {tenant_id}:{exchange_id}: {e}"
            )
            return ConsistencyCheckResult(
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                timestamp=timestamp,
                all_matched=False,
                mismatches=[],
                error_message=str(e)
            )
    
    async def _get_local_positions(
        self,
        tenant_id: str,
        exchange_id: str
    ) -> Dict[str, Decimal]:
        """
        Fetch positions from local database.
        
        Returns:
            Dict mapping symbol to position size
        """
        try:
            from backend_app.core.database import SessionLocal
            from backend_app.core.position_model import PositionModel
            
            positions: Dict[str, Decimal] = {}
            
            with SessionLocal() as db:
                # Query all positions for this tenant/exchange
                local_positions = db.query(PositionModel).filter(
                    and_(
                        PositionModel.tenant_id == tenant_id,
                        PositionModel.exchange_id == exchange_id
                    )
                ).all()
                
                for pos in local_positions:
                    symbol = pos.symbol
                    size = Decimal(str(pos.size))
                    positions[symbol] = size
            
            return positions
        
        except Exception as e:
            logger.error(f"STEP 4: Failed to fetch local positions: {e}")
            raise
    
    async def _get_exchange_positions(
        self,
        tenant_id: str,
        exchange_id: str
    ) -> Dict[str, Decimal]:
        """
        Fetch real per-currency holdings from the exchange.

        RT-16. TWO BUGS FIXED HERE; A THIRD DELIBERATELY LEFT OPEN -- see
        `_get_active_tenant_exchanges`'s own docstring for why this method has no
        caller yet.

        Fixed:
          1. `get_or_create_exchange(user_id=..., exchange_id=...)` built a connection
             with NO credentials (see its own docstring, which names this file as a known
             credential-less caller). Replaced with the same vault-based, per-tenant
             executor RT-21's manual cancel and RT-16's watchdog fix both use
             (`order_control._executor_for` -> `APIKeyVault.load_decrypted_keys` ->
             `get_exchange_executor` -> `.connect()`), so there is one place that builds
             an executor from stored keys, not three that could drift.
          2. `exchange.fetch_positions()` is a derivatives/margin call -- "position" on
             this spot-only platform (see RT-21's own finding: exposure is a plain,
             always-non-negative coin balance, never a long/short contract) has no
             `contracts`/`side` to read, so this always returned `[]` silently, which
             looked exactly like "no positions" instead of "wrong question". Replaced
             with the executor's own `get_balance()` (wraps `fetch_balance()`), and this
             returns real non-zero holdings keyed by CURRENCY CODE (e.g. "BTC"), which is
             what a spot account's "position" actually is.

        NOT fixed, and not safe to improvise here: the currency code this returns ("BTC")
        is not the market-pair symbol `PositionModel.symbol` stores ("BTC/USDT" or
        similar) -- a currency can be held against more than one quote, so there is no
        general, non-fabricated way to turn a balance into "the" symbol a position row
        would use. Comparing these two shapes meaningfully is a modelling decision for
        whoever does the PositionModel work this docstring's sibling describes, not
        something to guess at inside a position-fetch helper.
        """
        try:
            from backend_app.backend.order_control import _executor_for
            from backend_app.core.state import app_state

            vault = getattr(app_state, "vault", None)
            if vault is None:
                raise RuntimeError("account vault unavailable")

            executor = await _executor_for(str(tenant_id), str(exchange_id), vault)
            balances = await executor.get_balance()

            positions: Dict[str, Decimal] = {}
            for currency, amounts in balances.items():
                total = Decimal(str(amounts.get("total", "0")))
                if total != 0:
                    positions[currency] = total

            return positions

        except Exception as e:
            logger.error(f"STEP 4: Failed to fetch exchange positions: {e}")
            raise
    
    async def _get_active_tenant_exchanges(self) -> List[Tuple[str, str]]:
        """
        Get list of active tenant/exchange pairs to check.

        DELIBERATELY STILL EMPTY. RT-16 session note, not an oversight.
        ---------------------------------------------------------------
        `_get_exchange_positions` and `_handle_mismatch` were both fixed this session
        (vault-based real credentials + the correct spot balance call; mismatches now
        recorded through `ReconciliationMismatchRepository` at MEDIUM instead of
        unconditionally activating the GLOBAL kill switch). Enumerating real tenant/
        exchange pairs here anyway would still not be safe to turn on, because the LOCAL
        side of the comparison this feeds is not real:

        1. `_get_local_positions` reads `PositionModel` -- and nothing in the live or
           paper trading execution path ever writes a `PositionModel` row.
           `backend/position_engine.py`'s `PositionEngine` is fully built but has no
           caller anywhere (`grep -rn "PositionEngine(" backend_app/` finds only its own
           file). The table is permanently empty for every real tenant today.
        2. Even once something populates it, `PositionModel.side` is `long`/`short` --
           a derivatives model. This platform's actual exposure model (established by
           RT-21's own investigation of `BotRunner.recovered_exposure_coins`) is spot-
           only: a plain, always-non-negative coin balance, never a short. Mapping a
           currency balance onto a long/short position row is a real design decision,
           not a default to improvise inside this file.

        Turning this on today, with (1) unfixed, would mean every tenant holding any
        exchange balance registers as MISSING_LOCAL on every single check -- forever,
        for every tenant, every CHECK_INTERVAL_SECONDS. With this session's severity fix
        that no longer halts the platform, but it would permanently flood the
        `reconciliation_mismatches` table RT-16 just gave OrderWatchdog a real, useful
        feed into, burying genuine order-level signal under guaranteed structural noise.
        Enabling this for real needs PositionModel actually wired into the live order
        path first (a separate, nontrivial change) and a deliberate decision on (2) --
        both out of scope for this pass.

        Returns:
            List of (tenant_id, exchange_id) tuples
        """
        return []

    async def _handle_mismatch(self, result: ConsistencyCheckResult):
        """
        Record detected position mismatches for ReconciliationScheduler to escalate.

        RT-16. THIS NO LONGER CALLS THE KILL SWITCH DIRECTLY.
        -------------------------------------------------------
        Previously this activated the GLOBAL kill switch (platform-wide, every tenant,
        every process -- see `GlobalKillSwitch.activate`'s own docstring) unconditionally
        on ANY mismatch, with no severity distinction at all -- unlike
        `ReconciliationScheduler._escalate`, which already (correctly) reserves the kill
        switch for CRITICAL and only alerts for HIGH/MEDIUM. A platform with a real,
        working consistency checker but this handler wired up would have halted on its
        first false positive.

        Now this writes each mismatch through the SAME `ReconciliationMismatchRepository`
        RT-16 gave `OrderWatchdog` a real caller for, at severity MEDIUM -- never
        CRITICAL from this call site. This detector has zero production verification and,
        per `_get_active_tenant_exchanges`'s own docstring, is not even wired to real
        tenant data yet; a component in that state has no basis to claim the CRITICAL
        severity that is this platform's OWN definition for "trip the kill switch; this
        is real", and must not get a second, unreviewed path to the same halt that
        bypasses the scheduler's existing, correct severity gate. A record failure is
        logged and never raises -- recording is additive visibility, not a new control.
        """
        try:
            mismatch_details = "\n".join([
                f"  {m.symbol}: local={m.local_size}, exchange={m.exchange_size}, "
                f"diff={m.difference}, status={m.status.value}"
                for m in result.mismatches
            ])

            logger.warning(
                f"STEP 4: Position mismatch detected for "
                f"{result.tenant_id}:{result.exchange_id}\n"
                f"    Timestamp: {result.timestamp.isoformat()}\n"
                f"    Mismatches:\n{mismatch_details}"
            )

            await self._record_position_mismatches(result)

        except Exception as e:
            logger.error(
                f"STEP 4: Failed to handle position mismatch: {e}", exc_info=True
            )

    async def _record_position_mismatches(self, result: ConsistencyCheckResult) -> None:
        """Write each position mismatch to the shared reconciliation table.

        Severity is always MEDIUM from this call site -- see `_handle_mismatch`'s own
        docstring for why. A write failure is logged and never raises.
        """
        try:
            from backend_app.core.database import SessionLocal
            from backend_app.core.models.reconciliation import (
                MismatchSeverity, ReconciliationMismatchCreate,
                ReconciliationMismatchRepository)

            with SessionLocal() as db:
                repo = ReconciliationMismatchRepository(db)
                for m in result.mismatches:
                    repo.record_mismatch(ReconciliationMismatchCreate(
                        tenant_id=result.tenant_id,
                        execution_id=(
                            f"position:{result.tenant_id}:{result.exchange_id}:{m.symbol}"
                        ),
                        symbol=m.symbol,
                        side="unknown",
                        field="position_size",
                        local_value=str(m.local_size),
                        exchange_value=str(m.exchange_size),
                        severity=MismatchSeverity.MEDIUM,
                        raw_local_state={"size": str(m.local_size), "status": m.status.value},
                        raw_exchange_state={"size": str(m.exchange_size), "status": m.status.value},
                    ))
        except Exception as e:
            logger.error(
                f"STEP 4: could not record position mismatch(es) for "
                f"{result.tenant_id}:{result.exchange_id}: {e}"
            )
    
    def get_stats(self) -> dict:
        """Get consistency checker statistics."""
        return {
            "checks_run": self._check_count,
            "mismatches_detected": self._mismatch_count,
            "last_check_times": {
                k: v.isoformat() for k, v in self._last_check_time.items()
            }
        }


# Global singleton instance
_consistency_checker: Optional[PositionConsistencyChecker] = None


def get_consistency_checker() -> PositionConsistencyChecker:
    """Get or create the position consistency checker instance."""
    global _consistency_checker
    if _consistency_checker is None:
        _consistency_checker = PositionConsistencyChecker()
    return _consistency_checker


# Convenience exports
__all__ = [
    "PositionConsistencyChecker",
    "ConsistencyCheckResult",
    "PositionComparison",
    "PositionStatus",
    "get_consistency_checker",
]
