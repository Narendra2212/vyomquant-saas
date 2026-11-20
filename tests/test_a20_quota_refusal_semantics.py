"""A refused reservation must report a usage figure that is true, and an unmeasurable quota must refuse.

PHASE 11B-A20. Two defects in `SubscriptionEngine.reserve_quota`, both evidenced by CI run
`38061973739` on `f5b99e57` (see `staging/PHASE11B_A19_D5_CI_EVIDENCE_REPORT.md`).

DEFECT 1 — A REFUSAL REPORTED THE MID-CONTENTION TRANSIENT
-----------------------------------------------------------
`reserve_quota` increments first and reverts on overflow, which is the correct defence
against the check-then-increment race. But it returned the value `DECRBY` answered with,
and that is the meter as it stood after *this* caller's decrement — concurrent refusals
have incremented and not yet reverted theirs. So the figure handed back is a transient that
can sit far above the limit. The real-Redis test observed a refusal reporting **35 against
a limit of 10** while the meter itself settled at exactly 10:

    AssertionError: a refusal reported usage 35, outside [0, 10] -- the revert either did
    not happen or overshot

The revert *had* happened. The reporting was wrong, not the enforcement. The docstring
claimed "the reverted value is reported rather than the inflated one", which the code did
not deliver under concurrency.

DEFECT 2 — AN UNMEASURABLE QUOTA ADMITTED
-----------------------------------------
`redis_manager.incrby` is a fail-soft proxy: on any exception it logs a warning and returns
``0`` (`backend_app/core/cache/redis_manager.py`). That ``0`` flowed into
``if new_usage > effective``, which is false for every positive limit, so the reservation
was **admitted**. A cache fault therefore disabled the limit silently — the quota stopped
enforcing at exactly the moment it could no longer be measured. No test covered this,
because the fault is invisible on the in-memory substrate where `incrby` always succeeds.

WHY THESE TESTS RUN WITHOUT A REAL REDIS
----------------------------------------
Both defects live in `reserve_quota`'s handling of what the cache *returns*, so they are
reproducible by substituting the counter — no server required. That matters: the real-Redis
lane is opt-in and skips by default, and a defect only provable on infrastructure nobody
runs is a defect with no regression test. The concurrency property itself is covered
separately, in-memory and against real Redis, by
`tests/test_quota01_metered_reservation_is_hard.py`.

The substitute here is deliberately *not* a mock of `reserve_quota`'s logic: it is a real
dict-backed counter whose `incrby`/`decrby` can be told to answer the way the fail-soft
proxy answers under fault or contention. The logic under test is the production logic.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from backend_app.core import subscription_engine as se
from backend_app.core.subscription_engine import SubscriptionEngine

pytestmark = pytest.mark.asyncio


def _period() -> str:
    """A unique period per test so meters never collide across tests or runs."""
    return "2026-01-a20-" + uuid.uuid4().hex[:8]


class FakeMeter:
    """A dict-backed counter that can answer the way the fail-soft proxy does.

    `incr_returns` / `decr_returns`, when set, override the value *reported* for that
    operation while the underlying store still moves. That is exactly the shape of both
    defects: the store is fine, the returned figure is not.
    """

    def __init__(self, *, incr_returns=None, decr_returns=None):
        self.store: dict[str, int] = {}
        self.incr_returns = incr_returns
        self.decr_returns = decr_returns
        self.expire_calls: list[tuple[str, int]] = []

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key] if self.incr_returns is None else self.incr_returns

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - amount
        return self.store[key] if self.decr_returns is None else self.decr_returns

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        return True

    async def get(self, key: str):
        value = self.store.get(key)
        return None if value is None else str(value)


@pytest.fixture
def meter(monkeypatch):
    """Substitute the counter `reserve_quota` reaches for, and nothing else."""
    fake = FakeMeter()
    monkeypatch.setattr(se, "redis_manager", fake)
    return fake


# ─────────────────────────────────────────────────────────────────────────────
#  DEFECT 2 — an unmeasurable quota must refuse, not admit
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_failed_increment_refuses_instead_of_admitting(meter, caplog):
    """THE FAIL-CLOSED ASSERTION. A cache fault must not disable the limit.

    `incrby` returning 0 is the proxy's documented behaviour on a connection fault. Before
    the fix this produced `(True, 0, limit)` — an admission — because `0 > limit` is false.
    """
    meter.incr_returns = 0  # what the proxy returns when it swallows a connection error

    with caplog.at_level(logging.ERROR):
        allowed, usage, effective = await SubscriptionEngine.reserve_quota(
            f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
            amount=1, limit=10, period=_period(),
        )

    assert allowed is False, (
        "a reservation was ADMITTED while the meter could not be incremented; a quota that "
        "cannot be measured must refuse"
    )
    assert usage == 0
    assert effective == 10
    assert any("unmeasurable" in r.getMessage() for r in caplog.records), (
        "the refusal was not logged, so an operator cannot tell a broken cache from a "
        "genuinely exhausted quota"
    )


async def test_a_negative_increment_result_also_refuses(meter):
    """Any answer below the requested amount means the increment did not land."""
    meter.incr_returns = -5

    allowed, usage, _ = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=1, limit=10, period=_period(),
    )

    assert allowed is False
    assert usage == 0


async def test_a_zero_amount_is_not_mistaken_for_a_failure(meter):
    """The guard is scoped to a positive amount, so `amount=0` keeps its old meaning.

    A zero-amount reservation legitimately leaves the meter where it is, and the sentinel
    (`result < amount`) must not fire on it.
    """
    allowed, usage, effective = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=0, limit=10, period=_period(),
    )

    assert allowed is True
    assert usage == 0
    assert effective == 10


# ─────────────────────────────────────────────────────────────────────────────
#  DEFECT 1 — a refusal must report a figure inside [0, limit]
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_refusal_never_reports_more_than_the_limit(meter, caplog):
    """THE A19 REGRESSION. Reproduces the observed 35-against-10 and pins the fix.

    `decr_returns = 35` is the transient a real concurrent refusal saw: its own decrement
    landed, thirty-four other racers had incremented and not yet reverted.
    """
    meter.store["seed"] = 0
    meter.incr_returns = 46   # this caller pushed the meter past the limit
    meter.decr_returns = 35   # ...and the meter was still inflated when it reverted

    with caplog.at_level(logging.INFO):
        allowed, usage, effective = await SubscriptionEngine.reserve_quota(
            f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
            amount=1, limit=10, period=_period(),
        )

    assert allowed is False
    assert 0 <= usage <= effective, (
        f"a refusal reported usage {usage}, outside [0, {effective}] -- the exact assertion "
        f"that failed in CI run 38061973739"
    )
    assert usage == 10, "a refused caller is at its capacity, so it should report the limit"
    assert any("mid-contention" in r.getMessage() for r in caplog.records), (
        "the inflated transient was discarded without a trace; contention must stay "
        "observable even though it is not reported to the caller"
    )


async def test_a_refusal_never_reports_a_negative_usage(meter):
    """The lower bound matters too: a revert must not report a meter driven below zero."""
    meter.incr_returns = 11
    meter.decr_returns = -3

    allowed, usage, effective = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=1, limit=10, period=_period(),
    )

    assert allowed is False
    assert usage == 0
    assert 0 <= usage <= effective


async def test_an_uncontended_refusal_reports_the_limit_exactly(meter):
    """The clamp must not distort the ordinary case.

    With no contention the meter sits at the limit, this caller pushes it to limit+1 and
    reverts to the limit — so the honest figure and the clamped figure coincide, and the
    fix is invisible here. That is the point: it changes the contended case only.
    """
    meter.incr_returns = 11
    meter.decr_returns = 10

    allowed, usage, effective = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=1, limit=10, period=_period(),
    )

    assert (allowed, usage, effective) == (False, 10, 10)


async def test_a_rollback_that_itself_fails_is_handled_safely(meter, caplog):
    """A revert whose proxy also faults returns 0, and that must not become a negative or
    an admission.

    This is the compound fault: the increment landed, the limit was exceeded, and the
    decrement's proxy swallowed an error. The reservation stays refused and the reported
    figure stays inside the contract.
    """
    meter.incr_returns = 11
    meter.decr_returns = 0  # the proxy's fault value, not a real meter reading

    with caplog.at_level(logging.INFO):
        allowed, usage, effective = await SubscriptionEngine.reserve_quota(
            f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
            amount=1, limit=10, period=_period(),
        )

    assert allowed is False, "a failed rollback must not turn a refusal into an admission"
    assert 0 <= usage <= effective


# ─────────────────────────────────────────────────────────────────────────────
#  The paths the fix must leave alone
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_admission_still_reports_its_own_increment(meter):
    """The ordinary success path is untouched, including the first-write TTL."""
    user = f"a20-{uuid.uuid4().hex[:8]}"
    period = _period()

    allowed, usage, effective = await SubscriptionEngine.reserve_quota(
        user, "free", "backtests", amount=1, limit=10, period=period
    )

    assert (allowed, usage, effective) == (True, 1, 10)
    assert meter.expire_calls, "the first write did not set the meter's TTL"


async def test_an_unlimited_plan_is_unaffected(meter):
    """Unlimited admits by policy, not by measurement, and keeps its own branch."""
    allowed, _usage, effective = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=1, limit=-1, period=_period(),
    )

    assert allowed is True
    assert SubscriptionEngine.is_unlimited(effective)


async def test_a_zero_allowance_refuses_and_writes_no_meter(meter):
    """A plan that can never spend must not accumulate a figure."""
    allowed, usage, effective = await SubscriptionEngine.reserve_quota(
        f"a20-{uuid.uuid4().hex[:8]}", "free", "backtests",
        amount=1, limit=0, period=_period(),
    )

    assert (allowed, usage, effective) == (False, 0, 0)
    assert meter.store == {}, "a zero allowance wrote a meter it can never spend"
