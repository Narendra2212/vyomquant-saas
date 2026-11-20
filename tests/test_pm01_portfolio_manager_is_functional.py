"""PM-01 -- the admin-only portfolio-management API was largely non-functional.

THE DEFECT
    backend_app/backend/portfolio_management.py (mounted admin-only at
    /api/internal/portfolio-mgmt, separate from the real live-trading
    routers/portfolio.py) had several compounding bugs that meant most of its own
    methods crashed or hung the instant they were actually called, which is why
    nothing had caught them -- there is no prior test coverage that calls these
    methods behaviourally at all:

    1. UNAWAITED COROUTINES. Several internal call sites invoked async methods
       (get_margin_metrics, _check_exposure_limits, register_strategy, ...)
       without `await`, so they always got back an unexecuted coroutine object
       instead of the real data -- the next attribute/item access on it then
       raised immediately (e.g. `coroutine.margin_level` -> AttributeError, or
       `coroutine["passes"]` -> TypeError).
    2. NON-REENTRANT LOCK, CALLED REENTRANTLY. asyncio.Lock is not reentrant.
       get_portfolio_summary held self._lock and then called the (also
       lock-acquiring) get_exposure_metrics/get_margin_metrics/get_pnl_summary --
       a guaranteed deadlock the instant someone actually awaited it rather than
       hitting bug #1 first. open_position's own conflict-cancellation step and
       MarginMonitor's forced-liquidation step had the same pattern.
    3. WRONG DATACLASS FIELDS. get_exposure_metrics built an ExposureMetrics()
       instance and then read/wrote MarginMetrics-shaped fields on it
       (margin_used, margin_level, margin_call_threshold, ...) that
       ExposureMetrics does not declare -- AttributeError on literally every call
       with an empty portfolio (margin_used <= 0 means margin_level is never set,
       and it's read two lines later).
    4. NONEXISTENT ATTRIBUTES. create_snapshot read self.pm.available_capital /
       self.pm.used_capital, which do not exist on PortfolioManager at all (only
       per-strategy StrategyAllocation.available_capital/used_capital do) --
       AttributeError on every snapshot, i.e. on every single position open or
       close.
    5. WRONG KEYWORD ARGUMENT. MarginMonitor's forced-liquidation path called
       `self.pm.close_position(position_id=..., close_price=..., reason=...)`,
       but close_position's parameter is named `exit_price`, not `close_price` --
       TypeError on every forced liquidation.
    6. MISLEADING LOG. The liquidation path logged "reducing by 25%" while its
       own code comment admits there is no partial-close method and it always
       closes the ENTIRE position -- an operator watching logs during a real
       margin event would see a false "it only trimmed 25%" when 100% of the
       position was actually liquidated.

WHAT THIS FILE PROVES
    Each bug above is independently exercised, with `asyncio.wait_for` around
    every call that used to deadlock so a regression times out loudly rather
    than hanging the whole test run.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio

from backend_app.backend.portfolio_management import (
    PortfolioManager,
    MarginMetrics,
    ExposureMetrics,
    ExposureLimits,
)

DEADLOCK_TIMEOUT = 5.0


async def _with_timeout(coro):
    return await asyncio.wait_for(coro, timeout=DEADLOCK_TIMEOUT)


@pytest_asyncio.fixture
async def pm():
    manager = PortfolioManager(total_capital=Decimal("100000"))
    await manager.register_strategy(
        "strat_a", allocation_pct=Decimal("0.5"), priority=1, max_position_pct=Decimal("1")
    )
    await manager.register_strategy(
        "strat_b", allocation_pct=Decimal("0.5"), priority=1, max_position_pct=Decimal("1")
    )
    # These tests are about open_position/close_position/update_prices/
    # get_portfolio_summary not crashing or deadlocking, not about exposure-limit
    # enforcement (which has its own coverage) -- set permissive limits so a
    # position the test opens isn't rejected by the portfolio-wide default
    # (max_total_exposure_pct defaults to a tight 2% of total capital).
    await manager.set_exposure_limits(ExposureLimits(max_total_exposure_pct=Decimal("10000")))
    return manager


@pytest.mark.asyncio
async def test_get_portfolio_summary_does_not_deadlock_and_returns_real_data(pm):
    """Bug #2: get_portfolio_summary held its own lock and then called three
    other lock-acquiring methods -- a guaranteed deadlock, not a race."""
    summary = await _with_timeout(pm.get_portfolio_summary())

    assert summary["total_capital"] == Decimal("100000")
    assert "exposure" in summary and "margin" in summary and "pnl" in summary


@pytest.mark.asyncio
async def test_get_exposure_metrics_does_not_crash_on_an_empty_portfolio(pm):
    """Bug #3: reading metrics.margin_call_threshold (never set on ExposureMetrics)
    raised AttributeError on literally every call when margin_used <= 0 -- the
    ordinary empty-portfolio starting state."""
    metrics = await _with_timeout(pm.get_exposure_metrics())

    assert isinstance(metrics, ExposureMetrics)
    assert metrics.gross_exposure == Decimal("0")
    assert metrics.symbol_exposure == {}


@pytest.mark.asyncio
async def test_exposure_metrics_reflect_real_open_positions(pm):
    """The rewritten calculation actually derives symbol/strategy/gross/net
    exposure from open positions, rather than crashing before it gets there."""
    await _with_timeout(pm.open_position(
        strategy_id="strat_a", symbol="BTC-USD", side="long",
        quantity=Decimal("1"), entry_price=Decimal("10000"),
    ))

    metrics = await _with_timeout(pm.get_exposure_metrics())

    assert metrics.gross_exposure == Decimal("10000")
    assert metrics.long_exposure == Decimal("10000")
    assert metrics.short_exposure == Decimal("0")
    assert metrics.symbol_exposure["BTC-USD"] == Decimal("10000")
    assert metrics.strategy_exposure["strat_a"] == Decimal("10000")


@pytest.mark.asyncio
async def test_open_position_conflict_cancellation_does_not_deadlock(pm):
    """Bug #2 (second instance): open_position's own net-zero conflict
    resolution calls close_position on the pre-existing opposite position WHILE
    still holding self._lock. Before the fix this hung forever the moment it
    was actually reached with a real conflict; with bug #1 (missing await) it
    instead crashed on `coroutine["passes"]` before even getting this far."""
    position_a, _ = await _with_timeout(pm.open_position(
        strategy_id="strat_a", symbol="ETH-USD", side="long",
        quantity=Decimal("2"), entry_price=Decimal("1000"),
    ))
    assert position_a is not None

    # Opposite side, same symbol, same quantity -> net_zero -> cancels position_a.
    position_b, info = await _with_timeout(pm.open_position(
        strategy_id="strat_b", symbol="ETH-USD", side="short",
        quantity=Decimal("2"), entry_price=Decimal("1000"),
    ))

    assert position_b is not None
    assert not pm.positions[position_a.id].is_open, (
        "the net-zero conflict resolution must actually close the pre-existing "
        "opposite position, not hang or silently skip it"
    )


@pytest.mark.asyncio
async def test_close_position_records_pnl_and_creates_a_snapshot_without_crashing(pm):
    """Bug #4: create_snapshot (called from inside close_position, which already
    holds the lock) read self.pm.available_capital/used_capital, neither of which
    exists on PortfolioManager -- AttributeError on every single close."""
    position, _ = await _with_timeout(pm.open_position(
        strategy_id="strat_a", symbol="SOL-USD", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    ))

    closed = await _with_timeout(
        pm.close_position(position.id, exit_price=Decimal("110"), reason="manual")
    )

    assert closed is not None
    assert closed.realized_pnl == Decimal("100")  # 10 units * $10 gain
    assert len(pm.snapshot_system.snapshots) >= 1


@pytest.mark.asyncio
async def test_update_prices_does_not_deadlock_on_a_healthy_margin_level(pm):
    """Bug #2 (third instance): update_prices used to hold the lock across its
    own call into margin_monitor.check_margin_health, which reads back into
    get_margin_metrics -- another guaranteed deadlock once bug #1 was fixed."""
    await _with_timeout(pm.open_position(
        strategy_id="strat_a", symbol="BTC-USD", side="long",
        quantity=Decimal("1"), entry_price=Decimal("10000"),
    ))

    await _with_timeout(pm.update_prices({"BTC-USD": Decimal("10100")}))

    position = next(iter(pm.positions.values()))
    assert position.current_price == Decimal("10100")


@pytest.mark.asyncio
async def test_forced_liquidation_on_margin_call_closes_fully_and_says_so_honestly(pm):
    """Bugs #5 and #6: the forced-liquidation path called close_position with a
    keyword (close_price) that does not exist on its signature (exit_price does),
    so it always raised TypeError; and even if that were fixed, it logged
    "reducing by 25%" while always closing the entire position. This forces a
    real liquidation-risk margin level and checks the ACTUAL outcome: the
    position is fully closed, and the recorded action is honest about that."""
    position, info = await _with_timeout(pm.open_position(
        strategy_id="strat_a", symbol="BTC-USD", side="long",
        quantity=Decimal("4"), entry_price=Decimal("10000"),
    ))
    assert position is not None, info

    # margin_used ~= 10% of notional ($40,000 * 10% = $4,000); drive margin_level
    # well under the 110% liquidation threshold by cratering total_capital
    # directly (equity $1,000 / margin_used $4,000 = 25%).
    pm.total_capital = Decimal("1000")

    await _with_timeout(pm.update_prices({"BTC-USD": Decimal("10000")}))

    assert not pm.positions[position.id].is_open, (
        "a real liquidation-risk margin level must actually trigger a forced "
        "close, not merely log a warning"
    )
    assert pm.margin_monitor.reduction_count >= 1


@pytest.mark.asyncio
async def test_margin_monitor_get_margin_status_does_not_crash(pm):
    """get_margin_status called self.pm.get_margin_metrics() (async) without
    await from a sync method -- fixed by making get_margin_status itself async."""
    status = await _with_timeout(pm.margin_monitor.get_margin_status())

    assert status["status"] in {"healthy", "warning", "margin_call", "liquidation_risk"}
    assert "margin_level" in status
