import asyncio

import pytest

from backend_app.core.reconciliation_engine import ReconciliationEngine
from backend_app.backend.reconciliation_worker import ReconciliationWorker
import backend_app.backend.reconciliation_worker as worker_module


class FailingReconciliationSource:
    async def get_orders(self, *_args):
        raise RuntimeError("orders unavailable")

    async def get_positions(self, *_args):
        raise RuntimeError("positions unavailable")

    async def get_fills(self, *_args):
        raise RuntimeError("fills unavailable")


@pytest.mark.parametrize(
    ("method_name", "args", "error"),
    [
        ("_fetch_exchange_orders", ("binance", "user-1"), "orders unavailable"),
        ("_fetch_exchange_positions", ("binance", "user-1"), "positions unavailable"),
        ("_fetch_exchange_fills", ("binance", "user-1"), "fills unavailable"),
        ("_fetch_local_orders", ("tenant-1", "user-1"), "orders unavailable"),
        ("_fetch_local_positions", ("tenant-1", "user-1"), "positions unavailable"),
        ("_fetch_local_fills", ("tenant-1", "user-1"), "fills unavailable"),
    ],
)
def test_reconciliation_fetch_failures_are_not_empty_snapshots(method_name, args, error):
    source = FailingReconciliationSource()
    engine = ReconciliationEngine(source, source, source, auto_correct=True)

    with pytest.raises(RuntimeError, match=error):
        asyncio.run(getattr(engine, method_name)(*args))


def test_reconciliation_returns_failure_without_corrective_actions_when_exchange_read_fails():
    source = FailingReconciliationSource()
    engine = ReconciliationEngine(source, source, source, auto_correct=True)

    result = asyncio.run(engine.reconcile("tenant-1", "binance", "user-1"))

    assert result.success is False
    assert result.reconciliation_actions == []
    assert result.actions_executed == 0


class FailingWorkerExchange:
    async def fetch_open_orders(self):
        raise RuntimeError("worker orders unavailable")

    async def fetch_positions(self):
        raise RuntimeError("worker positions unavailable")


def test_reconciliation_worker_exchange_fetch_failures_propagate(monkeypatch):
    monkeypatch.setattr(worker_module, "CCXT_AVAILABLE", True)
    worker = ReconciliationWorker()
    client = FailingWorkerExchange()

    with pytest.raises(RuntimeError, match="worker orders unavailable"):
        asyncio.run(worker._fetch_exchange_orders(client))
    with pytest.raises(RuntimeError, match="worker positions unavailable"):
        asyncio.run(worker._fetch_exchange_positions(client))


def test_reconciliation_worker_rejects_unavailable_local_state():
    worker = ReconciliationWorker()

    with pytest.raises(RuntimeError, match="Local reconciliation state service is unavailable"):
        asyncio.run(worker._fetch_local_orders("user-1"))
    with pytest.raises(RuntimeError, match="Local position reconciliation is unavailable"):
        asyncio.run(worker._fetch_local_positions("user-1"))
