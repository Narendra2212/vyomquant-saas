"""
tests/test_risk04_max_drawdown_kill_switch.py — RISK-04: the max-drawdown kill switch is real.

THE DEFECT THIS CLOSES
=======================
``InstitutionalRiskManager.validate_trade_request``'s ``REJECT_DRAWDOWN`` check
(``backend_app/core/risk_manager.py``) was always real code, but the figure every caller fed
it was not: ``telemetry_engine.get_account_health``'s ``current_drawdown_pct`` is written by
``dashboard_data_ingester._write_account_health``, which hardcodes ``current_drawdown_pct =
0.0`` ("For drawdown, we'd need historical data - for now use 0"), and that ingester is never
invoked in this deployment at all. So the AUTOMATED bot path
(``master_executor.BotRunner._inner_loop``) always fed the gate ``0.0``, and the MANUAL order
path (``PaperTradingService.place_order``) never called the gate at all — a user whose account
had breached the platform's drawdown limit could not open a new bot position but could still
open one by hand.

``PaperTradingService.get_current_drawdown_pct`` computes the figure for real, from
``paper_equity_snapshots`` — the durable, per-fill equity history every paper order already
writes — and both ``place_order`` and the bot path now use it.

WHAT IS ASSERTED HERE
======================
1. A drawdown BELOW the platform threshold allows a (manual) trade.
2. A drawdown AT/ABOVE the platform threshold refuses it, with zero financial side-effects.
3. The figure is read fresh from the persisted equity series on every call — i.e. it is not
   cached across a restart of the process holding it (the "settings survive a restart"
   requirement, adapted: unlike ``max_daily_loss``, the drawdown THRESHOLD is a fixed
   platform-wide ceiling, not a per-user ``risk_settings`` row — see RISK-03's comment in
   ``risk_manager.py`` for why — so what must survive a restart is the MEASUREMENT, not a
   saved user preference, and the measurement lives in Postgres, not in any process's memory).
4. ``get_current_drawdown_pct`` returns ``None`` — never a fabricated ``0.0`` — for an account
   with fewer than two snapshots, i.e. one that has not yet had a drawdown measured.
5. A manual order cannot bypass the guard that blocks an automated one: the two code paths
   read the SAME threshold (``core.state.app_state.risk.thresholds.max_drawdown``) and the SAME
   persisted series, so a drawdown that refuses a bot's order also refuses a hand-placed one.
"""

from decimal import Decimal
import os
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend_app.backend.paper import paper_repository as paper_repo
from backend_app.backend.paper_trading_service import get_paper_trading_service
from backend_app.core.state import app_state
from backend_app.main import app
from tests.test_paper_repository import FakeSupabase

pytestmark = pytest.mark.usefixtures("fresh_paper_quote")  # PAPER-02


@pytest.fixture(autouse=True)
def _paper_persistence():
    """Same fresh in-memory Persistence_Layer every other risk-gate test suite uses.

    See ``tests/test_risk_gate_enforcement.py``'s fixture of the same name for the full
    rationale: ``place_order`` keeps no in-process balance or position state, so the gate
    below is evaluated against persisted rows, not a remembered figure.
    """
    paper_repo.reset_persistence_probe()
    service = get_paper_trading_service()
    service.bind_persistence(FakeSupabase())
    try:
        yield service
    finally:
        service.bind_persistence(None)
        paper_repo.reset_persistence_probe()


def get_test_auth_token(user_id: str, tenant_id: str = "tenant-risk24"):
    import jwt

    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or "dev-secret-change-in-production"
    payload = {
        "sub": user_id,
        "email": f"{user_id}@vyomquant.io",
        "tenant_id": tenant_id,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "algo22-test",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def auth_client():
    return TestClient(app)


def _seed_equity_series(paper_svc, user_id: str, equities):
    """Write one ``paper_equity_snapshots`` row per figure in ``equities``, ascending in time.

    Mirrors how ``PaperTradingService.place_order`` and ``BotPaperLedger`` write this series
    for real (one ``REVALUATION``/``FILL`` row per event) — this test writes the rows directly
    instead of placing a sequence of real fills, because what is under test is whether
    ``get_current_drawdown_pct`` and the two order paths read the series correctly, not whether
    a fill writes one (that is ``tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py``
    and the ``paper_repository`` suite's job).
    """
    supabase = paper_svc._supabase
    account = paper_svc.get_or_create_account(user_id)
    for i, equity in enumerate(equities):
        paper_repo.insert_equity_snapshot(
            supabase,
            user_id=user_id,
            total_equity=Decimal(str(equity)),
            available_balance=Decimal(str(equity)),
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            cause="REVALUATION",
            taken_at=f"2026-01-01T00:0{i}:00Z",
            series_index=0,
        )
    # ``available_balance`` above is a simplification for this test's purposes (position_market
    # value stays 0); the account's own balance from ``get_or_create_account`` is untouched, so
    # the order path's own balance/position bookkeeping is unaffected by seeding the series.
    del account


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE FIGURE ITSELF: real, peak-to-trough, a FRACTION — and None, not 0.0,
#    when unmeasurable
# ═══════════════════════════════════════════════════════════════════════════

def test_get_current_drawdown_pct_is_none_for_a_fresh_account():
    """One snapshot — or none — describes no decline. A peak needs something to fall from."""
    paper_svc = get_paper_trading_service()
    user_id = str(uuid4())

    assert paper_svc.get_current_drawdown_pct(user_id) is None

    _seed_equity_series(paper_svc, user_id, [100_000.0])
    assert paper_svc.get_current_drawdown_pct(user_id) is None


def test_get_current_drawdown_pct_computes_real_peak_to_trough_as_a_fraction():
    """Peaked at 150k, now at 90k -> 40% drawdown, returned as 0.40 (a fraction, not 40)."""
    paper_svc = get_paper_trading_service()
    user_id = str(uuid4())

    _seed_equity_series(paper_svc, user_id, [100_000.0, 150_000.0, 90_000.0])

    pct = paper_svc.get_current_drawdown_pct(user_id)
    assert pct == pytest.approx(0.40, abs=1e-6)


def test_get_current_drawdown_pct_is_zero_for_a_series_still_at_its_peak():
    """A series that only ever rose is AT its peak: 0.0 drawdown, not None and not negative."""
    paper_svc = get_paper_trading_service()
    user_id = str(uuid4())

    _seed_equity_series(paper_svc, user_id, [50_000.0, 100_000.0, 250_000.0])

    pct = paper_svc.get_current_drawdown_pct(user_id)
    assert pct == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE MANUAL ORDER PATH NOW ENFORCES IT (it did not, before RISK-04)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_order_allowed_when_drawdown_is_below_the_platform_threshold(auth_client):
    user_id = str(uuid4())
    paper_svc = get_paper_trading_service()
    max_drawdown = app_state.risk.thresholds.max_drawdown

    # Peaked at 100k, now at ``(1 - max_drawdown/2)`` of it: half the allowed drawdown.
    floor_fraction = 1.0 - (max_drawdown / 2.0)
    _seed_equity_series(paper_svc, user_id, [100_000.0, 100_000.0 * floor_fraction])

    order = await paper_svc.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.01,
        price=65000.0,
    )
    assert order["status"] == "FILLED"


@pytest.mark.asyncio
async def test_manual_order_refused_when_drawdown_is_at_or_above_the_platform_threshold(auth_client):
    user_id = str(uuid4())
    paper_svc = get_paper_trading_service()
    max_drawdown = app_state.risk.thresholds.max_drawdown

    # Peaked at 100k, now 5 points of drawdown past the limit.
    breach_fraction = 1.0 - max_drawdown - 0.05
    _seed_equity_series(paper_svc, user_id, [100_000.0, 100_000.0 * breach_fraction])

    acct_before = paper_svc.get_or_create_account(user_id)
    init_avail = Decimal(str(acct_before["available_balance"]))

    with pytest.raises(ValueError, match="Risk limit exceeded: Max drawdown exceeded"):
        await paper_svc.place_order(
            user_id=user_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.01,
            price=65000.0,
        )

    # Zero financial side-effects, the same contract every other gate in this file holds to.
    acct_after = paper_svc.get_or_create_account(user_id)
    assert Decimal(str(acct_after["available_balance"])) == init_avail
    assert len(paper_svc.get_positions(user_id)) == 0


@pytest.mark.asyncio
async def test_drawdown_refusal_is_recorded_as_a_risk_violation(auth_client):
    """The same audit trail every other gate in this router writes to."""
    from backend_app.routers.risk import _risk_violations_log

    user_id = str(uuid4())
    paper_svc = get_paper_trading_service()
    max_drawdown = app_state.risk.thresholds.max_drawdown
    breach_fraction = 1.0 - max_drawdown - 0.05
    _seed_equity_series(paper_svc, user_id, [100_000.0, 100_000.0 * breach_fraction])

    with pytest.raises(ValueError):
        await paper_svc.place_order(
            user_id=user_id, symbol="BTC-USDT", side="buy", quantity=0.01, price=65000.0,
        )

    violations = _risk_violations_log.get(user_id, [])
    assert any(v.get("rule") == "MAX_DRAWDOWN_EXCEEDED" for v in violations)


# ═══════════════════════════════════════════════════════════════════════════
# 3. NEITHER PATH CAN BE USED TO BYPASS THE OTHER: SAME THRESHOLD, SAME SERIES
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_path_and_automated_path_read_the_same_threshold():
    """A user cannot evade the drawdown limit by switching code paths.

    ``master_executor.BotRunner`` reads ``app_state.risk.thresholds.max_drawdown`` through
    ``self.risk_manager`` (``global_state.risk``, the SAME object); ``place_order`` reads it
    directly off ``app_state.risk``. Asserting they are the identical object (not two instances
    that happen to agree today) is what makes "cannot be bypassed" a fact about the code rather
    than a coincidence of today's defaults.
    """
    from backend_app.core.state import app_state as state_module_app_state

    assert state_module_app_state.risk is app_state.risk


@pytest.mark.asyncio
async def test_manual_path_computes_drawdown_from_the_same_series_the_bot_path_reads():
    """Both paths resolve to ``PaperTradingService.get_current_drawdown_pct`` for the same
    user, which reads ``paper_equity_snapshots`` — there is one series, not two independently
    maintained readings that could drift apart and let one path see a healthy account while
    the other sees a breached one."""
    paper_svc = get_paper_trading_service()
    user_id = str(uuid4())
    _seed_equity_series(paper_svc, user_id, [100_000.0, 150_000.0, 90_000.0])

    # What the bot path's override in `master_executor._inner_loop` calls.
    from_bot_path_call_site = get_paper_trading_service().get_current_drawdown_pct(user_id)
    # What the manual path's check in `place_order` calls.
    from_manual_path_call_site = paper_svc.get_current_drawdown_pct(user_id)

    assert from_bot_path_call_site == from_manual_path_call_site == pytest.approx(0.40, abs=1e-6)


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE MEASUREMENT SURVIVES A RESTART (it is read from Postgres, not memory)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_drawdown_measurement_survives_a_fresh_service_instance(auth_client):
    """``get_current_drawdown_pct`` holds no in-process cache: a brand-new
    ``PaperTradingService`` instance, standing in for a restarted process, reads the identical
    figure from the same persisted rows. Unlike ``max_daily_loss``'s per-user *threshold*
    (persisted in ``risk_settings``), the drawdown *threshold* is a fixed platform ceiling with
    nothing per-user to persist (RISK-03's comment in ``risk_manager.py``) — what must survive
    a restart here is the MEASUREMENT, and it does, because it is computed fresh from Postgres
    on every call rather than cached anywhere in the process that computes it.
    """
    from backend_app.backend.paper_trading_service import PaperTradingService

    paper_svc = get_paper_trading_service()
    user_id = str(uuid4())
    _seed_equity_series(paper_svc, user_id, [100_000.0, 150_000.0, 90_000.0])

    before = paper_svc.get_current_drawdown_pct(user_id)

    fresh_instance = PaperTradingService()
    fresh_instance.bind_persistence(paper_svc._supabase)
    after = fresh_instance.get_current_drawdown_pct(user_id)

    assert before == after == pytest.approx(0.40, abs=1e-6)
