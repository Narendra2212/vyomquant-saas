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


from backend_app.core.worker_base import WorkerBase

class CommandWorker(WorkerBase):
    def __init__(self, worker_id: str | None = None):
        name = worker_id or os.getenv("WORKER_NAME", "command_worker_1")
        super().__init__(worker_name=name, poll_interval=1.0)
        self.worker_id = name

    async def start(self):
        await redis_manager.connect()
        await _ensure_group("command_queue", "command_workers")
        await super().start()

    async def process_iteration(self):
        batches = await redis_manager.xreadgroup(
            group="command_workers", consumer=self.worker_id, streams={"command_queue": ">"}
        )
        if not batches:
            return
            
        for stream_name, messages in batches:
            for msg_id, fields in messages:
                try:
                    action = fields.get("action")
                    if isinstance(action, bytes):
                        action = action.decode()
                        
                    payload_raw = fields.get("payload", "{}")
                    if isinstance(payload_raw, bytes):
                        payload_raw = payload_raw.decode()
                        
                    payload = json.loads(payload_raw)
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

async def run_worker():
    worker = CommandWorker()
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
