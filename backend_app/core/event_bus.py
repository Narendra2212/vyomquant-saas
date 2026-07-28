"""Redis Stream helpers for bot and order command flow."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from backend_app.core.cache import redis_manager, PublishError

logger = logging.getLogger("EventBus")

COMMAND_STREAM    = "command_queue"
MARKET_STREAM     = "market_data"
STRATEGY_STREAM   = "strategy_signal"
RISK_STREAM       = "risk_signal"
EXECUTION_STREAM  = "execution_signal"

# ── Backtest job stream ────────────────────────────────────────────────────
# Non-trading-critical: user-initiated, can surface an HTTP 503 on failure.
BACKTEST_STREAM = "backtest_jobs"


async def publish(
    stream: str,
    payload: Dict[str, Any],
    max_retries: int = 3,
    raise_on_error: bool = True,
):
    """
    Publish a message payload to a Redis stream.
    Trading-critical streams will retry on failure and raise PublishError.
    """
    try:
        return await redis_manager.xadd(
            stream, payload, max_retries=max_retries, raise_on_error=raise_on_error
        )
    except PublishError as e:
        logger.error(f"EventBus publish failed for stream '{stream}': {e}")
        raise


async def publish_command(
    action: str,
    payload: Dict[str, Any],
    max_retries: int = 3,
    raise_on_error: bool = True,
):
    """
    Publish an operational command to the command_queue stream.
    Raises PublishError if the command cannot be delivered to Redis.
    """
    data = {"action": action, "payload": json.dumps(payload, default=str)}
    return await publish(
        COMMAND_STREAM, data, max_retries=max_retries, raise_on_error=raise_on_error
    )


async def publish_backtest_job(job_id: str, payload: Dict[str, Any]) -> str | None:
    """
    Publish a backtest job to the backtest_jobs stream.

    Returns the Redis stream entry-id on success, or None if Redis is
    unavailable (non-trading-critical — the caller should surface an HTTP 503).

    The consumer group ``backtest_workers`` must exist before the worker
    calls XREADGROUP; BacktestWorker creates it idempotently on startup.
    """
    data = {
        "job_id":  job_id,
        "payload": json.dumps(payload, default=str),
    }
    try:
        entry_id = await redis_manager.xadd(
            BACKTEST_STREAM, data, max_retries=1, raise_on_error=False
        )
        logger.info(f"Backtest job {job_id} published to stream (entry={entry_id})")
        return entry_id
    except Exception as e:
        logger.error(f"Failed to publish backtest job {job_id}: {e}")
        return None
