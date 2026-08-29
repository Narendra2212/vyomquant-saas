"""
tests/test_task_4_3_deploy_prerequisite_gate.py

The deploy prerequisite gate, held in place.

Spec: strategy-builder task 4.3 (`design.md` -> Phase 4 -> Clone correctness (SB-02)).
Requirements 9.5, 10.4.

Requirement 10.4: "IF a version's validation state is other than VALID, or its
Identity_Hash is absent, or its Compiled_Plan is absent, THEN THE Deployment_Service
SHALL refuse to deploy that version and SHALL name the missing prerequisite."

What these tests hold in place
-------------------------------
* Neither ``StrategyService.deploy_version`` nor ``StrategyService.deploy_strategy`` had
  any check on ``validation_state``, ``dag_hash`` or ``compiled_plan`` before this task.
  Both would happily create a ``strategy_deployments`` row and call
  ``app_state.fleet.start_bot(...)`` for a version that was never compiled, was compiled
  and rejected, or was written before migration 004 part 1 landed the columns at all.

* The gate runs through one shared helper, ``StrategyService._assert_deploy_prerequisites``,
  called from both entry points before any deployment row is inserted and before any
  fleet call. A rejected attempt costs no quota and creates no deployment record - this
  file asserts that directly by checking the mocked insert and fleet start were never
  called.

* The gate names exactly which prerequisite is missing, in the fixed order
  validation_state -> dag_hash -> compiled_plan, via ``DeployPrerequisiteError.
  missing_prerequisite``. This is deliberately not a ``ValueError``: both router
  endpoints already give a bare ``ValueError`` a specific meaning (404 "version not
  found" for ``deploy_version``, 400/403 "quota or subscription problem" for
  ``deploy_strategy``), and a prerequisite-not-met failure is neither - it is a 409
  conflict on a version that exists but is not deployable yet.

* A row with the canonical columns entirely absent (pre-migration-004 or a legacy row)
  is refused the same as a row with the columns present and NULL: ``dict.get`` returns
  ``None`` either way, and the gate treats "not recorded" and "recorded as absent"
  identically, refusing safe-by-default.

Nothing about ownership or RLS scoping is touched or tested here beyond confirming the
gate runs on the already-scoped ``version_data`` row; that scoping is exercised by
``tests/test_supabase_callers_tenant_isolation.py`` and
``tests/test_tenant_isolation_strategy_clone.py``.

Amended by task 8.2, and only in what a *happy path* now needs
--------------------------------------------------------------
``deploy_version`` gained the Requirement 13 gates. None of them changes what this file is
about - the Requirement 10.4 refusals below are unchanged and still assert that nothing is
written - but a version that is expected to reach the INSERT now additionally has to be
``READY``, has to carry a compiled plan that reads back and declares one market, and has to
belong to a strategy this user owns. So:

* ``_version_row()`` carries ``lifecycle_state = "READY"`` and a minimal readable
  ``CompiledPlan`` with one DATA node, instead of the unreadable ``{"execution_order": []}``
  stub.
* the fake database carries the ``strategies`` row the version belongs to, because the
  ownership assertion (Requirement 13.2) now re-reads it with an explicit ``user_id``
  filter.
* ``_seeded_asset_universe`` puts one real market in task 7.1's cache, because market
  compatibility is confirmed against that cache and an empty cache is deliberately a
  **refusal** rather than a pass.

The refusal tests are untouched in intent: each still asserts
``DeployPrerequisiteError``, still asserts ``start_bot`` was not awaited and still asserts
no row was inserted.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.strategy_service import (
    DeployPrerequisiteError,
    StrategyService,
)


def _user():
    return {"id": "user_task_4_3", "email": "task43@example.com", "access_token": "tok"}


#: The market the plan below declares. Real symbol, real timeframe from the pipeline's own
#: vocabulary - nothing here is a substituted default (SB-06).
_SYMBOL = "BTC/USDT"
_TIMEFRAME = "1h"
_MARKET_TYPE = "spot"


def _readable_plan(dag_hash="abc123def4567890"):
    """The smallest ``CompiledPlan`` dict that reads back and declares one market."""
    return {
        "dag_hash": dag_hash,
        "data_nodes": ["n_data"],
        "execution_order": ["n_data"],
        "node_index": {
            "n_data": {
                "id": "n_data",
                "block_id": "data.market_data",
                "category": "data",
                "params": {
                    "symbol": _SYMBOL,
                    "timeframe": _TIMEFRAME,
                    "market_type": _MARKET_TYPE,
                },
            }
        },
    }


@pytest.fixture
def _seeded_asset_universe():
    """One real market in task 7.1's cache, so the deploy gate can confirm it.

    An empty universe is a 503 refusal by design (task 8.2): "we could not check" is not
    "the venue lists it". A test that expects a deploy to succeed therefore has to say
    which market exists.
    """
    import asyncio

    from backend_app.backend import asset_universe as au

    universe = au.AssetUniverse(
        assets=[
            au.AssetRef(
                symbol=_SYMBOL,
                base="BTC",
                quote="USDT",
                market_type=_MARKET_TYPE,
                active=True,
                price_precision=2,
                amount_precision=6,
                min_notional=10.0,
                min_amount=0.0001,
                available_on=("binance",),
                precision_source="binance",
            )
        ],
        generated_at=__import__("time").time(),
        exchanges=["binance"],
    )
    asyncio.get_event_loop_policy()
    au.reset_asset_universe_state_for_tests()
    au._local_universe = universe  # noqa: SLF001 - the module's own test seam is a reset
    try:
        yield universe
    finally:
        au.reset_asset_universe_state_for_tests()


def _version_row(**overrides):
    """A version row with every prerequisite satisfied, unless overridden."""
    row = {
        "id": str(uuid4()),
        "strategy_id": "strategy_task_4_3",
        "version": "v2.0",
        "blueprint": {"nodes": [], "edges": []},
        "validation_state": "VALID",
        "dag_hash": "abc123def4567890",
        "compiled_plan": _readable_plan(),
        "lifecycle_state": "READY",
        "is_current": True,
    }
    row.update(overrides)
    return row


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Chainable stand-in for the supabase-py query builder used in strategy_service.py."""

    def __init__(self, data):
        self._data = data

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def insert(self, *a, **kw):
        return self

    def update(self, *a, **kw):
        return self

    def execute(self):
        return _FakeResult(self._data)


class _FakeSupabase:
    """Returns a canned row set per table name, and records every insert/update call."""

    def __init__(self, table_data: dict):
        self._table_data = table_data
        self.insert_calls = []
        self.update_calls = []

    def table(self, name):
        data = self._table_data.get(name, [])
        query = _FakeQuery(data)
        original_insert = query.insert
        original_update = query.update

        def tracking_insert(payload, *a, **kw):
            self.insert_calls.append((name, payload))
            return original_insert(payload, *a, **kw)

        def tracking_update(payload, *a, **kw):
            self.update_calls.append((name, payload))
            return original_update(payload, *a, **kw)

        query.insert = tracking_insert
        query.update = tracking_update
        return query


def _service_with(sb: _FakeSupabase) -> StrategyService:
    service = StrategyService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the seam under test
    return service


# ---------------------------------------------------------------------------
# The shared helper, tested directly
# ---------------------------------------------------------------------------


class TestAssertDeployPrerequisitesDirectly:
    def test_valid_version_passes(self):
        service = StrategyService()
        # Must not raise.
        service._assert_deploy_prerequisites(
            _version_row(), "strategy_task_4_3", "v2.0"
        )

    def test_invalid_validation_state_is_named(self):
        service = StrategyService()
        with pytest.raises(DeployPrerequisiteError) as exc_info:
            service._assert_deploy_prerequisites(
                _version_row(validation_state="INVALID"),
                "strategy_task_4_3",
                "v2.0",
            )
        assert exc_info.value.missing_prerequisite == "validation_state"
        assert "validation_state" in str(exc_info.value)

    def test_null_dag_hash_is_named(self):
        service = StrategyService()
        with pytest.raises(DeployPrerequisiteError) as exc_info:
            service._assert_deploy_prerequisites(
                _version_row(dag_hash=None), "strategy_task_4_3", "v2.0"
            )
        assert exc_info.value.missing_prerequisite == "dag_hash"
        assert "dag_hash" in str(exc_info.value)

    def test_null_compiled_plan_is_named(self):
        service = StrategyService()
        with pytest.raises(DeployPrerequisiteError) as exc_info:
            service._assert_deploy_prerequisites(
                _version_row(compiled_plan=None), "strategy_task_4_3", "v2.0"
            )
        assert exc_info.value.missing_prerequisite == "compiled_plan"
        assert "compiled_plan" in str(exc_info.value)

    def test_missing_canonical_columns_entirely_is_refused_naming_validation_state(self):
        """A pre-migration-004 / legacy row has none of the three keys at all.

        ``dict.get`` returns ``None`` for an absent key exactly as it does for a present
        ``NULL``, so this must refuse identically to the explicit-None cases above -
        never let an unrecompiled or legacy version deploy.
        """
        legacy_row = {
            "id": str(uuid4()),
            "strategy_id": "strategy_task_4_3",
            "version": "v1.0",
            "blueprint": {"nodes": [], "edges": []},
            # no validation_state, dag_hash or compiled_plan keys at all
        }
        service = StrategyService()
        with pytest.raises(DeployPrerequisiteError) as exc_info:
            service._assert_deploy_prerequisites(
                legacy_row, "strategy_task_4_3", "v1.0"
            )
        assert exc_info.value.missing_prerequisite == "validation_state"


# ---------------------------------------------------------------------------
# deploy_version — the full code path
# ---------------------------------------------------------------------------


class TestDeployVersionGate:
    @pytest.mark.asyncio
    async def test_valid_version_deploys_successfully(self, _seeded_asset_universe):
        version_row = _version_row()
        sb = _FakeSupabase(
            {
                "strategy_versions": [version_row],
                "strategies": [
                    {"id": "strategy_task_4_3", "user_id": "user_task_4_3", "symbol": _SYMBOL}
                ],
            }
        )
        service = _service_with(sb)

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch(
            "backend_app.core.subscription_dependencies.get_user_plan",
            AsyncMock(return_value="free"),
        ), patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
            AsyncMock(return_value=(True, 1, 10)),
        ), patch(
            "backend_app.core.state.app_state"
        ) as mock_app_state:
            mock_app_state.fleet = fake_fleet
            result = await service.deploy_version(
                user=_user(),
                strategy_id="strategy_task_4_3",
                version="v2.0",
                environment="paper",
            )

        assert result.get("success") is True
        fake_fleet.start_bot.assert_awaited_once()
        assert any(name == "strategy_deployments" for name, _ in sb.insert_calls)

    @pytest.mark.asyncio
    async def test_invalid_validation_state_is_refused_before_any_side_effect(self):
        version_row = _version_row(validation_state="INVALID")
        sb = _FakeSupabase(
            {
                "strategy_versions": [version_row],
                "strategies": [
                    {"id": "strategy_task_4_3", "user_id": "user_task_4_3", "symbol": _SYMBOL}
                ],
            }
        )
        service = _service_with(sb)

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_version(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "validation_state"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls  # no deployment row created

    @pytest.mark.asyncio
    async def test_null_dag_hash_is_refused_before_any_side_effect(self):
        version_row = _version_row(dag_hash=None)
        sb = _FakeSupabase(
            {
                "strategy_versions": [version_row],
                "strategies": [
                    {"id": "strategy_task_4_3", "user_id": "user_task_4_3", "symbol": _SYMBOL}
                ],
            }
        )
        service = _service_with(sb)

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_version(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "dag_hash"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls

    @pytest.mark.asyncio
    async def test_null_compiled_plan_is_refused_before_any_side_effect(self):
        version_row = _version_row(compiled_plan=None)
        sb = _FakeSupabase(
            {
                "strategy_versions": [version_row],
                "strategies": [
                    {"id": "strategy_task_4_3", "user_id": "user_task_4_3", "symbol": _SYMBOL}
                ],
            }
        )
        service = _service_with(sb)

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_version(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "compiled_plan"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls


# ---------------------------------------------------------------------------
# deploy_strategy — the full code path
# ---------------------------------------------------------------------------


class TestDeployStrategyGate:
    @pytest.mark.asyncio
    async def test_valid_version_deploys_successfully(self):
        version_row = _version_row()
        sb = _FakeSupabase({"strategy_versions": [version_row]})
        service = _service_with(sb)
        service.get_strategy = AsyncMock(
            return_value={
                "strategy": {
                    "id": "strategy_task_4_3",
                    "symbol": "BTC/USDT",
                    "pair": "BTC/USDT",
                },
            }
        )

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            result = await service.deploy_strategy(
                user=_user(),
                strategy_id="strategy_task_4_3",
                version="v2.0",
                environment="paper",
            )

        assert result.get("success") is True
        fake_fleet.start_bot.assert_awaited_once()
        assert any(name == "strategy_deployments" for name, _ in sb.insert_calls)

    @pytest.mark.asyncio
    async def test_invalid_validation_state_is_refused_before_any_side_effect(self):
        version_row = _version_row(validation_state="INVALID")
        sb = _FakeSupabase({"strategy_versions": [version_row]})
        service = _service_with(sb)
        service.get_strategy = AsyncMock(
            return_value={"strategy": {"id": "strategy_task_4_3", "symbol": "BTC/USDT"}}
        )

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_strategy(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "validation_state"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls

    @pytest.mark.asyncio
    async def test_null_dag_hash_is_refused_before_any_side_effect(self):
        version_row = _version_row(dag_hash=None)
        sb = _FakeSupabase({"strategy_versions": [version_row]})
        service = _service_with(sb)
        service.get_strategy = AsyncMock(
            return_value={"strategy": {"id": "strategy_task_4_3", "symbol": "BTC/USDT"}}
        )

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_strategy(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "dag_hash"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls

    @pytest.mark.asyncio
    async def test_null_compiled_plan_is_refused_before_any_side_effect(self):
        version_row = _version_row(compiled_plan=None)
        sb = _FakeSupabase({"strategy_versions": [version_row]})
        service = _service_with(sb)
        service.get_strategy = AsyncMock(
            return_value={"strategy": {"id": "strategy_task_4_3", "symbol": "BTC/USDT"}}
        )

        fake_fleet = MagicMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))

        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.fleet = fake_fleet
            with pytest.raises(DeployPrerequisiteError) as exc_info:
                await service.deploy_strategy(
                    user=_user(),
                    strategy_id="strategy_task_4_3",
                    version="v2.0",
                    environment="paper",
                )

        assert exc_info.value.missing_prerequisite == "compiled_plan"
        fake_fleet.start_bot.assert_not_awaited()
        assert not sb.insert_calls


# ---------------------------------------------------------------------------
# Router HTTP mapping — the rejection must not be misrouted through the
# "version not found" 404 or the quota/subscription 400/403 branches.
# ---------------------------------------------------------------------------


class TestRouterHTTPMapping:
    def test_deploy_version_endpoint_maps_gate_rejection_to_409(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import backend_app.routers.strategy_operations as ops

        app = FastAPI()
        app.include_router(ops.router, prefix="/api")

        async def _get_current_user_override():
            return _user()

        app.dependency_overrides[ops.get_current_user] = _get_current_user_override

        fake_service = MagicMock()
        fake_service.deploy_version = AsyncMock(
            side_effect=DeployPrerequisiteError(
                "Cannot deploy version v2.0 of strategy s1: dag_hash is missing",
                missing_prerequisite="dag_hash",
            )
        )

        async def _fake_get_strategy_service():
            return fake_service

        with patch.object(ops, "get_strategy_service", _fake_get_strategy_service):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/strategies/s1/versions/v2.0/deploy")

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error"] == "DEPLOY_PREREQUISITE_NOT_MET"
        assert body["detail"]["missing_prerequisite"] == "dag_hash"

    def test_deploy_strategy_endpoint_maps_gate_rejection_to_409(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import backend_app.routers.strategy_operations as ops

        app = FastAPI()
        app.include_router(ops.router, prefix="/api")

        async def _get_current_user_override():
            return _user()

        app.dependency_overrides[ops.get_current_user] = _get_current_user_override

        fake_service = MagicMock()
        fake_service.deploy_strategy = AsyncMock(
            side_effect=DeployPrerequisiteError(
                "Cannot deploy version v2.0 of strategy s1: validation_state is 'INVALID' "
                "(must be 'VALID')",
                missing_prerequisite="validation_state",
            )
        )

        async def _fake_get_strategy_service():
            return fake_service

        with patch.object(ops, "get_strategy_service", _fake_get_strategy_service):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/strategies/s1/deploy", json={})

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error"] == "DEPLOY_PREREQUISITE_NOT_MET"
        assert body["detail"]["missing_prerequisite"] == "validation_state"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
