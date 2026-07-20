"""Redis Stream helpers for bot and order command flow."""

from __future__ import annotations

import json
from typing import Any, Dict

from backend_app.core.cache import redis_manager

COMMAND_STREAM = "command_queue"
MARKET_STREAM = "market_data"
STRATEGY_STREAM = "strategy_signal"
RISK_STREAM = "risk_signal"
EXECUTION_STREAM = "execution_signal"


async def publish(stream: str, payload: Dict[str, Any]):
    return await redis_manager.xadd(stream, payload)


async def publish_command(action: str, payload: Dict[str, Any]):
    data = {"action": action, "payload": json.dumps(payload, default=str)}
    return await publish(COMMAND_STREAM, data)
