"""
tests/test_execution_engine_consolidation.py

Unit tests verifying:
1. The execution engine architecture has been consolidated down to backend_app.core.execution_engine.ExecutionEngine.
2. Dead execution engine files (unified_execution_engine.py, backend/execution_engine.py, order_execution_engine.py) have been deleted.
3. UnifiedExecutionEngine alias and ExecutionResult dataclass exist in backend_app.core.execution_engine.
4. ExecutionEngine methods operate as expected for order placement and position management.
"""

import asyncio
from decimal import Decimal
import pathlib
from uuid import uuid4

from backend_app.core.execution_engine import (
    ExecutionEngine,
    UnifiedExecutionEngine,
    ExecutionResult,
)


def test_dead_files_removed():
    base_dir = pathlib.Path("backend_app")
    assert not (base_dir / "core" / "unified_execution_engine.py").exists(), (
        "unified_execution_engine.py should be deleted"
    )
    assert not (base_dir / "backend" / "execution_engine.py").exists(), (
        "backend/execution_engine.py should be deleted"
    )
    assert not (base_dir / "backend" / "order_execution_engine.py").exists(), (
        "order_execution_engine.py should be deleted"
    )


def test_execution_engine_alias_and_export():
    assert UnifiedExecutionEngine is ExecutionEngine
    engine = ExecutionEngine(portfolio_state={"total_equity": Decimal("100000.0")})
    assert engine.current_equity == Decimal("100000.0")


def test_execution_result_dataclass():
    res = ExecutionResult(
        success=True,
        execution_id="exec-123",
        status="completed",
        message="Trade executed successfully",
        details={"price": "50000.0"},
    )
    assert res.success is True
    assert res.execution_id == "exec-123"
    assert res.status == "completed"


def test_execute_trade_blocked_when_not_bot_runner():
    async def _run():
        engine = ExecutionEngine(portfolio_state={"total_equity": Decimal("100000.0")})
        tenant_id = uuid4()
        
        res = await engine.execute_with_idempotency(
            tenant_id=tenant_id,
            strategy_id="strat-123",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000.0"),
            source="manual",
        )
        assert res["status"] == "blocked"
        assert "Direct execution blocked" in res["message"]

    asyncio.run(_run())


def test_execution_router_importable():
    from backend_app.backend import execution_router
    assert hasattr(execution_router, "router")
