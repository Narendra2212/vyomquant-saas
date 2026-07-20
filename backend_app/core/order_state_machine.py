"""
Order State Machine

STEP 3.1 — ORDER CONSISTENCY + EXCHANGE RECONCILIATION

This module defines the complete order lifecycle state machine
with validated transitions and comprehensive logging.

State Diagram:
┌─────────┐     ┌───────────┐     ┌─────────┐     ┌─────────────────┐
│ CREATED │────▶│ SUBMITTED │────▶│ PENDING │────▶│ PARTIALLY_FILLED│
└─────────┘     └───────────┘     └─────────┘     └─────────────────┘
                                                           │
                                                           ▼
                                                    ┌─────────┐
                                                    │  FILLED │
                                                    └─────────┘

Terminal States: FILLED, FAILED, CANCELLED, REJECTED

Any state can transition to: FAILED, CANCELLED, REJECTED
"""

from enum import Enum, auto
from typing import Dict, Set, Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class OrderState(Enum):
    """
    Complete order lifecycle states.
    
    States:
        CREATED: Order created in system, not yet sent to exchange
        SUBMITTED: Order sent to exchange, awaiting acknowledgment
        PENDING: Order acknowledged by exchange, awaiting fill
        PARTIALLY_FILLED: Order partially executed, remaining quantity open
        FILLED: Order completely executed
        FAILED: Order failed (network, validation, etc.)
        CANCELLED: Order cancelled by user or system
        REJECTED: Order rejected by exchange
    """
    CREATED = "created"
    SUBMITTED = "submitted"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    
    @property
    def is_terminal(self) -> bool:
        """Check if state is terminal (no further transitions allowed)."""
        return self in {
            OrderState.FILLED,
            OrderState.FAILED,
            OrderState.CANCELLED,
            OrderState.REJECTED
        }
    
    @property
    def is_active(self) -> bool:
        """Check if order is still active on exchange."""
        return self in {
            OrderState.SUBMITTED,
            OrderState.PENDING,
            OrderState.PARTIALLY_FILLED
        }


@dataclass
class StateTransition:
    """Record of a state transition."""
    execution_id: str
    from_state: OrderState
    to_state: OrderState
    timestamp: datetime
    reason: Optional[str] = None
    metadata: Optional[Dict] = None
    
    def __str__(self) -> str:
        return f"{self.execution_id} | {self.from_state.value} → {self.to_state.value}"


class InvalidStateTransitionError(Exception):
    """Raised when attempting an invalid state transition."""
    pass


class OrderStateMachine:
    """
    State machine for order lifecycle management.
    
    Ensures:
    1. Only valid state transitions are allowed
    2. All transitions are logged
    3. Terminal states are enforced
    4. Transition hooks can be registered
    
    Usage:
        machine = OrderStateMachine()
        
        # Transition with validation
        machine.transition(
            execution_id="exec_abc123",
            from_state=OrderState.CREATED,
            to_state=OrderState.SUBMITTED,
            reason="Order sent to exchange"
        )
        
        # Check if transition is valid
        is_valid = machine.can_transition(
            OrderState.PENDING,
            OrderState.FILLED
        )  # True
    """
    
    # Valid state transitions
    VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
        OrderState.CREATED: {
            OrderState.SUBMITTED,
            OrderState.FAILED,
            OrderState.CANCELLED
        },
        OrderState.SUBMITTED: {
            OrderState.PENDING,
            OrderState.FILLED,  # Instant fill
            OrderState.PARTIALLY_FILLED,
            OrderState.FAILED,
            OrderState.REJECTED,
            OrderState.CANCELLED
        },
        OrderState.PENDING: {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.FAILED,
            OrderState.REJECTED
        },
        OrderState.PARTIALLY_FILLED: {
            OrderState.FILLED,
            OrderState.CANCELLED,  # Cancel remaining
            OrderState.FAILED
        },
        # Terminal states - no outgoing transitions
        OrderState.FILLED: set(),
        OrderState.FAILED: set(),
        OrderState.CANCELLED: set(),
        OrderState.REJECTED: set()
    }
    
    def __init__(self):
        """Initialize state machine with transition hooks."""
        self._transition_history: Dict[str, List[StateTransition]] = {}
        self._current_states: Dict[str, OrderState] = {}
        self._pre_transition_hooks: List[Callable] = []
        self._post_transition_hooks: List[Callable] = []
        
        logger.info("OrderStateMachine initialized")
    
    def can_transition(self, from_state: OrderState, to_state: OrderState) -> bool:
        """
        Check if a transition is valid.
        
        Args:
            from_state: Current state
            to_state: Desired next state
            
        Returns:
            True if transition is valid, False otherwise
        """
        # Terminal states cannot transition
        if from_state.is_terminal:
            return False
        
        # Check if to_state is in valid transitions for from_state
        valid_next_states = self.VALID_TRANSITIONS.get(from_state, set())
        return to_state in valid_next_states
    
    def transition(
        self,
        execution_id: str,
        from_state: OrderState,
        to_state: OrderState,
        reason: Optional[str] = None,
        metadata: Optional[Dict] = None,
        force: bool = False
    ) -> StateTransition:
        """
        Execute a state transition with validation.
        
        Args:
            execution_id: Unique execution identifier
            from_state: Current state (for validation)
            to_state: Target state
            reason: Human-readable reason for transition
            metadata: Additional data about the transition
            force: Skip validation (use with caution)
            
        Returns:
            StateTransition record
            
        Raises:
            InvalidStateTransitionError: If transition is invalid and not forced
            ValueError: If state mismatch detected
        """
        # Validate current state matches
        current_stored_state = self._current_states.get(execution_id)
        
        if current_stored_state is not None and current_stored_state != from_state:
            # State mismatch - check if we need to handle drift
            logger.warning(
                f"State mismatch for {execution_id}: "
                f"expected {from_state.value}, found {current_stored_state.value}"
            )
            # Use stored state as source of truth
            from_state = current_stored_state
        
        # Validate transition
        if not force and not self.can_transition(from_state, to_state):
            error_msg = (
                f"Invalid transition for {execution_id}: "
                f"{from_state.value} → {to_state.value}"
            )
            logger.error(error_msg)
            raise InvalidStateTransitionError(error_msg)
        
        # Execute pre-transition hooks
        for hook in self._pre_transition_hooks:
            try:
                hook(execution_id, from_state, to_state, metadata)
            except Exception as e:
                logger.error(f"Pre-transition hook failed: {e}")
        
        # Record transition
        transition_record = StateTransition(
            execution_id=execution_id,
            from_state=from_state,
            to_state=to_state,
            timestamp=datetime.utcnow(),
            reason=reason,
            metadata=metadata
        )
        
        # Update state
        self._current_states[execution_id] = to_state
        
        # Add to history
        if execution_id not in self._transition_history:
            self._transition_history[execution_id] = []
        self._transition_history[execution_id].append(transition_record)
        
        # STEP 3.9: Log all state transitions
        self._log_transition(transition_record)
        
        # Execute post-transition hooks
        for hook in self._post_transition_hooks:
            try:
                hook(execution_id, from_state, to_state, metadata)
            except Exception as e:
                logger.error(f"Post-transition hook failed: {e}")
        
        return transition_record
    
    def _log_transition(self, transition: StateTransition):
        """
        STEP 3.9: Log state transition with full context.
        
        Format: {execution_id} | {old_state} → {new_state} | {reason} | {timestamp}
        """
        log_message = (
            f"ORDER STATE TRANSITION | "
            f"{transition.execution_id} | "
            f"{transition.from_state.value} → {transition.to_state.value}"
        )
        
        if transition.reason:
            log_message += f" | Reason: {transition.reason}"
        
        # Log at appropriate level based on transition type
        if transition.to_state in (OrderState.FAILED, OrderState.REJECTED):
            logger.error(log_message)
        elif transition.to_state == OrderState.CANCELLED:
            logger.warning(log_message)
        elif transition.to_state == OrderState.FILLED:
            logger.info(f"✅ {log_message}")
        else:
            logger.info(log_message)
        
        # Also log to structured logging for analytics
        logger.debug(
            f"Transition details: execution_id={transition.execution_id}, "
            f"from={transition.from_state.value}, "
            f"to={transition.to_state.value}, "
            f"timestamp={transition.timestamp.isoformat()}, "
            f"metadata={transition.metadata}"
        )
    
    def get_current_state(self, execution_id: str) -> Optional[OrderState]:
        """Get current state for an execution."""
        return self._current_states.get(execution_id)
    
    def get_transition_history(self, execution_id: str) -> List[StateTransition]:
        """Get full transition history for an execution."""
        return self._transition_history.get(execution_id, [])
    
    def register_pre_transition_hook(self, hook: Callable):
        """Register a hook to run before transitions."""
        self._pre_transition_hooks.append(hook)
    
    def register_post_transition_hook(self, hook: Callable):
        """Register a hook to run after transitions."""
        self._post_transition_hooks.append(hook)
    
    def get_valid_next_states(self, state: OrderState) -> Set[OrderState]:
        """Get all valid next states from a given state."""
        return self.VALID_TRANSITIONS.get(state, set()).copy()
    
    def is_terminal_state(self, execution_id: str) -> bool:
        """Check if order is in terminal state."""
        state = self.get_current_state(execution_id)
        return state is not None and state.is_terminal
    
    def is_active(self, execution_id: str) -> bool:
        """Check if order is still active on exchange."""
        state = self.get_current_state(execution_id)
        return state is not None and state.is_active
    
    def reset(self, execution_id: str):
        """Reset state for an execution (use with caution)."""
        if execution_id in self._current_states:
            del self._current_states[execution_id]
        if execution_id in self._transition_history:
            del self._transition_history[execution_id]
        logger.warning(f"State reset for {execution_id}")


# Global state machine instance
_state_machine: Optional[OrderStateMachine] = None


def get_order_state_machine() -> OrderStateMachine:
    """Get or create global state machine instance."""
    global _state_machine
    if _state_machine is None:
        _state_machine = OrderStateMachine()
    return _state_machine


# Convenience functions for common transitions
def transition_created_to_submitted(
    execution_id: str,
    exchange_order_id: Optional[str] = None,
    reason: str = "Order submitted to exchange"
) -> StateTransition:
    """Transition from CREATED to SUBMITTED."""
    machine = get_order_state_machine()
    return machine.transition(
        execution_id=execution_id,
        from_state=OrderState.CREATED,
        to_state=OrderState.SUBMITTED,
        reason=reason,
        metadata={"exchange_order_id": exchange_order_id}
    )


def transition_submitted_to_pending(
    execution_id: str,
    exchange_order_id: str,
    reason: str = "Order acknowledged by exchange"
) -> StateTransition:
    """Transition from SUBMITTED to PENDING."""
    machine = get_order_state_machine()
    return machine.transition(
        execution_id=execution_id,
        from_state=OrderState.SUBMITTED,
        to_state=OrderState.PENDING,
        reason=reason,
        metadata={"exchange_order_id": exchange_order_id}
    )


def transition_to_filled(
    execution_id: str,
    filled_size: float,
    avg_price: float,
    exchange_order_id: str,
    reason: str = "Order completely filled"
) -> StateTransition:
    """Transition to FILLED state."""
    machine = get_order_state_machine()
    current_state = machine.get_current_state(execution_id)
    
    # Can transition from PENDING, PARTIALLY_FILLED, or SUBMITTED (instant fill)
    if current_state == OrderState.PARTIALLY_FILLED:
        from_state = OrderState.PARTIALLY_FILLED
    elif current_state == OrderState.PENDING:
        from_state = OrderState.PENDING
    elif current_state == OrderState.SUBMITTED:
        from_state = OrderState.SUBMITTED
    else:
        # Force transition from any state (reconciliation)
        from_state = current_state or OrderState.SUBMITTED
    
    return machine.transition(
        execution_id=execution_id,
        from_state=from_state,
        to_state=OrderState.FILLED,
        reason=reason,
        metadata={
            "filled_size": filled_size,
            "avg_price": avg_price,
            "exchange_order_id": exchange_order_id
        }
    )


def transition_to_partial_fill(
    execution_id: str,
    filled_size: float,
    remaining_size: float,
    avg_price: float,
    exchange_order_id: str,
    reason: str = "Order partially filled"
) -> StateTransition:
    """Transition to PARTIALLY_FILLED state."""
    machine = get_order_state_machine()
    current_state = machine.get_current_state(execution_id)
    
    # Can transition from PENDING or SUBMITTED
    if current_state == OrderState.PENDING:
        from_state = OrderState.PENDING
    elif current_state == OrderState.SUBMITTED:
        from_state = OrderState.SUBMITTED
    else:
        from_state = current_state or OrderState.SUBMITTED
    
    return machine.transition(
        execution_id=execution_id,
        from_state=from_state,
        to_state=OrderState.PARTIALLY_FILLED,
        reason=reason,
        metadata={
            "filled_size": filled_size,
            "remaining_size": remaining_size,
            "avg_price": avg_price,
            "exchange_order_id": exchange_order_id
        }
    )


def transition_to_failed(
    execution_id: str,
    error_message: str,
    error_code: Optional[str] = None,
    reason: str = "Order failed"
) -> StateTransition:
    """Transition to FAILED state (from any state)."""
    machine = get_order_state_machine()
    current_state = machine.get_current_state(execution_id) or OrderState.CREATED
    
    return machine.transition(
        execution_id=execution_id,
        from_state=current_state,
        to_state=OrderState.FAILED,
        reason=reason,
        metadata={
            "error_message": error_message,
            "error_code": error_code
        },
        force=True  # Can transition from any state to FAILED
    )


def transition_to_cancelled(
    execution_id: str,
    cancelled_by: str = "system",
    reason: str = "Order cancelled"
) -> StateTransition:
    """Transition to CANCELLED state."""
    machine = get_order_state_machine()
    current_state = machine.get_current_state(execution_id)
    
    if current_state is None:
        # Order not found, create cancelled record for reconciliation
        current_state = OrderState.CREATED
    
    return machine.transition(
        execution_id=execution_id,
        from_state=current_state,
        to_state=OrderState.CANCELLED,
        reason=reason,
        metadata={"cancelled_by": cancelled_by},
        force=True  # Can transition from any state to CANCELLED
    )
