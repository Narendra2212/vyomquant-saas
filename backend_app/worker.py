"""
worker.py — Backtest job worker (Redis Streams consumer).

Replaces the former RQ-based worker with the Redis Streams consumer
pattern already used by every other async workflow in this codebase
(commands, market data, strategy/risk/execution signals, DAG tasks).

Architecture:
  POST /api/strategies/backtest
      └─ publishes job envelope to  "backtest_jobs"  stream
      └─ writes initial status hash  backtest:status:{job_id}

  BacktestWorker (this module)
      └─ XREADGROUP from "backtest_jobs", consumer-group "backtest_workers"
      └─ sets status → running
      └─ calls backtest_internal(payload)
      └─ stores result, sets status → completed / failed
      └─ XACKs the stream message

  GET /api/strategies/backtest/{job_id}
      └─ reads backtest:status:{job_id} hash
      └─ returns {job_id, status, result?, error?}

Retry semantics:
  The previous RQ implementation did NOT configure retry=Retry(…), so it
  provided zero automatic retries.  This worker replicates that behaviour
  deliberately: failed jobs are ACKed immediately and the "failed" status
  is surfaced so the user can resubmit.  If automatic retries are ever
  needed, set the failed job's message back into the stream instead of
  ACKing it, and cap redeliveries via the stream's PEL.

Startup:
  Run directly:   python -m backend_app.worker
  Or import BacktestWorker and call start() from your lifespan hook.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("BacktestWorker")

BACKTEST_STREAM         = "backtest_jobs"
CONSUMER_GROUP          = "backtest_workers"
STATUS_KEY_PREFIX       = "backtest:status:"
STATUS_TTL_SECONDS      = 86_400   # 24 hours
POLL_BLOCK_MS           = 2_000    # XREADGROUP block time


def _status_key(job_id: str) -> str:
    return f"{STATUS_KEY_PREFIX}{job_id}"


async def _write_status(redis, job_id: str, **fields):
    """Atomically update the status hash fields for a job."""
    key = _status_key(job_id)
    try:
        await redis.pool.hset(key, mapping={k: str(v) for k, v in fields.items()})
        await redis.pool.expire(key, STATUS_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"Status update failed for job {job_id}: {e}")


async def _ensure_consumer_group(redis):
    """Create the consumer group idempotently; safe to call on every startup."""
    try:
        await redis.pool.xgroup_create(
            BACKTEST_STREAM, CONSUMER_GROUP, id="0", mkstream=True
        )
        logger.info(f"Consumer group '{CONSUMER_GROUP}' created on stream '{BACKTEST_STREAM}'")
    except Exception as e:
        # BUSYGROUP: group already exists — expected after first startup
        if "BUSYGROUP" not in str(e):
            logger.warning(f"xgroup_create unexpected error: {e}")


from backend_app.core.worker_base import WorkerBase

class BacktestWorker(WorkerBase):
    """
    Redis Streams consumer for backtest jobs.

    Pattern matches backend/dag_worker.py's DAGWorker exactly.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        poll_interval_seconds: float = 1.0,
    ):
        worker_name = worker_id or f"bt-{str(uuid.uuid4())[:8]}"
        super().__init__(worker_name=worker_name, poll_interval=poll_interval_seconds)
        self.worker_id = worker_name

    async def start(self):
        """Start the worker event loop as an asyncio background task."""
        from backend_app.core.cache import redis_manager
        await _ensure_consumer_group(redis_manager)
        await super().start()

    async def process_iteration(self):
        """Process a single batch of messages from the stream."""
        from backend_app.core.cache import redis_manager
        
        messages = await redis_manager.xreadgroup(
            group=CONSUMER_GROUP,
            consumer=self.worker_id,
            streams={BACKTEST_STREAM: ">"},
            count=1,
            block=POLL_BLOCK_MS,
        )
        if messages:
            for stream_name, entries in messages:
                for entry_id, fields in entries:
                    await self._process(redis_manager, entry_id, fields)
        else:
            await asyncio.sleep(self.poll_interval)

    async def _process(self, redis, entry_id, fields):
        """Process one backtest job message."""
        # Decode fields (redis-py returns bytes or str depending on decode_responses)
        def _decode(v):
            return v.decode() if isinstance(v, bytes) else v

        job_id  = _decode(fields.get(b"job_id")  or fields.get("job_id",  ""))
        payload_raw = _decode(fields.get(b"payload") or fields.get("payload", "{}"))

        if not job_id:
            logger.warning(f"Skipping malformed stream entry {entry_id}: no job_id")
            await redis.xack(BACKTEST_STREAM, CONSUMER_GROUP, entry_id)
            return

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}

        logger.info(f"BacktestWorker {self.worker_id}: starting job {job_id}")

        # Mark running
        await _write_status(
            redis, job_id,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # Execute backtest (blocking CPU work — acceptable for a worker process)
        try:
            # Import here to avoid circular imports; backtest_internal is a
            # plain sync function that runs heavy CPU/IO work.
            from backend_app.routers.strategies import backtest_internal

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, backtest_internal, payload)

            # Detect application-level errors (backtest_internal returns a
            # dict with an "error" key on failure rather than raising)
            if isinstance(result, dict) and "error" in result and len(result) <= 3:
                raise RuntimeError(result.get("error", "unknown backtest error"))

            await _write_status(
                redis, job_id,
                status="completed",
                result=json.dumps(result, default=str),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.info(f"BacktestWorker {self.worker_id}: job {job_id} completed")

        except Exception as exc:
            logger.error(
                f"BacktestWorker {self.worker_id}: job {job_id} failed: {exc}",
                exc_info=True,
            )
            await _write_status(
                redis, job_id,
                status="failed",
                error=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

        # ACK unconditionally — no automatic retry (matches previous RQ behaviour
        # where no retry= was configured).  User must resubmit on failure.
        await redis.xack(BACKTEST_STREAM, CONSUMER_GROUP, entry_id)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────────────────────────────────────

async def _main():
    import os
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    worker = BacktestWorker()
    await worker.start()
    logger.info("BacktestWorker running — Ctrl-C to stop")
    try:
        # Keep the process alive until SIGINT/SIGTERM
        while True:
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    print("Starting Backtest Stream Worker…")
    asyncio.run(_main())
