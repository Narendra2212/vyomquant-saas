"""
tests/test_root_cause_stress_suite.py

ANTIGRAVITY v2 Stress Test Suite

Stress Tests:
1. 1,000 Concurrent Signal Executions (Duplicate Signal Ingest)
2. Worker Crash and Failover Recovery
3. Network Flakiness and Retry Idempotency
4. Single Portfolio Source of Truth Integrity
"""

import asyncio
import uuid
from decimal import Decimal
import pytest

from backend_app.core.execution_engine import ExecutionEngine, ExecutionResult
from backend_app.core.portfolio_engine import PortfolioEngine


class StressMockExecutor:
    def __init__(self):
        self.call_count = 0

    async def place_order(self, symbol, side, order_type, size, price=None):
        self.call_count += 1
        await asyncio.sleep(0.001)  # Simulate microsecond latency
        return type('ExecResult', (), {
            'success': True,
            'exchange_order_id': f"stress_ex_{uuid.uuid4().hex[:8]}",
            'filled_size': size,
            'avg_price': price or Decimal("50000.0"),
            'raw_response': {'info': {'status': 'FILLED'}}
        })()


class StressRepo:
    """An in-memory stand-in for ``ExecutionRecordRepository``.

    WHY THIS EXISTS. Without it this test depended on whatever happened to refuse the
    order first, and that made it measure almost nothing - see the long note on
    :func:`test_1000_executions_stress`. The engine's idempotency machinery is what this
    test is about, so it is driven against a deterministic double rather than against a
    database that may or may not be present: the first call for a given ``execution_id``
    proceeds, every repeat is told to skip. That is exactly the decision
    ``check_idempotent_execution`` makes, and it is the thing being stressed.

    Same pattern and the same reason as ``StressMockExecutor`` above.
    """

    def __init__(self):
        self.seen: set = set()
        self.proceeded: list = []
        self.lock = asyncio.Lock()

    def check_idempotent_execution(self, **kwargs):
        execution_id = kwargs.get("execution_id") or self._derive(kwargs)
        if execution_id in self.seen:
            return (execution_id, "skip_return_result", {"order_id": "stress_cached"})
        self.seen.add(execution_id)
        self.proceeded.append(execution_id)
        return (execution_id, "proceed", None)

    @staticmethod
    def _derive(kwargs) -> str:
        """The engine passes the identity fields; a stable key over them is enough here."""
        return "|".join(
            str(kwargs.get(field))
            for field in ("tenant_id", "strategy_id", "symbol", "side", "qty", "price")
        )

    def claim_execution(self, execution_id, tenant_id):
        return True, {"execution_id": execution_id}

    def claim_execution_within_capacity(self, execution_id, tenant_id, **kwargs):
        return True, {"execution_id": execution_id}, None

    def update_status(self, **kwargs):
        return None


class _KillSwitchOff:
    """An explicitly readable, explicitly disengaged kill switch."""

    async def is_active(self) -> bool:
        return False


def test_1000_executions_stress(monkeypatch):
    """1,000 concurrent calls through the execution funnel.

    MEASURES
      * every one of the 1,000 calls returns a structured ``ExecutionResult`` - no
        unhandled exception escapes the funnel under concurrency;
      * idempotency collapses 1,000 calls onto the 10 distinct executions they represent.
        The engine derives one ``execution_id`` per (tenant, strategy, symbol, bucket,
        side, qty, price), and the 1,000 calls use 10 distinct strategies with identical
        size and price - so exactly 10 may proceed and 990 must be told to skip;
      * the venue is never reached.

    WHY IT WAS REWRITTEN, AND WHAT IT USED TO MEASURE
    -------------------------------------------------
    This test previously accepted any of six statuses including ``blocked`` and ``failed``,
    and then asserted ``executor.call_count <= 10``. Both halves had stopped meaning
    anything:

    * In the Phase 7 PostgreSQL lane it FAILED with ``assert 0 == 1000``: the tenant uuid
      has no ``profiles`` row, so all 1,000 inserts violated
      ``fk_execution_records_tenant_id``, every result was an exception, and none matched
      the status filter.
    * After KILL-01 made the kill switch fail closed, an uninitialised Redis began
      refusing every order at the first gate. All 1,000 returned ``blocked``, the status
      filter accepted them, and the test went GREEN while exercising exactly one gate.
      ``executor.call_count`` was 0, so ``<= 10`` passed vacuously. A green run that
      measured less than the failing one.
    * ``executor.call_count <= 10`` can no longer measure idempotency through this entry
      point at all, because TRADE-05 deliberately made ``execute_trade`` unable to place a
      live order: it forwards no risk verdict and the venue branch refuses anything whose
      ``risk_approved`` is not ``True``. The count is now structurally 0 by design, not by
      idempotency. That is an OBSOLETE assertion with a documented contract change, so it
      is replaced by the assertion that states the structural truth directly.

    The collapse ratio is asserted at the funnel instead, against a deterministic
    repository double, which is where idempotency is actually decided. Both preconditions
    the old version left to chance - a readable kill switch and a working repository - are
    now stated.
    """
    async def _run():
        import backend_app.core.execution_engine as engine_module
        import backend_app.core.global_safety as global_safety

        portfolio_state = {"total_equity": Decimal("1000000.0")}
        executor = StressMockExecutor()
        engine = ExecutionEngine(portfolio_state=portfolio_state, exchange_executor=executor)

        repo = StressRepo()
        monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: _KillSwitchOff())
        monkeypatch.setattr(engine_module, "ExecutionRecordRepository", lambda _session: repo)
        monkeypatch.setattr(
            engine_module, "SessionLocal", lambda *a, **k: _NullSession()
        )
        monkeypatch.setattr(engine, "_validate_strategy_exists", lambda *a, **k: True)

        tenant_id = uuid.uuid4()
        strategy_id = "test_stress_strategy_1000"

        # 10 batches of 100 concurrent executions
        total_calls = 1000
        batch_size = 100

        all_results = []
        for batch in range(total_calls // batch_size):
            tasks = [
                engine.execute_trade(
                    tenant_id=tenant_id,
                    strategy_id=f"{strategy_id}_{i % 10}",  # 10 distinct strategies
                    symbol="BTC/USDT",
                    side="buy",
                    size=Decimal("0.01"),
                    price=Decimal("50000.0")
                )
                for i in range(batch_size)
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(batch_results)

        exceptions = [r for r in all_results if isinstance(r, BaseException)]
        assert exceptions == [], (
            f"{len(exceptions)} of 1000 concurrent calls raised instead of returning a "
            f"result. First: {exceptions[0]!r}"
        )

        structured = [r for r in all_results if isinstance(r, ExecutionResult)]
        assert len(structured) == 1000, (
            f"only {len(structured)} of 1000 calls returned an ExecutionResult; the rest "
            f"were {[type(r).__name__ for r in all_results if not isinstance(r, ExecutionResult)][:5]}"
        )

        statuses = sorted({r.status for r in structured})
        print(f"Statuses observed: {statuses}")
        print(f"Distinct executions that proceeded: {len(repo.proceeded)}")

        # IDEMPOTENCY, measured where it is decided.
        assert len(repo.proceeded) == 10, (
            f"1000 calls over 10 distinct execution identities produced "
            f"{len(repo.proceeded)} proceeding executions. Idempotency did not collapse "
            f"the duplicates."
        )

        skipped = [r for r in structured if r.status == "skipped_completed"]
        assert len(skipped) == 990, (
            f"expected 990 duplicate calls to be told to skip, got {len(skipped)}. "
            f"Statuses: {statuses}"
        )

        # TRADE-05's structural truth: this entry point cannot reach a venue, because
        # `execute_trade` forwards no risk verdict. Exact, not a bound.
        assert executor.call_count == 0, (
            f"the venue was called {executor.call_count} time(s) through execute_trade, "
            f"which forwards no risk verdict. Either TRADE-05's refusal has been removed "
            f"or execute_trade now supplies a verdict it did not evaluate."
        )

    asyncio.run(_run())


class _NullSession:
    """A session that is opened and closed and never queried."""

    def close(self):
        return None

    def rollback(self):
        return None

    def commit(self):
        return None


def test_portfolio_integrity_under_high_throughput():
    """
    Verify portfolio state single source of truth holds under 1,000 sequential allocations.
    """
    pe = PortfolioEngine(total_capital=1000000.0, max_positions=10, max_allocation_per_asset=0.2)
    
    for i in range(1000):
        signals = {f"ASSET_{i % 5}": 1.0}
        alloc = pe.allocate(signals)
        assert len(alloc) <= 10
        for asset, val in alloc.items():
            assert val <= 200000.0  # 20% limit

    assert pe.total_capital == 1000000.0
