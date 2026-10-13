"""SEC-02: the Razorpay webhook authorises from the server-created order, not payment notes."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.routers import billing as b

USER = "11111111-1111-4111-8111-111111111111"


def _order(**over):
    base = {"id": "order_1", "amount": 99900, "currency": "INR",
            "notes": {"user_id": USER, "item": "pro"}}
    base.update(over)
    return base


def _payment(**over):
    base = {"id": "pay_1", "order_id": "order_1", "amount": 99900, "currency": "INR",
            "notes": {"user_id": USER, "item": "pro"}}
    base.update(over)
    return base


def _recon(payment, order):
    async def fake(_oid):
        return order
    with patch.object(b, "_fetch_razorpay_order", fake):
        return asyncio.run(b._reconcile_razorpay_payment_with_order(payment, payment.get("notes")))


def test_matching_payment_returns_the_orders_notes():
    assert _recon(_payment(), _order())["item"] == "pro"


@pytest.mark.parametrize("payment,order", [
    (_payment(notes={"user_id": USER, "item": "enterprise"}), _order()),          # item upgraded in notes
    (_payment(notes={"user_id": "22222222-2222-4222-8222-222222222222", "item": "pro"}), _order()),  # other user
    (_payment(amount=100), _order()),                                             # paid less than the order
    (_payment(currency="USD"), _order()),
    (_payment(order_id=None), _order()),                                          # no order at all
    (_payment(), _order(notes={})),                                               # order has no authority
])
def test_any_disagreement_raises(payment, order):
    with pytest.raises(b.RazorpayOrderMismatch):
        _recon(payment, order)


def test_order_fetch_failure_propagates_so_the_webhook_is_retried():
    async def boom(_oid):
        raise ConnectionError("razorpay down")
    with patch.object(b, "_fetch_razorpay_order", boom):
        with pytest.raises(ConnectionError):
            asyncio.run(b._reconcile_razorpay_payment_with_order(_payment(), _payment()["notes"]))


def _run_webhook(payment, order, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_test_secret_value")
    body = json.dumps({"event": "payment.captured", "event_id": "evt_1",
                       "payload": {"payment": {"entity": payment}}}).encode()

    class Req:
        headers = {}
        client = None

        async def body(self):
            return body

    redis = AsyncMock()
    redis.get.return_value = None
    redis.set.return_value = True
    grant = AsyncMock()

    async def fake_order(_oid):
        return order

    import hmac, hashlib
    sig = hmac.new(b"whsec_test_secret_value", body, hashlib.sha256).hexdigest()
    with patch.object(b, "_validate_webhook_ip"), patch.object(b, "_validate_keys"), \
         patch.object(b, "_validate_webhook_signature"), patch.object(b, "_validate_webhook_timestamp"), \
         patch("backend_app.core.cache.redis_manager.redis_manager", redis), \
         patch.object(b, "redis_manager", redis, create=True), \
         patch.object(b, "_fetch_razorpay_order", fake_order), \
         patch.object(b, "_process_razorpay_entitlement", grant), \
         patch.object(b, "_background_sb", create=True):
        try:
            out = asyncio.run(b.razorpay_webhook(AsyncMock(), Req(), x_razorpay_signature=sig))
        except Exception as exc:  # post-grant bookkeeping may fail in this harness; grant is what matters
            out = {"exc": repr(exc)}
    return out, grant


def test_webhook_with_upgraded_notes_grants_nothing(monkeypatch):
    out, grant = _run_webhook(_payment(notes={"user_id": USER, "item": "enterprise"}), _order(), monkeypatch)
    assert out.get("status") == "ignored", out
    grant.assert_not_called()


def test_webhook_for_a_genuine_order_grants_the_orders_item(monkeypatch):
    out, grant = _run_webhook(_payment(), _order(), monkeypatch)
    grant.assert_called_once()
    assert grant.call_args.args[0] == USER and grant.call_args.args[1] == "pro"
