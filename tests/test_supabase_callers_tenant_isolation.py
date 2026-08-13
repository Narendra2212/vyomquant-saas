"""
tests/test_supabase_callers_tenant_isolation.py — E2E Tenant Isolation & Concurrency Verification

Verifies that all migrated caller sites using create_request_supabase_async():
1. Produce isolated AsyncPostgrestClient instances with distinct per-request Authorization headers.
2. Under 50 concurrent gather() tasks for multi-tenant users, no authorization token leaks occur.
3. Shared HTTP transport singleton is active across concurrent client instantiations.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from backend_app.core.dependencies import create_request_supabase_async, get_shared_async_transport
from backend_app.backend.signal_service import SignalService
from backend_app.backend.backtest_service import BacktestService
from backend_app.backend.strategy_service import StrategyService
from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService


@pytest.mark.asyncio
async def test_all_services_async_supabase_tenant_isolation():
    """Verify that all services instantiate scoped clients without header bleed."""
    user_a = {"id": "user_a_123", "access_token": "token_user_a_aaa"}
    user_b = {"id": "user_b_456", "access_token": "token_user_b_bbb"}

    # SignalService
    sig_svc = SignalService()
    sb_sig_a = await sig_svc._get_supabase(user_a)
    sb_sig_b = await sig_svc._get_supabase(user_b)
    assert sb_sig_a.session.headers["Authorization"] == "Bearer token_user_a_aaa"
    assert sb_sig_b.session.headers["Authorization"] == "Bearer token_user_b_bbb"

    # BacktestService
    bt_svc = BacktestService()
    sb_bt_a = await bt_svc._get_supabase(user_a)
    sb_bt_b = await bt_svc._get_supabase(user_b)
    assert sb_bt_a.session.headers["Authorization"] == "Bearer token_user_a_aaa"
    assert sb_bt_b.session.headers["Authorization"] == "Bearer token_user_b_bbb"

    # StrategyService
    strat_svc = StrategyService()
    sb_st_a = await strat_svc._get_supabase(user_a)
    sb_st_b = await strat_svc._get_supabase(user_b)
    assert sb_st_a.session.headers["Authorization"] == "Bearer token_user_a_aaa"
    assert sb_st_b.session.headers["Authorization"] == "Bearer token_user_b_bbb"

    # DashboardAggregationService
    dash_svc = DashboardAggregationService()
    sb_dash_a = await dash_svc._get_supabase(user_a)
    sb_dash_b = await dash_svc._get_supabase(user_b)
    assert sb_dash_a.session.headers["Authorization"] == "Bearer token_user_a_aaa"
    assert sb_dash_b.session.headers["Authorization"] == "Bearer token_user_b_bbb"

    # Transport sharing check
    transport = await get_shared_async_transport()
    assert sb_sig_a._transport is transport
    assert sb_bt_b._transport is transport
    assert sb_st_a._transport is transport
    assert sb_dash_b._transport is transport


@pytest.mark.asyncio
async def test_concurrent_services_token_integrity():
    """Run 50 concurrent rounds of service client requests and verify zero cross-tenant contamination."""
    user_a = {"id": "user_a_123", "access_token": "jwt_token_tenant_a"}
    user_b = {"id": "user_b_456", "access_token": "jwt_token_tenant_b"}

    sig_svc = SignalService()
    dash_svc = DashboardAggregationService()

    async def worker_a():
        sb_sig = await sig_svc._get_supabase(user_a)
        sb_dash = await dash_svc._get_supabase(user_a)
        await asyncio.sleep(0.001)
        assert sb_sig.session.headers["Authorization"] == "Bearer jwt_token_tenant_a"
        assert sb_dash.session.headers["Authorization"] == "Bearer jwt_token_tenant_a"
        return True

    async def worker_b():
        sb_sig = await sig_svc._get_supabase(user_b)
        sb_dash = await dash_svc._get_supabase(user_b)
        await asyncio.sleep(0.001)
        assert sb_sig.session.headers["Authorization"] == "Bearer jwt_token_tenant_b"
        assert sb_dash.session.headers["Authorization"] == "Bearer jwt_token_tenant_b"
        return True

    tasks = []
    for _ in range(25):
        tasks.append(worker_a())
        tasks.append(worker_b())

    results = await asyncio.gather(*tasks)
    assert all(results)
