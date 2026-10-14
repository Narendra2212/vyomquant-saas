"""RT-19: the user kill switch is durable across workers and stops the execution engine."""
import asyncio
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend_app.core import tenant_halt as th
from backend_app.core.execution_engine import ExecutionEngine


class FakeRedis:
    def __init__(self):
        self.d = {}
        self.fail = False

    async def set(self, k, v, **kw):
        if self.fail:
            raise ConnectionError("down")
        self.d[k] = v

    async def get(self, k):
        if self.fail:
            raise ConnectionError("down")
        return self.d.get(k)

    async def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)


@pytest.fixture
def redis():
    r = FakeRedis()

    async def _client():
        return r

    th._mirror.clear()
    with patch.object(th, "_client", _client):
        yield r
    th._mirror.clear()


def test_halt_is_visible_to_a_worker_with_an_empty_mirror(redis):
    tid = str(uuid4())
    assert asyncio.run(th.set_halt(tid, True)) is True
    th._mirror.clear()  # a different worker / a restart: no local memory of it
    assert asyncio.run(th.is_halted(tid)) is True


def test_recover_clears_the_shared_state(redis):
    tid = str(uuid4())
    asyncio.run(th.set_halt(tid, True))
    asyncio.run(th.set_halt(tid, False))
    th._mirror.clear()
    assert asyncio.run(th.is_halted(tid)) is False


def test_one_tenants_halt_does_not_halt_another(redis):
    a, b = str(uuid4()), str(uuid4())
    asyncio.run(th.set_halt(a, True))
    th._mirror.clear()
    assert asyncio.run(th.is_halted(b)) is False


def test_unreadable_store_is_fail_closed(redis):
    redis.fail = True
    with pytest.raises(th.TenantHaltUnknown):
        asyncio.run(th.is_halted(str(uuid4())))


def test_persist_failure_is_reported_but_the_local_worker_still_halts(redis):
    redis.fail = True
    tid = str(uuid4())
    assert asyncio.run(th.set_halt(tid, True)) is False
    assert th.mirror_is_halted(tid) is True


class _GlobalInactive:
    async def is_active(self):
        return False


def _engine_call(tid):
    engine = ExecutionEngine(portfolio_state={"total_equity": Decimal("100000")})
    # Isolate the tenant halt: the GLOBAL switch (checked first) is made inactive here.
    with patch("backend_app.core.global_safety.get_global_kill_switch", lambda: _GlobalInactive()):
        return asyncio.run(engine.execute_with_idempotency(
            tenant_id=tid, strategy_id="s1", symbol="BTC/USDT", side="buy",
            size=Decimal("0.01"), price=Decimal("50000"), source="bot_runner",
        ))


def test_engine_blocks_a_halted_tenant_before_any_strategy_or_db_work(redis):
    tid = uuid4()
    asyncio.run(th.set_halt(str(tid), True))
    th._mirror.clear()
    res = _engine_call(tid)
    assert res["status"] == "blocked"
    assert "Tenant kill switch" in res["message"]


def test_engine_blocks_when_halt_state_cannot_be_read(redis):
    redis.fail = True
    res = _engine_call(uuid4())
    assert res["status"] == "blocked"
    assert "halt check failed" in res["message"]
