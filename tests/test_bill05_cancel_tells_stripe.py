"""BILL-05: /api/billing/cancel and /resume must act on the Stripe subscription.

Before the fix only profiles.cancel_at_period_end was written, so Stripe kept charging.
Stripe is mocked; no network, no keys.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend_app.routers import billing

USER = {"id": "u-1", "email": "a@example.com"}


class _D(dict):
    """Stripe objects support both attribute and item access."""
    __getattr__ = dict.get


def _stripe(customers=None, subs=None, modify_exc=None):
    st = MagicMock()
    st.Customer.list.return_value = SimpleNamespace(data=customers or [])
    st.Subscription.list.return_value = SimpleNamespace(data=subs or [])
    if modify_exc:
        st.Subscription.modify.side_effect = modify_exc
    return st


def _call(fn, stripe_mod, provider="stripe", stripe_key="sk_live_x"):
    sb = MagicMock()
    with patch.dict("sys.modules", {"stripe": stripe_mod}), \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": stripe_key}), \
         patch.object(billing, "_validate_keys", return_value="sk_live_x"), \
         patch.object(billing, "_background_sb", return_value=sb), \
         patch.object(billing, "_profile_payment_provider", return_value=provider), \
         patch.object(billing, "invalidate_profile_cache", new=MagicMock(side_effect=lambda *_: _noop())), \
         patch.object(billing.RealtimeSync, "sync_subscription_change", new=MagicMock(side_effect=lambda *a, **k: _noop())), \
         patch("backend_app.core.billing_lifecycle.redis_manager") as rm:
        rm.delete = MagicMock(side_effect=lambda *_: _noop())
        try:
            out = asyncio.run(fn(request=MagicMock(), user=USER, supabase=MagicMock()))
            return out, sb, None
        except HTTPException as e:
            return None, sb, e


async def _noop():
    return None


def _live_sub(owner="u-1", status="active", sid="sub_1"):
    return _D(id=sid, status=status, metadata={"user_id": owner})


def _unwrap(fn):
    return getattr(fn, "__wrapped__", fn)


def test_cancel_calls_stripe_modify_cancel_at_period_end():
    st = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub()])
    out, sb, err = _call(_unwrap(billing.cancel_subscription), st)
    assert err is None and out["cancel_at_period_end"] is True
    st.Subscription.modify.assert_called_once_with("sub_1", cancel_at_period_end=True)
    sb.table.assert_called()  # local row updated after the provider accepted


def test_cancel_fails_closed_when_stripe_errors_and_local_state_untouched():
    st = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub()],
                 modify_exc=RuntimeError("stripe down"))
    out, sb, err = _call(_unwrap(billing.cancel_subscription), st)
    assert err is not None and err.status_code == 502
    sb.table.assert_not_called()  # nothing written locally


def test_cancel_never_touches_another_users_subscription():
    st = _stripe(customers=[_D(id="cus_1", metadata={"user_id": "someone-else"})],
                 subs=[_live_sub(owner="someone-else")])
    out, _, err = _call(_unwrap(billing.cancel_subscription), st)
    assert err is None
    st.Subscription.modify.assert_not_called()


def test_cancel_skips_already_ended_subscriptions():
    st = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub(status="canceled")])
    _call(_unwrap(billing.cancel_subscription), st)
    st.Subscription.modify.assert_not_called()


def test_cancel_for_non_stripe_user_does_not_contact_stripe():
    st = _stripe()
    out, _, err = _call(_unwrap(billing.cancel_subscription), st, provider="razorpay")
    assert err is None
    st.Customer.list.assert_not_called()
    st.Subscription.modify.assert_not_called()


def test_resume_reverses_at_stripe_and_fails_closed():
    st = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub()])
    out, sb, err = _call(_unwrap(billing.resume_subscription), st)
    assert err is None
    st.Subscription.modify.assert_called_once_with("sub_1", cancel_at_period_end=False)

    st2 = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub()],
                  modify_exc=RuntimeError("boom"))
    _, sb2, err2 = _call(_unwrap(billing.resume_subscription), st2)
    assert err2 is not None and err2.status_code == 502
    sb2.table.assert_not_called()


def test_unknown_provider_is_treated_as_stripe_when_stripe_is_configured():
    """payment_provider is never written by checkout, so None is the normal Stripe-customer state."""
    st = _stripe(customers=[_D(id="cus_1", metadata={})], subs=[_live_sub()])
    out, sb, err = _call(_unwrap(billing.cancel_subscription), st, provider=None)
    assert err is None
    st.Subscription.modify.assert_called_once_with("sub_1", cancel_at_period_end=True)


def test_unknown_provider_without_stripe_configured_skips_stripe():
    st = _stripe()
    out, sb, err = _call(_unwrap(billing.cancel_subscription), st, provider=None, stripe_key="")
    assert err is None
    st.Customer.list.assert_not_called()
