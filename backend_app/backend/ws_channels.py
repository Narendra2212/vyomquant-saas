"""
WebSocket Channel Constants

Single source of truth for WebSocket event channels.
Aligned with frontend wsChannels.js
"""

from enum import Enum
from typing import Any, Dict, Set


class ChannelType(str, Enum):
    """
    WebSocket streaming channels.
    
    These MUST match the frontend WS_CHANNELS exactly.
    """
    BOT_STATUS = "bot_status"
    SIGNAL_TRACE = "signal_trace"
    EXECUTION_EVENTS = "execution_events"
    RISK_EVENTS = "risk_events"
    DEPLOYMENT_EVENTS = "deployment_events"
    INFRASTRUCTURE = "infrastructure"


# Valid channel set for runtime validation
VALID_CHANNELS: Set[str] = {ch.value for ch in ChannelType}


def is_valid_channel(channel: str) -> bool:
    """
    Validate a channel name.
    
    Args:
        channel: Channel to validate
        
    Returns:
        True if valid
    """
    return channel in VALID_CHANNELS


def assert_valid_channel(channel: str, context: str = "subscription") -> None:
    """
    Assert channel is valid, raise if not.
    
    Args:
        channel: Channel to validate
        context: Context for error message
        
    Raises:
        ValueError: If channel is invalid
    """
    if not is_valid_channel(channel):
        valid_list = ", ".join(sorted(VALID_CHANNELS))
        raise ValueError(
            f"Invalid WebSocket channel {context}: '{channel}'. "
            f"Valid channels: {valid_list}"
        )


class EventType(str, Enum):
    """Event types per channel."""
    
    # Bot Status
    BOT_HEALTH = "bot_health"
    BOT_CONNECTED = "bot_connected"
    BOT_DISCONNECTED = "bot_disconnected"
    BOT_ERROR = "bot_error"
    HEARTBEAT = "heartbeat"
    
    # Signal Trace
    SIGNAL_RECEIVED = "signal_received"
    SIGNAL_VALIDATED = "signal_validated"
    SIGNAL_RISK_CHECKED = "signal_risk_checked"
    SIGNAL_EXECUTED = "signal_executed"
    SIGNAL_REJECTED = "signal_rejected"
    SIGNAL_FAILED = "signal_failed"
    
    # Execution Events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIAL = "order_partial"
    ORDER_REJECTED = "order_rejected"
    ORDER_ERROR = "order_error"
    
    # Risk Events
    RISK_BLOCK = "risk_block"
    RISK_WARNING = "risk_warning"
    KILL_SWITCH = "kill_switch"
    POSITION_LIMIT = "position_limit"
    DRAWDOWN_ALERT = "drawdown_alert"
    
    # Deployment Events
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILED = "deploy_failed"
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"


# Event types organized by channel
CHANNEL_EVENTS: Dict[ChannelType, Dict[str, str]] = {
    ChannelType.BOT_STATUS: {
        "BOT_HEALTH": EventType.BOT_HEALTH.value,
        "BOT_CONNECTED": EventType.BOT_CONNECTED.value,
        "BOT_DISCONNECTED": EventType.BOT_DISCONNECTED.value,
        "BOT_ERROR": EventType.BOT_ERROR.value,
        "HEARTBEAT": EventType.HEARTBEAT.value
    },
    ChannelType.SIGNAL_TRACE: {
        "SIGNAL_RECEIVED": EventType.SIGNAL_RECEIVED.value,
        "SIGNAL_VALIDATED": EventType.SIGNAL_VALIDATED.value,
        "SIGNAL_RISK_CHECKED": EventType.SIGNAL_RISK_CHECKED.value,
        "SIGNAL_EXECUTED": EventType.SIGNAL_EXECUTED.value,
        "SIGNAL_REJECTED": EventType.SIGNAL_REJECTED.value,
        "SIGNAL_FAILED": EventType.SIGNAL_FAILED.value
    },
    ChannelType.EXECUTION_EVENTS: {
        "ORDER_SUBMITTED": EventType.ORDER_SUBMITTED.value,
        "ORDER_FILLED": EventType.ORDER_FILLED.value,
        "ORDER_PARTIAL": EventType.ORDER_PARTIAL.value,
        "ORDER_REJECTED": EventType.ORDER_REJECTED.value,
        "ORDER_ERROR": EventType.ORDER_ERROR.value
    },
    ChannelType.RISK_EVENTS: {
        "RISK_BLOCK": EventType.RISK_BLOCK.value,
        "RISK_WARNING": EventType.RISK_WARNING.value,
        "KILL_SWITCH": EventType.KILL_SWITCH.value,
        "POSITION_LIMIT": EventType.POSITION_LIMIT.value,
        "DRAWDOWN_ALERT": EventType.DRAWDOWN_ALERT.value
    },
    ChannelType.DEPLOYMENT_EVENTS: {
        "DEPLOY_STARTED": EventType.DEPLOY_STARTED.value,
        "DEPLOY_SUCCESS": EventType.DEPLOY_SUCCESS.value,
        "DEPLOY_FAILED": EventType.DEPLOY_FAILED.value,
        "BOT_STARTED": EventType.BOT_STARTED.value,
        "BOT_STOPPED": EventType.BOT_STOPPED.value
    }
}


# Standard WebSocket message schema
WS_MESSAGE_SCHEMA = {
    "type": "string",           # Event type
    "channel": "string",        # Channel name
    "timestamp": "string",      # ISO 8601 timestamp
    "bot_id": "string|null",    # Associated bot ID
    "strategy_id": "string|null", # Associated strategy ID
    "tenant_id": "string",      # Tenant identifier
    "message_id": "string",     # Unique message ID
    "payload": "object"         # Event-specific data
}


def create_ws_message(
    event_type: EventType,
    channel: ChannelType,
    tenant_id: str,
    bot_id: str = None,
    strategy_id: str = None,
    payload: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Create a standard WebSocket message.
    
    Args:
        event_type: Type of event
        channel: Target channel
        tenant_id: Tenant identifier
        bot_id: Associated bot ID
        strategy_id: Associated strategy ID
        payload: Event-specific data
        
    Returns:
        Standardized message dict
    """
    import uuid
    from datetime import datetime, timezone
    
    return {
        "type": event_type.value,
        "channel": channel.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_id": bot_id,
        "strategy_id": strategy_id,
        "tenant_id": tenant_id,
        "message_id": str(uuid.uuid4()),
        "payload": payload or {}
    }


# Exports
__all__ = [
    "ChannelType",
    "EventType",
    "VALID_CHANNELS",
    "is_valid_channel",
    "assert_valid_channel",
    "CHANNEL_EVENTS",
    "create_ws_message"
]
