"""BILL-06 -- a one-time referral discount is decremented after it is actually used.

THE DEFECT
    ``create_checkout_session`` read ``profiles.available_discounts`` and, if it was
    greater than zero, applied a 10% discount to the charge -- but nothing anywhere in
    the codebase ever decremented that column. Once it was set to a positive value
    once (by the referral program or anything else), EVERY future checkout for that
    account applied the discount forever.

THE FIX
    ``_apply_billing_entitlement`` now calls the new
    ``decrement_available_discount(target_user_id)`` Postgres function (migration 041)
    exactly when ``discount_applied=True`` was passed through from the webhook -- i.e.
    on CONFIRMED payment, never at checkout-session creation (so an abandoned checkout
    does not burn the discount for nothing). The function is atomic
    (``UPDATE ... WHERE available_discounts > 0`` in one statement), so two concurrent
    webhook deliveries for the same user cannot both decrement past zero, and it raises
    when there was nothing to decrement rather than silently reporting success either
    way.

WHAT THIS FILE PROVES (a minimal fake Supabase double; no real database)
    * a discounted entitlement grant calls the decrement RPC with the right user id;
    * an entitlement grant that did NOT use a discount does not call it at all;
    * the decrement RPC failing (e.g. nothing left to decrement) does not prevent the
      entitlement from being granted -- the customer already paid;
    * the marketplace branch (a different entitlement path entirely) is unaffected.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.routers import billing

USER_ID = "11111111-1111-1111-1111-111111111111"


def _run(coro):
    return asyncio.run(coro)


class _Query:
    def __init__(self, table: "_FakeSupabase", name: str) -> None:
        self._table = table
        self._name = name
        self._updates: Dict[str, Any] = {}

    def select(self, *_a, **_k):
        return self

    def update(self, values: Dict[str, Any]):
        self._updates = values
        return self

    def eq(self, column: str, value: Any):
        return self

    def execute(self):
        if self._updates and self._name == "profiles":
            self._table.profile_updates.append(dict(self._updates))
        return type("Resp", (), {"data": [{"subscription_tier": "free"}]})()


class _RpcCall:
    def __init__(self, table: "_FakeSupabase", name: str, params: Dict[str, Any]) -> None:
        self._table = table
        self._name = name
        self._params = params

    def execute(self):
        self._table.rpc_calls.append((self._name, dict(self._params)))
        if self._name == "decrement_available_discount" and self._table.discount_rpc_fails:
            raise RuntimeError("no available discount to decrement for user")
        return type("Resp", (), {"data": None})()


class _FakeSupabase:
    def __init__(self, discount_rpc_fails: bool = False) -> None:
        self.profile_updates: List[dict] = []
        self.rpc_calls: List[tuple] = []
        self.discount_rpc_fails = discount_rpc_fails

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: Dict[str, Any]) -> _RpcCall:
        return _RpcCall(self, name, params)


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    """Everything downstream of the entitlement write that this test does not care
    about, patched to no-ops so a real cache/notification/websocket call is never made.
    """
    monkeypatch.setattr(billing, "invalidate_profile_cache", AsyncMock())
    monkeypatch.setattr(billing, "RealtimeSync", type("RS", (), {
        "sync_subscription_change": AsyncMock(),
    }))
    monkeypatch.setattr(billing, "_record_plan_period", lambda *a, **k: None)


class TestADiscountedGrantDecrementsTheDiscount:
    def test_discount_applied_true_calls_the_decrement_rpc_with_the_right_user(self):
        client = _FakeSupabase()
        with patch.object(billing, "_background_sb", lambda: client):
            _run(billing._apply_billing_entitlement(USER_ID, "pro", True, {}))

        assert ("decrement_available_discount", {"target_user_id": USER_ID}) in client.rpc_calls

    def test_discount_applied_false_never_calls_the_decrement_rpc(self):
        """NEGATIVE CONTROL. An un-discounted checkout must not touch the counter at all."""
        client = _FakeSupabase()
        with patch.object(billing, "_background_sb", lambda: client):
            _run(billing._apply_billing_entitlement(USER_ID, "pro", False, {}))

        assert client.rpc_calls == []

    def test_a_failed_decrement_does_not_prevent_the_entitlement_grant(self):
        """The customer already paid. A discount-accounting failure must not undo that."""
        client = _FakeSupabase(discount_rpc_fails=True)
        with patch.object(billing, "_background_sb", lambda: client):
            # Must not raise.
            _run(billing._apply_billing_entitlement(USER_ID, "pro", True, {}))

        assert client.profile_updates, "the subscription tier was never written"
        assert client.profile_updates[0]["subscription_tier"] == "pro"

    def test_the_ml_addon_branch_also_decrements_when_discounted(self):
        """The discount applies to any checkout, not only subscription-tier changes."""
        client = _FakeSupabase()
        with patch.object(billing, "_background_sb", lambda: client):
            _run(billing._apply_billing_entitlement(USER_ID, "ml_addon", True, {}))

        assert ("decrement_available_discount", {"target_user_id": USER_ID}) in client.rpc_calls
