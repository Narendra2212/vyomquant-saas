"""LIVE-ENT-01: live entitlement is re-checked for every live order, failing closed.

Found 2026-11-19 in the final launch certification. ``live_trading_entitlement`` is in the
live gate's REQUIRED_CHECKS but was only ASSERTED there ("established by the caller that
resolved the deployment") -- checked once by the deploy endpoint. Two production paths run a
live bot without passing that endpoint again:

* the orphan sweep restarts any ``running`` row with no live claim (RESUME_LIVE_DEPLOYMENTS
  is on in production), and a browser role may write ``strategy_deployments.status``
  (the guard trigger's user-updatable list), so a stopped live deployment can be set back to
  ``running`` over REST;
* a paused live bot stays in memory and un-pauses when its row reads ``running`` again.

An account that lost live trading (plan lapsed or downgraded) kept trading real money.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from backend_app.backend.master_executor import BotRunner
from backend_app.core import subscription_dependencies as sd


def _run(plan, entitled):
    async def _ctx(uid, client):
        return SimpleNamespace(plan=plan)

    async def _check(uid, plan_key, feature):
        assert feature == sd.Feature.LIVE_TRADING.value
        return entitled

    with patch.object(sd, "get_plan_context", _ctx), \
         patch.object(sd.SubscriptionEngine, "check_feature_entitlement", _check), \
         patch("backend_app.core.supabase_connection.SupabaseConnection.get_client", lambda self: object()):
        return asyncio.run(sd.live_trading_entitlement_holds("u1"))


def test_an_entitled_plan_may_trade_live():
    assert _run("pro", True) is True


def test_a_plan_without_live_trading_is_refused():
    assert _run("free", False) is False


def test_an_unreadable_plan_fails_closed():
    async def _boom(uid, client):
        raise RuntimeError("profiles unreadable")

    with patch.object(sd, "get_plan_context", _boom), \
         patch("backend_app.core.supabase_connection.SupabaseConnection.get_client", lambda self: object()):
        assert asyncio.run(sd.live_trading_entitlement_holds("u1")) is False


def test_the_bot_checks_it_for_every_live_order_before_submitting():
    src = inspect.getsource(BotRunner)
    check = src.index("live_trading_entitlement_holds(self.user_id)")
    submit = src.index("result = await self.execution_engine.execute_with_idempotency(")
    assert check < submit, "the entitlement must be read before the order is submitted"
    window = src[check - 600:check]
    assert "if not self.paper_trading:" in window, "paper bots need no live entitlement"
    after = src[check:submit]
    assert "continue" in after, "a refused order must not fall through to submission"
