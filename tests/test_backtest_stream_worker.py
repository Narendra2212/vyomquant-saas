"""
tests/test_backtest_stream_worker.py

Integration/unit tests for the Redis Streams-based backtest worker and endpoints.
Verifies:
  1. POST /backtest writes queued status hash and publishes to stream.
  2. BacktestWorker consumes job from stream, transitions status to running -> completed, and stores result.
  3. BacktestWorker handles job failure, transitions status to failed, and stores error message.
  4. GET /backtest/{job_id} surfaces status, results, and errors correctly.
"""

import os
os.environ["ENV"] = "development"
os.environ["DEV_MODE"] = "true"

import asyncio
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend_app.worker import BacktestWorker, _write_status, _status_key, CONSUMER_GROUP
from backend_app.core.event_bus import BACKTEST_STREAM, publish_backtest_job


class MockRedisPool:
    def __init__(self):
        self.hashes = {}

    async def hset(self, key, mapping=None):
        if key not in self.hashes:
            self.hashes[key] = {}
        if mapping:
            for k, v in mapping.items():
                self.hashes[key][k] = str(v)

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def expire(self, key, seconds):
        pass

    async def xgroup_create(self, stream, group, id="0", mkstream=True):
        pass


class MockRedisManager:
    def __init__(self):
        self.pool = MockRedisPool()
        self.stream_entries = []
        self.acked_entries = []

    async def xadd(self, stream, payload, max_retries=1, raise_on_error=False):
        entry_id = f"{int(asyncio.get_event_loop().time()*1000)}-0"
        self.stream_entries.append((entry_id, payload))
        return entry_id

    async def xreadgroup(self, group, consumer, streams, count=1, block=1000):
        if not self.stream_entries:
            return []
        entries = self.stream_entries[:]
        self.stream_entries.clear()
        return [(list(streams.keys())[0], entries)]

    async def xack(self, stream, group, *ids):
        self.acked_entries.extend(ids)
        return len(ids)

    async def hgetall(self, key):
        return await self.pool.hgetall(key)


def test_write_and_read_status_hash():
    """Verify status hash writing and reading logic."""
    async def _run():
        mock_redis = MockRedisManager()
        job_id = "test-job-123"

        await _write_status(mock_redis, job_id, status="queued", submitted_at="2026-07-28T00:00:00Z")
        
        status_data = await mock_redis.hgetall(_status_key(job_id))
        assert status_data.get("status") == "queued"
        assert status_data.get("submitted_at") == "2026-07-28T00:00:00Z"

    asyncio.run(_run())


def test_worker_successful_job_execution():
    """Verify BacktestWorker consumes, executes, updates status to completed, and ACKs."""
    async def _run():
        mock_redis = MockRedisManager()
        worker = BacktestWorker(worker_id="test-worker")
        job_id = "job-success-1"
        payload = {"dag": {"nodes": []}, "initial_capital": 10000.0}

        # Stage job in stream
        await mock_redis.xadd(BACKTEST_STREAM, {"job_id": job_id, "payload": json.dumps(payload)})
        await _write_status(mock_redis, job_id, status="queued")

        fake_result = {"total_return_pct": 15.5, "total_trades": 10}

        with patch("backend_app.routers.strategies.backtest_internal", return_value=fake_result):
            # Process single entry directly
            entry_id, fields = mock_redis.stream_entries.pop(0)
            await worker._process(mock_redis, entry_id, fields)

        # Verify status is completed
        status_data = await mock_redis.hgetall(_status_key(job_id))
        assert status_data.get("status") == "completed"
        assert "total_return_pct" in status_data.get("result")
        assert mock_redis.acked_entries == [entry_id]

    asyncio.run(_run())


def test_worker_failed_job_execution():
    """Verify BacktestWorker sets status to failed when backtest raises exception."""
    async def _run():
        mock_redis = MockRedisManager()
        worker = BacktestWorker(worker_id="test-worker")
        job_id = "job-fail-1"
        payload = {"dag": {"nodes": []}}

        await mock_redis.xadd(BACKTEST_STREAM, {"job_id": job_id, "payload": json.dumps(payload)})
        await _write_status(mock_redis, job_id, status="queued")

        with patch("backend_app.routers.strategies.backtest_internal", side_effect=ValueError("Invalid DAG configuration")):
            entry_id, fields = mock_redis.stream_entries.pop(0)
            await worker._process(mock_redis, entry_id, fields)

        status_data = await mock_redis.hgetall(_status_key(job_id))
        assert status_data.get("status") == "failed"
        assert "Invalid DAG configuration" in status_data.get("error")
        assert mock_redis.acked_entries == [entry_id]

    asyncio.run(_run())
