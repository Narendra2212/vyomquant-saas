"""
Fixtures for the deterministic sandbox suite (Requirement 26, task 21.1).

Everything the chain needs that is not a step of the chain lives here, done once, so
``test_deterministic_sandbox.py`` reads as the narrative Requirement 26.2 describes.

THE SEAMS THIS FILE SUBSTITUTES, AND WHY EACH ONE
-------------------------------------------------
1. ``create_request_supabase_async``, at each of the five modules that own a copy of the
   name. There is no PostgreSQL on this host, so the RLS-scoped client is
   :class:`~tests.sandbox_lifecycle.harness.SandboxDatabase`. Patched per module rather
   than only at ``core.dependencies`` because four of the five import the name at module
   scope, and a patch on the source module would not reach them.
2. ``distributed_idempotency.redis_manager``. There is no Redis either. The layer resolves
   this name at CALL time, so this one patch is what puts the REAL Lua check-and-set and
   the REAL owner-token release in front of a store this suite can inspect.
3. ``strategy_operations._backtest_exchange_instance``. The market-data boundary, and the
   only place Requirement 26.1's seeded synthetic source enters the backtest. Deliberately
   the endpoint's own published seam, so the version load, the compiler, the DAG engine and
   the simulator are all still the real ones.
4. The quota and entitlement calls, and the fleet. A bot allowance and a worker fleet are
   not this chain's subject; ``tests/test_task_8_2_deployment_binding.py`` grants the same
   four seams for the same reason and this file follows it exactly.
5. The asset universe. ``deployment_binding.assert_symbol_available`` checks the market
   against task 7.1's cached universe, which is populated from a venue this host cannot
   reach. The universe is seeded with the fixture's own market, through the module's own
   reset-for-tests inverse.

WHAT THIS FILE DOES **NOT** WEAKEN
---------------------------------
``SafetyMonitor.check_execution_allowed`` is not patched, weakened or bypassed. The
``sandbox_paper_mode`` fixture flips the platform's OWN paper-trading switch
(``ExecutionFlags.enable_paper_trading``, which itself refuses to run unless
``VYOMQUANT_MODE=paper``) and restores every flag and the environment variable afterwards -
the same restoring fixture ``tests/test_plan_engine_adaptation_fidelity.py`` established.

It is flipped on purpose rather than left at ``tests/conftest.py``'s ``safe``: in safe mode
``dag_engine.ActionExecutor`` returns an all-zero series by design, so the backtest would
complete with no trades at all and this suite would be asserting a lifecycle nothing ever
traded through. ``paper`` is also the mode the deployment under test is bound in, so it is
the honest setting for this chain. No test outside this package can observe it - the
teardown is asserted by ``test_paper_mode_is_not_left_enabled``.
"""

from __future__ import annotations

import time
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── EVERY BACKEND IMPORT HAPPENS HERE, AT COLLECTION TIME, DELIBERATELY ──────
#
# ``core.database_pool`` refuses AT IMPORT to run without a ``DATABASE_URL`` when
# ``VYOMQUANT_MODE`` is ``paper`` or ``live``, and this host has no database. Every module
# below is therefore imported while ``tests/conftest.py``'s ``VYOMQUANT_MODE=safe`` is
# still in force, so ``sandbox_paper_mode``'s flip is only ever observed by the RUNTIME
# checks it is meant for (``SafetyMonitor.check_execution_allowed``) and never by an import
# guard. Importing them lazily inside the fixture is what made this suite fail first time.
import backend_app.backend.asset_universe as au
import backend_app.routers.strategy_operations as SO
from backend_app.core import distributed_idempotency as di
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.safety_config import ExecutionFlags
from backend_app.core.subscription_dependencies import check_strategy_quota
from backend_app.main import app

from tests.sandbox_lifecycle.harness import (
    SANDBOX_MARKET_TYPE,
    SANDBOX_SYMBOL,
    SANDBOX_USER,
    SANDBOX_VENUE,
    RecordingRedis,
    SandboxClock,
    SandboxDatabase,
    SandboxExchangeClient,
    SandboxWorld,
    SeededSyntheticFeed,
)

#: Every module that holds its own reference to the request-scoped client factory.
_CLIENT_SEAM_MODULES = (
    "backend_app.core.dependencies",
    "backend_app.routers.strategies",
    "backend_app.backend.strategy_service",
    "backend_app.backend.signal_service",
    "backend_app.backend.backtest_service",
)


@pytest.fixture(scope="module")
def registry():
    """The real assembled block registry. Assembly costs ~150 ms, so it is shared."""
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def _clean_module_state() -> Iterator[None]:
    """Every per-process verdict this chain caches, forgotten before and after each test.

    All six are module globals that a previous test's answer would otherwise decide:
    005a's ``archived_at`` probe, 004e's binding-column probe, the venue timeframe cache,
    the asset universe, 005b's two probes (the ``signals`` columns and the transition log),
    and the shared rate limiter's per-route counts.
    """
    from backend_app.backend import asset_universe as au
    from backend_app.backend import deployment_binding as db_binding
    from backend_app.backend import signal_service as svc
    from backend_app.backend import strategy_archive as archive
    from backend_app.core.rate_limit import limiter

    def _reset() -> None:
        archive.reset_archive_column_support()
        db_binding.reset_binding_column_support()
        db_binding.reset_venue_timeframe_cache()
        au.reset_asset_universe_state_for_tests()
        svc.reset_signal_lifecycle_column_support()
        svc.reset_transitions_table_support()
        limiter.reset()

    _reset()
    yield
    _reset()


@pytest.fixture
def sandbox_paper_mode() -> Iterator[None]:
    """Permit strategy-signal execution for one test, then restore every flag.

    ``SafetyMonitor.check_execution_allowed`` is NOT patched. This flips the platform's own
    switch and restores the previous environment variable and the previous value of every
    flag afterwards, so no other test can observe a permissive state.
    """
    import os

    flag_names = (
        "LIVE_TRADING_ENABLED",
        "PAPER_TRADING_ENABLED",
        "MANUAL_ORDER_EXECUTION",
        "STRATEGY_SIGNAL_EXECUTION",
        "BACKTEST_CAN_SUBMIT_ORDERS",
        "ALLOW_ML_INFERENCE",
    )
    previous_mode = os.environ.get("VYOMQUANT_MODE")
    previous_flags = {name: getattr(ExecutionFlags, name) for name in flag_names}
    os.environ["VYOMQUANT_MODE"] = "paper"
    try:
        ExecutionFlags.enable_paper_trading()
        yield
    finally:
        for name, value in previous_flags.items():
            setattr(ExecutionFlags, name, value)
        if previous_mode is None:
            os.environ.pop("VYOMQUANT_MODE", None)
        else:
            os.environ["VYOMQUANT_MODE"] = previous_mode


@pytest.fixture
def sandbox(monkeypatch, sandbox_paper_mode) -> SandboxWorld:
    """The world the chain runs in, with every non-chain seam supplied.

    Returns a :class:`SandboxWorld` whose ``strategy_id`` / ``version_id`` /
    ``backtest_id`` / ``deployment_id`` the test fills in as each step succeeds.
    """
    monkeypatch.setenv("ENV", "testing")

    clock = SandboxClock()
    db = SandboxDatabase()
    redis = RecordingRedis(clock)
    feed = SeededSyntheticFeed()
    exchange = SandboxExchangeClient()
    world = SandboxWorld(
        clock=clock, db=db, redis=redis, feed=feed, exchange=exchange
    )
    world.seed_owner_rows()

    # ── 1. the request-scoped client, at every module that holds the name ──
    async def _client(_token: Any = None) -> SandboxDatabase:
        return db

    for module_path in _CLIENT_SEAM_MODULES:
        import importlib

        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, "create_request_supabase_async", _client, raising=False)

    # ── 2. the store the REAL idempotency layer locks in ──────────────────
    monkeypatch.setattr(di, "redis_manager", redis)

    # ── 3. the seeded synthetic market, at the endpoint's own feed seam ────
    async def _feed() -> SeededSyntheticFeed:
        return feed

    monkeypatch.setattr(SO, "_backtest_exchange_instance", _feed)

    # ── 4. the asset universe the deploy gate checks the market against ────
    au._local_universe = au.AssetUniverse(  # noqa: SLF001 - reset_..._for_tests is the inverse
        assets=[
            au.AssetRef(
                symbol=SANDBOX_SYMBOL,
                base=SANDBOX_SYMBOL.split("/")[0],
                quote=SANDBOX_SYMBOL.split("/")[-1],
                market_type=SANDBOX_MARKET_TYPE,
                active=True,
                price_precision=2,
                amount_precision=6,
                min_notional=10.0,
                min_amount=0.0001,
                available_on=(SANDBOX_VENUE,),
                precision_source=SANDBOX_VENUE,
            )
        ],
        generated_at=time.time(),
        exchanges=[SANDBOX_VENUE],
    )

    # ── 5. quota, entitlement and the fleet ───────────────────────────────
    quota_patches = (
        patch(
            "backend_app.core.subscription_dependencies.get_user_plan",
            AsyncMock(return_value="pro"),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
            AsyncMock(return_value=(True, 1, 10)),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.check_feature_entitlement",
            AsyncMock(return_value=True),
        ),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
            AsyncMock(return_value=None),
        ),
    )
    for quota_patch in quota_patches:
        quota_patch.start()

    fleet = MagicMock()
    fleet.start_bot = AsyncMock(return_value=(True, "started"))
    app_state_patch = patch("backend_app.core.state.app_state")
    app_state = app_state_patch.start()
    app_state.fleet = fleet
    world.fleet = fleet  # type: ignore[attr-defined]

    try:
        yield world
    finally:
        app_state_patch.stop()
        for quota_patch in quota_patches:
            quota_patch.stop()


@pytest.fixture
def client(sandbox: SandboxWorld):
    """A ``TestClient`` over the REAL ``backend_app.main.app``, authenticated as the owner.

    The app is the mounted one, not a router assembled here, so route resolution, mounting
    order and every dependency the real request graph pulls in are all exercised.

    Three dependency overrides, and no more: the authenticated user (there is no auth
    server), the request-scoped client dependency (the same
    :class:`SandboxDatabase` the module seams return), and the strategy save-quota gate
    (which reaches a subscription store this host does not have).
    """
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_user] = lambda: dict(SANDBOX_USER)
    app.dependency_overrides[get_request_supabase] = lambda: sandbox.db
    app.dependency_overrides[check_strategy_quota] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def stranger_client(sandbox: SandboxWorld):
    """The same app and the same database, authenticated as the SECOND tenant (task 22.1).

    Requirement 20.4's caller: authenticated, legitimate, and the owner of nothing. The
    three overrides are ``client``'s own, with one identity swapped - so the two fixtures
    differ in exactly the variable Requirement 20.2 is about and in nothing else. In
    particular the database is the SAME :class:`SandboxDatabase` instance, so the owner's
    rows really are present while the stranger is being refused; a suite that gave the
    stranger an empty database would prove nothing.

    WHAT THIS FIXTURE DELIBERATELY DOES NOT SIMULATE
        Row-level security. There is no PostgreSQL here, and
        ``create_request_supabase_async`` is patched to return one shared client regardless
        of the access token, so ``strategies_owner_select`` and its siblings are not in
        force. That is the STRICT direction: ownership has to be enforced by the
        application-layer predicate Requirement 20.1 asks for **on top of** RLS, and an
        endpoint that filtered by id alone and leaned on RLS selects the owner's row here
        and is caught, rather than passing on a policy this host cannot run.
    """
    from fastapi.testclient import TestClient

    from tests.sandbox_lifecycle.harness import STRANGER_USER

    app.dependency_overrides[get_current_user] = lambda: dict(STRANGER_USER)
    app.dependency_overrides[get_request_supabase] = lambda: sandbox.db
    app.dependency_overrides[check_strategy_quota] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()
