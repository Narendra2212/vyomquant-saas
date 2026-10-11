"""Order de-duplication must fail CLOSED and must be atomic.

WHAT THIS GUARDS
----------------
``IdempotentExchangeSubmission`` is the component whose entire purpose is "one signal
produces at most one exchange order". Three defects found in the pre-launch audit each
defeated that purpose:

1. **Fail-open duplicate check.** ``check_duplicate_submission`` ended with::

       except Exception as e:
           logger.error(f"Duplicate check failed: {e}")
           # Fail safe: assume not duplicate to prevent blocking
           return False

   ``False`` means "not a duplicate", so a Redis outage made every retry submit. The
   comment calls this fail-safe; it is fail-open on the only question the module asks.

2. **Non-atomic check-then-write.** The gate was a read (``exists``) followed by a
   separate write (``setex``). Two pods consuming one signal queue both read "new", both
   write, both submit.

3. **Ambiguous failure released the claim.** The exchange-submission ``except`` block
   called ``_remove_submission_record(...)`` "to allow retry". That exception includes
   the case where the venue accepted the order and the response was lost, so the retry
   placed a second real order.

No network and no real Redis: the store is replaced with in-process doubles whose
failure modes are the ones being asserted.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from backend_app.backend.distributed_execution.idempotent_exchange_submission import (
    IdempotencyUnavailable, IdempotentExchangeSubmission)

TENANT = "tenant-0001"
COID = "client_deadbeefdeadbeef"


class FakeRedis:
    """Minimal store with the three operations the subject uses, honouring ``nx``."""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def exists(self, key):
        return key in self.store

    async def set(self, key, value, ex=None, nx=False, **kw):
        self.set_calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None  # redis-py returns None when a SET NX loses
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return True


class BrokenRedis:
    """Every operation raises, as during a Redis outage or a network partition."""

    def __init__(self, exc=None):
        self.exc = exc or ConnectionError("redis is unreachable")

    async def exists(self, *a, **k):
        raise self.exc

    async def set(self, *a, **k):
        raise self.exc

    async def setex(self, *a, **k):
        raise self.exc

    async def delete(self, *a, **k):
        raise self.exc


@pytest.fixture
def sub():
    s = IdempotentExchangeSubmission()
    s.redis = FakeRedis()
    return s


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fail closed
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_check_raises_rather_than_reporting_not_a_duplicate(sub):
    """THE DEFECT. An unreachable store must not be answered as "this order is new"."""
    sub.redis = BrokenRedis()

    with pytest.raises(IdempotencyUnavailable):
        asyncio.run(sub.check_duplicate_submission(COID, TENANT))


def test_claim_raises_when_the_store_is_unreachable(sub):
    sub.redis = BrokenRedis()

    with pytest.raises(IdempotencyUnavailable):
        asyncio.run(sub.claim_submission(COID, TENANT))


def test_submission_refuses_and_sends_nothing_when_the_store_is_unreachable(sub):
    """End to end: the public entry point must report a refusal, not attempt a submit."""
    sub.redis = BrokenRedis()

    reached_exchange = False

    class Tripwire:
        def __getattr__(self, _):
            nonlocal reached_exchange
            reached_exchange = True
            raise AssertionError("the exchange must not be touched")

    result = asyncio.run(
        sub.submit_order_idempotent(
            tenant_id=TENANT,
            strategy_id="strat-1",
            signal_id="sig-1",
            timestamp=datetime.now(timezone.utc),
            order_data={"symbol": "BTC/USDT", "side": "buy", "quantity": "0.01",
                        "portfolio_state": {"total_equity": "10000"}},
            exchange_client=Tripwire(),
        )
    )

    assert result["success"] is False
    assert result["error"] == "IDEMPOTENCY_UNAVAILABLE"
    assert "NOT submitted" in result["message"]
    assert reached_exchange is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Atomicity
# ─────────────────────────────────────────────────────────────────────────────

def test_only_one_of_two_concurrent_claims_wins(sub):
    """The check-then-write race, driven concurrently rather than described."""

    async def scenario():
        return await asyncio.gather(
            sub.claim_submission(COID, TENANT),
            sub.claim_submission(COID, TENANT),
        )

    first, second = asyncio.run(scenario())

    assert sorted([first, second]) == [False, True], (
        "both workers were granted the right to submit the same client_order_id"
    )


def test_many_concurrent_claims_yield_exactly_one_winner(sub):
    """The pod-fan-out shape: one signal, twenty consumers, one order."""

    async def scenario():
        return await asyncio.gather(*[sub.claim_submission(COID, TENANT) for _ in range(20)])

    results = asyncio.run(scenario())

    assert sum(1 for r in results if r) == 1, f"expected exactly 1 winner, got {sum(1 for r in results if r)}"


def test_the_claim_uses_set_nx_and_not_a_read_then_write(sub):
    """Pins the mechanism, because a future refactor back to exists()+setex would pass
    the single-threaded tests above while reopening the race."""
    asyncio.run(sub.claim_submission(COID, TENANT))

    nx_calls = [c for c in sub.redis.set_calls if c["nx"]]
    assert nx_calls, "claim_submission did not issue a SET with NX"
    assert nx_calls[0]["ex"] == sub.client_order_id_ttl, "claim was written without a TTL"


def test_a_second_submission_of_the_same_signal_is_reported_as_duplicate(sub):
    """Same tenant, strategy, signal and timestamp -> same deterministic id -> refused."""
    args = dict(
        tenant_id=TENANT,
        strategy_id="strat-1",
        signal_id="sig-1",
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        order_data={"symbol": "BTC/USDT", "side": "buy", "quantity": "0.01"},
        exchange_client=object(),
    )

    # First attempt: no portfolio_state, so it refuses downstream of the claim. What
    # matters is that the claim was taken.
    asyncio.run(sub.submit_order_idempotent(**args))
    second = asyncio.run(sub.submit_order_idempotent(**dict(args)))

    assert second["success"] is False
    assert second["error"] == "duplicate_submission"


# ─────────────────────────────────────────────────────────────────────────────
# 3. An ambiguous failure must not release the claim
# ─────────────────────────────────────────────────────────────────────────────

def test_a_lost_response_keeps_the_claim_so_a_retry_cannot_double_the_order(sub, monkeypatch):
    """The order may have reached the venue. Retrying must not place a second one."""
    import backend_app.backend.distributed_execution.idempotent_exchange_submission as mod

    class TimingOutEngine:
        def __init__(self, *a, **k):
            pass

        async def execute_trade(self, **kw):
            raise TimeoutError("response lost after the venue accepted the order")

    monkeypatch.setattr(
        "backend_app.core.execution_engine.ExecutionEngine", TimingOutEngine
    )

    args = dict(
        tenant_id=TENANT,
        strategy_id="strat-1",
        signal_id="sig-ambiguous",
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        order_data={
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": "0.01",
            "portfolio_state": {"total_equity": "10000"},
        },
        exchange_client=object(),
    )

    first = asyncio.run(sub.submit_order_idempotent(**args))
    assert first["success"] is False
    assert first.get("requires_reconciliation") is True, (
        "an ambiguous submission failure must be flagged for reconciliation"
    )

    # The claim must still be held, so the retry is refused as a duplicate.
    retry = asyncio.run(sub.submit_order_idempotent(**dict(args)))
    assert retry["error"] == "duplicate_submission", (
        "the idempotency claim was released by a failure that may have placed a real "
        "order, so the retry would submit a second one"
    )


def test_the_failure_path_does_not_remove_the_submission_record(sub):
    """Pins the specific call that was removed, so it cannot be reinstated quietly.

    Checked by parsing the function rather than by scanning its text: the comment that
    replaced the call necessarily names it, so a substring search matches the
    explanation of the fix and reports the defect as still present.

    The helper itself is deliberately kept — releasing a claim is a legitimate
    administrative action. What must not happen is the SUBMISSION path doing it
    automatically on a failure it cannot interpret.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(sub.submit_order_idempotent)))

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "_remove_submission_record" not in called, (
        "submit_order_idempotent releases its idempotency claim on failure; a lost "
        "response would then be retried as a second real order"
    )
