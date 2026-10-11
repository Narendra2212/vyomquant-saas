"""
Idempotent Exchange Submission - Phase 6 Correctness Fix

This module implements idempotent exchange submission to prevent duplicate order
submission and retry duplication. This is a critical correctness fix for the
strict algo trading platform.

Key Features:
- Client order ID generation for idempotency
- Duplicate order detection
- Idempotent retry mechanism
- Order state persistence before submission
- Atomic order submission with journaling

Author: Principal Institutional Algo Execution Validation Engineer
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .immutable_journal import immutable_journal

logger = logging.getLogger("idempotent_exchange_submission")


class IdempotencyUnavailable(RuntimeError):
    """The idempotency store could not be consulted, so no order may be submitted.

    Raised instead of returning a verdict, because a verdict can be ignored by a caller
    and the one being ignored here is "I cannot tell you whether this order already
    exists". Callers convert it into a refusal with ``error="IDEMPOTENCY_UNAVAILABLE"``;
    nothing reaches a venue on this path.
    """


class IdempotentExchangeSubmission:
    """
    Idempotent exchange submission manager.
    
    Prevents duplicate order submission and retry duplication by using
    client order IDs and duplicate detection.
    """
    
    def __init__(self):
        """Initialize idempotent exchange submission manager."""
        self.redis = redis_manager
        self.journal = immutable_journal
        
        # Configuration
        self.client_order_id_ttl = 86400  # 24 hours
        self.submission_lock_ttl = 300  # 5 minutes
    
    async def generate_client_order_id(
        self,
        tenant_id: str,
        strategy_id: str,
        signal_id: str,
        timestamp: datetime
    ) -> str:
        """
        Generate a deterministic client order ID for idempotency.
        
        Args:
            tenant_id: Tenant ID
            strategy_id: Strategy ID
            signal_id: Signal ID
            timestamp: Signal timestamp
            
        Returns:
            Client order ID string
        """
        # Create deterministic client order ID
        client_order_id = f"{tenant_id}:{strategy_id}:{signal_id}:{timestamp.isoformat()}"
        
        # Hash for consistency
        client_order_hash = hashlib.sha256(client_order_id.encode()).hexdigest()[:16]
        
        return f"client_{client_order_hash}"
    
    async def check_duplicate_submission(
        self,
        client_order_id: str,
        tenant_id: str
    ) -> bool:
        """Whether this client order id has already been submitted.

        Returns:
            ``True`` if a prior submission is recorded.

        Raises:
            IdempotencyUnavailable: If the store could not be consulted. **This used to
                return ``False``** — "not a duplicate" — under the comment "Fail safe:
                assume not duplicate to prevent blocking". That is fail-OPEN on the one
                question this module exists to answer, and it is backwards: the thing
                being prevented is a second real order against a live venue, so the only
                safe answer to "I do not know whether this was already submitted" is to
                refuse. A Redis outage otherwise converts every retry into a duplicate
                order, which is exactly the failure mode the module is named after.

                Not blocking is not a virtue here. An order that was recorded but never
                submitted is recoverable by a human; an unintended second position is
                not.

        Note:
            :meth:`claim_submission` is the preferred entry point — this check followed
            by a separate write is not atomic, so two pods can both observe "not a
            duplicate" and both proceed. This method is retained because it is a
            meaningful question on its own (status endpoints ask it), but the submission
            path no longer uses it as a gate.
        """
        key = f"client_order_id:{tenant_id}:{client_order_id}"
        try:
            exists = await self.redis.exists(key)
        except Exception as e:
            logger.error(
                "Duplicate check for %s could not be completed: %s. Refusing rather "
                "than assuming the order is new.",
                client_order_id,
                e,
            )
            raise IdempotencyUnavailable(
                f"the idempotency store could not be consulted for {client_order_id}"
            ) from e

        if exists:
            logger.warning(f"Duplicate submission detected: {client_order_id}")
            return True

        return False

    async def claim_submission(
        self,
        client_order_id: str,
        tenant_id: str,
    ) -> bool:
        """Atomically claim the right to submit ``client_order_id`` exactly once.

        One ``SET key value NX EX ttl`` round trip. Returns ``True`` to exactly one
        caller; every other caller for the same id gets ``False`` while the key lives.

        WHY THIS REPLACES check-then-record. The previous sequence was::

            is_duplicate = await self.check_duplicate_submission(...)   # read
            if is_duplicate: return ...
            recorded = await self.record_submission(...)                # write

        Between the read and the write there is a window in which a second worker runs
        the same read. Both observe "not a duplicate", both write, both submit. With N
        pods consuming one signal queue that window is reached routinely rather than
        rarely, and the result is N real orders for one signal. ``NX`` closes it because
        the test and the set are one operation at the store.

        Raises:
            IdempotencyUnavailable: If the claim could not be attempted. Fails closed,
                for the reason given on :meth:`check_duplicate_submission`.
        """
        key = f"client_order_id:{tenant_id}:{client_order_id}"
        try:
            claimed = await self.redis.set(
                key,
                "submitted",
                ex=self.client_order_id_ttl,
                nx=True,
            )
        except Exception as e:
            logger.error(
                "Submission claim for %s could not be attempted: %s. Refusing rather "
                "than submitting an order that may already exist.",
                client_order_id,
                e,
            )
            raise IdempotencyUnavailable(
                f"the idempotency store could not be reached to claim {client_order_id}"
            ) from e

        # A `SET ... NX` that loses returns a falsey value (None on redis-py, False on
        # the in-memory stand-in). Both mean "somebody else holds this id".
        if not claimed:
            logger.warning(f"Duplicate submission blocked: {client_order_id}")
            return False

        return True
    
    async def record_submission(
        self,
        client_order_id: str,
        tenant_id: str,
        order_data: Dict[str, Any]
    ) -> bool:
        """
        Record order submission for idempotency.
        
        Args:
            client_order_id: Client order ID
            tenant_id: Tenant ID
            order_data: Order data
            
        Returns:
            True if recorded successfully, False otherwise
        """
        try:
            # Store in Redis
            key = f"client_order_id:{tenant_id}:{client_order_id}"
            await self.redis.setex(
                key,
                self.client_order_id_ttl,
                "submitted"
            )
            
            # Store order data
            data_key = f"client_order_data:{tenant_id}:{client_order_id}"
            await self.redis.setex(
                data_key,
                self.client_order_id_ttl,
                str(order_data)
            )
            
            logger.info(f"Submission recorded: {client_order_id}")
            return True
            
        except Exception as e:
            logger.error(f"Submission recording failed: {e}")
            return False
    
    async def submit_order_idempotent(
        self,
        tenant_id: str,
        strategy_id: str,
        signal_id: str,
        timestamp: datetime,
        order_data: Dict[str, Any],
        exchange_client: Any
    ) -> Dict[str, Any]:
        """
        Submit order idempotently.
        
        Args:
            tenant_id: Tenant ID
            strategy_id: Strategy ID
            signal_id: Signal ID
            timestamp: Signal timestamp
            order_data: Order data
            exchange_client: Exchange client
            
        Returns:
            Submission result
        """
        try:
            # Generate client order ID
            client_order_id = await self.generate_client_order_id(
                tenant_id,
                strategy_id,
                signal_id,
                timestamp
            )
            
            # ATOMIC CLAIM, NOT CHECK-THEN-WRITE.
            #
            # One `SET NX` decides, for all pods at once, who may submit this
            # client_order_id. The previous code read "is it a duplicate?" and then wrote
            # the record in a second round trip, so two workers consuming the same signal
            # could both read "new" and both submit. See `claim_submission`.
            #
            # A store that cannot be reached raises IdempotencyUnavailable and is handled
            # below as a refusal — never as "probably new".
            try:
                claimed = await self.claim_submission(client_order_id, tenant_id)
            except IdempotencyUnavailable as e:
                return {
                    "success": False,
                    "error": "IDEMPOTENCY_UNAVAILABLE",
                    "client_order_id": client_order_id,
                    "message": (
                        "The idempotency store could not be reached, so the order was "
                        "NOT submitted. Nothing was sent to the exchange. "
                        f"({e})"
                    ),
                }

            if not claimed:
                return {
                    "success": False,
                    "error": "duplicate_submission",
                    "client_order_id": client_order_id,
                    "message": "Order already submitted with this client order ID"
                }

            # Add client order ID to order data
            order_data["client_order_id"] = client_order_id

            # The claim above already persisted the submission marker atomically. This
            # records the order PAYLOAD alongside it for later status lookups; it is
            # bookkeeping, not the idempotency gate, so a failure here does not release
            # the claim and does not permit a second submission.
            try:
                await self.record_submission(client_order_id, tenant_id, order_data)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Submission payload for %s was not recorded (%s); the claim stands "
                    "and the order proceeds exactly once.",
                    client_order_id,
                    e,
                )
            
            # Submit to exchange via Canonical ExecutionEngine Gateway
            try:
                from backend_app.backend.execution_environment import \
                    ExecutionEnvironment
                from backend_app.core.execution_engine import ExecutionEngine
                from decimal import Decimal
                from uuid import UUID

                parsed_tenant = UUID(str(tenant_id)) if isinstance(tenant_id, (str, UUID)) else tenant_id
                # The submitter portfolio state, never an invented one. This used to
                # be a hardcoded {total_equity: Decimal(100000.0)} on the path the
                # comment above calls Submit to exchange, so the RiskManager equity
                # every guardrail is measured against was 100k that nobody read.
                # Refusing is the safe direction here: an order recorded for
                # idempotency but never submitted is recoverable, whereas one sized
                # against imaginary capital is not.
                portfolio_state = order_data.get("portfolio_state")
                if not portfolio_state or "total_equity" not in portfolio_state:
                    logger.error(
                        "Refusing submission %s: order_data carries no "
                        "portfolio_state with total_equity, and this gateway does "
                        "not invent capital to size against.",
                        client_order_id,
                    )
                    return {
                        "success": False,
                        "error": "PORTFOLIO_STATE_REQUIRED",
                        "client_order_id": client_order_id,
                        "message": (
                            "No portfolio state with total_equity was supplied, so "
                            "the order was not submitted. Nothing was sent to the "
                            "exchange."
                        ),
                    }
                # ═══════════════════════════════════════════════════════════
                # THIS PATH IS SEALED, AND IT HAS NEVER HAD A CALLER
                # ═══════════════════════════════════════════════════════════
                # `submit_order_idempotent` builds an `ExecutionEngine` declared LIVE and
                # calls `execute_trade`, which hardcodes `source="bot_runner"` — the
                # gateway's authorisation check. It therefore inherits the bot runner's
                # permission to place live orders WITHOUT inheriting the risk evaluation
                # that accompanies it: `BotRunner._inner_loop` calls
                # `risk_manager.validate_trade_request` before its own gateway call, and
                # there is no risk call anywhere in this function. The pre-launch audit
                # confirmed by grep that nothing in `backend_app/` calls this method, so
                # the bypass is latent rather than live.
                #
                # Sealing it is the correct action for a launch: a path that can place an
                # unrisk-checked live order and has no users is pure downside. Wiring it
                # up correctly means routing it through the same risk evaluation the bot
                # runner performs, which is a design change, not a patch — and doing that
                # under launch pressure to re-enable something nobody calls would be the
                # wrong trade.
                #
                # The idempotency machinery above (deterministic client_order_id, atomic
                # SET NX claim, fail-closed store errors) is deliberately left intact and
                # is independently tested: it is the reusable part, and the claim is taken
                # before this refusal so the semantics of a retry are unchanged.
                logger.error(
                    "Refusing submission %s: this gateway performs no risk evaluation "
                    "and must not place a live order. Route the intent through "
                    "BotRunner, which evaluates risk before calling the execution "
                    "gateway.",
                    client_order_id,
                )
                return {
                    "success": False,
                    "error": "RISK_EVALUATION_REQUIRED",
                    "client_order_id": client_order_id,
                    "exchange_order_id": None,
                    "message": (
                        "This submission path does not evaluate risk and is disabled for "
                        "live orders. Nothing was sent to the exchange."
                    ),
                }

                # Unreachable, retained to document the shape a risk-aware
                # reinstatement would restore.
                engine = ExecutionEngine(  # pragma: no cover
                    portfolio_state=portfolio_state,
                    execution_environment=ExecutionEnvironment.LIVE,
                )

                res = await engine.execute_trade(
                    tenant_id=parsed_tenant,
                    strategy_id=strategy_id or "idempotent_submission",
                    symbol=order_data.get("symbol", "BTC/USDT"),
                    side=order_data.get("side", "buy"),
                    size=Decimal(str(order_data.get("quantity", "0.01"))),
                    price=Decimal(str(order_data.get("price", "0"))),
                    metadata={"client_order_id": client_order_id, "signal_id": signal_id}
                )

                # THE VENUE'S ORDER ID, OR NONE.
                #
                # This was `res.execution_id or f"exchange_{uuid.uuid4().hex[:16]}"`.
                # Neither half of that is an exchange order id: `execution_id` is this
                # system's own deterministic idempotency key, and the fallback is a
                # random hex string generated here. A caller reconciling against the
                # venue, or cancelling by id, had nothing usable and no way to tell.
                #
                # The real id is on the fill. `_execute_trade_internal` puts the
                # exchange's `exchange_order_id` in `order_id` on its live branch, and
                # `execute_with_idempotency` nests that dict under `trade_result`.
                # Regression: tests/test_execution_paths_that_claim_an_exchange.py
                fill = (res.details or {}).get("trade_result") or {}
                venue_order_id = fill.get("order_id") if res.success else None

                if not res.success or not venue_order_id:
                    logger.error(
                        "Submission %s did not reach an exchange: %s. Reporting a "
                        "failure with no exchange_order_id rather than minting one.",
                        client_order_id,
                        res.message or res.status,
                    )
                    return {
                        "success": False,
                        "error": "exchange_submission_failed",
                        "client_order_id": client_order_id,
                        "exchange_order_id": None,
                        "message": (
                            res.message
                            or res.status
                            or "The order was not submitted to an exchange."
                        ),
                        "details": res.details or {},
                    }

                result = {
                    "success": True,
                    "client_order_id": client_order_id,
                    "exchange_order_id": venue_order_id,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "details": res.details or {}
                }

                logger.info(f"Order submitted idempotently via Gateway: {client_order_id} -> {venue_order_id}")
                return result
                
            except Exception as e:
                # AN AMBIGUOUS FAILURE KEEPS ITS CLAIM.
                #
                # This block used to call `self._remove_submission_record(...)` under the
                # comment "allow retry". But the exception that lands here includes the
                # case that matters most: the request reached the venue, the venue
                # accepted it, and the RESPONSE was lost to a timeout or a dropped
                # connection. Releasing the claim there means the retry places a SECOND
                # real order for one signal — the precise outcome this module exists to
                # prevent.
                #
                # An exception is not evidence that nothing happened. It is evidence that
                # we do not know what happened, and "unknown" must be resolved by
                # reconciling against the venue (reconciliation_engine queries the
                # exchange by order id), not by assuming the optimistic branch.
                #
                # The claim expires on its own after `client_order_id_ttl` (24 h), so this
                # does not strand the id permanently; it just stops an automatic retry
                # from doubling a position inside the window where ambiguity is live.
                logger.error(
                    "Exchange submission %s failed with %s: %s. The idempotency claim "
                    "is RETAINED because the order may have reached the venue; resolve "
                    "by reconciliation rather than by retrying.",
                    client_order_id,
                    type(e).__name__,
                    e,
                )

                return {
                    "success": False,
                    "requires_reconciliation": True,
                    "error": "exchange_submission_failed",
                    "client_order_id": client_order_id,
                    "message": str(e)
                }
            
        except Exception as e:
            logger.error(f"Idempotent submission failed: {e}")
            return {
                "success": False,
                "error": "idempotent_submission_failed",
                "message": str(e)
            }
    
    async def _remove_submission_record(self, client_order_id: str, tenant_id: str):
        """Remove submission record on failure."""
        try:
            key = f"client_order_id:{tenant_id}:{client_order_id}"
            await self.redis.delete(key)
            
            data_key = f"client_order_data:{tenant_id}:{client_order_id}"
            await self.redis.delete(data_key)
            
        except Exception as e:
            logger.error(f"Failed to remove submission record: {e}")
    
    async def get_submission_status(
        self,
        client_order_id: str,
        tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get submission status for client order ID.
        
        Args:
            client_order_id: Client order ID
            tenant_id: Tenant ID
            
        Returns:
            Submission status or None if not found
        """
        try:
            key = f"client_order_id:{tenant_id}:{client_order_id}"
            exists = await self.redis.exists(key)
            
            if not exists:
                return None
            
            data_key = f"client_order_data:{tenant_id}:{client_order_id}"
            data = await self.redis.get(data_key)
            
            return {
                "client_order_id": client_order_id,
                "status": "submitted",
                "data": data
            }
            
        except Exception as e:
            logger.error(f"Failed to get submission status: {e}")
            return None


# Global instance
_idempotent_exchange_submission: Optional[IdempotentExchangeSubmission] = None


def get_idempotent_exchange_submission() -> IdempotentExchangeSubmission:
    """Get or create idempotent exchange submission instance."""
    global _idempotent_exchange_submission
    if _idempotent_exchange_submission is None:
        _idempotent_exchange_submission = IdempotentExchangeSubmission()
    return _idempotent_exchange_submission
