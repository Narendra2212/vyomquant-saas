"""Optional worker that consumes command_queue events and executes them."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from backend_app.core.cache import redis_manager
from backend_app.core.state import app_state

logger = logging.getLogger("CommandWorker")


async def _ensure_group(stream: str, group: str):
    if not redis_manager.pool:
        return
    try:
        await redis_manager.pool.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception:
        pass


async def run_worker():
    await redis_manager.connect()
    await _ensure_group("command_queue", "command_workers")
    consumer = os.getenv("WORKER_NAME", "command_worker_1")
    while True:
        batches = await redis_manager.xreadgroup(
            group="command_workers", consumer=consumer, streams={"command_queue": ">"}
        )
        if not batches:
            await asyncio.sleep(1)
            continue
        for stream_name, messages in batches:
            for msg_id, fields in messages:
                try:
                    action = fields.get("action")
                    payload = json.loads(fields.get("payload", "{}"))
                    if action == "start_bot":
                        await app_state.fleet.start_bot(
                            payload["user_id"], payload["symbol"], payload["blueprint"]
                        )
                    elif action == "stop_bot":
                        await app_state.fleet.stop_bot(
                            payload["user_id"], payload["symbol"]
                        )
                    elif action == "kill_all":
                        await app_state.fleet.shutdown_all()
                except Exception as e:
                    logger.exception("Worker failed: %s", e)
                finally:
                    await redis_manager.xack(stream_name, "command_workers", msg_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
