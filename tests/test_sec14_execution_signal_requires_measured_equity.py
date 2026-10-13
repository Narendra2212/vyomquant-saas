"""SEC-14: the execution-signal endpoint must never invent account equity."""
import asyncio
import inspect
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend_app.backend import execution_router as er


def test_no_hardcoded_equity_in_source():
    src = inspect.getsource(er)
    assert "100000" not in src and "100_000" not in src


def test_refuses_when_equity_unmeasured_and_never_reaches_engine():
    body = er.SignalRequest(strategy_id="s1", signal="buy")
    with patch.object(er, "UnifiedExecutionEngine") as eng:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(er.process_signal(body, user={"id": "t1"}))
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == "MEASURED_EQUITY_UNAVAILABLE"
    eng.assert_not_called()


def test_uses_measured_equity_when_available():
    body = er.SignalRequest(strategy_id="s1", signal="buy")

    async def fake_equity(_t):
        return Decimal("2500")

    class Res:
        success = True
        execution_id = "e1"
        message = "ok"

    captured = {}

    class Eng:
        async def execute_trade(self, **kw):
            captured.update(kw)
            return Res()

    with patch.object(er, "_measured_total_equity", fake_equity), patch.object(er, "UnifiedExecutionEngine", Eng):
        out = asyncio.run(er.process_signal(body, user={"id": "t1"}))
    assert out["status"] == "completed"
    assert captured["metadata"]["portfolio_state"]["total_equity"] == Decimal("2500")
